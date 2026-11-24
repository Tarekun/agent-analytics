import io
import json
import multiprocessing

from agent_analytics import store
from agent_analytics.cli import main
from test_cli import _write_session


def test_claude_code_hook_payload(tmp_path, monkeypatch):
    transcript = _write_session(tmp_path / "elsewhere", "-proj", "abc", 1_000)
    payload = {"session_id": "abc", "transcript_path": str(transcript), "cwd": "/proj",
               "hook_event_name": "SessionEnd", "reason": "other"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    db = tmp_path / "db.json"

    assert main(["--hook-input", "--quiet", "--db", str(db)]) == 0
    rec = json.loads(db.read_text())["sessions"]["claude-code:abc"]
    assert rec["transcript_path"] == str(transcript)
    assert not (tmp_path / "errors.log").exists()


def test_transcript_without_session_id(tmp_path):
    transcript = _write_session(tmp_path, "-proj", "xyz", 1_000)
    db = tmp_path / "db.json"
    assert main(["--transcript", str(transcript), "--db", str(db), "-q"]) == 0
    assert "claude-code:xyz" in json.loads(db.read_text())["sessions"]


def test_quiet_errors_go_to_log(tmp_path, capsys):
    db = tmp_path / "db.json"
    assert main(["--transcript", str(tmp_path / "missing.jsonl"), "--db", str(db), "-q"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert "transcript not found" in (tmp_path / "errors.log").read_text()


def _save(args):
    db, i = args
    store.save_record(db, {"harness": "h", "session_id": str(i)})


def test_concurrent_writes_keep_all_records(tmp_path):
    db = tmp_path / "db.json"
    with multiprocessing.Pool(8) as pool:
        pool.map(_save, [(db, i) for i in range(40)])
    assert len(json.loads(db.read_text())["sessions"]) == 40
