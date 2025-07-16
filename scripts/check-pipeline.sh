#!/bin/bash

# Petrosa Trading Engine - Pipeline Check Script
# This script checks the status of the local CI/CD pipeline

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="petrosa-tradeengine"
NAMESPACE="petrosa-apps"

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

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check Python environment
check_python_env() {
    print_status "Checking Python environment..."

    if [ ! -d ".venv" ]; then
        print_error "Virtual environment not found. Run 'make setup' first."
        return 1
    fi

    if ! source .venv/bin/activate 2>/dev/null; then
        print_error "Failed to activate virtual environment"
        return 1
    fi

    # Check Python version
    python_version=$(python --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    required_version="3.11"

    if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
        print_error "Python 3.11 or higher is required. Current version: $python_version"
        return 1
    fi

    print_success "Python environment is ready"
}

# Function to check dependencies
check_dependencies() {
    print_status "Checking dependencies..."

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

    if ! python -c "import fastapi" 2>/dev/null; then
        missing_packages+=("fastapi")
    fi

    if ! python -c "import motor" 2>/dev/null; then
        missing_packages+=("motor")
    fi

    if ! python -c "import pytest" 2>/dev/null; then
        missing_packages+=("pytest")
    fi

    if [ ${#missing_packages[@]} -ne 0 ]; then
        print_error "Missing packages: ${missing_packages[*]}"
        print_status "Run 'make install-dev' to install dependencies"
        return 1
    fi

    print_success "Dependencies are installed"
}

# Function to check code quality tools
check_code_quality_tools() {
    print_status "Checking code quality tools..."

    local missing_tools=()

    if ! command_exists black; then
        missing_tools+=("black")
    fi

    if ! command_exists flake8; then
        missing_tools+=("flake8")
    fi

    if ! command_exists mypy; then
        missing_tools+=("mypy")
    fi

    if ! command_exists ruff; then
        missing_tools+=("ruff")
    fi

    if [ ${#missing_tools[@]} -ne 0 ]; then
        print_warning "Missing code quality tools: ${missing_tools[*]}"
        print_status "Run 'make install-dev' to install development tools"
    else
        print_success "Code quality tools are available"
    fi
}

# Function to check Docker
check_docker() {
    print_status "Checking Docker..."

    if ! command_exists docker; then
        print_error "Docker not found"
        return 1
    fi

    if ! docker info >/dev/null 2>&1; then
        print_error "Docker daemon not running"
        return 1
    fi

    print_success "Docker is ready"
}

# Function to check Kubernetes
check_kubernetes() {
    print_status "Checking Kubernetes..."

    if ! command_exists kubectl; then
        print_warning "kubectl not found - Kubernetes deployment will be skipped"
        return 0
    fi

    if ! kubectl cluster-info >/dev/null 2>&1; then
        print_warning "Cannot connect to Kubernetes cluster"
        return 0
    fi

    print_success "Kubernetes is accessible"
}

# Function to check Kubernetes manifests
check_kubernetes_manifests() {
    print_status "Checking Kubernetes manifests..."

    local missing_manifests=()

    if [ ! -f "k8s/deployment.yaml" ]; then
        missing_manifests+=("deployment.yaml")
    fi

    if [ ! -f "k8s/service.yaml" ]; then
        missing_manifests+=("service.yaml")
    fi

    if [ ! -f "k8s/ingress.yaml" ]; then
        missing_manifests+=("ingress.yaml")
    fi

    if [ ! -f "k8s/hpa.yaml" ]; then
        missing_manifests+=("hpa.yaml")
    fi

    if [ ! -f "k8s/networkpolicy-allow-egress.yaml" ]; then
        missing_manifests+=("networkpolicy-allow-egress.yaml")
    fi

    if [ ${#missing_manifests[@]} -ne 0 ]; then
        print_error "Missing Kubernetes manifests: ${missing_manifests[*]}"
        return 1
    fi

    print_success "Kubernetes manifests are present"
}

# Function to check application code
check_application_code() {
    print_status "Checking application code..."

    local missing_files=()

    if [ ! -f "tradeengine/api.py" ]; then
        missing_files+=("tradeengine/api.py")
    fi

    if [ ! -f "tradeengine/position_manager.py" ]; then
        missing_files+=("tradeengine/position_manager.py")
    fi

    if [ ! -f "tradeengine/order_manager.py" ]; then
        missing_files+=("tradeengine/order_manager.py")
    fi

    if [ ! -f "shared/config.py" ]; then
        missing_files+=("shared/config.py")
    fi

    if [ ! -f "contracts/order.py" ]; then
        missing_files+=("contracts/order.py")
    fi

    if [ ${#missing_files[@]} -ne 0 ]; then
        print_error "Missing application files: ${missing_files[*]}"
        return 1
    fi

    print_success "Application code is present"
}

# Function to check tests
check_tests() {
    print_status "Checking tests..."

    if [ ! -d "tests" ]; then
        print_error "Tests directory not found"
        return 1
    fi

    local test_files=($(find tests -name "test_*.py" -type f))

    if [ ${#test_files[@]} -eq 0 ]; then
        print_warning "No test files found"
    else
        print_success "Found ${#test_files[@]} test files"
    fi
}

# Function to check scripts
check_scripts() {
    print_status "Checking scripts..."

    local missing_scripts=()

    if [ ! -f "scripts/local-pipeline.sh" ]; then
        missing_scripts+=("local-pipeline.sh")
    fi

    if [ ! -f "scripts/setup-mongodb.sh" ]; then
        missing_scripts+=("setup-mongodb.sh")
    fi

    if [ ${#missing_scripts[@]} -ne 0 ]; then
        print_warning "Missing scripts: ${missing_scripts[*]}"
    else
        print_success "Scripts are present"
    fi
}

# Function to check MongoDB setup
check_mongodb_setup() {
    print_status "Checking MongoDB setup..."

    if ! command_exists mongosh; then
        print_warning "mongosh not found - MongoDB setup will be manual"
        return 0
    fi

    # Try to connect to MongoDB
    if mongosh --eval "db.adminCommand('ping')" >/dev/null 2>&1; then
        print_success "MongoDB is accessible"
    else
        print_warning "MongoDB is not accessible - run 'make setup-mongodb'"
    fi
}

# Function to run comprehensive check
run_comprehensive_check() {
    print_status "Running comprehensive pipeline check..."

    local errors=0

    # Check Python environment
    if ! check_python_env; then
        errors=$((errors + 1))
    fi

    # Check dependencies
    if ! check_dependencies; then
        errors=$((errors + 1))
    fi

    # Check code quality tools
    check_code_quality_tools

    # Check Docker
    if ! check_docker; then
        errors=$((errors + 1))
    fi

    # Check Kubernetes
    check_kubernetes

    # Check Kubernetes manifests
    if ! check_kubernetes_manifests; then
        errors=$((errors + 1))
    fi

    # Check application code
    if ! check_application_code; then
        errors=$((errors + 1))
    fi

    # Check tests
    if ! check_tests; then
        errors=$((errors + 1))
    fi

    # Check scripts
    check_scripts

    # Check MongoDB setup
    check_mongodb_setup

    # Summary
    echo ""
    if [ $errors -eq 0 ]; then
        print_success "All checks passed! Pipeline is ready to run."
        print_status "Run 'make pipeline' to execute the complete pipeline."
    else
        print_error "Found $errors error(s). Please fix the issues before running the pipeline."
        return 1
    fi
}

# Main execution
main() {
    echo "Petrosa Trading Engine - Pipeline Check"
    echo "======================================"
    echo ""

    case "${1:-all}" in
        python)
            check_python_env
            ;;
        deps)
            check_dependencies
            ;;
        docker)
            check_docker
            ;;
        k8s)
            check_kubernetes
            check_kubernetes_manifests
            ;;
        code)
            check_application_code
            check_tests
            ;;
        mongodb)
            check_mongodb_setup
            ;;
        all)
            run_comprehensive_check
            ;;
        *)
            echo "Usage: $0 [python|deps|docker|k8s|code|mongodb|all]"
            echo ""
            echo "Check specific components:"
            echo "  python   - Check Python environment"
            echo "  deps     - Check dependencies"
            echo "  docker   - Check Docker"
            echo "  k8s      - Check Kubernetes"
            echo "  code     - Check application code"
            echo "  mongodb  - Check MongoDB setup"
            echo "  all      - Run comprehensive check (default)"
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
