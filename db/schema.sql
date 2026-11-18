CREATE SCHEMA IF NOT EXISTS corvo;
SET LOCAL search_path TO corvo, pg_catalog;

CREATE TABLE IF NOT EXISTS baseline (
    singleton boolean PRIMARY KEY CHECK (singleton),
    fingerprint text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS plants (plant text PRIMARY KEY, name text NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    item_code text PRIMARY KEY, description text NOT NULL, uom text NOT NULL,
    category text NOT NULL, item_type text NOT NULL,
    standard_price numeric NOT NULL CHECK (standard_price >= 0)
);
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id text PRIMARY KEY, name text NOT NULL, city text NOT NULL,
    categories text NOT NULL, status text NOT NULL CHECK (status IN ('Active','Inactive','Blocked')),
    on_time_pct numeric NOT NULL CHECK (on_time_pct BETWEEN 0 AND 100),
    quality_rating numeric NOT NULL CHECK (quality_rating BETWEEN 0 AND 5)
);
CREATE TABLE IF NOT EXISTS po_history (
    po_number text NOT NULL, po_date date NOT NULL, supplier_id text NOT NULL REFERENCES suppliers,
    item_code text NOT NULL REFERENCES items, quantity numeric NOT NULL CHECK (quantity > 0),
    uom text NOT NULL, unit_price numeric NOT NULL CHECK (unit_price >= 0),
    plant text NOT NULL REFERENCES plants, delivered_on_time boolean NOT NULL
);
CREATE INDEX IF NOT EXISTS history_item_date ON po_history(item_code, po_date DESC, supplier_id);
CREATE TABLE IF NOT EXISTS contracts (
    contract_id text PRIMARY KEY, supplier_id text NOT NULL REFERENCES suppliers,
    start_date date NOT NULL, end_date date NOT NULL CHECK (end_date >= start_date),
    value_cap numeric NOT NULL CHECK (value_cap >= 0),
    consumed_value numeric NOT NULL CHECK (consumed_value BETWEEN 0 AND value_cap),
    source text NOT NULL CHECK (source IN ('register','pdf')),
    pdf_name text NOT NULL, pdf_sha256 text NOT NULL
);
CREATE TABLE IF NOT EXISTS contract_lines (
    contract_id text REFERENCES contracts, item_code text REFERENCES items,
    description text NOT NULL, uom text NOT NULL, rate numeric NOT NULL CHECK (rate >= 0),
    PRIMARY KEY (contract_id, item_code)
);
CREATE INDEX IF NOT EXISTS rates_item ON contract_lines(item_code);
CREATE TABLE IF NOT EXISTS batches (
    batch_id text PRIMARY KEY, imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pr_lines (
    batch_id text REFERENCES batches, row_no integer CHECK (row_no > 0),
    input jsonb NOT NULL, issue text, PRIMARY KEY (batch_id, row_no)
);
