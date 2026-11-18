"""Policy interpretation, approval, effective-date and replay tests over real HTTP/PostgreSQL."""
import base64
import copy
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.engine import service
from src.engine.policy import validate
from src.imports.load import import_data, psql
from src.policy import store
from src.policy.reader import propose, preview, read_source
from src.api.server import Handler
from src.workspace import auth

BASE=json.loads(service.POLICY_PATH.read_text())


class ReaderTests(unittest.TestCase):
    def test_thresholds_windows_and_order(self):
        text="Set spot limit to 1.5 lakh\nSet RFP threshold to INR 1200000\nSet consolidation window to 12 hours\nMove clause 5.3 before clause 5.2"
        draft,unresolved=propose(text,BASE,"5.1","2026-11-01")
        self.assertEqual(unresolved,[])
        self.assertEqual(draft["po_hours"],12)
        self.assertLess([r["clause"] for r in draft["rules"]].index("5.3"),[r["clause"] for r in draft["rules"]].index("5.2"))
        self.assertTrue(any("150000" in c for c in preview(BASE,draft)))
        self.assertEqual(sum("1200000" in json.dumps(r) for r in draft["rules"]),2)

    def test_plant_category_new_rule(self):
        draft,unresolved=propose('Add clause 9.1 before clause 5.2: for plant CHK1 and category "Safety" and value at least 1000, require RFQ, suggest 2',BASE,"5.1","2026-11-01")
        self.assertFalse(unresolved)
        rule=next(r for r in draft["rules"] if r["clause"]=="9.1")
        self.assertEqual(rule["when"]["all"][1]["value"],"Safety")
        self.assertEqual(rule["suggestions"],2)

    def test_partial_extraction_preserves_every_unresolved_statement(self):
        draft,unresolved=propose("Set spot limit to 100000\nSomething novel must be handled\nSet consolidation window to 3 days",BASE,"5.1","2026-11-01")
        self.assertEqual(len(unresolved),2)
        self.assertEqual(draft["po_hours"],24)
        self.assertTrue(any("100000" in c for c in preview(BASE,draft)))

    def test_failed_sentence_is_atomic(self):
        draft,unresolved=propose("Move clause 5.3 before clause missing",BASE,"5.1","2026-11-01")
        self.assertEqual(draft["rules"],BASE["rules"])
        self.assertEqual(len(unresolved),1)

    def test_json_and_validation_rejections(self):
        self.assertEqual(propose(json.dumps(BASE),BASE,"x","2026-11-01")[0],BASE)
        bad=copy.deepcopy(BASE);bad["rules"][0]["when"]={"field":"value","op":"in","value":["NaN"]}
        with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(BASE);bad["ranking_weights"]["quality"]=-1
        with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(BASE);bad["rules"][0]["unrecognized"]=True
        with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(BASE);bad["effective_date"]="tomorrow"
        with self.assertRaises(ValueError):validate(bad)

    def test_text_pdf_extraction(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=500,height=500)
        font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),
                               NameObject("/BaseFont"):NameObject("/Helvetica")})
        page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):font})})
        stream=DecodedStreamObject();stream.set_data(b"BT /F1 12 Tf 20 400 Td (Set spot limit to 100000) Tj ET")
        page[NameObject("/Contents")]=writer._add_object(stream)
        out=io.BytesIO();writer.write(out)
        text=read_source(out.getvalue(),"amendment.pdf")
        self.assertIn("Set spot limit",text)
        self.assertEqual(propose(text,BASE,"5.1","2026-11-01")[1],[])
        blank=PdfWriter();blank.add_blank_page(width=100,height=100);out=io.BytesIO();blank.write(out)
        with self.assertRaisesRegex(ValueError,"no readable text"):read_source(out.getvalue(),"scan.pdf")


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db="corvo_policy_"+uuid.uuid4().hex
        subprocess.run(["createdb",db],check=True)
        cls.addClassCleanup(subprocess.run,["dropdb",db],check=True)
        previous=os.environ.get("PGDATABASE")
        cls.addClassCleanup(lambda: os.environ.pop("PGDATABASE",None) if previous is None
                            else os.environ.__setitem__("PGDATABASE",previous))
        os.environ["PGDATABASE"]=db
        import_data("kit",KIT);service.initialize();store.initialize();auth.initialize()
        auth.create_user("testproc","Test Procurement","procurement","Synthetic-test-password-123")
        cls.token,_=auth.login("testproc","Synthetic-test-password-123")
        with ZipFile(KIT) as z:cls.csv=z.read(next(n for n in z.namelist() if n.endswith("/sample_pr_batch.csv")))
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.addClassCleanup(cls.stop)

    @classmethod
    def stop(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        psql("DELETE FROM corvo.policy_events; DELETE FROM corvo.policy_drafts;"
             "DELETE FROM corvo.policies WHERE version <> '5.0'; DELETE FROM corvo.run_owners; DELETE FROM corvo.runs;")

    def call(self,path,payload=None,raw=None):
        connection=HTTPConnection("127.0.0.1",self.server.server_port,timeout=20)
        headers={"Content-Type":"text/csv" if raw is not None else "application/json","Cookie":"corvo_session="+self.token}
        connection.request("POST" if payload is not None or raw is not None else "GET",path,
                           body=raw if raw is not None else json.dumps(payload).encode() if payload is not None else None,
                           headers=headers)
        response=connection.getresponse();status=response.status;body=response.read();connection.close()
        return status,json.loads(body) if response.getheader("Content-Type","").startswith("application/json") else body.decode()

    def create(self,text="Set spot limit to 100000",**kwargs):
        payload=dict(actor="Test buyer",base_version="5.0",version="5.1",effective_date="2026-11-01",text=text)
        payload.update(kwargs)
        status,draft=self.call("/api/policy-drafts",payload)
        self.assertEqual(status,200,draft)
        return draft

    def save(self,draft,reviewed=True):
        return self.call("/api/policy-drafts/"+draft["draft_id"]+"/save",
                         dict(actor="Reviewer",revision=draft["revision"],candidate=draft["candidate"],reviewed=reviewed))

    def approve(self,draft):
        return self.call("/api/policy-drafts/"+draft["draft_id"]+"/approve",
                         dict(actor="Approver",revision=draft["revision"]))

    def test_end_to_end_change_replay_and_audit(self):
        status,first=self.call("/api/process-batch",raw=self.csv);self.assertEqual(status,200)
        draft=self.create()
        self.assertEqual(self.call("/api/process-batch",raw=self.csv)[1],first)
        self.assertEqual(self.approve(draft)[0],409)
        status,draft=self.save(draft);self.assertEqual(status,200)
        status,approved=self.approve(draft);self.assertEqual(status,200,approved)
        self.assertEqual(approved["status"],"approved")
        status,second=self.call("/api/process-batch",raw=self.csv);self.assertEqual(status,200)
        def row(result):return next(r for r in result["decisions"] if r["pr_no"]=="PR-26-00146")
        self.assertEqual(row(first)["route"],"SPOT_PO");self.assertEqual(row(second)["route"],"RFQ")
        self.assertEqual(row(second)["policy_version"],"5.1")
        self.assertEqual(self.call("/api/replay-run",{"run_id":first["run_id"]})[1],first)
        self.assertEqual(self.call("/api/process-batch?policy_version=5.0",raw=self.csv)[1],first)
        self.assertEqual([e["action"] for e in approved["audit"]],["created","edited","approved"])
        self.assertEqual(self.approve(draft)[0],200)
        self.assertEqual(psql("SELECT count(*) FROM corvo.policy_events WHERE action='approved'"),"1")

    def test_future_effective_date_and_restart_persistence(self):
        draft=self.create(effective_date="2027-01-01")
        draft=self.save(draft)[1];self.assertEqual(self.approve(draft)[0],200)
        service.initialize();store.initialize()
        status,result=self.call("/api/process-batch",raw=self.csv)
        self.assertEqual(status,200)
        self.assertEqual({r["policy_version"] for r in result["decisions"] if r["decision"]},{"5.0"})
        self.assertEqual(len(self.call("/api/policies")[1]["policies"]),2)

    def test_stale_save_and_competing_approvals(self):
        one=self.create()
        two=self.create(version="5.2",effective_date="2026-11-02")
        status,new=self.save(one);self.assertEqual(status,200)
        self.assertEqual(self.save(one)[0],409)
        two=self.save(two)[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda d:self.approve(d)[0],[new,two]))
        self.assertEqual(sorted(statuses),[200,409])

    def test_duplicate_version_rejected_and_same_date_supersession(self):
        draft=self.create(version="5.0");draft=self.save(draft)[1]
        self.assertEqual(self.approve(draft)[0],409)
        draft=self.create(effective_date="2026-04-01");draft=self.save(draft)[1]
        self.assertEqual(self.approve(draft)[0],200)
        latest=self.call("/api/process-batch",raw=self.csv)[1]
        old=self.call("/api/process-batch?policy_version=5.0",raw=self.csv)[1]
        self.assertEqual({r["policy_version"] for r in latest["decisions"] if r["decision"]},{"5.1"})
        self.assertEqual({r["policy_version"] for r in old["decisions"] if r["decision"]},{"5.0"})

    def test_uploaded_text_and_corrected_unresolved_source(self):
        payload=dict(actor="Uploader",base_version="5.0",version="5.1",effective_date="2026-11-01",
                     filename="change.txt",file_base64=base64.b64encode(b"Please lower the spot limit dramatically").decode())
        status,draft=self.call("/api/policy-drafts",payload);self.assertEqual(status,200)
        self.assertEqual(len(draft["unresolved"]),1)
        self.assertEqual(self.approve(draft)[0],409)
        draft["candidate"],_=propose("Set spot limit to 50000",BASE,"5.1","2026-11-01")
        draft=self.save(draft)[1];self.assertEqual(self.approve(draft)[0],200)
        self.assertEqual(store.get(draft["draft_id"])["source_text"],"Please lower the spot limit dramatically")

    def test_invalid_json_editor_and_ui_assets(self):
        draft=self.create();draft["candidate"]["rules"][0]["when"]={"field":"not-a-field","op":"eq","value":"x"}
        self.assertEqual(self.save(draft)[0],400)
        self.assertEqual(self.call("/")[0],200)
        self.assertEqual(self.call("/policy.js")[0],200)
        self.assertEqual(self.call("/policy.css")[0],200)


if __name__=="__main__":
    KIT=Path(sys.argv.pop())
    unittest.main(verbosity=2)
