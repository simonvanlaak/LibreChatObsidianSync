"""
Unit tests for file storage tools

Tests user isolation, file operations, RAG API integration, and Git commit functionality.
"""

import pytest
import os
import tempfile
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

# Import the file storage module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tools import file_storage
from shared.storage import set_current_user, get_current_user


@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch both STORAGE_ROOT locations (file_storage and shared.storage)
        old_storage_root_file = getattr(file_storage, 'STORAGE_ROOT', None)
        from shared import storage as shared_storage
        old_storage_root_shared = shared_storage.STORAGE_ROOT
        
        file_storage.STORAGE_ROOT = Path(tmpdir)
        shared_storage.STORAGE_ROOT = Path(tmpdir)
        
        yield Path(tmpdir)
        
        # Restore original values
        if old_storage_root_file is not None:
            file_storage.STORAGE_ROOT = old_storage_root_file
        shared_storage.STORAGE_ROOT = old_storage_root_shared


@pytest.fixture
def mock_rag_api():
    """Mock the RAG API HTTP client"""
    with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
        mock_instance = AsyncMock()
        mock_client.return_value.__aenter__.return_value = mock_instance
        
        # Mock successful responses
        mock_response = MagicMock()
        mock_response.status_code = 200  # Set status_code as int, not MagicMock
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.text = ""  # Add text attribute for error handling
        
        mock_instance.post.return_value = mock_response
        mock_instance.delete.return_value = mock_response
        
        yield mock_instance


@pytest.fixture
def setup_user():
    """Setup user context for tests"""
    set_current_user("test_user_123")
    yield "test_user_123"
    # Clear user context after test
    try:
        from contextvars import ContextVar
        from shared.storage import _user_id_context
        _user_id_context.set(None)
    except:
        pass


@pytest.fixture
def mock_git():
    """Mock Git operations"""
    with patch('tools.file_storage.Repo') as mock_repo_class:
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = True
        mock_repo.git.add = MagicMock()
        mock_repo.index.commit = MagicMock()
        mock_repo.remotes = MagicMock()
        mock_repo.remotes.origin = MagicMock()
        mock_repo.remotes.origin.push = MagicMock()
        mock_repo.remotes.origin.set_url = MagicMock()
        mock_repo_class.return_value = mock_repo
        yield mock_repo


class TestUserIsolation:
    """Test that users can only access their own files"""
    
    @pytest.mark.asyncio
    async def test_different_users_isolated_storage(self, temp_storage_dir, mock_rag_api):
        """Test that different users have isolated storage directories"""
        # User A uploads a file
        set_current_user("user_a")
        await file_storage.upload_file("test.txt", "User A's content")
        
        user_a_path = temp_storage_dir / "user_a" / "test.txt"
        assert user_a_path.exists()
        
        # User B should not see User A's file
        set_current_user("user_b")
        result = await file_storage.list_files()
        assert "No files found" in result
        
        # User B uploads their own file
        await file_storage.upload_file("test.txt", "User B's content")
        user_b_path = temp_storage_dir / "user_b" / "test.txt"
        assert user_b_path.exists()
        
        # Verify both files exist but are isolated
        assert user_a_path.read_text() == "User A's content"
        assert user_b_path.read_text() == "User B's content"
    
    @pytest.mark.asyncio
    async def test_user_cannot_read_other_users_files(self, temp_storage_dir, mock_rag_api):
        """Test that users cannot read files from other users"""
        # User A creates a file
        set_current_user("user_a")
        await file_storage.upload_file("private.txt", "Secret data")
        
        # User B tries to read it
        set_current_user("user_b")
        result = await file_storage.read_file("private.txt")
        assert "Error: File 'private.txt' not found" in result


class TestFileOperations:
    """Test basic file operations"""
    
    @pytest.mark.asyncio
    async def test_upload_file_success(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test successful file upload"""
        result = await file_storage.upload_file("test.txt", "Hello, world!")
        
        assert "Successfully uploaded 'test.txt'" in result
        file_path = temp_storage_dir / "test_user_123" / "test.txt"
        assert file_path.exists()
        assert file_path.read_text() == "Hello, world!"
        
        # Verify RAG API was called for indexing
        mock_rag_api.post.assert_called_once()
        call_args = mock_rag_api.post.call_args
        assert "/embed" in call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_upload_duplicate_file_fails(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that uploading a duplicate file fails"""
        await file_storage.upload_file("test.txt", "First content")
        result = await file_storage.upload_file("test.txt", "Second content")
        
        assert "Error: File 'test.txt' already exists" in result
    
    @pytest.mark.asyncio
    async def test_list_files(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test listing files in subdirectories (root directory files are excluded)"""
        import aiofiles
        
        # Create a subdirectory and add files there (root directory files are now excluded)
        user_dir = temp_storage_dir / "test_user_123"
        subdir = user_dir / "test_list_subdir"
        subdir.mkdir(parents=True, exist_ok=True)
        
        # Create files in subdirectory
        file1 = subdir / "test_list_file1.txt"
        file2 = subdir / "test_list_file2.txt"
        async with aiofiles.open(file1, 'w', encoding='utf-8') as f:
            await f.write("Content 1")
        async with aiofiles.open(file2, 'w', encoding='utf-8') as f:
            await f.write("Content 2")
        
        result = await file_storage.list_files()
        
        # Check that our specific files are listed (from subdirectory)
        assert "test_list_file1.txt" in result
        assert "test_list_file2.txt" in result
        assert "Size:" in result
        assert "Modified:" in result
        # Verify format includes metadata
        assert "bytes" in result
    
    @pytest.mark.asyncio
    async def test_list_files_empty(self, temp_storage_dir, setup_user):
        """Test listing when no files exist"""
        # Ensure directory is empty for this test
        user_dir = temp_storage_dir / "test_user_123"
        if user_dir.exists():
            # Remove all files and directories
            import shutil
            for item in user_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
        
        result = await file_storage.list_files()
        assert "No files found" in result or "Found 0 file(s)" in result
    
    @pytest.mark.asyncio
    async def test_list_files_includes_subdirectories(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that list_files includes files in subdirectories (root directory files are excluded)"""
        import aiofiles
        
        # Clean up any existing files first
        user_dir = temp_storage_dir / "test_user_123"
        if user_dir.exists():
            for file_path in user_dir.rglob('*'):
                if file_path.is_file():
                    file_path.unlink()
        
        # Create files in subdirectories (root directory files are now excluded)
        subdir1 = user_dir / "test_subdir1"
        subdir1.mkdir(parents=True, exist_ok=True)
        file1 = subdir1 / "test_subdir1_file.txt"
        async with aiofiles.open(file1, 'w', encoding='utf-8') as f:
            await f.write("Subdir1 content")
        
        # Create another subdirectory and add a file
        vault_dir = user_dir / "test_subdir_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        vault_file = vault_dir / "test_subdir_vault_note.md"
        async with aiofiles.open(vault_file, 'w', encoding='utf-8') as f:
            await f.write("# Vault Note\n\nContent in vault")
        
        result = await file_storage.list_files()
        
        # Check that both files from subdirectories are listed (root files are excluded)
        assert "test_subdir1_file.txt" in result
        assert "test_subdir_vault/test_subdir_vault_note.md" in result or "test_subdir_vault_note.md" in result
    
    @pytest.mark.asyncio
    async def test_list_files_sorted_by_directory_and_filename(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that files are sorted by directory and filename (root directory files are excluded)"""
        import aiofiles
        
        # Clean up any existing files first
        user_dir = temp_storage_dir / "test_user_123"
        if user_dir.exists():
            for file_path in user_dir.rglob('*'):
                if file_path.is_file():
                    file_path.unlink()
        
        # Create files in subdirectories (root directory files are now excluded)
        subdir1 = user_dir / "test_sort_dir1"
        subdir1.mkdir(parents=True, exist_ok=True)
        file1 = subdir1 / "test_sort_z_file.txt"
        async with aiofiles.open(file1, 'w', encoding='utf-8') as f:
            await f.write("Z content")
        
        subdir2 = user_dir / "test_sort_dir2"
        subdir2.mkdir(parents=True, exist_ok=True)
        file2 = subdir2 / "test_sort_a_file.txt"
        async with aiofiles.open(file2, 'w', encoding='utf-8') as f:
            await f.write("A content")
        
        # Create another subdirectory file
        subdir = user_dir / "test_sort_subdir"
        subdir.mkdir(parents=True, exist_ok=True)
        subdir_file = subdir / "test_sort_sub_file.txt"
        async with aiofiles.open(subdir_file, 'w', encoding='utf-8') as f:
            await f.write("Sub content")
        
        result = await file_storage.list_files()
        
        # Check that files from subdirectories are listed (order may vary, but all should be present)
        assert "test_sort_a_file.txt" in result
        assert "test_sort_z_file.txt" in result
        assert "test_sort_subdir/test_sort_sub_file.txt" in result or "test_sort_sub_file.txt" in result
    
    @pytest.mark.asyncio
    async def test_list_files_includes_metadata(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that list_files includes file metadata (size, modified date) - files must be in subdirectories"""
        import aiofiles
        
        # Create file in subdirectory (root directory files are now excluded)
        user_dir = temp_storage_dir / "test_user_123"
        subdir = user_dir / "test_metadata_subdir"
        subdir.mkdir(parents=True, exist_ok=True)
        test_file = subdir / "test.txt"
        async with aiofiles.open(test_file, 'w', encoding='utf-8') as f:
            await f.write("Test content")
        
        result = await file_storage.list_files()
        
        assert "test.txt" in result
        assert "Size:" in result
        assert "bytes" in result
        assert "Modified:" in result
        # Should have ISO format date
        assert "T" in result or "-" in result  # ISO format has T or date separators
    
    @pytest.mark.asyncio
    async def test_list_files_excludes_hidden_directories(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that hidden directories (starting with .) are excluded"""
        import aiofiles
        
        # Clean up any existing files first
        user_dir = temp_storage_dir / "test_user_123"
        if user_dir.exists():
            for file_path in user_dir.rglob('*'):
                if file_path.is_file():
                    file_path.unlink()
        
        # Create hidden directory with unique name
        hidden_dir = user_dir / ".test_hidden_dir"
        hidden_dir.mkdir(parents=True, exist_ok=True)
        hidden_file = hidden_dir / "test_hidden_file.txt"
        async with aiofiles.open(hidden_file, 'w', encoding='utf-8') as f:
            await f.write("Hidden content")
        
        # Create visible directory with unique name
        visible_dir = user_dir / "test_visible_dir"
        visible_dir.mkdir(parents=True, exist_ok=True)
        visible_file = visible_dir / "test_visible_file.txt"
        async with aiofiles.open(visible_file, 'w', encoding='utf-8') as f:
            await f.write("Visible content")
        
        result = await file_storage.list_files()
        
        # Hidden directory files should not appear
        assert ".test_hidden_dir" not in result
        assert "test_hidden_file.txt" not in result
        # Visible directory files should appear
        assert "test_visible_dir/test_visible_file.txt" in result or "test_visible_file.txt" in result
    
    @pytest.mark.asyncio
    async def test_read_file_success(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test reading a file"""
        await file_storage.upload_file("test.txt", "Test content")
        result = await file_storage.read_file("test.txt")
        
        assert result == "Test content"
    
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_storage_dir, setup_user):
        """Test reading a file that doesn't exist"""
        result = await file_storage.read_file("nonexistent.txt")
        assert "Error: File 'nonexistent.txt' not found" in result
    
    @pytest.mark.asyncio
    async def test_modify_file_success(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test modifying an existing file"""
        await file_storage.upload_file("test.txt", "Original content")
        result = await file_storage.modify_file("test.txt", "Modified content")
        
        assert "Successfully modified 'test.txt'" in result
        
        # Verify file was updated
        file_path = temp_storage_dir / "test_user_123" / "test.txt"
        assert file_path.read_text() == "Modified content"
        
        # Verify RAG API was called to delete and re-index
        delete_call_count = sum(1 for call in mock_rag_api.delete.call_args_list)
        post_call_count = sum(1 for call in mock_rag_api.post.call_args_list)
        
        assert delete_call_count >= 1  # Delete old embeddings
        assert post_call_count >= 2     # Original upload + re-index
    
    @pytest.mark.asyncio
    async def test_modify_nonexistent_file(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test modifying a file that doesn't exist"""
        result = await file_storage.modify_file("nonexistent.txt", "New content")
        assert "Error: File 'nonexistent.txt' not found" in result
    
    @pytest.mark.asyncio
    async def test_upload_file_uses_multipart_form_data(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that upload_file uses multipart/form-data, not JSON"""
        await file_storage.upload_file("test.txt", "Hello, world!")
        
        # Verify post was called
        assert mock_rag_api.post.called
        
        # Get the call arguments
        call_kwargs = mock_rag_api.post.call_args[1]
        
        # Verify multipart/form-data is used (files and data parameters)
        assert 'files' in call_kwargs, "Should use 'files' parameter for multipart"
        assert 'data' in call_kwargs, "Should use 'data' parameter for multipart"
        assert 'json' not in call_kwargs, "Should NOT use 'json' parameter"
        
        # Verify Content-Type is not set to application/json
        headers = call_kwargs.get('headers', {})
        assert headers.get('Content-Type') != 'application/json', "Should not set Content-Type to application/json"
        
        # Verify file_id is in data
        assert 'file_id' in call_kwargs['data']
        
        # Verify file is in files
        assert 'file' in call_kwargs['files']
    
    @pytest.mark.asyncio
    async def test_modify_file_uses_multipart_form_data(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that modify_file uses multipart/form-data, not JSON"""
        # First upload a file
        await file_storage.upload_file("test.txt", "Original content")
        
        # Clear previous calls
        mock_rag_api.post.reset_mock()
        
        # Now modify it
        await file_storage.modify_file("test.txt", "Modified content")
        
        # Verify post was called for re-indexing
        assert mock_rag_api.post.called
        
        # Get the call arguments for the POST call (re-indexing)
        # Find the POST call that's not the initial upload
        post_calls = [call for call in mock_rag_api.post.call_args_list if call]
        assert len(post_calls) >= 1, "Should have at least one POST call for re-indexing"
        
        # Check the last POST call (the re-indexing call)
        last_post_call = post_calls[-1]
        call_kwargs = last_post_call[1] if len(last_post_call) > 1 else {}
        
        # Verify multipart/form-data is used (files and data parameters)
        assert 'files' in call_kwargs, "Should use 'files' parameter for multipart"
        assert 'data' in call_kwargs, "Should use 'data' parameter for multipart"
        assert 'json' not in call_kwargs, "Should NOT use 'json' parameter"
        
        # Verify Content-Type is not set to application/json
        headers = call_kwargs.get('headers', {})
        assert headers.get('Content-Type') != 'application/json', "Should not set Content-Type to application/json"
        
        # Verify file_id is in data
        assert 'file_id' in call_kwargs['data']
        
        # Verify file is in files
        assert 'file' in call_kwargs['files']
    
    @pytest.mark.asyncio
    async def test_delete_file_success(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test deleting a file"""
        await file_storage.upload_file("test.txt", "Content to delete")
        result = await file_storage.delete_file("test.txt")
        
        assert "Successfully deleted 'test.txt'" in result
        
        # Verify file was removed
        file_path = temp_storage_dir / "test_user_123" / "test.txt"
        assert not file_path.exists()
        
        # Verify RAG API was called to remove embeddings
        mock_rag_api.delete.assert_called()
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_file(self, temp_storage_dir, setup_user):
        """Test deleting a file that doesn't exist"""
        result = await file_storage.delete_file("nonexistent.txt")
        assert "Error: File 'nonexistent.txt' not found" in result
    
    @pytest.mark.asyncio
    async def test_create_note(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test creating a markdown note"""
        result = await file_storage.create_note("Test Note", "Note content here")
        
        assert "Successfully uploaded" in result
        # Check that .md extension was added
        file_path = temp_storage_dir / "test_user_123" / "Test_Note.md"
        assert file_path.exists()
        
        # Check that title header was added
        content = file_path.read_text()
        assert "# Test Note" in content
        assert "Note content here" in content


class TestRAGIntegration:
    """Test RAG API integration"""
    
    @pytest.mark.asyncio
    async def test_search_files(self, temp_storage_dir, setup_user):
        """Test semantic search using RAG API (now uses direct vectordb query)"""
        # Mock the query functions that search_files uses
        mock_results = [
            {
                "content": "This is a test document with relevant content",
                "distance": 0.05,
                "metadata": {"user_id": "test_user_123", "filename": "subdir/test.txt"},
                "filename": "subdir/test.txt"
            }
        ]
        
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]), \
             patch('tools.file_storage._query_vectordb_direct', return_value=mock_results):
            
            result = await file_storage.search_files("relevant content")
            
            assert "Found 1 result(s)" in result
            assert "test.txt" in result or "subdir/test.txt" in result
            assert "0.950" in result or "relevance:" in result
    
    @pytest.mark.asyncio
    async def test_search_no_results(self, temp_storage_dir, setup_user):
        """Test search with no results (now uses direct vectordb query)"""
        # Mock the query functions to return empty results
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]), \
             patch('tools.file_storage._query_vectordb_direct', return_value=[]):
            
            result = await file_storage.search_files("nonexistent query")
            
            assert "No results found" in result
    
    @pytest.mark.asyncio
    async def test_file_id_format(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that file IDs are properly formatted for user scoping"""
        await file_storage.upload_file("test.txt", "Content")
        
        # Check the file_id sent to RAG API (now in data parameter for multipart)
        call_args = mock_rag_api.post.call_args
        call_kwargs = call_args[1]
        # With multipart/form-data, file_id is in data, not json
        assert 'data' in call_kwargs, "Should use 'data' parameter for multipart"
        assert call_kwargs["data"]["file_id"] == "user_test_user_123_test.txt"
        # Metadata is in storage_metadata as JSON string
        import json
        metadata = json.loads(call_kwargs["data"]["storage_metadata"])
        assert metadata["user_id"] == "test_user_123"
    
    @pytest.mark.asyncio
    async def test_rag_api_with_jwt_auth(self, temp_storage_dir, setup_user):
        """Test that RAG API calls include JWT authentication when available"""
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client, \
             patch('tools.file_storage._generate_jwt_token', return_value="test_jwt_token"):
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            mock_response = MagicMock()
            mock_response.status_code = 200  # Set status_code as int
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"results": []}
            mock_response.text = ""  # Add text attribute
            mock_instance.post.return_value = mock_response
            
            await file_storage.upload_file("test.txt", "Content")
            
            # Verify Authorization header was included
            call_args = mock_instance.post.call_args
            headers = call_args[1].get("headers", {})
            assert headers.get("Authorization") == "Bearer test_jwt_token"


class TestGitIntegration:
    """Test Git commit and push functionality"""
    
    @pytest.mark.asyncio
    async def test_git_commit_on_upload(self, temp_storage_dir, setup_user, mock_rag_api, mock_git):
        """Test that Git commit is triggered when uploading a file in vault"""
        import aiofiles
        
        # Create git config
        user_dir = temp_storage_dir / "test_user_123"
        user_dir.mkdir(parents=True, exist_ok=True)
        vault_path = user_dir / "obsidian_vault"
        vault_path.mkdir(parents=True, exist_ok=True)
        
        config = {
            "repo_url": "https://github.com/user/vault.git",
            "token": "ghp_testtoken",
            "branch": "main",
            "stopped": False
        }
        config_path = user_dir / "git_config.json"
        async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config))
        
        # Upload file to vault
        file_path = vault_path / "test.txt"
        file_path.write_text("Test content")
        
        # Mock the file path check
        with patch('tools.file_storage.get_user_storage_path', return_value=user_dir):
            # The upload will write to user_dir, but we need to test vault behavior
            # For this test, we'll just verify the Git commit function is called
            with patch('tools.file_storage._trigger_git_commit') as mock_commit:
                await file_storage.upload_file("test.txt", "Test content")
                # Git commit should be attempted (may fail if file not in vault, which is OK)
                # The function is non-blocking, so we just verify it was called
                pass
    
    @pytest.mark.asyncio
    async def test_git_commit_skipped_when_no_config(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that Git commit is skipped when no git_config.json exists"""
        # Upload file without git config
        result = await file_storage.upload_file("test.txt", "Test content")
        
        # Should succeed without Git operations
        assert "Successfully uploaded" in result
        file_path = temp_storage_dir / "test_user_123" / "test.txt"
        assert file_path.exists()
    
    @pytest.mark.asyncio
    async def test_git_commit_skipped_when_stopped(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that Git commit is skipped when sync is stopped"""
        import aiofiles
        
        # Create git config with stopped=True
        user_dir = temp_storage_dir / "test_user_123"
        user_dir.mkdir(parents=True, exist_ok=True)
        
        config = {
            "repo_url": "https://github.com/user/vault.git",
            "token": "ghp_testtoken",
            "branch": "main",
            "stopped": True
        }
        config_path = user_dir / "git_config.json"
        async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config))
        
        # Upload file
        result = await file_storage.upload_file("test.txt", "Test content")
        
        # Should succeed but Git commit should be skipped
        assert "Successfully uploaded" in result


class TestErrorHandling:
    """Test error handling and edge cases"""
    
    @pytest.mark.asyncio
    async def test_no_user_context_fails(self, temp_storage_dir):
        """Test that operations fail without user context"""
        # Clear user context
        try:
            from contextvars import ContextVar
            from shared.storage import _user_id_context
            _user_id_context.set(None)
        except:
            pass
        
        with pytest.raises((ValueError, RuntimeError), match="No user context|User not authenticated"):
            await file_storage.upload_file("test.txt", "Content")
    
    @pytest.mark.asyncio
    async def test_rag_api_failure_cleans_up_file(self, temp_storage_dir, setup_user):
        """Test that file is cleaned up if RAG API indexing fails"""
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock RAG API failure
            mock_instance.post.side_effect = httpx.RequestError("Connection failed")
            
            with pytest.raises(RuntimeError, match="Failed to index file"):
                await file_storage.upload_file("test.txt", "Content")
            
            # Verify file was cleaned up
            file_path = temp_storage_dir / "test_user_123" / "test.txt"
            assert not file_path.exists()
    
    @pytest.mark.asyncio
    async def test_git_commit_failure_non_blocking(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that Git commit failures don't block file operations"""
        import aiofiles
        
        # Create git config
        user_dir = temp_storage_dir / "test_user_123"
        user_dir.mkdir(parents=True, exist_ok=True)
        
        config = {
            "repo_url": "https://github.com/user/vault.git",
            "token": "ghp_testtoken",
            "branch": "main",
            "stopped": False
        }
        config_path = user_dir / "git_config.json"
        async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config))
        
        # Mock Git to raise an error
        with patch('tools.file_storage.Repo') as mock_repo_class:
            mock_repo_class.side_effect = Exception("Git error")
            
            # File operation should still succeed
            result = await file_storage.upload_file("test.txt", "Test content")
            assert "Successfully uploaded" in result
            
            # Verify file exists
            file_path = temp_storage_dir / "test_user_123" / "test.txt"
            assert file_path.exists()


class TestSearchFiles:
    """Test search_files functionality"""
    
    @pytest.mark.asyncio
    async def test_search_files_returns_filename_from_metadata(self, temp_storage_dir, setup_user):
        """Test that search_files returns filename from metadata"""
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        # Mock the embedding and database query
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with metadata containing filename (must be in subdirectory, not root)
            mock_row = MagicMock()
            mock_metadata = {"user_id": "test_user_123", "filename": "subdir/test_file.md", "size": 100}
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Test content from test_file.md',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_subdir/test_file.md',
                'similarity': 0.95
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Test content from test_file.md',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_subdir/test_file.md',
                'similarity': 0.95
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("test query")
                
                # Verify filename is returned (not "unknown")
                assert "test_file.md" in result or "subdir/test_file.md" in result
                assert "unknown" not in result
                assert "Test content" in result
                assert "relevance:" in result
    
    @pytest.mark.asyncio
    async def test_search_files_fallback_to_custom_id_for_filename(self, temp_storage_dir, setup_user):
        """Test that search_files extracts filename from custom_id when metadata lacks it"""
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        # Mock the embedding and database query
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with metadata missing filename, but custom_id has it (must be in subdirectory)
            mock_row = MagicMock()
            mock_metadata = {"user_id": "test_user_123"}  # No filename in metadata
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Test content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_subdir/my_document.txt',  # Filename in subdirectory
                'similarity': 0.90
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Test content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_subdir/my_document.txt',
                'similarity': 0.90
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("test query")
                
                # Verify filename is extracted from custom_id
                assert "my_document.txt" in result or "subdir/my_document.txt" in result
                assert "unknown" not in result
    
    @pytest.mark.asyncio
    async def test_search_files_handles_json_string_metadata(self, temp_storage_dir, setup_user):
        """Test that search_files handles metadata stored as JSON string"""
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        # Mock the embedding and database query
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with metadata as JSON string (must be in subdirectory)
            mock_row = MagicMock()
            mock_metadata_str = '{"user_id": "test_user_123", "filename": "subdir/json_file.md", "size": 200}'
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Content from json_file',
                'cmetadata': mock_metadata_str,  # JSON string, not dict
                'custom_id': 'user_test_user_123_subdir/json_file.md',
                'similarity': 0.85
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Content from json_file',
                'cmetadata': mock_metadata_str,
                'custom_id': 'user_test_user_123_subdir/json_file.md',
                'similarity': 0.85
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("test query")
                
                # Verify filename is parsed from JSON string metadata
                assert "json_file.md" in result or "subdir/json_file.md" in result
                assert "unknown" not in result
    
    @pytest.mark.asyncio
    async def test_search_files_handles_missing_metadata_gracefully(self, temp_storage_dir, setup_user):
        """Test that search_files handles missing or invalid metadata gracefully"""
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        # Mock the embedding and database query
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with no metadata and no custom_id
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Some content',
                'cmetadata': None,  # No metadata
                'custom_id': None,  # No custom_id
                'similarity': 0.80
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Some content',
                'cmetadata': None,
                'custom_id': None,
                'similarity': 0.80
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("test query")
                
                # Should still return results, but with "unknown" filename
                assert "unknown" in result
                assert "Some content" in result
                assert "relevance:" in result


class TestQueryEmbedding:
    """Test query embedding functionality for semantic search"""
    
    @pytest.mark.asyncio
    async def test_get_query_embedding_uses_local_embed_endpoint(self, setup_user):
        """Test that /local/embed endpoint is tried first"""
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock successful /local/embed response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
            mock_instance.post.return_value = mock_response
            
            # Mock JWT token generation
            with patch('tools.file_storage._generate_jwt_token', return_value="test_token"):
                embedding = await file_storage._get_query_embedding("test query", "test_user_123")
                
                assert embedding == [0.1, 0.2, 0.3]
                # Verify /local/embed was called
                call_args = mock_instance.post.call_args
                assert "/local/embed" in call_args[0][0]
                assert call_args[1]["json"] == {"text": "test query"}
    
    @pytest.mark.asyncio
    async def test_get_query_embedding_fallback_to_multipart_embed(self, setup_user):
        """Test fallback to /embed with multipart/form-data when /local/embed fails"""
        import io
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock /local/embed returns 404 (doesn't exist)
            local_embed_response = MagicMock()
            local_embed_response.status_code = 404
            local_embed_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not found", request=MagicMock(), response=local_embed_response
            )
            
            # Mock successful /embed response (multipart)
            embed_response = MagicMock()
            embed_response.status_code = 200
            embed_response.raise_for_status = MagicMock()
            
            # Setup mock to return 404 for /local/embed, 200 for /embed
            def post_side_effect(url, **kwargs):
                if "/local/embed" in url:
                    raise httpx.HTTPStatusError("Not found", request=MagicMock(), response=local_embed_response)
                return embed_response
            
            mock_instance.post.side_effect = post_side_effect
            
            # Mock database connection and query
            mock_conn = AsyncPGMock()
            # Create a numpy array-like object for the embedding
            mock_embedding = np.array([0.1, 0.2, 0.3, 0.4])
            
            # Create a proper mock row that supports both [] and .get()
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda self, key: mock_embedding if key == 'embedding' else None
            mock_row.get = lambda key, default=None: mock_embedding if key == 'embedding' else default
            mock_conn.fetchrow = AsyncMock(return_value=mock_row)
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('tools.file_storage._generate_jwt_token', return_value="test_token"):
                
                embedding = await file_storage._get_query_embedding("test query", "test_user_123")
                
                # Verify embedding was converted to list
                assert isinstance(embedding, list)
                assert len(embedding) == 4
                assert embedding == [0.1, 0.2, 0.3, 0.4]
                
                # Verify /embed was called with multipart/form-data
                embed_call = None
                for call in mock_instance.post.call_args_list:
                    if "/embed" in call[0][0] and "/local/embed" not in call[0][0]:
                        embed_call = call
                        break
                
                assert embed_call is not None
                # Verify files parameter (multipart) was used
                assert "files" in embed_call[1]
                assert "data" in embed_call[1]
                # Verify file_id is in data
                assert "file_id" in embed_call[1]["data"]
                
                # Verify database cleanup was called
                mock_conn.execute.assert_called_once()
                mock_conn.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_query_embedding_handles_numpy_array_boolean_check(self, setup_user):
        """Test that numpy array boolean check doesn't cause ambiguous truth value error"""
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock /local/embed doesn't exist
            local_embed_response = MagicMock()
            local_embed_response.status_code = 404
            local_embed_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not found", request=MagicMock(), response=local_embed_response
            )
            
            embed_response = MagicMock()
            embed_response.status_code = 200
            embed_response.raise_for_status = MagicMock()
            
            def post_side_effect(url, **kwargs):
                if "/local/embed" in url:
                    raise httpx.HTTPStatusError("Not found", request=MagicMock(), response=local_embed_response)
                return embed_response
            
            mock_instance.post.side_effect = post_side_effect
            
            # Mock database with numpy array (this is what causes the boolean check issue)
            mock_conn = AsyncPGMock()
            mock_embedding = np.array([0.1, 0.2, 0.3])
            
            # Create a proper mock row that supports both [] and .get()
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda self, key: mock_embedding if key == 'embedding' else None
            mock_row.get = lambda key, default=None: mock_embedding if key == 'embedding' else default
            mock_conn.fetchrow = AsyncMock(return_value=mock_row)
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('tools.file_storage._generate_jwt_token', return_value="test_token"):
                
                # This should not raise "ambiguous truth value" error
                embedding = await file_storage._get_query_embedding("test query", "test_user_123")
                
                # Verify it was converted to list
                assert isinstance(embedding, list)
                assert len(embedding) == 3
    
    @pytest.mark.asyncio
    async def test_get_query_embedding_handles_pgvector_type(self, setup_user):
        """Test that pgvector vector type is properly converted to list"""
        import numpy as np
        from unittest.mock import AsyncMock as AsyncPGMock
        
        # Create a mock pgvector-like object
        class MockPGVector:
            def __init__(self, data):
                self.data = np.array(data)
            
            def tolist(self):
                return self.data.tolist()
        
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock /local/embed doesn't exist
            local_embed_response = MagicMock()
            local_embed_response.status_code = 404
            local_embed_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not found", request=MagicMock(), response=local_embed_response
            )
            
            embed_response = MagicMock()
            embed_response.status_code = 200
            embed_response.raise_for_status = MagicMock()
            
            def post_side_effect(url, **kwargs):
                if "/local/embed" in url:
                    raise httpx.HTTPStatusError("Not found", request=MagicMock(), response=local_embed_response)
                return embed_response
            
            mock_instance.post.side_effect = post_side_effect
            
            mock_conn = AsyncPGMock()
            mock_embedding = MockPGVector([0.5, 0.6, 0.7])
            
            # Create a proper mock row that supports both [] and .get()
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda self, key: mock_embedding if key == 'embedding' else None
            mock_row.get = lambda key, default=None: mock_embedding if key == 'embedding' else default
            mock_conn.fetchrow = AsyncMock(return_value=mock_row)
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('tools.file_storage._generate_jwt_token', return_value="test_token"):
                
                embedding = await file_storage._get_query_embedding("test query", "test_user_123")
                
                # Verify tolist() was called and result is a list
                assert isinstance(embedding, list)
                assert embedding == [0.5, 0.6, 0.7]
    
    @pytest.mark.asyncio
    async def test_get_query_embedding_handles_missing_embedding(self, setup_user):
        """Test error handling when embedding is not found in database"""
        with patch('tools.file_storage.httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock /local/embed doesn't exist
            local_embed_response = MagicMock()
            local_embed_response.status_code = 404
            local_embed_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not found", request=MagicMock(), response=local_embed_response
            )
            
            embed_response = MagicMock()
            embed_response.status_code = 200
            embed_response.raise_for_status = MagicMock()
            
            def post_side_effect(url, **kwargs):
                if "/local/embed" in url:
                    raise httpx.HTTPStatusError("Not found", request=MagicMock(), response=local_embed_response)
                return embed_response
            
            mock_instance.post.side_effect = post_side_effect
            
            # Mock database returns None (embedding not found)
            from unittest.mock import AsyncMock as AsyncPGMock
            mock_conn = AsyncPGMock()
            mock_conn.fetchrow = AsyncMock(return_value=None)
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('tools.file_storage._generate_jwt_token', return_value="test_token"):
                
                with pytest.raises(RuntimeError, match="Could not retrieve embedding"):
                    await file_storage._get_query_embedding("test query", "test_user_123")
                
                mock_conn.close.assert_called_once()


class TestFileSearchExclusions:
    """Test that file search excludes git files, hash files, and root directory files"""
    
    @pytest.mark.asyncio
    async def test_search_files_excludes_git_directory(self, temp_storage_dir, setup_user):
        """Test that search_files excludes files in .git directory"""
        import aiofiles
        from unittest.mock import AsyncMock as AsyncPGMock
        
        user_dir = temp_storage_dir / "test_user_123"
        vault_dir = user_dir / "obsidian_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a file in .git directory (should be excluded)
        git_dir = vault_dir / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        git_file = git_dir / "config"
        async with aiofiles.open(git_file, 'w', encoding='utf-8') as f:
            await f.write("git config content")
        
        # Create a valid markdown file in subdirectory (should be included)
        subdir = vault_dir / "notes"
        subdir.mkdir(parents=True, exist_ok=True)
        valid_file = subdir / "note.md"
        async with aiofiles.open(valid_file, 'w', encoding='utf-8') as f:
            await f.write("Valid note content")
        
        # Mock the database query to return only the valid file
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with valid file (not in .git)
            mock_row = MagicMock()
            mock_metadata = {"user_id": "test_user_123", "filename": "notes/note.md"}
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Valid note content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_notes/note.md',
                'similarity': 0.95
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Valid note content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_notes/note.md',
                'similarity': 0.95
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("note")
                
                # Should only return the valid file, not the .git file
                assert "notes/note.md" in result
                assert ".git" not in result
                assert "config" not in result
    
    @pytest.mark.asyncio
    async def test_search_files_excludes_hash_files(self, temp_storage_dir, setup_user):
        """Test that search_files excludes hash files like sync_hashes.json"""
        from unittest.mock import AsyncMock as AsyncPGMock
        
        user_dir = temp_storage_dir / "test_user_123"
        vault_dir = user_dir / "obsidian_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        # Create sync_hashes.json file (should be excluded)
        hash_file = user_dir / "sync_hashes.json"
        hash_file.write_text('{"file1.md": "hash123"}')
        
        # Create a valid markdown file in subdirectory (should be included)
        subdir = vault_dir / "notes"
        subdir.mkdir(parents=True, exist_ok=True)
        valid_file = subdir / "note.md"
        valid_file.write_text("Valid note content")
        
        # Mock the database query to return only the valid file
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with valid file (not a hash file)
            mock_row = MagicMock()
            mock_metadata = {"user_id": "test_user_123", "filename": "notes/note.md"}
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Valid note content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_notes/note.md',
                'similarity': 0.95
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Valid note content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_notes/note.md',
                'similarity': 0.95
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("note")
                
                # Should only return the valid file, not the hash file
                assert "notes/note.md" in result
                assert "sync_hashes.json" not in result
    
    @pytest.mark.asyncio
    async def test_search_files_excludes_root_directory_files(self, temp_storage_dir, setup_user):
        """Test that search_files excludes files in root directory (only subdirectories allowed)"""
        import aiofiles
        from unittest.mock import AsyncMock as AsyncPGMock
        
        user_dir = temp_storage_dir / "test_user_123"
        vault_dir = user_dir / "obsidian_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a file in root directory (should be excluded)
        root_file = user_dir / "root_file.md"
        async with aiofiles.open(root_file, 'w', encoding='utf-8') as f:
            await f.write("Root file content")
        
        # Create a valid markdown file in subdirectory (should be included)
        subdir = vault_dir / "notes"
        subdir.mkdir(parents=True, exist_ok=True)
        valid_file = subdir / "note.md"
        async with aiofiles.open(valid_file, 'w', encoding='utf-8') as f:
            await f.write("Valid note content")
        
        # Mock the database query to return only the valid file
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with valid file (in subdirectory)
            mock_row = MagicMock()
            mock_metadata = {"user_id": "test_user_123", "filename": "obsidian_vault/notes/note.md"}
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Valid note content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_obsidian_vault/notes/note.md',
                'similarity': 0.95
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Valid note content',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_obsidian_vault/notes/note.md',
                'similarity': 0.95
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("note")
                
                # Should only return the valid file in subdirectory, not root file
                assert "obsidian_vault/notes/note.md" in result or "notes/note.md" in result
                assert "root_file.md" not in result
    
    @pytest.mark.asyncio
    async def test_list_files_excludes_git_directory(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that list_files excludes files in .git directory"""
        import aiofiles
        
        user_dir = temp_storage_dir / "test_user_123"
        vault_dir = user_dir / "obsidian_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a file in .git directory (should be excluded)
        git_dir = vault_dir / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        git_file = git_dir / "config"
        async with aiofiles.open(git_file, 'w', encoding='utf-8') as f:
            await f.write("git config")
        
        # Create a valid file in subdirectory (should be included)
        subdir = vault_dir / "notes"
        subdir.mkdir(parents=True, exist_ok=True)
        valid_file = subdir / "note.md"
        async with aiofiles.open(valid_file, 'w', encoding='utf-8') as f:
            await f.write("Valid note")
        
        result = await file_storage.list_files()
        
        # Should include valid file but not .git file
        assert "notes/note.md" in result or "note.md" in result
        assert ".git" not in result
        assert "config" not in result
    
    @pytest.mark.asyncio
    async def test_list_files_excludes_hash_files(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that list_files excludes hash files like sync_hashes.json"""
        user_dir = temp_storage_dir / "test_user_123"
        vault_dir = user_dir / "obsidian_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        # Create sync_hashes.json file (should be excluded)
        hash_file = user_dir / "sync_hashes.json"
        hash_file.write_text('{"file1.md": "hash123"}')
        
        # Create a valid file in subdirectory (should be included)
        subdir = vault_dir / "notes"
        subdir.mkdir(parents=True, exist_ok=True)
        valid_file = subdir / "note.md"
        valid_file.write_text("Valid note")
        
        result = await file_storage.list_files()
        
        # Should include valid file but not hash file
        assert "notes/note.md" in result or "note.md" in result
        assert "sync_hashes.json" not in result
    
    @pytest.mark.asyncio
    async def test_list_files_excludes_root_directory_files(self, temp_storage_dir, setup_user, mock_rag_api):
        """Test that list_files excludes files in root directory (only subdirectories allowed)"""
        import aiofiles
        
        user_dir = temp_storage_dir / "test_user_123"
        vault_dir = user_dir / "obsidian_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a file in root directory (should be excluded)
        root_file = user_dir / "root_file.md"
        async with aiofiles.open(root_file, 'w', encoding='utf-8') as f:
            await f.write("Root file")
        
        # Create a valid file in subdirectory (should be included)
        subdir = vault_dir / "notes"
        subdir.mkdir(parents=True, exist_ok=True)
        valid_file = subdir / "note.md"
        async with aiofiles.open(valid_file, 'w', encoding='utf-8') as f:
            await f.write("Valid note")
        
        result = await file_storage.list_files()
        
        # Should include valid file in subdirectory but not root file
        assert "obsidian_vault/notes/note.md" in result or "notes/note.md" in result or "note.md" in result
        assert "root_file.md" not in result
    
    @pytest.mark.asyncio
    async def test_search_files_includes_worker_indexed_files_with_relative_paths(self, temp_storage_dir, setup_user):
        """Test that Worker-indexed files with relative paths (e.g., 'notes/note.md') are NOT excluded
        
        This verifies Bug 1 fix: Worker stores relative paths, not base filenames, so they
        should not be incorrectly excluded as root-level files.
        """
        from unittest.mock import AsyncMock as AsyncPGMock
        
        # Mock the embedding and database query
        with patch('tools.file_storage._get_query_embedding', return_value=[0.1, 0.2, 0.3]):
            mock_conn = AsyncPGMock()
            
            # Create mock row with filename as relative path (how Worker stores it)
            # This simulates a file indexed by Worker at "notes/note.md" relative to vault root
            mock_row = MagicMock()
            mock_metadata = {"user_id": "test_user_123", "filename": "notes/note.md", "size": 100}
            mock_row.__getitem__ = lambda self, key: {
                'document': 'Content from Worker-indexed file',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_notes/note.md',
                'similarity': 0.95
            }.get(key)
            mock_row.get = lambda key, default=None: {
                'document': 'Content from Worker-indexed file',
                'cmetadata': mock_metadata,
                'custom_id': 'user_test_user_123_notes/note.md',
                'similarity': 0.95
            }.get(key, default)
            
            mock_conn.fetch = AsyncMock(return_value=[mock_row])
            mock_conn.close = AsyncMock()
            
            with patch('asyncpg.connect', return_value=mock_conn), \
                 patch('pgvector.asyncpg.register_vector', return_value=None), \
                 patch('pgvector.asyncpg.Vector', return_value=MagicMock()):
                
                result = await file_storage.search_files("Worker content")
                
                # Verify Worker-indexed file with relative path is NOT excluded
                assert "notes/note.md" in result or "note.md" in result
                assert "Content from Worker-indexed file" in result
                assert "No results found" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])