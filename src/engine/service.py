"""PostgreSQL adapter and immutable run persistence for the procurement engine."""
import base64
import hashlib
import json
from pathlib import Path

from src.imports.load import psql
from .core import Engine

POLICY_PATH = Path(__file__).with_name("policy_v5.json")


def encoded(value):
    return base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()


def document(value):
    return f"convert_from(decode('{encoded(value)}','base64'),'UTF8')::jsonb"


def initialize():
    policy = json.loads(POLICY_PATH.read_text())
    psql("BEGIN; SELECT pg_advisory_xact_lock(53422017);"
         "CREATE TABLE IF NOT EXISTS corvo.policies (version text PRIMARY KEY, effective_date date UNIQUE NOT NULL,"
         " document jsonb NOT NULL);"
         "ALTER TABLE corvo.policies DROP CONSTRAINT IF EXISTS policies_effective_date_key;"
         "ALTER TABLE corvo.policies ADD COLUMN IF NOT EXISTS approval_order bigserial;"
         "CREATE TABLE IF NOT EXISTS corvo.runs (run_id text PRIMARY KEY, created_at timestamptz DEFAULT now(),"
         " batch_sha256 text NOT NULL, snapshot jsonb NOT NULL, result jsonb NOT NULL);"
         f"INSERT INTO corvo.policies(version,effective_date,document) VALUES ('5.0','2026-04-01',{document(policy)}) ON CONFLICT DO NOTHING; COMMIT;")


def snapshot():
    tables = ["plants", "items", "suppliers", "po_history", "contracts", "contract_lines"]
    pairs = [f"'{table}',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)"
             f" FROM corvo.{table} t),'[]'::jsonb)" for table in tables]
    query = "SELECT jsonb_build_object(" + ",".join(pairs) + ","
    query += "'policies',(SELECT jsonb_agg(document ORDER BY effective_date,approval_order) FROM corvo.policies));"
    # Retain database NUMERIC as decimal text, never binary float.
    return json.loads(psql(query), parse_float=str)


def process(content, policy_version=None):
    data = snapshot()
    if policy_version is not None:
        chosen = next((p for p in data["policies"] if p["version"] == policy_version), None)
        if chosen is None:
            raise ValueError("Unknown approved policy version")
        data["policies"] = data["policies"][:data["policies"].index(chosen) + 1]
    return evaluate_and_save(content, data)


def evaluate_and_save(content, data):
    result = Engine(data, data["policies"]).process(content)
    batch_hash = hashlib.sha256(content).hexdigest()
    run_id = hashlib.sha256((batch_hash + json.dumps(data, sort_keys=True)).encode()).hexdigest()
    psql(f"INSERT INTO corvo.runs(run_id,batch_sha256,snapshot,result)"
         f" VALUES ('{run_id}','{batch_hash}',"
         f"{document(dict(reference_data=data, input_csv=content.decode('utf-8-sig')))},{document(result)})"
         " ON CONFLICT DO NOTHING;")
    return dict(run_id=run_id, **result)


def replay(run_id):
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Invalid run ID")
    raw = psql(f"SELECT snapshot FROM corvo.runs WHERE run_id='{run_id}';")
    if not raw:
        raise ValueError("Run does not exist")
    saved = json.loads(raw, parse_float=str)
    data = saved["reference_data"]
    result = Engine(data, data["policies"]).process(saved["input_csv"].encode())
    return dict(run_id=run_id, **result)
