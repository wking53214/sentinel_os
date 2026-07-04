FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir numpy pytest

# Copy code
COPY . /app

# Set production environment
ENV PYTHONUNBUFFERED=1
ENV ICEBERG_ENV=production

# Expose metrics port
EXPOSE 9090

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python3 -c "import sys; sys.exit(0)"

# Run production orchestrator
CMD ["python3", "run_adaptive.py"]
