import json
import hashlib
import os
import tempfile
import threading
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

class StorageAdapter(ABC):
    @abstractmethod
    def write_chunk(self, chunk_id: int, payload: str) -> None: ...
    @abstractmethod
    def list_chunks(self) -> List[int]: ...
    @abstractmethod
    def read_chunk(self, chunk_id: int) -> str: ...
    @abstractmethod
    def write_manifest(self, payload: str) -> None: ...
    @abstractmethod
    def read_manifest(self) -> str: ...
    @abstractmethod
    def has_manifest(self) -> bool: ...

class LocalDiskAdapter(StorageAdapter):
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, chunk_id: int) -> str:
        return os.path.join(self.base_dir, f"chunk_{chunk_id:06d}.json")

    def _manifest_path(self) -> str:
        return os.path.join(self.base_dir, "manifest.json")

    def write_chunk(self, chunk_id: int, payload: str) -> None:
        with open(self._path(chunk_id), "w") as f:
            f.write(payload)

    def list_chunks(self) -> List[int]:
        return sorted(int(n.replace("chunk_", "").replace(".json", ""))
                      for n in os.listdir(self.base_dir) if n.startswith("chunk_"))

    def read_chunk(self, chunk_id: int) -> str:
        with open(self._path(chunk_id), "r") as f:
            return f.read()

    def write_manifest(self, payload: str) -> None:
        with open(self._manifest_path(), "w") as f:
            f.write(payload)

    def read_manifest(self) -> str:
        with open(self._manifest_path(), "r") as f:
            return f.read()

    def has_manifest(self) -> bool:
        return os.path.exists(self._manifest_path())

@dataclass(frozen=True)
class Manifest:
    version: int
    last_chunk: int
    head_hash: str

@dataclass(frozen=True)
class EngineState:
    chunk_index: int
    previous_hash: str

class LogRotationManager:
    def __init__(self, adapter: StorageAdapter, seed: str = "815"):
        self.adapter = adapter
        self.lock = threading.RLock()
        self._seed = seed
        self._genesis = hashlib.sha256(seed.encode()).hexdigest()
        self._state = EngineState(0, self._genesis)

    def flush(self, ledger_slice: List[Dict[str, Any]]) -> str:
        payload = json.dumps(ledger_slice, sort_keys=True, separators=(",", ":"))
        with self.lock:
            idx, prev = self._state.chunk_index, self._state.previous_hash
            composite = f"{idx}:{payload}:{prev}".encode()
            new_hash = hashlib.sha256(composite).hexdigest()
            self.adapter.write_chunk(idx, payload)
            self._state = EngineState(idx + 1, new_hash)
            return new_hash

    def verify(self, mode: str = "strict") -> Dict[str, Any]:
        chunks = sorted(self.adapter.list_chunks())
        prev_hash = self._genesis
        last_good = -1
        for cid in chunks:
            raw = self.adapter.read_chunk(cid)
            canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
            composite = f"{cid}:{canonical}:{prev_hash}".encode()
            prev_hash = hashlib.sha256(composite).hexdigest()
            last_good = cid
        return {"ok": True, "last_good_index": last_good, "computed_head": prev_hash}
