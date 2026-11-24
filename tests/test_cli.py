import json
import os

from agent_analytics.cli import main

LINES = [
    {"type": "user", "timestamp": "2026-01-01T10:00:00Z", "cwd": "/proj", "sessionId": "s",
     "message": {"role": "user", "content": "hello"}},
    {"type": "assistant", "timestamp": "2026-01-01T10:00:05Z", "requestId": "r1",
     "message": {"id": "m1", "model": "claude-x", "content": [{"type": "text", "text": "hi"}],
                 "usage": {"input_tokens": 10, "output_tokens": 5}}},
    # same message id, second content block -> usage must not be double counted
    {"type": "assistant", "timestamp": "2026-01-01T10:00:06Z", "requestId": "r1",
     "message": {"id": "m1", "model": "claude-x",
                 "content": [{"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}],
                 "usage": {"input_tokens": 10, "output_tokens": 5}}},
    {"type": "user", "timestamp": "2026-01-01T10:00:10Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True}]}},
    {"type": "ai-title", "aiTitle": "Test session"},
]


def _write_session(root, project, session_id, mtime):
    d = root / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session_id}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in LINES) + "\n")
    os.utime(f, (mtime, mtime))
    return f


def test_latest_session_is_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _write_session(tmp_path, "-proj-a", "old", 1_000)
    _write_session(tmp_path, "-proj-b", "new", 2_000)
    db = tmp_path / "db.json"

    assert main(["--db", str(db)]) == 0

    rec = json.loads(db.read_text())["sessions"]["claude-code:new"]
    assert rec["user_prompts"] == 1
    assert rec["assistant_messages"] == 1
    assert rec["tokens"]["input"] == 10 and rec["tokens"]["output"] == 5
    assert rec["tool_calls"] == {"Bash": 1}
    assert rec["tool_errors"] == 1
    assert rec["duration_seconds"] == 10
    assert rec["title"] == "Test session"
    assert json.loads(capsys.readouterr().out)["session_id"] == "new"


def test_explicit_session_and_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _write_session(tmp_path, "-proj-a", "old", 1_000)
    db = tmp_path / "db.json"
    assert main(["old", "--db", str(db)]) == 0
    assert main(["missing", "--db", str(db)]) == 1
    assert main(["--harness", "nope", "--db", str(db)]) == 2
