# Bug Investigation Quick Reference

## 🚨 When a Bug is Reported

### Phase 1: Confirm & Reproduce
```bash
# Quick confirmation
./scripts/bug-investigation.sh confirm

# Or manual steps:
make setup && make test
./scripts/local-pipeline.sh all
```

### Phase 2: Investigate & Hypothesize
```bash
# Run investigation
./scripts/bug-investigation.sh investigate

# Manual investigation checklist:
# □ Read error logs and stack traces
# □ Examine specific module/function mentioned
# □ Check config.py and env.example
# □ Review requirements.txt
# □ Look at test files for expected behavior
# □ Form multiple hypotheses about root cause
```

### Phase 3: Make Targeted Changes
- **Be surgical**: Change only what's necessary
- **Be explicit**: Document what you're changing and why
- **Follow patterns**: Use existing code style and patterns
- **Add logging**: Include proper error handling

### Phase 4: Test & Validate
```bash
# Comprehensive testing
./scripts/bug-investigation.sh test

# Docker testing (if applicable)
./scripts/bug-investigation.sh docker

# Kubernetes check (if applicable)
./scripts/bug-investigation.sh k8s
```

### Phase 5: Document & Prevent
- Add comments explaining the fix
- Update documentation if needed
- Consider adding regression tests
- Think about preventive measures

## 🔧 Service-Specific Commands

### TA Bot
```bash
make setup && make test
./scripts/local-pipeline.sh all
make run-docker
make k8s-status
```

### Trading Engine
```bash
make setup && make test
./scripts/local-pipeline.sh all
make run-docker
make k8s-status
./scripts/test-api-endpoint-flow.py
```

### Data Extractor
```bash
make setup && make test
./scripts/local-pipeline.sh all
make run-docker
make k8s-status
./scripts/deploy-local.sh
```

## 🐛 Common Bug Patterns

### Configuration Issues
**Symptoms**: Env var errors, connection failures
**Check**: `env.example`, `config.py`, Kubernetes secrets

### Dependency Conflicts
**Symptoms**: Import errors, version conflicts
**Check**: `requirements.txt`, `pip list`, NumPy compatibility

### Database Issues
**Symptoms**: Connection timeouts, auth failures
**Check**: Credentials, network, connection strings

### K8s Issues
**Symptoms**: Pod crashes, service unavailable
**Check**: `kubectl logs`, resource limits, service config

## 📋 Validation Checklist

- [ ] Original error no longer occurs
- [ ] No new errors introduced
- [ ] Related functionality still works
- [ ] Performance not degraded
- [ ] Fix doesn't break other services
- [ ] All tests pass
- [ ] Code quality checks pass

## 🚀 Emergency Procedures

### If Fix Breaks Other Things
1. Revert immediately
2. Document what went wrong
3. Form new hypothesis
4. Make smaller changes
5. Test incrementally

### If Critical Bug (Production Down)
1. Implement quick workaround
2. Deploy workaround to restore service
3. Investigate root cause properly
4. Implement proper fix
5. Remove workaround

## 💡 Pro Tips

1. **Always test locally first**
2. **Use existing scripts and Makefile commands**
3. **Follow systematic approach**: Confirm → Investigate → Fix → Test
4. **Be specific about changes and document reasoning**
5. **Test in multiple environments** (local, Docker, K8s)
6. **Ensure no regressions**
7. **Stop only when all tests pass**

## 📞 Quick Commands

```bash
# Complete investigation pipeline
./scripts/bug-investigation.sh all

# Just confirm the bug
./scripts/bug-investigation.sh confirm

# Just run tests
./scripts/bug-investigation.sh test

# Check K8s status
./scripts/bug-investigation.sh k8s
```

## 📚 Documentation

- **Full Guide**: `docs/BUG_INVESTIGATION_GUIDE.md`
- **Service Docs**: `docs/` directory
- **Architecture**: `docs/ARCHITECTURE.md`
- **Troubleshooting**: `docs/TROUBLESHOOTING.md`

---

**Remember**: The goal is not just to fix the immediate bug, but to understand why it happened and prevent similar issues in the future.
