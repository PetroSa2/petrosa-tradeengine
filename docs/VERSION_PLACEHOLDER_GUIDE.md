# VERSION_PLACEHOLDER System Guide

## Overview
All Petrosa systems use a unified version management system with `VERSION_PLACEHOLDER` in Kubernetes manifests. This system ensures consistent versioning across deployments and enables automated CI/CD pipelines.

## How It Works

### 1. Kubernetes Manifests
All Kubernetes manifests use `VERSION_PLACEHOLDER` instead of hardcoded versions:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: petrosa-ta-bot
spec:
  template:
    spec:
      containers:
      - name: ta-bot
        image: yurisa2/petrosa-ta-bot:VERSION_PLACEHOLDER  # ← This gets replaced
```

### 2. CI/CD Pipeline Replacement
During deployment, the CI/CD pipeline automatically replaces `VERSION_PLACEHOLDER` with the actual version:

```bash
# In GitHub Actions workflow
find k8s/ -name "*.yaml" -o -name "*.yml" | xargs sed -i "s|VERSION_PLACEHOLDER|${IMAGE_TAG}|g"
```

### 3. Version Sources
The version comes from:
- **Git tags** (e.g., `v1.0.1`)
- **GitHub release** version
- **Build number** from CI/CD pipeline

## Critical Rules

### ✅ DO
- Use `VERSION_PLACEHOLDER` in all Kubernetes manifests
- Let the CI/CD pipeline handle version replacement
- Use semantic versioning for releases (e.g., `v1.0.1`)

### ❌ DON'T
- **NEVER manually replace VERSION_PLACEHOLDER** in manifests
- **NEVER hardcode versions** in Kubernetes files
- **NEVER commit manifests with actual versions** instead of placeholders

## Files That Use VERSION_PLACEHOLDER

### Common Files
- `k8s/deployment.yaml`
- `k8s/service.yaml`
- `k8s/ingress.yaml`
- `k8s/hpa.yaml`
- `k8s/network-policy.yaml`

### Project-Specific Files
- **TA Bot**: All standard manifests
- **Trading Engine**: All standard manifests
- **Data Extractor**:
  - `k8s/klines-all-timeframes-cronjobs.yaml`
  - `k8s/klines-gap-filler-cronjob.yaml`
  - `k8s/klines-mongodb-production.yaml`

## Version Management Process

### 1. Development
```bash
# During development, manifests contain VERSION_PLACEHOLDER
image: yurisa2/petrosa-ta-bot:VERSION_PLACEHOLDER
```

### 2. Release Process
```bash
# 1. Create a new release
git tag v1.0.1
git push origin v1.0.1

# 2. CI/CD pipeline automatically:
#    - Builds Docker image with tag v1.0.1
#    - Replaces VERSION_PLACEHOLDER with v1.0.1
#    - Deploys to Kubernetes
```

### 3. Deployment
```bash
# After CI/CD processing, manifests contain actual version
image: yurisa2/petrosa-ta-bot:v1.0.1
```

## CI/CD Integration

### GitHub Actions Workflow
```yaml
# Example from .github/workflows/ci.yml
- name: Replace version placeholder
  run: |
    find k8s/ -name "*.yaml" -o -name "*.yml" | xargs sed -i "s|VERSION_PLACEHOLDER|${IMAGE_TAG}|g"

- name: Verify no placeholders remain
  run: |
    PLACEHOLDER_COUNT=$(grep -r "VERSION_PLACEHOLDER" k8s/ | wc -l || echo "0")
    if [ "$PLACEHOLDER_COUNT" -gt 0 ]; then
      echo "❌ Found $PLACEHOLDER_COUNT VERSION_PLACEHOLDER references"
      grep -r "VERSION_PLACEHOLDER" k8s/
      exit 1
    fi
```

### Local Development
```bash
# For local testing, you can manually replace (but don't commit)
sed -i "s|VERSION_PLACEHOLDER|local-test|g" k8s/deployment.yaml

# Revert after testing
git checkout k8s/deployment.yaml
```

## Troubleshooting

### Common Issues

#### 1. Placeholder Not Replaced
```bash
# Check if CI/CD pipeline is working
grep -r "VERSION_PLACEHOLDER" k8s/

# Expected: No output (all placeholders should be replaced)
# If found: CI/CD pipeline may have failed
```

#### 2. Version Mismatch
```bash
# Check deployed version
kubectl --kubeconfig=k8s/kubeconfig.yaml get deployment petrosa-ta-bot -n petrosa-apps -o jsonpath='{.spec.template.spec.containers[0].image}'

# Should show: yurisa2/petrosa-ta-bot:v1.0.1 (not VERSION_PLACEHOLDER)
```

#### 3. Manual Version Committed
```bash
# If someone accidentally committed a real version
git checkout HEAD -- k8s/deployment.yaml
# This restores VERSION_PLACEHOLDER
```

## Best Practices

### 1. Always Use Placeholders
```yaml
# ✅ Correct
image: yurisa2/petrosa-ta-bot:VERSION_PLACEHOLDER

# ❌ Wrong
image: yurisa2/petrosa-ta-bot:v1.0.1
```

### 2. Verify Before Committing
```bash
# Check for hardcoded versions
grep -r "yurisa2/petrosa.*:v" k8s/

# Should return no results
```

### 3. Use Semantic Versioning
```bash
# ✅ Good version tags
v1.0.0
v1.0.1
v1.1.0
v2.0.0

# ❌ Avoid
v1.0
1.0.1
latest
```

### 4. Test Version Replacement
```bash
# Test the replacement locally
IMAGE_TAG=v1.0.1-test
find k8s/ -name "*.yaml" -exec sed -i "s|VERSION_PLACEHOLDER|${IMAGE_TAG}|g" {} \;

# Verify replacement
grep -r "v1.0.1-test" k8s/

# Revert
git checkout k8s/
```

## Project-Specific Notes

### TA Bot
- Uses standard deployment pattern
- Version affects signal generation service

### Trading Engine
- Uses standard deployment pattern
- Version affects trading operations

### Data Extractor
- Uses CronJobs with version placeholders
- Multiple manifests need version replacement
- Version affects data extraction jobs

## Migration Guide

### If You Have Hardcoded Versions
```bash
# 1. Replace hardcoded versions with placeholders
find k8s/ -name "*.yaml" -exec sed -i "s|yurisa2/petrosa.*:v[0-9.]*|yurisa2/petrosa-ta-bot:VERSION_PLACEHOLDER|g" {} \;

# 2. Verify changes
grep -r "VERSION_PLACEHOLDER" k8s/

# 3. Commit changes
git add k8s/
git commit -m "Replace hardcoded versions with VERSION_PLACEHOLDER"
```

### If You Need to Update Version System
1. Update CI/CD pipeline scripts
2. Update version replacement logic
3. Test with a new release
4. Document changes

## Summary

The `VERSION_PLACEHOLDER` system ensures:
- **Consistent versioning** across all Petrosa systems
- **Automated deployments** through CI/CD pipelines
- **No manual version management** in Kubernetes manifests
- **Easy rollbacks** and version tracking
- **Standardized deployment process** across all projects

**Remember**: Never manually replace `VERSION_PLACEHOLDER` - let the CI/CD pipeline handle it automatically!
