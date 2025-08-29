#!/bin/bash

# Enhanced version manager with VERSION_PLACEHOLDER validation
# Generates auto-incremental versions for local development and deployment

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

# Function to validate VERSION_PLACEHOLDER integrity
validate_version_placeholders() {
    print_status "Validating VERSION_PLACEHOLDER integrity..."

    # Check for VERSION_PLACEHOLDER in k8s files
    PLACEHOLDER_COUNT=$(grep -r "VERSION_PLACEHOLDER" k8s/ 2>/dev/null | wc -l || echo "0")

    if [ "$PLACEHOLDER_COUNT" -eq 0 ]; then
        print_error "❌ No VERSION_PLACEHOLDER found in k8s/ directory"
        print_error "   This indicates the version system is broken"
        print_error "   Expected to find VERSION_PLACEHOLDER in Kubernetes manifests"
        return 1
    fi

    # Check for hardcoded versions
    HARDCODED_COUNT=$(grep -r "yurisa2/petrosa.*:v[0-9]" k8s/ 2>/dev/null | wc -l || echo "0")

    if [ "$HARDCODED_COUNT" -gt 0 ]; then
        print_warning "⚠️  Found $HARDCODED_COUNT hardcoded versions in k8s/"
        print_warning "   These should be VERSION_PLACEHOLDER instead"
        grep -r "yurisa2/petrosa.*:v[0-9]" k8s/ 2>/dev/null || true
        return 1
    fi

    # Check for "latest" tags
    LATEST_COUNT=$(grep -r "yurisa2/petrosa.*:latest" k8s/ 2>/dev/null | wc -l || echo "0")

    if [ "$LATEST_COUNT" -gt 0 ]; then
        print_warning "⚠️  Found $LATEST_COUNT 'latest' tags in k8s/"
        print_warning "   These should be VERSION_PLACEHOLDER instead"
        grep -r "yurisa2/petrosa.*:latest" k8s/ 2>/dev/null || true
        return 1
    fi

    print_success "✅ VERSION_PLACEHOLDER validation passed"
    print_status "   Found $PLACEHOLDER_COUNT VERSION_PLACEHOLDER references"
    return 0
}

# Function to generate version
generate_version() {
    local version_type=$1

    case $version_type in
        "patch")
            # Get latest version from git tags
            LATEST_VERSION=$(git tag --sort=-version:refname | grep '^v[0-9]' | head -1)
            if [ -z "$LATEST_VERSION" ]; then
                VERSION="v1.0.0"
            else
                if [[ "$LATEST_VERSION" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
                    MAJOR="${BASH_REMATCH[1]}"
                    MINOR="${BASH_REMATCH[2]}"
                    PATCH="${BASH_REMATCH[3]}"
                    NEW_PATCH=$((PATCH + 1))
                    VERSION="v${MAJOR}.${MINOR}.${NEW_PATCH}"
                else
                    VERSION="v1.0.0"
                fi
            fi
            ;;
        "minor")
            LATEST_VERSION=$(git tag --sort=-version:refname | grep '^v[0-9]' | head -1)
            if [ -z "$LATEST_VERSION" ]; then
                VERSION="v1.0.0"
            else
                if [[ "$LATEST_VERSION" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
                    MAJOR="${BASH_REMATCH[1]}"
                    MINOR="${BASH_REMATCH[2]}"
                    NEW_MINOR=$((MINOR + 1))
                    VERSION="v${MAJOR}.${NEW_MINOR}.0"
                else
                    VERSION="v1.0.0"
                fi
            fi
            ;;
        "major")
            LATEST_VERSION=$(git tag --sort=-version:refname | grep '^v[0-9]' | head -1)
            if [ -z "$LATEST_VERSION" ]; then
                VERSION="v1.0.0"
            else
                if [[ "$LATEST_VERSION" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
                    MAJOR="${BASH_REMATCH[1]}"
                    NEW_MAJOR=$((MAJOR + 1))
                    VERSION="v${NEW_MAJOR}.0.0"
                else
                    VERSION="v1.0.0"
                fi
            fi
            ;;
        "local")
            # Generate local development version with timestamp
            TIMESTAMP=$(date +%Y%m%d-%H%M%S)
            COMMIT_SHA=$(git rev-parse --short HEAD)
            VERSION="local-${TIMESTAMP}-${COMMIT_SHA}"
            ;;
        *)
            print_error "Unknown version type: $version_type"
            print_error "Available types: patch, minor, major, local"
            exit 1
            ;;
    esac

    echo "$VERSION"
}

# Function to update Kubernetes manifests (for local testing only)
update_k8s_manifests() {
    local version=$1
    local docker_username=${2:-"yurisa2"}

    print_status "Updating Kubernetes manifests with version: $version"
    print_warning "⚠️  This is for local testing only - DO NOT COMMIT these changes"

    # Update deployment.yaml
    if [ -f "k8s/deployment.yaml" ]; then
        sed -i.bak "s|VERSION_PLACEHOLDER|${version}|g" k8s/deployment.yaml
        sed -i.bak "s|yurisa2/petrosa-binance-data-extractor:VERSION_PLACEHOLDER|${docker_username}/petrosa-binance-data-extractor:${version}|g" k8s/deployment.yaml
        rm -f k8s/deployment.yaml.bak
        print_success "Updated k8s/deployment.yaml"
    fi

    # Update other manifests if they contain VERSION_PLACEHOLDER
    find k8s/ -name "*.yaml" -exec grep -l "VERSION_PLACEHOLDER" {} \; | while read file; do
        sed -i.bak "s|VERSION_PLACEHOLDER|${version}|g" "$file"
        rm -f "${file}.bak"
        print_success "Updated $file"
    done
}

# Function to revert Kubernetes manifests
revert_k8s_manifests() {
    print_status "Reverting Kubernetes manifests to VERSION_PLACEHOLDER..."

    # Revert all changes in k8s directory
    git checkout k8s/

    print_success "✅ Kubernetes manifests reverted to VERSION_PLACEHOLDER"
}

# Function to show version information
show_version_info() {
    print_status "📦 Version Information"
    echo "======================"

    # Current version
    LATEST_VERSION=$(git tag --sort=-version:refname | grep '^v[0-9]' | head -1 || echo 'None')
    echo "Latest version: $LATEST_VERSION"

    # Next versions
    echo "Next patch version: $(generate_version patch)"
    echo "Next minor version: $(generate_version minor)"
    echo "Next major version: $(generate_version major)"

    # VERSION_PLACEHOLDER status
    echo ""
    print_status "🔍 VERSION_PLACEHOLDER Status:"
    PLACEHOLDER_COUNT=$(grep -r "VERSION_PLACEHOLDER" k8s/ 2>/dev/null | wc -l || echo "0")
    echo "VERSION_PLACEHOLDER references: $PLACEHOLDER_COUNT"

    # Git status
    echo ""
    print_status "📋 Git Status:"
    git status --porcelain || echo "No changes"
}

# Function to debug version issues
debug_version_issues() {
    print_status "🐛 Version Debug Information"
    echo "============================"

    # Git status
    echo "Git status:"
    git status --porcelain
    echo ""

    # All git tags
    echo "All git tags:"
    git tag --sort=-version:refname
    echo ""

    # VERSION_PLACEHOLDER in k8s/
    echo "VERSION_PLACEHOLDER in k8s/:"
    grep -r "VERSION_PLACEHOLDER" k8s/ 2>/dev/null || echo "None found"
    echo ""

    # Hardcoded versions in k8s/
    echo "Hardcoded versions in k8s/:"
    grep -r "yurisa2/petrosa.*:v[0-9]" k8s/ 2>/dev/null || echo "None found"
    echo ""

    # Latest tags in k8s/
    echo "'latest' tags in k8s/:"
    grep -r "yurisa2/petrosa.*:latest" k8s/ 2>/dev/null || echo "None found"
    echo ""

    # CI/CD pipeline status
    if [ -f ".github/workflows/ci-cd.yml" ]; then
        echo "CI/CD pipeline file exists: ✅"
    else
        echo "CI/CD pipeline file missing: ❌"
    fi

    # Version management scripts
    if [ -f "scripts/create-release.sh" ]; then
        echo "Create release script exists: ✅"
    else
        echo "Create release script missing: ❌"
    fi
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  generate [patch|minor|major|local]  - Generate version"
    echo "  validate                            - Validate VERSION_PLACEHOLDER integrity"
    echo "  info                                - Show version information"
    echo "  debug                               - Debug version issues"
    echo "  update-local [version]              - Update manifests for local testing"
    echo "  revert                              - Revert manifests to VERSION_PLACEHOLDER"
    echo ""
    echo "Examples:"
    echo "  $0 generate patch                   # Generate next patch version"
    echo "  $0 validate                         # Check VERSION_PLACEHOLDER integrity"
    echo "  $0 info                             # Show version information"
    echo "  $0 debug                            # Debug version issues"
    echo "  $0 update-local v1.0.1-test         # Update for local testing"
    echo "  $0 revert                           # Revert to VERSION_PLACEHOLDER"
    echo ""
    echo "Note: Use ./scripts/create-release.sh for creating actual releases"
}

# Main function
main() {
    local command=$1
    local option=$2

    case $command in
        "generate")
            if [ -z "$option" ]; then
                print_error "Version type required: patch, minor, major, or local"
                exit 1
            fi
            VERSION=$(generate_version "$option")
            echo "$VERSION"
            ;;
        "validate")
            validate_version_placeholders
            ;;
        "info")
            show_version_info
            ;;
        "debug")
            debug_version_issues
            ;;
        "update-local")
            if [ -z "$option" ]; then
                print_error "Version required for local testing"
                exit 1
            fi
            update_k8s_manifests "$option"
            print_warning "⚠️  Remember to run '$0 revert' before committing"
            ;;
        "revert")
            revert_k8s_manifests
            ;;
        *)
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
