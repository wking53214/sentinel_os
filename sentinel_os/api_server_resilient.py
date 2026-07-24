"""
Iceberg API Server (Resilient + TLS + API Key Auth)

With SSL/HTTPS and API key authentication
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn
import os
from datetime import datetime, timezone

from resilient_harness import ResilientHarness
from operational_resilience import setup_logging, export_alert_rules
from grafana_dashboard import generate_dashboard_json
from api_key_auth import api_key_manager, require_api_key

logger = setup_logging("APIServer")

app = FastAPI(
    title="Iceberg IVR Platform (Resilient + TLS + Auth)",
    description="Self-healing IVR with TLS and API key authentication",
    version="1.0.0"
)

harness = None

@app.on_event("startup")
async def startup():
    global harness
    
    config = {
        "postgres_host": os.getenv("POSTGRES_HOST", "localhost"),
        "postgres_port": int(os.getenv("POSTGRES_PORT", 5432)),
        "postgres_db": os.getenv("POSTGRES_DB", "iceberg"),
        "postgres_user": os.getenv("POSTGRES_USER", "iceberg"),
        "postgres_password": os.getenv("POSTGRES_PASSWORD", "iceberg"),
        "claude_api_key": os.getenv("CLAUDE_API_KEY"),
        "twilio_account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
        "twilio_api_key": os.getenv("TWILIO_API_KEY"),
        "twilio_api_secret": os.getenv("TWILIO_API_SECRET"),
    }
    
    # require_cassette_binding is hardcoded True, not read from env --
    # same posture as sentinel_worker.py and ICEBERG_LEDGER_RUNTIME_USER:
    # this is a real production entrypoint, no fallback that lets it
    # start ungoverned by an operator forgetting to set a flag.
    harness = ResilientHarness(config, require_cassette_binding=True)
    logger.info("Resilient harness initialized (TLS + API key auth enabled)")

@app.on_event("shutdown")
async def shutdown():
    global harness
    if harness:
        harness.shutdown()

@app.get("/health")
async def health():
    """Public health check (no auth required)"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    health_status = harness.get_health()
    
    if health_status["overall"] == "healthy":
        return health_status
    elif health_status["overall"] == "degraded":
        return JSONResponse(status_code=200, content=health_status)
    else:
        raise HTTPException(status_code=503, detail=health_status)

@app.get("/metrics")
async def metrics(api_key: dict = Depends(require_api_key)):
    """Prometheus metrics endpoint (requires API key)"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    logger.info(f"Metrics request from {api_key['name']}")
    return PlainTextResponse(harness.export_metrics())

@app.get("/status")
async def status(api_key: dict = Depends(require_api_key)):
    """System status with health (requires API key)"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    health = harness.get_health()
    
    logger.info(f"Status request from {api_key['name']}")
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health": health,
        "protocol": "HTTPS/TLS",
        "authenticated_as": api_key['name']
    }

@app.get("/alerts")
async def alerts(api_key: dict = Depends(require_api_key)):
    """Prometheus alert rules (requires API key)"""
    logger.info(f"Alert rules request from {api_key['name']}")
    return PlainTextResponse(export_alert_rules())

@app.get("/dashboard")
async def dashboard(api_key: dict = Depends(require_api_key)):
    """Grafana dashboard JSON (requires API key)"""
    logger.info(f"Dashboard request from {api_key['name']}")
    return JSONResponse(content={"dashboard": generate_dashboard_json()})

@app.post("/process")
async def process_call(call: dict, api_key: dict = Depends(require_api_key)):
    """Process single call with resilience (requires API key)"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    try:
        logger.info(f"Processing call from {api_key['name']}: {call.get('sid')}")
        result = harness.process_call(call)
        return {
            "success": True,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "authenticated_as": api_key['name']
        }
    except Exception as e:
        logger.error(f"Call processing failed: {e}", extra={"extra_data": {"error": str(e)}})
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/batch")
async def process_batch(batch: dict, api_key: dict = Depends(require_api_key)):
    """Process batch with resilience (requires API key)"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    try:
        calls = batch.get("calls", [])
        if not calls:
            raise ValueError("No calls provided")
        
        logger.info(f"Processing batch from {api_key['name']}: {len(calls)} calls")
        summary = harness.process_batch(calls)
        return {
            "success": True,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "authenticated_as": api_key['name']
        }
    except Exception as e:
        logger.error(f"Batch processing failed: {e}", extra={"extra_data": {"error": str(e)}})
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ledger")
async def get_ledger(api_key: dict = Depends(require_api_key)):
    """Get ledger entries (requires API key)"""
    if harness is None or not harness.harness or not harness.harness.ledger:
        raise HTTPException(status_code=503, detail="Ledger not available")
    
    try:
        logger.info(f"Ledger access from {api_key['name']}")
        entries = harness.harness.ledger.get_entries(limit=10)
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        logger.error(f"Ledger retrieval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/verify")
async def verify_ledger(api_key: dict = Depends(require_api_key)):
    """Verify ledger integrity (requires API key)"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    logger.info(f"Ledger verification from {api_key['name']}")
    return harness.verify_ledger()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 9090))
    
    # TLS configuration
    cert_file = os.getenv("CERT_FILE", "./certs/cert.pem")
    key_file = os.getenv("KEY_FILE", "./certs/key.pem")
    
    # Check if certs exist
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print(f"⚠️  TLS certificates not found at {cert_file} and {key_file}")
        print("Running without TLS. To enable TLS, generate certs with:")
        print('  mkdir -p certs && openssl req -x509 -newkey rsa:4096 -nodes -out certs/cert.pem -keyout certs/key.pem -days 365 -subj "/C=US/ST=State/L=City/O=Iceberg/CN=localhost"')
        use_tls = False
    else:
        use_tls = True
        print(f"✓ Using TLS certificates: {cert_file}, {key_file}")
    
    print(f"✓ API key authentication enabled ({len(api_key_manager.keys)} keys loaded)")
    
    # Run server
    if use_tls:
        uvicorn.run(
            app,
            host="0.0.0.0",  # nosec B104 -- containerized deployment: must bind all interfaces to be reachable from outside the container (see docker-compose.yml)
            port=port,
            ssl_certfile=cert_file,
            ssl_keyfile=key_file,
            log_level="info"
        )
    else:
        uvicorn.run(
            app,
            host="0.0.0.0",  # nosec B104 -- containerized deployment: must bind all interfaces to be reachable from outside the container (see docker-compose.yml)
            port=port,
            log_level="info"
        )
