"""
Unit tests for directory scoping in list_files tool
"""

import pytest
import os
import tempfile
import aiofiles
from pathlib import Path
from unittest.mock import patch, MagicMock
from tools import file_storage
from shared.storage import set_current_user

@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        from shared import storage as shared_storage
        old_storage_root_shared = shared_storage.STORAGE_ROOT
        shared_storage.STORAGE_ROOT = Path(tmpdir)

        # Patch the file_storage module's vault path calculation if needed
        # but file_storage uses shared.storage functions

        yield Path(tmpdir)

        shared_storage.STORAGE_ROOT = old_storage_root_shared

@pytest.fixture
def setup_user():
    """Setup user context for tests"""
    set_current_user("test_user_123")
    yield "test_user_123"
    # Clear user context after test
    try:
        from shared.storage import _user_id_context
        _user_id_context.set(None)
    except:
        pass

@pytest.mark.asyncio
async def test_list_files_root_default(temp_storage_dir, setup_user):
    """Test that list_files defaults to root if no directory is provided"""
    vault_dir = temp_storage_dir / "test_user_123" / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Create a file in root
    (vault_dir / "root_file.md").write_text("Root content")

    # Create a subdir
    (vault_dir / "subdir1").mkdir()
    (vault_dir / "subdir1" / "inner.md").write_text("Inner content")

    result = await file_storage.list_files()

    assert "root_file.md" in result
    assert "subdir1" in result
    # It should recommend search_files
    assert "search_files" in result

@pytest.mark.asyncio
async def test_list_files_specific_directory(temp_storage_dir, setup_user):
    """Test listing a specific subdirectory"""
    vault_dir = temp_storage_dir / "test_user_123" / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    subdir = vault_dir / "projects"
    subdir.mkdir()
    (subdir / "project1.md").write_text("Project 1")
    (subdir / "project2.md").write_text("Project 2")

    # Another directory that should NOT be listed
    (vault_dir / "other").mkdir()
    (vault_dir / "other" / "ignored.md").write_text("Ignored")

    result = await file_storage.list_files(directory="projects")

    assert "project1.md" in result
    assert "project2.md" in result
    assert "other" not in result
    assert "ignored.md" not in result

@pytest.mark.asyncio
async def test_list_files_subdirectory_counts(temp_storage_dir, setup_user):
    """Test that subdirectories show file and directory counts"""
    vault_dir = temp_storage_dir / "test_user_123" / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Root -> projects/ (2 files, 1 dir)
    projects_dir = vault_dir / "projects"
    projects_dir.mkdir()
    (projects_dir / "p1.md").write_text("p1")
    (projects_dir / "p2.md").write_text("p2")
    (projects_dir / "archive").mkdir()

    result = await file_storage.list_files()

    # Check for counts in projects/
    # Expected format: [DIR] projects/ (2 files, 1 dirs)
    assert "[DIR] projects" in result
    assert "2 files" in result
    assert "1 dirs" in result

@pytest.mark.asyncio
async def test_list_files_nonexistent_directory(temp_storage_dir, setup_user):
    """Test error message for non-existent directory"""
    vault_dir = temp_storage_dir / "test_user_123" / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    result = await file_storage.list_files(directory="missing_folder")

    assert "Error: Directory 'missing_folder' not found" in result

@pytest.mark.asyncio
async def test_list_files_recommends_search(temp_storage_dir, setup_user):
    """Test that the output recommends using search_files"""
    vault_dir = temp_storage_dir / "test_user_123" / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "test.md").write_text("test")

    result = await file_storage.list_files()
    assert "search_files feature instead is recommended" in result
