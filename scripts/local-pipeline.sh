#!/bin/bash

# Petrosa Trading Engine - Local CI/CD Pipeline
# This script replicates the GitHub Actions workflow locally

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="petrosa-tradeengine"
DOCKER_IMAGE="petrosa/tradeengine"
DOCKER_TAG="local-$(date +%Y%m%d-%H%M%S)"
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

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    local missing_tools=()
    
    # Check Python
    if ! command_exists python3; then
        missing_tools+=("python3")
    fi
    
    # Check pip
    if ! command_exists pip; then
        missing_tools+=("pip")
    fi
    
    # Check Docker
    if ! command_exists docker; then
        missing_tools+=("docker")
    fi
    
    # Check kubectl (optional)
    if ! command_exists kubectl; then
        print_warning "kubectl not found - deployment stage will be skipped"
    fi
    
    # Check trivy (optional)
    if ! command_exists trivy; then
        print_warning "trivy not found - security scan will be skipped"
    fi
    
    if [ ${#missing_tools[@]} -ne 0 ]; then
        print_error "Missing required tools: ${missing_tools[*]}"
        print_error "Please install the missing tools and try again."
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

# Function to setup Python environment
setup_python_env() {
    print_status "Setting up Python environment..."
    
    # Check Python version
    python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    required_version="3.11"
    
    if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
        print_error "Python 3.11 or higher is required. Current version: $python_version"
        exit 1
    fi
    
    # Create virtual environment if it doesn't exist
    if [ ! -d ".venv" ]; then
        print_status "Creating virtual environment..."
        python3 -m venv .venv
    fi
    
    # Activate virtual environment
    print_status "Activating virtual environment..."
    source .venv/bin/activate
    
    # Upgrade pip
    print_status "Upgrading pip..."
    pip install --upgrade pip
    
    print_success "Python environment setup complete"
}

# Function to install dependencies
install_dependencies() {
    print_status "Installing dependencies..."
    
    # Install production dependencies
    print_status "Installing production dependencies..."
    pip install -r requirements.txt
    
    # Install development dependencies
    print_status "Installing development dependencies..."
    pip install -r requirements-dev.txt
    
    print_success "Dependencies installation complete"
}

# Function to run linting and formatting checks
run_linting() {
    print_status "Running linting and formatting checks..."
    
    local lint_errors=0
    
    # Run flake8
    print_status "Running flake8..."
    if ! flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics; then
        print_error "Flake8 found critical errors"
        lint_errors=$((lint_errors + 1))
    fi
    
    if ! flake8 . --count --exit-zero --max-complexity=10 --max-line-length=88 --statistics; then
        print_warning "Flake8 found style issues"
    fi
    
    # Run black check
    print_status "Running black formatting check..."
    if ! black --check --diff .; then
        print_error "Black formatting check failed"
        print_status "Run 'black .' to fix formatting issues"
        lint_errors=$((lint_errors + 1))
    fi
    
    # Run ruff
    print_status "Running ruff..."
    if ! ruff check .; then
        print_error "Ruff found issues"
        lint_errors=$((lint_errors + 1))
    fi
    
    # Run mypy
    print_status "Running mypy type checking..."
    if ! mypy tradeengine/ contracts/ shared/; then
        print_error "MyPy found type errors"
        lint_errors=$((lint_errors + 1))
    fi
    
    if [ $lint_errors -eq 0 ]; then
        print_success "All linting checks passed"
    else
        print_error "Linting failed with $lint_errors error(s)"
        return 1
    fi
}

# Function to run tests
run_tests() {
    print_status "Running tests..."
    
    # Set test environment variables
    export ENVIRONMENT=testing
    export MONGODB_URL=mongodb://localhost:27017
    export NATS_SERVERS=nats://localhost:4222
    
    # Run pytest with coverage
    if pytest --cov=tradeengine --cov=contracts --cov=shared --cov-report=term-missing --cov-report=html; then
        print_success "Tests passed"
        print_status "Coverage report generated in htmlcov/index.html"
    else
        print_error "Tests failed"
        return 1
    fi
}

# Function to run security scan
run_security_scan() {
    if ! command_exists trivy; then
        print_warning "Trivy not found - skipping security scan"
        return 0
    fi
    
    print_status "Running security scan with Trivy..."
    
    # Create output directory
    mkdir -p .trivy
    
    # Run Trivy scan
    if trivy fs --format json --output .trivy/trivy-results.json .; then
        print_success "Security scan completed"
        print_status "Results saved to .trivy/trivy-results.json"
        
        # Check for high/critical vulnerabilities
        if jq -e '.Results[] | select(.Vulnerabilities[] | .Severity == "HIGH" or .Severity == "CRITICAL")' .trivy/trivy-results.json >/dev/null 2>&1; then
            print_warning "High or critical vulnerabilities found"
            print_status "Review .trivy/trivy-results.json for details"
        else
            print_success "No high or critical vulnerabilities found"
        fi
    else
        print_error "Security scan failed"
        return 1
    fi
}

# Function to build Docker image
build_docker_image() {
    print_status "Building Docker image..."
    
    # Check if Docker daemon is running
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker daemon is not running"
        return 1
    fi
    
    # Build image
    if docker build -t "$DOCKER_IMAGE:$DOCKER_TAG" -t "$DOCKER_IMAGE:latest" .; then
        print_success "Docker image built successfully"
        print_status "Image: $DOCKER_IMAGE:$DOCKER_TAG"
    else
        print_error "Docker build failed"
        return 1
    fi
}

# Function to run Docker container tests
test_docker_container() {
    print_status "Testing Docker container..."
    
    # Create test container
    local container_name="petrosa-test-$(date +%s)"
    
    # Run container in background
    if docker run -d --name "$container_name" -p 8000:8000 "$DOCKER_IMAGE:$DOCKER_TAG"; then
        print_status "Container started, waiting for health check..."
        
        # Wait for container to be ready
        local max_attempts=30
        local attempt=1
        
        while [ $attempt -le $max_attempts ]; do
            if curl -f http://localhost:8000/health >/dev/null 2>&1; then
                print_success "Container health check passed"
                break
            fi
            
            if [ $attempt -eq $max_attempts ]; then
                print_error "Container health check failed after $max_attempts attempts"
                docker logs "$container_name"
                docker stop "$container_name" >/dev/null 2>&1 || true
                docker rm "$container_name" >/dev/null 2>&1 || true
                return 1
            fi
            
            sleep 2
            attempt=$((attempt + 1))
        done
        
        # Test health endpoints
        print_status "Testing health endpoints..."
        curl -s http://localhost:8000/health | jq . || print_warning "Health endpoint test failed"
        curl -s http://localhost:8000/ready | jq . || print_warning "Ready endpoint test failed"
        curl -s http://localhost:8000/live | jq . || print_warning "Live endpoint test failed"
        
        # Stop and remove test container
        docker stop "$container_name" >/dev/null 2>&1 || true
        docker rm "$container_name" >/dev/null 2>&1 || true
        
        print_success "Docker container tests passed"
    else
        print_error "Failed to start test container"
        return 1
    fi
}

# Function to deploy to Kubernetes
deploy_to_kubernetes() {
    print_status "Deploying to Kubernetes..."
    
    # Check if kubectl is available
    if ! command_exists kubectl; then
        print_error "kubectl not found - skipping deployment"
        return 1
    fi
    
    # Check if namespace exists, create if not
    if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
        print_status "Creating namespace $NAMESPACE..."
        kubectl create namespace "$NAMESPACE"
    fi
    
    # Apply Kubernetes manifests
    print_status "Applying Kubernetes manifests..."
    
    # Apply deployment
    kubectl apply -f k8s/deployment.yaml -n "$NAMESPACE"
    
    # Apply service
    kubectl apply -f k8s/service.yaml -n "$NAMESPACE"
    
    # Apply ingress
    kubectl apply -f k8s/ingress.yaml -n "$NAMESPACE"
    
    # Apply HPA
    kubectl apply -f k8s/hpa.yaml -n "$NAMESPACE"
    
    # Apply network policy
    kubectl apply -f k8s/networkpolicy-allow-egress.yaml -n "$NAMESPACE"
    
    # Wait for deployment to be ready
    print_status "Waiting for deployment to be ready..."
    kubectl rollout status deployment/petrosa-tradeengine -n "$NAMESPACE" --timeout=300s
    
    # Get deployment status
    print_status "Deployment status:"
    kubectl get pods -n "$NAMESPACE" -l app=petrosa-tradeengine
    
    print_success "Deployment completed successfully"
}

# Function to cleanup
cleanup() {
    print_status "Cleaning up..."
    
    # Remove temporary files
    rm -f k8s/deployment-local.yaml
    
    # Optionally remove Docker images
    if [ "${CLEANUP_DOCKER:-false}" = "true" ]; then
        print_status "Removing Docker images..."
        docker rmi "$DOCKER_IMAGE:$DOCKER_TAG" >/dev/null 2>&1 || true
        docker rmi "$DOCKER_IMAGE:latest" >/dev/null 2>&1 || true
    fi
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS] [STAGES]"
    echo ""
    echo "Stages:"
    echo "  setup      Setup Python environment and install dependencies"
    echo "  lint       Run linting and formatting checks"
    echo "  test       Run tests with coverage"
    echo "  security   Run security scan with Trivy"
    echo "  build      Build Docker image"
    echo "  container  Test Docker container"
    echo "  deploy     Deploy to local Kubernetes cluster"
    echo "  all        Run all stages (default)"
    echo ""
    echo "Options:"
    echo "  --cleanup-docker    Remove Docker images after pipeline"
    echo "  --help              Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Run all stages"
    echo "  $0 lint test          # Run only linting and tests"
    echo "  $0 build container    # Run only Docker stages"
    echo "  $0 --cleanup-docker   # Run all stages and cleanup Docker images"
}

# Main function
main() {
    local stages=("all")
    local cleanup_docker=false
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --cleanup-docker)
                cleanup_docker=true
                shift
                ;;
            --help)
                show_usage
                exit 0
                ;;
            -*)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
            *)
                stages=("$@")
                break
                ;;
        esac
    done
    
    # Set cleanup flag
    export CLEANUP_DOCKER="$cleanup_docker"
    
    print_status "Starting local CI/CD pipeline..."
    print_status "Stages: ${stages[*]}"
    print_status "Docker tag: $DOCKER_TAG"
    
    # Run stages
    for stage in "${stages[@]}"; do
        case $stage in
            setup)
                check_prerequisites
                setup_python_env
                install_dependencies
                ;;
            lint)
                run_linting
                ;;
            test)
                run_tests
                ;;
            security)
                run_security_scan
                ;;
            build)
                build_docker_image
                ;;
            container)
                test_docker_container
                ;;
            deploy)
                deploy_to_kubernetes
                ;;
            all)
                check_prerequisites
                setup_python_env
                install_dependencies
                run_linting
                run_tests
                run_security_scan
                build_docker_image
                test_docker_container
                deploy_to_kubernetes
                ;;
            *)
                print_error "Unknown stage: $stage"
                show_usage
                exit 1
                ;;
        esac
    done
    
    cleanup
    
    print_success "Local CI/CD pipeline completed successfully!"
    print_status "Docker image: $DOCKER_IMAGE:$DOCKER_TAG"
}

# Trap to cleanup on exit
trap cleanup EXIT

# Run main function
main "$@" 