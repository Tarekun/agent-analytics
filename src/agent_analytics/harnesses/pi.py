"""Pi coding agent harness (github.com/earendil-works/pi).

Pi writes one JSONL file per session at
    <sessions dir>/--<cwd-path>--/<timestamp>_<session-id>.jsonl
where <sessions dir> is $PI_CODING_AGENT_SESSION_DIR, else
$PI_CODING_AGENT_DIR/sessions, else ~/.pi/agent/sessions.

The first line is a header: {"type": "session", "version": 3, "id", "timestamp", "cwd"}.
Every other line is an entry with id/parentId/timestamp; entries form a tree,
and all branches are counted here.

Pi does not write its own version anywhere in the session, so harness_version
comes from `pi --version` at collection time (harness_version_source="installed").
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from agent_analytics.errors import SessionNotFound
from agent_analytics.record import new_record, parse_ts, time_fields

NAME = "pi"

TOKEN_KEYS = {"input": "input", "output": "output", "cacheRead": "cache_read", "cacheWrite": "cache_creation"}


def sessions_dir() -> Path:
    if d := os.environ.get("PI_CODING_AGENT_SESSION_DIR"):
        return Path(d).expanduser()
    base = os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent"
    return Path(base).expanduser() / "sessions"


def _transcripts() -> list[Path]:
    root = sessions_dir()
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*.jsonl") if p.is_file()]


def _header(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            first = json.loads(fh.readline() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return first if isinstance(first, dict) and first.get("type") == "session" else {}


def session_id_from_path(path: Path) -> str:
    # Prefer the header id; fall back to the part of the filename after "<timestamp>_".
    return _header(path).get("id") or path.stem.rsplit("_", 1)[-1]


def find_latest_session() -> str | None:
    files = _transcripts()
    if not files:
        return None
    return session_id_from_path(max(files, key=lambda p: p.stat().st_mtime))


def find_transcript(session_id: str) -> Path:
    files = _transcripts()
    # fast path: filename ends with the id
    for p in files:
        if p.stem == session_id or p.stem.endswith("_" + session_id):
            return p
    for p in files:
        if _header(p).get("id") == session_id:
            return p
    raise SessionNotFound(f"no pi session {session_id!r} under {sessions_dir()}")


@lru_cache(maxsize=1)
def installed_version() -> str | None:
    exe = shutil.which("pi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\d+\.\d+\.\d+[\w.+-]*", out.stdout + out.stderr)
    return m.group(0) if m else None


def _add_usage(tokens: Counter, cost: Counter, usage) -> None:
    if not isinstance(usage, dict):
        return
    for src, dst in TOKEN_KEYS.items():
        tokens[dst] += usage.get(src) or 0
    c = usage.get("cost")
    if isinstance(c, dict):
        for src, dst in {**TOKEN_KEYS, "total": "total"}.items():
            cost[dst] += c.get(src) or 0


def collect(session_id: str, transcript: Path | None = None) -> dict:
    path = Path(transcript) if transcript else find_transcript(session_id)
    if not path.is_file():
        raise SessionNotFound(f"transcript not found: {path}")

    timestamps: list[datetime] = []
    tool_calls: Counter[str] = Counter()
    models: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    tokens = Counter(input=0, output=0, cache_read=0, cache_creation=0)
    cost = Counter(input=0, output=0, cache_read=0, cache_creation=0, total=0)
    aux_tokens = Counter(input=0, output=0, cache_read=0, cache_creation=0)
    aux_cost = Counter(input=0, output=0, cache_read=0, cache_creation=0, total=0)
    user_prompts = assistant_messages = tool_errors = 0
    header: dict = {}
    title = None

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            etype = entry.get("type")
            if ts := parse_ts(entry.get("timestamp")):
                timestamps.append(ts)

            if etype == "session":
                header = entry
            elif etype == "session_info":
                title = entry.get("name") or title
            elif etype in ("usage", "compaction", "branch_summary"):
                _add_usage(aux_tokens, aux_cost, entry.get("usage"))
            elif etype == "message":
                msg = entry.get("message") or {}
                role = msg.get("role")
                if role == "user":
                    user_prompts += 1
                elif role == "assistant":
                    assistant_messages += 1
                    if model := msg.get("model"):
                        models[model] += 1
                    if provider := msg.get("provider"):
                        providers[provider] += 1
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "toolCall":
                            tool_calls[block.get("name", "?")] += 1
                    _add_usage(tokens, cost, msg.get("usage"))
                elif role == "toolResult":
                    if msg.get("isError"):
                        tool_errors += 1
                    # nested model work done by a tool is not part of the main calls
                    _add_usage(aux_tokens, aux_cost, msg.get("usage"))

    version = installed_version()
    return new_record(
        session_id=header.get("id") or session_id,
        harness=NAME,
        transcript_path=str(path),
        title=title,
        cwd=header.get("cwd"),
        harness_version=version,
        harness_version_source="installed" if version else None,
        session_format_version=header.get("version"),
        parent_session=header.get("parentSession"),
        **time_fields(timestamps),
        user_prompts=user_prompts,
        assistant_messages=assistant_messages,
        tool_calls_total=sum(tool_calls.values()),
        tool_calls=dict(tool_calls.most_common()),
        tool_errors=tool_errors,
        models=dict(models.most_common()),
        providers=dict(providers.most_common()),
        tokens=dict(tokens),
        cost={k: round(v, 6) for k, v in cost.items()},
        aux_usage={"tokens": dict(aux_tokens), "cost": {k: round(v, 6) for k, v in aux_cost.items()}},
    )
