BEGIN;
SELECT pg_advisory_xact_lock(53422017);
CREATE TABLE IF NOT EXISTS corvo.policy_drafts (
    draft_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL,
    base_version text NOT NULL REFERENCES corvo.policies(version),
    base_document jsonb NOT NULL,
    baseline_hash text NOT NULL,
    candidate jsonb NOT NULL,
    unresolved jsonb NOT NULL,
    source_text text NOT NULL,
    source_name text NOT NULL,
    source_sha256 text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    reviewed boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
    approved_by text,
    approved_at timestamptz
);
CREATE TABLE IF NOT EXISTS corvo.policy_events (
    event_id bigserial PRIMARY KEY,
    draft_id uuid NOT NULL REFERENCES corvo.policy_drafts,
    actor text NOT NULL,
    action text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    detail jsonb NOT NULL
);
COMMIT;
