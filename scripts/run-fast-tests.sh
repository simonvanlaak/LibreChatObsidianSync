#!/bin/bash
# Run fast tests for ObsidianSync (used by pre-commit hook)
# Extracted from run-checks.sh: lint, format, type-check, security, unit tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

check_command() {
    command -v "$1" >/dev/null 2>&1
}

print_section "ObsidianSync: Fast Tests (Pre-commit)"

FAILED=0

# Check if we're in a virtual environment
if [[ -z "$VIRTUAL_ENV" ]]; then
    print_warning "Not in a virtual environment. Consider activating one."
fi

# 1. Ruff linting
print_section "Ruff Linting"
if check_command ruff; then
    if ruff check McpService/ Worker/; then
        print_success "Ruff linting passed"
    else
        print_error "Ruff found issues. Run 'ruff check --fix McpService/ Worker/' to auto-fix."
        FAILED=1
    fi
else
    print_error "ruff not found"
    FAILED=1
fi

# 2. Ruff formatting
print_section "Ruff Formatting"
if check_command ruff; then
    if ruff format --check McpService/ Worker/; then
        print_success "Ruff formatting check passed"
    else
        print_error "Code formatting issues. Run 'ruff format McpService/ Worker/' to fix."
        FAILED=1
    fi
else
    print_error "ruff not found"
    FAILED=1
fi

# 3. Black formatting (as backup check)
print_section "Black Formatting"
if check_command black; then
    if black --check --line-length=100 McpService/ Worker/; then
        print_success "Black formatting check passed"
    else
        print_error "Code formatting issues. Run 'black --line-length=100 McpService/ Worker/' to fix."
        FAILED=1
    fi
else
    print_warning "black not found, skipping"
fi

# 4. MyPy type checking
print_section "MyPy Type Checking"
if check_command mypy; then
    if mypy McpService/ Worker/ --ignore-missing-imports; then
        print_success "MyPy type checking passed"
    else
        print_warning "MyPy found some type issues (non-blocking)"
    fi
else
    print_warning "mypy not found, skipping"
fi

# 5. Bandit security check
print_section "Bandit Security Check"
if check_command bandit; then
    if bandit -r McpService/ Worker/ -ll --exclude "tests/,*test*.py"; then
        print_success "Bandit security check passed"
    else
        print_warning "Bandit found some security issues (review recommended, non-blocking)"
    fi
else
    print_warning "bandit not found, skipping"
fi

# 6. Unit Tests
print_section "Unit Tests"
if check_command pytest; then
    cd "$PROJECT_ROOT/McpService"
    if pytest tests/unit/ -v; then
        print_success "McpService unit tests passed"
    else
        print_error "McpService unit tests failed"
        FAILED=1
    fi

    cd "$PROJECT_ROOT/Worker"
    if pytest tests/unit/ -v; then
        print_success "Worker unit tests passed"
    else
        print_error "Worker unit tests failed"
        FAILED=1
    fi
    cd "$PROJECT_ROOT"
else
    print_error "pytest not found"
    FAILED=1
fi

if [ $FAILED -eq 1 ]; then
    print_error "Fast tests failed. Please fix the issues above."
    exit 1
fi

print_success "All fast tests passed!"
exit 0
