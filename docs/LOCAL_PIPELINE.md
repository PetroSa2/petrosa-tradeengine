# Local CI/CD Pipeline Guide

This guide explains how to replicate the GitHub Actions CI/CD pipeline locally to test and fix potential errors before pushing to the repository.

## 🚀 Quick Start

### 1. Initial Setup
```bash
# Setup development environment
make setup

# Or run the complete pipeline
make pipeline
```

### 2. Individual Stages
```bash
# Run specific stages
make lint          # Code quality checks
make test          # Run tests with coverage
make security      # Security scan
make build         # Build Docker image
make container     # Test Docker container
make deploy        # Deploy to local K8s
```

## 📋 Pipeline Stages

The local pipeline replicates all GitHub Actions stages:

### 1. **Setup** (`setup`)
- ✅ Python 3.11+ version check
- ✅ Virtual environment creation
- ✅ Dependencies installation
- ✅ Environment configuration

### 2. **Lint & Test** (`lint`, `test`)
- ✅ **Flake8**: Code linting and style checks
- ✅ **Black**: Code formatting verification
- ✅ **Ruff**: Fast Python linter
- ✅ **MyPy**: Type checking
- ✅ **Pytest**: Unit tests with coverage reporting

### 3. **Security Scan** (`security`)
- ✅ **Trivy**: Vulnerability scanning
- ✅ JSON report generation
- ✅ High/critical vulnerability detection

### 4. **Build** (`build`)
- ✅ **Docker**: Multi-stage image build
- ✅ Local image tagging
- ✅ Build cache optimization

### 5. **Container Test** (`container`)
- ✅ Container startup verification
- ✅ Health endpoint testing
- ✅ Application functionality validation

### 6. **Deploy** (`deploy`)
- ✅ **Kubernetes**: Local cluster deployment
- ✅ Namespace creation
- ✅ Resource application
- ✅ Deployment verification

## 🛠️ Available Commands

### Makefile Targets
```bash
# Development
make setup          # Complete environment setup
make install-dev    # Install development dependencies
make install-prod   # Install production dependencies

# Code Quality
make lint           # Run all linting checks
make format         # Format code with black
make test           # Run tests with coverage
make security       # Run security scan

# Docker
make build          # Build Docker image
make container      # Test Docker container
make docker-clean   # Clean up Docker images

# Deployment
make deploy         # Deploy to local Kubernetes
make pipeline       # Run complete pipeline

# Utilities
make clean          # Clean up temporary files
make run            # Run application locally
make run-docker     # Run application in Docker

# Quick Workflows
make dev            # Setup + lint + test
make prod           # Lint + test + security + build + container
```

### Direct Script Usage
```bash
# Run complete pipeline
./scripts/local-pipeline.sh

# Run specific stages
./scripts/local-pipeline.sh lint test
./scripts/local-pipeline.sh build container
./scripts/local-pipeline.sh deploy

# With cleanup
./scripts/local-pipeline.sh --cleanup-docker
```

### Troubleshooting
```bash
# Run all checks
./scripts/troubleshoot.sh --check-all

# Run quick fixes
./scripts/troubleshoot.sh --fix

# Show detailed diagnostics
./scripts/troubleshoot.sh --diagnostics

# Check specific components
./scripts/troubleshoot.sh --python --deps --docker
```

## 🔧 Prerequisites

### Required Tools
- **Python 3.11+**: `python3 --version`
- **Docker**: `docker --version`
- **Make**: `make --version`

### Optional Tools
- **kubectl**: For Kubernetes deployment
- **trivy**: For security scanning
- **jq**: For JSON processing

### Install Optional Tools
```bash
# macOS (Homebrew)
brew install trivy jq

# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y trivy jq

# CentOS/RHEL
sudo yum install -y trivy jq

# Or use Makefile
make install-tools
```

## 🐳 Docker Configuration

### Local Image Tags
- **Format**: `petrosa/tradeengine:local-YYYYMMDD-HHMMSS`
- **Latest**: `petrosa/tradeengine:latest`

### Build Cache
- Uses Docker layer caching
- Optimized for faster rebuilds
- Multi-stage build for smaller images

### Container Testing
- Automatic health check verification
- Endpoint testing (`/health`, `/ready`, `/live`)
- Graceful cleanup on completion

## ☸️ Kubernetes Deployment

### Local Cluster Requirements
- **Docker Desktop**: With Kubernetes enabled
- **Minikube**: Local Kubernetes cluster
- **MicroK8s**: Ubuntu's local Kubernetes

### Namespace
- **Name**: `petrosa-apps`
- **Labels**: `app=petrosa-tradeengine`

### Resources Applied
- Deployment with 3 replicas
- Service (ClusterIP)
- ConfigMap
- Secrets (if configured)

## 🔍 Troubleshooting Common Issues

### Python Issues
```bash
# Check Python version
python3 --version

# Recreate virtual environment
rm -rf .venv
make setup
```

### Dependency Issues
```bash
# Reinstall dependencies
make clean
make install-dev

# Check for conflicts
pip check
```

### Docker Issues
```bash
# Check Docker daemon
docker info

# Clean up images
make docker-clean

# Rebuild from scratch
docker build --no-cache -t petrosa/tradeengine:latest .
```

### Kubernetes Issues
```bash
# Check cluster status
kubectl cluster-info

# Check namespace
kubectl get namespace petrosa-apps

# View deployment logs
make k8s-logs

# Clean up resources
make k8s-clean
```

### Linting Issues
```bash
# Auto-fix formatting
make format

# Check specific tools
flake8 . --count --select=E9,F63,F7,F82
black --check --diff .
ruff check .
mypy tradeengine/ contracts/ shared/
```

### Test Issues
```bash
# Run tests with verbose output
pytest -v --tb=short

# Run specific test file
pytest tests/test_api.py -v

# Run with coverage
pytest --cov=tradeengine --cov-report=html
```

## 📊 Monitoring and Debugging

### Health Checks
```bash
# Test health endpoints
make health

# Or manually
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/live
```

### Logs and Metrics
```bash
# Application logs
docker logs <container-name>

# Kubernetes logs
make k8s-logs

# Application metrics
make monitor
```

### Performance Testing
```bash
# Basic load testing
ab -n 100 -c 10 http://localhost:8000/health

# Or use wrk
wrk -t12 -c400 -d30s http://localhost:8000/health
```

## 🔄 Workflow Examples

### Development Workflow
```bash
# 1. Setup environment
make setup

# 2. Make changes to code

# 3. Run quality checks
make lint test

# 4. Test locally
make run

# 5. Build and test container
make build container

# 6. Deploy to local K8s
make deploy
```

### Pre-commit Workflow
```bash
# Before committing
make dev          # Quick development check
make prod         # Full production readiness check
make pipeline     # Complete CI/CD simulation
```

### Debugging Workflow
```bash
# 1. Identify issue
./scripts/troubleshoot.sh --check-all

# 2. Run quick fixes
./scripts/troubleshoot.sh --fix

# 3. Test specific component
./scripts/troubleshoot.sh --python --deps

# 4. Show detailed info
./scripts/troubleshoot.sh --diagnostics
```

## 📈 Performance Optimization

### Build Optimization
- Use Docker layer caching
- Optimize `.dockerignore`
- Multi-stage builds
- Minimal base images

### Test Optimization
- Parallel test execution
- Coverage caching
- Selective test running
- Mock external dependencies

### Deployment Optimization
- Resource limits and requests
- Health check tuning
- Rolling update strategy
- Horizontal pod autoscaling

## 🔐 Security Considerations

### Local Security
- Use `.env` for local secrets
- Don't commit sensitive data
- Regular dependency updates
- Vulnerability scanning

### Container Security
- Non-root user execution
- Minimal base images
- Regular security scans
- Resource limits

### Kubernetes Security
- Namespace isolation
- RBAC configuration
- Network policies
- Secret management

## 📚 Additional Resources

### Documentation
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [GitHub Actions](https://docs.github.com/en/actions)

### Tools
- [Trivy Security Scanner](https://aquasecurity.github.io/trivy/)
- [Black Code Formatter](https://black.readthedocs.io/)
- [Ruff Linter](https://github.com/astral-sh/ruff)
- [MyPy Type Checker](https://mypy.readthedocs.io/)

## 🆘 Getting Help

### Common Commands
```bash
# Show all available commands
make help

# Show script usage
./scripts/local-pipeline.sh --help
./scripts/troubleshoot.sh --help

# Run diagnostics
./scripts/troubleshoot.sh --diagnostics
```

### Debug Mode
```bash
# Enable verbose output
export DEBUG=1
make pipeline

# Or run with bash debug
bash -x ./scripts/local-pipeline.sh
```

### Log Files
- **Pipeline logs**: Console output
- **Docker logs**: `docker logs <container>`
- **Kubernetes logs**: `kubectl logs -n petrosa-apps`
- **Test reports**: `htmlcov/index.html`

This local pipeline ensures your code is production-ready before pushing to GitHub, catching issues early and maintaining high code quality standards! 🎉 