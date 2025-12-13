"""
File Storage Tools for ObsidianSyncMCP Server

Provides user-isolated file storage with semantic search via RAG API integration.
All file operations are scoped to the authenticated user via user_id from request headers.
All file changes trigger Git commit and push (if Git is configured) and RAG indexing.
"""

import os
import json
import httpx
import aiofiles
import asyncio
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import git
from git import Repo, GitCommandError

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


def get_file_id(user_id: str, filename: str) -> str:
    """Generate a unique file ID for vectordb scoping"""
    return f"user_{user_id}_{filename}"


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
        
        if not repo_url or not token:
            return  # Invalid config
        
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
                auth_url = f"{repo_url.split('://')[0]}://{token}@{repo_url.split('://')[1]}"
                if 'origin' in repo.remotes:
                    repo.remotes.origin.set_url(auth_url)
                else:
                    repo.create_remote('origin', auth_url)
                
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
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{RAG_API_URL}/embed",
                json={
                    "file_id": file_id,
                    "content": content,
                    "metadata": metadata,
                    "chunk_size": CHUNK_SIZE,
                    "chunk_overlap": CHUNK_OVERLAP
                },
                headers=headers
            )
            response.raise_for_status()
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


async def list_files() -> str:
    """
    List all files in the user's storage with metadata.
    Includes files in the root directory and in subdirectories (e.g., obsidian_vault).
    
    Returns:
        Formatted list of files with metadata
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    
    files = []
    
    # List files in root directory
    for file_path in user_dir.iterdir():
        if file_path.is_file():
            stat = file_path.stat()
            files.append({
                "filename": file_path.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "path": str(file_path.relative_to(user_dir)),
                "directory": ""
            })
    
    # Recursively list files in subdirectories (e.g., obsidian_vault)
    for subdir in user_dir.iterdir():
        if subdir.is_dir() and not subdir.name.startswith('.'):
            for file_path in subdir.rglob('*'):
                if file_path.is_file():
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
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Delete old embeddings
            import urllib.parse
            encoded_file_id = urllib.parse.quote(file_id, safe='')
            await client.delete(f"{RAG_API_URL}/embed/{encoded_file_id}", headers=headers)
            
            # Create new embeddings
            response = await client.post(
                f"{RAG_API_URL}/embed",
                json={
                    "file_id": file_id,
                    "content": content,
                    "metadata": metadata,
                    "chunk_size": CHUNK_SIZE,
                    "chunk_overlap": CHUNK_OVERLAP
                },
                headers=headers
            )
            response.raise_for_status()
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


async def search_files(query: str, max_results: int = 5) -> str:
    """
    Search user's files using semantic search via RAG API.
    
    Follows LibreChat's file search pattern: queries each file individually
    and combines results, matching the format used in LibreChat's fileSearch.js.
    
    Args:
        query: Search query text
        max_results: Maximum number of results to return (default: 5)
        
    Returns:
        Search results with relevant excerpts
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    
    # Get all markdown files for the user (from vault and root)
    files_to_search = []
    vault_path = user_dir / "obsidian_vault"
    
    # Collect markdown files from vault
    # IMPORTANT: Use only filename (not path) for file_id to match Worker's indexing format
    # Worker uses: get_file_id(user_id, filename) where filename is just the basename
    if vault_path.exists():
        for file_path in vault_path.rglob("*.md"):
            if file_path.is_file():
                relative_path = file_path.relative_to(user_dir)
                # Use only filename (not full path) to match Worker's file_id format
                files_to_search.append({
                    "file_id": get_file_id(user_id, file_path.name),  # Just filename, not path
                    "filename": file_path.name,
                    "path": str(relative_path)
                })
    
    # Also check root directory for markdown files
    for file_path in user_dir.glob("*.md"):
        if file_path.is_file():
            files_to_search.append({
                "file_id": get_file_id(user_id, file_path.name),
                "filename": file_path.name,
                "path": file_path.name
            })
    
    if not files_to_search:
        return f"No markdown files found to search for query: '{query}'"
    
    # Generate JWT token for authentication
    token = _generate_jwt_token(user_id)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    # Query each file individually (matching LibreChat's pattern)
    all_results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        query_tasks = []
        for file_info in files_to_search:
            # Use same format as LibreChat: file_id, query, k
            query_tasks.append(
                client.post(
                    f"{RAG_API_URL}/query",
                    json={
                        "file_id": file_info["file_id"],
                        "query": query,
                        "k": max_results  # Query more per file, then we'll limit total
                    },
                    headers=headers
                )
            )
        
        # Execute all queries in parallel
        responses = await asyncio.gather(*query_tasks, return_exceptions=True)
        
        # Process responses (matching LibreChat's fileSearch.js pattern)
        for i, response in enumerate(responses):
            if isinstance(response, Exception):
                # Log but continue with other files
                continue
            
            try:
                response.raise_for_status()
                # In httpx, response.json() returns the JSON body directly
                # LibreChat's axios returns result.data which is the same
                data = response.json()
                
                # LibreChat's RAG API returns: array of [docInfo, distance] tuples
                # Format: [[{page_content, metadata}, distance], ...]
                # See fileSearch.js line 130: result.data.map(([docInfo, distance]) => ...)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, list) and len(item) == 2:
                            doc_info, distance = item
                            # Extract content and metadata (matching LibreChat's structure)
                            page_content = doc_info.get("page_content") or doc_info.get("text", "")
                            metadata = doc_info.get("metadata", {})
                            
                            all_results.append({
                                "filename": files_to_search[i]["filename"],
                                "path": files_to_search[i]["path"],
                                "content": page_content,
                                "distance": distance,
                                "metadata": metadata,
                                "file_id": files_to_search[i]["file_id"]
                            })
                # Fallback: handle other response formats
                elif isinstance(data, dict):
                    # Try results array
                    if "results" in data:
                        for result in data["results"]:
                            all_results.append({
                                "filename": files_to_search[i]["filename"],
                                "path": files_to_search[i]["path"],
                                "content": result.get("text", result.get("page_content", "")),
                                "distance": result.get("distance", 1.0 - result.get("score", 0.0)),
                                "metadata": result.get("metadata", {}),
                                "file_id": files_to_search[i]["file_id"]
                            })
            except httpx.HTTPStatusError as e:
                # Log detailed error for debugging
                if e.response.status_code == 422:
                    error_detail = e.response.text
                    print(f"Warning: 422 error querying file {files_to_search[i]['file_id']}: {error_detail}")
                # Continue with other files
                continue
            except Exception as e:
                # Log but continue with other files
                print(f"Warning: Error querying file {files_to_search[i]['file_id']}: {e}")
                continue
    
    if not all_results:
        return f"No results found for query: '{query}'"
    
    # Sort by distance (lower is better) and limit results
    all_results.sort(key=lambda x: x["distance"])
    top_results = all_results[:max_results]
    
    # Format results
    output = f"Found {len(top_results)} result(s) for '{query}':\n\n"
    for i, result in enumerate(top_results, 1):
        relevance = 1.0 - result["distance"]  # Convert distance to relevance score
        excerpt = result["content"][:200]  # First 200 chars
        filename = result["filename"]
        path = result["path"]
        
        output += f"{i}. {filename}"
        if path != filename:
            output += f" ({path})"
        output += f" (relevance: {relevance:.3f})\n"
        output += f"   {excerpt}...\n\n"
    
    return output
