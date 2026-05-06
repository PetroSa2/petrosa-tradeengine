#!/bin/bash
# Wrapper script for bandit to handle argument parsing correctly
# Ignore all arguments passed by pre-commit (they are file paths)
bandit -ll -r . -c .bandit
