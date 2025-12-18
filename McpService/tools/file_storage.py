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
import re
import sys
import jwt
import urllib.parse
import asyncpg
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from git import Repo, GitCommandError
from pgvector.asyncpg import register_vector, Vector

# Ensure parent directory is in path for relative imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.storage import get_current_user, get_user_storage_path, get_user_vault_path

# API and Network Configuration
RAG_API_URL = os.environ.get("RAG_API_URL", "http://librechat-rag-api:8000")
RAG_API_JWT_SECRET = os.environ.get("RAG_API_JWT_SECRET", os.environ.get("JWT_SECRET", ""))
NETWORK_TIMEOUT_SECONDS = 30.0
JWT_EXPIRATION_MINUTES = 5

# RAG Configuration
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))
SEARCH_EXCERPT_LENGTH = 200
VECTOR_SEARCH_BUFFER_FACTOR = 3

# VectorDB Configuration
VECTORDB_HOST = os.environ.get("VECTORDB_HOST", "vectordb")
VECTORDB_PORT = int(os.environ.get("VECTORDB_PORT", "5432"))
VECTORDB_DB = os.environ.get("VECTORDB_DB", "mydatabase")
VECTORDB_USER = os.environ.get("VECTORDB_USER", "myuser")
VECTORDB_PASSWORD = os.environ.get("VECTORDB_PASSWORD", "mypassword")

def clean_remote_url(url: str) -> str:
    """Remove authentication tokens from a Git remote URL."""
    if not url:
        return url
    return re.sub(r'^(https?://)[^@/]+@', r'\1', url)

def setup_credential_store(repo: Repo, user_id: str, repo_url: str, token: str) -> None:
    """Configure Git to use persistent credential store for the user's token."""
    user_storage = get_user_storage_path(user_id)
    cred_file = user_storage / ".git-credentials"
    cred_file.parent.mkdir(parents=True, exist_ok=True)

    repo.git.config("credential.helper", f"store --file={cred_file}")

    if not token:
        return

    url_match = re.match(r'^(https?://)([^/]+)(/.*)?$', repo_url)
    if not url_match:
        return

    protocol, host_part, path = url_match.groups()
    host = host_part.split("@")[-1]
    path = path or "/"

    credential_input = f"protocol={protocol.rstrip('://')}\nhost={host}\npath={path}\nusername={token}\npassword=\n\n"

    try:
        subprocess.run(
            ["git", "-c", f"credential.helper=store --file={cred_file}", "credential", "approve"],
            input=credential_input.encode("utf-8"),
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to store credentials for user {user_id}: {e}")

def get_token_from_store(user_id: str, repo_url: str) -> Optional[str]:
    """Retrieve the token for a repository from the persistent Git store."""
    user_storage = get_user_storage_path(user_id)
    cred_file = user_storage / ".git-credentials"
    if not cred_file.exists():
        return None

    url_match = re.match(r'^(https?://)([^/]+)(/.*)?$', repo_url)
    if not url_match:
        return None

    protocol, host_part, path = url_match.groups()
    host = host_part.split("@")[-1]
    path = path or "/"

    credential_request = f"protocol={protocol.rstrip('://')}\nhost={host}\npath={path}\n"

    try:
        result = subprocess.run(
            ["git", "-c", f"credential.helper=store --file={cred_file}", "credential", "fill"],
            input=credential_request.encode("utf-8"),
            check=True,
            capture_output=True
        )
        for line in result.stdout.decode("utf-8").splitlines():
            if line.startswith("username="):
                return line.split("=", 1)[1]
    except subprocess.CalledProcessError:
        return None
    return None

def get_file_id(user_id: str, filename: str) -> str:
    """Generate a unique file ID for vectordb scoping."""
    return f"user_{user_id}_{filename}"

def _get_vault_paths(user_id: str, filename: str) -> Tuple[Path, str]:
    """Get consistent absolute path and vault-relative filename with traversal protection."""
    vault_dir = get_user_vault_path(user_id).resolve()
    clean_name = filename.lstrip('/')

    vault_prefix = "obsidian_vault/"
    if clean_name.startswith(vault_prefix):
        clean_name = clean_name[len(vault_prefix):]

    # joinpath and resolve to prevent traversal
    target_path = (vault_dir / clean_name).resolve()

    # Ensure the target path is still within the vault directory
    try:
        target_path.relative_to(vault_dir)
    except ValueError:
        raise ValueError(f"Security error: path traversal detected for '{filename}'")

    return target_path, f"{vault_prefix}{clean_name}"

def _should_exclude_file(file_path: Path, vault_dir: Path) -> bool:
    """Check if a file should be excluded from search and listing."""
    try:
        relative_path = file_path.relative_to(vault_dir)
    except ValueError:
        return True

    path_parts = relative_path.parts
    if '.git' in path_parts or any(part.startswith('.') for part in path_parts):
        return True

    return False

def _generate_jwt_token(user_id: str) -> str:
    """Generate a JWT token for RAG API authentication."""
    if not RAG_API_JWT_SECRET:
        return ""

    try:
        payload = {
            "id": user_id,
            "exp": datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES)
        }
        return jwt.encode(payload, RAG_API_JWT_SECRET, algorithm="HS256")
    except Exception:
        return ""

async def _load_git_config(user_id: str) -> Optional[Dict]:
    """Load Git synchronization configuration for a user."""
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"

    if not config_path.exists():
        return None

    try:
        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            return json.loads(await f.read())
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to read Git config: {e}")
        return None

async def _trigger_git_commit(user_id: str, file_path: Path, is_delete: bool = False) -> None:
    """Commit and push file changes to the user's Obsidian vault repository."""
    config = await _load_git_config(user_id)
    if not config or config.get('stopped', False):
        return

    repo_url = config.get('repo_url')
    branch = config.get('branch', 'main')
    if not repo_url:
        return

    token = config.get('token') or get_token_from_store(user_id, repo_url)
    vault_path = get_user_vault_path(user_id)

    try:
        repo = Repo(vault_path)
        relative_path = file_path.relative_to(vault_path)

        if is_delete:
            try:
                repo.git.rm(str(relative_path))
            except GitCommandError:
                pass
        else:
            repo.git.add(str(relative_path))

        if repo.is_dirty(untracked_files=True) or is_delete:
            action = "Delete" if is_delete else "Update"
            timestamp = datetime.utcnow().isoformat()
            repo.index.commit(f"{action} {file_path.name} from LibreChat: {timestamp}")

            clean_url = clean_remote_url(repo_url)
            if 'origin' in repo.remotes:
                repo.remotes.origin.set_url(clean_url)
            else:
                repo.create_remote('origin', clean_url)

            setup_credential_store(repo, user_id, repo_url, token)
            repo.remotes.origin.push(branch)

    except (GitCommandError, ValueError, Exception) as e:
        print(f"Warning: Git operation failed for {file_path}: {e}")

async def _index_in_rag_api(user_id: str, filename: str, content: str, is_update: bool = False) -> None:
    """Send file content to RAG API for embedding and indexing."""
    file_id = get_file_id(user_id, filename)
    token = _generate_jwt_token(user_id)

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=NETWORK_TIMEOUT_SECONDS) as client:
        if is_update:
            await client.delete(f"{RAG_API_URL}/embed/{urllib.parse.quote(file_id, safe='')}", headers=headers)

        metadata = {
            "user_id": user_id,
            "filename": filename,
            "size": len(content),
            "updated_at" if is_update else "created_at": datetime.utcnow().isoformat()
        }

        form_data = {'file_id': file_id, 'storage_metadata': json.dumps(metadata)}
        files = {'file': (Path(filename).name, io.BytesIO(content.encode('utf-8')), 'text/markdown')}

        multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}

        response = await client.post(f"{RAG_API_URL}/embed", files=files, data=form_data, headers=multipart_headers)

        if response.status_code >= 400:
            print(f"RAG API error for {filename} ({response.status_code}): {response.text[:500]}")
        response.raise_for_status()

async def upload_file(filename: str, content: str) -> str:
    """Upload a new file to the vault and trigger synchronization."""
    user_id = get_current_user()
    try:
        file_path, full_name = _get_vault_paths(user_id, filename)
    except ValueError as e:
        return f"Error: {e}"

    if file_path.exists():
        return f"Error: File '{filename}' already exists. Use modify_file to update it."

    file_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(content)

    try:
        await _index_in_rag_api(user_id, full_name, content)
    except Exception as e:
        file_path.unlink()
        raise RuntimeError(f"Failed to index file in RAG API: {e}")

    await _trigger_git_commit(user_id, file_path)
    return f"Successfully uploaded '{filename}' ({len(content)} bytes) to {file_path}"

async def create_note(title: str, content: str) -> str:
    """Convenience tool to create a markdown note with a title header."""
    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
    return await upload_file(f"{safe_title}.md", f"# {title}\n\n{content}")

async def list_files(directory: str = "") -> str:
    """
    List contents of a directory in the vault.

    Hint: Using the search_files feature instead is recommended when looking for a specific file.
    """
    user_id = get_current_user()
    vault_dir = get_user_vault_path(user_id).resolve()

    target_dir = vault_dir
    if directory:
        clean_dir = directory.lstrip('/')
        target_dir = (vault_dir / clean_dir).resolve()

        # Ensure the target directory is still within the vault directory
        try:
            target_dir.relative_to(vault_dir)
        except ValueError:
            return f"Error: Invalid directory path '{directory}'."

        if not target_dir.exists() or not target_dir.is_dir():
            return f"Error: Directory '{directory}' not found in your vault."

    files = []
    subdirs = []

    for item in target_dir.iterdir():
        if _should_exclude_file(item, vault_dir):
            continue

        rel_path = item.relative_to(vault_dir)

        if item.is_file():
            stat = item.stat()
            files.append({
                "path": str(rel_path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        elif item.is_dir():
            file_count = 0
            dir_count = 0
            for sub_item in item.rglob('*'):
                if _should_exclude_file(sub_item, vault_dir):
                    continue
                if sub_item.is_file():
                    file_count += 1
                elif sub_item.is_dir():
                    dir_count += 1

            subdirs.append({
                "path": str(rel_path),
                "file_count": file_count,
                "dir_count": dir_count
            })

    if not files and not subdirs:
        return f"No items found in '{directory or 'root'}'."

    output = f"Contents of '{directory or 'root'}' in your vault:\n"
    output += "Hint: Using the search_files feature instead is recommended when looking for a specific file.\n\n"

    if subdirs:
        output += "Directories:\n"
        for d in sorted(subdirs, key=lambda x: x['path']):
            output += f"- [DIR] {d['path']} ({d['file_count']} files, {d['dir_count']} dirs)\n"
        output += "\n"

    if files:
        output += "Files:\n"
        for f in sorted(files, key=lambda x: x['path']):
            output += f"- {f['path']}\n  Size: {f['size']} bytes\n  Modified: {f['modified']}\n\n"

    return output

async def read_file(filename: str) -> str:
    """Read the contents of a vault file."""
    user_id = get_current_user()
    try:
        file_path, _ = _get_vault_paths(user_id, filename)
    except ValueError as e:
        return f"Error: {e}"

    if not file_path.exists():
        return f"Error: File '{filename}' not found in your vault."

    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
        return await f.read()

async def modify_file(filename: str, content: str) -> str:
    """Update an existing vault file and trigger synchronization."""
    user_id = get_current_user()
    try:
        file_path, full_name = _get_vault_paths(user_id, filename)
    except ValueError as e:
        return f"Error: {e}"

    if not file_path.exists():
        return f"Error: File '{filename}' not found. Use upload_file to create new files."

    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(content)

    await _index_in_rag_api(user_id, full_name, content, is_update=True)
    await _trigger_git_commit(user_id, file_path)
    return f"Successfully modified '{filename}' ({len(content)} bytes)"

async def delete_file(filename: str) -> str:
    """Remove a file from the vault and the RAG index."""
    user_id = get_current_user()
    try:
        file_path, full_name = _get_vault_paths(user_id, filename)
    except ValueError as e:
        return f"Error: {e}"

    if not file_path.exists():
        return f"Error: File '{filename}' not found in your vault."

    file_id = get_file_id(user_id, full_name)
    token = _generate_jwt_token(user_id)

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=NETWORK_TIMEOUT_SECONDS) as client:
            await client.delete(f"{RAG_API_URL}/embed/{urllib.parse.quote(file_id, safe='')}", headers=headers)
    except Exception as e:
        print(f"Warning: Failed to remove file from RAG API: {e}")

    file_path.unlink()
    await _trigger_git_commit(user_id, file_path, is_delete=True)
    return f"Successfully deleted '{filename}'"

async def _get_query_embedding(query: str, user_id: str) -> list:
    """Get embedding vector for search query from RAG API."""
    token = _generate_jwt_token(user_id)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=NETWORK_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.post(f"{RAG_API_URL}/local/embed", json={"text": query}, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data["embedding"] if isinstance(data, dict) else data
        except (httpx.HTTPStatusError, httpx.RequestError):
            pass

        temp_id = f"temp_query_{user_id}_{int(datetime.now().timestamp())}"
        files = {'file': ('query.txt', io.BytesIO(query.encode('utf-8')), 'text/plain')}
        data = {'file_id': temp_id, 'storage_metadata': json.dumps({"user_id": user_id, "filename": "query.txt"})}

        multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
        await client.post(f"{RAG_API_URL}/embed", files=files, data=data, headers=multipart_headers)

        conn = await asyncpg.connect(
            host=VECTORDB_HOST,
            port=VECTORDB_PORT,
            database=VECTORDB_DB,
            user=VECTORDB_USER,
            password=VECTORDB_PASSWORD
        )
        try:
            await register_vector(conn)
            row = await conn.fetchrow(
                "SELECT embedding FROM langchain_pg_embedding WHERE custom_id = $1 LIMIT 1",
                temp_id
            )

            if not row or row.get('embedding') is None:
                raise RuntimeError("Could not retrieve embedding from database")

            embedding = row['embedding']
            result = embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)

            await conn.execute("DELETE FROM langchain_pg_embedding WHERE custom_id = $1", temp_id)
            return result
        finally:
            await conn.close()

async def _query_vectordb_direct(query_embedding: list, user_id: str, max_results: int = 5) -> List[Dict]:
    """Execute direct similarity search in pgvector database."""
    vault_dir = get_user_vault_path(user_id)
    conn = await asyncpg.connect(
        host=VECTORDB_HOST,
        port=VECTORDB_PORT,
        database=VECTORDB_DB,
        user=VECTORDB_USER,
        password=VECTORDB_PASSWORD
    )
    try:
        await register_vector(conn)

        sql = """
            SELECT document, cmetadata, custom_id, 1 - (embedding <=> $1::vector) as similarity
            FROM langchain_pg_embedding
            WHERE cmetadata->>'user_id' = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        """
        rows = await conn.fetch(sql, Vector(query_embedding), user_id, max_results * VECTOR_SEARCH_BUFFER_FACTOR)

        results = []
        for row in rows:
            metadata = row['cmetadata'] or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            name = metadata.get("filename") or (row.get('custom_id', '').replace(f"user_{user_id}_", "", 1) if row.get('custom_id') else "unknown")

            if not name:
                continue

            clean_name = name
            vault_prefix = "obsidian_vault/"

            if clean_name.startswith(vault_prefix):
                clean_name = clean_name[len(vault_prefix):]
                if not _should_exclude_file(vault_dir / clean_name, vault_dir):
                    results.append({
                        "content": row['document'] or "",
                        "relevance": float(row['similarity'] or 0.0),
                        "filename": clean_name
                    })
            elif clean_name == "unknown":
                results.append({
                    "content": row['document'] or "",
                    "relevance": float(row['similarity'] or 0.0),
                    "filename": "unknown"
                })
            else:
                # Legacy relative path (no obsidian_vault/ prefix)
                # We verify it exists in the vault to distinguish from root storage files
                full_path = vault_dir / clean_name
                if full_path.exists() and not _should_exclude_file(full_path, vault_dir):
                    results.append({
                        "content": row['document'] or "",
                        "relevance": float(row['similarity'] or 0.0),
                        "filename": clean_name
                    })

            if len(results) >= max_results:
                break

        return results
    finally:
        await conn.close()

async def search_files(query: str, max_results: int = 5) -> str:
    """Perform semantic search across all files in the Obsidian vault."""
    user_id = get_current_user()
    try:
        embedding = await _get_query_embedding(query, user_id)
        matches = await _query_vectordb_direct(embedding, user_id, max_results)
    except Exception as e:
        raise RuntimeError(f"Failed to search files: {e}")

    if not matches:
        return f"No results found for query: '{query}'"

    output = f"Found {len(matches)} result(s) for '{query}':\n\n"
    for i, match in enumerate(matches, 1):
        excerpt = match["content"][:SEARCH_EXCERPT_LENGTH]
        output += f"{i}. {match['filename']} (relevance: {match['relevance']:.3f})\n"
        output += f"   {excerpt}...\n\n"
    return output
