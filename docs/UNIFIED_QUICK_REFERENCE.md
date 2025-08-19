# Unified Quick Reference - Petrosa Systems

## 🚀 Essential Commands

### Cluster Connection
```bash
# Use repository's kubeconfig
export KUBECONFIG=k8s/kubeconfig.yaml

# Or use explicit flag
kubectl --kubeconfig=k8s/kubeconfig.yaml cluster-info

# Verify connection
kubectl --kubeconfig=k8s/kubeconfig.yaml get nodes
```

### Development Setup
```bash
# All projects use the same setup pattern
make setup
make install-dev
```

### Local Development
```bash
# Code quality
make lint
make format
make test

# Docker operations
make build
make container
make run-docker

# Complete pipeline
make pipeline
```

### Kubernetes Deployment
```bash
# Deploy to remote cluster
export KUBECONFIG=k8s/kubeconfig.yaml
make deploy

# Check status
make k8s-status
make k8s-logs

# Clean up
make k8s-clean
```

## 🔍 Debugging Commands

### Check System Status
```bash
# Overall status
kubectl --kubeconfig=k8s/kubeconfig.yaml get all -n petrosa-apps

# Project-specific deployments
kubectl --kubeconfig=k8s/kubeconfig.yaml get deployments -n petrosa-apps

# Pod logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -f deployment/{project-name} -n petrosa-apps

# Recent logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -l app=petrosa-{project-name} -n petrosa-apps --since=1h
```

### Common Issues

#### MicroK8s Connection Issues
```bash
# Check cluster connection
kubectl --kubeconfig=k8s/kubeconfig.yaml cluster-info

# Test connection with kubeconfig
kubectl --kubeconfig=k8s/kubeconfig.yaml get nodes
```

#### Certificate Issues
```bash
# Use insecure flag
kubectl --kubeconfig=k8s/kubeconfig.yaml --insecure-skip-tls-verify get nodes

# Or set environment variable
export KUBECONFIG=k8s/kubeconfig.yaml
kubectl --insecure-skip-tls-verify get nodes
```

#### Port Forwarding Issues
```bash
# Check if port is in use
netstat -an | grep 4222

# Kill existing port forwards
pkill -f "kubectl port-forward"

# Restart port forward with kubeconfig
kubectl --kubeconfig=k8s/kubeconfig.yaml port-forward -n nats svc/nats-server 4222:4222 &
```

## 🧪 Testing

### Run Tests
```bash
# All tests
make test

# With coverage
python -m pytest tests/ -v --cov=. --cov-report=term

# Specific test
python -m pytest tests/test_specific.py -v
```

### Pipeline Testing
```bash
# Complete pipeline
make pipeline

# Specific stages
./scripts/local-pipeline.sh lint test
./scripts/local-pipeline.sh build container
./scripts/local-pipeline.sh deploy
```

## 📊 Monitoring

### View Logs
```bash
# Application logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -f deployment/{project-name} -n petrosa-apps

# Recent logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -l app=petrosa-{project-name} -n petrosa-apps --since=1h

# Job logs (for data extractor)
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -f job/<job-name> -n petrosa-apps
```

### Resource Monitoring
```bash
# Resource usage
kubectl --kubeconfig=k8s/kubeconfig.yaml top pods -n petrosa-apps

# Events
kubectl --kubeconfig=k8s/kubeconfig.yaml get events -n petrosa-apps --sort-by=.metadata.creationTimestamp

# Pod details
kubectl --kubeconfig=k8s/kubeconfig.yaml describe pod <pod-name> -n petrosa-apps
```

## 🔧 Environment Setup

### Prerequisites Checklist
- [ ] Python 3.11+ installed
- [ ] Docker running
- [ ] kubectl installed
- [ ] Virtual environment activated
- [ ] Environment variables set

### Environment Variables
```bash
# Common (all projects)
ENVIRONMENT=production
LOG_LEVEL=INFO

# TA Bot
NATS_URL=nats://nats-server:4222
API_ENDPOINT=https://api.example.com/signals

# Trading Engine
MONGODB_URL=mongodb://mongodb:27017/trading
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
JWT_SECRET_KEY=your_jwt_secret

# Data Extractor
DB_ADAPTER=mysql
MYSQL_URI=mysql+pymysql://username:password@localhost:3306/binance_data
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
```

## 🚨 Emergency Procedures

### Cluster Issues
1. Check cluster connection: `kubectl --kubeconfig=k8s/kubeconfig.yaml cluster-info`
2. Verify namespace: `kubectl --kubeconfig=k8s/kubeconfig.yaml get namespace petrosa-apps`
3. Check all resources: `kubectl --kubeconfig=k8s/kubeconfig.yaml get all -n petrosa-apps`

### Application Issues
1. Check pod logs: `kubectl --kubeconfig=k8s/kubeconfig.yaml logs -f deployment/{project-name} -n petrosa-apps`
2. Check pod status: `kubectl --kubeconfig=k8s/kubeconfig.yaml describe pod <pod-name> -n petrosa-apps`
3. Restart deployment: `kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/{project-name} -n petrosa-apps`

### Database Issues
1. Test connection from pod
2. Verify credentials and connection string
3. Check database service status

## 📚 Useful Scripts

### All Projects
- `make setup` - Environment setup
- `make test` - Run tests
- `make lint` - Code quality checks
- `make build` - Build Docker image
- `make deploy` - Deploy to Kubernetes
- `make pipeline` - Complete CI/CD pipeline

### Project-Specific
- `./scripts/local-pipeline.sh` - Local pipeline execution
- `./scripts/dev-setup.sh` - Development environment setup
- `./scripts/troubleshoot.sh` - Troubleshooting utilities

## 🔗 Quick Links

### Documentation
- [Unified Guide](UNIFIED_GUIDE.md)
- [Project README](../README.md)
- [Kubernetes Guide](KUBERNETES.md)
- [CI/CD Guide](CI_CD.md)

### GitHub CLI
```bash
# Get repository info
gh api repos/owner/repo > /tmp/repo_info.json

# List issues
gh issue list --repo owner/repo --json number,title,state > /tmp/issues.json

# Get workflow runs
gh run list --repo owner/repo --json id,status,conclusion > /tmp/runs.json

# Read output
cat /tmp/repo_info.json | jq '.name'
```

## ⚠️ Critical Rules

### Kubernetes
- **ALWAYS use `--kubeconfig=k8s/kubeconfig.yaml`** with kubectl commands
- **NEVER create new secrets/configmaps** - use existing ones only
- **NEVER replace VERSION_PLACEHOLDER** - it's part of the deployment system
- **Use `petrosa-sensitive-credentials`** for all credentials
- **Use `petrosa-common-config`** for shared configuration

### GitHub CLI
- **ALWAYS dump output to `/tmp` files** and read from them
- **Example**: `gh command > /tmp/file.json && cat /tmp/file.json`

### CI/CD Pipeline
- **Continue until GitHub Actions pipeline is fully green**
- **Run all tests locally before pushing**
- **Fix all linting errors before committing**

## 🎯 Project-Specific Quick Commands

### TA Bot
```bash
# Run TA strategies locally
python -m ta_bot.main

# Test signal generation
python -m pytest tests/test_signal_engine.py -v
```

### Trading Engine
```bash
# Test trading operations
python -m pytest tests/test_api.py -v

# Check MongoDB connection
python scripts/check-mongodb.py
```

### Data Extractor
```bash
# Test data extraction
python -m pytest tests/test_extract_klines.py -v

# Run extraction job
python jobs/extract_klines.py
```

## 📋 Common Workflows

### New Feature Development
1. `make setup` - Setup environment
2. `make lint` - Check code quality
3. `make test` - Run tests
4. `make build` - Build Docker image
5. `make deploy` - Deploy to cluster
6. `make k8s-status` - Check deployment

### Bug Fix
1. `make k8s-logs` - Check application logs
2. `make test` - Run tests to reproduce
3. Fix the issue
4. `make lint` - Check code quality
5. `make test` - Verify fix
6. `make deploy` - Deploy fix

### Pipeline Fix
1. `make test` - Run all tests locally
2. `make lint` - Fix linting errors
3. `make security` - Run security scan
4. `make build` - Build and test container
5. Commit and push changes
6. Monitor GitHub Actions pipeline
7. **Continue until pipeline is green**
