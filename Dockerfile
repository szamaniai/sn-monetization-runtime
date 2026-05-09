# syntax=docker/dockerfile:1.4

# ---------- Build stage ----------
FROM python:3.12-slim AS builder

# Install system build dependencies (gcc, libpq-dev for PostgreSQL support)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------- Runtime stage ----------
FROM python:3.12-slim

# Add non‑root user
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app
COPY --from=builder /app /app
USER appuser

# Environment variables for production
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    LOG_LEVEL=info

EXPOSE 8000

# Entrypoint – start the FastAPI application with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]