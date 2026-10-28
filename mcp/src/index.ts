#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "./server.js";

async function main(): Promise<void> {
  const server = createServer();
  await server.connect(new StdioServerTransport());
}

main().catch((err) => {
  process.stderr.write("receipts-index-mcp failed to start: " + (err instanceof Error ? err.message : String(err)) + "\n");
  process.exit(1);
});
