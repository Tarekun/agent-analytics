# agent-analytics

A tiny CLI, for humans and agents, that records analytics about coding-agent sessions into a local JSON file.

## Usage

```bash
agent-analytics                       # latest Claude Code session
agent-analytics <session-id>          # a specific session
agent-analytics -H claude-code <id>   # explicit harness (default: claude-code)
agent-analytics --dry-run             # print, don't save
agent-analytics --db ./stats.json     # custom store path
```

Without installing run with: `uv run agent-analytics ...`.

- **stdout**: the recorded session as JSON.
- **stderr**: human messages / errors. Exit codes: `0` ok, `1` session not found, `2` bad arguments.

With `-q/--quiet` nothing is printed and errors are appended to `errors.log`
next to the store. `-t/--transcript PATH` reads that session file directly.

## Automatic collection (harness setup)

Both integrations call `agent-analytics`, so first put it on PATH:

```bash
uv tool install --editable .     # installs ~/.local/bin/agent-analytics
```

**Claude Code**: add the `SessionEnd` hook from
[`integrations/claude-code/settings.json`](integrations/claude-code/settings.json)
to `~/.claude/settings.json` (all projects) or a project's `.claude/settings.json`.
The hook passes its JSON payload on stdin (`--hook-input`). `SessionEnd` hooks get
only 1.5 s by default; the `"timeout": 30` raises that budget. The hook fires on
exit, `/clear` and `/resume`, and on SIGTERM; it does not fire on a crash or SIGKILL.

**pi**: link the extension into pi's global extensions folder (auto-discovered):

```bash
ln -s "$PWD/integrations/pi/agent-analytics.ts" ~/.pi/agent/extensions/agent-analytics.ts
```

It runs on pi's `session_shutdown` event (quit, and when switching with /new,
/resume or /fork) and passes the session id and file to the CLI.
Set `AGENT_ANALYTICS_BIN` if the command is not on pi's PATH.

## Storage

Records are upserted into `~/.agent-analytics/sessions.json` (override with
`--db` or `$AGENT_ANALYTICS_DB`), keyed by `<harness>:<session_id>`.

Every record has the same keys regardless of harness (see `src/agent_analytics/record.py`);
fields a harness can't provide are `null`.

| field | claude-code | pi |
|---|---|---|
| session_id, transcript_path, title, cwd | ✅ | ✅ (title only if the session was named) |
| git_branch, entrypoint | ✅ | `null` |
| harness_version | ✅ from the transcript | from `pi --version` at collection time |
| harness_version_source | `"transcript"` | `"installed"` (may differ from the version that wrote the session) |
| session_format_version, parent_session | `null` | ✅ |
| started_at, ended_at, duration_seconds | ✅ | ✅ |
| user_prompts, assistant_messages | ✅ | ✅ |
| tool_calls_total, tool_calls, tool_errors | ✅ | ✅ |
| models | ✅ | ✅ |
| providers | `null` | ✅ |
| tokens (main model calls) | ✅ | ✅ |
| cost (main model calls) | `null` | ✅ |
| aux_usage (compaction, branch summaries, nested tool calls, …) | `null` | ✅ |

## Harnesses

| name          | session source                                              |
|---------------|-------------------------------------------------------------|
| `claude-code` | `$CLAUDE_CONFIG_DIR/projects/*/<session-id>.jsonl` (default `~/.claude`) |
| `pi`          | `$PI_CODING_AGENT_SESSION_DIR` or `$PI_CODING_AGENT_DIR/sessions` (default `~/.pi/agent/sessions`), `*/<timestamp>_<session-id>.jsonl` |

"Latest session" = the most recently modified transcript.

Pi sessions are trees (branches are kept in the same file); all branches are counted.
The pi parser was written from pi's documented format and tested against synthetic
sessions, not real ones yet.

To add a harness, create a module in `src/agent_analytics/harnesses/` exposing
`NAME`, `find_latest_session()` and `collect(session_id)` (build the result with
`record.new_record`), and register it in `harnesses/__init__.py`.

## Tests

```bash
uv run --with pytest pytest
```
