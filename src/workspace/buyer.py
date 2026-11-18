"""Buyer review, commented overrides and transactional PO/sourcing document creation."""
import csv
import io
import json
import re
from datetime import date
from decimal import Decimal
from uuid import uuid4

from src.engine import service
from src.engine.core import Engine, money
from src.imports.load import PR_FIELDS, valid_number
from src.policy.store import query, Conflict
from .auth import scalar

SOURCING={"RFQ","RFP","CAPEX_SOURCING","NEW_ITEM_SOURCING"}
PO={"SPOT_PO","EMERGENCY_PO","CONTRACT_PO"}


def run_key(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9a-f]{64}",value):raise ValueError("Invalid run ID")
    return value


def group_key(value):
    if not isinstance(value,str) or not re.fullmatch(r"REQ-\d+",value):raise ValueError("Invalid group ID")
    return value


def register_run(result,user,origin):
    rid=run_key(result["run_id"])
    query(f"INSERT INTO corvo.run_owners(run_id,username,origin) VALUES ('{rid}',"
          f"{scalar(user['username'])},{scalar(origin)}) ON CONFLICT DO NOTHING;")


def list_runs():
    raw=query("SELECT COALESCE(json_agg(row_to_json(x)),'[]'::json) FROM "
              "(SELECT r.run_id,r.created_at,COALESCE(o.origin,'batch') origin,"
              "jsonb_array_length(result->'decisions') line_count,"
              "jsonb_array_length(result->'requirements') requirement_count"
              " FROM corvo.runs r LEFT JOIN corvo.run_owners o USING(run_id)"
              " ORDER BY r.created_at DESC LIMIT 50) x;")
    return json.loads(raw)


def saved_run(run_id):
    rid=run_key(run_id)
    raw=query(f"SELECT json_build_object('snapshot',snapshot,'result',result) FROM corvo.runs WHERE run_id='{rid}';")
    if not raw:raise ValueError("Run not found")
    return json.loads(raw,parse_float=str)


def details(run_id):
    rid=run_key(run_id);saved=saved_run(rid)
    inputs=list(csv.DictReader(io.StringIO(saved["snapshot"]["input_csv"])))
    originals=saved["result"]["decisions"]
    overrides=json.loads(query(f"SELECT COALESCE(json_agg(row_to_json(o)),'[]'::json) FROM corvo.buyer_overrides o WHERE run_id='{rid}';"))
    approvals=json.loads(query(f"SELECT COALESCE(json_agg(row_to_json(d)),'[]'::json) FROM corvo.document_lines d WHERE run_id='{rid}';"))
    by_override={r["group_id"]:r for r in overrides};by_approval={r["group_id"]:r for r in approvals}
    groups=[]
    for trace in saved["result"]["requirements"]:
        original=next(r for r in originals if r["group_id"]==trace["group_id"])
        override=by_override.get(trace["group_id"])
        groups.append(dict(group_id=trace["group_id"],original=original,
                           effective=override["decision"] if override else original,
                           revision=override["revision"] if override else 0,
                           override_comment=override["comment"] if override else "",
                           inputs=[inputs[i-1] for i in trace["input_rows"]],trace=trace,
                           approval=by_approval.get(trace["group_id"])))
    events=json.loads(query(f"SELECT COALESCE(json_agg(row_to_json(e) ORDER BY event_id),'[]'::json)"
                            f" FROM corvo.workspace_events e WHERE entity LIKE '{rid}/%';"))
    return dict(run_id=rid,groups=groups,rejected=[r for r in originals if r["route"]=="NOT_PROCESSED"],
                audit=events)


def requirement(run_id,group_id):
    gid=group_key(group_id)
    group=next((g for g in details(run_id)["groups"] if g["group_id"]==gid),None)
    if not group:raise ValueError("Requirement not found")
    return group


def evaluate_queue(user):
    raw=query("SELECT COALESCE(json_agg(input ORDER BY created_at,pr_no),'[]'::json) FROM corvo.requests r "
              "WHERE NOT EXISTS (SELECT FROM corvo.approved_pr_keys a WHERE a.pr_no=r.pr_no);")
    rows=json.loads(raw)
    if not rows:raise ValueError("No confirmed requests await evaluation")
    out=io.StringIO();writer=csv.DictWriter(out,PR_FIELDS.split());writer.writeheader();writer.writerows(rows)
    data=service.snapshot()
    spent=json.loads(query("SELECT COALESCE(json_object_agg(contract_id,total),'{}'::json) FROM "
                           "(SELECT contract_id,sum(contract_value)::text total FROM corvo.document_lines"
                           " WHERE contract_id IS NOT NULL GROUP BY contract_id) c;"))
    for contract in data["contracts"]:
        contract["consumed_value"]=str(Decimal(str(contract["consumed_value"]))+Decimal(spent.get(contract["contract_id"],"0")))
    result=service.evaluate_and_save(out.getvalue().encode(),data)
    register_run(result,user,"chat")
    return result


def override(run_id,group_id,user,revision,route,comment,supplier_id="",unit_price="",contract_id=""):
    rid,gid=run_key(run_id),group_key(group_id)
    if type(revision) is not int:raise ValueError("Revision must be an integer")
    if not isinstance(comment,str) or not 1<=len(comment.strip())<=4000:raise ValueError("A written override reason is required")
    if route not in SOURCING|PO:raise ValueError("Unsupported override route")
    group=requirement(rid,gid)
    if group["approval"]:raise Conflict("Requirement already approved")
    effective={**group["original"],"route":route,"supplier_id":"","unit_price":"","suggested_supplier_ids":"","po_id":""}
    data=service.snapshot();engine=Engine(data,data["policies"])
    item=group["original"]["resolved_item_code"]
    as_of=date.fromisoformat(group["inputs"][0]["created_at"][:10])
    if route in PO:
        if item=="NEW":raise ValueError("Create the catalogue item before awarding a PO for a new item")
        if supplier_id not in engine.suppliers or not engine.active(supplier_id):raise ValueError("Choose an Active supplier")
        if not valid_number(str(unit_price),0):raise ValueError("Enter a valid nonnegative unit price")
        if route=="CONTRACT_PO":
            contract=next((r for r in engine.contracts[item] if r["contract_id"]==contract_id and r["supplier_id"]==supplier_id
                           and r["start_date"]<=as_of.isoformat()<=r["end_date"]),None)
            if not contract or Decimal(str(unit_price))!=Decimal(str(contract["rate"])):raise ValueError("Contract, supplier, date or rate does not match")
        effective.update(decision="PO",supplier_id=supplier_id,unit_price=money(Decimal(str(unit_price))))
    else:
        effective["decision"]="SOURCING_REQUEST"
        if item!="NEW":
            from src.engine.policy import select
            policy=select(data["policies"],as_of)
            ranked=engine.preferred(item,as_of,policy)
            effective["suggested_supplier_ids"]=";".join(engine.suggestions(item,ranked,3 if route=="RFQ" else 5))
    effective["override_contract_id"]=contract_id if route=="CONTRACT_PO" else ""
    payload=service.document(dict(decision=effective,comment=comment.strip(),actor=user["username"]))
    query(f"""BEGIN; SELECT pg_advisory_xact_lock(53422017);
DO $body$ DECLARE p jsonb:={payload}; current_revision int; BEGIN
 IF EXISTS(SELECT FROM corvo.document_lines WHERE run_id='{rid}' AND group_id='{gid}') THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Requirement already approved'; END IF;
 SELECT COALESCE((SELECT o.revision FROM corvo.buyer_overrides o WHERE run_id='{rid}' AND group_id='{gid}'),0) INTO current_revision;
 IF current_revision<>{revision} THEN RAISE EXCEPTION 'POLICY_CONFLICT: Requirement changed. Reload it.'; END IF;
 INSERT INTO corvo.buyer_overrides(run_id,group_id,decision,comment,revision,actor)
 VALUES ('{rid}','{gid}',p->'decision',p->>'comment',current_revision+1,p->>'actor')
 ON CONFLICT(run_id,group_id) DO UPDATE SET decision=EXCLUDED.decision,comment=EXCLUDED.comment,revision=EXCLUDED.revision,actor=EXCLUDED.actor;
 INSERT INTO corvo.workspace_events(actor,action,entity,detail)
 VALUES(p->>'actor','overridden','{rid}/{gid}',p);
END $body$; COMMIT;""")
    return requirement(rid,gid)


def approve(run_id,group_id,user,revision,capex_approved=False):
    rid,gid=run_key(run_id),group_key(group_id)
    if type(revision) is not int or type(capex_approved) is not bool:raise ValueError("Invalid approval fields")
    group=requirement(rid,gid)
    if group["approval"]:return group
    effective=group["effective"]
    item=group["original"]["resolved_item_code"]
    snapshot=saved_run(rid)["snapshot"]["reference_data"]
    item_row=next((r for r in snapshot["items"] if r["item_code"]==item),{})
    if (effective["route"]=="CAPEX_SOURCING" or item_row.get("item_type")=="Capex") and not capex_approved:
        raise ValueError("Record capex approval before creating this sourcing request")
    quantity=Decimal(group["trace"]["quantity"])
    contract_id=effective.get("override_contract_id") if group["revision"] else group["trace"].get("contract_id")
    if effective["route"] != "CONTRACT_PO":contract_id=None
    contract_id=contract_id or None
    if effective["route"]=="CONTRACT_PO" and not contract_id:
        match=re.search(r"award contract ([\w-]+)",effective["reason"])
        contract_id=match[1] if match else None
    if effective["route"]=="CONTRACT_PO" and not contract_id:raise ValueError("Contract evidence missing")
    value=quantity*Decimal(effective["unit_price"]) if contract_id else Decimal(0)
    first=min(r["created_at"] for r in group["inputs"])
    consolidation=(effective["po_id"] if effective["decision"]=="PO" and not group["revision"] else gid)
    kind="PO" if effective["decision"]=="PO" else "SOURCING_REQUEST"
    payload=dict(original=group["original"],effective=effective,inputs=group["inputs"],
                 actor=user["username"],contract_id=contract_id,contract_value=str(value),item=item,quantity=str(quantity),
                 date=first[:10],capex_approved=capex_approved,supplier=effective["supplier_id"],
                 plant=group["inputs"][0]["plant"],kind=kind,first=first,consolidation=consolidation)
    document_id=str(uuid4())
    query(f"""BEGIN; SELECT pg_advisory_xact_lock(53422017);
DO $body$
DECLARE p jsonb:={service.document(payload)}; current_revision int; doc uuid;
        ceiling numeric; opening numeric; rate numeric; used numeric;
BEGIN
 IF EXISTS(SELECT FROM corvo.document_lines WHERE run_id='{rid}' AND group_id='{gid}') THEN RETURN; END IF;
 SELECT COALESCE((SELECT o.revision FROM corvo.buyer_overrides o WHERE run_id='{rid}' AND group_id='{gid}'),0) INTO current_revision;
 IF current_revision<>{revision} THEN RAISE EXCEPTION 'POLICY_CONFLICT: Requirement changed. Reload it.'; END IF;
 IF EXISTS (SELECT FROM jsonb_array_elements(p->'inputs') i JOIN corvo.approved_pr_keys a
 ON a.pr_no=i->>'pr_no' AND a.pr_line=i->>'pr_line') THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: A PR line in this group was approved in another run. Evaluate remaining requests.'; END IF;
 IF p->>'kind'='PO' AND NOT EXISTS (SELECT FROM corvo.suppliers WHERE supplier_id=p->>'supplier' AND status='Active') THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Supplier is no longer Active'; END IF;
 IF p->>'kind'='SOURCING_REQUEST' AND EXISTS (
 SELECT FROM unnest(string_to_array(p->'effective'->>'suggested_supplier_ids',';')) sid
 WHERE sid<>'' AND NOT EXISTS(SELECT FROM corvo.suppliers s WHERE s.supplier_id=sid AND s.status='Active')) THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: A suggested supplier is no longer Active. Re-evaluate.'; END IF;
 IF p->>'contract_id' IS NOT NULL THEN
 SELECT c.value_cap,c.consumed_value,l.rate INTO ceiling,opening,rate FROM corvo.contracts c
 JOIN corvo.contract_lines l USING(contract_id)
 WHERE c.contract_id=p->>'contract_id' AND l.item_code=p->>'item'
 AND c.supplier_id=p->>'supplier' AND (p->>'date')::date BETWEEN c.start_date AND c.end_date;
 SELECT COALESCE(sum(contract_value),0) INTO used FROM corvo.document_lines WHERE contract_id=p->>'contract_id';
 IF ceiling IS NULL OR rate<>(p->'effective'->>'unit_price')::numeric
 OR opening+used+(p->>'contract_value')::numeric>ceiling THEN
 RAISE EXCEPTION 'POLICY_CONFLICT: Contract rate, validity or remaining ceiling changed. Re-evaluate.'; END IF;
 END IF;
 SELECT document_id INTO doc FROM corvo.purchase_documents
 WHERE run_id='{rid}' AND consolidation_key=p->>'consolidation';
 IF doc IS NULL THEN
 doc:='{document_id}';
 INSERT INTO corvo.purchase_documents(document_id,run_id,consolidation_key,kind,supplier_id,plant,route,first_created_at)
 VALUES(doc,'{rid}',p->>'consolidation',p->>'kind',NULLIF(p->>'supplier',''),p->>'plant',
 p->'effective'->>'route',(p->>'first')::timestamp);
 END IF;
 UPDATE corvo.purchase_documents SET first_created_at=LEAST(first_created_at,(p->>'first')::timestamp) WHERE document_id=doc;
 INSERT INTO corvo.document_lines(run_id,group_id,document_id,original,effective,inputs,actor,contract_id,contract_value,capex_approved)
 VALUES('{rid}','{gid}',doc,p->'original',p->'effective',p->'inputs',p->>'actor',p->>'contract_id',
 (p->>'contract_value')::numeric,(p->>'capex_approved')::boolean);
 INSERT INTO corvo.approved_pr_keys(pr_no,pr_line,run_id,group_id)
 SELECT i->>'pr_no',i->>'pr_line','{rid}','{gid}' FROM jsonb_array_elements(p->'inputs') i;
 INSERT INTO corvo.workspace_events(actor,action,entity,detail)
 VALUES(p->>'actor','approved','{rid}/{gid}',p || jsonb_build_object('document_id',doc));
END $body$; COMMIT;""")
    return requirement(rid,gid)


def my_requests(user):
    return json.loads(query("SELECT COALESCE(json_agg(row_to_json(x) ORDER BY created_at DESC),'[]'::json) FROM "
        "(SELECT r.pr_no,r.input,r.created_at,d.document_id,d.effective FROM corvo.requests r "
        "LEFT JOIN corvo.approved_pr_keys a ON a.pr_no=r.pr_no AND a.pr_line='1' "
        "LEFT JOIN corvo.document_lines d ON d.run_id=a.run_id AND d.group_id=a.group_id "
        f"WHERE r.username={scalar(user['username'])}) x;"))


def documents():
    return json.loads(query("SELECT COALESCE(json_agg(row_to_json(x) ORDER BY created_at DESC),'[]'::json) FROM "
        "(SELECT d.*,(SELECT json_agg(row_to_json(l)) FROM corvo.document_lines l WHERE l.document_id=d.document_id) lines"
        " FROM corvo.purchase_documents d) x;"))
