"""
Shared storage utilities for ObsidianSyncMCP.
Extracted from LibreChat-MCP for reuse.
"""
import os
import sqlite3
import json
from pathlib import Path
from contextvars import ContextVar
from typing import Optional, Dict, Any

# Storage configuration
STORAGE_ROOT_DEFAULT = "/tmp/obsidian-mcp-storage" if os.name != 'nt' else "./storage"
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", STORAGE_ROOT_DEFAULT))
DB_PATH = STORAGE_ROOT / "mcp_tokens.db"

# User context using contextvars for thread-safe per-request storage
_user_id_context: ContextVar[Optional[str]] = ContextVar('user_id', default=None)

# Obsidian config headers context (for auto-configuration)
_obsidian_repo_url_context: ContextVar[Optional[str]] = ContextVar('obsidian_repo_url', default=None)
_obsidian_token_context: ContextVar[Optional[str]] = ContextVar('obsidian_token', default=None)
_obsidian_branch_context: ContextVar[Optional[str]] = ContextVar('obsidian_branch', default=None)

def set_current_user(user_id: Optional[str]):
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

class TokenStore:
    """Persistent storage for MCP access tokens using SQLite"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_access_tokens (
                    token TEXT PRIMARY KEY,
                    user_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def save_mcp_token(self, token: str, user_id: str):
        """Save or update an MCP access token"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO mcp_access_tokens (token, user_id, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (token, user_id))
            conn.commit()

    def get_user_by_mcp_token(self, token: str) -> Optional[str]:
        """Retrieve a user_id associated with an MCP access token"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT user_id FROM mcp_access_tokens WHERE token = ?",
                (token,)
            )
            row = cursor.fetchone()
            if row:
                return row[0]
        return None

    def delete_token(self, user_id: str):
        """Delete tokens for a user"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mcp_access_tokens WHERE user_id = ?", (user_id,))
            conn.commit()

# Singleton instance
token_store = TokenStore()

def get_user_storage_path(user_id: str) -> Path:
    """Get the storage directory path for a user"""
    user_dir = STORAGE_ROOT / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_vault_path(user_id: str) -> Path:
    """Get the Obsidian vault directory path for a user"""
    user_dir = get_user_storage_path(user_id)
    vault_dir = user_dir / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    return vault_dir


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
