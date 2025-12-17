# Code Quality Setup Summary

This document summarizes the code quality and testing setup for ObsidianSync.

## ✅ What Was Set Up

### 1. Configuration Files

- **`pyproject.toml`** - Central configuration for:
  - Ruff (linter and formatter)
  - Black (code formatter)
  - MyPy (type checker)
  - Pytest (test runner)
  - Coverage (test coverage)

- **`.pre-commit-config.yaml`** (at repository root) - Git hooks for:
  - General file checks (whitespace, YAML, JSON, TOML)
  - Python linting (Ruff)
  - Python formatting (Ruff & Black)
  - Type checking (MyPy)
  - Security scanning (Bandit)
  - Import sorting (isort)
  - Docstring checking (Pydocstyle)

- **`requirements-dev.txt`** - Development dependencies

### 2. Scripts

- **`scripts/run-checks.sh`** - Convenience script to run all checks locally

### 3. Documentation

- **`docs/DEVELOPMENT.md`** - Comprehensive development guide
- **`docs/README-TESTING.md`** - Quick reference for testing

## 🚀 Quick Start

### Install Dependencies

```bash
cd ObsidianSync
pip install -r requirements-dev.txt
```

### Install Pre-commit Hooks

```bash
# From repository root
cd /home/simon/Projects/2025/TheMaryAnne
pre-commit install
```

### Run All Checks

```bash
# From ObsidianSync directory
./scripts/run-checks.sh
```

## 📋 Tools Included

1. **Ruff** - Fast Python linter and formatter (replaces flake8, isort, etc.)
2. **Black** - Code formatter for consistent style
3. **MyPy** - Static type checker
4. **Bandit** - Security linter
5. **Pydocstyle** - Docstring style checker
6. **Pytest** - Test framework (already in use)
7. **Coverage** - Test coverage reporting

## 🔧 How It Works

### Pre-commit Hooks

When you commit code, pre-commit hooks automatically:
1. Check for trailing whitespace
2. Ensure files end with newlines
3. Validate YAML/JSON/TOML syntax
4. Format Python code (Ruff & Black)
5. Lint Python code (Ruff)
6. Check types (MyPy)
7. Scan for security issues (Bandit)
8. Check import sorting (isort)
9. Validate docstring style (Pydocstyle)

If any check fails, the commit is blocked until issues are fixed.

### Manual Checks

You can run checks manually using:
- `./scripts/run-checks.sh` - Run all checks
- `pre-commit run --all-files` - Run all hooks on all files
- Individual tools (ruff, black, mypy, etc.)

## 📝 Configuration Details

### Ruff Configuration

- Line length: 100 characters
- Target Python version: 3.10
- Selected rules: E, W, F, I, B, C4, UP, ARG, SIM, TCH, PTH, ERA, PD, PL, TRY, RUF
- Per-file ignores for tests and main.py files

### Black Configuration

- Line length: 100 characters
- Target Python version: 3.10

### MyPy Configuration

- Python version: 3.10
- Ignores missing imports for third-party libraries
- Excludes test files

### Pytest Configuration

- Async mode: auto
- Coverage reporting enabled
- Test markers: unit, integration, slow

## 🎯 Next Steps

1. **Install dependencies**: `pip install -r requirements-dev.txt`
2. **Install hooks**: `pre-commit install` (from repo root)
3. **Run checks**: `./scripts/run-checks.sh`
4. **Start coding**: Hooks will run automatically on commit!

## 📚 Additional Resources

- See `docs/DEVELOPMENT.md` for detailed usage instructions
- See `docs/README-TESTING.md` for quick reference
- Pre-commit documentation: https://pre-commit.com
- Ruff documentation: https://docs.astral.sh/ruff/
- Black documentation: https://black.readthedocs.io/
