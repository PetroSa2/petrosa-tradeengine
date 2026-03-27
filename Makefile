#!/usr/bin/env make

# Standardized Makefile for Petrosa Systems
# Provides consistent development and testing procedures across all services

# Python enforcement
PYTHON_VERSION_EXPECTED := 3.11
PYTHON_VERSION_ACTUAL := $(shell python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

.PHONY: help setup validate-python install install-dev clean format lint type-check unit integration e2e test security build container deploy pipeline pre-commit pre-commit-install pre-commit-run coverage coverage-html coverage-check setup-mongodb mongodb-status mongodb-check version-check version-info version-debug install-git-hooks test-ci-pipeline

# Default target
help:
	@echo "🚀 Petrosa Trading Engine - Standardized Development Commands"
	@echo "=========================================================="
	@echo ""
	@echo "📦 Setup & Installation:"
	@echo "  setup          - Complete environment setup with pre-commit"
	@echo "  install        - Install production dependencies"
	@echo "  install-dev    - Install development dependencies"
	@echo "  clean          - Clean up cache and temporary files"
	@echo ""
	@echo "🔧 Code Quality:"
	@echo "  format         - Format code with black and isort"
	@echo "  lint           - Run linting checks (flake8, ruff)"
	@echo "  type-check     - Run type checking with mypy"
	@echo "  pre-commit     - Run pre-commit hooks on all files"
	@echo "  pre-commit-install - Install pre-commit hooks"
	@echo ""
	@echo "🧪 Testing:"
	@echo "  unit           - Run unit tests only"
	@echo "  integration    - Run integration tests only"
	@echo "  e2e            - Run end-to-end tests only"
	@echo "  test           - Run all tests with coverage"
	@echo "  coverage       - Generate coverage reports"
	@echo "  coverage-html  - Generate HTML coverage report"
	@echo "  coverage-check - Check coverage threshold (80%)"
	@echo ""
	@echo "🔒 Security:"
	@echo "  security       - Run security scans (bandit, safety, trivy)"
	@echo ""
	@echo "🐳 Docker:"
	@echo "  build          - Build Docker image"
	@echo "  container      - Test Docker container"
	@echo "  docker-clean   - Clean up Docker images"
	@echo ""
	@echo "🚀 Deployment:"
	@echo "  deploy         - Deploy to Kubernetes cluster"
	@echo "  pipeline       - Run complete CI/CD pipeline"
	@echo ""
	@echo "🗄️  Database:"
	@echo "  setup-mongodb  - Setup MongoDB for distributed state"
	@echo "  mongodb-status - Check MongoDB connection"
	@echo "  mongodb-check  - Detailed MongoDB health check"
	@echo ""
	@echo "📊 Utilities:"
	@echo "🔢 Version Management:"
	@echo "  version-check  - Check VERSION_PLACEHOLDER integrity"
	@echo "  version-info   - Show version information"
	@echo "  version-debug  - Debug version issues"
	@echo "  install-git-hooks - Install VERSION_PLACEHOLDER protection hooks"	@echo "  k8s-status     - Check Kubernetes deployment status"
	@echo "  k8s-logs       - View Kubernetes logs"
	@echo "  k8s-clean      - Clean up Kubernetes resources"

# Setup and installation
validate-python:
	@echo "$(BLUE)Validating Python version...$(NC)"
	@if [ "$(PYTHON_VERSION_ACTUAL)" != "$(PYTHON_VERSION_EXPECTED)" ]; then \
		echo "$(RED)❌ ERROR: Python $(PYTHON_VERSION_EXPECTED) required, found $(PYTHON_VERSION_ACTUAL)$(NC)"; \
		echo "$(YELLOW)💡 Recommended resolution: Use 'pyenv install 3.11.9 && pyenv local 3.11.9'$(NC)"; \
		exit 1; \
	fi
	@echo "$(GREEN)✅ Python version $(PYTHON_VERSION_ACTUAL) matches expected $(PYTHON_VERSION_EXPECTED)$(NC)"

setup: validate-python
	@echo "🚀 Setting up development environment..."
	python -m pip install --upgrade pip
	pip install -r requirements.txt
	pip install -r requirements-dev.txt
	@echo "🔧 Installing pre-commit hooks..."
	pre-commit install
	@echo "✅ Setup completed!"

install:
	@echo "📦 Installing production dependencies..."
	pip install -r requirements.txt

install-dev:
	@echo "🔧 Installing development dependencies..."
	pip install -r requirements-dev.txt

clean:
	@echo "🧹 Cleaning up cache and temporary files..."
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .trivy/
	rm -f bandit-report.json
	rm -f coverage.xml
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -delete
	@echo "✅ Cleanup completed!"

# Code quality
format:
	@echo "🎨 Formatting code with black and isort..."
	black . --line-length=88
	isort . --profile=black --line-length=88
	@echo "✅ Code formatting completed!"

lint:
	@echo "✨ Running linting checks..."
	@echo "Running flake8..."
	flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics --exclude=.venv,venv,htmlcov,.git,__pycache__,*.egg-info
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=88 --statistics --exclude=.venv,venv,htmlcov,.git,__pycache__,*.egg-info
	@echo "Running ruff..."
	ruff check . --fix
	@echo "✅ Linting completed!"

type-check:
	@echo "🔍 Running type checking with mypy..."
	mypy tradeengine/ contracts/ shared/ --ignore-missing-imports --strict
	@echo "✅ Type checking completed!"

pre-commit-install:
	@echo "🔧 Installing pre-commit hooks..."
	pre-commit install
	@echo "✅ Pre-commit hooks installed!"

pre-commit:
	@echo "🔍 Running pre-commit hooks on all files..."
	pre-commit run --all-files
	@echo "✅ Pre-commit checks completed!"

# Testing
unit:
	@echo "🧪 Running unit tests..."
	pytest tests/ -m "unit" -v --tb=short

integration:
	@echo "🔗 Running integration tests..."
	pytest tests/ -m "integration" -v --tb=short

e2e:
	@echo "🌐 Running end-to-end tests..."
	pytest tests/ -m "e2e" -v --tb=short

test: validate-python
	@echo "🧪 Running all tests with coverage..."
	pytest tests/ -v --cov=tradeengine --cov=contracts --cov=shared --cov-report=term-missing --cov-report=html --cov-report=xml --cov-fail-under=40

coverage:
	@echo "📊 Running tests with coverage..."
	pytest tests/ --cov=tradeengine --cov=contracts --cov=shared --cov-report=term-missing --cov-report=html --cov-report=xml

coverage-html:
	@echo "📈 Generating HTML coverage report..."
	coverage html
	@echo "📄 HTML report generated in htmlcov/index.html"

coverage-check:
	@echo "📊 Checking coverage threshold..."
	@COVERAGE_PERCENT=$$(coverage report --format=total 2>/dev/null || echo "0"); \
	echo "📈 Total Coverage: $${COVERAGE_PERCENT}%"; \
	COVERAGE_THRESHOLD=80; \
	if (( $$(echo "$${COVERAGE_PERCENT} >= $${COVERAGE_THRESHOLD}" | bc -l 2>/dev/null || echo "0") )); then \
		echo "✅ Coverage meets threshold of $${COVERAGE_THRESHOLD}%"; \
	else \
		echo "⚠️  Coverage below threshold of $${COVERAGE_THRESHOLD}%"; \
		echo "❌ Current: $${COVERAGE_PERCENT}%, Required: $${COVERAGE_THRESHOLD}%"; \
		exit 1; \
	fi

# Security
security:
	@echo "🔒 Running security scans..."
	@echo "Running bandit security scan..."
	bandit -r . -f json -o bandit-report.json --configfile .bandit
	@echo "Running safety dependency check..."
	safety check --ignore 72347,71922,77744,77745,71601,64402,77714,77149,74882,76262,65398,65215,76769,76771,60917,68094,73725,73970,76348,76349,76347,77680,76225,76219,74427,66706,70716,70715,54980,70626,51457,77942,65182,65183,72963,75114,70625,72715,59300,76378,74735,75976,64459,64396,49337,66742,64484,66947,71199,74380,74251,74252,64644,62583,62582,64642,62326,71545,70630,64278,62105,73795,73800,71642,68088,71643,54672,71987,71640,71988,66738,66736,71641,55261,77323
	@echo "Running Trivy vulnerability scan..."
	@if command -v trivy >/dev/null 2>&1; then \
		trivy fs . --format table; \
	else \
		echo "⚠️  Trivy not installed. Install with: brew install trivy (macOS) or see https://aquasecurity.github.io/trivy/latest/getting-started/installation/"; \
	fi
	@echo "✅ Security scans completed!"

# Docker
build:
	@echo "🐳 Building Docker image..."
	docker build -t petrosa-tradeengine:latest .

container:
	@echo "📦 Testing Docker container..."
	docker run --rm --name test-container petrosa-tradeengine:latest python -c "import tradeengine; print('✅ Container test passed')" || (docker logs test-container 2>/dev/null || true; exit 1)

docker-clean:
	@echo "🧹 Cleaning up Docker images..."
	docker rmi petrosa-tradeengine:latest 2>/dev/null || true
	docker system prune -f

# Deployment
deploy:
	@echo "☸️  Deploying to Kubernetes..."
	kubectl --kubeconfig=$(KUBECONFIG) apply -f ../petrosa_k8s/k8s/tradeengine/ --recursive
	@echo "✅ Deployment completed!"

pipeline:
	@echo "🔄 Running complete CI/CD pipeline..."
	@echo "=================================="
	@echo ""
	@echo "1️⃣ Installing dependencies..."
	$(MAKE) install-dev
	@echo ""
	@echo "2️⃣ Running pre-commit checks..."
	$(MAKE) pre-commit
	@echo ""
	@echo "3️⃣ Running code quality checks..."
	$(MAKE) format
	$(MAKE) lint
	$(MAKE) type-check
	@echo ""
	@echo "4️⃣ Running tests..."
	$(MAKE) test
	@echo ""
	@echo "5️⃣ Running security scans..."
	$(MAKE) security
	@echo ""
	@echo "6️⃣ Building Docker image..."
	$(MAKE) build
	@echo ""
	@echo "7️⃣ Testing container..."
	$(MAKE) container
	@echo ""
	@echo "✅ Pipeline completed successfully!"

# MongoDB setup
setup-mongodb:
	@echo "🗄️  Setting up MongoDB for distributed state management..."
	@chmod +x scripts/setup-mongodb.sh
	@./scripts/setup-mongodb.sh

mongodb-status:
	@echo "🔍 Checking MongoDB connection..."
	@python scripts/check-mongodb.py

mongodb-check:
	@echo "🔍 Checking MongoDB connection and collections..."
	@python scripts/check-mongodb.py detailed

# Kubernetes utilities
KUBECONFIG ?= ../petrosa_k8s/k8s/kubeconfig.yaml

k8s-status:
	@echo "📊 Kubernetes deployment status:"
	kubectl --kubeconfig=$(KUBECONFIG) get pods -n petrosa-apps -l app=petrosa-tradeengine
	kubectl --kubeconfig=$(KUBECONFIG) get svc -n petrosa-apps -l app=petrosa-tradeengine
	kubectl --kubeconfig=$(KUBECONFIG) get ingress -n petrosa-apps -l app=petrosa-tradeengine

k8s-logs:
	@echo "📋 Kubernetes logs:"
	kubectl --kubeconfig=$(KUBECONFIG) logs -n petrosa-apps -l app=petrosa-tradeengine --tail=50

k8s-clean:
	@echo "🧹 Cleaning up Kubernetes resources..."
	kubectl --kubeconfig=$(KUBECONFIG) delete namespace petrosa-apps 2>/dev/null || true

# Quick development workflow
dev: setup format lint type-check test
	@echo "✅ Development workflow completed!"

# Quick production check
prod: format lint type-check test security build container
	@echo "✅ Production readiness check completed!"

# Version Management
version-check:
	@echo "🔍 Checking VERSION_PLACEHOLDER integrity..."
	@if [ -f "scripts/version-manager.sh" ]; then \
		./scripts/version-manager.sh validate; \
	else \
		echo "❌ scripts/version-manager.sh not found"; \
		exit 1; \
	fi

version-info:
	@echo "📦 Version Information:"
	@if [ -f "scripts/version-manager.sh" ]; then \
		./scripts/version-manager.sh info; \
	else \
		echo "❌ scripts/version-manager.sh not found"; \
		exit 1; \
	fi

version-debug:
	@echo "🐛 Version Debug Information:"
	@if [ -f "scripts/version-manager.sh" ]; then \
		./scripts/version-manager.sh debug; \
	else \
		echo "❌ scripts/version-manager.sh not found"; \
		exit 1; \
	fi

install-git-hooks:
	@echo "🔧 Installing git hooks for VERSION_PLACEHOLDER protection..."
	@if [ -f "scripts/install-git-hooks.sh" ]; then \
		chmod +x scripts/install-git-hooks.sh; \
		./scripts/install-git-hooks.sh; \
	else \
		echo "❌ scripts/install-git-hooks.sh not found"; \
		exit 1; \
	fi

# Local CI/CD pipeline simulation
test-ci-pipeline:
	@echo "🧪 Running CI/CD pipeline simulation..."
	@echo "This matches GitHub Actions workflow exactly"
	@echo "=================================="
	@echo ""
	@echo "Stage 1: Dependencies"
	$(MAKE) clean
	$(MAKE) setup
	@echo ""
	@echo "Stage 2: Linting & Formatting"
	$(MAKE) format
	$(MAKE) lint
	@echo ""
	@echo "Stage 3: Tests"
	$(MAKE) test
	@echo ""
	@echo "Stage 4: Docker Build"
	$(MAKE) build
	@echo ""
	@echo "Stage 5: Container Test"
	$(MAKE) container
	@echo ""
	@echo "✅ Local CI/CD simulation passed!"
	@echo "Safe to push to GitHub"
