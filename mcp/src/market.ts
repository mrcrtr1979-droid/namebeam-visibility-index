import { makeClient, type FetchLike } from "./api.js";

const source = "https://namebeam.ai/data/explorer_data.json";
type Market = {
  market: string; niche: string; window_start: string; window_end: string;
  asked_by_engine: Record<string, number>;
};
function isDay(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}
function isMarket(value: unknown): value is Market {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<Market>;
  return typeof row.market === "string" && typeof row.niche === "string" &&
    isDay(row.window_start) && isDay(row.window_end) && !!row.asked_by_engine &&
    typeof row.asked_by_engine === "object" && !Array.isArray(row.asked_by_engine) &&
    Object.values(row.asked_by_engine).every((n) => Number.isSafeInteger(n) && n >= 0);
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/[,.]/g, " ").trim().replace(/\s+/g, " ");
}

export async function lookupMarket(city: string, trade: string, fetchImpl?: FetchLike) {
  const payload = await makeClient("https://namebeam.ai", fetchImpl).getJson("/data/explorer_data.json");
  const data = payload as { window_end?: unknown; pairs?: unknown } | null;
  if (!data || !isDay(data.window_end) || !Array.isArray(data.pairs) || !data.pairs.every(isMarket)) {
    throw new Error("The public explorer could not be read or validated.");
  }
  const cityKey = normalize(city);
  const tradeKey = normalize(trade);
  const matches = data.pairs.filter((pair) => {
    const market = normalize(pair.market);
    const niche = normalize(pair.niche);
    return (market === cityKey || market.replace(/ [a-z]{2}$/, "") === cityKey) &&
      (niche === tradeKey || niche.split(" ").includes(tradeKey));
  });
  const head = { source, data_date: data.window_end };
  if (matches.length !== 1) {
    return {
      ...head, status: matches.length ? "ambiguous" : "not_found",
      candidates: matches.map(({ market, niche }) => ({ market, trade: niche })),
    };
  }
  const pair = matches[0];
  return {
    ...head, status: "ok", market: pair.market, trade: pair.niche,
    window_start: pair.window_start, window_end: pair.window_end,
    denominator: "Recorded answers per engine (asked_by_engine in the explorer)",
    answer_counts_by_engine: pair.asked_by_engine,
  };
}
