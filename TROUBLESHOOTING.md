# Troubleshooting Guide

Can't get Sentinel OS running? Don't worry—this guide covers the most common issues and how to fix them.

---

## Table of Contents

1. [PostgreSQL Connection Issues](#postgresql-connection-issues)
2. [Python & Dependency Problems](#python--dependency-problems)
3. [TLS Certificate Issues](#tls-certificate-issues)
4. [Test Failures](#test-failures)
5. [Docker Issues](#docker-issues)
6. [Claude API Key Problems](#claude-api-key-problems)
7. [Performance Issues](#performance-issues)
8. [Logging & Debug Mode](#logging--debug-mode)
9. [Getting Help](#getting-help)

---

## PostgreSQL Connection Issues

### Problem: "could not connect to server: Connection refused"

**Cause:** PostgreSQL is not running

**Solution:**

```bash
# macOS
brew services start postgresql

# Linux (Ubuntu/Debian)
sudo systemctl start postgresql

# Windows/WSL
sudo service postgresql start

# Verify it's running
psql --version
psql -U iceberg -d iceberg -c "SELECT version();"
```

### Problem: "FATAL: password authentication failed for user 'iceberg'"

**Cause:** PostgreSQL user doesn't exist or wrong password

**Solution:**

```bash
# Check if user exists
sudo -u postgres psql -c "\du"

# If not, create it
sudo -u postgres psql <<EOF
CREATE USER iceberg WITH PASSWORD 'iceberg';
CREATE DATABASE iceberg OWNER iceberg;
GRANT ALL PRIVILEGES ON DATABASE iceberg TO iceberg;
EOF

# Test connection
psql -h localhost -U iceberg -d iceberg -c "SELECT 1;"
```

### Problem: "FATAL: database 'iceberg' does not exist"

**Cause:** Database wasn't created

**Solution:**

```bash
# Create the database
sudo -u postgres psql -c "CREATE DATABASE iceberg OWNER iceberg;"

# Verify
psql -h localhost -U iceberg -d iceberg -c "SELECT current_database();"
```

### Problem: "could not translate host name 'postgres' to address"

**Cause:** Using Docker Compose but host is wrong, or PostgreSQL container isn't running

**Solution:**

```bash
# If using Docker Compose, ensure it's running
cd sentinel_os
docker-compose ps

# If postgres container isn't running, start it
docker-compose up -d postgres

# If connecting manually, use correct host
# Inside Docker: psql -h postgres -U iceberg -d iceberg
# From host machine: psql -h localhost -U iceberg -d iceberg
```

---

## Python & Dependency Problems

### Problem: "No module named 'sentinel_os'"

**Cause:** Virtual environment not activated, or dependencies not installed

**Solution:**

```bash
# Activate virtual environment
source venv/bin/activate  # macOS/Linux/Chromebook
# or
venv\Scripts\activate  # Windows

# Reinstall dependencies
pip install -r sentinel_os/requirements.txt

# Verify
python3 -c "import sentinel_os; print(sentinel_os.__file__)"
```

### Problem: "ModuleNotFoundError: No module named 'cassettes'"

**Cause:** Running from wrong directory

**Solution:**

```bash
# Make sure you're in the project root
cd sentinel_os  # If you're in a subdirectory

# Or run with python module syntax
python3 -m sentinel_os.iceberg_complete_simulator

# Not:
python3 iceberg_complete_simulator.py  # from root
```

### Problem: "pip install fails with 'ERROR: Could not find a version'"

**Cause:** Old pip version or incompatible Python version

**Solution:**

```bash
# Check Python version (should be 3.8+)
python3 --version

# Upgrade pip
pip install --upgrade pip

# Retry install
pip install -r sentinel_os/requirements.txt

# If still failing, check requirements.txt
cat sentinel_os/requirements.txt
```

### Problem: "AttributeError: module 'X' has no attribute 'Y'"

**Cause:** Dependency version mismatch

**Solution:**

```bash
# Clear and reinstall
pip uninstall -y -r sentinel_os/requirements.txt
pip install -r sentinel_os/requirements.txt

# Or reinstall specific package
pip install --force-reinstall package-name==version
```

---

## TLS Certificate Issues

### Problem: "FileNotFoundError: [Errno 2] No such file or directory: './certs/cert.pem'"

**Cause:** TLS certificates don't exist

**Solution:**

```bash
# Generate self-signed certificates
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -subj "/CN=localhost"

# Verify
ls -la certs/
file certs/cert.pem
```

### Problem: "SSL: CERTIFICATE_VERIFY_FAILED"

**Cause:** Invalid or expired certificate

**Solution:**

```bash
# Check certificate expiration
openssl x509 -in certs/cert.pem -text -noout | grep -A2 "Validity"

# If expired, regenerate
rm -f certs/cert.pem certs/key.pem
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -subj "/CN=localhost"

# For production, use a proper certificate from a CA
```

### Problem: "Permission denied" when accessing certs/

**Cause:** Wrong file permissions

**Solution:**

```bash
# Fix permissions
chmod 644 certs/cert.pem
chmod 600 certs/key.pem

# Verify
ls -la certs/
```

---

## Test Failures

### Problem: "7 tests skipped - requires PostgreSQL"

**Status:** This is expected behavior ✅

**Explanation:** These tests require a live PostgreSQL database:
- Ledger immutability tests
- SHA-256 chain verification tests
- End-to-end governance decision recording

**Solution:** To run the full test suite:

```bash
# 1. Ensure PostgreSQL is running
sudo systemctl start postgresql  # Linux
brew services start postgresql   # macOS
sudo service postgresql start    # Windows/WSL

# 2. Verify database exists
psql -h localhost -U iceberg -d iceberg -c "SELECT 1;"

# 3. Run full test suite
pytest sentinel_os/Tests/ -v

# All 110 tests should pass, 7 no longer skipped
```

### Problem: "FAILED test_X - AssertionError"

**Cause:** Test logic failed, or code changed

**Solution:**

```bash
# Run just that test with verbose output
pytest sentinel_os/Tests/test_file.py::test_name -vvv

# Run with print statements visible
pytest sentinel_os/Tests/test_file.py::test_name -vvs

# Run with traceback
pytest sentinel_os/Tests/test_file.py::test_name --tb=long
```

### Problem: "TIMEOUT - test took >30 seconds"

**Cause:** Performance issue or infinite loop

**Solution:**

```bash
# Run load tests to check baseline performance
python3 sentinel_os/load_test.py
# Should maintain ~942K calls/sec

# If slower, profile the code
python3 -m cProfile -s cumtime sentinel_os/iceberg_complete_simulator.py | head -50

# Or run specific test with timeout
pytest sentinel_os/Tests/test_file.py -v --timeout=60
```

---

## Docker Issues

### Problem: "docker: command not found"

**Cause:** Docker not installed

**Solution:**

```bash
# Install Docker
# macOS: https://docs.docker.com/desktop/install/mac-install/
# Linux: https://docs.docker.com/engine/install/ubuntu/
# Windows: https://docs.docker.com/desktop/install/windows-install/

# Verify installation
docker --version
docker run hello-world
```

### Problem: "ERROR: Cannot connect to Docker daemon"

**Cause:** Docker daemon not running

**Solution:**

```bash
# macOS
open /Applications/Docker.app

# Linux
sudo systemctl start docker

# Windows
# Start Docker Desktop from Start menu
```

### Problem: "docker-compose: command not found"

**Cause:** Docker Compose not installed (older Docker versions)

**Solution:**

```bash
# Install Docker Compose V2
pip install docker-compose

# Or upgrade Docker (includes Compose)
docker --version  # Should be 20.10+

# Verify
docker compose version
```

### Problem: "port 5432 is already allocated"

**Cause:** Another service using the PostgreSQL port

**Solution:**

```bash
# Option 1: Stop conflicting service
docker-compose down
# or
sudo systemctl stop postgresql

# Option 2: Use different port
# Edit docker-compose.yml and change:
# ports:
#   - "5433:5432"  # Use 5433 instead of 5432

# Verify port is free
lsof -i :5432
```

### Problem: "ERROR: Service 'postgres' failed to start"

**Cause:** Database initialization failed

**Solution:**

```bash
# View logs
docker-compose logs postgres

# Restart with fresh volume
docker-compose down -v  # -v removes volumes
docker-compose up -d postgres

# Wait for startup
sleep 10
docker-compose exec postgres pg_isready
```

---

## Claude API Key Problems

### Problem: "CLAUDE_API_KEY not set" or governance decisions fail

**Cause:** API key environment variable missing

**Solution:**

```bash
# Set API key
export CLAUDE_API_KEY="sk-ant-your-actual-key-here"

# Verify
echo $CLAUDE_API_KEY

# Or add to .env file
cat > sentinel_os/.env <<EOF
CLAUDE_API_KEY=sk-ant-your-actual-key-here
POSTGRES_HOST=localhost
POSTGRES_USER=iceberg
POSTGRES_PASSWORD=iceberg
POSTGRES_DB=iceberg
EOF

# Source it
source sentinel_os/.env
```

### Problem: "Claude API call failed: 401 Unauthorized"

**Cause:** Invalid or expired API key

**Solution:**

```bash
# Check API key format (should start with sk-ant-)
echo $CLAUDE_API_KEY | head -c 10

# Get a new key from https://console.anthropic.com/
# Set it correctly
export CLAUDE_API_KEY="sk-ant-your-new-key"

# Test connection
python3 -c "from sentinel_os.claude_governance_api import ClaudeGovernor; print('API configured')"
```

### Problem: "Governor error: timeout"

**Cause:** Claude API taking too long to respond

**Solution:**

```bash
# This is fail-closed behavior (working as designed)
# Governor returns: approved=false, risk_level=critical

# Check logs for details
tail -f sentinel_os/logs/governor.log

# Verify internet connection
curl https://api.anthropic.com/v1/models

# Increase timeout in code if needed
# See: claude_governance_api.py, timeout parameter
```

---

## Performance Issues

### Problem: "System is slow / throughput <942K calls/sec"

**Cause:** Various factors—check in order

**Solution:**

```bash
# 1. Run baseline test
python3 sentinel_os/load_test.py

# 2. Check system resources
top  # or Task Manager on Windows

# 3. Check database performance
psql -U iceberg -d iceberg -c "EXPLAIN ANALYZE SELECT COUNT(*) FROM ledger_entries;"

# 4. Profile the code
python3 -m cProfile -s cumtime sentinel_os/load_test.py 2>&1 | head -30

# 5. Check for bottlenecks
# Look for: long database queries, API calls, lock contention
```

### Problem: "Out of memory" errors

**Cause:** Processing too much data at once

**Solution:**

```bash
# Check available memory
free -h  # Linux
vm_stat  # macOS

# Reduce batch size in config
# Edit: sentinel_os/adaptive_config.py
# Reduce BATCH_SIZE parameter

# Or run with memory limit
python3 -m memory_profiler sentinel_os/iceberg_complete_simulator.py
```

---

## Logging & Debug Mode

### Enable Debug Logging

```bash
# Set log level
export LOG_LEVEL=DEBUG

# Run with debug output
python3 sentinel_os/iceberg_complete_simulator.py --debug

# Or modify code
import logging
logging.basicConfig(level=logging.DEBUG)
```

### View Logs

```bash
# Sentinel logs
tail -f sentinel_os/logs/sentinel.log

# Governor logs
tail -f sentinel_os/logs/governor.log

# All logs
tail -f sentinel_os/logs/*.log

# Search for errors
grep "ERROR" sentinel_os/logs/*.log
grep "WARN" sentinel_os/logs/*.log
```

### Check Prometheus Metrics

```bash
# If running with docker-compose
curl http://localhost:9090/metrics

# Key metrics to check:
# - sentinel_governance_decisions_total
# - sentinel_governance_approval_rate
# - sentinel_governance_errors_total
# - sentinel_governance_latency_ms
```

---

## Getting Help

### Still stuck? Here's how to get support:

1. **Check existing issues:** https://github.com/wking53214/sentinel_os/issues

2. **Search documentation:**
   - [README.md](README.md) — Overview
   - [DEPLOYMENT.md](sentinel_os/DEPLOYMENT.md) — Deployment guide
   - [SETUP_GUIDE.md](SETUP_GUIDE.md) — Step-by-step setup

3. **Create a GitHub issue** with:
   - Clear description of the problem
   - Steps to reproduce
   - Error message (full traceback)
   - Your environment (OS, Python version, etc.)
   - What you've already tried

4. **Join discussions:** https://github.com/wking53214/sentinel_os/discussions

### Helpful Commands for Debugging

```bash
# System info
uname -a
python3 --version
pip list

# PostgreSQL status
psql -U iceberg -d iceberg -c "SELECT version();"
psql -U iceberg -d iceberg -c "SELECT COUNT(*) FROM ledger_entries;"

# Docker status
docker ps
docker logs sentinel_os_postgres_1

# Network connectivity
curl -I https://api.anthropic.com/v1/models

# File permissions
ls -la certs/
ls -la sentinel_os/

# Python path
python3 -c "import sys; print('\n'.join(sys.path))"
```

---

## Common Success Indicators

✅ **You're set up correctly if:**

- `pytest sentinel_os/Tests/ -v` shows 110 passed tests
- `python3 sentinel_os/iceberg_complete_simulator.py` runs without errors
- `docker-compose up -d` starts all services
- `curl http://localhost:9090/health` returns 200 OK
- `psql -U iceberg -d iceberg -c "SELECT 1;"` succeeds

---

## Report a Bug

Found an issue not covered here? Please report it:

1. Open: https://github.com/wking53214/sentinel_os/issues/new
2. Use the bug report template
3. Include your troubleshooting steps
4. Attach logs if relevant

Thank you for helping us improve Sentinel OS! 🚀
