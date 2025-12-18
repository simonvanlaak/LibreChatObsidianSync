import os
import json
import unittest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import sys
from unittest.mock import MagicMock

# Mock git module before importing main
sys.modules["git"] = MagicMock()
import sys
from pathlib import Path

# Add Worker directory to path
WORKER_DIR = Path(__file__).parent.parent
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from main import GitSync, SyncManager, IndexingManager

class TestGitSync(unittest.TestCase):
    def setUp(self):
        self.user_id = "testuser"
        self.config = {
            "repo_url": "https://github.com/user/repo",
            "token": "secret_token",
            "branch": "main"
        }
        self.sync = GitSync(self.user_id, self.config)

    def test_auth_url_cleaning(self):
        """Test that token is NOT injected into URL."""
        # This used to test injection, now we test that URLs are kept clean
        # Import here to avoid issues with mock git
        from main import clean_remote_url
        url_with_token = "https://secret_token@github.com/user/repo"
        expected_url = "https://github.com/user/repo"
        self.assertEqual(clean_remote_url(url_with_token), expected_url)

    @patch("main.Repo")
    @patch("main.setup_credential_store")
    def test_ensure_repo_clones_if_missing(self, mock_setup_creds, mock_repo):
        """Test cloning when directory is missing."""
        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "mkdir") as mock_mkdir:
                self.sync._ensure_repo()
                mock_mkdir.assert_called_with(parents=True, exist_ok=True)
                mock_repo.clone_from.assert_called()
                mock_setup_creds.assert_called()

    @patch("main.Repo")
    @patch("main.setup_credential_store")
    def test_ensure_repo_loads_if_exists(self, mock_setup_creds, mock_repo):
        """Test loading existing repo."""
        with patch.object(Path, "exists", return_value=True):
             self.sync._ensure_repo()
             mock_repo.assert_called()
             mock_setup_creds.assert_called()

class TestSyncManager(unittest.TestCase):
    @patch("main.STORAGE_ROOT")
    @patch("main.GitSync")
    def test_run_discovers_users(self, mock_git_sync, mock_storage_root):
        """Test that the manager finds users with valid config."""

        # Setup mock directory structure
        mock_user_dir = MagicMock()
        mock_user_dir.is_dir.return_value = True
        mock_user_dir.name = "testuser_1"

        # Mock git_config.json existence
        mock_config_path = MagicMock()
        mock_config_path.exists.return_value = True

        # Mock constructing path / "git_config.json"
        # When user_dir / "git_config.json" is called, return mock_config_path
        mock_user_dir.__truediv__.return_value = mock_config_path

        # Mock file reading
        mock_config_path.read_text.return_value = '{"repo_url": "http://test", "token": "abc"}'
        mock_config_path.with_suffix.return_value = mock_config_path # For atomic write temp file

        mock_storage_root.exists.return_value = True
        mock_storage_root.iterdir.return_value = [mock_user_dir]

        manager = SyncManager()
        manager.process_cycle()

        # Verify GitSync was instantiated and sync() called
        mock_git_sync.assert_called()
        mock_git_sync.return_value.sync.assert_called_once()

if __name__ == '__main__':
    unittest.main()
