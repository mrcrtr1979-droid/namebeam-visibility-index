import { test } from "node:test";
import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "../src/server.js";
import { setupTelemetry, telemetryEnabled } from "../src/telemetry.js";

test("telemetry is on by default and off for RECEIPTS_INDEX_TELEMETRY=0 or DO_NOT_TRACK=1", () => {
  assert.equal(telemetryEnabled({}), true);
  assert.equal(telemetryEnabled({ RECEIPTS_INDEX_TELEMETRY: "0" }), false);
  assert.equal(telemetryEnabled({ DO_NOT_TRACK: "1" }), false);
  assert.equal(telemetryEnabled({ RECEIPTS_INDEX_TELEMETRY: "1", DO_NOT_TRACK: "0" }), true);
});

test("with telemetry disabled nothing is constructed and the server lists exactly the same three tools", async () => {
  for (const env of [{ RECEIPTS_INDEX_TELEMETRY: "0" }, { DO_NOT_TRACK: "1" }]) {
    const server = createServer({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => "{}" }) });
    assert.equal(setupTelemetry(server, env), null);
    const client = new Client({ name: "test", version: "0.0.0" });
    const [a, b] = InMemoryTransport.createLinkedPair();
    await Promise.all([server.connect(a), client.connect(b)]);
    const { tools } = await client.listTools();
    assert.deepEqual(tools.map((t) => t.name).sort(), ["receipts_index", "receipts_pricing", "receipts_sample"]);
  }
});
