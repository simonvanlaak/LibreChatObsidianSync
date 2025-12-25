"""
Style enforcement for the McpService package.

Ensures Ruff formatting and linting pass using the shared configuration.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(args: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    assert result.returncode == 0, "Ruff reported style issues. See output above."


def test_ruff_formatting() -> None:
    _run_ruff(["format", "--check", "."])


def test_ruff_lint() -> None:
    _run_ruff(["check", "."])
