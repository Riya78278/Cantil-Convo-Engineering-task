"""Read-only verification of the original sample kit and Phase 1 specification."""
import csv
import io
import re
import sys
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "items": ("master_data/item_master", "item_code description uom category item_type standard_price", 73),
    "suppliers": ("master_data/supplier_master", "supplier_id name city categories status on_time_pct quality_rating", 48),
    "plants": ("master_data/plants", "plant name", 2),
    "history": ("master_data/po_history", "po_number po_date supplier_id item_code quantity uom unit_price plant delivered_on_time", 468),
    "register": ("contracts/contract_register", "contract_id supplier_id supplier_name start_date end_date value_cap consumed_value", 10),
    "rates": ("contracts/contract_lines", "contract_id item_code description uom rate", 26),
    "batch": ("pr_batches/sample_pr_batch", "pr_no pr_line created_at requester department plant item_code description quantity uom estimated_unit_price need_by_date urgency", 186),
}


class PhaseOne(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.headers = {}, {}
        with ZipFile(KIT) as archive:
            cls.bad_member = archive.testzip()
            root = "Cantill_FullStack_Task_Kit/"
            cls.members = {name[len(root):] for name in archive.namelist() if name.startswith(root)}
            for table, (filename, _, _) in TABLES.items():
                content = archive.read(root + filename + ".csv").decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(content))
                cls.rows[table] = list(reader)
                cls.headers[table] = reader.fieldnames

    def test_archive_integrity_and_reference_documents(self):
        self.assertIsNone(self.bad_member)
        self.assertTrue({"Cantill_FullStack_Task_Brief.pdf", "Corvo_Procurement_Policy.pdf",
                         "Worked_Examples.pdf"} <= self.members)

    def test_all_csv_headers_and_counts(self):
        for table, (_, fields, count) in TABLES.items():
            with self.subTest(table=table):
                self.assertEqual(self.headers[table], fields.split())
                self.assertEqual(len(self.rows[table]), count)
                self.assertTrue(all(None not in row and None not in row.values()
                                    for row in self.rows[table]))

    def test_unique_keys(self):
        for table, fields in {"items": ["item_code"], "suppliers": ["supplier_id"],
                              "plants": ["plant"], "register": ["contract_id"],
                              "rates": ["contract_id", "item_code"], "batch": ["pr_no", "pr_line"]}.items():
            keys = [tuple(row[k] for k in fields) for row in self.rows[table]]
            with self.subTest(table=table):
                self.assertEqual(len(keys), len(set(keys)))
                self.assertTrue(all(all(key) for key in keys))

    def test_reference_integrity(self):
        for table, key, master in [
            ("history", "plant", "plants"), ("history", "item_code", "items"),
            ("history", "supplier_id", "suppliers"), ("register", "supplier_id", "suppliers"),
            ("rates", "contract_id", "register"), ("rates", "item_code", "items"),
            ("batch", "item_code", "items"),
        ]:
            with self.subTest(table=table, key=key):
                source = {row[key] for row in self.rows[table] if row[key]}
                self.assertFalse(source - {row[key] for row in self.rows[master]})

    def test_signed_contract_inventory(self):
        signed = {Path(n).stem for n in self.members if n.startswith("contracts/pdf/") and n.endswith(".pdf")}
        registered = {r["contract_id"] for r in self.rows["register"]}
        self.assertTrue(registered <= signed)
        self.assertEqual(signed - registered, {"RC-2026-031", "RC-2026-033"})

    def test_status_and_batch_exceptions(self):
        statuses = Counter(r["status"] for r in self.rows["suppliers"])
        self.assertEqual(statuses, {"Active": 43, "Blocked": 3, "Inactive": 2})
        batch = self.rows["batch"]
        plants = {r["plant"] for r in self.rows["plants"]}
        self.assertEqual(sum(not r["item_code"] for r in batch), 39)
        self.assertEqual(sum(Decimal(r["quantity"]) <= 0 for r in batch), 1)
        self.assertEqual(sum(r["plant"] not in plants for r in batch), 1)

    def test_worked_example_1_exact_contract_value(self):
        line = next(r for r in self.rows["batch"] if r["pr_no"].endswith("00019") and r["pr_line"] == "1")
        rate = next(r for r in self.rows["rates"] if r["contract_id"] == "RC-2025-011"
                    and r["item_code"] == line["item_code"])
        self.assertEqual(Decimal(line["quantity"]) * Decimal(rate["rate"]), Decimal("137530.65"))

    def test_worked_example_3_insufficient_ceiling(self):
        contract = next(r for r in self.rows["register"] if r["contract_id"] == "RC-2026-002")
        rate = next(r for r in self.rows["rates"] if r["contract_id"] == contract["contract_id"]
                    and r["item_code"] == "ITM-1022")
        lines = [r for r in self.rows["batch"] if r["pr_no"][-5:] in {"00004", "00026"} and r["pr_line"] == "1"]
        self.assertEqual(len(lines), 2)
        value = sum(Decimal(r["quantity"]) for r in lines) * Decimal(rate["rate"])
        self.assertEqual(value, Decimal("220255.60"))
        self.assertGreater(value, Decimal(contract["value_cap"]) - Decimal(contract["consumed_value"]))

    def test_clause_matrix_complete_and_unique(self):
        spec = (ROOT / "docs/phase-one.md").read_text()
        ids = re.findall(r"^\| (\d+\.\d+) \| [^|]+ \| [^|]+ \|$", spec, re.M)
        expected = [f"{section}.{i}" for section, count in enumerate([2, 3, 1, 4, 7, 4, 2, 1], 1)
                    for i in range(1, count + 1)]
        self.assertEqual(ids, expected)

    def test_export_headers_and_submission_paths(self):
        spec = (ROOT / "docs/exports.md").read_text()
        headers = re.findall(r"```csv\n([^\n]+)\n```", spec)
        self.assertEqual(headers, [
            "pr_no,pr_line,resolved_item_code,group_id,po_id,decision,route,supplier_id,unit_price,group_value_inr,suggested_supplier_ids,policy_version,reason",
            "item_code,rank,supplier_id,reference_price",
        ])
        for suffix in ["run1", "run2", "preferred"]:
            self.assertIn(f"submission/yourname__{suffix}.csv", spec)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 tests/check_phase_one.py PATH_TO_ORIGINAL_KIT.zip")
    KIT = Path(sys.argv.pop())
    unittest.main(verbosity=2)
