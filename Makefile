#!/usr/bin/env make

# Standardized Makefile for Petrosa Systems
# Provides consistent development and testing procedures across all services

.PHONY: help setup install install-dev clean format lint type-check unit integration e2e test security build container deploy pipeline pre-commit pre-commit-install pre-commit-run coverage coverage-html coverage-check setup-mongodb mongodb-status mongodb-check

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
	@echo "  k8s-status     - Check Kubernetes deployment status"
	@echo "  k8s-logs       - View Kubernetes logs"
	@echo "  k8s-clean      - Clean up Kubernetes resources"

# Setup and installation
setup:
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

test:
	@echo "🧪 Running all tests with coverage..."
	pytest tests/ -v --cov=tradeengine --cov=contracts --cov=shared --cov-report=term-missing --cov-report=html --cov-report=xml --cov-fail-under=80

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
	bandit -r . -f json -o bandit-report.json -ll --exclude tests/
	@echo "Running safety dependency check..."
	safety check
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
	docker run --rm petrosa-tradeengine:latest --help

docker-clean:
	@echo "🧹 Cleaning up Docker images..."
	docker rmi petrosa-tradeengine:latest 2>/dev/null || true
	docker system prune -f

# Deployment
deploy:
	@echo "☸️  Deploying to Kubernetes..."
	@echo "Setting kubeconfig..."
	export KUBECONFIG=k8s/kubeconfig.yaml
	kubectl apply -f k8s/ --recursive
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
k8s-status:
	@echo "📊 Kubernetes deployment status:"
	kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine
	kubectl --kubeconfig=k8s/kubeconfig.yaml get svc -n petrosa-apps -l app=petrosa-tradeengine
	kubectl --kubeconfig=k8s/kubeconfig.yaml get ingress -n petrosa-apps -l app=petrosa-tradeengine

k8s-logs:
	@echo "📋 Kubernetes logs:"
	kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=petrosa-tradeengine --tail=50

k8s-clean:
	@echo "🧹 Cleaning up Kubernetes resources..."
	kubectl --kubeconfig=k8s/kubeconfig.yaml delete namespace petrosa-apps 2>/dev/null || true

# Quick development workflow
dev: setup format lint type-check test
	@echo "✅ Development workflow completed!"

# Quick production check
prod: format lint type-check test security build container
	@echo "✅ Production readiness check completed!"
