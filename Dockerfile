# Global News Briefing - Dockerfile
# Multi-stage build for smaller final image

# ═══════════════════════════════════════════
# Stage 1: Builder
# ═══════════════════════════════════════════
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev \
    libxslt1-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ═══════════════════════════════════════════
# Stage 2: Runtime
# ═══════════════════════════════════════════
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY prompts/ ./prompts/
COPY run.py .

# Create necessary directories and set ownership
RUN mkdir -p output cache data/history data/events data/evolution data/metrics data/risk data/runtime logs \
    && chown -R appuser:appuser /app

USER appuser

# Default command (can be overridden)
CMD ["python", "run.py", "--no-open"]
