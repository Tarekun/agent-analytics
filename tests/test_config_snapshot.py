"""Config snapshot fields for Claude Code: effort, instruction files, invoked skills, MCP servers.

The entry shapes copy real Claude Code transcripts (version 2.1.x)."""

import json

from agent_analytics.harnesses import claude_code
from agent_analytics.record import content_hash

SKILL_TEXT = "# Review\n\nReview exactly one pull request."
FETCH_DEF = {"name": "mcp__docs__fetch", "description": "Fetch a doc", "input_schema": {"type": "object"}}


def user(content, **extra):
    return {"type": "user", "timestamp": "2026-01-01T10:00:00Z", "message": {"role": "user", "content": content}, **extra}


def assistant(msg_id, blocks, effort="high"):
    return {"type": "assistant", "timestamp": "2026-01-01T10:00:01Z", "effort": effort,
            "message": {"id": msg_id, "model": "claude-x", "content": blocks, "usage": {}}}


def tool_use(name, **input):
    return {"type": "tool_use", "id": f"t-{name}", "name": name, "input": input}


def skill_load(args=None, **extra):
    text = f"Base directory for this skill: /proj/.claude/skills/review\n\n{SKILL_TEXT}"
    if args:
        text += f"\n\n\nARGUMENTS: {args}"
    return user([{"type": "text", "text": text}], isMeta=True, **extra)


def instructions(content):
    return {"type": "attachment", "attachment": {"type": "instructions", "files": [
        {"path": "/proj/AGENTS.md", "type": "Project", "content": content}]}}


LINES = [
    instructions("rule v1"),
    {"type": "api-request-shape", "shape": {"system": [], "tools": [
        {"name": "Bash", "description": "run", "input_schema": {}},
        {**FETCH_DEF, "eager_input_streaming": True},
    ]}},
    # typed /review: a command message, then the skill load
    user("<command-message>review</command-message>\n<command-name>/review</command-name>\n<command-args>51</command-args>"),
    skill_load("51"),
    assistant("m1", [tool_use("Skill", skill="review", args="52")], effort="medium"),
    # Skill tool call: the skill load points back at the tool call
    skill_load("52", sourceToolUseID="t-Skill"),
    assistant("m2", [tool_use("mcp__docs__fetch", id="1")]),
    assistant("m2", [tool_use("mcp__docs__fetch", id="2")]),  # same response, second block
    assistant("m3", [tool_use("mcp__tracker__list")]),        # definition never sent in a request shape
    instructions("rule v2"),                                  # file edited mid-session
    instructions("rule v2"),
]


def _write_transcript(tmp_path, monkeypatch, lines):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    project = tmp_path / "projects" / "p"
    project.mkdir(parents=True)
    (project / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_config_snapshot(tmp_path, monkeypatch):
    _write_transcript(tmp_path, monkeypatch, LINES)

    rec = claude_code.collect("s")

    assert rec["effort"] == {"high": 2, "medium": 1}
    assert rec["instructions"] == {"/proj/AGENTS.md": [content_hash("rule v1"), content_hash("rule v2")]}
    # both invocations have the same hash: the arguments are not part of it
    assert rec["skills"] == {"review": {"hashes": [content_hash(SKILL_TEXT)], "invocations": 2}}
    assert rec["mcp_servers"] == {
        "docs": {"calls": 2, "tools": {"fetch": content_hash(FETCH_DEF)}},
        "tracker": {"calls": 1, "tools": {"list": None}},
    }
    assert rec["user_prompts"] == 1  # the skill loads are not prompts


def test_no_config_used_gives_empty_values(tmp_path, monkeypatch):
    _write_transcript(tmp_path, monkeypatch, [user("hi")])
    rec = claude_code.collect("s")
    assert rec["skills"] == {} and rec["mcp_servers"] == {} and rec["instructions"] == {} and rec["effort"] == {}
