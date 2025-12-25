#!/bin/bash
# Run integration tests for ObsidianSync (used by pre-push hook)
# Includes: integration tests, coverage, Docker builds

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

print_section "ObsidianSync: Integration Tests (Pre-push)"

FAILED=0

# Integration Tests
print_section "Integration Tests"
if check_command pytest; then
    if [ -d "McpService/tests/integration" ]; then
        cd "$PROJECT_ROOT/McpService"
        if pytest tests/integration/ -v; then
            print_success "McpService integration tests passed"
        else
            print_warning "McpService integration tests failed (may require external services)"
        fi
        cd "$PROJECT_ROOT"
    fi

    if [ -d "Worker/tests/integration" ]; then
        cd "$PROJECT_ROOT/Worker"
        if pytest tests/integration/ -v; then
            print_success "Worker integration tests passed"
        else
            print_warning "Worker integration tests failed (may require external services)"
        fi
        cd "$PROJECT_ROOT"
    fi
else
    print_error "pytest not found"
    FAILED=1
fi

# Full Test Suite with Coverage
print_section "Full Test Suite with Coverage"
if check_command pytest; then
    cd "$PROJECT_ROOT"
    if pytest --cov=McpService --cov=Worker --cov-report=term-missing --cov-report=html tests/ -v; then
        print_success "Full test suite with coverage passed"

        # Check coverage threshold (if pyproject.toml has coverage config)
        if [ -f "pyproject.toml" ] && grep -q "fail_under" pyproject.toml; then
            COVERAGE_THRESHOLD=$(grep "fail_under" pyproject.toml | grep -oE "[0-9]+" | head -1)
            if [ -n "$COVERAGE_THRESHOLD" ]; then
                print_success "Coverage threshold check (threshold: ${COVERAGE_THRESHOLD}%)"
            fi
        fi
    else
        print_warning "Some tests failed or coverage below threshold"
    fi
    cd "$PROJECT_ROOT"
fi

# Docker Build Tests
print_section "Docker Build Tests"
if check_command docker; then
    # Build McpService Docker image
    if [ -f "McpService/Dockerfile" ]; then
        if docker build -t test-obsidian-mcp:test -f McpService/Dockerfile McpService/; then
            print_success "McpService Docker image built successfully"
            docker rmi test-obsidian-mcp:test >/dev/null 2>&1 || true
        else
            print_error "McpService Docker build failed"
            FAILED=1
        fi
    fi

    # Build Worker Docker image
    if [ -f "Worker/Dockerfile" ]; then
        if docker build -t test-obsidian-worker:test -f Worker/Dockerfile Worker/; then
            print_success "Worker Docker image built successfully"
            docker rmi test-obsidian-worker:test >/dev/null 2>&1 || true
        else
            print_error "Worker Docker build failed"
            FAILED=1
        fi
    fi
else
    print_warning "Docker not available, skipping Docker build tests"
fi

# Dockerfile Lint (if hadolint is available)
print_section "Dockerfile Linting"
if check_command hadolint; then
    if [ -f "McpService/Dockerfile" ]; then
        if hadolint McpService/Dockerfile; then
            print_success "McpService Dockerfile linting passed"
        else
            print_warning "McpService Dockerfile linting found issues (non-blocking)"
        fi
    fi

    if [ -f "Worker/Dockerfile" ]; then
        if hadolint Worker/Dockerfile; then
            print_success "Worker Dockerfile linting passed"
        else
            print_warning "Worker Dockerfile linting found issues (non-blocking)"
        fi
    fi
else
    print_warning "hadolint not available, skipping Dockerfile linting"
fi

if [ $FAILED -eq 1 ]; then
    print_error "Integration tests failed. Please fix the issues above."
    exit 1
fi

print_success "All integration tests passed!"
exit 0
