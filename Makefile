#!/usr/bin/env make

.PHONY: run lint test format install clean consumer

# Default Python executable path
PYTHON := /Users/yurisa2/petrosa/petrosa-tradeengine/.venv/bin/python

# Development commands
run:
	$(PYTHON) -m uvicorn tradeengine.api:app --reload --host 0.0.0.0 --port 8000

consumer:
	$(PYTHON) -m tradeengine.consumer

lint:
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest -v

format:
	$(PYTHON) -m black .

# Setup commands
install:
	poetry install

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

# Docker commands (for future use)
docker-build:
	docker build -t petrosa-tradeengine .

docker-run:
	docker run -p 8000:8000 petrosa-tradeengine

# Help
help:
	@echo "Petrosa Trading Engine - Available commands:"
	@echo "  run       - Start the FastAPI server"
	@echo "  consumer  - Start the NATS consumer"
	@echo "  lint      - Run code linting"
	@echo "  test      - Run tests"
	@echo "  format    - Format code with Black"
	@echo "  install   - Install dependencies"
	@echo "  clean     - Clean Python cache files"
