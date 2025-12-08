"""
Test that Worker directory structure exists after migration.
These tests should fail initially (RED phase), then pass after moving code (GREEN phase).
"""
import pytest
from pathlib import Path


def test_worker_directory_exists():
    """Test that Worker directory exists"""
    assert Path("ObsidianSync/Worker").exists()


def test_worker_main_exists():
    """Test that main.py exists in Worker"""
    assert Path("ObsidianSync/Worker/main.py").exists()


def test_worker_requirements_exists():
    """Test that requirements.txt exists"""
    assert Path("ObsidianSync/Worker/requirements.txt").exists()


def test_worker_dockerfile_exists():
    """Test that Dockerfile exists"""
    assert Path("ObsidianSync/Worker/Dockerfile").exists()
