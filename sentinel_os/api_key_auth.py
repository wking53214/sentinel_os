"""
API Key Authentication - Secure endpoint access

Simple API key validation for production
"""

import os
import secrets
import hashlib
from typing import Optional, Dict
from datetime import datetime
from fastapi import HTTPException, Header

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
            # Generate a default development key
            dev_key = self._generate_key()
            self.keys[dev_key] = {
                "name": "development",
                "created": datetime.utcnow().isoformat(),
                "enabled": True
            }
            print(f"⚠️  No API keys configured. Generated development key:")
            print(f"    ICEBERG_API_KEYS={dev_key}:development")
            return
        
        # Parse keys from env
        for key_pair in keys_env.split(","):
            if ":" not in key_pair:
                continue
            
            key, name = key_pair.split(":", 1)
            self.keys[key.strip()] = {
                "name": name.strip(),
                "created": datetime.utcnow().isoformat(),
                "enabled": True
            }
        
        print(f"✓ Loaded {len(self.keys)} API keys")
    
    def _generate_key(self) -> str:
        """Generate a random API key"""
        return f"icebergkey_{secrets.token_urlsafe(32)}"
    
    def validate_key(self, api_key: Optional[str]) -> Dict:
        """Validate an API key"""
        
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing API key")
        
        if api_key not in self.keys:
            raise HTTPException(status_code=403, detail="Invalid API key")
        
        key_info = self.keys[api_key]
        
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

# Global API key manager
api_key_manager = APIKeyManager()

def require_api_key(x_api_key: str = Header(None)) -> Dict:
    """FastAPI dependency: require valid API key"""
    return api_key_manager.validate_key(x_api_key)
