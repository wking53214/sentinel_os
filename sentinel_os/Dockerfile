FROM python:3.11-slim

WORKDIR /app

# curl is needed by the healthcheck below (docker-compose-prod.yml also
# curls /health directly), and it isn't in python:3.11-slim by default.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies from requirements.txt. Previously this only
# installed numpy+pytest and skipped requirements.txt entirely -- the
# built image was missing fastapi, anthropic, psycopg2-binary, pydantic,
# uvicorn, and python-dotenv, so it would fail on import as soon as it
# started. requirements.txt already includes numpy and pytest.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . /app

# Set production environment
ENV PYTHONUNBUFFERED=1
ENV ICEBERG_ENV=production

# Run as a non-root user
RUN useradd --create-home --uid 1000 iceberg \
    && chown -R iceberg:iceberg /app
USER iceberg

# Expose metrics port
EXPOSE 9090

# Health check: previously this always exited 0 regardless of whether the
# app was actually up (`python3 -c "import sys; sys.exit(0)"`), which is a
# no-op that never reports unhealthy. Curl the real health endpoint the
# resilient API server exposes instead. If your entrypoint doesn't serve
# /health (e.g. a non-API worker process), override HEALTHCHECK for that
# image/service.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${PORT:-9090}/health || exit 1

# Run the persistent API server. Previously this ran run_adaptive.py,
# a one-shot demo/batch script that prints a report and exits -- it never
# binds to a port or serves /health, which is why the healthcheck above
# had to be a no-op stub that always passed. api_server_resilient.py is
# the actual long-running service (uvicorn, /health, /metrics, /status,
# TLS handling) that EXPOSE 9090 and docker-compose-prod.yml's healthcheck
# were written for.
CMD ["python3", "api_server_resilient.py"]
