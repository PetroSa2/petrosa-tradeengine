#!/bin/bash

# Script to manually create a release with a specific version
# Usage: ./scripts/create-release.sh [major|minor|patch] [version]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
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

# Function to show usage
show_usage() {
    echo "Usage: $0 [major|minor|patch] [version]"
    echo ""
    echo "Options:"
    echo "  major|minor|patch  - Increment the specified version component"
    echo "  version            - Specific version to create (e.g., v2.1.0)"
    echo ""
    echo "Examples:"
    echo "  $0 patch           # Increment patch version (v1.0.0 -> v1.0.1)"
    echo "  $0 minor           # Increment minor version (v1.0.0 -> v1.1.0)"
    echo "  $0 major           # Increment major version (v1.0.0 -> v2.0.0)"
    echo "  $0 v2.1.0          # Create specific version v2.1.0"
    echo ""
    echo "Note: This script will create a Git tag and push it to trigger the CI/CD pipeline."
}

# Function to get current version
get_current_version() {
    local latest_tag=$(git tag --sort=-version:refname | grep '^v[0-9]' | head -1)
    if [ -z "$latest_tag" ]; then
        echo "v0.0.0"
    else
        echo "$latest_tag"
    fi
}

# Function to increment version
increment_version() {
    local current_version=$1
    local increment_type=$2
    
    if [[ "$current_version" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        local major="${BASH_REMATCH[1]}"
        local minor="${BASH_REMATCH[2]}"
        local patch="${BASH_REMATCH[3]}"
        
        case $increment_type in
            major)
                major=$((major + 1))
                minor=0
                patch=0
                ;;
            minor)
                minor=$((minor + 1))
                patch=0
                ;;
            patch)
                patch=$((patch + 1))
                ;;
            *)
                print_error "Invalid increment type: $increment_type"
                exit 1
                ;;
        esac
        
        echo "v${major}.${minor}.${patch}"
    else
        print_error "Current version '$current_version' is not in semantic version format"
        exit 1
    fi
}

# Function to validate version format
validate_version() {
    local version=$1
    if [[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        return 0
    else
        return 1
    fi
}

# Function to check if tag exists
tag_exists() {
    local version=$1
    git tag "$version" >/dev/null 2>&1
}

# Function to create and push tag
create_tag() {
    local version=$1
    
    print_status "Creating tag: $version"
    
    # Check if tag already exists
    if tag_exists "$version"; then
        print_warning "Tag $version already exists!"
        read -p "Do you want to continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_status "Aborted."
            exit 0
        fi
    fi
    
    # Create the tag
    git tag "$version"
    
    # Push the tag
    print_status "Pushing tag to remote..."
    git push origin "$version"
    
    print_success "Tag $version created and pushed successfully!"
    print_status "This will trigger the CI/CD pipeline to build and deploy version $version"
}

# Main script logic
main() {
    # Check if we're in a git repository
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        print_error "Not in a git repository"
        exit 1
    fi
    
    # Check if we have uncommitted changes
    if ! git diff-index --quiet HEAD --; then
        print_warning "You have uncommitted changes. Please commit or stash them before creating a release."
        exit 1
    fi
    
    # Parse arguments
    if [ $# -eq 0 ]; then
        show_usage
        exit 1
    fi
    
    local current_version=$(get_current_version)
    local new_version=""
    
    if [ $# -eq 1 ]; then
        # Check if it's a specific version
        if validate_version "$1"; then
            new_version="$1"
        else
            # Check if it's an increment type
            case $1 in
                major|minor|patch)
                    new_version=$(increment_version "$current_version" "$1")
                    ;;
                *)
                    print_error "Invalid argument: $1"
                    show_usage
                    exit 1
                    ;;
            esac
        fi
    elif [ $# -eq 2 ]; then
        # Two arguments: increment type and specific version
        case $1 in
            major|minor|patch)
                if validate_version "$2"; then
                    new_version="$2"
                else
                    print_error "Invalid version format: $2"
                    show_usage
                    exit 1
                fi
                ;;
            *)
                print_error "Invalid increment type: $1"
                show_usage
                exit 1
                ;;
        esac
    else
        print_error "Too many arguments"
        show_usage
        exit 1
    fi
    
    # Validate the new version
    if ! validate_version "$new_version"; then
        print_error "Invalid version format: $new_version"
        exit 1
    fi
    
    # Show what we're about to do
    print_status "Current version: $current_version"
    print_status "New version: $new_version"
    
    # Confirm action
    read -p "Create and push tag $new_version? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Aborted."
        exit 0
    fi
    
    # Create and push the tag
    create_tag "$new_version"
}

# Run main function
main "$@" 