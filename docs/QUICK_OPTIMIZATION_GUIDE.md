# Quick Guide: Apply CI/CD Optimizations to Other Petrosa Projects

This guide provides a checklist for applying the same build time optimizations to other Petrosa projects.

## Projects to Optimize

- [ ] petrosa-binance-data-extractor
- [ ] petrosa-socket-client
- [ ] petrosa-bot-ta-analysis
- [ ] petrosa-realtime-strategies

## Step-by-Step Process

### Step 1: Update GitHub Actions Workflow

**File**: `.github/workflows/ci-cd.yml` (or similar)

1. Find the cache step (usually around line 63-69)
2. Replace with enhanced caching:

```yaml
- name: Cache pip packages and virtual environment
  uses: actions/cache@v3
  with:
    path: |
      ~/.cache/pip
      .venv
    key: ${{ runner.os }}-python-3.11-venv-${{ hashFiles('**/requirements*.txt') }}
    restore-keys: |
      ${{ runner.os }}-python-3.11-venv-
      ${{ runner.os }}-python-3.11-
```

3. Update the "Install dependencies" step:

```yaml
- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    python -m venv .venv || echo "Virtual environment already exists"
    source .venv/bin/activate
    pip install -r requirements.txt
    pip install -r requirements-dev.txt
```

4. Update ALL subsequent steps that run Python commands to activate venv:

```yaml
- name: Run flake8 linting
  run: |
    source .venv/bin/activate
    flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

**Tip**: Use find/replace to add `source .venv/bin/activate` to each step

### Step 2: Optimize Dockerfile

**File**: `Dockerfile`

1. Add BuildKit syntax directive at the very top:

```dockerfile
# syntax=docker/dockerfile:1.4
# Enable BuildKit for advanced caching features
```

2. Remove `PIP_NO_CACHE_DIR=1` from ENV variables:

**Before**:
```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

**After**:
```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

3. Update pip install commands to use cache mounts:

**Before**:
```dockerfile
RUN pip install --no-cache-dir -r requirements.txt
```

**After**:
```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

4. Apply to ALL pip install commands in the Dockerfile

### Step 3: Verify BuildKit is Enabled

Check that the build-and-push job uses:

```yaml
- name: Set up Docker Buildx
  uses: docker/setup-buildx-action@v3

- name: Build and push Docker image
  uses: docker/build-push-action@v5
  with:
    context: .
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

## Testing the Changes

### Local Testing

1. **Test Docker build locally**:
```bash
export DOCKER_BUILDKIT=1
docker build -t test-image .
# Second build should be much faster
docker build -t test-image .
```

2. **Test virtual environment caching**:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Should be fast - packages are cached
```

### CI/CD Testing

1. Create a test branch
2. Make a small change and push
3. Monitor GitHub Actions workflow
4. Check cache logs:
   - First run: "Cache not found" (expected)
   - Second run: "Cache restored from key" (success!)

## Verification Checklist

- [ ] Dockerfile has BuildKit syntax directive
- [ ] Dockerfile removed PIP_NO_CACHE_DIR=1
- [ ] All pip install commands use --mount=type=cache
- [ ] GitHub Actions caches .venv directory
- [ ] GitHub Actions caches ~/.cache/pip
- [ ] All Python steps activate virtual environment
- [ ] docker/setup-buildx-action is used
- [ ] Build uses cache-from and cache-to
- [ ] First build completes successfully
- [ ] Second build shows cache hits
- [ ] Build time reduced by 50-70%

## Expected Results

| Project | Before | After (Cache Hit) | Improvement |
|---------|--------|-------------------|-------------|
| tradeengine | 25-35 min | 10-16 min | ~60% |
| binance-data-extractor | TBD | TBD | ~60% expected |
| socket-client | TBD | TBD | ~60% expected |
| bot-ta-analysis | TBD | TBD | ~60% expected |
| realtime-strategies | TBD | TBD | ~60% expected |

## Common Issues

### Issue: Cache not working
**Solution**: Verify cache key matches requirements file hash

### Issue: Docker build not using cache
**Solution**: Ensure DOCKER_BUILDKIT=1 and syntax directive is present

### Issue: Virtual environment not found
**Solution**: Check .gitignore doesn't exclude .venv, verify cache path

### Issue: Steps fail with "command not found"
**Solution**: Ensure all steps activate venv: `source .venv/bin/activate`

## Additional Optimization Ideas

1. **Split requirements into layers**:
   - Create `requirements-base.txt` for rarely changing deps
   - Keep frequently updated deps in main `requirements.txt`

2. **Use pip-compile for deterministic builds**:
   ```bash
   pip install pip-tools
   pip-compile requirements.in
   ```

3. **Consider using Python slim images**:
   - `python:3.11-slim` vs `python:3.11-alpine`
   - Slim is faster to pull but larger

4. **Enable parallel testing**:
   ```bash
   pytest -n auto  # uses pytest-xdist
   ```

## Next Steps

After optimizing all projects:
1. Monitor build times across all projects
2. Document improvements in each README
3. Update team documentation
4. Consider infrastructure-level caching (Docker registry cache)
