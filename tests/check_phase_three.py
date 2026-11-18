"""Phase 3 acceptance tests using private worked examples and independent synthetic boundaries."""
import copy
import csv
import io
import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.imports.load import load_kit, PR_FIELDS
from src.engine.core import Engine, export_csv
from src.engine.policy import validate
from src.engine.service import POLICY_PATH

POLICY = json.loads(POLICY_PATH.read_text())


def csv_bytes(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, PR_FIELDS.split())
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode()


def request(key="P1", **kwargs):
    row = dict.fromkeys(PR_FIELDS.split(), "")
    row.update(pr_no=key, pr_line="1", created_at="2026-11-01 09:00", requester="Test",
               department="Test", plant="A", item_code="I1", description="Bearing 10x20",
               quantity="1", uom="NOS", need_by_date="2026-12-01", urgency="Normal")
    row.update(kwargs)
    return row


def fixture(price="100"):
    return dict(plants=[{"plant":"A"},{"plant":"B"}],
                items=[dict(item_code="I1", description="Bearing 10x20", uom="NOS",
                            category="Parts", item_type="Material", standard_price=price)],
                suppliers=[dict(supplier_id="S1",status="Active",categories="Parts",on_time_pct="90",quality_rating="4")],
                po_history=[dict(po_number="1",po_date="2026-10-01",supplier_id="S1",item_code="I1",unit_price=price)],
                contracts=[],contract_lines=[])


def contract(tables, cid="C1", price="80", cap="1000", consumed="0", **kwargs):
    row = dict(contract_id=cid,supplier_id="S1",start_date="2026-01-01",end_date="2026-12-31",
               value_cap=cap,consumed_value=consumed)
    row.update(kwargs)
    tables["contracts"].append(row)
    tables["contract_lines"].append(dict(contract_id=cid,item_code="I1",rate=price))


class Boundaries(unittest.TestCase):
    def run_rows(self, tables, rows, policies=None):
        return Engine(tables, policies or [copy.deepcopy(POLICY)]).process(csv_bytes(rows))["decisions"]

    def test_thresholds(self):
        for value, emergency, expected in [
            ("199999.99","Normal","SPOT_PO"),("200000","Normal","RFQ"),
            ("500000","Emergency","EMERGENCY_PO"),("500000.01","Emergency","RFQ"),
            ("999999.99","Normal","RFQ"),("1000000","Normal","RFP")]:
            with self.subTest(value=value, urgency=emergency):
                self.assertEqual(self.run_rows(fixture(value),[request(urgency=emergency)])[0]["route"],expected)

    def test_no_history_and_blocked_suppliers(self):
        for status in ["Blocked","Inactive"]:
            t=fixture()
            t["suppliers"][0]["status"]=status
            contract(t)
            r=self.run_rows(t,[request(urgency="Emergency")])[0]
            self.assertEqual(r["route"],"RFQ")
            self.assertEqual(r["suggested_supplier_ids"],"")
        t=fixture()
        t["po_history"]=[]
        self.assertEqual(self.run_rows(t,[request()])[0]["route"],"RFQ")

    def test_rule_priority(self):
        t=fixture("1000000")
        contract(t,cap="9999999")
        self.assertEqual(self.run_rows(t,[request(urgency="Emergency")])[0]["route"],"CONTRACT_PO")
        t["items"][0]["item_type"]="Capex"
        engine=Engine(t,[POLICY])
        result=engine.process(csv_bytes([request()]))
        self.assertEqual(result["decisions"][0]["route"],"CAPEX_SOURCING")
        self.assertEqual(result["closing_contract_balances"]["C1"],"0")

    def test_ceiling_exact_and_next_cheapest(self):
        t=fixture()
        contract(t,price="80",cap="80")
        self.assertEqual(self.run_rows(t,[request()])[0]["unit_price"],"80.00")
        t["contracts"][0]["consumed_value"]="0.01"
        contract(t,cid="C2",price="90",cap="100")
        r=self.run_rows(t,[request()])[0]
        self.assertIn("C2",r["reason"])
        self.assertEqual(r["group_value_inr"],"90.00")
        t["contracts"][1]["consumed_value"]="11"
        r=self.run_rows(t,[request()])[0]
        self.assertEqual(r["route"],"SPOT_PO")
        self.assertEqual(r["group_value_inr"],"80.00")
        self.assertEqual(r["unit_price"],"100.00")

    def test_shared_ceiling_across_items(self):
        t=fixture()
        contract(t,cap="100")
        t["items"].append({**t["items"][0],"item_code":"I2","description":"Other"})
        t["contract_lines"].append(dict(contract_id="C1",item_code="I2",rate="80"))
        r=self.run_rows(t,[request(),request("P2",item_code="I2")])
        self.assertEqual([x["route"] for x in r],["CONTRACT_PO","RFQ"])

    def test_contract_date_endpoints(self):
        for start,end,expected in [
            ("2026-11-01","2026-11-01","CONTRACT_PO"),
            ("2026-11-02","2026-12-01","SPOT_PO"),
            ("2026-01-01","2026-10-31","SPOT_PO")]:
            t=fixture()
            contract(t,start_date=start,end_date=end)
            self.assertEqual(self.run_rows(t,[request()])[0]["route"],expected)

    def test_clubbing_first_anchor_and_earliest(self):
        rows=[request(),request("P2",created_at="2026-11-15 23:00",need_by_date="2026-12-08"),
              request("P3",created_at="2026-11-16 09:00"),request("P4",need_by_date="2026-12-09")]
        r=self.run_rows(fixture(),rows)
        self.assertEqual(r[0]["group_id"],r[1]["group_id"])
        self.assertNotEqual(r[0]["group_id"],r[2]["group_id"])
        self.assertNotEqual(r[0]["group_id"],r[3]["group_id"])
        rows=[request(),request("P2",need_by_date="2026-12-09"),
              request("P3",created_at="2026-11-02 09:00",need_by_date="2026-12-05")]
        r=self.run_rows(fixture(),rows)
        self.assertEqual(r[0]["group_id"],r[2]["group_id"])

    def test_plants_and_emergency_propagation(self):
        r=self.run_rows(fixture("150000"),[request(),request("P2",urgency="Emergency"),request("P3",plant="B")])
        self.assertEqual(r[0]["route"],"EMERGENCY_PO")
        self.assertEqual(r[0]["group_value_inr"],"300000.00")
        self.assertNotEqual(r[0]["group_id"],r[2]["group_id"])

    def test_consolidation_24_hours_and_route(self):
        rows=[request(),request("P2",created_at="2026-11-02 09:00",need_by_date="2027-01-01"),
              request("P3",created_at="2026-11-02 09:01",need_by_date="2027-02-01"),
              request("P4",urgency="Emergency",need_by_date="2027-03-01")]
        r=self.run_rows(fixture(),rows)
        self.assertEqual(r[0]["po_id"],r[1]["po_id"])
        self.assertNotEqual(r[0]["group_id"],r[1]["group_id"])
        self.assertNotEqual(r[0]["po_id"],r[2]["po_id"])
        self.assertNotEqual(r[0]["po_id"],r[3]["po_id"])

    def test_history_windows_reference_and_future(self):
        t=fixture()
        t["po_history"][0]["po_date"]="2025-11-01"
        self.assertEqual(self.run_rows(t,[request()])[0]["route"],"SPOT_PO")
        t["po_history"][0]["po_date"]="2025-10-31"
        self.assertEqual(self.run_rows(t,[request()])[0]["route"],"RFQ")
        t["po_history"][0]["po_date"]="2026-11-01"
        self.assertEqual(self.run_rows(t,[request()])[0]["route"],"RFQ")
        contract(t,end_date="2026-10-01")
        ranked=Engine(t,[POLICY]).preferred("I1",date(2026,11,1),POLICY)
        self.assertEqual(ranked[0]["reference_price"],Decimal("80"))

    def test_unknown_and_ambiguous_matching(self):
        t=fixture()
        t["items"].append({**t["items"][0],"item_code":"I2","description":"Bearing 30x40"})
        rows=[request(item_code="",description="Bearing"),
              request("P2",item_code="",description="New widget",estimated_unit_price="10"),
              request("P3",item_code="",description="New widget",estimated_unit_price="10"),
              request("P4",item_code="",description="New widget"),
              request("P5",item_code="",description="Bearing 20x10",estimated_unit_price="10")]
        r=self.run_rows(t,rows)
        self.assertEqual(r[0]["route"],"NOT_PROCESSED")
        self.assertEqual(r[1]["route"],"NEW_ITEM_SOURCING")
        self.assertNotEqual(r[1]["group_id"],r[2]["group_id"])
        self.assertEqual(r[3]["route"],"NOT_PROCESSED")
        self.assertEqual(r[4]["resolved_item_code"],"NEW")

    def test_policy_data_changes_without_engine_edit(self):
        amended=copy.deepcopy(POLICY)
        amended.update(version="5.1",effective_date="2026-11-02")
        amended["rules"].insert(0,dict(clause="9.1",route="RFQ",suggestions=2,
            when={"all":[{"field":"plant","op":"eq","value":"B"},
                         {"field":"category","op":"in","value":["Parts"]}]}))
        rows=[request(plant="B"),request("P2",plant="B",created_at="2026-11-02 09:00")]
        r=self.run_rows(fixture(),rows,[POLICY,amended])
        self.assertEqual([x["route"] for x in r],["SPOT_PO","RFQ"])
        self.assertEqual([x["policy_version"] for x in r],["5.0","5.1"])
        invalid=copy.deepcopy(POLICY)
        invalid["rules"][0]["when"]={"code":"eval"}
        with self.assertRaises(ValueError):validate(invalid)

    def test_preferred_548_day_boundary(self):
        from datetime import timedelta
        t=fixture()
        as_of=date(2026,11,1)
        t["po_history"][0]["po_date"]=(as_of-timedelta(days=548)).isoformat()
        self.assertEqual(len(Engine(t,[POLICY]).preferred("I1",as_of,POLICY)),1)
        t["po_history"][0]["po_date"]=(as_of-timedelta(days=549)).isoformat()
        self.assertEqual(Engine(t,[POLICY]).preferred("I1",as_of,POLICY),[])

    def test_input_order_independent_processing(self):
        rows=[request(),request("P2",created_at="2026-11-02 09:00")]
        forward=self.run_rows(fixture(),rows)
        backward=self.run_rows(fixture(),list(reversed(rows)))
        self.assertEqual(forward,list(reversed(backward)))

    def test_repeatability_and_csv_roundtrip(self):
        t=fixture()
        contract(t)
        engine=Engine(t,[POLICY])
        content=csv_bytes([request()])
        first=engine.process(content)
        self.assertEqual(first,engine.process(content))
        self.assertEqual(list(csv.DictReader(io.StringIO(export_csv(first["decisions"])))),first["decisions"])
        self.assertEqual(t["contracts"][0]["consumed_value"],"0")


class WorkedExamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tables,_,content=load_kit(KIT)
        cls.engine=Engine(tables,[POLICY])
        cls.result=cls.engine.process(content)
        cls.rows={(r["pr_no"],r["pr_line"]):r for r in cls.result["decisions"]}

    def row(self,number):
        return self.rows[f"PR-26-{number:05d}","1"]

    def test_all_fourteen_examples(self):
        cases=[
            (19,"CONTRACT_PO","S001","137530.65"), (45,"CONTRACT_PO","S021","26829.11"),
            (4,"RFQ","","220255.60"),(34,"CONTRACT_PO","S007","32546.60"),
            (146,"SPOT_PO","S024","126471.60"),(106,"SPOT_PO","S027","156170.30"),
            (88,"SPOT_PO","S023","97955.00"),(59,"EMERGENCY_PO","S014","253184.40"),
            (89,"RFQ","","336562.15"),(50,"RFQ","","80300.00"),
            (100,"RFP","","2070780.00"),(47,"CAPEX_SOURCING","","6036704.08"),
            (170,"NEW_ITEM_SOURCING","","141100.00"),(31,"CONTRACT_PO","S007","23227.40")]
        for number,route,supplier,value in cases:
            with self.subTest(number=number):
                r=self.row(number)
                self.assertEqual((r["route"],r["supplier_id"],r["group_value_inr"]),(route,supplier,value))
        for members in [(4,26),(34,32),(146,144,145),(31,35)]:
            self.assertEqual(len({self.row(n)["group_id"] for n in members}),1)
        self.assertNotEqual(self.row(106)["group_id"],self.row(103)["group_id"])
        self.assertEqual(len({self.row(n)["po_id"] for n in [31,35,34,32]}),1)
        self.assertNotEqual(self.row(31)["group_id"],self.row(34)["group_id"])
        for n,ids in [(4,"S014;S013;S016"),(89,"S043;S044;S042"),(50,"S003;S002;S005"),
                      (100,"S020;S017;S019;S018"),(47,"S045;S047;S020;S048;S046")]:
            self.assertEqual(self.row(n)["suggested_supplier_ids"],ids)

    def test_three_preferred_lists(self):
        for item,expected in [("ITM-1048",["S029","S030"]),("ITM-1036",["S023","S022","S024"]),
                              ("ITM-1016",["S009","S010"])]:
            self.assertEqual([r["supplier_id"] for r in self.engine.preferred(item,date(2026,11,1),POLICY)],expected)

    def test_every_sample_line_and_supplier_safety(self):
        self.assertEqual(len(self.result["decisions"]),186)
        self.assertEqual(sum(r["route"]=="NOT_PROCESSED" for r in self.result["decisions"]),2)
        for r in self.result["decisions"]:
            ids=[r["supplier_id"]] if r["supplier_id"] else r["suggested_supplier_ids"].split(";")
            for sid in filter(None,ids):
                self.assertTrue(self.engine.active(sid))


if __name__=="__main__":
    KIT=Path(sys.argv.pop())
    unittest.main(verbosity=2)
