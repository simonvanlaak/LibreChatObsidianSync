"""
Obsidian Sync Tools for ObsidianSyncMCP Server

Provides tools for configuring and managing Obsidian vault synchronization via Git.
All operations are scoped to the authenticated user.
"""

import json
import aiofiles
import os
import sys
import re
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Tuple, List

# Ensure parent directory is in path for relative imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.storage import get_current_user, get_user_storage_path, get_obsidian_headers, get_user_vault_path

# Time and Sync Constants
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
MAX_CONSECUTIVE_FAILURES = 5
VERSION_TAG = "1.1"

def clean_remote_url(url: str) -> str:
    """Remove authentication tokens from a Git remote URL."""
    if not url:
        return url
    return re.sub(r'^(https?://)[^@/]+@', r'\1', url)

def setup_credential_store(user_id: str, repo_url: str, token: str) -> None:
    """Save the user's token into the persistent Git credential store."""
    user_storage = get_user_storage_path(user_id)
    cred_file = user_storage / ".git-credentials"
    cred_file.parent.mkdir(parents=True, exist_ok=True)

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
        logging.getLogger(__name__).warning(f"Failed to store credentials for user {user_id}: {e}")

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

def _is_unreplaced_placeholder(value: str) -> bool:
    """Check if a string is an unreplaced LibreChat placeholder."""
    return bool(value and value.startswith("{{") and value.endswith("}}"))

def _validate_config_values(repo_url: str, token: str, branch: str) -> None:
    """Ensure configuration values are not unreplaced placeholders."""
    if any(_is_unreplaced_placeholder(v) for v in [repo_url, token, branch]):
        raise ValueError(
            "Invalid configuration: LibreChat did not replace placeholder values. "
            "Please ensure customUserVars are properly set in LibreChat UI settings."
        )

async def auto_configure_obsidian_sync(user_id: str, repo_url: str, token: str, branch: str = "main") -> None:
    """Initialize synchronization configuration from provided credentials."""
    _validate_config_values(repo_url, token, branch)

    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"

    config = {
        "repo_url": clean_remote_url(repo_url),
        "branch": branch,
        "updated_at": datetime.utcnow().isoformat(),
        "auto_configured": True,
        "version": VERSION_TAG,
        "failure_count": 0,
        "stopped": False
    }

    setup_credential_store(user_id, repo_url, token)

    temp_path = config_path.with_suffix(".tmp")
    try:
        async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config, indent=2))
        temp_path.replace(config_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"Failed to save sync configuration: {e}")

async def configure_obsidian_sync(repo_url: Optional[str] = None, token: Optional[str] = None, branch: str = "main") -> str:
    """Manual tool to configure or update Git Sync for the Obsidian Vault."""
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"

    if not repo_url or not token:
        if not config_path.exists():
            return "No Obsidian sync configuration found. Please provide repo_url and token."

        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            config = json.loads(await f.read())

        status = "stopped" if config.get("stopped") else "active"
        return f"Current sync status: {status}. Repository: {config.get('repo_url')}"

    _validate_config_values(repo_url, token, branch)
    await auto_configure_obsidian_sync(user_id, repo_url, token, branch)
    return f"Successfully configured Obsidian Sync for: {repo_url}"

def _get_vault_stats(vault_path: Path, hash_db_path: Path) -> Tuple[int, int]:
    """Calculate total markdown files and currently synced files."""
    if not vault_path.exists():
        return 0, 0

    total_md_files = 0
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        if any(part.startswith('.') for part in Path(root).parts):
            continue
        total_md_files += sum(1 for f in files if f.endswith('.md'))

    synced_count = 0
    if hash_db_path.exists():
        try:
            with open(hash_db_path, 'r', encoding='utf-8') as f:
                hashes = json.loads(f.read())
            for path_str in hashes.keys():
                path = Path(path_str)
                if path.exists() and path.suffix == '.md':
                    try:
                        path.relative_to(vault_path)
                        synced_count += 1
                    except ValueError:
                        pass
        except Exception:
            pass

    return total_md_files, synced_count

def _calculate_eta(remaining_files: int) -> Optional[str]:
    """Estimate remaining sync time based on worker cycle limits."""
    if remaining_files <= 0:
        return None

    files_per_cycle = int(os.environ.get("MAX_FILES_PER_CYCLE", "10"))
    interval_seconds = int(os.environ.get("SYNC_INTERVAL", "60"))

    cycles_needed = (remaining_files + files_per_cycle - 1) // files_per_cycle
    total_minutes = int(round(cycles_needed * (interval_seconds / SECONDS_PER_MINUTE)))

    if total_minutes <= 0:
        return "less than 1 minute"

    days = total_minutes // (HOURS_PER_DAY * MINUTES_PER_HOUR)
    hours = (total_minutes % (HOURS_PER_DAY * MINUTES_PER_HOUR)) // MINUTES_PER_HOUR
    minutes = total_minutes % MINUTES_PER_HOUR

    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    if not parts: return "less than 1 minute"
    if len(parts) == 1: return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"

async def get_obsidian_sync_status() -> str:
    """Report the detailed synchronization status for the user's vault."""
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"

    if not config_path.exists():
        repo_url, token, branch = get_obsidian_headers()
        if repo_url and token and not any(_is_unreplaced_placeholder(v) for v in [repo_url, token]):
            await auto_configure_obsidian_sync(user_id, repo_url, token, branch or "main")
            return "✅ Configuration initialized from UI settings. Sync is now active."
        return "No Obsidian sync configuration found."

    async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
        config = json.loads(await f.read())

    repo = config.get('repo_url', 'unknown')
    if _is_unreplaced_placeholder(repo):
        return "⚠️ CONFIGURATION ERROR: Placeholders detected. Please update your UI settings."

    total, synced = _get_vault_stats(get_user_vault_path(user_id), user_dir / "sync_hashes.json")
    percentage = (synced / total * 100) if total > 0 else 0
    eta = _calculate_eta(total - synced)

    lines = [
        "=== Obsidian Sync Status ===",
        f"Repository: {repo}",
        f"Branch: {config.get('branch', 'main')}",
        f"Last Update: {config.get('updated_at', 'unknown')}",
        ""
    ]

    if total > 0:
        lines.append(f"**Progress:** {synced}/{total} files ({percentage:.1f}%)")
        if eta: lines.append(f"**Estimated completion:** {eta}")
        lines.append("")

    if config.get('stopped'):
        lines.append(f"❌ **STOPPED** - Failed {config.get('failure_count')} times.")
        if config.get('last_failure_error'):
            lines.append(f"Error: {config.get('last_failure_error')}")
    elif config.get('failure_count', 0) > 0:
        lines.append(f"⚠️ **WARNING:** {config.get('failure_count')} recent failures.")
    else:
        lines.append("✅ **ACTIVE**")

    if config.get('last_success'):
        lines.append(f"Last success: {config.get('last_success')}")

    return "\n".join(lines)

async def reset_obsidian_sync_failures() -> str:
    """Clear consecutive failure count to resume a stopped sync."""
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"

    if not config_path.exists():
        return "No configuration found to reset."

    async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
        config = json.loads(await f.read())

    config.update({
        "failure_count": 0,
        "stopped": False,
        "updated_at": datetime.utcnow().isoformat()
    })
    config.pop('last_failure', None)
    config.pop('last_failure_error', None)

    async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(config, indent=2))

    return "Successfully reset sync failure count. Sync will resume on the next cycle."

async def force_complete_reindex() -> str:
    """Delete the hash database to force a full re-embedding of all vault files."""
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    hash_db = user_dir / "sync_hashes.json"

    if hash_db.exists():
        hash_db.unlink()
        return "✅ Full reindex scheduled. All files will be refreshed on the next sync cycle."
    return "✅ Full reindex scheduled. No existing index was found."
