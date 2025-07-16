# Petrosa Trading Engine - Production Deployment Guide

This document describes the production-ready CI/CD pipeline and Kubernetes deployment for the Petrosa Trading Engine.

## Architecture Overview

The deployment consists of:
- **GitHub Actions CI/CD Pipeline**: Automated testing, building, and deployment
- **Docker Multi-architecture Images**: Support for AMD64 and ARM64
- **Kubernetes Deployment**: Production-ready with autoscaling, health checks, and ingress
- **Security**: Vulnerability scanning, secrets management, and secure configurations

## CI/CD Pipeline

### Pipeline Stages

1. **Lint & Test**
   - Python 3.11 setup
   - Install production and development dependencies
   - Run flake8, black, ruff, and mypy
   - Execute pytest with coverage reporting
   - Upload coverage to Codecov

2. **Security Scan**
   - Trivy vulnerability scanner for code and dependencies
   - Upload results to GitHub Security tab

3. **Build & Push**
   - Multi-architecture Docker builds (linux/amd64, linux/arm64)
   - Automated versioning with semantic tags
   - Push to Docker Hub with version and latest tags
   - Build cache optimization

4. **Deploy**
   - Deploy to MicroK8s cluster
   - Update image tags in Kubernetes manifests
   - Create/verify secrets and ConfigMaps
   - Apply all Kubernetes resources
   - Verify deployment status

### Required GitHub Secrets

Configure these secrets in your GitHub repository:

```bash
# Docker Hub credentials
DOCKERHUB_USERNAME=your-dockerhub-username
DOCKERHUB_TOKEN=your-dockerhub-token

# Kubernetes cluster access
KUBE_CONFIG=base64-encoded-kubeconfig

# Application secrets
MONGODB_URL=mongodb://your-mongodb-url
BINANCE_API_KEY=your-binance-api-key
BINANCE_API_SECRET=your-binance-api-secret
JWT_SECRET_KEY=your-jwt-secret-key
```

## Kubernetes Deployment

### Namespace
- **Namespace**: `petrosa-apps`
- **Labels**: `app=petrosa-tradeengine`

### Components

#### 1. Deployment (`k8s/deployment.yaml`)
- **Replicas**: 3 (minimum)
- **Resources**: 256Mi-512Mi memory, 250m-500m CPU
- **Health Checks**:
  - Liveness: `/health` (30s interval)
  - Readiness: `/ready` (5s interval)
  - Startup: `/live` (10s interval)
- **Environment Variables**: From ConfigMap and Secrets

#### 2. Service (`k8s/service.yaml`)
- **Type**: ClusterIP
- **Port**: 80 → 8000
- **Protocol**: TCP

#### 3. Ingress (`k8s/ingress.yaml`)
- **Host**: `api.petrosa.com`
- **SSL**: Automatic with Let's Encrypt
- **Annotations**: Nginx ingress with SSL redirect

#### 4. Horizontal Pod Autoscaler (`k8s/hpa.yaml`)
- **Min Replicas**: 3
- **Max Replicas**: 10
- **CPU Target**: 80% utilization
- **Memory Target**: 80% utilization
- **Scale Down**: 5-minute stabilization
- **Scale Up**: 1-minute stabilization

#### 5. ConfigMap (`k8s/configmap.yaml`)
- Environment configuration
- Log levels
- Feature flags
- API settings

#### 6. Secrets (`k8s/secrets.yaml`)
- Database credentials
- API keys
- JWT secrets
- NATS credentials

## Docker Configuration

### Multi-stage Dockerfile
- **Base Image**: Python 3.11-slim
- **Security**: Non-root user (appuser)
- **Optimization**: Layer caching, minimal dependencies
- **Health Check**: HTTP endpoint verification

### Build Optimization
- **Cache**: GitHub Actions cache for pip dependencies
- **Multi-arch**: AMD64 and ARM64 support
- **Security**: Vulnerability scanning with Trivy

## Health Check Endpoints

The FastAPI application provides three health check endpoints:

### `/health`
- **Purpose**: Detailed health status
- **Response**: Service status, timestamp, environment
- **Use**: General health monitoring

### `/ready`
- **Purpose**: Readiness probe for Kubernetes
- **Response**: Service readiness status
- **Use**: Determines if pod can receive traffic

### `/live`
- **Purpose**: Liveness probe for Kubernetes
- **Response**: Service liveness status
- **Use**: Determines if pod should be restarted

## Deployment Commands

### Manual Deployment

```bash
# Create namespace
kubectl create namespace petrosa-apps

# Create secrets
kubectl create secret generic petrosa-secrets \
  --namespace=petrosa-apps \
  --from-literal=mongodb-url="your-mongodb-url" \
  --from-literal=binance-api-key="your-api-key" \
  --from-literal=binance-api-secret="your-api-secret" \
  --from-literal=jwt-secret-key="your-jwt-secret"

# Create ConfigMap
kubectl create configmap petrosa-config \
  --namespace=petrosa-apps \
  --from-literal=environment=production \
  --from-literal=log-level=INFO \
  --from-literal=simulation-enabled=false

# Apply Kubernetes manifests
kubectl apply -f k8s/ -n petrosa-apps

# Verify deployment
kubectl get pods -n petrosa-apps
kubectl get svc -n petrosa-apps
kubectl get ingress -n petrosa-apps
```

### Monitoring

```bash
# Check pod status
kubectl get pods -n petrosa-apps -l app=petrosa-tradeengine

# View logs
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine

# Check HPA status
kubectl get hpa -n petrosa-apps

# Monitor resource usage
kubectl top pods -n petrosa-apps
```

## Environment Variables

### Required Environment Variables
- `MONGODB_URL`: MongoDB connection string
- `BINANCE_API_KEY`: Binance API key
- `BINANCE_API_SECRET`: Binance API secret
- `JWT_SECRET_KEY`: JWT signing secret

### Optional Environment Variables
- `ENVIRONMENT`: Environment name (default: production)
- `LOG_LEVEL`: Logging level (default: INFO)
- `SIMULATION_ENABLED`: Enable simulation mode (default: false)
- `BINANCE_TESTNET`: Use Binance testnet (default: false)

## Security Considerations

### Secrets Management
- All sensitive data stored in Kubernetes secrets
- Base64 encoded values in secrets template
- Production secrets should use external secret management

### Network Security
- Ingress with SSL/TLS termination
- Internal service communication via ClusterIP
- Network policies for pod-to-pod communication

### Container Security
- Non-root user execution
- Minimal base image (Python slim)
- Regular vulnerability scanning
- Resource limits and requests

## Troubleshooting

### Common Issues

1. **Pod Startup Failures**
   ```bash
   kubectl describe pod -n petrosa-apps <pod-name>
   kubectl logs -n petrosa-apps <pod-name>
   ```

2. **Health Check Failures**
   ```bash
   # Test health endpoints
   curl http://localhost:8000/health
   curl http://localhost:8000/ready
   curl http://localhost:8000/live
   ```

3. **Resource Issues**
   ```bash
   kubectl top pods -n petrosa-apps
   kubectl describe hpa -n petrosa-apps
   ```

4. **Ingress Issues**
   ```bash
   kubectl get ingress -n petrosa-apps
   kubectl describe ingress -n petrosa-apps
   ```

### Log Analysis

```bash
# View application logs
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine

# Follow logs in real-time
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine -f

# View logs from specific pod
kubectl logs -n petrosa-apps <pod-name>
```

## Scaling

### Manual Scaling
```bash
# Scale deployment
kubectl scale deployment petrosa-tradeengine -n petrosa-apps --replicas=5

# Update HPA
kubectl patch hpa petrosa-tradeengine-hpa -n petrosa-apps -p '{"spec":{"maxReplicas":15}}'
```

### Auto-scaling
The HPA automatically scales based on:
- CPU utilization (target: 80%)
- Memory utilization (target: 80%)
- Min replicas: 3
- Max replicas: 10

## Backup and Recovery

### Configuration Backup
```bash
# Export current configuration
kubectl get configmap petrosa-config -n petrosa-apps -o yaml > config-backup.yaml
kubectl get secret petrosa-secrets -n petrosa-apps -o yaml > secrets-backup.yaml
```

### Application Data
- MongoDB data should be backed up separately
- Trading data stored in external databases
- Logs can be collected via log aggregation

## Performance Optimization

### Resource Tuning
- Monitor resource usage with `kubectl top`
- Adjust CPU/memory requests and limits
- Optimize HPA thresholds based on load patterns

### Caching
- Enable Redis for session caching
- Use CDN for static assets
- Implement application-level caching

### Database Optimization
- Use connection pooling
- Implement read replicas
- Optimize database queries

## Support

For deployment issues:
1. Check the GitHub Actions logs
2. Review Kubernetes events and logs
3. Verify configuration and secrets
4. Test health endpoints manually
5. Contact the development team

## Version History

- **v1.0.0**: Initial production deployment
- **v1.1.0**: Added HPA and improved health checks
- **v1.2.0**: Enhanced security and monitoring
