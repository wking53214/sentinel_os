"""
Iceberg API Server (Resilient) - With error handling, health checks, observability

Uses ResilientHarness instead of plain production_harness
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn
import os
from datetime import datetime

from resilient_harness import ResilientHarness
from operational_resilience import setup_logging, export_alert_rules
from grafana_dashboard import generate_dashboard_json

logger = setup_logging("APIServer")

app = FastAPI(
    title="Iceberg IVR Platform (Resilient)",
    description="Self-healing IVR with operational hardening",
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
    
    harness = ResilientHarness(config)
    logger.info("Resilient harness initialized")

@app.on_event("shutdown")
async def shutdown():
    global harness
    if harness:
        harness.shutdown()

@app.get("/health")
async def health():
    """Detailed health check"""
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
async def metrics():
    """Prometheus metrics endpoint"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    return PlainTextResponse(harness.export_metrics())

@app.get("/status")
async def status():
    """System status with health"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    health = harness.get_health()
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "health": health,
    }

@app.get("/alerts")
async def alerts():
    """Prometheus alert rules"""
    return PlainTextResponse(export_alert_rules())

@app.get("/dashboard")
async def dashboard():
    """Grafana dashboard JSON"""
    return JSONResponse(content={"dashboard": generate_dashboard_json()})

@app.post("/process")
async def process_call(call: dict):
    """Process single call with resilience"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    try:
        result = harness.process_call(call)
        return {
            "success": True,
            "result": result,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Call processing failed: {e}", extra={"extra_data": {"error": str(e)}})
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/batch")
async def process_batch(batch: dict):
    """Process batch with resilience"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    try:
        calls = batch.get("calls", [])
        if not calls:
            raise ValueError("No calls provided")
        
        summary = harness.process_batch(calls)
        return {
            "success": True,
            "summary": summary,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Batch processing failed: {e}", extra={"extra_data": {"error": str(e)}})
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ledger")
async def get_ledger():
    """Get ledger entries"""
    if harness is None or not harness.harness or not harness.harness.ledger:
        raise HTTPException(status_code=503, detail="Ledger not available")
    
    try:
        entries = harness.harness.ledger.get_entries(limit=10)
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        logger.error(f"Ledger retrieval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/verify")
async def verify_ledger():
    """Verify ledger integrity"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    return harness.verify_ledger()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 9090))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
