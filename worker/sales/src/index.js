// namebeam-sales: Stripe checkout.session.completed and invoice.paid (renewals) -> D1 row -> GitHub repository_dispatch (sale id only).
// TEST MODE build. Secrets by name only: STRIPE_WEBHOOK_SECRET, GH_DISPATCH_TOKEN, BREVO_API_KEY.
const ENC = new TextEncoder();
const TOLERANCE_SEC = 300;

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export async function verifyStripeSignature(raw, header, secret, nowSec) {
  if (!header || !secret) return { ok: false, reason: 'missing' };
  let t = null;
  const v1 = [];
  for (const part of header.split(',')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k === 't') t = v;
    else if (k === 'v1') v1.push(v);
  }
  if (!t || !/^\d+$/.test(t) || v1.length === 0) return { ok: false, reason: 'malformed' };
  if (Math.abs(nowSec - Number(t)) > TOLERANCE_SEC) return { ok: false, reason: 'stale' };
  const key = await crypto.subtle.importKey('raw', ENC.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = hex(await crypto.subtle.sign('HMAC', key, ENC.encode(`${t}.${raw}`)));
  return v1.some((s) => safeEqual(s, mac)) ? { ok: true } : { ok: false, reason: 'mismatch' };
}

// Offer ladder, matched on mode and amount_total (cents). Agency 397 and segment 99 stay manual.
export function classifyOffer(session) {
  const amt = session.amount_total;
  const sub = session.mode === 'subscription';
  if (sub && amt === 4900) return { offer: 'market_1', auto: true, max: 1 };
  if (sub && amt === 19900) return { offer: 'market_5', auto: true, max: 5 };
  if (amt === 9900) return { offer: 'segment_99', auto: false };
  if (amt === 39700) return { offer: 'agency_397', auto: false };
  return { offer: 'unknown', auto: false };
}

export function customText(session) {
  const fields = Array.isArray(session.custom_fields) ? session.custom_fields : [];
  const vals = [];
  for (const f of fields) {
    const v = f && f.text && typeof f.text.value === 'string' ? f.text.value.trim() : '';
    if (v) vals.push({ key: String(f.key || ''), label: String((f.label && f.label.custom) || ''), value: v });
  }
  const pairs = vals.find((f) => /pair/i.test(f.key + f.label));
  if (pairs) return pairs.value;
  const m = vals.find((f) => /market|city/i.test(f.key + f.label));
  const n = vals.find((f) => /niche|trade|category/i.test(f.key + f.label));
  if (m && n) return `${m.value}|${n.value}`;
  return vals.length ? vals[0].value : '';
}

export function parsePairs(text, max) {
  const chunks = String(text || '').split(/[;\n]+/).map((s) => s.trim()).filter(Boolean);
  if (chunks.length === 0) return { error: 'no market given' };
  if (chunks.length > max) return { error: `more than ${max} market(s)` };
  const pairs = [];
  for (const c of chunks) {
    const bits = c.split('|').map((s) => s.trim());
    if (bits.length !== 2 || !bits[0] || !bits[1]) return { error: `bad pair: ${c.slice(0, 60)}` };
    if (!/^[A-Za-z][A-Za-z .'-]{1,60} [A-Z]{2}$/.test(bits[0])) return { error: `market needs "City ST": ${bits[0].slice(0, 40)}` };
    if (bits[1].length > 80) return { error: 'niche too long' };
    pairs.push({ market: bits[0], niche: bits[1] });
  }
  return { pairs };
}

// Public repo: the dispatch payload carries the sale id and nothing else. The runner reads the row from D1.
async function dispatchBuild(env, saleId, fetchFn) {
  const res = await fetchFn(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'namebeam-sales',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ event_type: 'namebeam-sale', client_payload: { sale_id: saleId } }),
  });
  return res.status;
}

// Alert only (agency pack and other manual offers). No buyer data in the alert. Dry run unless DRY_RUN is "false".
async function alertManual(env, row, fetchFn) {
  const text = `Manual offer paid: ${row.offer}, ${row.amount_cents} ${row.currency}, session ${row.sale_id}. Status NEEDS_HUMAN. Fulfil by hand.`;
  if (env.DRY_RUN !== 'false' || !env.BREVO_API_KEY || !env.ALERT_TO) return { sent: false, text };
  const r = await fetchFn('https://api.brevo.com/v3/smtp/email', {
    method: 'POST',
    headers: { 'api-key': env.BREVO_API_KEY, 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      sender: { name: 'Namebeam sales', email: 'terry@namebeam.ai' },
      to: [{ email: env.ALERT_TO }],
      subject: `Manual order: ${row.offer}`,
      textContent: text,
    }),
  });
  return { sent: r.status >= 200 && r.status < 300, text };
}

const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { 'content-type': 'application/json' } });

const INSERT_SQL = 'INSERT OR IGNORE INTO sales_events (sale_id,event_id,created_utc,livemode,offer,amount_cents,currency,buyer_email,buyer_name,custom_text,pairs_json,subscription_id,invoice_id,status,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)';

// One row per payment. Returns a Response. Idempotent: a repeated event id or sale id inserts nothing.
async function storeAndDispatch(env, row, alertManualOffer, deps) {
  const fetchFn = deps.fetch || fetch;
  const now = deps.now || new Date();
  const ins = await env.DB.prepare(INSERT_SQL).bind(
    row.sale_id, row.event_id, row.created_utc, row.livemode, row.offer, row.amount_cents, row.currency, row.buyer_email,
    row.buyer_name, row.custom_text, row.pairs_json, row.subscription_id, row.invoice_id, row.status, row.note
  ).run();
  if (!ins.meta || ins.meta.changes === 0) return json({ duplicate: true, event_id: row.event_id }, 200);
  if (alertManualOffer) await alertManual(env, row, fetchFn);
  if (row.status !== 'PAID') return json({ stored: true, status: row.status, note: row.note }, 200);
  let code = 0;
  try { code = await dispatchBuild(env, row.sale_id, fetchFn); } catch { code = 0; }
  if (code === 204) {
    await env.DB.prepare("UPDATE sales_events SET status='DISPATCHED', dispatched_utc=? WHERE sale_id=? AND status='PAID'").bind(now.toISOString(), row.sale_id).run();
    return json({ stored: true, status: 'DISPATCHED' }, 200);
  }
  await env.DB.prepare('UPDATE sales_events SET note=? WHERE sale_id=?').bind(`dispatch http ${code}`, row.sale_id).run();
  return json({ stored: true, status: 'PAID', note: `dispatch http ${code}` }, 200);
}

function invoiceSubscription(inv) {
  if (typeof inv.subscription === 'string') return inv.subscription;
  const p = inv.parent && inv.parent.subscription_details;
  return p && typeof p.subscription === 'string' ? p.subscription : null;
}

async function handleInvoice(event, env, deps) {
  const now = deps.now || new Date();
  const inv = event.data && event.data.object ? event.data.object : {};
  // The first invoice (subscription_create) belongs to the checkout event. Only real renewals fulfil here.
  if (inv.billing_reason !== 'subscription_cycle') return json({ ignored: `invoice ${inv.billing_reason || 'unknown'}` }, 200);
  if (inv.status !== 'paid') return json({ ignored: 'invoice not paid' }, 200);
  const sub = invoiceSubscription(inv);
  if (!sub || !inv.id) return json({ ignored: 'invoice without subscription' }, 200);
  const found = await env.DB.prepare(
    'SELECT offer,buyer_email,buyer_name,pairs_json FROM sales_events WHERE subscription_id=? AND invoice_id IS NULL AND pairs_json IS NOT NULL ORDER BY created_utc LIMIT 1'
  ).bind(sub).all();
  const orig = found.results && found.results[0];
  const row = {
    sale_id: inv.id, event_id: event.id, created_utc: now.toISOString(), livemode: event.livemode ? 1 : 0,
    offer: orig ? orig.offer : 'unknown', amount_cents: inv.amount_paid ?? null, currency: inv.currency ?? null,
    buyer_email: orig ? orig.buyer_email : null, buyer_name: orig ? orig.buyer_name : null, custom_text: null,
    pairs_json: orig ? orig.pairs_json : null, subscription_id: sub, invoice_id: inv.id, status: 'PAID', note: null,
  };
  if (!orig) { row.status = 'NEEDS_HUMAN'; row.note = 'renewal with no original sale on file'; }
  else if (inv.currency !== 'usd' || !(inv.amount_paid > 0)) { row.status = 'NEEDS_HUMAN'; row.note = 'renewal amount or currency unexpected'; }
  return storeAndDispatch(env, row, false, deps);
}

export async function handleStripe(request, env, deps = {}) {
  const now = deps.now || new Date();
  const raw = await request.text();
  const sig = await verifyStripeSignature(raw, request.headers.get('stripe-signature'), env.STRIPE_WEBHOOK_SECRET, Math.floor(now.getTime() / 1000));
  if (!sig.ok) return json({ error: 'bad signature', reason: sig.reason }, 400);
  let event;
  try { event = JSON.parse(raw); } catch { return json({ error: 'bad json' }, 400); }
  if (event.livemode === true && env.ALLOW_LIVE !== 'true') return json({ ignored: 'live event while test mode only' }, 200);
  if (event.type === 'invoice.paid') return handleInvoice(event, env, deps);
  if (event.type !== 'checkout.session.completed') return json({ ignored: event.type }, 200);
  const s = event.data && event.data.object ? event.data.object : {};
  if (s.payment_status !== 'paid') return json({ ignored: 'not paid yet' }, 200);

  const cls = classifyOffer(s);
  const text = customText(s);
  let status = 'PAID';
  let note = null;
  let pairs = null;
  if (s.currency !== 'usd') { status = 'NEEDS_HUMAN'; note = 'currency not usd'; }
  else if (!cls.auto) { status = 'NEEDS_HUMAN'; note = `${cls.offer} is a manual offer`; }
  else {
    const p = parsePairs(text, cls.max);
    if (p.error) { status = 'NEEDS_INTAKE_CORRECTION'; note = p.error; } else pairs = p.pairs;
  }
  const email = (s.customer_details && s.customer_details.email) || s.customer_email || null;
  if (status === 'PAID' && !email) { status = 'NEEDS_HUMAN'; note = 'no buyer email'; }

  const row = {
    sale_id: s.id, event_id: event.id, created_utc: now.toISOString(), livemode: event.livemode ? 1 : 0,
    offer: cls.offer, amount_cents: s.amount_total ?? null, currency: s.currency ?? null,
    buyer_email: email, buyer_name: (s.customer_details && s.customer_details.name) || null,
    custom_text: text.slice(0, 255), pairs_json: pairs ? JSON.stringify(pairs) : null,
    subscription_id: typeof s.subscription === 'string' ? s.subscription : null, invoice_id: null, status, note,
  };
  return storeAndDispatch(env, row, status === 'NEEDS_HUMAN' && !cls.auto, deps);
}

// Backstop: re-dispatch rows still PAID after 2 hours. Cron is off in wrangler.toml until the first test passes.
export async function redispatchStale(env, deps = {}) {
  const fetchFn = deps.fetch || fetch;
  const now = deps.now || new Date();
  const cutoff = new Date(now.getTime() - 2 * 3600000).toISOString();
  const rs = await env.DB.prepare("SELECT sale_id FROM sales_events WHERE status='PAID' AND created_utc<=?").bind(cutoff).all();
  let done = 0;
  for (const r of rs.results || []) {
    const code = await dispatchBuild(env, r.sale_id, fetchFn).catch(() => 0);
    if (code === 204) {
      await env.DB.prepare("UPDATE sales_events SET status='DISPATCHED', dispatched_utc=? WHERE sale_id=? AND status='PAID'").bind(now.toISOString(), r.sale_id).run();
      done++;
    }
  }
  return done;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/stripe') return json({ error: 'not found' }, 404);
    if (request.method !== 'POST') return json({ error: 'method' }, 405);
    try { return await handleStripe(request, env); } catch (e) { return json({ error: 'server' }, 500); }
  },
  async scheduled(event, env, ctx) { ctx.waitUntil(redispatchStale(env)); },
};
