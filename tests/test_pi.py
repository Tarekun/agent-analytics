"""Pi tests use a session built from the documented format (docs/session-format.md,
docs/message-types.md in github.com/earendil-works/pi), not a real recorded session."""

import json
import os

import pytest

from agent_analytics.cli import main
from agent_analytics.harnesses import pi
from agent_analytics.record import FIELDS, content_hash, tool_definition_hash

SID = "0b6f7a2e-1111-2222-3333-444455556666"


def usage(inp, out, cr=0, cw=0, total_cost=0.0):
    return {"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw, "totalTokens": inp + out + cr + cw,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": total_cost}}


LINES = [
    {"type": "session", "version": 3, "id": SID, "timestamp": "2026-01-01T10:00:00.000Z", "cwd": "/proj"},
    {"type": "model_change", "id": "a1", "parentId": None, "timestamp": "2026-01-01T10:00:00.500Z",
     "provider": "anthropic", "modelId": "claude-x"},
    {"type": "thinking_level_change", "id": "a1b", "parentId": "a1", "timestamp": "2026-01-01T10:00:00.600Z",
     "thinkingLevel": "medium"},
    {"type": "message", "id": "a2", "parentId": "a1", "timestamp": "2026-01-01T10:00:01.000Z",
     "message": {"role": "user", "content": "fix the bug", "timestamp": 1767261601000}},
    {"type": "message", "id": "a3", "parentId": "a2", "timestamp": "2026-01-01T10:00:05.000Z",
     "message": {"role": "assistant", "provider": "anthropic", "model": "claude-x", "stopReason": "toolUse",
                 "content": [{"type": "text", "text": "looking"},
                             {"type": "toolCall", "id": "t1", "name": "bash", "arguments": {"command": "ls"}}],
                 "usage": usage(100, 20, cr=50, total_cost=0.01)}},
    {"type": "message", "id": "a4", "parentId": "a3", "timestamp": "2026-01-01T10:00:06.000Z",
     "message": {"role": "toolResult", "toolCallId": "t1", "toolName": "bash", "isError": True, "content": [],
                 "usage": usage(7, 3, total_cost=0.001)}},
    {"type": "message", "id": "a5", "parentId": "a4", "timestamp": "2026-01-01T10:00:09.000Z",
     "message": {"role": "assistant", "provider": "anthropic", "model": "claude-x", "stopReason": "stop",
                 "thinkingLevel": "high",
                 "content": [{"type": "text", "text": "done"}], "usage": usage(10, 5, total_cost=0.002)}},
    {"type": "compaction", "id": "a6", "parentId": "a5", "timestamp": "2026-01-01T10:00:20.000Z",
     "summary": "...", "firstKeptEntryId": "a5", "tokensBefore": 500, "usage": usage(40, 10, total_cost=0.004)},
    {"type": "session_info", "id": "a7", "parentId": "a6", "timestamp": "2026-01-01T10:00:21.000Z", "name": "Bug hunt"},
]


@pytest.fixture
def pi_env(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(sessions))
    # fake `pi` executable reporting a version
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "pi"
    exe.write_text("#!/bin/sh\necho 1.6.0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    pi.installed_version.cache_clear()
    yield sessions
    pi.installed_version.cache_clear()


def write_session(sessions, sid, mtime, lines=LINES):
    d = sessions / "--proj--"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"2026-01-01T10-00-00-000Z_{sid}.jsonl"
    f.write_text("\n".join(json.dumps({**x, "id": sid} if x["type"] == "session" else x) for x in lines) + "\n")
    os.utime(f, (mtime, mtime))
    return f


def test_pi_latest_session(pi_env, tmp_path, capsys):
    write_session(pi_env, "older", 1_000)
    write_session(pi_env, SID, 2_000)
    db = tmp_path / "db.json"

    assert main(["-H", "pi", "--db", str(db)]) == 0
    rec = json.loads(db.read_text())["sessions"][f"pi:{SID}"]

    assert list(rec)[: len(FIELDS)] == list(FIELDS)
    assert rec["title"] == "Bug hunt"
    assert rec["cwd"] == "/proj"
    assert rec["harness_version"] == "1.6.0" and rec["harness_version_source"] == "installed"
    assert rec["session_format_version"] == 3
    assert rec["git_branch"] is None and rec["entrypoint"] is None
    assert rec["user_prompts"] == 1
    assert rec["assistant_messages"] == 2
    assert rec["tool_calls"] == {"bash": 1} and rec["tool_errors"] == 1
    assert rec["models"] == {"claude-x": 2} and rec["providers"] == {"anthropic": 2}
    assert rec["effort"] == {"medium": 1, "high": 1}
    # no system message: what was loaded is unknown, but MCP calls would still be visible
    assert rec["instructions"] is None and rec["skills"] is None and rec["mcp_servers"] == {}
    assert rec["tokens"] == {"input": 110, "output": 25, "cache_read": 50, "cache_creation": 0}
    assert rec["cost"]["total"] == pytest.approx(0.012)
    assert rec["aux_usage"]["tokens"]["input"] == 47  # tool result (7) + compaction (40)
    assert rec["aux_usage"]["cost"]["total"] == pytest.approx(0.005)
    assert rec["duration_seconds"] == 21


def test_pi_explicit_session_and_missing(pi_env, tmp_path):
    write_session(pi_env, SID, 1_000)
    db = tmp_path / "db.json"
    assert main([SID, "-H", "pi", "--db", str(db)]) == 0
    assert main(["nope", "-H", "pi", "--db", str(db)]) == 1


def test_pi_without_pi_installed(pi_env, tmp_path, monkeypatch):
    write_session(pi_env, SID, 1_000)
    monkeypatch.setenv("PATH", "/nonexistent")
    pi.installed_version.cache_clear()
    rec = pi.collect(SID)
    assert rec["harness_version"] is None and rec["harness_version_source"] is None


SKILL_BODY = "# Dev\n\nDevelop the issue."
SKILL_FILE = f"---\nname: dev\ndescription: Develop an issue\n---\n\n{SKILL_BODY}\n"
SKILLS_SECTION = """<skills>
The following skills provide specialized instructions for specific tasks.

<available_skills>
  <skill>
    <name>dev</name>
    <description>Develop an issue</description>
    <location>/proj/.agents/skills/dev/SKILL.md</location>
  </skill>
</available_skills>
</skills>"""
FETCH_TOOL = {"name": "mcp__docs__fetch", "description": "Fetch a page", "parameters": {"type": "object"}}


def instructions_section(text):
    return ("<project_context>\nProject-specific instructions and guidelines:\n\n"
            f'<project_instructions path="/proj/AGENTS.md">\n{text}\n\n</project_instructions>\n</project_context>')


def msg(i, message):
    return {"type": "message", "id": i, "parentId": None, "timestamp": "2026-01-01T10:00:00.000Z", "message": message}


def read_call(i, call_id, path, **extra):
    return msg(i, {"role": "assistant", "model": "m", "content": [
        {"type": "toolCall", "id": call_id, "name": "read", "arguments": {"path": path, **extra}}]})


def read_result(i, call_id, text, is_error=False):
    return msg(i, {"role": "toolResult", "toolCallId": call_id, "toolName": "read", "isError": is_error,
                   "content": [{"type": "text", "text": text}]})


CONFIG_LINES = [
    {"type": "session", "version": 3, "id": SID, "timestamp": "2026-01-01T10:00:00.000Z", "cwd": "/proj"},
    msg("s1", {"role": "system", "content": "", "toolsAdded": [{"name": "read"}, FETCH_TOOL],
               "sections": {"preamble": "...", "project_context": instructions_section("rule v1"),
                            "skills": SKILLS_SECTION}}),
    # the model loads the skill: full read, relative to cwd
    read_call("r1", "c1", ".agents/skills/dev/SKILL.md"),
    read_result("r2", "c1", SKILL_FILE),
    # not loads: a partial re-read, a failed read, a SKILL.md the session did not offer
    read_call("r3", "c2", "/proj/.agents/skills/dev/SKILL.md", offset=3, limit=2),
    read_result("r4", "c2", "Develop the issue."),
    read_call("r5", "c3", "/proj/.agents/skills/dev/SKILL.md"),
    read_result("r6", "c3", "ENOENT", is_error=True),
    read_call("r7", "c4", "/elsewhere/skills/other/SKILL.md"),
    read_result("r8", "c4", SKILL_FILE),
    # the user types /skill:dev with arguments: same skill text, same hash
    msg("u1", {"role": "user", "content": [{"type": "text", "text":
        '<skill name="dev" location="/proj/.agents/skills/dev/SKILL.md">\n'
        f"References are relative to /proj/.agents/skills/dev.\n\n{SKILL_BODY}\n</skill>\n\nimplement #12"}]}),
    # MCP: one direct call, two calls from a codemode script (one to a tool never declared)
    msg("a1", {"role": "assistant", "model": "m", "content": [
        {"type": "toolCall", "id": "c5", "name": "mcp__docs__fetch", "arguments": {}},
        {"type": "toolCall", "id": "c6", "name": "codemode", "arguments": {"code": "..."}}]}),
    msg("t5", {"role": "toolResult", "toolCallId": "c5", "toolName": "mcp__docs__fetch", "isError": False, "content": []}),
    msg("t6", {"role": "toolResult", "toolCallId": "c6", "toolName": "codemode", "isError": False, "content": [],
               "nestedCalls": [{"id": "n1", "name": "mcp__docs__fetch", "status": "ok"},
                               {"id": "n2", "name": "mcp__tracker__list", "status": "ok"},
                               {"id": "n3", "name": "bash", "status": "ok"}]}),
    # AGENTS.md edited mid-session: a later system message patches only that section
    msg("s2", {"role": "system", "content": "", "sections": {"project_context": instructions_section("rule v2")}}),
]


def test_pi_config_snapshot(pi_env):
    write_session(pi_env, SID, 1_000, CONFIG_LINES)
    rec = pi.collect(SID)

    assert rec["instructions"] == {"/proj/AGENTS.md": [content_hash("rule v1"), content_hash("rule v2")]}
    # the hash covers the instructions without frontmatter, as in claude-code
    assert rec["skills"] == {"dev": {"hashes": [content_hash(SKILL_BODY)], "invocations": 2}}
    assert rec["mcp_servers"] == {
        "docs": {"calls": 2, "tools": {"fetch": tool_definition_hash("mcp__docs__fetch", "Fetch a page", {"type": "object"})}},
        "tracker": {"calls": 1, "tools": {"list": None}},
    }
    assert rec["tool_calls"]["read"] == 4 and rec["user_prompts"] == 1


def test_pi_session_without_config_used(pi_env):
    lines = [CONFIG_LINES[0], msg("s1", {"role": "system", "content": "", "sections": {"preamble": "..."}})]
    write_session(pi_env, SID, 1_000, lines)
    rec = pi.collect(SID)
    assert rec["instructions"] == {} and rec["skills"] == {} and rec["mcp_servers"] == {}


def test_claude_code_has_pi_fields_as_null(tmp_path, monkeypatch):
    from test_cli import _write_session
    from agent_analytics.harnesses import claude_code

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _write_session(tmp_path, "-proj", "s", 1_000)
    rec = claude_code.collect("s")
    assert list(rec) == list(FIELDS)
    for field in ("providers", "cost", "aux_usage", "session_format_version", "parent_session"):
        assert rec[field] is None
    assert rec["harness_version_source"] is None  # fixture has no version
