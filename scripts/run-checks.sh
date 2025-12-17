#!/bin/bash
# Script to run all code quality checks for ObsidianSync
# Usage: ./scripts/run-checks.sh [--fix] [--skip-tests]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
FIX=false
SKIP_TESTS=false

for arg in "$@"; do
    case $arg in
        --fix)
            FIX=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        *)
            # Unknown option
            ;;
    esac
done

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo -e "${GREEN}Running code quality checks for ObsidianSync...${NC}\n"

# Check if we're in a virtual environment
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo -e "${YELLOW}Warning: Not in a virtual environment. Consider activating one.${NC}\n"
fi

# 1. Ruff linting
echo -e "${GREEN}[1/7] Running Ruff linter...${NC}"
if [ "$FIX" = true ]; then
    ruff check --fix McpService/ Worker/
else
    ruff check McpService/ Worker/
fi
echo -e "${GREEN}✓ Ruff check passed${NC}\n"

# 2. Ruff formatting
echo -e "${GREEN}[2/7] Running Ruff formatter...${NC}"
if [ "$FIX" = true ]; then
    ruff format McpService/ Worker/
    echo -e "${GREEN}✓ Files formatted${NC}\n"
else
    ruff format --check McpService/ Worker/
    echo -e "${GREEN}✓ Formatting check passed${NC}\n"
fi

# 3. Black formatting (as backup check)
echo -e "${GREEN}[3/7] Running Black formatter...${NC}"
if [ "$FIX" = true ]; then
    black --line-length=100 McpService/ Worker/
    echo -e "${GREEN}✓ Files formatted with Black${NC}\n"
else
    black --check --line-length=100 McpService/ Worker/
    echo -e "${GREEN}✓ Black check passed${NC}\n"
fi

# 4. MyPy type checking
echo -e "${GREEN}[4/7] Running MyPy type checker...${NC}"
mypy McpService/ Worker/ --ignore-missing-imports || {
    echo -e "${YELLOW}⚠ MyPy found some type issues (non-blocking)${NC}\n"
}
echo -e "${GREEN}✓ MyPy check completed${NC}\n"

# 5. Bandit security check
echo -e "${GREEN}[5/7] Running Bandit security checker...${NC}"
bandit -r McpService/ Worker/ -ll --exclude "tests/,*test*.py" || {
    echo -e "${YELLOW}⚠ Bandit found some security issues (review recommended)${NC}\n"
}
echo -e "${GREEN}✓ Bandit check completed${NC}\n"

# 6. Pydocstyle docstring check
echo -e "${GREEN}[6/7] Running Pydocstyle docstring checker...${NC}"
pydocstyle --convention=google McpService/ Worker/ --match='(?!test_).*\.py' || {
    echo -e "${YELLOW}⚠ Pydocstyle found some docstring issues (non-blocking)${NC}\n"
}
echo -e "${GREEN}✓ Pydocstyle check completed${NC}\n"

# 7. Pytest tests
if [ "$SKIP_TESTS" = false ]; then
    echo -e "${GREEN}[7/7] Running Pytest tests...${NC}"
    cd "$PROJECT_ROOT/McpService"
    pytest tests/ -v
    cd "$PROJECT_ROOT/Worker"
    pytest tests/ -v
    echo -e "${GREEN}✓ All tests passed${NC}\n"
else
    echo -e "${YELLOW}[7/7] Skipping tests (--skip-tests flag set)${NC}\n"
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}All checks completed successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
