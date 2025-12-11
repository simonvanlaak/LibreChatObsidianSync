"""
Shared storage utilities for ObsidianSyncMCP.
Extracted from LibreChatMCP for reuse.
"""
import os
from pathlib import Path
from contextvars import ContextVar
from typing import Optional

# Storage configuration
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/storage"))

# User context using contextvars for thread-safe per-request storage
_user_id_context: ContextVar[Optional[str]] = ContextVar('user_id', default=None)

# Obsidian config headers context (for auto-configuration)
_obsidian_repo_url_context: ContextVar[Optional[str]] = ContextVar('obsidian_repo_url', default=None)
_obsidian_token_context: ContextVar[Optional[str]] = ContextVar('obsidian_token', default=None)
_obsidian_branch_context: ContextVar[Optional[str]] = ContextVar('obsidian_branch', default=None)


def set_current_user(user_id: str):
    """Set the current user context for file operations (thread-safe)"""
    _user_id_context.set(user_id)


def get_current_user() -> str:
    """Get the current user ID or raise error if not authenticated"""
    user_id = _user_id_context.get()
    if not user_id:
        raise ValueError("No user context set. User must be authenticated via OAuth.")
    
    # Reject placeholder strings (LibreChat bug: placeholders not replaced)
    if user_id.startswith("{{") and user_id.endswith("}}"):
        raise ValueError(
            f"Invalid user_id: '{user_id}' appears to be an unreplaced placeholder. "
            "This indicates LibreChat's processMCPEnv() didn't receive the user object. "
            "Please check LibreChat configuration or use OAuth authentication."
        )
    
    return user_id


def get_user_storage_path(user_id: str) -> Path:
    """Get the storage directory path for a user"""
    user_dir = STORAGE_ROOT / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def set_obsidian_headers(repo_url: Optional[str], token: Optional[str], branch: Optional[str]):
    """Set Obsidian config headers in context for auto-configuration"""
    _obsidian_repo_url_context.set(repo_url)
    _obsidian_token_context.set(token)
    _obsidian_branch_context.set(branch)


def get_obsidian_headers() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Get Obsidian config headers from context"""
    return (
        _obsidian_repo_url_context.get(),
        _obsidian_token_context.get(),
        _obsidian_branch_context.get()
    )


def clear_obsidian_headers():
    """Clear Obsidian config headers from context"""
    _obsidian_repo_url_context.set(None)
    _obsidian_token_context.set(None)
    _obsidian_branch_context.set(None)
