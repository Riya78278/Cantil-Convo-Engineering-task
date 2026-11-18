"""Durable policy drafts, explicit approval and append-only audit events."""
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from src.engine.policy import validate
from src.engine.service import document
from src.imports.load import psql
from .reader import propose, preview, read_source


class Conflict(ValueError):
    pass


def initialize():
    psql((Path(__file__).resolve().parents[2] / "db/policy_workflow.sql").read_text())


def query(sql):
    try:
        return psql(sql)
    except ValueError as exc:
        if "POLICY_CONFLICT:" in str(exc):
            message = str(exc).split("POLICY_CONFLICT:", 1)[1].split("\n")[0].strip()
            raise Conflict(message) from None
        raise


def identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ValueError("Enter an actor name of 1–120 characters")
    return value.strip()


def key(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid draft ID") from None


def fingerprint(policies):
    return hashlib.sha256(json.dumps(policies, sort_keys=True).encode()).hexdigest()


def policies():
    return json.loads(psql("SELECT COALESCE(jsonb_agg(document ORDER BY effective_date,approval_order),'[]'::jsonb) FROM corvo.policies;"))


def list_all():
    return json.loads(psql("SELECT json_build_object('policies',"
                          "(SELECT COALESCE(json_agg(document ORDER BY effective_date,approval_order),'[]'::json) FROM corvo.policies),"
                          "'drafts',(SELECT COALESCE(json_agg(row_to_json(d) ORDER BY created_at DESC),'[]'::json)"
                          " FROM corvo.policy_drafts d));"))


def get(draft_id):
    identifier = key(draft_id)
    raw = psql(f"SELECT row_to_json(d) FROM corvo.policy_drafts d WHERE draft_id='{identifier}';")
    if not raw:
        raise ValueError("Draft does not exist")
    draft = json.loads(raw)
    draft["changes"] = preview(draft["base_document"], draft["candidate"])
    draft["audit"] = json.loads(psql(
        f"SELECT COALESCE(json_agg(row_to_json(e) ORDER BY event_id),'[]'::json)"
        f" FROM corvo.policy_events e WHERE draft_id='{identifier}';"))
    return draft


def create(actor, base_version, version, effective_date, text=None, data=None, filename=None):
    actor = identity(actor)
    available = policies()
    base = next((p for p in available if p["version"] == base_version), None)
    if base is None:
        raise ValueError("Select an existing base policy")
    if data is not None:
        source = read_source(data, filename)
        source_hash = hashlib.sha256(data).hexdigest()
    else:
        source = text
        source_hash = hashlib.sha256((text or "").encode()).hexdigest()
    if not isinstance(source, str) or not source.strip() or len(source) > 200000:
        raise ValueError("Supply policy text of 1–200000 characters")
    candidate, unresolved = propose(source, base, version, effective_date)
    payload = dict(draft_id=str(uuid4()), base_version=base_version, base_document=base,
                   baseline_hash=fingerprint(available), candidate=candidate, unresolved=unresolved,
                   source_text=source, source_name=filename or "Chat amendment", source_sha256=source_hash,
                   created_by=actor)
    sql = ("BEGIN; INSERT INTO corvo.policy_drafts(draft_id,base_version,base_document,baseline_hash,candidate,"
           "unresolved,source_text,source_name,source_sha256,created_by)"
           f" SELECT (p->>'draft_id')::uuid,p->>'base_version',p->'base_document',p->>'baseline_hash',p->'candidate',"
           f"p->'unresolved',p->>'source_text',p->>'source_name',p->>'source_sha256',p->>'created_by'"
           f" FROM (SELECT {document(payload)} p) s;"
           f"INSERT INTO corvo.policy_events(draft_id,actor,action,detail) VALUES ('{payload['draft_id']}',"
           f"{document(actor)}#>>'{{}}','created',{document(dict(candidate=candidate, unresolved=unresolved))}); COMMIT;")
    psql(sql)
    return get(payload["draft_id"])


def transaction_payload(payload):
    return ("BEGIN; SELECT pg_advisory_xact_lock(53422017);"
            f"CREATE TEMP TABLE action_payload ON COMMIT DROP AS SELECT {document(payload)} p;")


def revise(draft_id, revision, actor, candidate, reviewed):
    identifier, actor = key(draft_id), identity(actor)
    if type(revision) is not int or type(reviewed) is not bool:
        raise ValueError("Revision must be an integer and reviewed must be a boolean")
    validate(candidate)
    payload = dict(candidate=candidate, revision=revision, actor=actor, reviewed=reviewed)
    sql = transaction_payload(payload) + f"""
DO $body$
DECLARE d corvo.policy_drafts%ROWTYPE; p jsonb;
BEGIN
 SELECT * INTO d FROM corvo.policy_drafts WHERE draft_id='{identifier}' FOR UPDATE;
 SELECT action_payload.p INTO p FROM action_payload;
 IF d.draft_id IS NULL OR d.status <> 'draft' OR d.revision <> (p->>'revision')::int THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Draft changed or was already approved. Reload it.'; END IF;
 UPDATE corvo.policy_drafts SET candidate=p->'candidate', revision=revision+1,
 reviewed=(p->>'reviewed')::boolean WHERE draft_id=d.draft_id;
 INSERT INTO corvo.policy_events(draft_id,actor,action,detail)
 VALUES (d.draft_id,p->>'actor','edited',p);
END $body$;
COMMIT;
"""
    query(sql)
    return get(identifier)


def approve(draft_id, revision, actor):
    identifier, actor = key(draft_id), identity(actor)
    if type(revision) is not int:
        raise ValueError("Revision must be an integer")
    draft = get(identifier)
    validate(draft["candidate"])
    available = policies()
    current_hash = fingerprint(available)
    payload = dict(actor=actor, revision=revision, current_hash=current_hash)
    # Check both revision and policy list under the lock. Recompute list in the transaction to
    # reject a competing approval between the Python read and the locked write.
    expected_policies = document(available)
    sql = transaction_payload(payload) + f"""
DO $body$
DECLARE d corvo.policy_drafts%ROWTYPE; p jsonb; live jsonb;
BEGIN
 SELECT * INTO d FROM corvo.policy_drafts WHERE draft_id='{identifier}' FOR UPDATE;
 SELECT action_payload.p INTO p FROM action_payload;
 IF d.draft_id IS NULL OR d.revision <> (p->>'revision')::int THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Draft revision changed. Reload it.'; END IF;
 IF d.status='approved' THEN RETURN; END IF;
 SELECT COALESCE(jsonb_agg(document ORDER BY effective_date,approval_order),'[]'::jsonb) INTO live FROM corvo.policies;
 IF live <> {expected_policies} OR d.baseline_hash <> p->>'current_hash' THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Approved policies changed. Create a draft from the current policy list.'; END IF;
 IF NOT d.reviewed THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Review and save the draft before approval.'; END IF;
 IF EXISTS (SELECT FROM corvo.policies WHERE version=d.candidate->>'version') THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Policy version must be unique.'; END IF;
 INSERT INTO corvo.policies(version,effective_date,document)
 VALUES(d.candidate->>'version',(d.candidate->>'effective_date')::date,d.candidate);
 UPDATE corvo.policy_drafts SET status='approved',approved_by=p->>'actor',approved_at=now()
 WHERE draft_id=d.draft_id;
 INSERT INTO corvo.policy_events(draft_id,actor,action,detail)
 VALUES(d.draft_id,p->>'actor','approved',jsonb_build_object('revision',d.revision,'candidate',d.candidate));
END $body$;
COMMIT;
"""
    query(sql)
    return get(identifier)
