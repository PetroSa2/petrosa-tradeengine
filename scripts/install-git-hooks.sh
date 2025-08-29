#!/bin/bash

# Install git hooks to prevent VERSION_PLACEHOLDER changes

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

print_status "🔧 Installing git hooks for VERSION_PLACEHOLDER protection..."

# Check if we're in a git repository
if [ ! -d ".git" ]; then
    print_error "❌ Not in a git repository"
    print_error "   Please run this script from the root of a git repository"
    exit 1
fi

# Create .git/hooks directory if it doesn't exist
if [ ! -d ".git/hooks" ]; then
    print_status "Creating .git/hooks directory..."
    mkdir -p .git/hooks
fi

# Check if pre-commit hook already exists
if [ -f ".git/hooks/pre-commit" ]; then
    print_warning "⚠️  Pre-commit hook already exists"
    read -p "Do you want to overwrite it? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Installation aborted."
        exit 0
    fi
fi

# Copy pre-commit hook
if [ -f "scripts/pre-commit-version-check.sh" ]; then
    cp scripts/pre-commit-version-check.sh .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
    print_success "✅ Pre-commit hook installed"
else
    print_error "❌ scripts/pre-commit-version-check.sh not found"
    print_error "   Please ensure the script exists before installing hooks"
    exit 1
fi

print_success ""
print_success "🎉 Git hooks installation completed!"
print_success ""
print_success "The pre-commit hook will now prevent accidental VERSION_PLACEHOLDER changes."
print_success ""
print_success "To test the hook:"
print_success "  1. Make a change to a k8s/*.yaml file"
print_success "  2. Replace VERSION_PLACEHOLDER with a specific version"
print_success "  3. Try to commit: git add k8s/deployment.yaml && git commit -m 'test'"
print_success "  4. The commit should be blocked with an error message"
print_success ""
print_success "To remove the hook (not recommended):"
print_success "  rm .git/hooks/pre-commit"
print_success ""
print_success "For more information, see: docs/CURSOR_AI_VERSION_RULES.md"
