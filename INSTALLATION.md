# Installation

These steps set up `agent-analytics` on a new machine and make Claude Code and pi
record every session automatically.

## 1. Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- git
- At least one harness: Claude Code and/or pi

## 2. Install the CLI

```bash
git clone <repo-url> ~/agent-analytics
cd ~/agent-analytics
uv tool install --editable .
```

This puts the `agent-analytics` command in `~/.local/bin`. The install is
editable, so a `git pull` updates the command; no reinstall is needed.

## 3. Claude Code: session-end hook

Add this block to `~/.claude/settings.json` (applies to all projects).
To enable it for one project only, use that project's `.claude/settings.json` instead.

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agent-analytics --hook-input --quiet",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The same block is in [`integrations/claude-code/settings.json`](integrations/claude-code/settings.json).

- If the file already has a `"hooks"` key, add `"SessionEnd"` inside it. Do not
  replace the existing hooks.
- If the hook cannot find the command, use the absolute path from
  `which agent-analytics` in `"command"`.

The hook runs when a session ends: exit, `/clear`, `/resume`, or SIGTERM.

## 4. pi: extension

Link the extension into pi's global extensions folder. Pi loads it automatically.

```bash
mkdir -p ~/.pi/agent/extensions
ln -s ~/agent-analytics/integrations/pi/agent-analytics.ts ~/.pi/agent/extensions/agent-analytics.ts
```

If pi uses a different config folder (`PI_CODING_AGENT_DIR`), link it into `$PI_CODING_AGENT_DIR/extensions/` instead.

The extension runs when a session ends: quit, or a switch with `/new`, `/resume` or
`/fork`. Ephemeral sessions (`--no-session`) are not recorded, because they have no file.

If pi cannot find the command, set `AGENT_ANALYTICS_BIN` to the absolute path from
`which agent-analytics` in the environment that starts pi.

## 5. Verify

1. Start a short session in each harness and exit it.
2. Check that the session is in the store:
   ```bash
   python3 -m json.tool ~/.agent-analytics/sessions.json | grep '"session_id"'
   ```
3. If a session is missing, read the error log:
   ```bash
   cat ~/.agent-analytics/errors.log
   ```
   The hooks run in quiet mode, so all errors go to this file.

## Optional settings

| Environment variable | Effect |
|---|---|
| `AGENT_ANALYTICS_DB` | Store path. Default: `~/.agent-analytics/sessions.json`. `errors.log` is created next to it. |
| `CLAUDE_CONFIG_DIR` | Claude Code config folder, if it is not `~/.claude`. |
| `PI_CODING_AGENT_DIR` / `PI_CODING_AGENT_SESSION_DIR` | pi config / session folders, if they are not the defaults. |
| `AGENT_ANALYTICS_BIN` | Command the pi extension runs. Default: `agent-analytics`. |

Set these variables in the environment that starts the harness, so the hooks see them too.
