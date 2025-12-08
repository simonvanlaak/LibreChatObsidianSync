"""
Test that Worker functionality is preserved after migration.
These tests verify that core classes can be imported and work correctly.
"""
import pytest
import sys
from pathlib import Path

# Add Worker to path for imports
WORKER_PATH = Path(__file__).parent.parent.parent
if str(WORKER_PATH) not in sys.path:
    sys.path.insert(0, str(WORKER_PATH))


def test_sync_manager_imports():
    """Test that SyncManager can be imported"""
    try:
        from main import SyncManager
        assert SyncManager is not None
    except ImportError as e:
        pytest.fail(f"Failed to import SyncManager: {e}")


def test_indexing_manager_imports():
    """Test that IndexingManager can be imported"""
    try:
        from main import IndexingManager
        assert IndexingManager is not None
    except ImportError as e:
        pytest.fail(f"Failed to import IndexingManager: {e}")


def test_git_sync_imports():
    """Test that GitSync can be imported"""
    try:
        from main import GitSync
        assert GitSync is not None
    except ImportError as e:
        pytest.fail(f"Failed to import GitSync: {e}")
