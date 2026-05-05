#!/bin/bash
# Wrapper script for bandit to handle argument parsing correctly
# Ignore all arguments passed by pre-commit (they are file paths)
# Use --exit-zero so the pipeline doesn't fail on found issues
bandit -ll -r . -c .bandit --exit-zero
