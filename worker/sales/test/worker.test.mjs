// No network. Real SQL runs against node:sqlite using schema.sql. Stripe events are signed with a test secret.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHmac } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { handleStripe, verifyStripeSignature, parsePairs, redispatchStale } from '../src/index.js';

const SECRET = 'whsec_test_fixture_only';
const NOW = new Date('2026-10-12T15:00:00Z');
const schema = readFileSync(new URL('../schema.sql', import.meta.url), 'utf8');

function makeDb() {
  const db = new DatabaseSync(':memory:');
  db.exec(schema);
  const wrap = {
    prepare(sql) {
      const st = db.prepare(sql);
      return { bind(...a) { return {
        run: async () => ({ meta: { changes: Number(st.run(...a).changes) } }),
        all: async () => ({ results: st.all(...a) }),
      }; } };
    },
    raw: db,
  };
  return wrap;
}
function session(over = {}) {
  return {
    id: 'cs_test_1', mode: 'subscription', payment_status: 'paid', currency: 'usd', amount_total: 4900,
    customer_details: { email: 'buyer@example.com', name: 'Test Buyer' },
    custom_fields: [{ key: 'marketcityandnichepairs', label: { custom: 'Market city and niche pairs', type: 'custom' }, type: 'text', text: { value: 'Phoenix AZ | hvac repair and replacement' } }],
    ...over,
  };
}
function event(sess, over = {}) {
  return { id: 'evt_test_1', object: 'event', livemode: false, type: 'checkout.session.completed', data: { object: sess }, ...over };
}
function signed(evt, { secret = SECRET, t = Math.floor(NOW.getTime() / 1000), body } = {}) {
  const raw = body ?? JSON.stringify(evt);
  const v1 = createHmac('sha256', secret).update(`${t}.${raw}`).digest('hex');
  return new Request('https://x.test/stripe', { method: 'POST', headers: { 'stripe-signature': `t=${t},v1=${v1}` }, body: raw });
}
function env(db, extra = {}) {
  return { DB: db, STRIPE_WEBHOOK_SECRET: SECRET, GH_REPO: 'o/r', GH_DISPATCH_TOKEN: 'ghp_fixture', DRY_RUN: 'true', ...extra };
}
function fakeFetch(status = 204) {
  const calls = [];
  const f = async (url, init) => { calls.push({ url, init }); return { status }; };
  f.calls = calls;
  return f;
}
const count = (db) => db.raw.prepare('select count(*) c from sales_events').get().c;
const row = (db) => db.raw.prepare('select * from sales_events').get();

test('valid signature stores one row and dispatches once', async () => {
  const db = makeDb(); const f = fakeFetch();
  const res = await handleStripe(signed(event(session())), env(db), { fetch: f, now: NOW });
  assert.equal(res.status, 200);
  assert.equal(count(db), 1);
  assert.equal(row(db).status, 'DISPATCHED');
  assert.equal(row(db).offer, 'market_1');
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, 'https://api.github.com/repos/o/r/dispatches');
  const body = JSON.parse(f.calls[0].init.body);
  assert.equal(body.event_type, 'namebeam-sale');
  assert.deepEqual(body.client_payload.pairs, [{ market: 'Phoenix AZ', niche: 'hvac repair and replacement' }]);
  assert.equal(body.client_payload.email, 'buyer@example.com');
  assert.equal(body.client_payload.end_date, '2026-10-11');
});

test('tampered body returns 400 and writes nothing', async () => {
  const db = makeDb(); const f = fakeFetch();
  const good = JSON.stringify(event(session()));
  // sign the original body, then send altered text
  const t = Math.floor(NOW.getTime() / 1000);
  const v1 = createHmac('sha256', SECRET).update(`${t}.${good}`).digest('hex');
  const bad = new Request('https://x.test/stripe', { method: 'POST', headers: { 'stripe-signature': `t=${t},v1=${v1}` }, body: good.replace('4900', '1') });
  const res = await handleStripe(bad, env(db), { fetch: f, now: NOW });
  assert.equal(res.status, 400);
  assert.equal(count(db), 0);
  assert.equal(f.calls.length, 0);
});

test('wrong secret returns 400', async () => {
  const db = makeDb();
  const res = await handleStripe(signed(event(session()), { secret: 'whsec_other' }), env(db), { fetch: fakeFetch(), now: NOW });
  assert.equal(res.status, 400);
});

test('timestamp 10 minutes old returns 400', async () => {
  const db = makeDb();
  const t = Math.floor(NOW.getTime() / 1000) - 600;
  const res = await handleStripe(signed(event(session()), { t }), env(db), { fetch: fakeFetch(), now: NOW });
  assert.equal(res.status, 400);
  assert.equal(count(db), 0);
});

test('missing header returns 400', async () => {
  const r = await verifyStripeSignature('{}', null, SECRET, 1);
  assert.equal(r.ok, false);
});

test('same event id twice gives one row and one dispatch', async () => {
  const db = makeDb(); const f = fakeFetch();
  const a = await handleStripe(signed(event(session())), env(db), { fetch: f, now: NOW });
  const b = await handleStripe(signed(event(session())), env(db), { fetch: f, now: NOW });
  assert.equal(a.status, 200); assert.equal(b.status, 200);
  assert.equal((await b.json()).duplicate, true);
  assert.equal(count(db), 1);
  assert.equal(f.calls.length, 1);
});

test('same event id with a different session id is still ignored', async () => {
  const db = makeDb(); const f = fakeFetch();
  await handleStripe(signed(event(session())), env(db), { fetch: f, now: NOW });
  await handleStripe(signed(event(session({ id: 'cs_test_2' }))), env(db), { fetch: f, now: NOW });
  assert.equal(count(db), 1);
});

test('199 offer with five pairs maps to market_5', async () => {
  const db = makeDb(); const f = fakeFetch();
  const five = 'Phoenix AZ|hvac repair and replacement; Houston TX|personal injury attorneys\nDenver CO|residential roofing;St. Louis MO|interior painting;Pleasanton CA|personal injury law';
  const s = session({ amount_total: 19900, custom_fields: [{ key: 'pairs', type: 'text', text: { value: five } }] });
  await handleStripe(signed(event(s)), env(db), { fetch: f, now: NOW });
  assert.equal(row(db).offer, 'market_5');
  assert.equal(JSON.parse(f.calls[0].init.body).client_payload.pairs.length, 5);
});

test('49 offer with two pairs goes to intake correction, no dispatch', async () => {
  const db = makeDb(); const f = fakeFetch();
  const s = session({ custom_fields: [{ key: 'pairs', type: 'text', text: { value: 'Phoenix AZ|hvac; Houston TX|roofing' } }] });
  await handleStripe(signed(event(s)), env(db), { fetch: f, now: NOW });
  assert.equal(row(db).status, 'NEEDS_INTAKE_CORRECTION');
  assert.equal(f.calls.length, 0);
});

test('agency 397 is recorded NEEDS_HUMAN with no dispatch and no alert while DRY_RUN is true', async () => {
  const db = makeDb(); const f = fakeFetch();
  const s = session({ mode: 'payment', amount_total: 39700 });
  await handleStripe(signed(event(s)), env(db), { fetch: f, now: NOW });
  assert.equal(row(db).offer, 'agency_397');
  assert.equal(row(db).status, 'NEEDS_HUMAN');
  assert.equal(f.calls.length, 0);
});

test('agency 397 alert goes to Brevo only when DRY_RUN is false', async () => {
  const db = makeDb(); const f = fakeFetch(201);
  const s = session({ mode: 'payment', amount_total: 39700 });
  await handleStripe(signed(event(s)), env(db, { DRY_RUN: 'false', BREVO_API_KEY: 'k', ALERT_TO: 'a@example.com' }), { fetch: f, now: NOW });
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, 'https://api.brevo.com/v3/smtp/email');
  assert.ok(!f.calls[0].init.body.includes('buyer@example.com'));
});

test('segment 99 and unknown amounts are NEEDS_HUMAN', async () => {
  for (const [amt, offer] of [[9900, 'segment_99'], [12345, 'unknown']]) {
    const db = makeDb(); const f = fakeFetch();
    await handleStripe(signed(event(session({ mode: 'payment', amount_total: amt }))), env(db), { fetch: f, now: NOW });
    assert.equal(row(db).offer, offer); assert.equal(row(db).status, 'NEEDS_HUMAN'); assert.equal(f.calls.length, 0);
  }
});

test('eur currency is NEEDS_HUMAN', async () => {
  const db = makeDb(); const f = fakeFetch();
  await handleStripe(signed(event(session({ currency: 'eur' }))), env(db), { fetch: f, now: NOW });
  assert.equal(row(db).status, 'NEEDS_HUMAN'); assert.equal(f.calls.length, 0);
});

test('unpaid session and other event types write nothing', async () => {
  const db = makeDb(); const f = fakeFetch();
  await handleStripe(signed(event(session({ payment_status: 'unpaid' }))), env(db), { fetch: f, now: NOW });
  await handleStripe(signed(event(session(), { id: 'evt_2', type: 'charge.refunded' })), env(db), { fetch: f, now: NOW });
  assert.equal(count(db), 0);
});

test('live event is ignored while test mode only', async () => {
  const db = makeDb(); const f = fakeFetch();
  const res = await handleStripe(signed(event(session(), { livemode: true })), env(db), { fetch: f, now: NOW });
  assert.equal(res.status, 200); assert.equal(count(db), 0); assert.equal(f.calls.length, 0);
});

test('dispatch failure leaves row PAID and the stale backstop retries it', async () => {
  const db = makeDb();
  await handleStripe(signed(event(session())), env(db), { fetch: fakeFetch(401), now: NOW });
  assert.equal(row(db).status, 'PAID');
  const later = new Date(NOW.getTime() + 3 * 3600000);
  const f = fakeFetch(204);
  const n = await redispatchStale(env(db), { fetch: f, now: later });
  assert.equal(n, 1); assert.equal(row(db).status, 'DISPATCHED'); assert.equal(f.calls.length, 1);
});

test('parsePairs rules', () => {
  assert.equal(parsePairs('', 1).error, 'no market given');
  assert.ok(parsePairs('Phoenix|hvac', 1).error);
  assert.ok(parsePairs('a|b|c', 1).error);
  assert.equal(parsePairs('Phoenix AZ|hvac', 1).pairs.length, 1);
});
