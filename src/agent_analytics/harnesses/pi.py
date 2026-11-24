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

The config snapshot comes from what the session recorded:
- system messages carry the prompt sections (patched by name, so a later one
  holds only what changed) and the tools declared to the model (`toolsAdded`);
  instruction files are in the `project_context` section, the skills Pi offers
  in the `skills` section;
- a skill is loaded either by the model reading its SKILL.md with the `read`
  tool, or by the user typing /skill:<name>, which sends the skill as a
  `<skill name=... location=...>` user message;
- MCP tools are named mcp__<server>__<tool> and are called directly or from
  tools such as `codemode`; Pi records those nested calls as `nestedCalls` on
  the calling tool's result.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from agent_analytics.errors import SessionNotFound
from agent_analytics.record import (
    add_unique,
    content_hash,
    AuxUsage,
    SessionRecord,
    SkillUsage,
    mcp_servers_field,
    parse_ts,
    split_mcp_tool,
    time_fields,
    to_cost,
    to_tokens,
    tool_definition_hash,
)

NAME = "pi"

TOKEN_KEYS = {"input": "input", "output": "output", "cacheRead": "cache_read", "cacheWrite": "cache_creation"}

PROJECT_INSTRUCTIONS = re.compile(r'<project_instructions path="([^"]+)">\n?(.*?)</project_instructions>', re.DOTALL)
AVAILABLE_SKILL = re.compile(r"<skill>\s*<name>(.*?)</name>.*?<location>(.*?)</location>\s*</skill>", re.DOTALL)
SKILL_COMMAND = re.compile(
    r'\A<skill name="([^"]+)" location="[^"]*">\n(?:References are relative to [^\n]*\n)?(.*?)\n</skill>', re.DOTALL
)
FRONTMATTER = re.compile(r"\A---\n.*?\n---(?:\n|\Z)", re.DOTALL)


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


def _session_id(path: Path) -> str:
    # Prefer the header id; fall back to the part of the filename after "<timestamp>_".
    return _header(path).get("id") or path.stem.rsplit("_", 1)[-1]


def find_latest_session() -> str | None:
    files = _transcripts()
    if not files:
        return None
    return _session_id(max(files, key=lambda p: p.stat().st_mtime))


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


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _skill_hash(text: str) -> str:
    """Hash of a skill's instructions without frontmatter, so both ways of loading it give the same hash."""
    return content_hash(FRONTMATTER.sub("", text.strip(), count=1).strip())


@dataclass
class _Session:
    """Accumulates one session file, entry by entry."""

    cwd: str | None = None
    timestamps: list[datetime] = field(default_factory=list)
    user_prompts: int = 0
    assistant_messages: int = 0
    tool_errors: int = 0
    tool_calls: Counter = field(default_factory=Counter)
    models: Counter = field(default_factory=Counter)
    providers: Counter = field(default_factory=Counter)
    effort: Counter = field(default_factory=Counter)
    thinking_level: str | None = None  # last thinking_level_change, for messages that don't carry their own
    tokens: Counter = field(default_factory=lambda: Counter(input=0, output=0, cache_read=0, cache_creation=0))
    cost: Counter = field(default_factory=lambda: Counter(input=0, output=0, cache_read=0, cache_creation=0, total=0))
    aux_tokens: Counter = field(default_factory=lambda: Counter(input=0, output=0, cache_read=0, cache_creation=0))
    aux_cost: Counter = field(default_factory=lambda: Counter(input=0, output=0, cache_read=0, cache_creation=0, total=0))
    header: dict = field(default_factory=dict)
    title: str | None = None
    has_system_prompt: bool = False
    instructions: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    available_skills: dict[str, str] = field(default_factory=dict)  # SKILL.md path -> skill name
    skill_reads: dict[str, str] = field(default_factory=dict)  # read tool call id -> skill name
    skills: dict[str, SkillUsage] = field(default_factory=dict)
    mcp_calls: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))  # server -> {tool: calls}
    mcp_definitions: dict[str, str] = field(default_factory=dict)  # full tool name -> definition hash

    def add(self, entry: dict) -> None:
        etype = entry.get("type")
        if ts := parse_ts(entry.get("timestamp")):
            self.timestamps.append(ts)

        if etype == "session":
            self.header = entry
            self.cwd = entry.get("cwd")
        elif etype == "thinking_level_change":
            self.thinking_level = entry.get("thinkingLevel") or self.thinking_level
        elif etype == "session_info":
            self.title = entry.get("name") or self.title
        elif etype in ("usage", "compaction", "branch_summary"):
            _add_usage(self.aux_tokens, self.aux_cost, entry.get("usage"))
            if isinstance(entry.get("systemMessage"), dict):
                self._system(entry["systemMessage"])
        elif etype == "message":
            msg = entry.get("message") or {}
            role = msg.get("role")
            if role == "system":
                self._system(msg)
            elif role == "user":
                self._user(msg)
            elif role == "assistant":
                self._assistant(msg)
            elif role == "toolResult":
                self._tool_result(msg)

    def _system(self, msg: dict) -> None:
        self.has_system_prompt = True
        sections = msg.get("sections") or {}
        if context := sections.get("project_context"):
            for path, content in PROJECT_INSTRUCTIONS.findall(context):
                add_unique(self.instructions[path], content_hash(content.strip()))
        if skills := sections.get("skills"):
            for name, location in AVAILABLE_SKILL.findall(skills):
                self.available_skills[os.path.normpath(location.strip())] = name.strip()
        for tool in msg.get("toolsAdded") or []:
            name = tool.get("name") or ""
            if split_mcp_tool(name):
                self.mcp_definitions[name] = tool_definition_hash(name, tool.get("description"), tool.get("parameters"))

    def _load_skill(self, name: str, text: str) -> None:
        skill = self.skills.setdefault(name, SkillUsage(hashes=[], invocations=0))
        skill["invocations"] += 1
        add_unique(skill["hashes"], _skill_hash(text))

    def _user(self, msg: dict) -> None:
        self.user_prompts += 1
        # /skill:<name> [args] sends the skill text followed by the arguments
        if m := SKILL_COMMAND.match(_text(msg.get("content"))):
            self._load_skill(m.group(1), m.group(2))

    def _assistant(self, msg: dict) -> None:
        self.assistant_messages += 1
        if model := msg.get("model"):
            self.models[model] += 1
        if provider := msg.get("provider"):
            self.providers[provider] += 1
        if level := msg.get("thinkingLevel") or self.thinking_level:
            self.effort[level] += 1
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                name = block.get("name", "?")
                self.tool_calls[name] += 1
                self._count_mcp(name)
                call_id = block.get("id")
                if name == "read" and call_id and (skill := self._read_skill(block.get("arguments"))):
                    self.skill_reads[call_id] = skill
        _add_usage(self.tokens, self.cost, msg.get("usage"))

    def _read_skill(self, args) -> str | None:
        """The skill a read call loads: a full read of a SKILL.md the session offered.

        Partial reads (offset/limit) only re-read part of a skill and are not loads.
        """
        if not isinstance(args, dict) or not isinstance(args.get("path"), str):
            return None
        if args.get("offset") not in (None, 1) or args.get("limit") is not None:
            return None
        path = os.path.expanduser(args["path"].removeprefix("@"))
        if not os.path.isabs(path) and self.cwd:
            path = os.path.join(self.cwd, path)
        return self.available_skills.get(os.path.normpath(path))

    def _tool_result(self, msg: dict) -> None:
        if msg.get("isError"):
            self.tool_errors += 1
        elif skill := self.skill_reads.pop(msg.get("toolCallId") or "", None):
            self._load_skill(skill, _text(msg.get("content")))
        for call in msg.get("nestedCalls") or []:
            if isinstance(call, dict):
                self._count_mcp(call.get("name") or "")
        # nested model work done by a tool is not part of the main calls
        _add_usage(self.aux_tokens, self.aux_cost, msg.get("usage"))

    def _count_mcp(self, tool_name: str) -> None:
        if mcp := split_mcp_tool(tool_name):
            server, tool = mcp
            self.mcp_calls[server][tool] += 1


def collect(session_id: str) -> SessionRecord:
    path = find_transcript(session_id)

    s = _Session()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                s.add(entry)

    # Instructions and offered skills are only known from system messages, which
    # sessions written before Pi recorded them do not have.
    config_known = s.has_system_prompt
    version = installed_version()
    return SessionRecord(
        session_id=s.header.get("id") or session_id,
        harness=NAME,
        transcript_path=str(path),
        title=s.title,
        cwd=s.cwd,
        harness_version=version,
        harness_version_source="installed" if version else None,
        session_format_version=s.header.get("version"),
        parent_session=s.header.get("parentSession"),
        **time_fields(s.timestamps),
        user_prompts=s.user_prompts,
        assistant_messages=s.assistant_messages,
        tool_calls_total=sum(s.tool_calls.values()),
        tool_calls=dict(s.tool_calls.most_common()),
        tool_errors=s.tool_errors,
        models=dict(s.models.most_common()),
        providers=dict(s.providers.most_common()),
        effort=dict(s.effort.most_common()),
        instructions=dict(s.instructions) if config_known else None,
        skills=dict(sorted(s.skills.items())) if config_known else None,
        mcp_servers=mcp_servers_field(s.mcp_calls, s.mcp_definitions),
        tokens=to_tokens(s.tokens),
        cost=to_cost(s.cost),
        aux_usage=AuxUsage(tokens=to_tokens(s.aux_tokens), cost=to_cost(s.aux_cost)),
    )
