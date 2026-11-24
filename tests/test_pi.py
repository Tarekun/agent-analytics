"""Pi tests use a session built from the documented format (docs/session-format.md,
docs/message-types.md in github.com/earendil-works/pi), not a real recorded session."""

import json
import os

import pytest

from agent_analytics.cli import main
from agent_analytics.harnesses import pi
from agent_analytics.record import FIELDS

SID = "0b6f7a2e-1111-2222-3333-444455556666"


def usage(inp, out, cr=0, cw=0, total_cost=0.0):
    return {"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw, "totalTokens": inp + out + cr + cw,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": total_cost}}


LINES = [
    {"type": "session", "version": 3, "id": SID, "timestamp": "2026-01-01T10:00:00.000Z", "cwd": "/proj"},
    {"type": "model_change", "id": "a1", "parentId": None, "timestamp": "2026-01-01T10:00:00.500Z",
     "provider": "anthropic", "modelId": "claude-x"},
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
