
import pytest
from pathlib import Path
from tools import file_storage
from shared.storage import set_current_user
import tempfile
import shutil

@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        from shared import storage as shared_storage
        old_storage_root_shared = shared_storage.STORAGE_ROOT
        shared_storage.STORAGE_ROOT = Path(tmpdir)
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

from unittest.mock import patch, AsyncMock, MagicMock
import httpx

@pytest.fixture
def mock_rag_api():
    """Mock the RAG API HTTP client"""
    with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
        mock_instance = AsyncMock()
        mock_client.return_value.__aenter__.return_value = mock_instance

        # Mock successful responses
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.text = ""

        mock_instance.post.return_value = mock_response
        mock_instance.delete.return_value = mock_response

        yield mock_instance

@pytest.mark.asyncio
async def test_path_traversal_read(temp_storage_dir, setup_user):
    """Test that path traversal is blocked for reading files"""
    # Create a sensitive file outside the vault
    sensitive_file = temp_storage_dir / "sensitive.txt"
    sensitive_file.write_text("SENSITIVE DATA")

    # Try to read it using relative path traversal
    result = await file_storage.read_file("../../sensitive.txt")

    # It should not contain the sensitive data
    assert "SENSITIVE DATA" not in result
    assert "Error" in result

@pytest.mark.asyncio
async def test_path_traversal_upload(temp_storage_dir, setup_user, mock_rag_api):
    """Test that path traversal is blocked for uploading files"""
    # Try to upload outside the vault
    result = await file_storage.upload_file("../../traversal.txt", "Traversal attempt")

    traversal_path = (temp_storage_dir / "traversal.txt").resolve()
    print(f"DEBUG: temp_storage_dir={temp_storage_dir}")
    print(f"DEBUG: traversal_path={traversal_path}, exists={traversal_path.exists()}")

    assert not traversal_path.exists()
    assert "Error" in result or "traversal" in result.lower()

@pytest.mark.asyncio
async def test_list_files_path_traversal(temp_storage_dir, setup_user):
    """Test that path traversal is blocked for listing directories"""
    # Create a directory outside the vault
    outside_dir = temp_storage_dir / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("Secret")

    result = await file_storage.list_files("../../../outside")

    assert "secret.txt" not in result
    assert "Error" in result
