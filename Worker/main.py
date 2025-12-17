import os
import sys
import time
import json
import logging
import shutil
import hashlib
import httpx
import jwt
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import git
from git import Repo, GitCommandError
import re

# Configuration
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/storage"))
RAG_API_URL = os.environ.get("RAG_API_URL", "http://librechat-rag-api:8000")
RAG_API_JWT_SECRET = os.environ.get("RAG_API_JWT_SECRET", os.environ.get("JWT_SECRET", ""))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "60"))

# Throttling configuration to prevent RAG API overload
# Maximum number of files to index per sync cycle per user
MAX_FILES_PER_CYCLE = int(os.environ.get("MAX_FILES_PER_CYCLE", "10"))
# Delay between indexing requests (seconds)
INDEX_DELAY = float(os.environ.get("INDEX_DELAY", "0.5"))
# Maximum concurrent indexing operations
MAX_CONCURRENT_INDEXING = int(os.environ.get("MAX_CONCURRENT_INDEXING", "2"))

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
        logger.warning(f"Failed to store credentials for user {user_id}: {e}")

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
        logger.warning(f"Failed to retrieve token from store for user {user_id}: {e}")
        return None

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ObsidianSync")

class IndexingManager:
    """Handles indexing of markdown files to the RAG API."""

    def __init__(self, user_id: str, vault_path: Optional[Path] = None):
        self.user_id = user_id
        self.vault_path = vault_path or (STORAGE_ROOT / user_id / "obsidian_vault")

    def get_file_id(self, filename: str) -> str:
        """Generate file ID matching LibreChatMCP format."""
        return f"user_{self.user_id}_{filename}"

    def _cleanup_hidden_directory_files_from_rag(self):
        """Remove any files from directories starting with '.' that may have been indexed in the RAG API.

        This cleans up files from .git, .obsidian, .vscode, and any other hidden directories
        to ensure no files from hidden directories remain in the vector database.
        """
        try:
            token = self._generate_jwt_token()
            if not token:
                logger.warning("Cannot cleanup hidden directory files: JWT token not available")
                return

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}"
            }

            # Find all files in hidden directories (starting with '.')
            hidden_files = []
            if self.vault_path.exists():
                for root, dirs, files in os.walk(self.vault_path):
                    # Skip hidden directories themselves
                    if any(part.startswith('.') for part in Path(root).parts):
                        continue

                    for file in files:
                        if file.endswith('.md'):
                            path = Path(root) / file
                            # Check if file is in any hidden directory path
                            if any(part.startswith('.') for part in path.parts):
                                filename = path.name
                                file_id = self.get_file_id(filename)
                                hidden_files.append((file_id, str(path)))

            # Remove each hidden directory file from RAG API
            if hidden_files:
                logger.debug(f"Cleaning up {len(hidden_files)} file(s) from hidden directories in RAG API for user {self.user_id}")
                import urllib.parse

                for file_id, file_path in hidden_files:
                    try:
                        encoded_file_id = urllib.parse.quote(file_id, safe='')
                        response = httpx.delete(
                            f"{RAG_API_URL}/embed/{encoded_file_id}",
                            headers=headers,
                            timeout=10.0
                        )
                        if response.status_code in [200, 204]:
                            logger.debug(f"Removed hidden directory file from RAG API: {file_path} (file_id: {file_id})")
                        elif response.status_code == 404:
                            logger.debug(f"Hidden directory file not found in RAG API (already removed): {file_path}")
                        else:
                            logger.warning(f"Failed to remove hidden directory file from RAG API: {file_path} (status: {response.status_code})")
                    except Exception as e:
                        logger.warning(f"Error removing hidden directory file {file_path} from RAG API: {e}")
            else:
                logger.debug(f"No hidden directory files found to cleanup for user {self.user_id}")
        except Exception as e:
            logger.warning(f"Error during hidden directory cleanup for user {self.user_id}: {e}")

    def _generate_jwt_token(self) -> str:
        """Generate a JWT token for RAG API authentication."""
        if not RAG_API_JWT_SECRET:
            logger.warning("RAG_API_JWT_SECRET not set, RAG API requests may fail")
            return ""

        # Generate token with user_id in payload (matching LibreChat's format)
        # Token expires in 5 minutes (matching LibreChat's generateShortLivedToken)
        payload = {
            "id": self.user_id,
            "exp": datetime.utcnow() + timedelta(minutes=5)
        }
        return jwt.encode(payload, RAG_API_JWT_SECRET, algorithm="HS256")

    def index_file(self, file_path: Path, max_retries: int = 3, initial_delay: float = 1.0):
        """Send file content to RAG API with retry logic for transient failures.

        Args:
            file_path: Path to the file to index
            max_retries: Maximum number of retry attempts (default: 3)
            initial_delay: Initial delay in seconds before first retry (default: 1.0)

        Returns:
            True if indexing succeeded, False otherwise
        """
        # Store relative path from vault root (e.g., "notes/note.md") not just base filename
        # This is needed for proper exclusion checking in search
        try:
            relative_path = file_path.relative_to(self.vault_path)
            filename = str(relative_path)  # Use relative path, e.g., "notes/note.md"
        except ValueError:
            # File not in vault path, fall back to base filename
            filename = file_path.name

        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                file_id = self.get_file_id(filename)
                metadata = {
                    "user_id": self.user_id,
                    "filename": filename,
                    "updated_at": datetime.utcnow().isoformat(),
                    "source": "obsidian-git-sync"
                }

                payload = {
                    "file_id": file_id,
                    "content": content,
                    "metadata": metadata,
                    "chunk_size": CHUNK_SIZE,
                    "chunk_overlap": CHUNK_OVERLAP
                }

                # Generate JWT token for authentication
                token = self._generate_jwt_token()
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                # First delete existing embeddings to avoid stale data
                try:
                    # URL encode the file_id for the delete request
                    import urllib.parse
                    encoded_file_id = urllib.parse.quote(file_id, safe='')
                    httpx.delete(f"{RAG_API_URL}/embed/{encoded_file_id}", headers=headers, timeout=10.0)
                except (httpx.ConnectError, httpx.TimeoutException, ConnectionRefusedError):
                    # Connection errors during delete are OK, we'll retry
                    if attempt < max_retries:
                        continue
                except Exception:
                    pass # Ignore other errors (e.g., 404)

                # Create new embeddings
                # RAG API expects FormData with 'file' field (multipart/form-data), not JSON
                import io

                # Create multipart form data
                # httpx expects files as tuple: (filename, file-like object, content_type)
                files = {
                    'file': (filename, io.BytesIO(content.encode('utf-8')), 'text/markdown')
                }
                data = {
                    'file_id': file_id
                }

                # Add storage_metadata if needed (optional, but LibreChat sends it)
                if metadata:
                    data['storage_metadata'] = json.dumps(metadata)

                # Remove Content-Type header - httpx will set it correctly for multipart
                multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}

                response = httpx.post(
                    f"{RAG_API_URL}/embed",
                    files=files,
                    data=data,
                    headers=multipart_headers,
                    timeout=30.0
                )

                # Log response details for debugging 422 errors
                if response.status_code == 422:
                    try:
                        error_detail = response.json()
                        logger.error(f"422 error details for {filename}: {error_detail}")
                    except:
                        logger.error(f"422 error for {filename}: {response.text[:200]}")

                response.raise_for_status()
                logger.debug(f"Indexed {filename} for user {self.user_id}")
                return True

            except (httpx.ConnectError, httpx.TimeoutException, ConnectionRefusedError) as e:
                # Transient connection errors - retry with exponential backoff
                if attempt < max_retries:
                    delay = initial_delay * (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                    logger.warning(
                        f"Connection error indexing {filename} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Failed to index {file_path} after {max_retries + 1} attempts: {e}")
                    return False
            except httpx.HTTPStatusError as e:
                # HTTP errors (4xx, 5xx) - don't retry for client errors (4xx), but retry for server errors (5xx)
                if e.response.status_code >= 500 and attempt < max_retries:
                    delay = initial_delay * (2 ** attempt)
                    logger.warning(
                        f"Server error {e.response.status_code} indexing {filename} (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Failed to index {file_path}: HTTP {e.response.status_code} - {e}")
                    return False
            except Exception as e:
                # Other errors - log and fail
                logger.error(f"Failed to index {file_path}: {e}")
                return False

        return False

class GitSync:
    """Handles Git operations for a specific user vault."""

    def __init__(self, user_id: str, config: Dict):
        self.user_id = user_id
        self.repo_url = config['repo_url']
        self.token = config.get('token') # May be None if using credential store
        self.branch = config.get('branch', 'main')
        self.vault_path = STORAGE_ROOT / user_id / "obsidian_vault"
        self.indexer = IndexingManager(user_id, self.vault_path)
        # Cleanup any files from hidden directories (starting with '.') that may have been indexed previously
        # This runs once per sync cycle to ensure no hidden directory files remain in RAG API
        self.indexer._cleanup_hidden_directory_files_from_rag()

    def sync(self):
        """Main sync logic: Clone/Pull -> Index -> Commit/Push."""
        try:
            repo = self._ensure_repo()

            # Ensure credential store is configured before any Git operation
            setup_credential_store(repo, self.user_id, self.repo_url, self.token)

            # 1. Pull latest changes
            logger.debug(f"Pulling {self.user_id}...")
            try:
                repo.remotes.origin.pull(self.branch)
            except Exception as e:
                logger.warning(f"Pull failed for {self.user_id}: {e}")
                raise  # Re-raise to count as failure

            # 2. Index changes (LIFO by modification time)
            self._index_vault_files()

            # 3. Push local changes (made by MCP tools)
            if repo.is_dirty(untracked_files=True):
                logger.debug(f"Pushing changes for {self.user_id}...")
                repo.git.add(A=True)
                repo.index.commit(f"Sync from LibreChat: {datetime.utcnow().isoformat()}")
                repo.remotes.origin.push(self.branch)

        except Exception as e:
            logger.error(f"Sync failed for {self.user_id}: {e}")
            raise  # Re-raise so SyncManager can track the failure

    def _ensure_repo(self) -> Repo:
        """Clone if not exists, else return Repo object."""
        clean_url = clean_remote_url(self.repo_url)

        if not self.vault_path.exists():
            logger.info(f"Cloning vault for {self.user_id}...")
            self.vault_path.mkdir(parents=True, exist_ok=True)
            # Use clean URL for clone - credential helper will handle auth
            repo = Repo.clone_from(clean_url, self.vault_path, branch=self.branch)
            # Configure credential helper immediately after clone
            setup_credential_store(repo, self.user_id, self.repo_url, self.token)
            return repo

        repo = Repo(self.vault_path)

        # Update remote URL to clean URL (no token)
        if 'origin' in repo.remotes:
            repo.remotes.origin.set_url(clean_url)
        else:
            repo.create_remote('origin', clean_url)

        # Ensure credential helper is configured
        setup_credential_store(repo, self.user_id, self.repo_url, self.token)

        return repo

    def _index_vault_files(self):
        """Scan and index markdown files, prioritizing recent modifications.

        Implements throttling to prevent RAG API overload:
        - Limits number of files indexed per cycle
        - Adds delay between requests
        - Only indexes changed files
        - Explicitly excludes ALL directories starting with '.' (e.g., .git, .obsidian, .vscode)
        - Skips files in any directory path containing a '.' directory
        - Indexes files in LIFO order (most recently modified first)
        """
        md_files = []
        for root, dirs, files in os.walk(self.vault_path):
            # Skip directories that start with '.' (e.g., .git, .obsidian, .vscode, etc.)
            # Modify dirs in-place to prevent os.walk from descending into them
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            # Skip if current directory path contains any '.' directory
            if any(part.startswith('.') for part in Path(root).parts):
                continue

            for file in files:
                if file.endswith('.md'):
                    path = Path(root) / file
                    # Double-check: explicitly exclude any file in a '.' directory path
                    if any(part.startswith('.') for part in path.parts):
                        logger.debug(f"Skipping file in hidden directory: {path}")
                        continue

                    # Exclude files in root directory (only subdirectories allowed)
                    # This shouldn't happen in vault, but check to be safe
                    try:
                        relative_path = path.relative_to(self.vault_path)
                        if relative_path.parent == Path('.'):
                            logger.debug(f"Skipping root directory file: {path}")
                            continue
                    except ValueError:
                        # File not in vault path, skip it
                        logger.debug(f"Skipping file outside vault: {path}")
                        continue

                    md_files.append(path)

        if not md_files:
            logger.debug(f"No markdown files found for user {self.user_id}")
            return

        # Sort by modification time descending (LIFO) - most recently modified first
        md_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # Filter to only changed files (maintains LIFO order)
        changed_files = [f for f in md_files if self._has_changed(f)]

        if not changed_files:
            logger.debug(f"No changed files to index for user {self.user_id}")
            return

        # Throttle: Limit number of files processed per cycle
        # Already sorted by modification time (LIFO), so take first N
        files_to_index = changed_files[:MAX_FILES_PER_CYCLE]

        if len(changed_files) > MAX_FILES_PER_CYCLE:
            logger.debug(
                f"Throttling: {len(changed_files)} changed files found, "
                f"indexing only {MAX_FILES_PER_CYCLE} most recent (LIFO) for user {self.user_id}"
            )

        # Index files with delay between requests (already in LIFO order)
        indexed_count = 0
        for file_path in files_to_index:
            try:
                self.indexer.index_file(file_path)
                self._update_hash(file_path)
                indexed_count += 1

                # Add delay between requests to prevent API overload
                if indexed_count < len(files_to_index):  # Don't delay after last file
                    time.sleep(INDEX_DELAY)
            except Exception as e:
                logger.error(f"Failed to index {file_path} for user {self.user_id}: {e}")
                # Continue with next file even if one fails

        if indexed_count > 0:
            logger.debug(f"Indexed {indexed_count} file(s) for user {self.user_id}")

    def _has_changed(self, file_path: Path) -> bool:
        """Check against local hash DB if file changed."""
        hash_db_path = STORAGE_ROOT / self.user_id / "sync_hashes.json"
        try:
            if hash_db_path.exists():
                with open(hash_db_path, 'r') as f:
                    hashes = json.load(f)
            else:
                hashes = {}

            old_hash = hashes.get(str(file_path))
            new_hash = self._compute_hash(file_path)
            return old_hash != new_hash
        except Exception:
            return True

    def _update_hash(self, file_path: Path):
        """Update local hash DB."""
        hash_db_path = STORAGE_ROOT / self.user_id / "sync_hashes.json"
        try:
            if hash_db_path.exists():
                with open(hash_db_path, 'r') as f:
                    hashes = json.load(f)
            else:
                hashes = {}

            hashes[str(file_path)] = self._compute_hash(file_path)

            with open(hash_db_path, 'w') as f:
                json.dump(hashes, f)
        except Exception as e:
            logger.warning(f"Failed to update hash DB: {e}")

    def _compute_hash(self, file_path: Path) -> str:
        """MD5 hash of file."""
        return hashlib.md5(file_path.read_bytes()).hexdigest()


class SyncManager:
    """Manages sync cycles for all configured users."""

    MAX_FAILURES = 5  # Stop syncing after 5 consecutive failures

    def run(self):
        """Main loop."""
        logger.info("SyncManager started.")
        while True:
            self.process_cycle()
            time.sleep(SYNC_INTERVAL)

    def _update_sync_status(self, user_id: str, success: bool, error: Optional[str] = None):
        """Update sync status in git_config.json."""
        config_path = STORAGE_ROOT / user_id / "git_config.json"
        if not config_path.exists():
            return

        try:
            with open(config_path, 'r') as f:
                config = json.load(f)

            if success:
                # Check if sync was previously stopped and is now resuming
                was_stopped = config.get('stopped', False)
                had_failures = config.get('failure_count', 0) > 0

                # Reset failure count on success
                config['failure_count'] = 0
                config['last_success'] = datetime.utcnow().isoformat()
                config['stopped'] = False
                config.pop('last_failure', None)  # Remove last_failure on success

                # Log if sync resumed after being stopped
                if was_stopped:
                    logger.info(f"Sync resumed for user {user_id} after reset")
                # Log if this is first successful sync after failures (but not stopped)
                elif had_failures:
                    logger.info(f"Sync recovered for user {user_id} (previous failures cleared)")
            else:
                # Increment failure count
                current_failures = config.get('failure_count', 0) + 1
                config['failure_count'] = current_failures
                config['last_failure'] = datetime.utcnow().isoformat()
                config['last_failure_error'] = str(error) if error else "Unknown error"

                # Stop if max failures reached
                if current_failures >= self.MAX_FAILURES:
                    config['stopped'] = True
                    logger.error(f"SYNC STOPPED for user {user_id} after {current_failures} consecutive failures. Last error: {str(error) if error else 'Unknown'}")

            # Atomic write
            temp_path = config_path.with_suffix('.json.tmp')
            with open(temp_path, 'w') as f:
                json.dump(config, f, indent=2)
            temp_path.replace(config_path)

        except Exception as e:
            logger.error(f"Failed to update sync status for user {user_id}: {e}")

    def process_cycle(self):
        """Run one sync cycle across all users."""
        # Scan for users
        if not STORAGE_ROOT.exists():
            logger.warning(f"Storage root {STORAGE_ROOT} does not exist.")
            return

        user_count = 0
        skipped_count = 0
        for user_dir in STORAGE_ROOT.iterdir():
            if user_dir.is_dir():
                config_path = user_dir / "git_config.json"
                if config_path.exists():
                    user_count += 1
                    try:
                        with open(config_path, 'r') as f:
                            config = json.load(f)

                        user_id = user_dir.name

                        # Check if sync is stopped due to failures
                        if config.get('stopped', False):
                            skipped_count += 1
                            # Only log once per cycle if there are stopped syncs
                            continue

                        user_id = user_dir.name

                        # Handle missing token by retrieving from Git store
                        if not config.get('token'):
                            repo_url = config.get('repo_url')
                            if repo_url:
                                token = get_token_from_store(user_id, repo_url)
                                if token:
                                    config['token'] = token
                                else:
                                    logger.warning(f"No token found in config or Git store for user {user_id}")
                                    # We'll still try to sync, maybe the repo is public or doesn't need auth

                        syncer = GitSync(user_id, config)
                        syncer.sync()

                        # Mark as successful
                        self._update_sync_status(user_id, success=True)

                    except Exception as e:
                        logger.error(f"Sync failed for user {user_dir.name}: {e}")
                        # Mark as failed
                        self._update_sync_status(user_dir.name, success=False, error=str(e))


def main():
    logger.info("Obsidian Sync Service starting...")
    manager = SyncManager()
    manager.run()

if __name__ == "__main__":
    main()
