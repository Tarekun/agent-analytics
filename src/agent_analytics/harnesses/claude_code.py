"""Claude Code harness.

Claude Code writes one JSONL transcript per session at
    $CLAUDE_CONFIG_DIR/projects/<project-slug>/<session-id>.jsonl
(CLAUDE_CONFIG_DIR defaults to ~/.claude).
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent_analytics.errors import SessionNotFound
from agent_analytics.record import (
    add_unique,
    content_hash,
    mcp_servers_field,
    new_record,
    parse_ts,
    split_mcp_tool,
    time_fields,
    tool_definition_hash,
)

NAME = "claude-code"

SKILL_LOAD_PREFIX = "Base directory for this skill: "
ARGUMENTS_SUFFIX = re.compile(r"\n+ARGUMENTS: .*\Z", re.DOTALL)


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


def find_transcript(session_id: str) -> Path:
    for p in _transcripts():
        if p.stem == session_id:
            return p
    raise SessionNotFound(f"no Claude Code transcript for session {session_id!r} under {projects_dir()}")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _skill_load(entry: dict) -> tuple[str, str] | None:
    """(skill name, hash of the skill text the model received) for a skill-load message.

    Claude Code loads a skill, from a `Skill` tool call or a typed /<skill>, as a
    meta user message: "Base directory for this skill: <dir>\\n<skill text>[\\n\\nARGUMENTS: ...]".
    The arguments are removed so the hash changes only when the skill changes.
    """
    if not entry.get("isMeta"):
        return None
    text = _text((entry.get("message") or {}).get("content"))
    if not text.startswith(SKILL_LOAD_PREFIX):
        return None
    first_line, _, body = text.partition("\n")
    name = Path(first_line.removeprefix(SKILL_LOAD_PREFIX).strip()).name
    body = ARGUMENTS_SUFFIX.sub("", body).strip()
    return name, content_hash(body)


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


def _read_entries(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry


@dataclass
class _Session:
    """Accumulates one transcript, entry by entry."""

    meta: dict = field(default_factory=dict)
    timestamps: list[datetime] = field(default_factory=list)
    user_prompts: int = 0
    assistant_messages: int = 0
    tool_errors: int = 0
    tool_calls: Counter = field(default_factory=Counter)
    models: Counter = field(default_factory=Counter)
    effort: Counter = field(default_factory=Counter)
    tokens: Counter = field(default_factory=lambda: Counter(input=0, output=0, cache_read=0, cache_creation=0))
    instructions: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    skills: dict[str, dict] = field(default_factory=dict)
    mcp_calls: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))  # server -> {tool: calls}
    mcp_definitions: dict[str, str] = field(default_factory=dict)  # full tool name -> definition hash
    seen_message_ids: set[str] = field(default_factory=set)

    def add(self, entry: dict) -> None:
        etype = entry.get("type")
        if etype == "ai-title":
            self.meta["title"] = entry.get("aiTitle")
        elif etype == "attachment":
            self._attachment(entry.get("attachment") or {})
        elif etype == "api-request-shape":
            self._request_shape(entry.get("shape") or {})
        elif etype in ("user", "assistant"):
            for key in ("cwd", "gitBranch", "version", "entrypoint"):
                if entry.get(key):
                    self.meta[key] = entry[key]
            if ts := parse_ts(entry.get("timestamp")):
                self.timestamps.append(ts)
            if etype == "user":
                self._user(entry)
            else:
                self._assistant(entry)

    def _attachment(self, attachment: dict) -> None:
        if attachment.get("type") == "instructions":
            for f in attachment.get("files") or []:
                if f.get("path"):
                    add_unique(self.instructions[f["path"]], content_hash((f.get("content") or "").strip()))

    def _request_shape(self, shape: dict) -> None:
        # The tool definitions sent to the model. MCP tools appear here once loaded.
        for tool in shape.get("tools") or []:
            name = tool.get("name") or ""
            if split_mcp_tool(name):
                self.mcp_definitions[name] = tool_definition_hash(name, tool.get("description"), tool.get("input_schema"))

    def _user(self, entry: dict) -> None:
        if _is_human_prompt(entry):
            self.user_prompts += 1
        if loaded := _skill_load(entry):
            name, skill_hash = loaded
            skill = self.skills.setdefault(name, {"hashes": [], "invocations": 0})
            skill["invocations"] += 1
            add_unique(skill["hashes"], skill_hash)
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            self.tool_errors += sum(
                1 for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")
            )

    def _assistant(self, entry: dict) -> None:
        message = entry.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "?")
                self.tool_calls[name] += 1
                if mcp := split_mcp_tool(name):
                    server, tool = mcp
                    self.mcp_calls[server][tool] += 1

        # One API response may be split across several entries that share the
        # same message id (one per content block) and repeat the same usage and
        # metadata, so count those once per message id.
        msg_id = message.get("id") or entry.get("requestId") or entry.get("uuid")
        if msg_id in self.seen_message_ids:
            return
        self.seen_message_ids.add(msg_id)
        self.assistant_messages += 1
        if model := message.get("model"):
            self.models[model] += 1
        if level := entry.get("effort"):
            self.effort[level] += 1
        usage = message.get("usage") or {}
        self.tokens["input"] += usage.get("input_tokens") or 0
        self.tokens["output"] += usage.get("output_tokens") or 0
        self.tokens["cache_read"] += usage.get("cache_read_input_tokens") or 0
        self.tokens["cache_creation"] += usage.get("cache_creation_input_tokens") or 0



def collect(session_id: str) -> dict:
    path = find_transcript(session_id)

    s = _Session()
    for entry in _read_entries(path):
        s.add(entry)

    return new_record(
        session_id=session_id,
        harness=NAME,
        transcript_path=str(path),
        title=s.meta.get("title"),
        cwd=s.meta.get("cwd"),
        git_branch=s.meta.get("gitBranch"),
        harness_version=s.meta.get("version"),
        harness_version_source="transcript" if s.meta.get("version") else None,
        entrypoint=s.meta.get("entrypoint"),
        **time_fields(s.timestamps),
        user_prompts=s.user_prompts,
        assistant_messages=s.assistant_messages,
        tool_calls_total=sum(s.tool_calls.values()),
        tool_calls=dict(s.tool_calls.most_common()),
        tool_errors=s.tool_errors,
        models=dict(s.models.most_common()),
        effort=dict(s.effort.most_common()),
        instructions=dict(s.instructions),
        skills=dict(sorted(s.skills.items())),
        mcp_servers=mcp_servers_field(s.mcp_calls, s.mcp_definitions),
        tokens=dict(s.tokens),
    )
