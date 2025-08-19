# Unified Petrosa Systems Guide

## Overview
This guide provides unified procedures for all Petrosa systems:
- **petrosa-bot-ta-analysis**: Technical Analysis bot for crypto trading
- **petrosa-tradeengine**: Cryptocurrency trading engine system
- **petrosa-binance-data-extractor**: Cryptocurrency data extraction system

All systems share the same infrastructure, deployment patterns, and development workflows.

## Infrastructure

### Remote MicroK8s Cluster
- **Server**: `https://192.168.194.253:16443`
- **Namespace**: `petrosa-apps`
- **Connection**: Use `k8s/kubeconfig.yaml` in each project
- **No local Kubernetes installation required**

### Common Kubernetes Resources
- **Secret**: `petrosa-sensitive-credentials` (shared across all projects)
- **ConfigMap**: `petrosa-common-config` (shared configuration)
- **Project-specific ConfigMaps**: Each project has its own config

## Development Workflow

### 1. Environment Setup
```bash
# All projects use the same setup pattern
make setup
make install-dev
```

### 2. Code Quality
```bash
# Standardized quality checks
make lint      # flake8, black, ruff, mypy
make format    # black formatting
make test      # pytest with coverage
make security  # Trivy vulnerability scan
```

### 3. Docker Operations
```bash
# Standardized Docker workflow
make build     # Build image
make container # Test container
make run-docker # Run locally in Docker
```

### 4. Kubernetes Deployment
```bash
# Set cluster context
export KUBECONFIG=k8s/kubeconfig.yaml

# Deploy to remote cluster
make deploy
make k8s-status
make k8s-logs
```

## Testing Procedures

### Standard Test Commands
```bash
# All projects follow the same testing pattern
make test                                    # Run all tests
python -m pytest tests/ -v --cov=. --cov-report=term  # With coverage
python -m pytest tests/test_specific.py -v   # Specific test file
make lint                                    # Code quality
make security                                # Security scan
```

### Pipeline Testing
```bash
# Complete local CI/CD pipeline
make pipeline

# Specific pipeline stages
./scripts/local-pipeline.sh lint test
./scripts/local-pipeline.sh build container
./scripts/local-pipeline.sh deploy
```

## Environment Variables

### Common Variables (All Projects)
```bash
ENVIRONMENT=production
LOG_LEVEL=INFO
```

### Project-Specific Variables

#### TA Bot
```bash
NATS_URL=nats://nats-server:4222
API_ENDPOINT=https://api.example.com/signals
```

#### Trading Engine
```bash
MONGODB_URL=mongodb://mongodb:27017/trading
BINANCE_API_KEY=your-api-key
BINANCE_API_SECRET=your-api-secret
JWT_SECRET_KEY=your-jwt-secret
```

#### Data Extractor
```bash
DB_ADAPTER=mysql
MYSQL_URI=mysql+pymysql://user:pass@mysql:3306/binance_data
BINANCE_API_KEY=your-api-key
BINANCE_API_SECRET=your-api-secret
```

## Kubernetes Configuration

### Critical Rules
- **ALWAYS use `--kubeconfig=k8s/kubeconfig.yaml`** with kubectl commands
- **NEVER create new secrets/configmaps** - use existing ones only
- **NEVER replace VERSION_PLACEHOLDER** - it's part of the deployment system
- **Use `petrosa-sensitive-credentials`** for all credentials
- **Use `petrosa-common-config`** for shared configuration

### Standard Deployment Pattern
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: petrosa-{project-name}
  namespace: petrosa-apps
  labels:
    app: petrosa-{project-name}
spec:
  replicas: 3
  selector:
    matchLabels:
      app: petrosa-{project-name}
  template:
    metadata:
      labels:
        app: petrosa-{project-name}
    spec:
      containers:
      - name: {project-name}
        image: yurisa2/petrosa-{project-name}:VERSION_PLACEHOLDER
        ports:
        - containerPort: 80
        envFrom:
        - secretRef:
            name: petrosa-sensitive-credentials
        - configMapRef:
            name: petrosa-common-config
        livenessProbe:
          httpGet:
            path: /health
            port: 80
        readinessProbe:
          httpGet:
            path: /ready
            port: 80
```

## CI/CD Pipeline

### Standard Pipeline Stages
1. **Setup**: Environment and dependencies
2. **Lint**: Code quality checks (flake8, black, ruff, mypy)
3. **Test**: Unit tests with coverage
4. **Security**: Vulnerability scanning with Trivy
5. **Build**: Docker image building
6. **Container**: Container testing
7. **Deploy**: Kubernetes deployment

### Pipeline Fix Process
When fixing CI/CD issues:
1. Run all tests locally: `make test`
2. Fix linting errors: `make lint`
3. Run security scan: `make security`
4. Build and test container: `make build && make container`
5. Commit and push changes
6. Monitor GitHub Actions pipeline
7. **Continue until pipeline is fully green**

## GitHub CLI Commands

### File Output Pattern
Always use file-based approach for GitHub CLI:
```bash
# Example pattern
gh api repos/owner/repo/contents/path > /tmp/gh_output.json
cat /tmp/gh_output.json

# For commands that need processing
gh repo list --json name,url > /tmp/repos.json
jq -r '.[].name' /tmp/repos.json

# Clean up after use
rm /tmp/gh_output.json
```

### Common Patterns
```bash
# Get repository info
gh api repos/owner/repo > /tmp/repo_info.json

# List issues
gh issue list --repo owner/repo --json number,title,state > /tmp/issues.json

# Get workflow runs
gh run list --repo owner/repo --json id,status,conclusion > /tmp/runs.json

# Read and process the output
cat /tmp/repo_info.json | jq '.name'
```

## Troubleshooting

### Common Issues

#### Python Environment
```bash
# Check Python version
python3 --version

# Recreate virtual environment
rm -rf .venv
make setup
```

#### Docker Build Issues
```bash
# Clean Docker cache
make docker-clean

# Rebuild image
make build
```

#### Kubernetes Connection
```bash
# Set kubeconfig
export KUBECONFIG=k8s/kubeconfig.yaml

# Check connection
kubectl --kubeconfig=k8s/kubeconfig.yaml cluster-info

# Check namespace
kubectl --kubeconfig=k8s/kubeconfig.yaml get namespace petrosa-apps
```

#### NumPy Compatibility
```bash
# Fix NumPy 2.x compatibility issues
pip install 'numpy<2.0.0'

# Reinstall dependencies
pip install -r requirements.txt
```

### Diagnostic Commands
```bash
# Complete diagnostics
./scripts/local-pipeline.sh all

# Specific component checks
./scripts/local-pipeline.sh setup
./scripts/local-pipeline.sh lint
./scripts/local-pipeline.sh build
./scripts/local-pipeline.sh deploy
```

## Quick Reference

### Essential Commands
```bash
# Setup
make setup
make install-dev

# Development
make lint
make test
make format

# Docker
make build
make container
make run-docker

# Kubernetes
export KUBECONFIG=k8s/kubeconfig.yaml
make deploy
make k8s-status
make k8s-logs

# Pipeline
make pipeline
```

### Cluster Management
```bash
# Connect to cluster
export KUBECONFIG=k8s/kubeconfig.yaml

# Check status
kubectl --kubeconfig=k8s/kubeconfig.yaml get all -n petrosa-apps

# View logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -f deployment/{project-name} -n petrosa-apps

# Restart deployment
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/{project-name} -n petrosa-apps
```

## Project-Specific Notes

### TA Bot
- Uses NATS for real-time data
- Implements 5 technical analysis strategies
- Publishes signals via REST API

### Trading Engine
- Uses MongoDB for distributed state
- Implements order management and position tracking
- Provides REST API for trading operations

### Data Extractor
- Extracts data from Binance API
- Supports multiple database adapters
- Runs as CronJobs for scheduled data extraction

## Best Practices

### Code Quality
- Follow PEP 8 for Python formatting
- Use type hints where possible
- Implement proper error handling
- Add comprehensive logging
- Write unit tests for all functions

### Security
- Never commit secrets to version control
- Use existing Kubernetes secrets only
- Run security scans regularly
- Keep dependencies updated

### Deployment
- Always test locally before deploying
- Use VERSION_PLACEHOLDER in manifests
- Monitor deployment health
- Check logs for issues

### Documentation
- Keep README.md updated
- Document environment variables
- Provide troubleshooting guides
- Update this unified guide as needed
