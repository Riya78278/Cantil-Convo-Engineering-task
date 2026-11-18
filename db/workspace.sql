BEGIN;
SELECT pg_advisory_xact_lock(53422017);
CREATE TABLE IF NOT EXISTS corvo.app_users (
    username text PRIMARY KEY, display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('employee','buyer','procurement')),
    password_hash text NOT NULL, salt text NOT NULL,
    failures integer NOT NULL DEFAULT 0, locked_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corvo.sessions (
    token_hash text PRIMARY KEY, username text NOT NULL REFERENCES corvo.app_users,
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS corvo.conversations (
    conversation_id uuid PRIMARY KEY, username text NOT NULL REFERENCES corvo.app_users,
    revision integer NOT NULL DEFAULT 1, state jsonb NOT NULL, messages jsonb NOT NULL,
    pr_no text UNIQUE, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corvo.requests (
    pr_no text PRIMARY KEY, conversation_id uuid UNIQUE NOT NULL REFERENCES corvo.conversations,
    username text NOT NULL REFERENCES corvo.app_users, input jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corvo.run_owners (
    run_id text PRIMARY KEY REFERENCES corvo.runs, username text NOT NULL REFERENCES corvo.app_users,
    origin text NOT NULL CHECK (origin IN ('batch','chat')),
    registered_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corvo.buyer_overrides (
    run_id text REFERENCES corvo.runs, group_id text,
    decision jsonb NOT NULL, comment text NOT NULL CHECK (length(trim(comment)) > 0),
    revision integer NOT NULL, actor text NOT NULL REFERENCES corvo.app_users,
    PRIMARY KEY (run_id, group_id)
);
CREATE TABLE IF NOT EXISTS corvo.purchase_documents (
    document_id uuid PRIMARY KEY, run_id text NOT NULL REFERENCES corvo.runs,
    consolidation_key text NOT NULL, kind text NOT NULL, supplier_id text,
    plant text NOT NULL, route text NOT NULL, first_created_at timestamp NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corvo.document_lines (
    run_id text REFERENCES corvo.runs, group_id text, document_id uuid NOT NULL REFERENCES corvo.purchase_documents,
    original jsonb NOT NULL, effective jsonb NOT NULL, inputs jsonb NOT NULL,
    actor text NOT NULL REFERENCES corvo.app_users, contract_id text REFERENCES corvo.contracts,
    contract_value numeric NOT NULL DEFAULT 0 CHECK (contract_value >= 0),
    capex_approved boolean NOT NULL DEFAULT false, approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id,group_id)
);
CREATE TABLE IF NOT EXISTS corvo.approved_pr_keys (
    pr_no text, pr_line text, run_id text NOT NULL, group_id text NOT NULL,
    PRIMARY KEY (pr_no,pr_line), FOREIGN KEY (run_id,group_id) REFERENCES corvo.document_lines
);
CREATE TABLE IF NOT EXISTS corvo.workspace_events (
    event_id bigserial PRIMARY KEY, actor text NOT NULL REFERENCES corvo.app_users,
    action text NOT NULL, entity text NOT NULL, detail jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
