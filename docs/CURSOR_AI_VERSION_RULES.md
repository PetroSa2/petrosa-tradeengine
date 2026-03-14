# CURSOR AI VERSION MANAGEMENT RULES

## 🚨 CRITICAL: NEVER MODIFY VERSION_PLACEHOLDER

### What VERSION_PLACEHOLDER Is
- **Intentional placeholder** in Kubernetes manifests in the `k8s/` directory
- **Automatically replaced** by CI/CD pipeline during deployment
- **Part of the deployment system** - NOT a bug to fix
- **Ensures consistent versioning** across all Petrosa systems

### CI/CD ENFORCEMENT - NEW!
The CI now **automatically validates** that VERSION_PLACEHOLDER exists in your k8s/ manifests before deployment. **If you replace it with a hardcoded version, the CI will FAIL with an error.**

### What Cursor AI Should NEVER Do
❌ Replace VERSION_PLACEHOLDER with specific versions (v1.0.1, latest, etc.)
❌ Suggest "fixing" VERSION_PLACEHOLDER in Kubernetes manifests
❌ Commit manifests with real versions instead of placeholders
❌ Change VERSION_PLACEHOLDER to any other value
❌ "Help" by replacing placeholders with actual versions

### What Cursor AI Should Do
✅ Leave VERSION_PLACEHOLDER exactly as is in all k8s/ files
✅ Understand this is the correct pattern
✅ Only suggest changes to version management scripts if needed
✅ Help with debugging version-related issues without touching placeholders
✅ Point to this document when asked about VERSION_PLACEHOLDER

### Version Management Flow
1. **Development**: Manifests contain VERSION_PLACEHOLDER
2. **CI/CD**: Pipeline replaces VERSION_PLACEHOLDER with actual version
3. **Deployment**: Kubernetes gets versioned images
4. **Rollback**: Easy to revert to previous versions

### If You See VERSION_PLACEHOLDER
- This is CORRECT and INTENTIONAL
- Do NOT "fix" it
- The CI/CD pipeline will handle version replacement
- This ensures consistent deployments across environments

### Version Management Commands
```bash
# Check version status
make version-check

# Show version information
make version-info

# Create new release
./scripts/create-release.sh patch
./scripts/create-release.sh minor
./scripts/create-release.sh major

# Debug version issues
make version-debug
```

### Files That Should Contain VERSION_PLACEHOLDER
- `k8s/deployment.yaml`
- `k8s/service.yaml`
- `k8s/ingress.yaml`
- `k8s/hpa.yaml`
- `k8s/network-policy.yaml`
- `k8s/configmap.yaml`
- `k8s/common-configmap.yaml`
- All CronJob manifests

### If Asked About VERSION_PLACEHOLDER
- Explain it's intentional and part of the deployment system
- Suggest using version management scripts instead
- Never suggest manual replacement
- Point to VERSION_PLACEHOLDER_GUIDE.md for details

## Why This System Exists

### Benefits
- **Consistent versioning** across all Petrosa systems
- **Automated deployments** through CI/CD pipelines
- **No manual version management** in Kubernetes manifests
- **Easy rollbacks** and version tracking
- **Standardized deployment process** across all projects

### How It Works
1. **Git tags** provide the version source (e.g., `v1.0.1`)
2. **CI/CD pipeline** automatically replaces VERSION_PLACEHOLDER
3. **Kubernetes** gets the exact version for deployment
4. **Monitoring** can track which version is running

### Auto-Increment System
- **Patch versions** increment automatically on main branch pushes
- **Minor/Major versions** require manual release creation
- **Local development** uses timestamp-based versions
- **Production** uses semantic versioning

## Troubleshooting

### Common Issues
1. **VERSION_PLACEHOLDER not replaced**: Check CI/CD pipeline
2. **Version mismatch**: Verify git tags and deployment
3. **Manual version committed**: Revert to VERSION_PLACEHOLDER
4. **CI fails with "VERSION_PLACEHOLDER not found"**: You accidentally replaced it with a hardcoded version - revert immediately!

### The Silent Failure Problem (NOW FIXED!)
Previously, if an agent replaced VERSION_PLACEHOLDER with a hardcoded version (e.g., `v1.2.15`):
1. The gitops-update sed command would find nothing to replace
2. No changes would be committed to petrosa_k8s
3. The cluster would keep running the OLD version
4. Nobody would notice until someone checked manually

**Now the CI catches this before deployment!**

### Debugging Commands
```bash
# Check for VERSION_PLACEHOLDER
grep -r "VERSION_PLACEHOLDER" k8s/

# Check for hardcoded versions
grep -r "yurisa2/petrosa.*:v[0-9]" k8s/

# Check deployed version
kubectl --kubeconfig=k8s/kubeconfig.yaml get deployment -n petrosa-apps -o jsonpath='{.spec.template.spec.containers[0].image}'
```

## Summary

**VERSION_PLACEHOLDER is intentional and correct. Never change it.**
The CI/CD pipeline handles version replacement automatically.
This system ensures reliable, consistent deployments across all Petrosa services.
