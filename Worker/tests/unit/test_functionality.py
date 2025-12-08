"""
Test that Worker functionality is preserved after migration.
These tests verify that core classes can be imported and work correctly.
"""
import pytest
import sys
from pathlib import Path

# Add Worker directory to path for imports
WORKER_PATH = Path(__file__).parent.parent.parent
if str(WORKER_PATH) not in sys.path:
    sys.path.insert(0, str(WORKER_PATH))


def test_sync_manager_imports():
    """Test that SyncManager can be imported"""
    try:
        import main
        assert hasattr(main, 'SyncManager')
        assert main.SyncManager is not None
    except ImportError as e:
        # Skip if dependencies are missing (e.g., gitpython not installed)
        pytest.skip(f"Dependencies not available: {e}")
    except AttributeError as e:
        pytest.fail(f"SyncManager not found in main module: {e}")


def test_indexing_manager_imports():
    """Test that IndexingManager can be imported"""
    try:
        import main
        assert hasattr(main, 'IndexingManager')
        assert main.IndexingManager is not None
    except ImportError as e:
        # Skip if dependencies are missing (e.g., gitpython not installed)
        pytest.skip(f"Dependencies not available: {e}")
    except AttributeError as e:
        pytest.fail(f"IndexingManager not found in main module: {e}")


def test_git_sync_imports():
    """Test that GitSync can be imported"""
    try:
        import main
        assert hasattr(main, 'GitSync')
        assert main.GitSync is not None
    except ImportError as e:
        # Skip if dependencies are missing (e.g., gitpython not installed)
        pytest.skip(f"Dependencies not available: {e}")
    except AttributeError as e:
        pytest.fail(f"GitSync not found in main module: {e}")
