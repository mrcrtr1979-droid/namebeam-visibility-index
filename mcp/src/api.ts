export const DEFAULT_BASE_URL = "https://api.receiptsindex.com";
export const USER_AGENT = "receipts-index-mcp/0.1.4";

export type FetchLike = (url: string, init?: { headers?: Record<string, string>; signal?: AbortSignal }) => Promise<{
  ok: boolean;
  status: number;
  text(): Promise<string>;
}>;

export class ApiError extends Error {
  constructor(public status: number, public url: string, public body: string) {
    super("HTTP " + status + " from " + url);
  }
}

export interface Client {
  baseUrl: string;
  getJson(path: string): Promise<unknown>;
}

export function makeClient(baseUrl: string = DEFAULT_BASE_URL, fetchImpl?: FetchLike, timeoutMs = 20000): Client {
  const f: FetchLike = fetchImpl ?? ((globalThis.fetch as unknown) as FetchLike);
  const base = baseUrl.replace(/\/+$/, "");
  return {
    baseUrl: base,
    async getJson(path: string): Promise<unknown> {
      const url = base + path;
      const res = await f(url, {
        headers: { accept: "application/json", "user-agent": USER_AGENT },
        signal: AbortSignal.timeout(timeoutMs)
      });
      const text = await res.text();
      if (!res.ok) throw new ApiError(res.status, url, text.slice(0, 500));
      try {
        return JSON.parse(text);
      } catch {
        throw new ApiError(res.status, url, "response was not JSON: " + text.slice(0, 200));
      }
    }
  };
}

export interface PricedRoute {
  route: string;
  url: string;
  method: string;
  price_usdc: string | null;
  amount_atomic: string | null;
  note: string | null;
}

// Turns an example paid URL such as /v1/row/602 into its route template.
export function routeTemplate(urlOrTemplate: string): string {
  let path = urlOrTemplate;
  try {
    path = new URL(urlOrTemplate).pathname;
  } catch {
    // keep as given
  }
  path = decodeURIComponent(path);
  if (/^\/v1\/row\/[^/]+$/.test(path)) return "/v1/row/{row_id}";
  if (/^\/v1\/day\/[^/]+$/.test(path)) return "/v1/day/{YYYY-MM-DD}";
  return path;
}

function templateUrl(url: string, route: string): string {
  if (!route.includes("{")) return url;
  try {
    return new URL(url).origin + route;
  } catch {
    return route;
  }
}

// Reads "270000 USDC atomic units (0.27 USDC)" and "... (200 USDC per month)".
export function parsePriceString(s: string): { amount_atomic: string | null; price_usdc: string | null; note: string | null } {
  const atomic = s.match(/^(\d+)\s+USDC atomic units/);
  const usd = s.match(/\(([0-9.]+)\s*USDC(?:\s+([^)]*))?\)/);
  return {
    amount_atomic: atomic ? atomic[1] : null,
    price_usdc: usd ? usd[1] : null,
    note: usd && usd[2] ? usd[2] : null
  };
}

export function buildPricing(wellKnown: unknown, manifest: unknown) {
  const wk = (wellKnown ?? {}) as Record<string, unknown>;
  const mf = (manifest ?? {}) as Record<string, unknown>;
  const priceInfo = (mf.pricing_info ?? {}) as Record<string, string>;
  const entries = Array.isArray(wk.resources) ? (wk.resources as unknown[]) : [];
  const routes: PricedRoute[] = [];
  const seen = new Set<string>();
  for (const e of entries) {
    const obj = typeof e === "string" ? { url: e } : ((e ?? {}) as Record<string, unknown>);
    const url = typeof obj.url === "string" ? obj.url : "";
    if (!url) continue;
    const route = routeTemplate(url);
    if (seen.has(route)) continue;
    seen.add(route);
    let price_usdc: string | null = typeof obj.price === "string" ? obj.price : null;
    let amount_atomic: string | null = typeof obj.amount === "string" ? obj.amount : null;
    let note: string | null = null;
    const fromManifest = priceInfo[route];
    if (fromManifest) {
      const p = parsePriceString(fromManifest);
      price_usdc = price_usdc ?? p.price_usdc;
      amount_atomic = amount_atomic ?? p.amount_atomic;
      note = p.note;
    }
    routes.push({ route, url: templateUrl(url, route), method: "GET", price_usdc, amount_atomic, note });
  }
  // Payment terms: the discovery file first, then the manifest "payment" block (Worker v3.5 and later).
  const pay = (mf.payment ?? {}) as Record<string, unknown>;
  const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
  return {
    currency: "USDC",
    network: str(wk.network) ?? str(pay.network) ?? "eip155:8453 (Base mainnet, per the API manifest text; confirm in the 402 challenge)",
    asset: str(wk.asset) ?? str(pay.asset),
    pay_to: str(wk.payTo) ?? str(pay.pay_to),
    facilitator: str(wk.facilitator) ?? str(pay.facilitator),
    routes,
    source: ["/.well-known/x402", "/v1/manifest"],
    note: "Prices are as listed by the API. This tool never pays. To buy, use an x402 client against the route; the 402 response carries the exact payment terms."
  };
}
