"""agent-analytics: record analytics about an agent session.

Usage:
    agent-analytics [SESSION_ID] [--harness NAME] [--db PATH] [--dry-run]

With no SESSION_ID, the most recent session for the harness is used.
Prints the recorded JSON to stdout (machine-readable); messages go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys

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
    p.add_argument("--db", help=f"JSON store path (default: $AGENT_ANALYTICS_DB or {store.DEFAULT_DB})")
    p.add_argument("-n", "--dry-run", action="store_true", help="print analytics without saving")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        harness = get_harness(args.harness)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    session_id = args.session_id or harness.find_latest_session()
    if not session_id:
        print(f"error: no sessions found for harness {harness.NAME!r}", file=sys.stderr)
        return 1

    try:
        record = harness.collect(session_id)
    except SessionNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not args.dry_run:
        path = store.db_path(args.db)
        store.save_record(path, record)
        print(f"recorded {harness.NAME}:{session_id} -> {path}", file=sys.stderr)

    print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
