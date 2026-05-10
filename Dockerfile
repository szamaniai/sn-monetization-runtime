```dockerfile
# syntax=docker/dockerfile:1.4

# ---------- Build stage ----------
ARG PYTHON_VERSION=3.12-slim
FROM python:${PYTHON_VERSION} AS builder

# Build‑time environment – avoid interactive prompts and reduce image size
ARG DEBIAN_FRONTEND=noninteractive
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install only the required build tools (gcc, libpq-dev) and clean apt cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies in a deterministic, cache‑free manner
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------- Runtime stage ----------
ARG PYTHON_VERSION=3.12-slim
FROM python:${PYTHON_VERSION}

# Create a reproducible non‑root user (fixed UID/GID) for security
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} appgroup && \
    useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /bin/bash appuser

# Set working directory and copy artifacts from the builder, preserving ownership
WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app /app

# Switch to the non‑root user
USER appuser

# Production‑ready environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    LOG_LEVEL=info

# Expose the application port
EXPOSE 8000

# Healthcheck to verify the service is alive (expects a /health endpoint)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD curl -f http://localhost:${UVICORN_PORT}/health || exit 1

# Entrypoint – start the FastAPI application with uvicorn (exec form)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
```