"""
Test that Worker directory structure exists after migration.
These tests verify the Worker service structure is correct.
"""
import pytest
from pathlib import Path


def test_worker_directory_exists():
    """Test that Worker directory exists"""
    # Test from Worker directory perspective
    worker_dir = Path(__file__).parent.parent.parent
    assert worker_dir.exists()
    assert worker_dir.name == "Worker"


def test_worker_main_exists():
    """Test that main.py exists in Worker"""
    worker_dir = Path(__file__).parent.parent.parent
    assert (worker_dir / "main.py").exists()


def test_worker_requirements_exists():
    """Test that requirements.txt exists"""
    worker_dir = Path(__file__).parent.parent.parent
    assert (worker_dir / "requirements.txt").exists()


def test_worker_dockerfile_exists():
    """Test that Dockerfile exists"""
    worker_dir = Path(__file__).parent.parent.parent
    assert (worker_dir / "Dockerfile").exists()
