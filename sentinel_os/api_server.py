"""
Iceberg API Server - Production FastAPI endpoint

Exposes:
- /health - Health check
- /metrics - Prometheus metrics
- /process - Process single call
- /batch - Process batch of calls
- /status - System status
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn
import logging
from datetime import datetime
import os

from production_harness import IcebergProductionHarness

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("iceberg")

# FastAPI app
app = FastAPI(
    title="Iceberg IVR Platform",
    description="Self-healing IVR system with governance",
    version="1.0.0"
)

# Global harness instance
harness = None

@app.on_event("startup")
async def startup():
    """Initialize harness on startup"""
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
    
    harness = IcebergProductionHarness(config)
    logger.info("Iceberg harness initialized")

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    global harness
    if harness:
        harness.shutdown()
        logger.info("Iceberg harness shutdown")

@app.get("/health")
async def health():
    """Health check endpoint"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "iceberg",
        "version": "1.0.0"
    }

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    return PlainTextResponse(harness.export_metrics())

@app.get("/status")
async def status():
    """System status endpoint"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    summary = harness.metrics.get_summary()
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": summary,
        "ledger_connected": harness.ledger is not None,
        "claude_connected": harness.claude_decider is not None,
        "twilio_connected": harness.twilio_adapter is not None,
    }

@app.post("/process")
async def process_call(call: dict):
    """Process single Twilio call"""
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
        logger.error(f"Call processing failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/batch")
async def process_batch(calls: dict):
    """Process batch of calls"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    try:
        call_list = calls.get("calls", [])
        if not call_list:
            raise ValueError("No calls provided")
        
        summary = harness.process_batch(call_list)
        return {
            "success": True,
            "summary": summary,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/ledger")
async def get_ledger():
    """Get recent ledger entries"""
    if harness is None or not harness.ledger:
        raise HTTPException(status_code=503, detail="Ledger not available")
    
    try:
        entries = harness.ledger.get_entries(limit=10)
        return {
            "entries": entries,
            "count": len(entries)
        }
    except Exception as e:
        logger.error(f"Ledger retrieval failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/verify")
async def verify_ledger():
    """Verify ledger integrity"""
    if harness is None:
        raise HTTPException(status_code=503, detail="Harness not initialized")
    
    try:
        result = harness.verify_ledger()
        return {
            "ledger_available": harness.ledger is not None,
            "verification": result if harness.ledger else None
        }
    except Exception as e:
        logger.error(f"Ledger verification failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 9090))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
