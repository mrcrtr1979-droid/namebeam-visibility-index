-- D1 schema for namebeam-sales (TEST MODE build). Apply once: wrangler d1 execute namebeam_sales --file=schema.sql
CREATE TABLE IF NOT EXISTS sales_events (
  sale_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  created_utc TEXT NOT NULL,
  livemode INTEGER NOT NULL,
  offer TEXT NOT NULL,
  amount_cents INTEGER,
  currency TEXT,
  buyer_email TEXT,
  buyer_name TEXT,
  custom_text TEXT,
  pairs_json TEXT,
  subscription_id TEXT,
  invoice_id TEXT,
  status TEXT NOT NULL,
  dispatched_utc TEXT,
  sha256 TEXT,
  brevo_message_id TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_status ON sales_events (status, created_utc);
CREATE INDEX IF NOT EXISTS idx_sales_sub ON sales_events (subscription_id);
-- sale_id is the checkout session id (first purchase) or the invoice id (renewal). invoice_id is null on the first purchase.
-- status values: PAID, DISPATCHED, BUILDING, SENT, FAILED, NEEDS_INTAKE_CORRECTION, NEEDS_HUMAN
