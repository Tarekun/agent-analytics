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

## Storage

Records are upserted into `~/.agent-analytics/sessions.json` (override with
`--db` or `$AGENT_ANALYTICS_DB`), keyed by `<harness>:<session_id>`.

Every record has the same keys regardless of harness (`SessionRecord` in `src/agent_analytics/record.py`);
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
| effort (thinking level → assistant messages) | ✅ | ✅ (`thinkingLevel`) |
| instructions | ✅ | ✅ |
| skills (invoked only) | ✅ | ✅ |
| mcp_servers (servers with at least one call) | ✅ | ✅ (also calls made from `codemode` scripts) |

`{}` means the harness records the field but the session used none of it; `null` means
the harness can't provide it. Pi sessions written before Pi recorded system messages have
`null` instructions and skills.

### Config snapshot

All hashes are the first 12 hex characters of a SHA-256 and come from the transcript,
so they describe exactly what the model received, also for old sessions. Hashes are
computed the same way in every harness, so a file, skill or tool definition that reaches
the model unchanged has the same hash in Claude Code and Pi.

- **instructions**: `{path: [hash, ...]}` of each instruction file (CLAUDE.md, AGENTS.md, …)
  loaded in the session, hashed without leading and trailing whitespace. Several hashes
  mean the file changed during the session.
- **skills**: `{name: {"hashes": [...], "invocations": n}}` for each skill loaded in the
  session. The hash covers the skill instructions without frontmatter and arguments, so it
  changes only when the skill changes. Supporting files in the skill directory are not
  part of the hash.
  - Claude Code: a `Skill` tool call or a typed `/<skill>`.
  - Pi: the model reading a SKILL.md the session offered (a full `read`; a partial re-read
    with `offset`/`limit` is not a load), or a typed `/skill:<name>`. Reading a SKILL.md
    to edit it also counts, since the transcript can't tell the two apart.
- **mcp_servers**: `{server: {"calls": n, "tools": {tool: hash}}}` for each server with at
  least one call. Each called tool gets the hash of its definition (name, description,
  input schema). A tool is hashed separately because the model receives only the tools
  it loaded, so a hash over the whole server would change with what was loaded.
  The hash is `null` when the definition is not in the transcript, which in Pi is the
  case for tools called only from `codemode` scripts: their definitions are never
  declared to the model.

## Harnesses

| name          | session source                                              |
|---------------|-------------------------------------------------------------|
| `claude-code` | `$CLAUDE_CONFIG_DIR/projects/*/<session-id>.jsonl` (default `~/.claude`) |
| `pi`          | `$PI_CODING_AGENT_SESSION_DIR` or `$PI_CODING_AGENT_DIR/sessions` (default `~/.pi/agent/sessions`), `*/<timestamp>_<session-id>.jsonl` |

"Latest session" = the most recently modified transcript.

Pi sessions are trees (branches are kept in the same file); all branches are counted.
The pi parser was written from pi's documented format and tested against synthetic
sessions. The config snapshot was also checked against real pi 1.4.0 sessions.

To add a harness, create a module in `src/agent_analytics/harnesses/` exposing
`NAME`, `find_latest_session()` and `collect(session_id)` returning a
`record.SessionRecord`, and register it in `harnesses/__init__.py`.

## Tests

```bash
uv run --with pytest pytest
```
