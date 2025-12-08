"""
Obsidian Sync Tools for ObsidianSyncMCP Server

Provides tools for configuring and managing Obsidian vault synchronization via Git.
All operations are scoped to the authenticated user.
"""

import json
import aiofiles
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.storage import get_current_user, get_user_storage_path


async def configure_obsidian_sync(repo_url: str = None, token: str = None, branch: str = "main") -> str:
    """
    Configure Git Sync for Obsidian Vault.
    
    All parameters are optional. If not provided, the tool will:
    1. Check if already configured (returns existing config)
    2. If not configured, prompt user to set via customUserVars in UI settings
    
    This tool can be used to:
    - Check current configuration status
    - Update existing configuration
    - Configure manually (if customUserVars not used)
    
    Args:
        repo_url: HTTP(S) URL of the Git repository (optional)
        token: Personal Access Token (optional)
        branch: Branch to sync (default: "main", optional)
        
    Returns:
        Status message with current configuration or success message
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"
    
    # If no parameters provided, check if already configured
    if not repo_url or not token:
        if config_path.exists():
            try:
                async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    existing_config = json.loads(content)
                repo = existing_config.get('repo_url', 'unknown')
                auto_configured = existing_config.get('auto_configured', False)
                config_source = "auto-configured via customUserVars" if auto_configured else "manually configured"
                
                # Check sync status
                stopped = existing_config.get('stopped', False)
                failure_count = existing_config.get('failure_count', 0)
                last_failure = existing_config.get('last_failure')
                last_success = existing_config.get('last_success')
                
                status_msg = f"Obsidian sync is already configured for repository: {repo}\n"
                status_msg += f"Configuration was {config_source}.\n"
                
                if stopped:
                    status_msg += f"\n⚠️ **SYNC STOPPED** - Failed {failure_count} times consecutively.\n"
                    if last_failure:
                        status_msg += f"Last failure: {last_failure}\n"
                    if existing_config.get('last_failure_error'):
                        status_msg += f"Error: {existing_config.get('last_failure_error')}\n"
                    status_msg += "Sync will not run until you reconfigure or reset the failure count.\n"
                    status_msg += "To fix: Update your credentials (repo_url/token) or use 'reset_obsidian_sync_failures' to clear failures."
                elif failure_count > 0:
                    status_msg += f"\n⚠️ Warning: {failure_count} recent failure(s).\n"
                    if last_failure:
                        status_msg += f"Last failure: {last_failure}\n"
                    if last_success:
                        status_msg += f"Last success: {last_success}\n"
                else:
                    status_msg += "\n✅ Sync is active and running.\n"
                    if last_success:
                        status_msg += f"Last successful sync: {last_success}\n"
                
                status_msg += "\nTo update, provide new repo_url and/or token parameters, or update customUserVars in UI settings."
                return status_msg
            except Exception as e:
                return (
                    f"No Obsidian sync configuration found.\n"
                    f"To configure, either:\n"
                    f"1. Set customUserVars in UI settings (OBSIDIAN_REPO_URL, OBSIDIAN_TOKEN, OBSIDIAN_BRANCH) - recommended\n"
                    f"2. Provide repo_url and token parameters to this tool\n"
                    f"Error reading existing config: {e}"
                )
        else:
            return (
                "No Obsidian sync configuration found.\n"
                "To configure, either:\n"
                "1. Set customUserVars in UI settings (OBSIDIAN_REPO_URL, OBSIDIAN_TOKEN, OBSIDIAN_BRANCH) - recommended\n"
                "2. Provide repo_url and token parameters to this tool"
            )
    
    # Validate that values are not placeholders
    def is_placeholder(value: str) -> bool:
        """Check if value is an unreplaced LibreChat placeholder."""
        return value.startswith("{{") and value.endswith("}}")
    
    if is_placeholder(repo_url) or is_placeholder(token) or is_placeholder(branch):
        raise ValueError(
            "Invalid configuration: LibreChat did not replace placeholder values. "
            "Please ensure customUserVars are properly set in LibreChat UI settings."
        )
    
    # Parameters provided - update/create configuration
    config = {
        "repo_url": repo_url,
        "token": token,
        "branch": branch,
        "updated_at": datetime.utcnow().isoformat(),
        "auto_configured": False,
        "version": "1.0",
        "failure_count": 0,  # Reset failure count when reconfiguring
        "stopped": False
    }
    
    temp_path = user_dir / "git_config.json.tmp"
    try:
        # Use atomic write pattern for consistency
        async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config, indent=2))
        temp_path.replace(config_path)  # Atomic rename
        return f"Successfully configured Obsidian Sync for repository: {repo_url}"
    except Exception as e:
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"Failed to save sync configuration: {e}")


async def get_obsidian_sync_status() -> str:
    """
    Get the current status of Obsidian sync, including failure information.
    
    Returns:
        Detailed status message including sync state, failure count, and last sync times
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"
    
    if not config_path.exists():
        return (
            "No Obsidian sync configuration found.\n"
            "To configure, either:\n"
            "1. Set customUserVars in UI settings (OBSIDIAN_REPO_URL, OBSIDIAN_TOKEN, OBSIDIAN_BRANCH) - recommended\n"
            "2. Use configure_obsidian_sync tool with repo_url and token parameters"
        )
    
    try:
        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = json.loads(content)
        
        repo = config.get('repo_url', 'unknown')
        branch = config.get('branch', 'main')
        auto_configured = config.get('auto_configured', False)
        config_source = "auto-configured via customUserVars" if auto_configured else "manually configured"
        
        # Check for placeholder values (LibreChat bug)
        def is_placeholder(value: str) -> bool:
            return value.startswith("{{") and value.endswith("}}")
        
        has_placeholders = is_placeholder(repo) or is_placeholder(branch) or is_placeholder(config.get('token', ''))
        
        # Calculate sync percentage
        vault_path = user_dir / "obsidian_vault"
        total_files = 0
        synced_files = 0
        
        if vault_path.exists():
            import os
            from pathlib import Path
            
            # Count total markdown files (excluding hidden directories)
            for root, dirs, files in os.walk(vault_path):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                if any(part.startswith('.') for part in Path(root).parts):
                    continue
                for file in files:
                    if file.endswith('.md'):
                        total_files += 1
            
            # Count synced files (files with entries in sync_hashes.json)
            hash_db_path = user_dir / "sync_hashes.json"
            if hash_db_path.exists():
                try:
                    async with aiofiles.open(hash_db_path, 'r', encoding='utf-8') as f:
                        hash_content = await f.read()
                        hashes = json.loads(hash_content)
                    
                    # Count how many hash entries correspond to actual .md files in vault
                    for hash_key in hashes.keys():
                        hash_path = Path(hash_key)
                        # Check if this path is within the vault and is a .md file
                        try:
                            if hash_path.exists() and hash_path.suffix == '.md':
                                # Check if it's within the vault directory
                                try:
                                    hash_path.relative_to(vault_path)
                                    synced_files += 1
                                except ValueError:
                                    pass  # Path not in vault
                        except Exception:
                            pass  # Path might not exist anymore
                except Exception:
                    pass  # If hash DB is corrupted, just show 0 synced
        
        sync_percentage = (synced_files / total_files * 100) if total_files > 0 else 0
        
        # Sync status information
        stopped = config.get('stopped', False)
        failure_count = config.get('failure_count', 0)
        last_failure = config.get('last_failure')
        last_failure_error = config.get('last_failure_error')
        last_success = config.get('last_success')
        updated_at = config.get('updated_at', 'unknown')
        
        status = []
        status.append("=== Obsidian Sync Status ===")
        
        if has_placeholders:
            status.append("⚠️ **CONFIGURATION ERROR**")
            status.append("LibreChat did not replace placeholder values in customUserVars.")
            status.append("")
            status.append("**Current (invalid) configuration:**")
            status.append(f"Repository URL: {repo}")
            status.append(f"Branch: {branch}")
            status.append("")
            status.append("**To fix:**")
            status.append("1. Go to LibreChat UI Settings → MCP Servers → obsidian_sync_mcp")
            status.append("2. Set customUserVars:")
            status.append("   - OBSIDIAN_REPO_URL: Your actual Git repository URL")
            status.append("   - OBSIDIAN_TOKEN: Your actual Personal Access Token")
            status.append("   - OBSIDIAN_BRANCH: Your branch name (e.g., 'main')")
            status.append("3. Save and reconnect to MCP server")
            status.append("")
            status.append("**Note:** Sync cannot run with placeholder values.")
            return "\n".join(status)
        
        status.append(f"Repository: {repo}")
        status.append(f"Branch: {branch}")
        status.append(f"Configuration: {config_source}")
        status.append(f"Last updated: {updated_at}")
        status.append("")
        
        # Add sync progress
        if total_files > 0:
            status.append(f"**Sync Progress:** {synced_files}/{total_files} files ({sync_percentage:.1f}%)")
            if sync_percentage < 100:
                remaining = total_files - synced_files
                status.append(f"Remaining: {remaining} file(s) to sync")
            status.append("")
        
        if stopped:
            status.append("❌ **SYNC STOPPED**")
            status.append(f"Sync has been stopped after {failure_count} consecutive failures.")
            status.append("")
            if last_failure:
                status.append(f"Last failure: {last_failure}")
            if last_failure_error:
                status.append(f"Last error: {last_failure_error}")
            status.append("")
            status.append("**To resume syncing:**")
            status.append("1. Fix the issue (check repository URL, token, network connectivity)")
            status.append("2. Use 'reset_obsidian_sync_failures' to clear the failure count")
            status.append("3. Or reconfigure with 'configure_obsidian_sync' (this also resets failures)")
        elif failure_count > 0:
            status.append(f"⚠️ **Warning: {failure_count} recent failure(s)**")
            status.append("Sync is still running but has encountered errors.")
            status.append("")
            if last_failure:
                status.append(f"Last failure: {last_failure}")
            if last_failure_error:
                status.append(f"Last error: {last_failure_error}")
            if last_success:
                status.append(f"Last success: {last_success}")
            status.append("")
            status.append("If failures continue, sync will be stopped after 5 consecutive failures.")
        else:
            status.append("✅ **Sync is active and running**")
            if last_success:
                status.append(f"Last successful sync: {last_success}")
            else:
                status.append("No sync attempts recorded yet.")
        
        return "\n".join(status)
        
    except Exception as e:
        return f"Error reading sync status: {e}"


async def reset_obsidian_sync_failures() -> str:
    """
    Reset the failure count for Obsidian sync, allowing it to run again if it was stopped.
    
    Returns:
        Success message
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    config_path = user_dir / "git_config.json"
    
    if not config_path.exists():
        return "No Obsidian sync configuration found. Nothing to reset."
    
    try:
        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = json.loads(content)
        
        # Reset failure tracking
        config['failure_count'] = 0
        config['stopped'] = False
        config.pop('last_failure', None)
        config.pop('last_failure_error', None)
        config['updated_at'] = datetime.utcnow().isoformat()
        
        # Atomic write
        temp_path = user_dir / "git_config.json.tmp"
        async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config, indent=2))
        temp_path.replace(config_path)
        
        return (
            "Successfully reset Obsidian sync failure count.\n"
            "Sync will resume on the next cycle. Make sure your repository URL and token are correct."
        )
        
    except Exception as e:
        raise RuntimeError(f"Failed to reset sync failures: {e}")


async def force_complete_reindex() -> str:
    """
    Force a complete reindex of all Obsidian vault files by deleting sync_hashes.json.
    
    This will cause the sync worker to reindex all markdown files in the next sync cycle,
    regardless of whether they have changed. Useful when:
    - RAG API data is corrupted or missing
    - Files were modified outside the sync process
    - User wants to refresh all indexed content
    
    Returns:
        Success message indicating reindex will occur on next sync cycle
    """
    user_id = get_current_user()
    user_dir = get_user_storage_path(user_id)
    
    # Check if sync is configured
    config_path = user_dir / "git_config.json"
    if not config_path.exists():
        return (
            "Obsidian sync is not configured. "
            "Please configure sync first using 'configure_obsidian_sync'."
        )
    
    # Delete sync_hashes.json to force reindex
    hash_db_path = user_dir / "sync_hashes.json"
    try:
        if hash_db_path.exists():
            hash_db_path.unlink()
            return (
                "✅ Complete reindex scheduled.\n\n"
                "The sync_hashes.json file has been deleted. "
                "On the next sync cycle, all markdown files in your Obsidian vault "
                "will be reindexed to the RAG API, regardless of whether they have changed.\n\n"
                "This may take some time depending on the number of files in your vault."
            )
        else:
            return (
                "✅ Complete reindex scheduled.\n\n"
                "No existing sync hashes found. "
                "All markdown files will be indexed on the next sync cycle."
            )
    except Exception as e:
        raise RuntimeError(f"Failed to force reindex: {e}")
