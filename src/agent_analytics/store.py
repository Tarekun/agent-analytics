"""Tiny JSON-file store. Records are keyed by "<harness>:<session_id>" and upserted."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_analytics.record import SessionRecord

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


def save_record(path: Path, record: SessionRecord) -> None:
    data = load(path)
    entry = {**record.to_dict(), "recorded_at": datetime.now(timezone.utc).isoformat()}
    data["sessions"][f"{record.harness}:{record.session_id}"] = entry

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)  # atomic on POSIX
