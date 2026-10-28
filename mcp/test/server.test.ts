import { test } from "node:test";
// Tests never touch the network: keep telemetry off even when run without the npm script.
process.env.RECEIPTS_INDEX_TELEMETRY = "0";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "../src/server.js";
import type { FetchLike } from "../src/api.js";

// Compiled location: dist/test/server.test.js, so the package root is two levels up.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const fx = (name: string): string => readFileSync(join(ROOT, "fixtures", name), "utf8");

function mockFetch(routes: Record<string, { status?: number; body: string }>, log: string[] = []): FetchLike {
  return async (url: string) => {
    const u = new URL(url);
    log.push(u.pathname + u.search);
    const hit = routes[u.pathname + u.search] ?? routes[u.pathname];
    const status = hit ? (hit.status ?? 200) : 404;
    const body = hit ? hit.body : '{"error":"not found"}';
    return { ok: status >= 200 && status < 300, status, text: async () => body };
  };
}

async function connect(fetchImpl: FetchLike) {
  const server = createServer({ fetchImpl });
  const client = new Client({ name: "test", version: "0.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(a), client.connect(b)]);
  return client;
}

function textOf(res: unknown): string {
  const r = res as { content: { type: string; text: string }[] };
  return r.content[0].text;
}

test("lists exactly three tools", async () => {
  const c = await connect(mockFetch({}));
  const { tools } = await c.listTools();
  assert.deepEqual(tools.map((t) => t.name).sort(), ["receipts_index", "receipts_pricing", "receipts_sample"]);
  for (const t of tools) assert.equal(t.annotations?.readOnlyHint, true);
});

test("receipts_index passes start and limit and returns the fixture", async () => {
  const log: string[] = [];
  const c = await connect(mockFetch({ "/v1/index": { body: fx("index.json") } }, log));
  const res = await c.callTool({ name: "receipts_index", arguments: { start: 2, limit: 50 } });
  assert.equal(log[0], "/v1/index?start=2&limit=50");
  const data = JSON.parse(textOf(res));
  assert.equal(data.row_count, 3);
  assert.equal(data.rows[0].row_id, 1);
  assert.ok(Array.isArray(data.days));
});

test("receipts_index with no arguments sends no query string", async () => {
  const log: string[] = [];
  const c = await connect(mockFetch({ "/v1/index": { body: fx("index.json") } }, log));
  await c.callTool({ name: "receipts_index", arguments: {} });
  assert.equal(log[0], "/v1/index");
});

test("receipts_index rejects limit above 500 before any request", async () => {
  const log: string[] = [];
  const c = await connect(mockFetch({ "/v1/index": { body: fx("index.json") } }, log));
  const res = (await c.callTool({ name: "receipts_index", arguments: { limit: 501 } })) as { isError?: boolean };
  assert.equal(res.isError, true);
  assert.equal(log.length, 0);
});

test("receipts_sample returns the recorded sample row", async () => {
  const log: string[] = [];
  const c = await connect(mockFetch({ "/v1/sample": { body: fx("sample.json") } }, log));
  const res = await c.callTool({ name: "receipts_sample", arguments: {} });
  assert.equal(log[0], "/v1/sample");
  const data = JSON.parse(textOf(res));
  assert.equal(data.sample, true);
  assert.equal(data.row_id, 602);
  assert.equal(data.row.date_utc, "2026-09-01");
  assert.match(data.csv, /^date_utc,check_id,/);
});

test("receipts_pricing joins the discovery url list with manifest prices", async () => {
  const log: string[] = [];
  const c = await connect(
    mockFetch({ "/.well-known/x402": { body: fx("well-known-x402.json") }, "/v1/manifest": { body: fx("manifest.json") } }, log)
  );
  const res = await c.callTool({ name: "receipts_pricing", arguments: {} });
  assert.deepEqual(log, ["/.well-known/x402", "/v1/manifest"]);
  const data = JSON.parse(textOf(res));
  const by = Object.fromEntries(data.routes.map((r: { route: string }) => [r.route, r]));
  assert.equal(by["/v1/row/{row_id}"].price_usdc, "0.27");
  assert.equal(by["/v1/row/{row_id}"].amount_atomic, "270000");
  assert.equal(by["/v1/day/{YYYY-MM-DD}"].price_usdc, "1.81");
  assert.equal(by["/v1/corpus"].price_usdc, "540");
  assert.equal(by["/v1/feed/founding-partner"].price_usdc, "200");
  assert.equal(by["/v1/feed/founding-partner"].note, "per month");
  assert.equal(by["/v1/row/{row_id}"].url, "https://api.receiptsindex.com/v1/row/{row_id}");
  assert.equal(data.currency, "USDC");
});

test("receipts_pricing reads the older discovery format that carries prices inline", async () => {
  const c = await connect(mockFetch({ "/.well-known/x402": { body: fx("well-known-x402-1019.json") } }));
  const res = await c.callTool({ name: "receipts_pricing", arguments: {} });
  const data = JSON.parse(textOf(res));
  assert.equal(data.routes.length, 4);
  assert.equal(data.network, "eip155:8453");
  assert.equal(data.routes.find((r: { route: string }) => r.route === "/v1/corpus").amount_atomic, "540000000");
});

test("receipts_pricing makes only GET calls to the two free paths and never touches a paid route", async () => {
  const log: string[] = [];
  const c = await connect(
    mockFetch({ "/.well-known/x402": { body: fx("well-known-x402.json") }, "/v1/manifest": { body: fx("manifest.json") } }, log)
  );
  await c.callTool({ name: "receipts_pricing", arguments: {} });
  for (const p of log) assert.ok(p === "/.well-known/x402" || p === "/v1/manifest", "unexpected path " + p);
});

test("an HTTP error becomes an error result with the status", async () => {
  const c = await connect(mockFetch({ "/v1/sample": { status: 503, body: '{"error":"corpus fetch failed"}' } }));
  const res = (await c.callTool({ name: "receipts_sample", arguments: {} })) as { isError?: boolean };
  assert.equal(res.isError, true);
  assert.match(textOf(res), /HTTP 503/);
});

test("package, server.json and README agree", () => {
  const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
  const sj = JSON.parse(readFileSync(join(ROOT, "server.json"), "utf8"));
  assert.equal(sj.name, "io.github.mrcrtr1979-droid/receipts-index");
  assert.equal(pkg.mcpName, sj.name);
  assert.equal(sj.version, pkg.version);
  assert.equal(sj.packages[0].identifier, pkg.name);
  assert.equal(sj.packages[0].version, pkg.version);
  assert.equal(sj.packages[0].transport.type, "stdio");
  assert.ok(sj.description.length >= 1 && sj.description.length <= 100, "registry description limit is 100");
  assert.match(sj.name, /^[a-zA-Z0-9.-]+\/[a-zA-Z0-9._-]+$/);
  assert.ok(pkg.bin["receipts-index-mcp"]);
  const readme = readFileSync(join(ROOT, "README.md"), "utf8").trimEnd();
  assert.ok(readme.endsWith("Namebeam, a Carter Enterprise property. This is the first leg."));
});

function walk(dir: string, out: string[] = []): string[] {
  for (const n of readdirSync(dir)) {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

test("copy rules: ASCII only, no em or en dashes, no banned words in shipped text", () => {
  const files = [join(ROOT, "README.md"), join(ROOT, "server.json"), join(ROOT, "package.json"), join(ROOT, "LICENSE")];
  const srcDir = join(ROOT, "src");
  files.push(...walk(srcDir).filter((f) => f.endsWith(".ts")));
  const banned = ["honest", "seamless", "powerful", "revolutionary", "cutting-edge", "game-changing", "unlock", "supercharge", "best-in-class", "world-class", "state-of-the-art", "leverage"];
  for (const f of files) {
    const text = readFileSync(f, "utf8");
    // eslint-disable-next-line no-control-regex
    assert.ok(!/[^\x00-\x7F]/.test(text), "non-ASCII in " + f);
    for (const w of banned) assert.ok(!text.toLowerCase().includes(w), "banned word " + w + " in " + f);
  }
});
