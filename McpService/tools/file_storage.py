"""
File Storage Tools for ObsidianSyncMCP Server

Provides user-isolated file storage with semantic search via RAG API integration.
All file operations are scoped to the authenticated user via user_id from request headers.
All file changes trigger Git commit and push (if Git is configured) and RAG indexing.
"""

import os
import io
import json
import httpx
import aiofiles
import asyncio
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import git
from git import Repo, GitCommandError
import re

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.storage import get_current_user, get_user_storage_path

# Storage and RAG API configuration
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/storage"))
RAG_API_URL = os.environ.get("RAG_API_URL", "http://librechat-rag-api:8000")
RAG_API_JWT_SECRET = os.environ.get("RAG_API_JWT_SECRET", os.environ.get("JWT_SECRET", ""))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))

# VectorDB configuration
VECTORDB_HOST = os.environ.get("VECTORDB_HOST", "vectordb")
VECTORDB_PORT = int(os.environ.get("VECTORDB_PORT", "5432"))
VECTORDB_DB = os.environ.get("VECTORDB_DB", "mydatabase")
VECTORDB_USER = os.environ.get("VECTORDB_USER", "myuser")
VECTORDB_PASSWORD = os.environ.get("VECTORDB_PASSWORD", "mypassword")

def clean_remote_url(url: str) -> str:
    """
    Remove authentication tokens from a Git remote URL.
    Example: https://token@github.com/user/repo -> https://github.com/user/repo
    """
    if not url:
        return url
    # Matches http(s)://token@... or http(s)://user:password@...
    cleaned = re.sub(r'^(https?://)[^@/]+@', r'\1', url)
    return cleaned

def setup_credential_store(repo, user_id: str, repo_url: str, token: str) -> None:
    """
    Configure Git to use the built-in 'store' helper pointing to a persistent file,
    and save the user's token into that store.
    """
    # 1. Define persistent credential file path in /storage/{user_id}/.git-credentials
    cred_file = STORAGE_ROOT / user_id / ".git-credentials"
    cred_file.parent.mkdir(parents=True, exist_ok=True)

    # 2. Configure Git to use standard 'store' helper with this specific file
    repo.git.config("credential.helper", f"store --file={cred_file}")

    # 3. Use git credential approve to save the token if provided
    if not token:
        return

    protocol = "https"
    host = ""
    path = "/"
    url_parts = repo_url.split("://", 1)
    if len(url_parts) > 1:
        protocol = url_parts[0]
        rest = url_parts[1].split("@", 1)[-1]
        host_path = rest.split("/", 1)
        host = host_path[0]
        if len(host_path) > 1:
            path = "/" + host_path[1]

    input_data = f"protocol={protocol}\nhost={host}\npath={path}\nusername={token}\npassword=\n\n"

    try:
        subprocess.run(
            ["git", "-c", f"credential.helper=store --file={cred_file}", "credential", "approve"],
            input=input_data.encode("utf-8"),
            check=True,
            capture_output=True
        )
    except Exception as e:
        print(f"Warning: Failed to store credentials for user {user_id}: {e}")

def get_token_from_store(user_id: str, repo_url: str) -> Optional[str]:
    """
    Retrieve the token for a specific repository from the persistent Git store.
    """
    cred_file = STORAGE_ROOT / user_id / ".git-credentials"
    if not cred_file.exists():
        return None

    protocol = "https"
    host = ""
    path = "/"
    url_parts = repo_url.split("://", 1)
    if len(url_parts) > 1:
        protocol = url_parts[0]
        rest = url_parts[1].split("@", 1)[-1]
        host_path = rest.split("/", 1)
        host = host_path[0]
        if len(host_path) > 1:
            path = "/" + host_path[1]

    input_data = f"protocol={protocol}\nhost={host}\npath={path}\n"

    try:
        result = subprocess.run(
            ["git", "-c", f"credential.helper=store --file={cred_file}", "credential", "fill"],
            input=input_data.encode("utf-8"),
            check=True,
            capture_output=True
        )
        output = result.stdout.decode("utf-8")
        # Parse output for username=...
        for line in output.splitlines():
            if line.startswith("username="):
                return line.split("=", 1)[1]
    except Exception as e:
        print(f"Warning: Failed to retrieve token from store for user {user_id}: {e}")
        return None

def get_file_id(user_id: str, filename: str) -> str:
    """Generate a unique file ID for vectordb scoping"""
    return f"user_{user_id}_{filename}"


def _should_exclude_file(file_path: Path, user_dir: Path) -> bool:
    """
    Check if a file should be excluded from search and listing.

    Excludes:
    - Files in .git directory or any path containing .git
    - Hash files (e.g., sync_hashes.json)
    - Files in root directory (only subdirectories allowed)

    Args:
        file_path: Full path to the file (may be absolute or relative)
        user_dir: User's storage root directory

    Returns:
        True if file should be excluded, False otherwise
    """
    try:
        # Try to get relative path from user_dir
        relative_path = file_path.relative_to(user_dir)
    except ValueError:
        # File is not under user_dir, check if it's a hash file by name
        hash_file_names = {'sync_hashes.json', 'git_config.json'}
        if file_path.name in hash_file_names:
            return True
        # If we can't determine relative path, don't exclude (might be valid)
        return False

    # Exclude files in root directory (only subdirectories allowed)
    if relative_path.parent == Path('.'):
        return True

    # Exclude .git directory and any path containing .git
    if '.git' in relative_path.parts:
        return True

    # Exclude hash files (sync_hashes.json, git_config.json, etc.)
    hash_file_names = {'sync_hashes.json', 'git_config.json'}
    if file_path.name in hash_file_names:
        return True

    # Exclude any file in a directory starting with '.'
    if any(part.startswith('.') for part in relative_path.parts):
        return True

    return False


def _generate_jwt_token(user_id: str) -> str:
    """Generate a JWT token for RAG API authentication."""
    if not RAG_API_JWT_SECRET:
        return ""

    try:
        import jwt
        from datetime import timedelta

        # Generate token with user_id in payload (matching LibreChat's format)
        # Token expires in 5 minutes (matching LibreChat's generateShortLivedToken)
        payload = {
            "id": user_id,
            "exp": datetime.utcnow() + timedelta(minutes=5)
        }
        return jwt.encode(payload, RAG_API_JWT_SECRET, algorithm="HS256")
    except ImportError:
        # JWT not available, return empty token
        return ""


async def _trigger_git_commit(user_id: str, file_path: Path, is_delete: bool = False) -> None:
    """
    Trigger Git commit and push for file changes if Git is configured.

    This function checks if the user has Git configured and commits/pushes
    the file change immediately. Files are committed to the obsidian_vault
    repository if it exists and is configured.

    Args:
        user_id: User ID
        file_path: Path to the file that was changed
        is_delete: Whether this is a delete operation
    """
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"

    # Check if Git is configured
    if not config_path.exists():
        return  # No Git config, skip

    try:
        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = json.loads(content)

        # Check if sync is stopped
        if config.get('stopped', False):
            return  # Sync is stopped, don't commit

        repo_url = config.get('repo_url')
        token = config.get('token')
        branch = config.get('branch', 'main')

        if not repo_url:
            return  # Invalid config

        # Handle missing token by retrieving from Git store
        if not token:
            token = get_token_from_store(user_id, repo_url)
            if not token:
                # We'll still try to commit, maybe it's a public repo or doesn't need auth
                # But we need a token for our push logic if it's private
                pass

        vault_path = user_dir / "obsidian_vault"

        # Only commit files that are in the vault directory
        # Files outside vault won't be committed (but will still be indexed in RAG)
        if not vault_path.exists():
            return  # No vault, skip Git operations

        # Check if file is in vault
        try:
            file_path.relative_to(vault_path)
        except ValueError:
            # File is not in vault, skip Git operations
            # Note: The Worker will handle syncing vault files, so we only
            # commit files that are already in the vault
            return

        try:
            repo = Repo(vault_path)

            # For deletes, we need to handle differently
            if is_delete:
                # File was already deleted, so we need to stage the deletion
                # Get relative path before file was deleted
                try:
                    rel_path = file_path.relative_to(vault_path)
                    repo.git.rm(str(rel_path))
                except GitCommandError:
                    # File might not have been tracked, that's OK
                    pass
            else:
                # Add file to Git (or stage changes)
                rel_path = file_path.relative_to(vault_path)
                repo.git.add(str(rel_path))

            # Only commit if there are changes
            if repo.is_dirty(untracked_files=True) or is_delete:
                # Commit
                if is_delete:
                    commit_message = f"Delete {file_path.name} from LibreChat: {datetime.utcnow().isoformat()}"
                else:
                    commit_message = f"Update {file_path.name} from LibreChat: {datetime.utcnow().isoformat()}"
                repo.index.commit(commit_message)

                # Push
                clean_url = clean_remote_url(repo_url)
                if 'origin' in repo.remotes:
                    repo.remotes.origin.set_url(clean_url)
                else:
                    repo.create_remote('origin', clean_url)

                # Ensure credential helper is configured
                setup_credential_store(repo, user_id, repo_url, token)

                repo.remotes.origin.push(branch)

        except GitCommandError as e:
            # Git errors are non-fatal - log but don't fail the file operation
            print(f"Warning: Git commit/push failed for {file_path}: {e}")
        except Exception as e:
            # Other errors are also non-fatal
            print(f"Warning: Git operation failed for {file_path}: {e}")

    except Exception as e:
        # Config read errors are non-fatal
        print(f"Warning: Failed to read Git config for Git commit: {e}")


async def upload_file(filename: str, content: str) -> str:
    """
    Upload a file to user's storage and index it in RAG API.
    Also triggers Git commit and push if Git is configured.

    Args:
        filename: Name of the file to create
        content: Text content to write to the file

    Returns:
        Success message with file path
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    file_path = user_dir / filename

    # Check if file already exists
    if file_path.exists():
        return f"Error: File '{filename}' already exists. Use modify_file to update it."

    # Write file to storage
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(content)

    # Index in RAG API
    file_id = get_file_id(user_id, filename)
    metadata = {
        "user_id": user_id,
        "filename": filename,
        "created_at": datetime.utcnow().isoformat(),
        "size": len(content)
    }

    try:
        token = _generate_jwt_token(user_id)
        headers = {
            "Accept": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Create multipart form data
        # httpx expects files as tuple: (filename, file-like object, content_type)
        # Use only the basename for the file field to avoid RAG API directory creation issues
        # The full path is preserved in metadata
        file_content = io.BytesIO(content.encode('utf-8'))
        file_content.seek(0)  # Ensure we're at the beginning
        file_basename = Path(filename).name  # Extract just the filename, not the path
        files = {
            'file': (file_basename, file_content, 'text/markdown')
        }
        data = {
            'file_id': file_id
        }

        # Add storage_metadata if needed (optional, but LibreChat sends it)
        if metadata:
            data['storage_metadata'] = json.dumps(metadata)

        # Remove Content-Type header - httpx will set it correctly for multipart
        multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{RAG_API_URL}/embed",
                files=files,
                data=data,
                headers=multipart_headers,
                timeout=30.0
            )

            # Log response details for debugging errors
            if response.status_code >= 400:
                try:
                    error_detail = response.json()
                    print(f"RAG API error details for {filename}: {error_detail}")
                except:
                    print(f"RAG API error for {filename} (status {response.status_code}): {response.text[:500]}")

            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Clean up file if indexing failed
        file_path.unlink()
        # Include response details in error message
        error_msg = f"Failed to index file in RAG API: {e}"
        if e.response is not None:
            try:
                error_detail = e.response.json()
                error_msg += f" - Details: {error_detail}"
            except:
                error_msg += f" - Response: {e.response.text[:500]}"
        raise RuntimeError(error_msg)
    except Exception as e:
        # Clean up file if indexing failed
        file_path.unlink()
        raise RuntimeError(f"Failed to index file in RAG API: {e}")

    # Trigger Git commit/push if configured (non-blocking)
    try:
        await _trigger_git_commit(user_id, file_path)
    except Exception as e:
        # Git errors are non-fatal
        print(f"Warning: Git commit failed: {e}")

    return f"Successfully uploaded '{filename}' ({len(content)} bytes) to {file_path}"


async def create_note(title: str, content: str) -> str:
    """
    Create a markdown note in user's storage.

    This is a convenience wrapper around upload_file that automatically adds .md extension
    and formats the note with a title header.

    Args:
        title: Title of the note (will be used as filename without .md extension)
        content: Content of the note (markdown formatted)

    Returns:
        Success message with file path
    """
    # Sanitize title for filename (remove special characters)
    import re
    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
    filename = f"{safe_title}.md"

    # Format note with title header
    note_content = f"# {title}\n\n{content}"

    # Use upload_file to create the note
    return await upload_file(filename, note_content)


async def list_files(_: str = "") -> str:
    """
    List all files in the user's storage with metadata.
    Only includes files in subdirectories (excludes root directory files).
    Excludes .git directory, hash files, and other hidden directories.

    Args:
        _: Optional parameter (ignored) - FastMCP may pass empty string

    Returns:
        Formatted list of files with metadata
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)

    files = []

    # Only list files in subdirectories (exclude root directory files)
    # Recursively list files in subdirectories (e.g., obsidian_vault)
    for subdir in user_dir.iterdir():
        if subdir.is_dir() and not subdir.name.startswith('.'):
            for file_path in subdir.rglob('*'):
                if file_path.is_file():
                    # Exclude .git files, hash files, and other excluded files
                    if _should_exclude_file(file_path, user_dir):
                        continue

                    stat = file_path.stat()
                    relative_path = file_path.relative_to(user_dir)
                    files.append({
                        "filename": file_path.name,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "path": str(relative_path),
                        "directory": str(relative_path.parent)
                    })

    if not files:
        return "No files found in your storage."

    # Format as readable output
    output = f"Found {len(files)} file(s):\n\n"
    for f in sorted(files, key=lambda x: (x['directory'], x['filename'])):
        if f['directory']:
            output += f"- {f['directory']}/{f['filename']}\n"
        else:
            output += f"- {f['filename']}\n"
        output += f"  Size: {f['size']} bytes\n"
        output += f"  Modified: {f['modified']}\n\n"

    return output


async def read_file(filename: str) -> str:
    """
    Read the contents of a file from user's storage.

    Args:
        filename: Name of the file to read

    Returns:
        File contents as string
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    file_path = user_dir / filename

    if not file_path.exists():
        return f"Error: File '{filename}' not found in your storage."

    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
        content = await f.read()

    return content


async def modify_file(filename: str, content: str) -> str:
    """
    Modify an existing file's contents and re-index in RAG API.
    Also triggers Git commit and push if Git is configured.

    Args:
        filename: Name of the file to modify
        content: New content to write

    Returns:
        Success message
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    file_path = user_dir / filename

    if not file_path.exists():
        return f"Error: File '{filename}' not found. Use upload_file to create new files."

    # Update file
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(content)

    # Re-index in RAG API
    file_id = get_file_id(user_id, filename)
    metadata = {
        "user_id": user_id,
        "filename": filename,
        "modified_at": datetime.utcnow().isoformat(),
        "size": len(content)
    }

    try:
        token = _generate_jwt_token(user_id)
        headers = {
            "Accept": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Delete old embeddings
            import urllib.parse
            encoded_file_id = urllib.parse.quote(file_id, safe='')
            await client.delete(f"{RAG_API_URL}/embed/{encoded_file_id}", headers=headers)

            # Create new embeddings using multipart/form-data
            # httpx expects files as tuple: (filename, file-like object, content_type)
            # Use only the basename for the file field to avoid RAG API directory creation issues
            # The full path is preserved in metadata
            file_content = io.BytesIO(content.encode('utf-8'))
            file_content.seek(0)  # Ensure we're at the beginning
            file_basename = Path(filename).name  # Extract just the filename, not the path
            files = {
                'file': (file_basename, file_content, 'text/markdown')
            }
            data = {
                'file_id': file_id
            }

            # Add storage_metadata if needed (optional, but LibreChat sends it)
            if metadata:
                data['storage_metadata'] = json.dumps(metadata)

            # Remove Content-Type header - httpx will set it correctly for multipart
            multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}

            response = await client.post(
                f"{RAG_API_URL}/embed",
                files=files,
                data=data,
                headers=multipart_headers,
                timeout=30.0
            )

            # Log response details for debugging errors
            if response.status_code >= 400:
                try:
                    error_detail = response.json()
                    print(f"RAG API error details for {filename}: {error_detail}")
                except:
                    print(f"RAG API error for {filename} (status {response.status_code}): {response.text[:500]}")

            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Include response details in error message
        error_msg = f"Failed to re-index file in RAG API: {e}"
        if e.response is not None:
            try:
                error_detail = e.response.json()
                error_msg += f" - Details: {error_detail}"
            except:
                error_msg += f" - Response: {e.response.text[:500]}"
        raise RuntimeError(error_msg)
    except Exception as e:
        raise RuntimeError(f"Failed to re-index file in RAG API: {e}")

    # Trigger Git commit/push if configured (non-blocking)
    try:
        await _trigger_git_commit(user_id, file_path)
    except Exception as e:
        # Git errors are non-fatal
        print(f"Warning: Git commit failed: {e}")

    return f"Successfully modified '{filename}' ({len(content)} bytes)"


async def delete_file(filename: str) -> str:
    """
    Delete a file from storage and remove from RAG API index.
    Also triggers Git commit and push if Git is configured.

    Args:
        filename: Name of the file to delete

    Returns:
        Success message
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    file_path = user_dir / filename

    if not file_path.exists():
        return f"Error: File '{filename}' not found in your storage."

    # Remove from RAG API
    file_id = get_file_id(user_id, filename)
    try:
        token = _generate_jwt_token(user_id)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            import urllib.parse
            encoded_file_id = urllib.parse.quote(file_id, safe='')
            await client.delete(f"{RAG_API_URL}/embed/{encoded_file_id}", headers=headers)
    except Exception as e:
        print(f"Warning: Failed to remove file from RAG API: {e}")
        # Continue with file deletion even if RAG API deletion fails

    # Check if file is in vault before deletion (for Git commit)
    vault_path = user_dir / "obsidian_vault"
    was_in_vault = False
    if vault_path.exists():
        try:
            file_path.relative_to(vault_path)
            was_in_vault = True
        except ValueError:
            pass

    # Delete file
    file_path.unlink()

    # Trigger Git commit/push if configured (non-blocking)
    # Note: For deletions, we need to commit the deletion in the vault
    if was_in_vault:
        try:
            await _trigger_git_commit(user_id, file_path, is_delete=True)
        except Exception as e:
            # Git errors are non-fatal
            print(f"Warning: Git commit failed: {e}")

    return f"Successfully deleted '{filename}'"


async def _get_query_embedding(query: str, user_id: str) -> list:
    """
    Get embedding vector for query text from RAG API.

    Uses RAG API's /local/embed endpoint if available, otherwise falls back to
    embedding as a temporary document and extracting from database.

    Args:
        query: Query text to embed
        user_id: User ID for authentication

    Returns:
        List of floats representing the embedding vector
    """
    token = _generate_jwt_token(user_id)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try /local/embed endpoint first (if it exists and doesn't store embeddings)
            try:
                response = await client.post(
                    f"{RAG_API_URL}/local/embed",
                    json={"text": query},
                    headers=headers
                )
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and "embedding" in data:
                        return data["embedding"]
                    elif isinstance(data, list):
                        return data
            except (httpx.HTTPStatusError, httpx.RequestError):
                # /local/embed doesn't exist or failed, fall back to database method
                pass

            # Fallback: Embed as temporary document and extract from database
            # The /embed endpoint expects multipart/form-data with a file, not JSON
            temp_file_id = f"temp_query_{user_id}_{int(datetime.now().timestamp())}"

            # Create multipart form data with the query text as a file
            import io
            files = {
                'file': ('query.txt', io.BytesIO(query.encode('utf-8')), 'text/plain')
            }
            data = {
                'file_id': temp_file_id
            }

            # Add metadata if needed
            metadata = {"user_id": user_id, "filename": "query.txt"}
            data['storage_metadata'] = json.dumps(metadata)

            # Remove Content-Type header - httpx will set it correctly for multipart
            multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
            if token:
                multipart_headers["Authorization"] = f"Bearer {token}"

            # Embed the query text using multipart/form-data
            embed_response = await client.post(
                f"{RAG_API_URL}/embed",
                files=files,
                data=data,
                headers=multipart_headers
            )
            embed_response.raise_for_status()

            # Now query the database to get the embedding vector we just created
            import asyncpg
            from pgvector.asyncpg import register_vector

            conn = await asyncpg.connect(
                host=VECTORDB_HOST,
                port=VECTORDB_PORT,
                database=VECTORDB_DB,
                user=VECTORDB_USER,
                password=VECTORDB_PASSWORD
            )
            await register_vector(conn)

            # Get the embedding we just created
            row = await conn.fetchrow(
                "SELECT embedding FROM langchain_pg_embedding WHERE custom_id = $1 LIMIT 1",
                temp_file_id
            )

            if row is not None and row.get('embedding') is not None:
                embedding = row['embedding']
                # Convert pgvector vector to list
                # Handle different types: numpy array, pgvector vector, or list
                if hasattr(embedding, 'tolist'):
                    embedding_list = embedding.tolist()
                elif hasattr(embedding, '__iter__') and not isinstance(embedding, (str, bytes)):
                    embedding_list = list(embedding)
                else:
                    embedding_list = embedding

                # Clean up temporary embedding
                await conn.execute(
                    "DELETE FROM langchain_pg_embedding WHERE custom_id = $1",
                    temp_file_id
                )
                await conn.close()

                return embedding_list
            else:
                await conn.close()
                raise RuntimeError("Could not retrieve embedding from database")

    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Failed to get query embedding from RAG API: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to get query embedding: {e}")


async def _query_vectordb_direct(query_embedding: list, user_id: str, max_results: int = 5) -> List[Dict]:
    """
    Query vectordb directly using pgvector for semantic search across all user files.
    Excludes .git files, hash files, and root directory files.

    Args:
        query_embedding: Embedding vector for the query
        user_id: User ID to filter results
        max_results: Maximum number of results to return

    Returns:
        List of result dictionaries with content, distance, and metadata (excluding filtered files)
    """
    try:
        import asyncpg
        from pgvector.asyncpg import register_vector
    except ImportError:
        raise RuntimeError("asyncpg and pgvector are required for direct vectordb queries. Install with: pip install asyncpg pgvector")

    try:
        # Get user directory for exclusion checking
        user_dir = get_user_storage_path(user_id)

        # Connect to PostgreSQL
        conn = await asyncpg.connect(
            host=VECTORDB_HOST,
            port=VECTORDB_PORT,
            database=VECTORDB_DB,
            user=VECTORDB_USER,
            password=VECTORDB_PASSWORD
        )

        # Register pgvector type
        await register_vector(conn)

        # Convert embedding list to pgvector format
        from pgvector.asyncpg import Vector
        query_vector = Vector(query_embedding)

        # Query using pgvector cosine distance
        # Table name is typically langchain_pg_embedding based on docs
        # Filter by user_id in metadata JSONB field
        # <=> operator is cosine distance (lower is better)
        # Also select custom_id to extract filename as fallback
        # Query more results than needed to account for filtering
        query_sql = """
            SELECT
                document,
                cmetadata,
                custom_id,
                1 - (embedding <=> $1::vector) as similarity
            FROM langchain_pg_embedding
            WHERE cmetadata->>'user_id' = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        """

        # Query more results to account for filtering (3x to ensure we get enough after filtering)
        rows = await conn.fetch(query_sql, query_vector, user_id, max_results * 3)

        results = []
        for row in rows:
            # Parse metadata - it might be a dict or JSON string
            metadata_raw = row['cmetadata']
            if isinstance(metadata_raw, str):
                try:
                    metadata = json.loads(metadata_raw)
                except:
                    metadata = {}
            elif isinstance(metadata_raw, dict):
                metadata = metadata_raw
            else:
                metadata = {}

            # Extract filename from metadata or fallback to custom_id
            filename = metadata.get("filename") if metadata else None
            if not filename:
                # Fallback: extract filename from custom_id (format: user_{user_id}_{filename})
                custom_id = row.get('custom_id', '')
                if custom_id and custom_id.startswith(f"user_{user_id}_"):
                    filename = custom_id.replace(f"user_{user_id}_", "", 1)
                else:
                    filename = "unknown"

            # Check if file should be excluded
            # Filename may be:
            # 1. Base filename (e.g., "note.md") - from MCP tools, indicates root-level file
            # 2. Relative path (e.g., "notes/note.md") - from Worker, relative to vault root, indicates subdirectory
            # For exclusion check, we only need to know if it's root-level (base filename) or subdirectory (has path separators)
            if filename != "unknown":
                # Check if filename is a base name (root-level) or has path separators (subdirectory)
                # Worker now stores relative paths (e.g., "notes/note.md"), so files with "/" are in subdirectories
                filename_path = Path(filename)

                # If filename has no parent (is base name), it's root-level and should be excluded
                # If filename has parent parts, it's in a subdirectory and should not be excluded
                if filename_path.parent == Path('.'):
                    # Base filename - root-level file, exclude it
                    continue

                # For files with path separators, check other exclusions (git, hash files, etc.)
                # Construct a path for exclusion checking (doesn't need to exist)
                file_path = user_dir / filename
                if _should_exclude_file(file_path, user_dir):
                    continue  # Skip excluded files

            results.append({
                "content": row['document'] or "",
                "distance": 1.0 - float(row['similarity']),  # Convert similarity to distance
                "metadata": metadata,
                "filename": filename  # Explicitly include filename
            })

            # Stop once we have enough results
            if len(results) >= max_results:
                break

        await conn.close()
        return results

    except Exception as e:
        raise RuntimeError(f"Failed to query vectordb directly: {e}")


async def search_files(query: str, max_results: int = 5) -> str:
    """
    Search user's files using semantic search by querying vectordb directly.

    This bypasses the RAG API's /query endpoint limitation (requires file_id)
    and queries all user files at once using pgvector similarity search.

    Args:
        query: Search query text
        max_results: Maximum number of results to return (default: 5)

    Returns:
        Search results with relevant excerpts
    """
    user_id = get_current_user()

    try:
        # Step 1: Get embedding for the query text from RAG API
        query_embedding = await _get_query_embedding(query, user_id)

        # Step 2: Query vectordb directly using pgvector
        results = await _query_vectordb_direct(query_embedding, user_id, max_results)

    except Exception as e:
        raise RuntimeError(f"Failed to search files: {e}")

    if not results:
        return f"No results found for query: '{query}'"

    # Sort by distance (lower is better) and limit results
    results.sort(key=lambda x: x["distance"])
    top_results = results[:max_results]

    # Format results
    output = f"Found {len(top_results)} result(s) for '{query}':\n\n"
    for i, result in enumerate(top_results, 1):
        relevance = 1.0 - result["distance"]  # Convert distance to relevance score
        excerpt = result["content"][:200] if result["content"] else ""  # First 200 chars

        # Get filename from result (explicitly included in _query_vectordb_direct)
        filename = result.get("filename", "unknown")
        if filename == "unknown":
            # Fallback: try to get from metadata
            metadata = result.get("metadata", {})
            filename = metadata.get("filename", "unknown")

        output += f"{i}. {filename} (relevance: {relevance:.3f})\n"
        output += f"   {excerpt}...\n\n"

    return output
