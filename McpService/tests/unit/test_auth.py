"""
Unit tests for OAuth authentication.
"""
import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.auth import (
    generate_token,
    generate_auth_code,
    get_user_from_token,
    CLIENT_ID,
)


def test_generate_token():
    """Test token generation"""
    token = generate_token()
    assert token is not None
    assert len(token) > 0
    assert isinstance(token, str)


def test_generate_auth_code():
    """Test auth code generation"""
    code = generate_auth_code()
    assert code is not None
    assert len(code) > 0
    assert isinstance(code, str)


def test_get_user_from_token_returns_none_for_invalid():
    """Test that invalid token returns None"""
    assert get_user_from_token("invalid-token") is None


def test_oauth_client_id_is_obsidian_sync_mcp():
    """Test that OAuth client_id is obsidian_sync_mcp"""
    assert CLIENT_ID == "obsidian_sync_mcp"
