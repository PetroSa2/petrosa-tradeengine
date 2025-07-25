# Multi-stage Dockerfile for Petrosa Trading Engine
FROM python:3.11-alpine AS base

# Build arguments
ARG VERSION=dev
ARG COMMIT_SHA=unknown
ARG BUILD_DATE=unknown

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION="${VERSION}" \
    COMMIT_SHA="${COMMIT_SHA}" \
    BUILD_DATE="${BUILD_DATE}" \
    MONGODB_URI="mongodb://localhost:27017/test" \
    MONGODB_DATABASE="test"

# Metadata labels
LABEL org.opencontainers.image.title="Petrosa Trading Engine" \
      org.opencontainers.image.description="Signal-driven trading execution engine" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/petrosa/petrosa-tradeengine" \
      org.opencontainers.image.vendor="Petrosa" \
      org.opencontainers.image.licenses="MIT"

# Install system dependencies
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    curl \
    ca-certificates

# Create app user
RUN addgroup -S appuser && adduser -S -G appuser appuser

# Set work directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Change ownership to app user
RUN chown -R appuser:appuser /app

# Switch to app user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "tradeengine.api:app", "--host", "0.0.0.0", "--port", "8000"]
