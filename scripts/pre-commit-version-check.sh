#!/bin/bash

# Pre-commit hook to prevent VERSION_PLACEHOLDER changes
# This hook ensures that VERSION_PLACEHOLDER is never accidentally modified

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_status "🔍 Checking for VERSION_PLACEHOLDER modifications..."

# Check if any staged files contain VERSION_PLACEHOLDER changes
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM)

if [ -z "$STAGED_FILES" ]; then
    print_success "No staged files to check"
    exit 0
fi

ERRORS_FOUND=0

for file in $STAGED_FILES; do
    # Only check YAML files in k8s directory
    if [[ "$file" == k8s/*.yaml ]] || [[ "$file" == k8s/*.yml ]]; then
        print_status "Checking $file..."

        # Check if VERSION_PLACEHOLDER was removed or changed
        if git diff --cached "$file" | grep -q "^-.*VERSION_PLACEHOLDER"; then
            print_error "❌ VERSION_PLACEHOLDER was removed or changed in $file"
            print_error "   This is NOT allowed. VERSION_PLACEHOLDER must remain unchanged."
            print_error "   The CI/CD pipeline will handle version replacement automatically."
            ERRORS_FOUND=$((ERRORS_FOUND + 1))
        fi

        # Check if a specific version was added instead of VERSION_PLACEHOLDER
        if git diff --cached "$file" | grep -q "^+.*yurisa2/petrosa.*:v[0-9]"; then
            print_error "❌ Specific version detected in $file"
            print_error "   This is NOT allowed. Use VERSION_PLACEHOLDER instead."
            print_error "   The CI/CD pipeline will handle version replacement automatically."
            ERRORS_FOUND=$((ERRORS_FOUND + 1))
        fi

        # Check if "latest" was added instead of VERSION_PLACEHOLDER
        if git diff --cached "$file" | grep -q "^+.*yurisa2/petrosa.*:latest"; then
            print_error "❌ 'latest' tag detected in $file"
            print_error "   This is NOT allowed. Use VERSION_PLACEHOLDER instead."
            print_error "   The CI/CD pipeline will handle version replacement automatically."
            ERRORS_FOUND=$((ERRORS_FOUND + 1))
        fi
    fi
done

if [ $ERRORS_FOUND -gt 0 ]; then
    print_error ""
    print_error "🚨 VERSION_PLACEHOLDER violations found: $ERRORS_FOUND"
    print_error ""
    print_error "To fix these issues:"
    print_error "1. Revert the changes to VERSION_PLACEHOLDER"
    print_error "2. Use version management scripts instead:"
    print_error "   - ./scripts/create-release.sh patch"
    print_error "   - ./scripts/create-release.sh minor"
    print_error "   - ./scripts/create-release.sh major"
    print_error "3. Read docs/CURSOR_AI_VERSION_RULES.md for more information"
    print_error ""
    print_error "Commit aborted. Please fix the VERSION_PLACEHOLDER issues and try again."
    exit 1
fi

print_success "✅ VERSION_PLACEHOLDER check passed"
print_status "   All staged files maintain proper VERSION_PLACEHOLDER usage"
