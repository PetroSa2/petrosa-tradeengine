# Versioning Guide

This document explains how the automatic versioning system works in the Petrosa Trading Engine project.

## Overview

The project uses semantic versioning (SemVer) with automatic tag creation and deployment. Every push to the `main` branch triggers a new version release.

## Version Format

Versions follow the semantic versioning format: `vMAJOR.MINOR.PATCH`

- **MAJOR**: Breaking changes (incompatible API changes)
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

Examples: `v1.0.0`, `v1.2.3`, `v2.0.0`

## Automatic Versioning

### How It Works

1. **Push to main branch**: When you push code to the `main` branch, the CI/CD pipeline automatically:
   - Generates the next patch version (e.g., `v1.0.0` → `v1.0.1`)
   - Creates a Git tag with that version
   - Builds and deploys the Docker image with that version tag

2. **Tag-based deployment**: When you push a specific version tag (e.g., `v2.1.0`), the pipeline:
   - Uses that exact version for the Docker image
   - Deploys without creating a new tag

### Version Generation Logic

The system automatically increments the **patch** version for each deployment from the main branch:

- Current version: `v1.0.0` → Next: `v1.0.1`
- Current version: `v1.0.1` → Next: `v1.0.2`
- Current version: `v2.1.5` → Next: `v2.1.6`

## Manual Version Management

### Using the Release Script

For major or minor version bumps, use the provided script:

```bash
# Increment patch version (automatic)
./scripts/create-release.sh patch

# Increment minor version (manual)
./scripts/create-release.sh minor

# Increment major version (manual)
./scripts/create-release.sh major

# Create specific version
./scripts/create-release.sh v2.1.0
```

### Manual Tag Creation

You can also create tags manually:

```bash
# Create and push a tag
git tag v2.1.0
git push origin v2.1.0
```

## CI/CD Pipeline Flow

### For Main Branch Pushes

1. **create-release** job:
   - Generates next semantic version
   - Creates and pushes Git tag
   - Outputs version to other jobs

2. **build-and-push** job:
   - Uses version from create-release job
   - Builds multi-architecture Docker image
   - Pushes to Docker Hub with version tag

3. **deploy** job:
   - Updates Kubernetes manifests with new image tag
   - Deploys to production cluster

### For Tag Pushes

1. **build-and-push** job:
   - Uses the pushed tag as version
   - Builds and pushes Docker image

2. **deploy** job:
   - Deploys the specific version

## Best Practices

### When to Use Different Version Types

- **Patch (automatic)**: Regular deployments, bug fixes, minor improvements
- **Minor (manual)**: New features, significant improvements
- **Major (manual)**: Breaking changes, major refactoring

### Release Workflow

1. **Regular development**:
   ```bash
   git add .
   git commit -m "Add new feature"
   git push origin main
   # Automatically creates v1.0.1, builds, and deploys
   ```

2. **Feature release**:
   ```bash
   ./scripts/create-release.sh minor
   # Creates v1.1.0, builds, and deploys
   ```

3. **Major release**:
   ```bash
   ./scripts/create-release.sh major
   # Creates v2.0.0, builds, and deploys
   ```

### Version History

Keep track of your versions:

```bash
# List all version tags
git tag --sort=-version:refname

# Show version history
git log --oneline --decorate --tags
```

## Docker Images

Each version creates a Docker image tagged with:

- **Version tag**: `docker.io/petrosa/petrosa-tradeengine:v1.0.1`
- **Latest tag**: `docker.io/petrosa/petrosa-tradeengine:latest`

## Kubernetes Deployment

The deployment automatically updates to use the new image version:

```yaml
image: petrosa/petrosa-tradeengine:v1.0.1
```

## Troubleshooting

### Common Issues

1. **Tag already exists**:
   - The script will warn you if a tag already exists
   - Choose whether to continue or use a different version

2. **Uncommitted changes**:
   - The script requires a clean working directory
   - Commit or stash changes before creating a release

3. **Permission issues**:
   - Ensure you have write access to the repository
   - Check GitHub Actions permissions

### Checking Current Version

```bash
# Get the latest version tag
git tag --sort=-version:refname | grep '^v[0-9]' | head -1

# Check what version is deployed
kubectl get deployment -n petrosa-apps -o jsonpath='{.items[*].spec.template.spec.containers[*].image}'
```

## Migration from Old System

If you have existing non-semantic version tags (like `v1`, `v2`), the system will:

1. Start with `v1.0.0` if no semantic version tags exist
2. Convert to semantic versioning for future releases
3. Maintain backward compatibility

## Security Considerations

- Version tags are immutable once created
- Each version creates a unique Docker image
- Rollback is possible by deploying a previous version tag
- All versions are tracked in Git history

## Monitoring

Monitor your deployments:

```bash
# Check deployment status
kubectl get all -l app=petrosa-tradeengine -n petrosa-apps

# View logs for specific version
kubectl logs -l app=petrosa-tradeengine --tail=100 -n petrosa-apps

# Check deployment status
kubectl get deployment -n petrosa-apps
```

## MySQL Integration

The versioning system works seamlessly with the MySQL audit logging:

- Each version includes proper MySQL connection handling
- Audit logs are versioned with the application version
- Database schema changes are tracked with version tags

## Non-Blocking Operations

The versioning system ensures that:

- Audit logging doesn't block core trading operations
- Database failures don't prevent application startup
- Version deployment continues even if audit logging fails
- Retries and backoff are implemented for database operations
