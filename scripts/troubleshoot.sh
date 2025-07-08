#!/bin/bash

# Petrosa Trading Engine - Troubleshooting Script
# Helps diagnose and fix common issues with the local pipeline

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

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check Python environment
check_python() {
    print_status "Checking Python environment..."
    
    if ! command_exists python3; then
        print_error "Python 3 is not installed"
        echo "Install Python 3.11+ from https://python.org"
        return 1
    fi
    
    python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    required_version="3.11"
    
    if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
        print_error "Python 3.11 or higher is required. Current version: $python_version"
        echo "Please upgrade Python to 3.11+"
        return 1
    fi
    
    print_success "Python version: $python_version"
    
    # Check virtual environment
    if [ ! -d ".venv" ]; then
        print_warning "Virtual environment not found"
        echo "Run: make setup"
        return 1
    fi
    
    print_success "Virtual environment found"
    return 0
}

# Function to check dependencies
check_dependencies() {
    print_status "Checking dependencies..."
    
    # Activate virtual environment
    source .venv/bin/activate
    
    # Check if requirements files exist
    if [ ! -f "requirements.txt" ]; then
        print_error "requirements.txt not found"
        return 1
    fi
    
    if [ ! -f "requirements-dev.txt" ]; then
        print_error "requirements-dev.txt not found"
        return 1
    fi
    
    # Check if key packages are installed
    local missing_packages=()
    
    if ! python3 -c "import fastapi" 2>/dev/null; then
        missing_packages+=("fastapi")
    fi
    
    if ! python3 -c "import pytest" 2>/dev/null; then
        missing_packages+=("pytest")
    fi
    
    if ! python3 -c "import black" 2>/dev/null; then
        missing_packages+=("black")
    fi
    
    if ! python3 -c "import flake8" 2>/dev/null; then
        missing_packages+=("flake8")
    fi
    
    if [ ${#missing_packages[@]} -ne 0 ]; then
        print_warning "Missing packages: ${missing_packages[*]}"
        echo "Run: make install-dev"
        return 1
    fi
    
    print_success "All dependencies installed"
    return 0
}

# Function to check Docker
check_docker() {
    print_status "Checking Docker..."
    
    if ! command_exists docker; then
        print_error "Docker is not installed"
        echo "Install Docker from https://docker.com"
        return 1
    fi
    
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker daemon is not running"
        echo "Start Docker Desktop or run: sudo systemctl start docker"
        return 1
    fi
    
    print_success "Docker is running"
    return 0
}

# Function to check Kubernetes
check_kubernetes() {
    print_status "Checking Kubernetes..."
    
    if ! command_exists kubectl; then
        print_warning "kubectl is not installed"
        echo "Install kubectl from https://kubernetes.io/docs/tasks/tools/"
        return 1
    fi
    
    if ! kubectl cluster-info >/dev/null 2>&1; then
        print_warning "Cannot connect to Kubernetes cluster"
        echo "Start your local cluster (Docker Desktop, Minikube, etc.)"
        return 1
    fi
    
    print_success "Kubernetes cluster is accessible"
    return 0
}

# Function to check code quality tools
check_tools() {
    print_status "Checking code quality tools..."
    
    local missing_tools=()
    
    if ! command_exists trivy; then
        missing_tools+=("trivy")
    fi
    
    if ! command_exists jq; then
        missing_tools+=("jq")
    fi
    
    if [ ${#missing_tools[@]} -ne 0 ]; then
        print_warning "Missing tools: ${missing_tools[*]}"
        echo "Run: make install-tools"
        return 1
    fi
    
    print_success "All tools installed"
    return 0
}

# Function to check file permissions
check_permissions() {
    print_status "Checking file permissions..."
    
    local scripts=("scripts/local-pipeline.sh" "scripts/dev-setup.sh" "scripts/troubleshoot.sh")
    
    for script in "${scripts[@]}"; do
        if [ -f "$script" ] && [ ! -x "$script" ]; then
            print_warning "Making $script executable"
            chmod +x "$script"
        fi
    done
    
    print_success "File permissions checked"
}

# Function to check environment variables
check_env() {
    print_status "Checking environment variables..."
    
    if [ ! -f ".env" ]; then
        print_warning ".env file not found"
        echo "Run: make setup"
        return 1
    fi
    
    # Check if .env has required variables
    local required_vars=("ENVIRONMENT" "LOG_LEVEL" "API_HOST" "API_PORT")
    local missing_vars=()
    
    for var in "${required_vars[@]}"; do
        if ! grep -q "^${var}=" .env; then
            missing_vars+=("$var")
        fi
    done
    
    if [ ${#missing_vars[@]} -ne 0 ]; then
        print_warning "Missing environment variables: ${missing_vars[*]}"
        echo "Update .env file with required variables"
        return 1
    fi
    
    print_success "Environment variables configured"
    return 0
}

# Function to check for common issues
check_common_issues() {
    print_status "Checking for common issues..."
    
    # Check for Python cache issues
    if [ -d "__pycache__" ] || [ -d ".pytest_cache" ]; then
        print_warning "Python cache directories found"
        echo "Run: make clean"
    fi
    
    # Check for Docker image issues
    if docker images | grep -q "petrosa/tradeengine"; then
        print_status "Docker images found"
    else
        print_warning "No Docker images found"
        echo "Run: make build"
    fi
    
    # Check for Kubernetes namespace
    if command_exists kubectl; then
        if kubectl get namespace petrosa-apps >/dev/null 2>&1; then
            print_status "Kubernetes namespace exists"
        else
            print_warning "Kubernetes namespace not found"
            echo "Run: make deploy"
        fi
    fi
    
    print_success "Common issues check completed"
}

# Function to run quick fixes
run_fixes() {
    print_status "Running quick fixes..."
    
    # Clean up cache
    make clean
    
    # Fix permissions
    check_permissions
    
    # Reinstall dependencies if needed
    if ! check_dependencies; then
        print_status "Reinstalling dependencies..."
        make install-dev
    fi
    
    print_success "Quick fixes completed"
}

# Function to show detailed diagnostics
show_diagnostics() {
    print_status "Running detailed diagnostics..."
    
    echo ""
    echo "=== System Information ==="
    echo "OS: $(uname -s)"
    echo "Architecture: $(uname -m)"
    echo "Shell: $SHELL"
    
    echo ""
    echo "=== Python Information ==="
    python3 --version
    which python3
    
    echo ""
    echo "=== Docker Information ==="
    docker --version
    docker info --format "{{.ServerVersion}}"
    
    echo ""
    echo "=== Kubernetes Information ==="
    if command_exists kubectl; then
        kubectl version --client
        kubectl cluster-info 2>/dev/null || echo "Cannot connect to cluster"
    else
        echo "kubectl not installed"
    fi
    
    echo ""
    echo "=== Project Information ==="
    echo "Working directory: $(pwd)"
    echo "Git branch: $(git branch --show-current 2>/dev/null || echo "Not a git repo")"
    echo "Git status: $(git status --porcelain 2>/dev/null | wc -l) changes"
    
    echo ""
    echo "=== Environment ==="
    echo "Virtual environment: $([ -d ".venv" ] && echo "Found" || echo "Not found")"
    echo ".env file: $([ -f ".env" ] && echo "Found" || echo "Not found")"
    echo "Requirements files: $([ -f "requirements.txt" ] && echo "Found" || echo "Not found")"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --check-all      Run all checks"
    echo "  --python         Check Python environment"
    echo "  --deps           Check dependencies"
    echo "  --docker         Check Docker"
    echo "  --k8s            Check Kubernetes"
    echo "  --tools          Check code quality tools"
    echo "  --env            Check environment variables"
    echo "  --fix            Run quick fixes"
    echo "  --diagnostics    Show detailed diagnostics"
    echo "  --help           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --check-all   # Run all checks"
    echo "  $0 --fix         # Run quick fixes"
    echo "  $0 --diagnostics # Show detailed information"
}

# Main function
main() {
    local check_all=false
    local check_python=false
    local check_deps=false
    local check_docker=false
    local check_k8s=false
    local check_tools=false
    local check_env=false
    local run_fixes=false
    local show_diag=false
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --check-all)
                check_all=true
                shift
                ;;
            --python)
                check_python=true
                shift
                ;;
            --deps)
                check_deps=true
                shift
                ;;
            --docker)
                check_docker=true
                shift
                ;;
            --k8s)
                check_k8s=true
                shift
                ;;
            --tools)
                check_tools=true
                shift
                ;;
            --env)
                check_env=true
                shift
                ;;
            --fix)
                run_fixes=true
                shift
                ;;
            --diagnostics)
                show_diag=true
                shift
                ;;
            --help)
                show_usage
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
    
    # If no specific checks requested, run all
    if [ "$check_all" = false ] && [ "$check_python" = false ] && [ "$check_deps" = false ] && \
       [ "$check_docker" = false ] && [ "$check_k8s" = false ] && [ "$check_tools" = false ] && \
       [ "$check_env" = false ] && [ "$run_fixes" = false ] && [ "$show_diag" = false ]; then
        check_all=true
    fi
    
    print_status "Starting troubleshooting..."
    
    # Run requested checks
    if [ "$check_all" = true ] || [ "$check_python" = true ]; then
        check_python
    fi
    
    if [ "$check_all" = true ] || [ "$check_deps" = true ]; then
        check_dependencies
    fi
    
    if [ "$check_all" = true ] || [ "$check_docker" = true ]; then
        check_docker
    fi
    
    if [ "$check_all" = true ] || [ "$check_k8s" = true ]; then
        check_kubernetes
    fi
    
    if [ "$check_all" = true ] || [ "$check_tools" = true ]; then
        check_tools
    fi
    
    if [ "$check_all" = true ] || [ "$check_env" = true ]; then
        check_env
    fi
    
    if [ "$check_all" = true ]; then
        check_permissions
        check_common_issues
    fi
    
    if [ "$run_fixes" = true ]; then
        run_fixes
    fi
    
    if [ "$show_diag" = true ]; then
        show_diagnostics
    fi
    
    print_success "Troubleshooting completed!"
    echo ""
    echo "If issues persist, try:"
    echo "1. make clean"
    echo "2. make setup"
    echo "3. make pipeline"
}

# Run main function
main "$@" 