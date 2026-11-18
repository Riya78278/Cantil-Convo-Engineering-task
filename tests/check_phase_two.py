"""Integration tests. Uses a unique disposable PostgreSQL database."""
import csv
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/imports"))
from load import PR_FIELDS, import_data, psql


class PrivateDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database = "corvo_test_" + uuid.uuid4().hex
        subprocess.run(["createdb", database], check=True)
        cls.addClassCleanup(subprocess.run, ["dropdb", database], check=True)
        original = os.environ.get("PGDATABASE")
        os.environ["PGDATABASE"] = database
        cls.addClassCleanup(os.environ.pop if original is None else os.environ.__setitem__,
                            *(("PGDATABASE",) if original is None else ("PGDATABASE", original)))

    def count(self, table):
        return int(psql(f"SELECT count(*) FROM corvo.{table}"))


class KitImport(PrivateDatabase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.summary = import_data("kit", KIT)

    def test_counts_provenance_and_opening_balances(self):
        expected = dict(plants=2, items=73, suppliers=48, po_history=468, contracts=12, contract_lines=31)
        for table, count in expected.items():
            self.assertEqual(self.count(table), count)
        self.assertEqual(psql("SELECT count(*) FROM corvo.contracts WHERE source='pdf' AND consumed_value=0"
                              " AND length(pdf_sha256)=64"), "2")
        self.assertEqual(psql("SELECT rate FROM corvo.contract_lines WHERE contract_id='RC-2026-031'"
                              " AND item_code='ITM-1037'"), "2439.01")
        self.assertEqual(psql("SELECT consumed_value FROM corvo.contracts WHERE contract_id='RC-2025-011'"), "4100000")

    def test_sample_rows_and_issues(self):
        batch = self.summary["batch_id"]
        report = psql(f"SELECT json_build_array(count(*), count(issue), count(*) FILTER (WHERE input->>'item_code'=''))"
                      f" FROM corvo.pr_lines WHERE batch_id='{batch}'")
        self.assertEqual(json.loads(report), [186, 2, 39])
        self.assertEqual(psql(f"SELECT issue FROM corvo.pr_lines WHERE batch_id='{batch}' AND issue IS NOT NULL ORDER BY issue"
                              ).splitlines(), ["quantity must be positive", "unknown plant"])

    def test_reimport_same_kit_and_standalone_sample(self):
        before = self.count("pr_lines"), self.count("batches")
        self.assertEqual(import_data("kit", KIT), self.summary)
        with ZipFile(KIT) as archive, tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "same.csv"
            member = next(n for n in archive.namelist() if n.endswith("/sample_pr_batch.csv"))
            path.write_bytes(archive.read(member))
            self.assertEqual(import_data("batch", path), self.summary)
        self.assertEqual((self.count("pr_lines"), self.count("batches")), before)
        self.assertEqual(self.count("po_history"), 468)

    def test_concurrent_reimport(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: import_data("kit", KIT), range(2)))
        self.assertEqual(results, [self.summary, self.summary])
        self.assertEqual(self.count("po_history"), 468)
        self.assertEqual(psql(f"SELECT count(*) FROM corvo.pr_lines WHERE batch_id='{self.summary['batch_id']}'"), "186")

    def test_novel_batch_keeps_duplicate_keys_and_bad_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new.csv"
            row = dict.fromkeys(PR_FIELDS.split(), "")
            row.update(pr_no="Synthetic", pr_line="1", quantity="1", uom="NOS", description="Sample",
                       created_at="bad", need_by_date="2027-01-01", urgency="Normal", plant="CHK1")
            with path.open("w", newline="") as out:
                writer = csv.DictWriter(out, PR_FIELDS.split())
                writer.writeheader()
                writer.writerows([row, row])
            result = import_data("batch", path)
            self.assertEqual(result["flagged"], 2)
            self.assertEqual(psql(f"SELECT issue FROM corvo.pr_lines WHERE batch_id='{result['batch_id']}' ORDER BY row_no"
                                  ).splitlines(), ["duplicate PR key, invalid date"] * 2)
            path.write_text("invalid,header\n1,2\n")
            before = self.count("batches")
            with self.assertRaisesRegex(ValueError, "column"):
                import_data("batch", path)
            self.assertEqual(self.count("batches"), before)


class AtomicImport(PrivateDatabase):
    def test_bad_history_rolls_back_whole_import(self):
        with tempfile.TemporaryDirectory() as tmp, ZipFile(KIT) as original:
            path = Path(tmp) / "invalid.zip"
            with ZipFile(path, "w") as changed:
                for name in original.namelist():
                    data = original.read(name)
                    if name.endswith("/po_history.csv"):
                        rows = data.splitlines(keepends=True)
                        cells = rows[1].split(b",")
                        cells[2] = b"missing-supplier"
                        rows[1] = b",".join(cells)
                        data = b"".join(rows)
                    changed.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "foreign key constraint"):
                import_data("kit", path)
            self.assertEqual(psql("SELECT to_regnamespace('corvo') IS NULL"), "t")


if __name__ == "__main__":
    KIT = Path(sys.argv.pop())
    unittest.main(verbosity=2)
