# MobilityOne WhatsApp Bot
# Version: 11.0 - Production Ready
# 
# Changes from v10:
# - Added postgresql-client for database initialization
# - Added proper health checks
# - Non-root user for security
# - Multi-stage could be added for smaller image

FROM python:3.11-slim

# Build arguments
ARG APP_VERSION=11.0.0

# Environment - no secrets here, all from runtime env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION}

# Install system dependencies including PostgreSQL client
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create app directory
WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application code
COPY . .

# Make init script executable
RUN chmod +x /app/docker/init-db.sh 2>/dev/null || true

# Create non-root user for security
RUN groupadd -r appgroup && \
    useradd -r -g appgroup appuser && \
    mkdir -p /app/.cache && \
    chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check - will be overridden per-service in docker-compose
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (overridden in docker-compose for worker)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
