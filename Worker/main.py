import os
import sys
import time
import json
import logging
import hashlib
import httpx
import jwt
import subprocess
import urllib.parse
import io
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from git import Repo, GitCommandError
import re

# Persistent Storage Configuration
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/storage"))
RAG_API_URL = os.environ.get("RAG_API_URL", "http://librechat-rag-api:8000")
RAG_API_JWT_SECRET = os.environ.get("RAG_API_JWT_SECRET", os.environ.get("JWT_SECRET", ""))

# Sync Timing and Performance
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "60"))
MAX_FILES_PER_CYCLE = int(os.environ.get("MAX_FILES_PER_CYCLE", "10"))
INDEX_DELAY = float(os.environ.get("INDEX_DELAY", "0.5"))
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1.0
NETWORK_TIMEOUT = 30.0
CLEANUP_TIMEOUT = 10.0

# RAG Configuration
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))

# Sync Limits
MAX_CONSECUTIVE_FAILURES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ObsidianSync")

def clean_remote_url(url: str) -> str:
    """Remove authentication tokens from a Git remote URL for safe storage and display."""
    if not url:
        return url
    return re.sub(r'^(https?://)[^@/]+@', r'\1', url)

def setup_credential_store(repo: Repo, user_id: str, repo_url: str, token: str) -> None:
    """Configure Git to use a persistent credential store for the user's repository token."""
    user_storage = STORAGE_ROOT / user_id
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
        logger.warning(f"Failed to store credentials for user {user_id}: {e}")

def get_token_from_store(user_id: str, repo_url: str) -> Optional[str]:
    """Retrieve the token for a specific repository from the persistent Git store."""
    cred_file = STORAGE_ROOT / user_id / ".git-credentials"
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

class IndexingManager:
    """Handles coordination with the RAG API for embedding and indexing vault files."""

    def __init__(self, user_id: str, vault_path: Path):
        self.user_id = user_id
        self.vault_path = vault_path

    def get_file_id(self, filename: str) -> str:
        """Generate a consistent file ID for vector database scoping."""
        return f"user_{self.user_id}_{filename}"

    def cleanup_hidden_directory_files(self) -> None:
        """Remove previously indexed files that are now in excluded hidden directories."""
        try:
            token = self._generate_jwt_token()
            if not token:
                return

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            hidden_files = self._find_hidden_markdown_files()

            for file_id, file_path in hidden_files:
                self._delete_from_rag(file_id, file_path, headers)
        except Exception as e:
            logger.warning(f"Hidden directory cleanup failed for user {self.user_id}: {e}")

    def _find_hidden_markdown_files(self) -> List[Tuple[str, str]]:
        """Identify all markdown files located within hidden directories."""
        hidden_files = []
        if not self.vault_path.exists():
            return hidden_files

        for root, dirs, files in os.walk(self.vault_path):
            if any(part.startswith('.') for part in Path(root).parts):
                for file in files:
                    if file.endswith('.md'):
                        path = Path(root) / file
                        hidden_files.append((self.get_file_id(path.name), str(path)))
        return hidden_files

    def _delete_from_rag(self, file_id: str, file_path: str, headers: Dict) -> None:
        """Send a delete request to the RAG API for a specific file ID."""
        try:
            encoded_id = urllib.parse.quote(file_id, safe='')
            response = httpx.delete(f"{RAG_API_URL}/embed/{encoded_id}", headers=headers, timeout=CLEANUP_TIMEOUT)
            if response.status_code in [200, 204]:
                logger.debug(f"Removed hidden file from RAG: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to remove hidden file {file_path}: {e}")

    def _generate_jwt_token(self) -> str:
        """Create a short-lived JWT for RAG API authentication."""
        if not RAG_API_JWT_SECRET:
            return ""
        payload = {
            "id": self.user_id,
            "exp": datetime.utcnow() + timedelta(minutes=5)
        }
        return jwt.encode(payload, RAG_API_JWT_SECRET, algorithm="HS256")

    def index_file(self, file_path: Path) -> bool:
        """Upload file content to RAG API with retry logic and stale data cleanup."""
        filename = self._get_relative_filename(file_path)

        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._process_indexing_request(file_path, filename)
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if self._should_retry(e, attempt):
                    self._backoff_delay(attempt, filename)
                    continue
                logger.error(f"Indexing failed for {filename} after {attempt + 1} attempts: {e}")
                return False
        return False

    def _get_relative_filename(self, file_path: Path) -> str:
        """Determine the vault-relative path for a file."""
        try:
            return str(file_path.relative_to(self.vault_path))
        except ValueError:
            return file_path.name

    def _should_retry(self, error: Exception, attempt: int) -> bool:
        """Determine if a failed request should be retried."""
        if attempt >= MAX_RETRIES:
            return False
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code >= 500
        return True

    def _backoff_delay(self, attempt: int, filename: str) -> None:
        """Wait for an increasing amount of time before retrying a failed request."""
        delay = INITIAL_RETRY_DELAY * (2 ** attempt)
        logger.warning(f"Retrying indexing for {filename} in {delay}s...")
        time.sleep(delay)

    def _process_indexing_request(self, file_path: Path, filename: str) -> bool:
        """Execute the actual delete-then-post sequence for a file."""
        content = file_path.read_text(encoding='utf-8')
        file_id = self.get_file_id(filename)
        token = self._generate_jwt_token()

        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._clear_stale_embeddings(file_id, headers)
        return self._upload_embeddings(file_id, filename, content, headers)

    def _clear_stale_embeddings(self, file_id: str, headers: Dict) -> None:
        """Remove existing embeddings from the RAG API to ensure freshness."""
        try:
            encoded_id = urllib.parse.quote(file_id, safe='')
            httpx.delete(f"{RAG_API_URL}/embed/{encoded_id}", headers=headers, timeout=NETWORK_TIMEOUT)
        except Exception:
            pass

    def _upload_embeddings(self, file_id: str, filename: str, content: str, headers: Dict) -> bool:
        """Upload the file content and metadata to the RAG API for embedding."""
        metadata = {
            "user_id": self.user_id,
            "filename": filename,
            "updated_at": datetime.utcnow().isoformat(),
            "source": "obsidian-git-sync"
        }

        files = {'file': (filename, io.BytesIO(content.encode('utf-8')), 'text/markdown')}
        data = {'file_id': file_id, 'storage_metadata': json.dumps(metadata)}

        multipart_headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}

        response = httpx.post(
            f"{RAG_API_URL}/embed",
            files=files,
            data=data,
            headers=multipart_headers,
            timeout=NETWORK_TIMEOUT
        )
        response.raise_for_status()
        return True

class GitSync:
    """Orchestrates the synchronization process for a specific user's vault."""

    def __init__(self, user_id: str, config: Dict):
        self.user_id = user_id
        self.repo_url = config['repo_url']
        self.token = config.get('token')
        self.branch = config.get('branch', 'main')
        self.vault_path = STORAGE_ROOT / user_id / "obsidian_vault"
        self.indexer = IndexingManager(user_id, self.vault_path)
        self.indexer.cleanup_hidden_directory_files()

    def sync(self) -> None:
        """Execute a full synchronization cycle: Pull -> Index -> Push."""
        repo = self._ensure_repo()
        setup_credential_store(repo, self.user_id, self.repo_url, self.token)

        self._pull_latest_changes(repo)
        self._index_vault_files(repo)
        self._push_local_changes(repo)

    def _ensure_repo(self) -> Repo:
        """Return an existing Repo instance or clone the vault if it's missing."""
        clean_url = clean_remote_url(self.repo_url)

        if not self.vault_path.exists():
            self.vault_path.mkdir(parents=True, exist_ok=True)
            repo = Repo.clone_from(clean_url, self.vault_path, branch=self.branch)
            setup_credential_store(repo, self.user_id, self.repo_url, self.token)
            return repo

        repo = Repo(self.vault_path)
        if 'origin' in repo.remotes:
            repo.remotes.origin.set_url(clean_url)
        else:
            repo.create_remote('origin', clean_url)

        setup_credential_store(repo, self.user_id, self.repo_url, self.token)
        return repo

    def _pull_latest_changes(self, repo: Repo, max_retries: int = 3) -> None:
        """Attempt to pull the latest changes from the remote repository with retry logic."""
        for attempt in range(max_retries + 1):
            try:
                repo.remotes.origin.pull(self.branch)
                return
            except Exception as e:
                if attempt < max_retries:
                    delay = INITIAL_RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"Git pull failed for {self.user_id} (attempt {attempt + 1}): {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                logger.warning(f"Git pull failed for user {self.user_id} after {max_retries + 1} attempts: {e}")
                raise

    def _push_local_changes(self, repo: Repo, max_retries: int = 3) -> None:
        """Commit and push any local modifications to the remote repository with retry logic."""
        if repo.is_dirty(untracked_files=True):
            repo.git.add(A=True)
            timestamp = datetime.utcnow().isoformat()
            repo.index.commit(f"Sync from LibreChat: {timestamp}")

            for attempt in range(max_retries + 1):
                try:
                    repo.remotes.origin.push(self.branch)
                    return
                except Exception as e:
                    if attempt < max_retries:
                        delay = INITIAL_RETRY_DELAY * (2 ** attempt)
                        logger.warning(f"Git push failed for {self.user_id} (attempt {attempt + 1}): {e}. Retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    logger.error(f"Git push failed for user {self.user_id} after {max_retries + 1} attempts: {e}")
                    raise

    def _index_vault_files(self, repo: Repo) -> None:
        """Identify and index recently modified markdown files, respecting throttling limits."""
        md_files = self._get_eligible_markdown_files(repo)
        if not md_files:
            return

        md_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        changed_files = [f for f in md_files if self._has_changed(f)]

        if not changed_files:
            return

        self._process_indexing_queue(changed_files[:MAX_FILES_PER_CYCLE])

    def _get_eligible_markdown_files(self, repo: Repo) -> List[Path]:
        """Collect markdown files using Git to optimize discovery, excluding hidden directories."""
        try:
            # Use git ls-files to quickly find all tracked and untracked markdown files
            # -z for null termination, -c for cached, -o for others (untracked), --exclude-standard for .gitignore
            files_output = repo.git.ls_files("-z", "-c", "-o", "--exclude-standard", "*.md")
            relative_paths = [p for p in files_output.split('\0') if p]

            md_files = []
            for rel_path in relative_paths:
                path = self.vault_path / rel_path
                # Check if file exists and is not in a hidden directory
                if path.exists() and not any(part.startswith('.') for part in path.parts):
                    md_files.append(path)
            return md_files
        except GitCommandError as e:
            logger.warning(f"Git ls-files failed, falling back to os.walk: {e}")
            return self._fallback_get_markdown_files()

    def _fallback_get_markdown_files(self) -> List[Path]:
        """Fallback method to collect markdown files using os.walk."""
        md_files = []
        for root, dirs, files in os.walk(self.vault_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            if any(part.startswith('.') for part in Path(root).parts):
                continue

            for file in files:
                if file.endswith('.md'):
                    path = Path(root) / file
                    if not any(part.startswith('.') for part in path.parts):
                        md_files.append(path)
        return md_files

    def _process_indexing_queue(self, queue: List[Path]) -> None:
        """Iterate through the queue of files to index, applying rate limiting."""
        indexed_count = 0
        for file_path in queue:
            try:
                if self.indexer.index_file(file_path):
                    self._update_hash(file_path)
                    indexed_count += 1
                    if indexed_count < len(queue):
                        time.sleep(INDEX_DELAY)
            except Exception as e:
                logger.error(f"Indexing error for {file_path}: {e}")

    def _has_changed(self, file_path: Path) -> bool:
        """Compare the current file hash against the stored hash to detect changes."""
        hash_db_path = STORAGE_ROOT / self.user_id / "sync_hashes.json"
        try:
            if not hash_db_path.exists():
                return True
            hashes = json.loads(hash_db_path.read_text())
            current_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
            return hashes.get(str(file_path)) != current_hash
        except Exception:
            return True

    def _update_hash(self, file_path: Path) -> None:
        """Persist the new hash of a successfully indexed file."""
        hash_db_path = STORAGE_ROOT / self.user_id / "sync_hashes.json"
        try:
            hashes = json.loads(hash_db_path.read_text()) if hash_db_path.exists() else {}
            hashes[str(file_path)] = hashlib.md5(file_path.read_bytes()).hexdigest()

            temp_path = hash_db_path.with_suffix('.tmp')
            temp_path.write_text(json.dumps(hashes))
            temp_path.replace(hash_db_path)
        except Exception as e:
            logger.warning(f"Hash database update failed: {e}")

class SyncManager:
    """Manages the lifecycle and execution of sync cycles for all users."""

    def run(self) -> None:
        """Enter the continuous synchronization loop."""
        logger.info("SyncManager started.")
        while True:
            self.process_cycle()
            time.sleep(SYNC_INTERVAL)

    def process_cycle(self) -> None:
        """Scan for configured users and execute a sync cycle for each."""
        if not STORAGE_ROOT.exists():
            return

        for user_dir in STORAGE_ROOT.iterdir():
            if user_dir.is_dir():
                config_path = user_dir / "git_config.json"
                if config_path.exists():
                    self._sync_user(user_dir.name, config_path)

    def _sync_user(self, user_id: str, config_path: Path) -> None:
        """Attempt to synchronize the vault for a specific user."""
        try:
            config = json.loads(config_path.read_text())
            if config.get('stopped', False):
                return

            self._enrich_config_with_token(user_id, config)
            GitSync(user_id, config).sync()
            self._update_status(user_id, config_path, success=True)
        except Exception as e:
            logger.error(f"Sync failed for user {user_id}: {e}")
            self._update_status(user_id, config_path, success=False, error=str(e))

    def _enrich_config_with_token(self, user_id: str, config: Dict) -> None:
        """Retrieve the Git token from the credential store if it's missing from the config."""
        if not config.get('token'):
            repo_url = config.get('repo_url')
            if repo_url:
                config['token'] = get_token_from_store(user_id, repo_url)

    def _update_status(self, user_id: str, config_path: Path, success: bool, error: Optional[str] = None) -> None:
        """Update the user's sync configuration with latest status and failure counts."""
        try:
            config = json.loads(config_path.read_text())
            if success:
                self._mark_success(config)
            else:
                self._mark_failure(config, error, user_id)

            temp_path = config_path.with_suffix('.tmp')
            temp_path.write_text(json.dumps(config, indent=2))
            temp_path.replace(config_path)
        except Exception as e:
            logger.error(f"Status update failed for user {user_id}: {e}")

    def _mark_success(self, config: Dict) -> None:
        """Reset failure tracking and record successful sync timestamp."""
        config.update({
            "failure_count": 0,
            "last_success": datetime.utcnow().isoformat(),
            "stopped": False
        })
        config.pop('last_failure', None)
        config.pop('last_failure_error', None)

    def _mark_failure(self, config: Dict, error: Optional[str], user_id: str) -> None:
        """Increment failure count and stop sync if the limit is exceeded."""
        count = config.get('failure_count', 0) + 1
        config.update({
            "failure_count": count,
            "last_failure": datetime.utcnow().isoformat(),
            "last_failure_error": error or "Unknown error"
        })
        if count >= MAX_CONSECUTIVE_FAILURES:
            config['stopped'] = True
            logger.error(f"Sync disabled for {user_id} after {count} failures.")

def main():
    """Service entry point."""
    logger.info("Obsidian Sync Service starting...")
    SyncManager().run()

if __name__ == "__main__":
    main()
