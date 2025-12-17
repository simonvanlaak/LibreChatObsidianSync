# Development Guide for ObsidianSync

This guide covers code quality tools, testing, and development workflows for the ObsidianSync project.

## Setup

### 1. Install Development Dependencies

```bash
cd ObsidianSync
pip install -r requirements-dev.txt
```

### 2. Install Pre-commit Hooks

Pre-commit hooks will automatically run code quality checks before each commit:

```bash
# Install pre-commit (if not already installed)
pip install pre-commit

# Install the git hooks
pre-commit install

# Optional: Install hooks for commit messages too
pre-commit install --hook-type commit-msg
```

### 3. Verify Installation

Run the checks manually to verify everything is set up correctly:

```bash
./scripts/run-checks.sh
```

## Code Quality Tools

### Ruff (Linter & Formatter)

Fast Python linter and formatter that replaces flake8, isort, and more.

```bash
# Check for issues
ruff check McpService/ Worker/

# Auto-fix issues
ruff check --fix McpService/ Worker/

# Format code
ruff format McpService/ Worker/
```

### Black (Code Formatter)

Python code formatter for consistent style.

```bash
# Check formatting
black --check --line-length=100 McpService/ Worker/

# Format code
black --line-length=100 McpService/ Worker/
```

### MyPy (Type Checker)

Static type checker for Python.

```bash
# Run type checking
mypy McpService/ Worker/ --ignore-missing-imports
```

### Bandit (Security Checker)

Security linter for Python code.

```bash
# Run security checks
bandit -r McpService/ Worker/ -ll --exclude "tests/,*test*.py"
```

### Pydocstyle (Docstring Checker)

Checks Python docstring conventions.

```bash
# Check docstrings
pydocstyle --convention=google McpService/ Worker/ --match='(?!test_).*\.py'
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run tests for McpService only
cd McpService && pytest

# Run tests for Worker only
cd Worker && pytest

# Run with coverage
pytest --cov=McpService --cov=Worker --cov-report=html

# Run specific test file
pytest tests/unit/test_file_storage.py

# Run specific test
pytest tests/unit/test_file_storage.py::test_upload_file
```

### Test Organization

- **Unit tests**: `tests/unit/` - Fast, isolated tests
- **Integration tests**: `tests/integration/` - Tests that require external services

## Running All Checks

Use the provided script to run all code quality checks:

```bash
# Run all checks (read-only)
./scripts/run-checks.sh

# Run all checks and auto-fix issues
./scripts/run-checks.sh --fix

# Run checks without running tests
./scripts/run-checks.sh --skip-tests
```

## Pre-commit Hooks

Pre-commit hooks automatically run before each commit. They check:

- Trailing whitespace
- End of file newlines
- YAML/JSON/TOML syntax
- Large files
- Merge conflicts
- Private keys
- Python code formatting (Ruff & Black)
- Python linting (Ruff)
- Type checking (MyPy)
- Security issues (Bandit)
- Import sorting (isort)
- Docstring style (Pydocstyle)

### Bypassing Hooks (Not Recommended)

If you need to bypass hooks in an emergency:

```bash
git commit --no-verify -m "Emergency commit"
```

**Warning**: Only use this when absolutely necessary. It bypasses all quality checks.

### Updating Hooks

To update pre-commit hooks to the latest versions:

```bash
pre-commit autoupdate
```

## Configuration Files

- `pyproject.toml` - Configuration for Ruff, Black, MyPy, and Pytest
- `.pre-commit-config.yaml` - Pre-commit hooks configuration
- `requirements-dev.txt` - Development dependencies

## Continuous Integration

These same checks should be run in CI/CD pipelines. The pre-commit configuration can be used as a reference for CI checks.

## Best Practices

1. **Always run checks before committing**: Use `./scripts/run-checks.sh` or let pre-commit handle it
2. **Fix issues immediately**: Don't accumulate technical debt
3. **Write tests first**: Follow TDD (Test-Driven Development)
4. **Keep commits focused**: One logical change per commit
5. **Review pre-commit output**: Understand what's being checked and why

## Troubleshooting

### Pre-commit hooks not running

```bash
# Reinstall hooks
pre-commit uninstall
pre-commit install
```

### MyPy errors with third-party libraries

MyPy is configured to ignore missing imports for third-party libraries. If you see errors, they're likely in your own code and should be fixed.

### Ruff and Black conflicts

Both Ruff and Black are configured with the same line length (100). If you see conflicts, run both formatters:

```bash
ruff format McpService/ Worker/
black --line-length=100 McpService/ Worker/
```

### Tests failing

1. Check that all dependencies are installed
2. Ensure test data is available
3. Check test isolation (tests shouldn't depend on each other)
4. Review test output for specific error messages
