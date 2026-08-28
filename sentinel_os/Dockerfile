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

# Port follows the default CMD (see the end of this file): the image's
# default program is api_server_v2.py, the governed ingress, which
# listens on INGRESS_PORT (default 8000). The IVR lane
# (api_server_resilient.py, port 9090) moved to the GSA-815 repo.
EXPOSE 8000

# Health check: previously this always exited 0 regardless of whether the
# app was actually up (`python3 -c "import sys; sys.exit(0)"`), which is a
# no-op that never reports unhealthy. Curl the real health endpoint the
# resilient API server exposes instead. If your entrypoint doesn't serve
# /health (e.g. a non-API worker process), override HEALTHCHECK for that
# image/service.
#
# Protocol fallback (2026-08-07): api_server_resilient.py serves HTTPS
# (self-signed cert) whenever ./certs/cert.pem + key.pem exist at
# startup, plain HTTP otherwise -- and cert presence depends on the
# build context, not anything this Dockerfile pins. A plain `curl
# http://` healthcheck against a TLS-serving instance gets "Empty reply
# from server" at the TLS handshake, never reaching the app -- confirmed
# live against a 2-day-old container stuck reporting unhealthy on every
# check despite the app itself answering /health fine over HTTPS. Try
# HTTPS (-k: the cert is self-signed, curl has no CA to validate it
# against) first, fall back to HTTP -- correct whichever mode the app
# actually started in, instead of assuming one.
#
# Port follows the default CMD as of the governed-lane wiring: probing
# 9090 while the default program serves 8000 would report unhealthy
# forever against a perfectly working app -- the exact failure the
# protocol fallback above was added to fix, in a different disguise.
# Every docker-compose.yml service declares its own healthcheck, so this
# line only governs a bare `docker run`.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsk https://localhost:${INGRESS_PORT:-8000}/health || curl -fs http://localhost:${INGRESS_PORT:-8000}/health || exit 1

# Run the governed ingress. The image's default program is what a bare
# `docker run` starts, and that is the governed lane: ingress -> redis
# queue -> sentinel_worker -> GovernanceHarness (+ Reg B lens). The IVR
# lane (api_server_resilient.py -> resilient_harness ->
# IcebergProductionHarness, port 9090) moved to the GSA-815 repo along
# with the rest of the telephony application; docker-compose-prod.yml and
# k8s/ moved with it.
CMD ["python3", "api_server_v2.py"]
