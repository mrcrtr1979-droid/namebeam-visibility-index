#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "./server.js";
import { setupTelemetry } from "./telemetry.js";

async function main(): Promise<void> {
  const server = createServer();
  const telemetry = setupTelemetry(server);

  // Flush batched analytics events once, then exit. Guarded so repeated signals/events do nothing.
  let exiting = false;
  const finish = async (code: number): Promise<void> => {
    if (exiting) return;
    exiting = true;
    try {
      if (telemetry) await telemetry.shutdown();
    } catch {
      // telemetry must never block or crash exit
    }
    process.exit(code);
  };
  process.on("SIGTERM", () => void finish(0));
  process.on("SIGINT", () => void finish(0));
  process.stdin.on("end", () => void finish(0));
  process.stdin.on("close", () => void finish(0));

  await server.connect(new StdioServerTransport());
}

main().catch((err) => {
  process.stderr.write("receipts-index-mcp failed to start: " + (err instanceof Error ? err.message : String(err)) + "\n");
  process.exit(1);
});
