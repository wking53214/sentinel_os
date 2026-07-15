import json
import hashlib
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List

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
        # Atomic: a crash mid-write must never leave a torn manifest,
        # because the manifest is what a restart resumes from. Write to
        # a temp file in the same directory, fsync, then rename over
        # the old one -- rename is atomic on POSIX, so the manifest on
        # disk is always either the previous complete one or the new
        # complete one, never a partial.
        fd, tmp_path = tempfile.mkstemp(
            dir=self.base_dir, prefix=".manifest_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._manifest_path())
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

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
    MANIFEST_VERSION = 1

    def __init__(self, adapter: StorageAdapter, seed: str = "815"):
        self.adapter = adapter
        self.lock = threading.RLock()
        self._seed = seed
        self._genesis = hashlib.sha256(seed.encode()).hexdigest()
        self._state = self._resume_state()

    def _resume_state(self) -> EngineState:
        """Resume from the manifest on restart.

        The old constructor always started at (0, genesis), so a
        restarted manager would overwrite chunk 0 and orphan the entire
        existing chain. Order of trust: (1) manifest, if present;
        (2) no manifest but chunks on disk (pre-manifest directory, or
        a deleted manifest) -- recompute the chain from the chunks
        rather than clobbering them; the next flush re-establishes a
        manifest. verify() will still flag the missing-manifest state.
        (3) empty directory -- genesis.
        """
        if self.adapter.has_manifest():
            m = json.loads(self.adapter.read_manifest())
            return EngineState(int(m["last_chunk"]) + 1, m["head_hash"])
        chunks = sorted(self.adapter.list_chunks())
        if chunks:
            head = self._recompute_head(chunks)
            return EngineState(chunks[-1] + 1, head)
        return EngineState(0, self._genesis)

    def _recompute_head(self, chunks: List[int]) -> str:
        prev_hash = self._genesis
        for cid in chunks:
            raw = self.adapter.read_chunk(cid)
            canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
            composite = f"{cid}:{canonical}:{prev_hash}".encode()
            prev_hash = hashlib.sha256(composite).hexdigest()
        return prev_hash

    def flush(self, ledger_slice: List[Dict[str, Any]]) -> str:
        payload = json.dumps(ledger_slice, sort_keys=True, separators=(",", ":"))
        with self.lock:
            idx, prev = self._state.chunk_index, self._state.previous_hash
            composite = f"{idx}:{payload}:{prev}".encode()
            new_hash = hashlib.sha256(composite).hexdigest()
            self.adapter.write_chunk(idx, payload)
            manifest = Manifest(
                version=self.MANIFEST_VERSION, last_chunk=idx, head_hash=new_hash
            )
            self.adapter.write_manifest(
                json.dumps(
                    {
                        "version": manifest.version,
                        "last_chunk": manifest.last_chunk,
                        "head_hash": manifest.head_hash,
                    },
                    sort_keys=True,
                )
            )
            self._state = EngineState(idx + 1, new_hash)
            return new_hash

    def verify(self, mode: str = "strict") -> Dict[str, Any]:
        """Recompute the chain from chunk contents and COMPARE it.

        The old implementation walked the chain, recomputed every hash,
        and then returned ok=True unconditionally -- it exercised the
        hashes without ever comparing them to anything, which is not
        verification. Now the recomputed head is checked against the
        manifest head (content tampering), the chunk sequence is
        checked for gaps (deletion), and the manifest's last_chunk is
        checked against what's on disk (truncation / phantom appends).
        Always returns a report; ok=False carries the violations.
        """
        with self.lock:
            chunks = sorted(self.adapter.list_chunks())
            prev_hash = self._genesis
            last_good = -1
            for cid in chunks:
                raw = self.adapter.read_chunk(cid)
                canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
                composite = f"{cid}:{canonical}:{prev_hash}".encode()
                prev_hash = hashlib.sha256(composite).hexdigest()
                last_good = cid

            report: Dict[str, Any] = {
                "ok": True,
                "last_good_index": last_good,
                "computed_head": prev_hash,
                "violations": [],
            }

            if chunks != list(range(len(chunks))):
                report["ok"] = False
                report["violations"].append(
                    f"chunk sequence has gaps or does not start at 0: {chunks}"
                )

            if self.adapter.has_manifest():
                m = json.loads(self.adapter.read_manifest())
                report["manifest_head"] = m.get("head_hash")
                report["manifest_last_chunk"] = m.get("last_chunk")
                if m.get("head_hash") != prev_hash:
                    report["ok"] = False
                    report["violations"].append(
                        "computed head does not match manifest head "
                        "(chunk content was altered after commit)"
                    )
                if chunks and m.get("last_chunk") != chunks[-1]:
                    report["ok"] = False
                    report["violations"].append(
                        "manifest last_chunk does not match chunks on disk "
                        "(truncated tail or unrecorded appends)"
                    )
            elif chunks:
                report["ok"] = False
                report["violations"].append(
                    "chunks present but manifest missing: chain cannot be attested"
                )

            return report
