import { test } from "node:test";
process.env.RECEIPTS_INDEX_TELEMETRY = "0";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { Client as McpClient } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "../src/server.js";
import { ApiError, USER_AGENT, type Client } from "../src/api.js";
import { handleMcpRequest, TOOLS, SUPPORTED_PROTOCOL_VERSIONS, LATEST_PROTOCOL_VERSION } from "../src/remote.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const fx = (name: string): string => readFileSync(join(ROOT, "fixtures", name), "utf8");

// A stub of the API client: serves fixtures by path and records every path asked for.
function stub(routes: Record<string, { status?: number; body: string }>, log: string[] = []): Client {
  return {
    baseUrl: "https://stub.test",
    async getJson(path: string): Promise<unknown> {
      log.push(path);
      const hit = routes[path];
      const status = hit ? (hit.status ?? 200) : 404;
      const body = hit ? hit.body : '{"error":"not found"}';
      if (status < 200 || status >= 300) throw new ApiError(status, "https://stub.test" + path, body);
      return JSON.parse(body);
    }
  };
}

const FREE = {
  "/v1/index": { body: fx("index.json") },
  "/v1/index?start=2&limit=50": { body: fx("index.json") },
  "/v1/sample": { body: fx("sample.json") },
  "/.well-known/x402": { body: fx("well-known-x402.json") },
  "/v1/manifest": { body: fx("manifest.json") }
};

function post(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request("https://api.receiptsindex.com/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body)
  });
}

async function rpc(client: Client, body: unknown, headers: Record<string, string> = {}) {
  const res = await handleMcpRequest(post(body, headers), { client });
  const text = await res.text();
  return { res, json: text ? JSON.parse(text) : null };
}

function textOf(result: { content: { text: string }[] }): string {
  return result.content[0].text;
}

test("initialize echoes a supported protocol version and declares only tools", async () => {
  const { res, json } = await rpc(stub({}), { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "0" } } });
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-type") ?? "", /^application\/json/);
  assert.equal(json.id, 1);
  assert.equal(json.result.protocolVersion, "2025-06-18");
  assert.deepEqual(Object.keys(json.result.capabilities), ["tools"]);
  assert.equal(json.result.serverInfo.name, "receipts-index");
  assert.equal(json.result.serverInfo.version, USER_AGENT.split("/")[1]);
  assert.equal(res.headers.get("mcp-session-id"), null, "stateless: no session id");
});

test("initialize with an unknown protocol version answers with the latest supported one", async () => {
  const { json } = await rpc(stub({}), { jsonrpc: "2.0", id: "a", method: "initialize", params: { protocolVersion: "2099-01-01" } });
  assert.equal(json.result.protocolVersion, LATEST_PROTOCOL_VERSION);
  assert.equal(json.id, "a");
});

test("notifications and client responses get 202 with no body", async () => {
  const c = stub({});
  const n = await handleMcpRequest(post({ jsonrpc: "2.0", method: "notifications/initialized" }), { client: c });
  assert.equal(n.status, 202);
  assert.equal(await n.text(), "");
  const r = await handleMcpRequest(post({ jsonrpc: "2.0", id: 9, result: {} }), { client: c });
  assert.equal(r.status, 202);
});

test("ping answers an empty result", async () => {
  const { json } = await rpc(stub({}), { jsonrpc: "2.0", id: 3, method: "ping" });
  assert.deepEqual(json.result, {});
});

test("tools/list matches the stdio server's tools/list, field by field", async () => {
  const stdio = createServer({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => "{}" }) });
  const mc = new McpClient({ name: "parity", version: "0.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([stdio.connect(a), mc.connect(b)]);
  const expected = (await mc.listTools()).tools;

  const { json } = await rpc(stub({}), { jsonrpc: "2.0", id: 2, method: "tools/list" });
  const got = json.result.tools;
  assert.deepEqual(got.map((t: { name: string }) => t.name), expected.map((t) => t.name));
  assert.deepEqual(got, JSON.parse(JSON.stringify(expected)), "remote tools differ from stdio tools");
  assert.equal(got.length, 3);
  assert.equal(TOOLS.length, 3);
  for (const t of got) assert.equal(t.annotations.readOnlyHint, true);
});

test("free tool receipts_sample returns the stub row and calls only /v1/sample", async () => {
  const log: string[] = [];
  const { json } = await rpc(stub(FREE, log), { jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "receipts_sample", arguments: {} } });
  assert.deepEqual(log, ["/v1/sample"]);
  assert.notEqual(json.result.isError, true);
  const data = JSON.parse(textOf(json.result));
  assert.equal(data.sample, true);
  assert.equal(data.row_id, 602);
});

test("free tool receipts_index passes start and limit, and sends no query string without them", async () => {
  const log: string[] = [];
  const c = stub(FREE, log);
  const a = await rpc(c, { jsonrpc: "2.0", id: 5, method: "tools/call", params: { name: "receipts_index", arguments: { start: 2, limit: 50 } } });
  assert.equal(JSON.parse(textOf(a.json.result)).row_count, 3);
  await rpc(c, { jsonrpc: "2.0", id: 6, method: "tools/call", params: { name: "receipts_index" } });
  assert.deepEqual(log, ["/v1/index?start=2&limit=50", "/v1/index"]);
});

test("receipts_index rejects limit 501 and a non-integer start before any API call", async () => {
  const log: string[] = [];
  const c = stub(FREE, log);
  const a = await rpc(c, { jsonrpc: "2.0", id: 7, method: "tools/call", params: { name: "receipts_index", arguments: { limit: 501 } } });
  assert.equal(a.json.result.isError, true);
  const b = await rpc(c, { jsonrpc: "2.0", id: 8, method: "tools/call", params: { name: "receipts_index", arguments: { start: 1.5 } } });
  assert.equal(b.json.result.isError, true);
  assert.equal(log.length, 0);
});

test("paid-route tool receipts_pricing returns the x402 payment terms and never touches a paid route", async () => {
  const log: string[] = [];
  const { json } = await rpc(stub(FREE, log), { jsonrpc: "2.0", id: 10, method: "tools/call", params: { name: "receipts_pricing", arguments: {} } });
  assert.deepEqual(log, ["/.well-known/x402", "/v1/manifest"]);
  const data = JSON.parse(textOf(json.result));
  assert.equal(data.currency, "USDC");
  const by = Object.fromEntries(data.routes.map((r: { route: string }) => [r.route, r]));
  assert.equal(by["/v1/row/{row_id}"].price_usdc, "0.27");
  assert.equal(by["/v1/row/{row_id}"].amount_atomic, "270000");
  assert.equal(by["/v1/day/{YYYY-MM-DD}"].price_usdc, "1.81");
  assert.equal(by["/v1/corpus"].price_usdc, "540");
  assert.ok(data.note.includes("never pays"));
  // The shape matches what the stdio server returns for the same fixtures.
  const stdio = createServer({
    fetchImpl: async (url: string) => {
      const p = new URL(url).pathname;
      const hit = (FREE as Record<string, { body: string }>)[p];
      return { ok: !!hit, status: hit ? 200 : 404, text: async () => (hit ? hit.body : "{}") };
    }
  });
  const mc = new McpClient({ name: "parity", version: "0.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([stdio.connect(a), mc.connect(b)]);
  const viaStdio = (await mc.callTool({ name: "receipts_pricing", arguments: {} })) as { content: { text: string }[] };
  assert.deepEqual(data, JSON.parse(viaStdio.content[0].text));
});

test("an API error becomes an error result with the status, as in stdio", async () => {
  const { json } = await rpc(stub({ "/v1/sample": { status: 503, body: '{"error":"corpus fetch failed"}' } }), { jsonrpc: "2.0", id: 11, method: "tools/call", params: { name: "receipts_sample", arguments: {} } });
  assert.equal(json.result.isError, true);
  assert.match(textOf(json.result), /HTTP 503/);
});

test("unknown tool and unknown method are JSON-RPC errors", async () => {
  const a = await rpc(stub({}), { jsonrpc: "2.0", id: 12, method: "tools/call", params: { name: "receipts_buy", arguments: {} } });
  assert.equal(a.json.error.code, -32602);
  const b = await rpc(stub({}), { jsonrpc: "2.0", id: 13, method: "resources/list" });
  assert.equal(b.json.error.code, -32601);
});

test("MCP-Protocol-Version header: supported passes, unsupported is 400, absent is accepted", async () => {
  const body = { jsonrpc: "2.0", id: 14, method: "tools/list" };
  for (const v of SUPPORTED_PROTOCOL_VERSIONS) assert.equal((await rpc(stub({}), body, { "mcp-protocol-version": v })).res.status, 200);
  assert.equal((await rpc(stub({}), body)).res.status, 200);
  const bad = await rpc(stub({}), body, { "mcp-protocol-version": "1999-01-01" });
  assert.equal(bad.res.status, 400);
  assert.equal(bad.json.error.code, -32600);
});

test("bad JSON is 400 with -32700; an oversized body is 413", async () => {
  const a = await handleMcpRequest(post("{not json"), { client: stub({}) });
  assert.equal(a.status, 400);
  assert.equal((await a.json()).error.code, -32700);
  const b = await handleMcpRequest(post(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping", pad: "x".repeat(70000) })), { client: stub({}) });
  assert.equal(b.status, 413);
});

test("a batch returns an array of responses and skips notifications", async () => {
  const { json } = await rpc(stub({}), [
    { jsonrpc: "2.0", id: 1, method: "ping" },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    { jsonrpc: "2.0", id: 2, method: "tools/list" }
  ]);
  assert.equal(json.length, 2);
  assert.deepEqual(json.map((r: { id: number }) => r.id), [1, 2]);
});

test("GET and DELETE are 405 with Allow, OPTIONS is a 204 CORS preflight", async () => {
  for (const method of ["GET", "DELETE"]) {
    const r = await handleMcpRequest(new Request("https://api.receiptsindex.com/mcp", { method }), { client: stub({}) });
    assert.equal(r.status, 405);
    assert.match(r.headers.get("allow") ?? "", /POST/);
    assert.equal(r.headers.get("access-control-allow-origin"), "*");
  }
  const o = await handleMcpRequest(
    new Request("https://api.receiptsindex.com/mcp", { method: "OPTIONS", headers: { origin: "https://chatgpt.com", "access-control-request-method": "POST", "access-control-request-headers": "content-type,mcp-protocol-version" } }),
    { client: stub({}) }
  );
  assert.equal(o.status, 204);
  assert.equal(o.headers.get("access-control-allow-origin"), "*");
  assert.match(o.headers.get("access-control-allow-methods") ?? "", /POST/);
  assert.match(o.headers.get("access-control-allow-headers") ?? "", /mcp-protocol-version/);
  assert.match(o.headers.get("access-control-allow-headers") ?? "", /content-type/);
});

test("every POST response carries the CORS origin header", async () => {
  const { res } = await rpc(stub({}), { jsonrpc: "2.0", id: 1, method: "ping" });
  assert.equal(res.headers.get("access-control-allow-origin"), "*");
});

test("official SDK client works against the remote handler end to end (initialize, list, call)", async () => {
  const { StreamableHTTPClientTransport } = await import("@modelcontextprotocol/sdk/client/streamableHttp.js");
  const c = stub(FREE);
  const fetchImpl = (async (input: unknown, init?: RequestInit) => handleMcpRequest(new Request(input as string, init), { client: c })) as typeof fetch;
  const transport = new StreamableHTTPClientTransport(new URL("https://api.receiptsindex.com/mcp"), { fetch: fetchImpl });
  const mc = new McpClient({ name: "sdk-http", version: "0.0.0" });
  await mc.connect(transport);
  assert.deepEqual((await mc.listTools()).tools.map((t) => t.name).sort(), ["receipts_index", "receipts_pricing", "receipts_sample"]);
  const r = (await mc.callTool({ name: "receipts_sample", arguments: {} })) as { content: { text: string }[] };
  assert.equal(JSON.parse(r.content[0].text).row_id, 602);
  await mc.close();
});

test("the generated Worker module (build-worker-module.mjs) runs standalone and lists the same tools", async () => {
  const file = join(ROOT, "dist", "worker", "mcp-remote.worker.js");
  const code = readFileSync(file, "utf8");
  assert.ok(!/^\s*(import|export)\s/m.test(code), "no import or export in the Worker module");
  const ctx = vm.createContext({ Request, Response, URL, URLSearchParams, JSON, Error, Number, String, Array, Object, Set, Promise, AbortSignal, globalThis: {} });
  const mod = vm.runInContext(code + "\nMCP_REMOTE;", ctx) as { handleMcpRequest: typeof handleMcpRequest };
  const res = await mod.handleMcpRequest(post({ jsonrpc: "2.0", id: 1, method: "tools/list" }), { client: stub({}) });
  const json = (await res.json()) as { result: { tools: unknown[] } };
  assert.deepEqual(json.result.tools, JSON.parse(JSON.stringify(TOOLS)));
});

test("server.json lists the remote Streamable HTTP endpoint next to the npm package", () => {
  const sj = JSON.parse(readFileSync(join(ROOT, "server.json"), "utf8"));
  assert.deepEqual(sj.remotes, [{ type: "streamable-http", url: "https://api.receiptsindex.com/mcp" }]);
  assert.equal(sj.packages[0].transport.type, "stdio");
  assert.match(sj.remotes[0].url, /^https?:\/\/[^\s]+$/); // registry schema pattern for remote urls
});
