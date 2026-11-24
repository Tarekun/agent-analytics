"""Tiny JSON-file store. Records are keyed by "<harness>:<session_id>" and upserted.

Writes hold an exclusive lock on "<store>.lock", so hooks from sessions that end
at the same time do not overwrite each other's records.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no locking
    fcntl = None

DEFAULT_DB = Path.home() / ".agent-analytics" / "sessions.json"


def db_path(override: str | None = None) -> Path:
    return Path(override or os.environ.get("AGENT_ANALYTICS_DB") or DEFAULT_DB).expanduser()


def load(path: Path) -> dict:
    if not path.exists():
        return {"sessions": {}}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("sessions", {})
    return data


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("w") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save_record(path: Path, record: dict) -> None:
    record = {**record, "recorded_at": datetime.now(timezone.utc).isoformat()}
    with _locked(path):
        data = load(path)
        data["sessions"][f"{record['harness']}:{record['session_id']}"] = record
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        tmp.replace(path)  # atomic on POSIX
