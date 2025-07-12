#!/bin/bash

# Pipeline Diagnostic Script
# This script helps diagnose CI/CD pipeline issues

set -e

echo "🔍 Petrosa Trading Engine Pipeline Diagnostics"
echo "=============================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    local status=$1
    local message=$2
    case $status in
        "SUCCESS")
            echo -e "${GREEN}✅ $message${NC}"
            ;;
        "WARNING")
            echo -e "${YELLOW}⚠️  $message${NC}"
            ;;
        "ERROR")
            echo -e "${RED}❌ $message${NC}"
            ;;
        "INFO")
            echo -e "${BLUE}ℹ️  $message${NC}"
            ;;
    esac
}

# Check if we're in a GitHub Actions environment
check_github_environment() {
    echo ""
    print_status "INFO" "Checking GitHub Actions environment..."
    
    if [ -n "$GITHUB_ACTIONS" ]; then
        print_status "SUCCESS" "Running in GitHub Actions"
        echo "  - Workflow: $GITHUB_WORKFLOW"
        echo "  - Run ID: $GITHUB_RUN_ID"
        echo "  - Ref: $GITHUB_REF"
        echo "  - SHA: $GITHUB_SHA"
    else
        print_status "WARNING" "Not running in GitHub Actions"
    fi
}

# Check required secrets
check_secrets() {
    echo ""
    print_status "INFO" "Checking required secrets..."
    
    local missing_secrets=()
    
    # Check Docker Hub secrets
    if [ -z "$DOCKERHUB_USERNAME" ] && [ -z "${{ secrets.DOCKERHUB_USERNAME }}" ]; then
        missing_secrets+=("DOCKERHUB_USERNAME")
    fi
    
    if [ -z "$DOCKERHUB_TOKEN" ] && [ -z "${{ secrets.DOCKERHUB_TOKEN }}" ]; then
        missing_secrets+=("DOCKERHUB_TOKEN")
    fi
    
    # Check Kubernetes secrets
    if [ -z "$KUBE_CONFIG_DATA" ] && [ -z "${{ secrets.KUBE_CONFIG_DATA }}" ]; then
        missing_secrets+=("KUBE_CONFIG_DATA")
    fi
    
    if [ ${#missing_secrets[@]} -eq 0 ]; then
        print_status "SUCCESS" "All required secrets appear to be configured"
    else
        print_status "ERROR" "Missing required secrets: ${missing_secrets[*]}"
        echo "  Please configure these secrets in your GitHub repository settings"
    fi
}

# Check Docker Hub authentication
check_docker_auth() {
    echo ""
    print_status "INFO" "Checking Docker Hub authentication..."
    
    if [ -n "$DOCKERHUB_USERNAME" ] && [ -n "$DOCKERHUB_TOKEN" ]; then
        print_status "SUCCESS" "Docker Hub credentials available"
        echo "  - Username: $DOCKERHUB_USERNAME"
        echo "  - Token: [HIDDEN]"
    else
        print_status "WARNING" "Docker Hub credentials not available in environment"
        echo "  - This is normal if not running in GitHub Actions"
    fi
}

# Check Kubernetes configuration
check_k8s_config() {
    echo ""
    print_status "INFO" "Checking Kubernetes configuration..."
    
    if [ -f "k8s/kubeconfig.yaml" ]; then
        print_status "SUCCESS" "Local kubeconfig found"
    else
        print_status "WARNING" "Local kubeconfig not found"
    fi
    
    if [ -n "$KUBE_CONFIG_DATA" ]; then
        print_status "SUCCESS" "Kubernetes config data available"
    else
        print_status "WARNING" "Kubernetes config data not available"
    fi
}

# Check pipeline files
check_pipeline_files() {
    echo ""
    print_status "INFO" "Checking pipeline files..."
    
    local files=(
        ".github/workflows/ci-cd.yml"
        "k8s/deployment.yaml"
        "k8s/service.yaml"
        "k8s/ingress.yaml"
        "Dockerfile"
        "requirements.txt"
    )
    
    for file in "${files[@]}"; do
        if [ -f "$file" ]; then
            print_status "SUCCESS" "Found: $file"
        else
            print_status "ERROR" "Missing: $file"
        fi
    done
}

# Check Dockerfile
check_dockerfile() {
    echo ""
    print_status "INFO" "Checking Dockerfile..."
    
    if [ -f "Dockerfile" ]; then
        print_status "SUCCESS" "Dockerfile exists"
        
        # Check for common issues
        if grep -q "VERSION_PLACEHOLDER" Dockerfile; then
            print_status "WARNING" "Dockerfile contains VERSION_PLACEHOLDER"
        fi
        
        if grep -q "petrosa-tradeengine" Dockerfile; then
            print_status "SUCCESS" "Dockerfile references correct image name"
        else
            print_status "WARNING" "Dockerfile may not reference correct image name"
        fi
    else
        print_status "ERROR" "Dockerfile not found"
    fi
}

# Check Kubernetes manifests
check_k8s_manifests() {
    echo ""
    print_status "INFO" "Checking Kubernetes manifests..."
    
    local manifests=(
        "k8s/deployment.yaml"
        "k8s/service.yaml"
        "k8s/ingress.yaml"
        "k8s/configmap.yaml"
    )
    
    for manifest in "${manifests[@]}"; do
        if [ -f "$manifest" ]; then
            print_status "SUCCESS" "Found: $manifest"
            
            # Check for VERSION_PLACEHOLDER
            if grep -q "VERSION_PLACEHOLDER" "$manifest"; then
                print_status "INFO" "  - Contains VERSION_PLACEHOLDER (will be replaced during deployment)"
            fi
            
            # Check for correct image name
            if grep -q "petrosa/petrosa-tradeengine" "$manifest"; then
                print_status "SUCCESS" "  - Uses correct image name"
            else
                print_status "WARNING" "  - May not use correct image name"
            fi
        else
            print_status "WARNING" "Missing: $manifest"
        fi
    done
}

# Check recent pipeline runs
check_recent_runs() {
    echo ""
    print_status "INFO" "Checking recent pipeline runs..."
    
    if [ -n "$GITHUB_ACTIONS" ]; then
        echo "  - Current run: $GITHUB_RUN_ID"
        echo "  - Workflow: $GITHUB_WORKFLOW"
        echo "  - Branch: $GITHUB_REF_NAME"
        echo "  - Commit: $GITHUB_SHA"
    else
        print_status "INFO" "Not in GitHub Actions environment"
        echo "  - Check GitHub repository Actions tab for recent runs"
    fi
}

# Main diagnostic function
run_diagnostics() {
    echo "Starting pipeline diagnostics..."
    
    check_github_environment
    check_secrets
    check_docker_auth
    check_k8s_config
    check_pipeline_files
    check_dockerfile
    check_k8s_manifests
    check_recent_runs
    
    echo ""
    print_status "INFO" "Diagnostic Summary"
    echo "=================="
    echo ""
    echo "Common Pipeline Issues:"
    echo "1. Missing GitHub Secrets: DOCKERHUB_USERNAME, DOCKERHUB_TOKEN, KUBE_CONFIG_DATA"
    echo "2. Docker Hub authentication failures"
    echo "3. Kubernetes cluster connectivity issues"
    echo "4. Image name mismatches between pipeline and manifests"
    echo "5. Missing or incorrect VERSION_PLACEHOLDER replacements"
    echo ""
    echo "Next Steps:"
    echo "1. Check GitHub repository Settings > Secrets and variables > Actions"
    echo "2. Verify Docker Hub repository exists and is accessible"
    echo "3. Test Kubernetes cluster connectivity"
    echo "4. Review recent pipeline runs in GitHub Actions tab"
    echo "5. Check pipeline logs for specific error messages"
}

# Run diagnostics
run_diagnostics 