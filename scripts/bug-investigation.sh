#!/bin/bash

# Bug Investigation Script for Petrosa Services
# This script provides quick commands for systematic bug investigation

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

# Function to check if we're in a Petrosa service directory
check_service() {
    if [[ -f "Makefile" && -f "requirements.txt" ]]; then
        if [[ -d "ta_bot" ]]; then
            echo "ta-bot"
        elif [[ -d "tradeengine" ]]; then
            echo "tradeengine"
        elif [[ -d "fetchers" ]]; then
            echo "data-extractor"
        else
            echo "unknown"
        fi
    else
        echo "none"
    fi
}

# Function to show usage
show_usage() {
    echo "Bug Investigation Script for Petrosa Services"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  confirm     - Confirm bug behavior locally"
    echo "  investigate - Run investigation commands"
    echo "  test        - Run comprehensive tests"
    echo "  docker      - Test in Docker environment"
    echo "  k8s         - Check Kubernetes status"
    echo "  all         - Run complete investigation pipeline"
    echo "  help        - Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 confirm    # Confirm the bug exists locally"
    echo "  $0 test       # Run all tests"
    echo "  $0 all        # Run complete investigation"
}

# Function to confirm bug behavior
confirm_bug() {
    print_status "Phase 1: Confirming bug behavior locally"

    SERVICE=$(check_service)
    if [[ "$SERVICE" == "none" ]]; then
        print_error "Not in a Petrosa service directory"
        exit 1
    fi

    print_status "Service detected: $SERVICE"

    # Clean environment setup
    print_status "Setting up clean environment..."
    make setup

    # Run basic tests
    print_status "Running basic tests..."
    make test

    # Run complete pipeline
    print_status "Running complete pipeline..."
    ./scripts/local-pipeline.sh all

    print_success "Bug confirmation phase completed"
}

# Function to run investigation commands
investigate() {
    print_status "Phase 2: Investigation and hypothesis formation"

    SERVICE=$(check_service)
    if [[ "$SERVICE" == "none" ]]; then
        print_error "Not in a Petrosa service directory"
        exit 1
    fi

    print_status "Service: $SERVICE"

    # Check Python environment
    print_status "Checking Python environment..."
    python3 --version
    pip list | head -10

    # Check configuration
    print_status "Checking configuration files..."
    if [[ -f "env.example" ]]; then
        echo "Environment variables template:"
        cat env.example | head -10
    fi

    if [[ -f "ta_bot/config.py" ]]; then
        echo "TA Bot config structure:"
        head -20 ta_bot/config.py
    elif [[ -f "shared/config.py" ]]; then
        echo "Shared config structure:"
        head -20 shared/config.py
    elif [[ -f "config/__init__.py" ]]; then
        echo "Data extractor config structure:"
        head -20 config/__init__.py
    fi

    # Check dependencies
    print_status "Checking dependencies..."
    echo "Requirements:"
    cat requirements.txt

    # Check for common issues
    print_status "Checking for common issues..."

    # Check for NumPy compatibility issues
    if grep -q "numpy" requirements.txt; then
        print_warning "NumPy detected - check for compatibility issues"
        pip show numpy
    fi

    # Check for missing environment variables
    if [[ -f ".env" ]]; then
        print_status "Environment file exists"
    else
        print_warning "No .env file found - check env.example"
    fi

    print_success "Investigation phase completed"
}

# Function to run comprehensive tests
run_tests() {
    print_status "Phase 4: Comprehensive testing and validation"

    SERVICE=$(check_service)
    if [[ "$SERVICE" == "none" ]]; then
        print_error "Not in a Petrosa service directory"
        exit 1
    fi

    # Run unit tests
    print_status "Running unit tests..."
    make test

    # Run linting
    print_status "Running linting..."
    make lint

    # Run complete pipeline
    print_status "Running complete pipeline..."
    ./scripts/local-pipeline.sh all

    # Service-specific tests
    case $SERVICE in
        "tradeengine")
            if [[ -f "scripts/test-api-endpoint-flow.py" ]]; then
                print_status "Running API endpoint tests..."
                python scripts/test-api-endpoint-flow.py
            fi
            ;;
        "data-extractor")
            if [[ -f "scripts/deploy-local.sh" ]]; then
                print_status "Running local deployment test..."
                ./scripts/deploy-local.sh
            fi
            ;;
    esac

    print_success "Testing phase completed"
}

# Function to test in Docker
test_docker() {
    print_status "Testing in Docker environment"

    SERVICE=$(check_service)
    if [[ "$SERVICE" == "none" ]]; then
        print_error "Not in a Petrosa service directory"
        exit 1
    fi

    # Build image
    print_status "Building Docker image..."
    make build

    # Test in container
    print_status "Testing in container..."
    make run-docker

    # Container-specific tests
    print_status "Running container-specific tests..."
    make container

    print_success "Docker testing completed"
}

# Function to check Kubernetes status
check_k8s() {
    print_status "Checking Kubernetes status"

    # Check if kubeconfig exists
    if [[ ! -f "k8s/kubeconfig.yaml" ]]; then
        print_error "kubeconfig.yaml not found"
        exit 1
    fi

    # Set kubeconfig
    export KUBECONFIG=k8s/kubeconfig.yaml

    # Check cluster connection
    print_status "Checking cluster connection..."
    kubectl --kubeconfig=k8s/kubeconfig.yaml cluster-info

    # Check namespace
    print_status "Checking petrosa-apps namespace..."
    kubectl --kubeconfig=k8s/kubeconfig.yaml get namespace petrosa-apps

    # Check deployments
    print_status "Checking deployments..."
    kubectl --kubeconfig=k8s/kubeconfig.yaml get deployments -n petrosa-apps

    # Check pods
    print_status "Checking pods..."
    kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps

    # Check services
    print_status "Checking services..."
    kubectl --kubeconfig=k8s/kubeconfig.yaml get services -n petrosa-apps

    print_success "Kubernetes status check completed"
}

# Function to run complete investigation
run_all() {
    print_status "Running complete bug investigation pipeline"

    confirm_bug
    echo ""
    investigate
    echo ""
    run_tests
    echo ""
    test_docker
    echo ""
    check_k8s
    echo ""

    print_success "Complete investigation pipeline finished"
    print_status "Next steps:"
    echo "1. Review the output above"
    echo "2. Form hypotheses about the root cause"
    echo "3. Make targeted changes"
    echo "4. Re-run tests to validate fixes"
    echo "5. Document the solution"
}

# Main script logic
case "${1:-help}" in
    "confirm")
        confirm_bug
        ;;
    "investigate")
        investigate
        ;;
    "test")
        run_tests
        ;;
    "docker")
        test_docker
        ;;
    "k8s")
        check_k8s
        ;;
    "all")
        run_all
        ;;
    "help"|*)
        show_usage
        ;;
esac
