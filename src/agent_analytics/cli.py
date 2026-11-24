"""agent-analytics: record analytics about an agent session.

Usage:
    agent-analytics [SESSION_ID] [--harness NAME] [--transcript PATH] [--db PATH] [--dry-run]
    agent-analytics --hook-input --quiet          # from a Claude Code hook (JSON on stdin)

With no SESSION_ID, the most recent session for the harness is used.
Prints the recorded JSON to stdout (machine-readable); messages go to stderr.
With --quiet nothing is printed and errors are appended to errors.log next to the store.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent_analytics import __version__, store
from agent_analytics.harnesses import DEFAULT_HARNESS, HARNESSES, SessionNotFound, get_harness


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-analytics",
        description="Record analytics about an agent session into a local JSON file.",
    )
    p.add_argument("session_id", nargs="?", help="session id (default: latest session for the harness)")
    p.add_argument(
        "-H", "--harness", default=DEFAULT_HARNESS,
        help=f"agent harness (default: {DEFAULT_HARNESS}; available: {', '.join(HARNESSES)})",
    )
    p.add_argument("-t", "--transcript", help="session file to read (skips session discovery)")
    p.add_argument(
        "--hook-input", action="store_true",
        help="read a Claude Code hook payload from stdin (uses its session_id and transcript_path)",
    )
    p.add_argument("--db", help=f"JSON store path (default: $AGENT_ANALYTICS_DB or {store.DEFAULT_DB})")
    p.add_argument("-n", "--dry-run", action="store_true", help="print analytics without saving")
    p.add_argument("-q", "--quiet", action="store_true", help="print nothing; log errors to errors.log next to the store")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = store.db_path(args.db).with_name("errors.log")

    def fail(message: str, code: int) -> int:
        if args.quiet:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as fh:
                    stamp = datetime.now(timezone.utc).isoformat()
                    fh.write(f"{stamp} [{args.harness}] {message} (argv={argv if argv is not None else sys.argv[1:]})\n")
            except OSError:
                pass
        else:
            print(f"error: {message}", file=sys.stderr)
        return code

    try:
        harness = get_harness(args.harness)
    except ValueError as e:
        return fail(str(e), 2)

    session_id, transcript = args.session_id, args.transcript
    if args.hook_input:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError) as e:
            return fail(f"invalid hook payload on stdin: {e}", 2)
        session_id = session_id or payload.get("session_id")
        transcript = transcript or payload.get("transcript_path")

    if transcript:
        transcript = Path(transcript).expanduser()
        session_id = session_id or harness.session_id_from_path(transcript)
    session_id = session_id or harness.find_latest_session()
    if not session_id:
        return fail(f"no sessions found for harness {harness.NAME!r}", 1)

    try:
        record = harness.collect(session_id, transcript)
    except SessionNotFound as e:
        return fail(str(e), 1)
    except Exception as e:  # a broken transcript must not crash a hook silently
        return fail(f"failed to collect {harness.NAME}:{session_id}: {e!r}", 1)

    if not args.dry_run:
        path = store.db_path(args.db)
        store.save_record(path, record)
        if not args.quiet:
            print(f"recorded {harness.NAME}:{session_id} -> {path}", file=sys.stderr)

    if not args.quiet:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
