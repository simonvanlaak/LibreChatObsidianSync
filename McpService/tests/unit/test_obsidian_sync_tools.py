"""
Unit tests for Obsidian sync tools.
"""
import pytest
import json
import tempfile
from pathlib import Path
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tools.obsidian_sync import configure_obsidian_sync
from shared.storage import set_current_user


@pytest.fixture
def temp_storage(monkeypatch, tmp_path):
    """Set up temporary storage for tests"""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    # Reload modules to pick up new env var
    import importlib
    import shared.storage
    importlib.reload(shared.storage)
    return tmp_path


@pytest.mark.asyncio
async def test_configure_obsidian_sync_creates_git_config(temp_storage):
    """Test that configure_obsidian_sync creates git_config.json"""
    from shared.storage import set_current_user, get_user_storage_path
    set_current_user("test-user-123")
    
    result = await configure_obsidian_sync(
        repo_url="https://github.com/test/vault.git",
        token="test-token",
        branch="main"
    )
    
    user_dir = get_user_storage_path("test-user-123")
    config_path = user_dir / "git_config.json"
    assert config_path.exists()
    
    with open(config_path) as f:
        config = json.load(f)
        assert config["repo_url"] == "https://github.com/test/vault.git"
        assert config["token"] == "test-token"
        assert config["branch"] == "main"
        assert config["failure_count"] == 0
        assert config["stopped"] is False


@pytest.mark.asyncio
async def test_configure_obsidian_sync_requires_user_context():
    """Test that configure_obsidian_sync requires user context"""
    from shared.storage import set_current_user
    set_current_user(None)
    
    with pytest.raises(ValueError, match="No user context"):
        await configure_obsidian_sync(
            repo_url="https://github.com/test/vault.git",
            token="test-token"
        )


@pytest.mark.asyncio
async def test_configure_obsidian_sync_rejects_placeholder_values(temp_storage):
    """Test that configure_obsidian_sync rejects placeholder values"""
    from shared.storage import set_current_user
    set_current_user("test-user-123")
    
    with pytest.raises(ValueError, match="placeholder"):
        await configure_obsidian_sync(
            repo_url="{{OBSIDIAN_REPO_URL}}",
            token="test-token"
        )
