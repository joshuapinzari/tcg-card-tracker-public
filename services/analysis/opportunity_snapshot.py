"""Atomic, restart-persistent display snapshots for expensive opportunity scans.

The UI NEVER computes a scan on a cache miss. Builds are explicit (CLI/button) or
part of the daily pipeline. Price-floor/era/set filters operate on saved scores,
not on a newly ranked, smaller universe. JSON is used instead of unpickling data.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import uuid

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def _database_namespace() -> str:
    import getpass
    identity = [os.getenv("TCG_DATABASE_NAME", "tcg_card_tracker_public"),
                os.getenv("PGHOST", ""), os.getenv("PGPORT", ""),
                os.getenv("PGUSER", getpass.getuser())]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]


CACHE_DIR = ROOT / "data" / "cache" / _database_namespace()
SCHEMA_VERSION = 1
MAX_AGE = timedelta(hours=26)
LOGGER = logging.getLogger(__name__)
# Include executable scoring code as well as frozen parameters. UI-only edits do
# not invalidate otherwise identical model outputs.
MODEL_FILES = (
    "models/card_rank_v3.json", "models/card_rank_v4_hybrid.json",
    "services/analysis/opportunity_model.py", "services/analysis/research_rank.py",
    "services/analysis/hybrid_rank.py", "services/analysis/regime_overheating_signal.py",
    "database/opportunity_scanner.py", "pokemon_eras.py",
)


def model_fingerprint(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for name in MODEL_FILES:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def snapshot_path(cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / "opportunities-v1.json.gz"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def revision(cache_dir: Path = CACHE_DIR) -> str:
    try:
        return (cache_dir / "opportunities.revision").read_text(encoding="utf-8")
    except FileNotFoundError:
        return "initial"


def invalidate_snapshot(reason: str, *, cache_dir: Path = CACHE_DIR) -> None:
    """Advisory invalidation: a cache failure must not discard collected data.

    Collectors/research builders call this when they write inputs. Direct manual
    database edits need an explicit rebuild. Saved results remain readable, with
    a stale warning; they are not presented as current Card Analysis evidence.
    """
    try:
        token = json.dumps({"revision": uuid.uuid4().hex, "reason": str(reason),
                            "at": datetime.now(timezone.utc).isoformat()})
        _atomic_write(cache_dir / "opportunities.revision", token.encode())
    except OSError:
        LOGGER.warning("Could not mark opportunity snapshot stale; rebuild it after updating data.",
                       exc_info=True)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@dataclass
class Snapshot:
    frame: pd.DataFrame
    metadata: dict
    status: str
    reason: str = ""

    def card(self, product_id: int, printing: str | None) -> dict | None:
        """Never lend another finish's research to the requested card."""
        if self.status != "ready" or self.frame.empty or not printing:
            return None
        if "printing" not in self.frame or "product_id" not in self.frame:
            return None
        match = self.frame[(self.frame["product_id"] == int(product_id))
                           & (self.frame["printing"] == printing)]
        return None if match.empty else match.iloc[0].to_dict()


def load_snapshot(*, cache_dir: Path = CACHE_DIR, fingerprint: str | None = None,
                  now: datetime | None = None) -> Snapshot:
    """Read only: no provider calls, DB queries or hidden scanner fallback."""
    path = snapshot_path(cache_dir)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            payload = json.load(source)
        metadata = payload["metadata"]
        if metadata["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported snapshot schema; rebuild it.")
        if metadata["model_fingerprint"] != (fingerprint or model_fingerprint()):
            return Snapshot(pd.DataFrame(), metadata, "incompatible", "Scoring code/specification changed.")
        frame = pd.DataFrame(payload["rows"], columns=payload["columns"])
        for col in metadata.get("numeric_columns", []):
            if col in frame:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        for col in metadata.get("datetime_columns", []):
            if col in frame:
                frame[col] = pd.to_datetime(frame[col], errors="coerce")
        built = datetime.fromisoformat(metadata["built_at"])
        if built.tzinfo is None:
            raise ValueError("Snapshot timestamp has no timezone.")
        current = now or datetime.now(timezone.utc)
        if metadata.get("source_revision", "initial") != revision(cache_dir):
            return Snapshot(frame, metadata, "stale", "Source data was updated after this build.")
        if current - built > MAX_AGE:
            return Snapshot(frame, metadata, "stale", "This snapshot is more than 26 hours old.")
        return Snapshot(frame, metadata, "ready")
    except FileNotFoundError:
        return Snapshot(pd.DataFrame(), {}, "missing", "No saved scan yet.")
    except (OSError, EOFError, KeyError, TypeError, ValueError) as error:
        return Snapshot(pd.DataFrame(), {}, "invalid", str(error))


def save_snapshot(frame: pd.DataFrame, *, metadata: dict, source_revision: str,
                  cache_dir: Path = CACHE_DIR, fingerprint: str | None = None,
                  now: datetime | None = None) -> Snapshot:
    """Publish a complete result atomically; never replace good data mid-build."""
    if source_revision != revision(cache_dir):
        raise RuntimeError("Inputs changed during the scan. Previous snapshot kept; rebuild after updates finish.")
    numeric = [c for c in frame if pd.api.types.is_numeric_dtype(frame[c])]
    datetime_cols = [c for c in frame if pd.api.types.is_datetime64_any_dtype(frame[c])]
    meta = {**metadata, "schema_version": SCHEMA_VERSION,
            "model_fingerprint": fingerprint or model_fingerprint(),
            "source_revision": source_revision,
            "built_at": (now or datetime.now(timezone.utc)).isoformat(),
            "numeric_columns": numeric, "datetime_columns": datetime_cols, "cards": len(frame)}
    payload = _json_safe({"metadata": meta, "columns": list(frame.columns),
                          "rows": frame.to_dict("records")})
    encoded = gzip.compress(json.dumps(payload, allow_nan=False, separators=(",", ":")).encode(),
                            compresslevel=3, mtime=0)
    # Serialisation may take time: recheck before publishing.
    if source_revision != revision(cache_dir):
        raise RuntimeError("Inputs changed during serialisation. Previous snapshot kept.")
    _atomic_write(snapshot_path(cache_dir), encoded)
    return load_snapshot(cache_dir=cache_dir, fingerprint=fingerprint, now=now)


# Writers and builders coordinate across Mac terminal/Streamlit processes. A lock
# file is not a "job is running" flag: the OS releases flock after a crash.
_INPUT_MUTEX = threading.RLock()
_INPUT_LOCAL = threading.local()


@contextmanager
def input_lock(*, cache_dir: Path = CACHE_DIR, blocking: bool = True):
    import fcntl
    if not _INPUT_MUTEX.acquire(blocking=blocking):
        raise RuntimeError("Market data is being updated; rebuild when that job finishes.")
    try:
        if getattr(_INPUT_LOCAL, "depth", 0):
            _INPUT_LOCAL.depth += 1
            try:
                yield
            finally:
                _INPUT_LOCAL.depth -= 1
        else:
            cache_dir.mkdir(parents=True, exist_ok=True)
            with (cache_dir / "opportunities.inputs.lock").open("a") as handle:
                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                try:
                    fcntl.flock(handle, flags)
                except BlockingIOError as error:
                    raise RuntimeError("Market data is being updated; rebuild when that job finishes.") from error
                _INPUT_LOCAL.depth = 1
                try:
                    yield
                finally:
                    _INPUT_LOCAL.depth = 0
                    fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        _INPUT_MUTEX.release()


def snapshot_input_writer(function):
    """Invalidate before and after supported batch writes, including failures."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with input_lock():
            invalidate_snapshot(f"{function.__module__}.{function.__name__} starting")
            try:
                return function(*args, **kwargs)
            finally:
                invalidate_snapshot(f"{function.__module__}.{function.__name__} finished")
    return wrapped
