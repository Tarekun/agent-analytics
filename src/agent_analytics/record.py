"""The common record schema shared by every harness.

Every harness returns a SessionRecord. Fields without a default must be set by
every harness (possibly to None when a session lacks the data); fields that
default to None are ones only some harnesses can provide. Records from
different harnesses therefore have the same keys, in the same order, and are
directly comparable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict


class Tokens(TypedDict):
    input: int
    output: int
    cache_read: int
    cache_creation: int


class Cost(TypedDict):
    input: float
    output: float
    cache_read: float
    cache_creation: float
    total: float


class AuxUsage(TypedDict):
    tokens: Tokens
    cost: Cost


class SkillUsage(TypedDict):
    hashes: list[str]  # one per version of the skill seen in the session
    invocations: int


class McpServerUsage(TypedDict):
    calls: int
    tools: dict[str, str | None]  # called tool -> definition hash (None if not in the transcript)


class TimeFields(TypedDict):
    started_at: str | None
    ended_at: str | None
    duration_seconds: float | None


@dataclass(frozen=True, kw_only=True)
class SessionRecord:
    # identity
    session_id: str
    harness: str
    transcript_path: str
    title: str | None
    cwd: str | None
    git_branch: str | None = None              # claude-code only
    harness_version: str | None
    # "transcript" (written by the session) | "installed" (version installed at collection time)
    harness_version_source: Literal["transcript", "installed"] | None
    entrypoint: str | None = None              # claude-code only
    session_format_version: int | None = None  # pi only: on-disk session format version
    parent_session: str | None = None          # pi only: session this one was forked/cloned from
    # time
    started_at: str | None
    ended_at: str | None
    duration_seconds: float | None
    # activity
    user_prompts: int
    assistant_messages: int
    tool_calls_total: int
    tool_calls: dict[str, int]
    tool_errors: int
    models: dict[str, int]
    providers: dict[str, int] | None = None    # pi only: {provider: assistant message count}
    # config the session ran with; None when the harness can't tell, {} when nothing was used
    effort: dict[str, int]                     # {thinking effort level: assistant message count}
    instructions: dict[str, list[str]] | None  # {file path: [content hash, ...]} (several if edited mid-session)
    skills: dict[str, SkillUsage] | None       # invoked skills only
    mcp_servers: dict[str, McpServerUsage]     # servers with at least one call
    # usage of the main model calls
    tokens: Tokens
    cost: Cost | None = None                   # pi only
    aux_usage: AuxUsage | None = None          # pi only: usage outside the main model calls

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


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


def mcp_servers_field(calls: dict[str, Counter], definitions: dict[str, str]) -> dict[str, McpServerUsage]:
    """The mcp_servers field from {server: {tool: calls}} and {full tool name: definition hash}."""
    return {
        server: {
            "calls": sum(tools.values()),
            "tools": {tool: definitions.get(f"mcp__{server}__{tool}") for tool in sorted(tools)},
        }
        for server, tools in sorted(calls.items())
    }


def to_tokens(c: Counter) -> Tokens:
    return Tokens(input=c["input"], output=c["output"], cache_read=c["cache_read"], cache_creation=c["cache_creation"])


def to_cost(c: Counter) -> Cost:
    return Cost(
        input=round(c["input"], 6),
        output=round(c["output"], 6),
        cache_read=round(c["cache_read"], 6),
        cache_creation=round(c["cache_creation"], 6),
        total=round(c["total"], 6),
    )


def parse_ts(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def time_fields(timestamps: list[datetime]) -> TimeFields:
    if not timestamps:
        return {"started_at": None, "ended_at": None, "duration_seconds": None}
    start, end = min(timestamps), max(timestamps)
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": round((end - start).total_seconds(), 3),
    }
