"""Claude Code harness.

Claude Code writes one JSONL transcript per session at
    $CLAUDE_CONFIG_DIR/projects/<project-slug>/<session-id>.jsonl
(CLAUDE_CONFIG_DIR defaults to ~/.claude).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from agent_analytics.errors import SessionNotFound
from agent_analytics.record import new_record, parse_ts, time_fields

NAME = "claude-code"


def projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / "projects"


def _transcripts() -> list[Path]:
    root = projects_dir()
    if not root.is_dir():
        return []
    return [p for p in root.glob("*/*.jsonl") if p.is_file()]


def find_latest_session() -> str | None:
    """Return the id of the most recently modified session transcript."""
    files = _transcripts()
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime).stem


def session_id_from_path(path: Path) -> str:
    return Path(path).stem


def find_transcript(session_id: str) -> Path:
    for p in _transcripts():
        if p.stem == session_id:
            return p
    raise SessionNotFound(f"no Claude Code transcript for session {session_id!r} under {projects_dir()}")


def _is_human_prompt(entry: dict) -> bool:
    """A user entry typed by a person (not a tool result or meta message)."""
    if entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False


def collect(session_id: str, transcript: Path | None = None) -> dict:
    path = Path(transcript) if transcript else find_transcript(session_id)
    if not path.is_file():
        raise SessionNotFound(f"transcript not found: {path}")

    timestamps: list[datetime] = []
    tool_calls: Counter[str] = Counter()
    models: Counter[str] = Counter()
    tokens = Counter(input=0, output=0, cache_read=0, cache_creation=0)
    seen_message_ids: set[str] = set()
    user_prompts = 0
    assistant_turns = 0
    tool_errors = 0
    meta: dict = {}

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = entry.get("type")

            if etype == "ai-title":
                meta["title"] = entry.get("aiTitle")
                continue
            if etype not in ("user", "assistant"):
                continue

            for key in ("cwd", "gitBranch", "version", "entrypoint"):
                if entry.get(key):
                    meta[key] = entry[key]
            if ts := parse_ts(entry.get("timestamp")):
                timestamps.append(ts)

            message = entry.get("message") or {}
            content = message.get("content")

            if etype == "user":
                if _is_human_prompt(entry):
                    user_prompts += 1
                if isinstance(content, list):
                    tool_errors += sum(
                        1 for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")
                    )
                continue

            # assistant: one API response may be split across several entries
            # sharing the same message id (one per content block) and repeating
            # the same usage block, so count usage once per message id.
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls[block.get("name", "?")] += 1

            msg_id = message.get("id") or entry.get("requestId") or entry.get("uuid")
            if msg_id in seen_message_ids:
                continue
            seen_message_ids.add(msg_id)
            assistant_turns += 1
            if model := message.get("model"):
                models[model] += 1
            usage = message.get("usage") or {}
            tokens["input"] += usage.get("input_tokens", 0) or 0
            tokens["output"] += usage.get("output_tokens", 0) or 0
            tokens["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
            tokens["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0

    return new_record(
        session_id=session_id,
        harness=NAME,
        transcript_path=str(path),
        title=meta.get("title"),
        cwd=meta.get("cwd"),
        git_branch=meta.get("gitBranch"),
        harness_version=meta.get("version"),
        harness_version_source="transcript" if meta.get("version") else None,
        entrypoint=meta.get("entrypoint"),
        **time_fields(timestamps),
        user_prompts=user_prompts,
        assistant_messages=assistant_turns,
        tool_calls_total=sum(tool_calls.values()),
        tool_calls=dict(tool_calls.most_common()),
        tool_errors=tool_errors,
        models=dict(models.most_common()),
        tokens=dict(tokens),
    )
