// Remote MCP endpoint (Streamable HTTP, stateless, JSON responses) for the Receipts Index API.
//
// Same three tools as the stdio server in server.ts, read-only, no auth, no session, no key handling.
// This file uses only the Fetch API types (Request, Response) and ./api.js, so the compiled output can be
// inlined into a Cloudflare Worker (scripts/build-worker-module.mjs) and also runs in Node 20+.
// It does not import the MCP SDK on purpose: a Worker is deployed as one pasted file.
//
// Spec: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports (Streamable HTTP).
// A server may answer a POST with application/json instead of an SSE stream, and may answer GET with 405
// when it has no server-initiated stream. This server does both.
import { ApiError, buildPricing, USER_AGENT, type Client } from "./api.js";

export const REMOTE_SERVER_NAME = "receipts-index";
// Version comes from api.ts USER_AGENT so a release bump does not add a fifth place to edit.
export const REMOTE_SERVER_VERSION = USER_AGENT.split("/")[1] ?? "0.0.0";
export const LATEST_PROTOCOL_VERSION = "2025-11-25";
export const DEFAULT_PROTOCOL_VERSION = "2025-03-26"; // assumed when the MCP-Protocol-Version header is absent
export const SUPPORTED_PROTOCOL_VERSIONS = [LATEST_PROTOCOL_VERSION, "2025-06-18", "2025-03-26", "2024-11-05"];
export const MAX_BODY_BYTES = 65536;

export const INSTRUCTIONS =
  "Read-only access to the Receipts Index API. receipts_index and receipts_sample are free; receipts_pricing lists the paid x402 routes and prices. This server never pays and never signs anything; paid rows are bought with an x402 client against the listed route.";

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
type Obj = Record<string, unknown>;

interface ToolDef {
  name: string;
  title: string;
  description: string;
  inputSchema: Obj;
  annotations: Obj;
  execution: Obj;
}

const DRAFT07 = "http://json-schema.org/draft-07/schema#";
const ANNOTATIONS = { readOnlyHint: true, openWorldHint: true };
const EXECUTION = { taskSupport: "forbidden" };

// Names, titles, descriptions and schemas are copied from server.ts. test/remote.test.ts compares this list
// to the stdio server's tools/list, field by field, and fails on any drift.
export const TOOLS: ToolDef[] = [
  {
    name: "receipts_index",
    title: "Receipts Index: free row index",
    description:
      "Calls GET /v1/index on the Receipts API. Free. Returns row ids and UTC dates (no row content), the row count, the first and last dates, and the list of dates that have rows. Use start and limit to page through row ids. Row content is sold per call through x402 and is not returned here.",
    inputSchema: {
      type: "object",
      properties: {
        start: { description: "First row id of the page. Default 1.", type: "integer", minimum: 1, maximum: 9007199254740991 },
        limit: { description: "Rows per page, 1 to 500. The API default applies when omitted.", type: "integer", minimum: 1, maximum: 500 }
      },
      $schema: DRAFT07
    },
    annotations: { title: "Receipts Index: free row index", ...ANNOTATIONS },
    execution: EXECUTION
  },
  {
    name: "receipts_sample",
    title: "Receipts Index: free sample row",
    description:
      "Calls GET /v1/sample on the Receipts API. Free. Returns one real record row labeled as a sample, with its row id, the paid route and price for that row, and the row as fields and as CSV. It takes no parameters and is not a purchase.",
    inputSchema: { type: "object", properties: {}, $schema: DRAFT07 },
    annotations: { title: "Receipts Index: free sample row", ...ANNOTATIONS },
    execution: EXECUTION
  },
  {
    name: "receipts_pricing",
    title: "Receipts Index: paid endpoints and prices",
    description:
      "Reads GET /.well-known/x402 (and /v1/manifest for the price text) on the Receipts API and returns the paid endpoints with their prices in USDC. Free to call. It never pays and never signs anything. Paying is done by an x402 client against the listed route.",
    inputSchema: { type: "object", properties: {}, $schema: DRAFT07 },
    annotations: { title: "Receipts Index: paid endpoints and prices", ...ANNOTATIONS },
    execution: EXECUTION
  }
];

type ToolResult = { content: { type: "text"; text: string }[]; isError?: boolean };

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}

function fail(e: unknown): ToolResult {
  if (e instanceof ApiError) {
    return { isError: true, content: [{ type: "text", text: "Request failed: HTTP " + e.status + " from " + e.url + ". " + e.body }] };
  }
  const msg = e instanceof Error ? e.message : String(e);
  return { isError: true, content: [{ type: "text", text: "Request failed: " + msg }] };
}

function invalid(tool: string, detail: string): ToolResult {
  return { isError: true, content: [{ type: "text", text: "MCP error -32602: Input validation error: Invalid arguments for tool " + tool + ": " + detail }] };
}

function intArg(args: Obj, key: string, min: number, max: number): { value?: number; error?: string } {
  const v = args[key];
  if (v === undefined) return {};
  if (typeof v !== "number" || !Number.isInteger(v)) return { error: "expected integer at " + key };
  if (v < min) return { error: "Too small: expected number to be >=" + min + " at " + key };
  if (v > max) return { error: "Too big: expected number to be <=" + max + " at " + key };
  return { value: v };
}

async function callTool(client: Client, name: string, rawArgs: unknown): Promise<ToolResult> {
  const args: Obj = rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs) ? (rawArgs as Obj) : {};
  if (rawArgs !== undefined && rawArgs !== null && args !== rawArgs) return invalid(name, "expected object");
  try {
    if (name === "receipts_index") {
      const s = intArg(args, "start", 1, Number.MAX_SAFE_INTEGER);
      if (s.error) return invalid(name, s.error);
      const l = intArg(args, "limit", 1, 500);
      if (l.error) return invalid(name, l.error);
      const q = new URLSearchParams();
      if (s.value !== undefined) q.set("start", String(s.value));
      if (l.value !== undefined) q.set("limit", String(l.value));
      const qs = q.toString();
      return ok(await client.getJson("/v1/index" + (qs ? "?" + qs : "")));
    }
    if (name === "receipts_sample") return ok(await client.getJson("/v1/sample"));
    if (name === "receipts_pricing") {
      const wellKnown = await client.getJson("/.well-known/x402");
      let manifest: unknown = {};
      try {
        manifest = await client.getJson("/v1/manifest");
      } catch {
        // prices may already be inside the discovery file
      }
      return ok(buildPricing(wellKnown, manifest));
    }
  } catch (e) {
    return fail(e);
  }
  throw new RpcError(-32602, "Unknown tool: " + name);
}

class RpcError extends Error {
  constructor(public code: number, message: string) {
    super(message);
  }
}

function rpcResult(id: unknown, result: unknown): Obj {
  return { jsonrpc: "2.0", id: id as Json, result };
}

function rpcError(id: unknown, code: number, message: string): Obj {
  return { jsonrpc: "2.0", id: (id ?? null) as Json, error: { code, message } };
}

// Handles one JSON-RPC message. Returns null for notifications and for responses from the client.
async function handleMessage(msg: unknown, client: Client, serverVersion: string): Promise<Obj | null> {
  if (!msg || typeof msg !== "object" || Array.isArray(msg)) return rpcError(null, -32600, "Invalid Request");
  const m = msg as Obj;
  if (m.jsonrpc !== "2.0") return rpcError(m.id, -32600, "Invalid Request: jsonrpc must be 2.0");
  const hasId = m.id !== undefined && m.id !== null;
  if (typeof m.method !== "string") {
    // A JSON-RPC response sent by the client. This server never makes requests, so there is nothing to match.
    if ("result" in m || "error" in m) return null;
    return rpcError(m.id, -32600, "Invalid Request: method missing");
  }
  if (!hasId) return null; // notification (for example notifications/initialized)
  const params = (m.params && typeof m.params === "object" ? m.params : {}) as Obj;
  try {
    switch (m.method) {
      case "initialize": {
        const asked = typeof params.protocolVersion === "string" ? params.protocolVersion : "";
        const protocolVersion = SUPPORTED_PROTOCOL_VERSIONS.includes(asked) ? asked : LATEST_PROTOCOL_VERSION;
        return rpcResult(m.id, {
          protocolVersion,
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: REMOTE_SERVER_NAME, version: serverVersion },
          instructions: INSTRUCTIONS
        });
      }
      case "ping":
        return rpcResult(m.id, {});
      case "tools/list":
        return rpcResult(m.id, { tools: TOOLS });
      case "tools/call": {
        if (typeof params.name !== "string") throw new RpcError(-32602, "Invalid params: name is required");
        return rpcResult(m.id, await callTool(client, params.name, params.arguments));
      }
      default:
        return rpcError(m.id, -32601, "Method not found: " + m.method);
    }
  } catch (e) {
    if (e instanceof RpcError) return rpcError(m.id, e.code, e.message);
    return rpcError(m.id, -32603, "Internal error");
  }
}

// No credentials are ever accepted or sent, so any origin may call this read-only public endpoint.
export const CORS_HEADERS: Record<string, string> = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, GET, DELETE, OPTIONS",
  "access-control-allow-headers": "content-type, accept, mcp-protocol-version, mcp-session-id, last-event-id, authorization",
  "access-control-expose-headers": "mcp-protocol-version, mcp-session-id",
  "access-control-max-age": "86400"
};

function json(body: unknown, status: number, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", ...CORS_HEADERS, ...extra }
  });
}

export interface RemoteOptions {
  client: Client;
  serverVersion?: string;
}

export async function handleMcpRequest(request: Request, opts: RemoteOptions): Promise<Response> {
  const serverVersion = opts.serverVersion ?? REMOTE_SERVER_VERSION;
  const method = request.method.toUpperCase();
  if (method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
  if (method !== "POST") {
    // No server-initiated stream and no sessions, so GET and DELETE are 405 as the transport spec allows.
    return json(rpcError(null, -32000, "Method not allowed. POST JSON-RPC messages to this endpoint."), 405, { allow: "POST, OPTIONS" });
  }

  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return json(rpcError(null, -32600, "Request body too large"), 413);
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) return json(rpcError(null, -32600, "Request body too large"), 413);

  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return json(rpcError(null, -32700, "Parse error"), 400);
  }

  // Version header: absent means 2025-03-26; an unsupported value is a 400. initialize carries its version
  // in the body, so the header is not checked for it.
  const batch = Array.isArray(body) ? body : [body];
  const onlyInitialize = batch.length === 1 && (batch[0] as Obj | null)?.method === "initialize";
  const headerVersion = request.headers.get("mcp-protocol-version");
  if (headerVersion !== null && !onlyInitialize && !SUPPORTED_PROTOCOL_VERSIONS.includes(headerVersion)) {
    return json(rpcError(null, -32600, "Unsupported MCP-Protocol-Version: " + headerVersion + ". Supported: " + SUPPORTED_PROTOCOL_VERSIONS.join(", ")), 400);
  }
  if (Array.isArray(body) && body.length === 0) return json(rpcError(null, -32600, "Invalid Request: empty batch"), 400);

  const out: Obj[] = [];
  for (const msg of batch) {
    const r = await handleMessage(msg, opts.client, serverVersion);
    if (r) out.push(r);
  }
  if (out.length === 0) return new Response(null, { status: 202, headers: CORS_HEADERS }); // notifications and responses only
  return json(Array.isArray(body) ? out : out[0], 200);
}
