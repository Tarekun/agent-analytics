"""The common record schema shared by every harness.

Every record has exactly these keys, in this order. A harness that cannot
provide a field leaves it as None, so records from different harnesses are
directly comparable.
"""

from __future__ import annotations

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
