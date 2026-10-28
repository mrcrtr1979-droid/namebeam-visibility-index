import { instrument } from "@posthog/mcp";
import { PostHog } from "posthog-node";
import { SERVER_VERSION } from "./server.js";

// Public, write-only PostHog capture token (same kind of key that ships in public website JS).
export const DEFAULT_POSTHOG_TOKEN = "phc_kKsaZbRt2hgQG4a24VW7VFEPxkcESnKWWWHaYRwyYc2v";
export const DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com";

type Env = Record<string, string | undefined>;

export interface Telemetry {
  shutdown(): Promise<void>;
}

/** Telemetry is on by default. RECEIPTS_INDEX_TELEMETRY=0 or DO_NOT_TRACK=1 turns it off. */
export function telemetryEnabled(env: Env = process.env): boolean {
  if (env.RECEIPTS_INDEX_TELEMETRY === "0") return false;
  if (env.DO_NOT_TRACK === "1") return false;
  return true;
}

// stdout carries the MCP protocol, so every log line goes to stderr.
function stderrLogger(message: string): void {
  try {
    process.stderr.write("receipts-index-mcp telemetry: " + message + "\n");
  } catch {
    // never let logging throw
  }
}

/**
 * Instruments the server with PostHog MCP Analytics. Returns null (and constructs nothing)
 * when telemetry is off or setup fails. Must be called before server.connect().
 */
export function setupTelemetry(server: unknown, env: Env = process.env): Telemetry | null {
  if (!telemetryEnabled(env)) return null;
  try {
    const posthog = new PostHog(env.POSTHOG_PROJECT_TOKEN || DEFAULT_POSTHOG_TOKEN, {
      host: env.POSTHOG_HOST || DEFAULT_POSTHOG_HOST,
      flushAt: 1,
      flushInterval: 0
    });
    posthog.on("error", (err: unknown) => stderrLogger("send error: " + (err instanceof Error ? err.message : String(err))));
    instrument(server, posthog, {
      reportMissing: true,
      context: true,
      serverBuild: SERVER_VERSION,
      logger: stderrLogger,
      shouldRecordInputKey: (key) => key === "start" || key === "limit",
      identify: null
    });
    return {
      shutdown: async () => {
        try {
          await posthog.shutdown();
        } catch (e) {
          stderrLogger("shutdown error: " + (e instanceof Error ? e.message : String(e)));
        }
      }
    };
  } catch (e) {
    stderrLogger("disabled, setup failed: " + (e instanceof Error ? e.message : String(e)));
    return null;
  }
}
