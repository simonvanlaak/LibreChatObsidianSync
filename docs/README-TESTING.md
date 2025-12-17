# Testing and Code Quality Setup

This document provides a quick reference for running tests and code quality checks for ObsidianSync.

## Quick Start

### Install Development Dependencies

```bash
cd ObsidianSync
pip install -r requirements-dev.txt
```

### Install Pre-commit Hooks

Pre-commit hooks are installed at the repository root level. They automatically run on commits:

```bash
# From repository root
cd /home/simon/Projects/2025/TheMaryAnne
pre-commit install
```

## Running Checks

### All Checks (Recommended)

```bash
# From ObsidianSync directory
./scripts/run-checks.sh

# Auto-fix issues
./scripts/run-checks.sh --fix

# Skip tests
./scripts/run-checks.sh --skip-tests
```

### Individual Tools

```bash
# Ruff (linting and formatting)
ruff check ObsidianSync/McpService/ ObsidianSync/Worker/
ruff format ObsidianSync/McpService/ ObsidianSync/Worker/

# Black (formatting)
black --check ObsidianSync/McpService/ ObsidianSync/Worker/

# MyPy (type checking)
mypy ObsidianSync/McpService/ ObsidianSync/Worker/ --ignore-missing-imports

# Bandit (security)
bandit -r ObsidianSync/McpService/ ObsidianSync/Worker/ -ll

# Pytest (testing)
cd ObsidianSync/McpService && pytest
cd ObsidianSync/Worker && pytest
```

## Pre-commit Hooks

The pre-commit hooks automatically run when you commit. They check:

- ✅ Trailing whitespace
- ✅ End of file newlines
- ✅ YAML/JSON/TOML syntax
- ✅ Python code formatting (Ruff & Black)
- ✅ Python linting (Ruff)
- ✅ Type checking (MyPy)
- ✅ Security issues (Bandit)
- ✅ Import sorting (isort)
- ✅ Docstring style (Pydocstyle)

### Testing Hooks Manually

```bash
# Test all hooks on staged files
pre-commit run

# Test all hooks on all files
pre-commit run --all-files

# Test a specific hook
pre-commit run ruff --all-files
```

## Configuration Files

- `pyproject.toml` - Tool configurations (Ruff, Black, MyPy, Pytest)
- `.pre-commit-config.yaml` - Pre-commit hooks (at repository root)
- `requirements-dev.txt` - Development dependencies

For detailed information, see [DEVELOPMENT.md](./DEVELOPMENT.md).
