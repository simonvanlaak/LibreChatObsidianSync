"""
Unit tests for storage utilities.
"""
import pytest
import os
import tempfile
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.storage import (
    get_current_user,
    set_current_user,
    get_user_storage_path,
)


def test_set_current_user():
    """Test setting current user in context"""
    set_current_user("test-user-123")
    assert get_current_user() == "test-user-123"


def test_get_current_user_raises_when_none():
    """Test that get_current_user raises when no user set"""
    set_current_user(None)
    with pytest.raises(ValueError, match="No user context"):
        get_current_user()


def test_get_user_storage_path(tmp_path, monkeypatch):
    """Test getting user storage path"""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    # Reload module to pick up new env var
    import importlib
    import shared.storage
    importlib.reload(shared.storage)
    from shared.storage import get_user_storage_path
    
    user_id = "test-user-123"
    expected = tmp_path / user_id
    result = get_user_storage_path(user_id)
    assert result == expected


def test_get_user_storage_path_creates_directory(tmp_path, monkeypatch):
    """Test that storage path directory is created if needed"""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    # Reload module to pick up new env var
    import importlib
    import shared.storage
    importlib.reload(shared.storage)
    from shared.storage import get_user_storage_path
    
    user_id = "test-user-456"
    path = get_user_storage_path(user_id)
    assert path.exists()
    assert path.is_dir()
