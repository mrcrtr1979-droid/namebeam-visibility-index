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
  status TEXT NOT NULL,
  dispatched_utc TEXT,
  sha256 TEXT,
  brevo_message_id TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_status ON sales_events (status, created_utc);
-- status values: PAID, DISPATCHED, NEEDS_INTAKE_CORRECTION, NEEDS_HUMAN
