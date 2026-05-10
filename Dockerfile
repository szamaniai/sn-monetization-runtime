# syntax=docker/dockerfile:1.4

########################
# Builder Stage
########################
FROM python:3.12-slim AS builder

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build‑time dependencies (gcc, libpq-dev for psycopg2)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the lock/requirements file first for cache‑friendly builds
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

########################
# Runtime Stage
########################
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy application source code
COPY . .

# Create a non‑root user for security
RUN useradd --create-home appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose the default FastAPI port
EXPOSE 8000

# Entrypoint: run the FastAPI app with Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]