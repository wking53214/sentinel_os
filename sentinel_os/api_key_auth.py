"""
API Key Authentication - Secure endpoint access

Simple API key validation for production
"""

import os
import secrets
import hmac
import time
import threading
from collections import deque, defaultdict
from typing import Optional, Dict
from datetime import datetime, timezone
from fastapi import HTTPException, Header, Request

class APIKeyManager:
    """Manage API keys"""
    
    def __init__(self):
        self.keys = {}
        self._load_keys_from_env()
    
    def _load_keys_from_env(self):
        """Load API keys from environment variables"""
        
        # Format: ICEBERG_API_KEYS=key1:name1,key2:name2
        keys_env = os.getenv("ICEBERG_API_KEYS", "")
        
        if not keys_env:
            if os.getenv("ICEBERG_REQUIRE_API_KEYS", "").lower() in ("1", "true", "yes"):
                raise RuntimeError(
                    "ICEBERG_API_KEYS is not set and ICEBERG_REQUIRE_API_KEYS=true -- "
                    "refusing to start with a silently-generated development key. "
                    "Set ICEBERG_API_KEYS=key:name explicitly."
                )
            # Generate a default development key
            dev_key = self._generate_key()
            self.keys[dev_key] = {
                "name": "development",
                "created": datetime.now(timezone.utc).isoformat(),
                "enabled": True
            }
            print("⚠️  No API keys configured. Generated development key:")
            print(f"    ICEBERG_API_KEYS={dev_key}:development")
            return
        
        # Parse keys from env
        for key_pair in keys_env.split(","):
            if ":" not in key_pair:
                continue
            
            key, name = key_pair.split(":", 1)
            self.keys[key.strip()] = {
                "name": name.strip(),
                "created": datetime.now(timezone.utc).isoformat(),
                "enabled": True
            }
        
        print(f"✓ Loaded {len(self.keys)} API keys")
    
    def _generate_key(self) -> str:
        """Generate a random API key"""
        return f"icebergkey_{secrets.token_urlsafe(32)}"
    
    def _find_key_constant_time(self, api_key: str) -> Optional[str]:
        """Compare api_key against every configured key with hmac.compare_digest,
        never short-circuiting, so response time doesn't leak how much of the
        key matched or which key (if any) it matched."""
        match = None
        api_key_bytes = api_key.encode("utf-8")
        for candidate in self.keys:
            if hmac.compare_digest(candidate.encode("utf-8"), api_key_bytes):
                match = candidate
            # deliberately no early return/break -- keep comparing all keys
        return match

    def validate_key(self, api_key: Optional[str]) -> Dict:
        """Validate an API key"""
        
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing API key")
        
        matched = self._find_key_constant_time(api_key)
        if matched is None:
            raise HTTPException(status_code=403, detail="Invalid API key")

        key_info = self.keys[matched]
        
        if not key_info.get("enabled", False):
            raise HTTPException(status_code=403, detail="API key is disabled")
        
        return key_info
    
    def get_key_info(self, api_key: str) -> Dict:
        """Get info about a key"""
        if api_key not in self.keys:
            return {"valid": False}
        
        return {
            "valid": True,
            "name": self.keys[api_key]["name"],
            "created": self.keys[api_key]["created"],
            "enabled": self.keys[api_key]["enabled"]
        }

class RateLimiter:
    """Simple in-memory fixed-window-ish rate limiter (sliding via deque).

    Not distributed -- fine for a single-process deployment; a multi-replica
    deployment would need a shared store (e.g. Redis) instead. Documented as
    a known limitation rather than silently pretending this scales.
    """

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, identity: str) -> None:
        """Raise HTTPException(429) if `identity` has exceeded the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[identity]
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                retry_after = max(0.0, self.window_seconds - (now - q[0]))
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )
            q.append(now)


rate_limiter = RateLimiter(
    max_requests=int(os.getenv("ICEBERG_RATE_LIMIT_MAX", "60")),
    window_seconds=float(os.getenv("ICEBERG_RATE_LIMIT_WINDOW_SECONDS", "60")),
)


# Global API key manager
api_key_manager = APIKeyManager()

def require_api_key(request: Request, x_api_key: str = Header(None)) -> Dict:
    """FastAPI dependency: require a valid API key AND enforce a per-IP
    rate limit on the *attempt*, before that key is known to be valid.

    Previously the identity rate-limited was the caller-supplied key
    itself when one was present, falling back to IP only when the
    header was missing. Since a caller can supply a different string
    on every request at zero cost, that gave a brute-force attacker a
    fresh quota on every guess -- confirmed live: 500 distinct guessed
    keys, 0 throttled, plus one unbounded in-memory bucket per guess
    (nothing evicts a bucket that's never checked again). Identity is
    now always the connecting IP for this pre-auth check, which is the
    actual limiting resource an attacker can't mint for free.

    This limits ATTEMPTS, not validated keys -- callers behind a
    shared NAT/proxy share one bucket here, which is a deliberate
    trade for closing the brute-force bypass. A separate per-key quota
    for legitimate authenticated traffic is a reasonable addition, but
    is a distinct feature, not part of this fix.
    """
    identity = request.client.host if request.client else "unknown"
    rate_limiter.check(identity)
    return api_key_manager.validate_key(x_api_key)
