#!/bin/bash

set -e

echo "=========================================="
echo "Iceberg Production Startup"
echo "=========================================="

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Install Docker first."
    exit 1
fi

# Check docker-compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose not found. Install docker-compose first."
    exit 1
fi

echo "✓ Docker found"
echo "✓ docker-compose found"

# Start services
echo ""
echo "[1/4] Building Docker image..."
docker-compose -f docker-compose-prod.yml build

echo ""
echo "[2/4] Starting PostgreSQL..."
docker-compose -f docker-compose-prod.yml up -d postgres
sleep 5

echo ""
echo "[3/4] Waiting for PostgreSQL to be ready..."
for i in {1..30}; do
    if docker-compose -f docker-compose-prod.yml exec -T postgres pg_isready -U iceberg > /dev/null 2>&1; then
        echo "✓ PostgreSQL is ready"
        break
    fi
    echo "  Waiting... ($i/30)"
    sleep 1
done

echo ""
echo "[4/4] Starting Iceberg API..."
docker-compose -f docker-compose-prod.yml up -d iceberg
sleep 5

echo ""
echo "=========================================="
echo "✓ Iceberg Production Started"
echo "=========================================="
echo ""
echo "API Server: http://localhost:9090"
echo "Health Check: curl http://localhost:9090/health"
echo "Metrics: curl http://localhost:9090/metrics"
echo "Status: curl http://localhost:9090/status"
echo ""
echo "To view logs: docker-compose -f docker-compose-prod.yml logs -f iceberg"
echo "To stop: docker-compose -f docker-compose-prod.yml down"
echo ""
