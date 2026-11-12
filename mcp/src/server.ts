import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import {
  ApiError,
  buildPricing,
  makeClient,
  withNextStep,
  type Client,
  type FetchLike,
  DEFAULT_BASE_URL,
  FREE_CHECK_MAX_INPUT,
  FREE_CHECK_TOOL_TEXT
} from "./api.js";

export const SERVER_NAME = "receipts-index";
export const SERVER_VERSION = "0.1.6";

type ToolResult = { content: { type: "text"; text: string }[]; isError?: boolean };

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}

function text(s: string): ToolResult {
  return { content: [{ type: "text", text: s }] };
}

function fail(e: unknown): ToolResult {
  if (e instanceof ApiError) {
    return { isError: true, content: [{ type: "text", text: "Request failed: HTTP " + e.status + " from " + e.url + ". " + e.body }] };
  }
  const msg = e instanceof Error ? e.message : String(e);
  return { isError: true, content: [{ type: "text", text: "Request failed: " + msg }] };
}

export interface ServerOptions {
  baseUrl?: string;
  fetchImpl?: FetchLike;
  client?: Client;
}

export function createServer(opts: ServerOptions = {}): McpServer {
  const client = opts.client ?? makeClient(opts.baseUrl ?? DEFAULT_BASE_URL, opts.fetchImpl);
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION });

  server.registerTool(
    "receipts_index",
    {
      title: "Receipts Index: free row index",
      description:
        "Calls GET /v1/index on the Receipts API. Free. Returns row ids and UTC dates (no row content), the row count, the first and last dates, and the list of dates that have rows. Use start and limit to page through row ids. Row content is sold per call through x402 and is not returned here.",
      inputSchema: {
        start: z.number().int().min(1).optional().describe("First row id of the page. Default 1."),
        limit: z.number().int().min(1).max(500).optional().describe("Rows per page, 1 to 500. The API default applies when omitted.")
      },
      annotations: { title: "Receipts Index: free row index", readOnlyHint: true, openWorldHint: true }
    },
    async ({ start, limit }) => {
      try {
        const q = new URLSearchParams();
        if (start !== undefined) q.set("start", String(start));
        if (limit !== undefined) q.set("limit", String(limit));
        const qs = q.toString();
        return ok(await client.getJson("/v1/index" + (qs ? "?" + qs : "")));
      } catch (e) {
        return fail(e);
      }
    }
  );

  server.registerTool(
    "receipts_sample",
    {
      title: "Receipts Index: free sample row",
      description:
        "Calls GET /v1/sample on the Receipts API. Free. Returns one real record row labeled as a sample, with its row id, the paid route and price for that row, and the row as fields and as CSV. It takes no parameters and is not a purchase.",
      inputSchema: {},
      annotations: { title: "Receipts Index: free sample row", readOnlyHint: true, openWorldHint: true }
    },
    async () => {
      try {
        return ok(withNextStep(await client.getJson("/v1/sample")));
      } catch (e) {
        return fail(e);
      }
    }
  );

  server.registerTool(
    "receipts_pricing",
    {
      title: "Receipts Index: paid endpoints and prices",
      description:
        "Reads GET /.well-known/x402 (and /v1/manifest for the price text) on the Receipts API and returns the paid endpoints with their prices in USDC. Free to call. It never pays and never signs anything. Paying is done by an x402 client against the listed route.",
      inputSchema: {},
      annotations: { title: "Receipts Index: paid endpoints and prices", readOnlyHint: true, openWorldHint: true }
    },
    async () => {
      try {
        const wellKnown = await client.getJson("/.well-known/x402");
        let manifest: unknown = {};
        try {
          manifest = await client.getJson("/v1/manifest");
        } catch {
          // prices may already be inside the discovery file
        }
        return ok(buildPricing(wellKnown, manifest));
      } catch (e) {
        return fail(e);
      }
    }
  );

  // LB-48 (2026-11-12): front door to the free check. No request, no data read; the text is the link and one sentence.
  server.registerTool(
    "namebeam_free_check",
    {
      title: "Namebeam: free check link",
      description:
        "Returns the link to the free Namebeam check for one US city and trade. Free. Makes no request and reads no data. The check reports a count of which businesses one AI engine named for that city and trade on a dated run; it is not a ranking.",
      inputSchema: {
        city: z.string().max(FREE_CHECK_MAX_INPUT).optional().describe("US city, up to 80 characters."),
        trade: z.string().max(FREE_CHECK_MAX_INPUT).optional().describe("Trade or business type, up to 80 characters.")
      },
      annotations: { title: "Namebeam: free check link", readOnlyHint: true, openWorldHint: false }
    },
    async () => text(FREE_CHECK_TOOL_TEXT)
  );

  return server;
}
