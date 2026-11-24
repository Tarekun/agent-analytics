"""The common record schema shared by every harness.

Every record has exactly these keys, in this order. A harness that cannot
provide a field leaves it as None, so records from different harnesses are
directly comparable.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime

FIELDS: tuple[str, ...] = (
    # identity
    "session_id",
    "harness",
    "transcript_path",
    "title",
    "cwd",
    "git_branch",              # claude-code only
    "harness_version",
    "harness_version_source",  # "transcript" (written by the session) | "installed" (version installed at collection time)
    "entrypoint",              # claude-code only
    "session_format_version",  # pi only: on-disk session format version
    "parent_session",          # pi only: session this one was forked/cloned from
    # time
    "started_at",
    "ended_at",
    "duration_seconds",
    # activity
    "user_prompts",
    "assistant_messages",
    "tool_calls_total",
    "tool_calls",
    "tool_errors",
    "models",
    "providers",               # pi only: {provider: assistant message count}
    # config the session ran with
    "effort",                  # {thinking effort level: assistant message count}
    "instructions",            # {instruction file path: [content hash, ...]} (several if edited mid-session)
    "skills",                  # {invoked skill: {"hashes": [...], "invocations": n}}
    "mcp_servers",             # {server: {"calls": n, "tools": {called tool: definition hash}}}
    # usage of the main model calls
    "tokens",                  # {input, output, cache_read, cache_creation}
    "cost",                    # pi only: {input, output, cache_read, cache_creation, total}
    "aux_usage",               # pi only: usage outside the main model calls (compaction, branch summaries, tools, ...)
)


def new_record(**values) -> dict:
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise KeyError(f"unknown record fields: {sorted(unknown)}")
    return {field: values.get(field) for field in FIELDS}


def content_hash(value) -> str:
    """Short, stable hash of a string or a JSON-serialisable value."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def add_unique(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def split_mcp_tool(tool_name: str) -> tuple[str, str] | None:
    """Split "mcp__<server>__<tool>" into (server, tool)."""
    parts = tool_name.split("__", 2)
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "mcp" and parts[1] and parts[2] else None


def tool_definition_hash(name, description, input_schema) -> str:
    """Hash of a tool definition as the model received it, the same for every harness."""
    return content_hash({"name": name, "description": description, "input_schema": input_schema})


def mcp_servers_field(calls: dict[str, Counter], definitions: dict[str, str]) -> dict:
    """The mcp_servers field from {server: {tool: calls}} and {full tool name: definition hash}."""
    return {
        server: {
            "calls": sum(tools.values()),
            "tools": {tool: definitions.get(f"mcp__{server}__{tool}") for tool in sorted(tools)},
        }
        for server, tools in sorted(calls.items())
    }


def parse_ts(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def time_fields(timestamps: list[datetime]) -> dict:
    if not timestamps:
        return {"started_at": None, "ended_at": None, "duration_seconds": None}
    start, end = min(timestamps), max(timestamps)
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": round((end - start).total_seconds(), 3),
    }
