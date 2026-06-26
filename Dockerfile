# ── Nabil-gold — Multi-Asset Paper-Trading Bot ──────────────────────
# Multi-stage build for a small, secure production image.
# Build:  docker build -t nabil-gold .
# Run:    docker run --env-file .env nabil-gold
# ────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# Security: non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Storage directory for local JSON fallback
RUN mkdir -p storage && chown -R appuser:appuser /app

USER appuser

# Default: run analysis
CMD ["python", "scripts/run_analysis.py"]
