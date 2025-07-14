#!/usr/bin/env make

.PHONY: help setup lint test security build container deploy pipeline clean format install-dev install-prod setup-mongodb mongodb-status mongodb-check

# Default target
help:
	@echo "Petrosa Trading Engine - Available Commands"
	@echo "=========================================="
	@echo ""
	@echo "Development Setup:"
	@echo "  setup          Setup Python environment and install dependencies"
	@echo "  install-dev    Install development dependencies"
	@echo "  install-prod   Install production dependencies"
	@echo "  setup-mongodb  Setup MongoDB for distributed state management"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint           Run all linting and formatting checks"
	@echo "  format         Format code with black"
	@echo "  test           Run tests with coverage"
	@echo "  security       Run security scan with Trivy"
	@echo ""
	@echo "Docker:"
	@echo "  build          Build Docker image"
	@echo "  container      Test Docker container"
	@echo "  docker-clean   Clean up Docker images"
	@echo ""
	@echo "Deployment:"
	@echo "  deploy         Deploy to local Kubernetes cluster"
	@echo "  pipeline       Run complete local CI/CD pipeline"
	@echo ""
	@echo "Database:"
	@echo "  setup-mongodb  Setup MongoDB for distributed state"
	@echo "  mongodb-status Check MongoDB connection"
	@echo "  mongodb-check  Detailed MongoDB health check"
	@echo ""
	@echo "Utilities:"
	@echo "  clean          Clean up temporary files and caches"
	@echo "  run            Run the application locally"
	@echo "  run-docker     Run the application in Docker"
	@echo ""

# Development setup
setup:
	@echo "🚀 Setting up development environment..."
	@chmod +x scripts/dev-setup.sh
	@./scripts/dev-setup.sh

install-dev:
	@echo "📚 Installing development dependencies..."
	pip install -r requirements-dev.txt

install-prod:
	@echo "📦 Installing production dependencies..."
	pip install -r requirements.txt

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

# Code quality
lint:
	@echo "🔍 Running linting checks..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh lint

format:
	@echo "🎨 Formatting code with black..."
	black .

test:
	@echo "🧪 Running tests..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh test

security:
	@echo "🔒 Running security scan..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh security

# Docker
build:
	@echo "🐳 Building Docker image..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh build

container:
	@echo "📦 Testing Docker container..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh container

docker-clean:
	@echo "🧹 Cleaning up Docker images..."
	docker rmi petrosa-tradeengine:VERSION_PLACEHOLDER 2>/dev/null || true
	docker rmi petrosa-tradeengine:local-* 2>/dev/null || true
	docker system prune -f

# Deployment
deploy:
	@echo "☸️  Deploying to Kubernetes..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh deploy

pipeline:
	@echo "🔄 Running complete local CI/CD pipeline..."
	@chmod +x scripts/local-pipeline.sh
	@./scripts/local-pipeline.sh all

# Application
run:
	@echo "🏃 Running application locally..."
	python -m tradeengine.api

run-docker:
	@echo "🐳 Running application in Docker..."
	docker run -p 8000:8000 \
		-e MONGODB_URI=mongodb://host.docker.internal:27017/petrosa \
		-e BINANCE_API_KEY=test \
		-e BINANCE_API_SECRET=test \
		-e JWT_SECRET_KEY=test \
		petrosa-tradeengine:VERSION_PLACEHOLDER

# Utilities
clean:
	@echo "🧹 Cleaning up..."
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .trivy/
	rm -f k8s/deployment-local.yaml
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete

# Quick development workflow
dev: setup lint test
	@echo "✅ Development workflow completed!"

# Quick production check
prod: lint test security build container
	@echo "✅ Production readiness check completed!"

# Install additional tools (optional)
install-tools:
	@echo "🔧 Installing additional development tools..."
	@if command -v brew >/dev/null 2>&1; then \
		echo "Installing tools via Homebrew..."; \
		brew install trivy jq; \
	elif command -v apt-get >/dev/null 2>&1; then \
		echo "Installing tools via apt..."; \
		sudo apt-get update && sudo apt-get install -y trivy jq; \
	elif command -v yum >/dev/null 2>&1; then \
		echo "Installing tools via yum..."; \
		sudo yum install -y trivy jq; \
	else \
		echo "Please install trivy and jq manually"; \
	fi

# Kubernetes utilities
k8s-status:
	@echo "📊 Kubernetes deployment status:"
	kubectl get pods -n petrosa-apps -l app=petrosa-tradeengine
	kubectl get svc -n petrosa-apps -l app=petrosa-tradeengine
	kubectl get ingress -n petrosa-apps -l app=petrosa-tradeengine

k8s-logs:
	@echo "📋 Kubernetes logs:"
	kubectl logs -n petrosa-apps -l app=petrosa-tradeengine --tail=50

k8s-clean:
	@echo "🧹 Cleaning up Kubernetes resources..."
	kubectl delete namespace petrosa-apps 2>/dev/null || true

# Health checks
health:
	@echo "🏥 Testing health endpoints..."
	@curl -s http://localhost:8000/health | jq . || echo "Health endpoint not available"
	@curl -s http://localhost:8000/ready | jq . || echo "Ready endpoint not available"
	@curl -s http://localhost:8000/live | jq . || echo "Live endpoint not available"

# Documentation
docs:
	@echo "📚 Generating documentation..."
	@echo "API documentation available at: http://localhost:8000/docs"
	@echo "OpenAPI spec available at: http://localhost:8000/openapi.json"

# Performance testing
benchmark:
	@echo "⚡ Running performance tests..."
	@echo "This would run performance benchmarks (not implemented yet)"

# Database utilities
db-migrate:
	@echo "🗄️  Running database migrations..."
	@echo "Database migrations not implemented yet"

db-seed:
	@echo "🌱 Seeding database..."
	@echo "Database seeding not implemented yet"

# Monitoring
monitor:
	@echo "📊 Application metrics:"
	@curl -s http://localhost:8000/metrics | head -20 || echo "Metrics endpoint not available"

# Backup
backup:
	@echo "💾 Creating backup..."
	@echo "Backup functionality not implemented yet"

# Restore
restore:
	@echo "🔄 Restoring from backup..."
	@echo "Restore functionality not implemented yet"
