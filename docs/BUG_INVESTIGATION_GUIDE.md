# Bug Investigation and Squashing Guide

## Overview
This guide provides a systematic approach for investigating and fixing bugs across all Petrosa services. Follow this procedure whenever a bug is reported or detected.

## Quick Reference
```bash
# 1. Confirm the bug
make setup && make test
./scripts/local-pipeline.sh all

# 2. Investigate systematically
# - Read error logs and stack traces
# - Examine relevant files
# - Form hypotheses

# 3. Make targeted changes
# - Be specific and surgical
# - Document your reasoning

# 4. Test comprehensively
make test && make lint
./scripts/local-pipeline.sh all
make run-docker  # if applicable
```

## Phase 1: Confirmation and Reproduction

### 1.1 Confirm Bug Behavior Locally
Always start by confirming the bug exists in your local environment:

```bash
# Clean environment setup
make setup

# Run basic tests
make test

# Run complete pipeline
./scripts/local-pipeline.sh all

# If Docker is involved
make run-docker
make container
```

### 1.2 Verify Environment
Check that the bug exists in the expected environment:

- **Local Python environment** (`.venv`)
- **Docker container** (if applicable)
- **Kubernetes deployment** (if applicable): `make k8s-status`

### 1.3 Document Reproduction Steps
Write down the exact steps to reproduce the issue:
- What command was run?
- What was the expected behavior?
- What was the actual behavior?
- What error messages appeared?

## Phase 2: Investigation and Hypothesis Formation

### 2.1 Systematic File Analysis
Read and analyze relevant files in this order:

1. **Error logs and stack traces** - Start here
2. **Specific module/function** mentioned in the error
3. **Configuration files** (`config.py`, `env.example`)
4. **Dependencies** (`requirements.txt`)
5. **Test files** for expected behavior
6. **Related modules** that interact with the failing code

### 2.2 Form Multiple Hypotheses
Consider these common root causes:

- **Configuration issues**: Environment variables, settings, paths
- **Dependency conflicts**: Version mismatches, missing packages
- **Logic errors**: Incorrect algorithms, edge cases
- **Data flow issues**: State management, data corruption
- **Timing issues**: Race conditions, async problems
- **Error handling**: Missing exception handling, improper cleanup

### 2.3 Create Specific Conjectures
Document your reasoning with specific predictions:

```
Hypothesis: The error is caused by missing environment variable X
Prediction: If I set X=value, the error should disappear

Hypothesis: There's a dependency conflict between packages A and B
Prediction: If I update package A to version Y, the conflict should resolve

Hypothesis: The function is not handling null input properly
Prediction: If I add a null check, the error should be prevented
```

## Phase 3: Targeted Changes and Implementation

### 3.1 Make Surgical Changes
- **Change only what's necessary** to fix the issue
- **Use precise line-by-line edits** with clear context
- **Include proper error handling** and logging
- **Follow existing code patterns** and style
- **Add comments** explaining the fix if not obvious

### 3.2 Be Explicit About Changes
Always document what you're changing and why:

```
"I'm adding a null check on line 45 to prevent AttributeError when data is None"
"I'm updating the configuration to use the correct database URL format"
"I'm fixing the import order to resolve the circular dependency"
"I'm adding proper exception handling for the edge case where the API returns empty data"
```

### 3.3 Code Quality Standards
- Follow PEP 8 style guidelines
- Add type hints where appropriate
- Include proper docstrings
- Use meaningful variable names
- Add logging for debugging

## Phase 4: Comprehensive Testing and Validation

### 4.1 Local Testing
```bash
# Run unit tests
make test

# Run linting
make lint

# Run complete pipeline
./scripts/local-pipeline.sh all

# Test specific functionality
python -m pytest tests/test_specific_module.py -v
```

### 4.2 Docker Testing (if applicable)
```bash
# Rebuild image
make build

# Test in container
make run-docker

# Container-specific tests
make container
```

### 4.3 Validation Checklist
- [ ] Original error no longer occurs
- [ ] No new errors are introduced
- [ ] Related functionality still works
- [ ] Performance is not degraded
- [ ] Fix doesn't break other services
- [ ] All tests pass
- [ ] Code quality checks pass

## Phase 5: Documentation and Prevention

### 5.1 Document the Fix
- Add comments explaining the root cause
- Update relevant documentation
- Consider adding regression tests
- Update troubleshooting guides

### 5.2 Preventive Measures
- Add input validation to prevent similar issues
- Improve error handling for edge cases
- Add monitoring or logging for early detection
- Consider if the fix should be applied to other services

## Service-Specific Testing Commands

### TA Bot (petrosa-bot-ta-analysis)
```bash
# Basic validation
make setup && make test

# Full pipeline
./scripts/local-pipeline.sh all

# Docker testing
make run-docker

# Kubernetes status
make k8s-status
```

### Trading Engine (petrosa-tradeengine)
```bash
# Basic validation
make setup && make test

# Full pipeline
./scripts/local-pipeline.sh all

# Docker testing
make run-docker

# Kubernetes status
make k8s-status

# API testing
./scripts/test-api-endpoint-flow.py
```

### Data Extractor (petrosa-binance-data-extractor)
```bash
# Basic validation
make setup && make test

# Full pipeline
./scripts/local-pipeline.sh all

# Docker testing
make run-docker

# Kubernetes status
make k8s-status

# Local deployment test
./scripts/deploy-local.sh
```

## Common Bug Patterns and Solutions

### Configuration Issues
**Symptoms**: Environment variable errors, connection failures
**Solutions**:
- Check `env.example` for required variables
- Verify environment setup in `config.py`
- Ensure proper secret management in Kubernetes

### Dependency Conflicts
**Symptoms**: Import errors, version conflicts
**Solutions**:
- Check `requirements.txt` for version conflicts
- Use `pip list` to see installed versions
- Consider using `pip-tools` for dependency management

### Database Connection Issues
**Symptoms**: Connection timeouts, authentication failures
**Solutions**:
- Verify database credentials in secrets
- Check network connectivity
- Validate connection string format

### Kubernetes Deployment Issues
**Symptoms**: Pod crashes, service unavailability
**Solutions**:
- Check pod logs: `kubectl logs <pod-name>`
- Verify resource limits and requests
- Check service and ingress configuration

## Critical Reminders

1. **Always test locally** before pushing changes
2. **Use existing scripts** and Makefile commands
3. **Follow the systematic approach**: Confirm → Investigate → Fix → Test
4. **Be specific about changes** and document reasoning
5. **Test in multiple environments** (local, Docker, K8s)
6. **Ensure no regressions** are introduced
7. **Stop only when all tests pass** and the fix is validated

## Emergency Procedures

### If the fix breaks other functionality:
1. Revert the changes immediately
2. Document what went wrong
3. Form a new hypothesis
4. Make smaller, more targeted changes
5. Test incrementally

### If the bug is critical (production down):
1. Implement a quick workaround first
2. Deploy the workaround to restore service
3. Then investigate the root cause properly
4. Implement a proper fix
5. Remove the workaround

### If the bug affects multiple services:
1. Identify the common root cause
2. Create a shared fix or library update
3. Apply the fix to all affected services
4. Test all services thoroughly
5. Deploy in a coordinated manner

## Tools and Resources

### Debugging Tools
- `pdb` or `ipdb` for Python debugging
- `logging` for structured logging
- `kubectl logs` for Kubernetes debugging
- `docker logs` for container debugging

### Monitoring and Observability
- Application logs
- Kubernetes events
- Prometheus metrics
- Distributed tracing (OpenTelemetry)

### Documentation
- Service-specific README files
- API documentation
- Architecture diagrams
- Troubleshooting guides

Remember: The goal is not just to fix the immediate bug, but to understand why it happened and prevent similar issues in the future.
