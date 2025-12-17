import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys
import os

# Add relevant directories to path
# Path to Worker directory so we can 'from main import ...'
WORKER_DIR = Path(__file__).parent.parent
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

# Now import the actual module
from main import clean_remote_url, setup_credential_store

class TestGitCredentials(unittest.TestCase):
    def test_clean_remote_url(self):
        """Test that tokens are removed from various Git URL formats."""
        test_cases = [
            ("https://token@github.com/user/repo.git", "https://github.com/user/repo.git"),
            ("http://my_token:x-oauth-basic@github.com/org/repo", "http://github.com/org/repo"),
            ("https://github.com/user/repo", "https://github.com/user/repo"),
        ]
        for input_url, expected in test_cases:
            self.assertEqual(clean_remote_url(input_url), expected)

    @patch("main.subprocess.run")
    @patch("main.Path.mkdir") # Mock directory creation
    @patch("git.Repo")
    def test_setup_credential_store(self, mock_repo, mock_mkdir, mock_subprocess):
        """Test that Git is configured to use the built-in 'store' helper."""
        user_id = "test_user_123"
        repo_url = "https://github.com/user/repo"
        token = "ghp_secure_token"

        mock_repo_instance = MagicMock()

        # This function should:
        # 1. Set Git config 'credential.helper' to 'store --file=/storage/{user_id}/.git-credentials'
        # 2. Use git credential approve to save the token
        setup_credential_store(mock_repo_instance, user_id, repo_url, token)

        # Verify directory creation was called
        mock_mkdir.assert_called()

        # Verify Git config was set to use the built-in store helper with persistent file
        mock_repo_instance.git.config.assert_called()
        args, _ = mock_repo_instance.git.config.call_args
        self.assertIn("credential.helper", args)
        # Check that it points to the persistent storage
        helper_config = [arg for arg in args if "store --file=" in str(arg)][0]
        self.assertIn("/storage/test_user_123/.git-credentials", helper_config)

        # Verify subprocess.run was called to save the token
        mock_subprocess.assert_called()
        # Check that it called git credential approve
        cmd_args = mock_subprocess.call_args[0][0]
        self.assertEqual(cmd_args[-2:], ["credential", "approve"])

        # Check input data contains the token
        kwargs = mock_subprocess.call_args[1]
        input_data = kwargs.get("input").decode()
        self.assertIn("username=ghp_secure_token", input_data)

    @patch("main.subprocess.run")
    @patch("main.Path.mkdir")
    @patch("git.Repo")
    def test_setup_credential_store_no_token(self, mock_repo, mock_mkdir, mock_subprocess):
        """Test that setup_credential_store doesn't fail and doesn't save when token is None."""
        user_id = "test_user_123"
        repo_url = "https://github.com/user/repo"
        token = None

        mock_repo_instance = MagicMock()
        setup_credential_store(mock_repo_instance, user_id, repo_url, token)

        # Git config should still be set (to enable the helper for retrieval)
        mock_repo_instance.git.config.assert_called_with("credential.helper", f"store --file=/storage/test_user_123/.git-credentials")

        # But subprocess.run should NOT be called
        mock_subprocess.assert_not_called()

    @patch("main.subprocess.run")
    @patch("main.STORAGE_ROOT")
    def test_get_token_from_store(self, mock_storage_root, mock_subprocess):
        """Test retrieving token from the persistent store."""
        user_id = "test_user_123"
        repo_url = "https://github.com/user/repo"
        expected_token = "ghp_retrieved_token"

        # Mock storage path
        mock_storage_root.__truediv__.return_value.__truediv__.return_value.exists.return_value = True

        # Mock subprocess.run output
        mock_result = MagicMock()
        mock_result.stdout.decode.return_value = f"protocol=https\nhost=github.com\nusername={expected_token}\npassword=\n"
        mock_subprocess.return_value = mock_result

        from main import get_token_from_store
        token = get_token_from_store(user_id, repo_url)

        self.assertEqual(token, expected_token)
        mock_subprocess.assert_called_once()
        cmd_args = mock_subprocess.call_args[0][0]
        self.assertEqual(cmd_args[-2:], ["credential", "fill"])

if __name__ == "__main__":
    unittest.main()
