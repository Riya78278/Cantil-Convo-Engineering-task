"""Import a Corvo kit or standalone PR batch into PostgreSQL using libpq environment settings."""
import argparse
import base64
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile, BadZipFile

SCHEMA = Path(__file__).resolve().parents[2] / "db/schema.sql"
SOURCES = {
    "plants": ("master_data/plants.csv", "plant name"),
    "items": ("master_data/item_master.csv", "item_code description uom category item_type standard_price"),
    "suppliers": ("master_data/supplier_master.csv", "supplier_id name city categories status on_time_pct quality_rating"),
    "po_history": ("master_data/po_history.csv", "po_number po_date supplier_id item_code quantity uom unit_price plant delivered_on_time"),
    "contracts": ("contracts/contract_register.csv", "contract_id supplier_id supplier_name start_date end_date value_cap consumed_value"),
    "contract_lines": ("contracts/contract_lines.csv", "contract_id item_code description uom rate"),
}
PR_FIELDS = "pr_no pr_line created_at requester department plant item_code description quantity uom estimated_unit_price need_by_date urgency"
SAMPLE = "pr_batches/sample_pr_batch.csv"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_csv(content, fields, label):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    if sorted(reader.fieldnames or []) != sorted(fields.split()):
        raise ValueError(f"{label}: missing, repeated or unexpected column")
    rows = []
    for row in reader:
        if None in row or None in row.values():
            raise ValueError(f"{label}: wrong field count at line {reader.line_num}")
        rows.append({k: v.strip() for k, v in row.items()})
    return rows


def signed_contract(content):
    from pypdf import PdfReader
    doc = " ".join(" ".join(p.extract_text().split()) for p in PdfReader(io.BytesIO(content)).pages)
    def capture(pattern):
        match = re.search(pattern, doc)
        if not match:
            raise ValueError(f"Signed contract has an unreadable field ({pattern})")
        return match.group(1)
    cid = capture(r"Contract No\. ([\w-]+)")
    dates = capture(r"valid from (\d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d)").split(" to ")
    contract = dict(contract_id=cid, supplier_id=capture(r"Supplier ID (\w+)"),
                    start_date=dates[0], end_date=dates[1],
                    value_cap=capture(r"exceed INR ([\d,.]+)").replace(",", ""),
                    consumed_value="0", source="pdf")
    schedule = capture(r"Rate \(INR\) (.+?) For Corvo Engineering")
    pattern = r"(\d+) ([\w-]+) (.+?) ([A-Z]+) ([\d,]+\.\d{2})(?: |$)"
    lines = []
    pos = 0
    while pos < len(schedule):
        match = re.compile(pattern).match(schedule, pos)
        if not match or int(match[1]) != len(lines) + 1:
            raise ValueError(f"{cid}: invalid schedule at row {len(lines) + 1}")
        lines.append(dict(contract_id=cid, item_code=match[2], description=match[3],
                          uom=match[4], rate=match[5].replace(",", "")))
        pos = match.end()
    return contract, lines


def reconcile(tables, pdfs):
    register = {r["contract_id"]: r for r in tables["contracts"]}
    if len(register) != len(tables["contracts"]):
        raise ValueError("Duplicate contract in register")
    seen = set()
    for name, content in sorted(pdfs.items()):
        signed, rates = signed_contract(content)
        cid = signed["contract_id"]
        if Path(name).stem != cid or cid in seen:
            raise ValueError(f"Signed contract identity mismatch: {name}")
        seen.add(cid)
        if cid in register:
            row = register[cid]
            for field in ["supplier_id", "start_date", "end_date"]:
                if row[field] != signed[field]:
                    raise ValueError(f"{cid}: signed {field} disagrees with register")
            def terms(lines):
                return sorted((r["item_code"], r["uom"], Decimal(r["rate"])) for r in lines)
            recorded = [r for r in tables["contract_lines"] if r["contract_id"] == cid]
            if Decimal(row["value_cap"]) != Decimal(signed["value_cap"]) or terms(recorded) != terms(rates):
                raise ValueError(f"{cid}: ceiling or rates disagree with signed terms")
            row["source"] = "register"
            del row["supplier_name"]
        else:
            row = signed
            tables["contracts"].append(row)
            tables["contract_lines"].extend(rates)
        row.update(pdf_name=name, pdf_sha256=digest(content))
    if set(register) - seen:
        raise ValueError("Missing signed copy for registered contract")


def load_kit(path):
    with ZipFile(path) as kit:
        if len(kit.namelist()) != len(set(kit.namelist())):
            raise ValueError("Duplicate archive member")
        roots = [n[:-len(SAMPLE)] for n in kit.namelist() if n.endswith(SAMPLE)]
        if len(roots) != 1:
            raise ValueError("Expected one sample batch")
        root = roots[0]
        files = {name: kit.read(root + name) for name, _ in SOURCES.values()}
        pdfs = {n[len(root):]: kit.read(n) for n in kit.namelist()
                if n.startswith(root + "contracts/pdf/") and n.lower().endswith(".pdf")}
        tables = {table: read_csv(files[name], fields, name) for table, (name, fields) in SOURCES.items()}
        for table, rows in tables.items():
            if not rows or any(not value for row in rows for value in row.values()):
                raise ValueError(f"{table}: empty table or missing required value")
        numeric_fields = {"items": ["standard_price"], "suppliers": ["on_time_pct", "quality_rating"],
                          "po_history": ["quantity", "unit_price"], "contracts": ["value_cap", "consumed_value"],
                          "contract_lines": ["rate"]}
        for table, fields in numeric_fields.items():
            for row in tables[table]:
                if any(not valid_number(row[field], 0) for field in fields):
                    raise ValueError(f"{table}: invalid numeric field")
        reconcile(tables, pdfs)
        inventory = {name: digest(data) for name, data in {**files, **pdfs}.items()}
        fingerprint = digest(json.dumps(inventory, sort_keys=True).encode())
        return tables, fingerprint, kit.read(root + SAMPLE)


def psql(sql):
    result = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                            input=sql, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


def insert_rows(table, rows):
    # Base64 has no SQL or psql metacharacters. Column conversion and constraints run in PostgreSQL.
    if not rows:
        return ""
    payload = base64.b64encode(json.dumps(rows, ensure_ascii=False).encode()).decode()
    return (f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table}, "
            f"convert_from(decode('{payload}','base64'),'UTF8')::jsonb);")


def valid_number(value, minimum, strict=False):
    try:
        n = Decimal(value)
        return n.is_finite() and (n > minimum if strict else n >= minimum)
    except InvalidOperation:
        return False


def batch_lines(content, items, plants):
    rows = read_csv(content, PR_FIELDS, "PR batch")
    keys = Counter((r["pr_no"], r["pr_line"]) for r in rows)
    batch_id = digest(content)
    lines = []
    for index, r in enumerate(rows, 1):
        issues = []
        if not r["pr_no"] or not re.fullmatch(r"[1-9]\d*", r["pr_line"]):
            issues.append("invalid PR key")
        if keys[r["pr_no"], r["pr_line"]] > 1:
            issues.append("duplicate PR key")
        if r["plant"] not in plants:
            issues.append("unknown plant")
        if not valid_number(r["quantity"], 0, strict=True):
            issues.append("quantity must be positive")
        code = r["item_code"]
        if code and code not in items:
            issues.append("unknown item code")
        if not r["uom"] or (code in items and r["uom"] != items[code]):
            issues.append("invalid unit")
        if not code and not r["description"]:
            issues.append("missing item description")
        if r["estimated_unit_price"] and not valid_number(r["estimated_unit_price"], 0):
            issues.append("invalid estimate")
        try:
            datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M")
            date.fromisoformat(r["need_by_date"])
        except ValueError:
            issues.append("invalid date")
        if r["urgency"] not in {"Normal", "Emergency"}:
            issues.append("invalid urgency")
        lines.append(dict(batch_id=batch_id, row_no=index, input=r, issue=", ".join(issues) or None))
    return batch_id, lines


def import_data(mode, path):
    sql = ["BEGIN;", "SELECT pg_advisory_xact_lock(53422017);", SCHEMA.read_text()]
    if mode == "kit":
        tables, fingerprint, content = load_kit(path)
        items = {r["item_code"]: r["uom"] for r in tables["items"]}
        plants = {r["plant"] for r in tables["plants"]}
        sql.extend([f"DO $$ BEGIN IF EXISTS (SELECT FROM baseline WHERE fingerprint <> '{fingerprint}') THEN "
                    "RAISE EXCEPTION 'Different baseline already installed'; END IF; END $$;",
                    "SELECT NOT EXISTS (SELECT FROM baseline) AS fresh \\gset", "\\if :fresh"])
        for table, rows in tables.items():
            sql.append(insert_rows(table, rows))
        sql.extend([f"INSERT INTO baseline(singleton,fingerprint) VALUES(true,'{fingerprint}');", "\\endif"])
    else:
        lookup = json.loads(psql("SELECT json_build_object('items',(SELECT json_object_agg(item_code,uom) FROM corvo.items),"
                                 "'plants',(SELECT json_agg(plant) FROM corvo.plants)) FROM corvo.baseline;"))
        items, plants = lookup["items"], set(lookup["plants"])
        content = path.read_bytes()
    batch_id, lines = batch_lines(content, items, plants)
    sql.extend([f"SELECT NOT EXISTS (SELECT FROM batches WHERE batch_id='{batch_id}') AS fresh_batch \\gset",
                "\\if :fresh_batch", f"INSERT INTO batches(batch_id) VALUES('{batch_id}');",
                insert_rows("pr_lines", lines), "\\endif", "COMMIT;"])
    psql("\n".join(sql))
    return {"batch_id": batch_id, "rows": len(lines), "flagged": sum(bool(r["issue"]) for r in lines)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["kit", "batch"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(import_data(args.mode, args.path)))
    except (ValueError, KeyError, OSError, BadZipFile, csv.Error, InvalidOperation) as exc:
        sys.exit(str(exc))
