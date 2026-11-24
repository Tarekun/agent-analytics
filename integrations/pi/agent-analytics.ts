/**
 * pi extension: record session analytics with `agent-analytics` when a session ends.
 *
 * Install (global, auto-discovered by pi):
 *   ln -s "$PWD/integrations/pi/agent-analytics.ts" ~/.pi/agent/extensions/agent-analytics.ts
 *
 * Requires `agent-analytics` on PATH (or set AGENT_ANALYTICS_BIN).
 * Failures never interrupt pi; they are logged by the CLI to ~/.agent-analytics/errors.log.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BIN = process.env.AGENT_ANALYTICS_BIN || "agent-analytics";

export default function (pi: ExtensionAPI) {
  // Remember the session at start: the docs don't guarantee ctx is still usable during shutdown.
  let sessionId: string | undefined;
  let sessionFile: string | undefined;

  const remember = (ctx: any) => {
    try {
      sessionId = ctx.sessionManager.getSessionId() ?? sessionId;
      sessionFile = ctx.sessionManager.getSessionFile() ?? sessionFile;
    } catch {
      /* keep previous values */
    }
  };

  pi.on("session_start", async (_event, ctx) => remember(ctx));

  pi.on("session_shutdown", async (event, ctx) => {
    // "reload" keeps the same session running; every other reason ends this session
    // ("quit", or "new" / "resume" / "fork" switching to another one).
    if (event.reason === "reload") return;
    remember(ctx);
    if (!sessionId || !sessionFile) return; // ephemeral session (--no-session): nothing on disk

    try {
      await pi.exec(BIN, [sessionId, "--harness", "pi", "--transcript", sessionFile, "--quiet"], {
        timeout: 15000,
      });
    } catch {
      /* never block or break pi */
    }
  });
}
