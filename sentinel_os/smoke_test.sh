#!/bin/bash

set -e

echo "=========================================="
echo "Iceberg Production Smoke Test"
echo "=========================================="

API_URL="http://localhost:9090"
TIMEOUT=5

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

test_endpoint() {
    local name=$1
    local endpoint=$2
    local method=${3:-GET}
    
    echo -n "Testing $name... "
    
    if [ "$method" = "GET" ]; then
        if curl -s -f -m $TIMEOUT "$API_URL$endpoint" > /dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
            return 0
        else
            echo -e "${RED}✗${NC}"
            return 1
        fi
    else
        if curl -s -f -m $TIMEOUT -X POST -H "Content-Type: application/json" \
            -d '{"calls":[{"sid":"TEST001","status":"completed","duration":120,"from":"+1234","to":"+test"}]}' \
            "$API_URL$endpoint" > /dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
            return 0
        else
            echo -e "${RED}✗${NC}"
            return 1
        fi
    fi
}

# Check if API is running
echo ""
echo "Checking API server..."
if ! curl -s -f -m $TIMEOUT "$API_URL/health" > /dev/null 2>&1; then
    echo -e "${RED}✗ API server not responding at $API_URL${NC}"
    echo ""
    echo "Start the server with:"
    echo "  ./start_production.sh"
    exit 1
fi
echo -e "${GREEN}✓ API server is running${NC}"

# Run smoke tests
echo ""
echo "Running smoke tests..."
echo ""

test_endpoint "Health check" "/health" "GET"
test_endpoint "Metrics endpoint" "/metrics" "GET"
test_endpoint "Status endpoint" "/status" "GET"
test_endpoint "Process endpoint" "/process" "POST"
test_endpoint "Batch endpoint" "/batch" "POST"

# Get current metrics
echo ""
echo "Current System Metrics:"
METRICS=$(curl -s "$API_URL/metrics" 2>/dev/null | grep "iceberg_calls_total ")
if [ ! -z "$METRICS" ]; then
    echo "  $METRICS"
fi

ABANDONED=$(curl -s "$API_URL/metrics" 2>/dev/null | grep "iceberg_calls_abandoned ")
if [ ! -z "$ABANDONED" ]; then
    echo "  $ABANDONED"
fi

# Run load test if requested
if [ "$1" = "load" ]; then
    echo ""
    echo "Running load test..."
    python3 load_test_live.py
fi

echo ""
echo "=========================================="
echo "✓ Smoke test complete"
echo "=========================================="
echo ""
