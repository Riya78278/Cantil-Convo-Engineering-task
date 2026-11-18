"""Employee chat, identity and buyer workflow acceptance with real HTTP and PostgreSQL."""
import copy
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.imports.load import import_data,load_kit,psql
from src.engine import service
from src.policy import store
from src.workspace import auth,chat
from src.api.server import Handler

PASSWORD="Synthetic-phase-five-password-123"


class ConversationParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables,_,_=load_kit(KIT)
    def advance(self,state,text):
        return chat.advance(state,text,self.tables,date(2026,11,18))

    def test_complete_request_and_missing_urgency(self):
        state,reply=self.advance({},"need 200 nitrile gloves for Chakan by next Friday")
        self.assertEqual(state["fields"]["item_code"],"ITM-1048")
        self.assertEqual(state["fields"]["need_by_date"],"2026-11-20")
        self.assertFalse(state["ready"])
        state,reply=self.advance(state,"Normal")
        self.assertTrue(state["ready"])
        self.assertIn("confirm",reply.lower())

    def test_ambiguous_catalogue_choices(self):
        state,_=self.advance({},"Need 20 bearings")
        # Plural descriptions use the same catalogue candidates.
        self.assertFalse(state["ready"]);self.assertGreater(len(state["choices"]),1)
        state,_=self.advance({},"Need 20 bearing")
        self.assertGreater(len(state["choices"]),1)
        state,_=self.advance(state,"ITM-1021")
        self.assertEqual(state["fields"]["item_code"],"ITM-1021")

    def test_copied_instruction_prefix_and_paste_artifacts(self):
        for text in ["Type Need 200 nitrile gloves for Chakan by tomorrow",
                     "&#x9;Type Need 200 nitrile gloves for Chakan by tomorrow\\",
                     "\tEnter Need 200 nitrile gloves for Chakan by tomorrow\\"]:
            with self.subTest(text=text):
                state,reply=self.advance({},text)
                self.assertEqual(state["fields"]["item_code"],"ITM-1048")
                self.assertEqual(state["fields"]["quantity"],"200")
                self.assertEqual(state["fields"]["plant"],"CHK1")
                self.assertEqual(state["awaiting"],"urgency")
                self.assertEqual(reply,"Is this Normal or Emergency?")

    def test_type_in_actual_item_description_is_preserved(self):
        state,_=self.advance({},"Need 1 Type K thermocouple for Chakan by tomorrow")
        self.assertEqual(state["fields"]["description"],"Type K thermocouple")

    def test_unknown_item_estimate_and_unit(self):
        state,_=self.advance({},"Need 1 quantum widget for Chakan by tomorrow normal")
        self.assertTrue(state["unknown"])
        state,_=self.advance(state,"new item")
        self.assertEqual(state["awaiting"],"uom")
        state,_=self.advance(state,"NOS")
        self.assertEqual(state["awaiting"],"estimated_unit_price")
        state,_=self.advance(state,"INR 1200")
        self.assertTrue(state["ready"])
        self.assertEqual(state["fields"]["estimated_unit_price"],"1200")

    def test_invalid_quantity_date_and_units(self):
        state,_=self.advance({},"need 0 nitrile gloves for Chakan by 2026-01-01 normal")
        self.assertFalse(state["ready"]);self.assertEqual(state["awaiting"],"quantity")
        state,_=self.advance(state,"200")
        self.assertEqual(state["awaiting"],"need_by_date")
        state,_=self.advance(state,"tomorrow")
        self.assertTrue(state["ready"])
        state,_=self.advance({},"need 20 KG nitrile gloves for Chakan by tomorrow normal")
        self.assertFalse(state["ready"]);self.assertEqual(state["awaiting"],"uom")
        state,_=self.advance(state,"PAIR")
        self.assertTrue(state["ready"])

    def test_correction_and_restart(self):
        state,_=self.advance({},"need 200 nitrile gloves for Chakan by tomorrow normal")
        state,_=self.advance(state,"change quantity to 50")
        self.assertEqual(state["fields"]["quantity"],"50")
        state,_=self.advance(state,"need 10 safety helmet ISI")
        self.assertEqual(state["fields"]["item_code"],"ITM-1049")
        state,_=self.advance(state,"start over")
        self.assertEqual(state["fields"],{})


class WorkspaceHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db="corvo_workspace_"+uuid.uuid4().hex
        subprocess.run(["createdb",db],check=True)
        cls.addClassCleanup(subprocess.run,["dropdb",db],check=True)
        previous=os.environ.get("PGDATABASE")
        cls.addClassCleanup(lambda:os.environ.pop("PGDATABASE",None) if previous is None else os.environ.__setitem__("PGDATABASE",previous))
        os.environ["PGDATABASE"]=db
        import_data("kit",KIT);service.initialize();store.initialize();auth.initialize()
        cls.tokens={}
        for username,role in [("employee1","employee"),("employee2","employee"),("buyer1","buyer"),("procurement1","procurement")]:
            auth.create_user(username,username,role,PASSWORD)
            cls.tokens[username]=auth.login(username,PASSWORD)[0]
        with ZipFile(KIT) as z:cls.csv=z.read(next(n for n in z.namelist() if n.endswith("/sample_pr_batch.csv")))
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.addClassCleanup(cls.stop)

    @classmethod
    def stop(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        psql("DELETE FROM corvo.workspace_events;DELETE FROM corvo.approved_pr_keys;DELETE FROM corvo.document_lines;"
             "DELETE FROM corvo.purchase_documents;DELETE FROM corvo.buyer_overrides;DELETE FROM corvo.requests;"
             "DELETE FROM corvo.conversations;DELETE FROM corvo.run_owners;DELETE FROM corvo.runs;")

    def call(self,path,payload=None,username="buyer1",raw=None,token=None):
        connection=HTTPConnection("127.0.0.1",self.server.server_port,timeout=20)
        headers={"Content-Type":"text/csv" if raw is not None else "application/json"}
        if username is not None:headers["Cookie"]="corvo_session="+(token or self.tokens[username])
        body=raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        connection.request("POST" if body is not None else "GET",path,body=body,headers=headers)
        response=connection.getresponse();status=response.status;data=response.read()
        self.cookie=response.getheader("Set-Cookie");connection.close()
        return status,json.loads(data)

    def batch(self):
        status,result=self.call("/api/process-batch",raw=self.csv)
        self.assertEqual(status,200,result)
        return result

    def test_preferred_csv_http_permissions_validation_and_read_only(self):
        import csv
        import io
        from src.engine.preferred_export import render
        before = service.snapshot()
        count = psql("SELECT count(*) FROM corvo.runs;")
        path = "/api/preferred-suppliers.csv?as_of=2026-11-01&policy_version=5.0"
        self.assertEqual(self.call(path,username=None)[0],401)
        self.assertEqual(self.call(path,username="employee1")[0],403)
        self.assertEqual(self.call('/api/preferred-suppliers.csv')[0],400)
        self.assertEqual(self.call('/api/preferred-suppliers.csv?as_of=2026-02-30')[0],400)
        self.assertEqual(self.call(path.replace('5.0','missing'))[0],400)
        for user in ['buyer1','procurement1']:
            connection = HTTPConnection('127.0.0.1',self.server.server_port,timeout=20)
            connection.request('GET',path,headers={'Cookie':'corvo_session='+self.tokens[user]})
            response = connection.getresponse(); body = response.read().decode(); connection.close()
            self.assertEqual(response.status,200)
            self.assertEqual(response.getheader('Content-Type'),'text/csv; charset=utf-8')
            self.assertEqual(body,render(before,'2026-11-01','5.0'))
            rows = list(csv.DictReader(io.StringIO(body)))
            for item, ids in [('ITM-1048',['S029','S030']),('ITM-1036',['S023','S022','S024']),('ITM-1016',['S009','S010'])]:
                self.assertEqual([r['supplier_id'] for r in rows if r['item_code']==item],ids)
        self.assertEqual(service.snapshot(),before)
        self.assertEqual(psql("SELECT count(*) FROM corvo.runs;"),count)

    def group(self,result,pr):
        row=next(r for r in result["decisions"] if r["pr_no"]==pr and r["pr_line"]=="1")
        return row["group_id"]

    def approve(self,result,gid,**kwargs):
        payload=dict(run_id=result["run_id"],group_id=gid,revision=0);payload.update(kwargs)
        return self.call("/api/buyer/approve",payload)

    def test_authentication_roles_and_logout(self):
        self.assertEqual(self.call("/api/buyer/runs",username=None)[0],401)
        self.assertEqual(self.call("/api/buyer/runs",username="employee1")[0],403)
        self.assertEqual(self.call("/api/policy-drafts",{},username="buyer1")[0],403)
        status,user=self.call("/api/login",{"username":"employee1","password":PASSWORD},username=None)
        self.assertEqual(status,200);self.assertIn("HttpOnly",self.cookie);self.assertIn("SameSite=Strict",self.cookie)
        token=self.cookie.split(";",1)[0].split("=",1)[1]
        self.assertEqual(self.call("/api/logout",{},username="employee1",token=token)[0],200)
        self.assertEqual(self.call("/api/me",username="employee1",token=token)[0],401)
        self.assertNotEqual(psql("SELECT password_hash FROM corvo.app_users WHERE username='employee1'"),PASSWORD)

    def test_chat_confirmation_is_atomic_and_private(self):
        status,draft=self.call("/api/conversations",{},username="employee1")
        self.assertEqual(status,200)
        path="/api/conversations/"+draft["conversation_id"]
        self.assertEqual(self.call(path,username="employee2")[0],400)
        status,draft=self.call(path+"/message",{"revision":1,"message":"need 20 nitrile gloves for Chakan by tomorrow normal"},username="employee1")
        self.assertEqual(status,200,draft);self.assertTrue(draft["state"]["ready"])
        self.assertEqual(psql("SELECT count(*) FROM corvo.requests"),"0")
        self.assertEqual(self.call(path+"/confirm",{"revision":1},username="employee1")[0],409)
        payload={"revision":draft["revision"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.call(path+"/confirm",payload,username="employee1"),range(2)))
        self.assertEqual([r[0] for r in responses],[200,200])
        self.assertEqual(psql("SELECT count(*) FROM corvo.requests"),"1")
        status,evaluated=self.call("/api/buyer/evaluate",{})
        self.assertEqual(status,200,evaluated)
        gid=evaluated["decisions"][0]["group_id"]
        status,approved=self.approve(evaluated,gid)
        self.assertEqual(status,200,approved)
        status,requests=self.call("/api/my-requests",username="employee1")
        self.assertIsNotNone(requests[0]["document_id"])
        self.assertEqual(self.call("/api/my-requests",username="employee2")[1],[])

    def test_parallel_approval_and_po_consolidation(self):
        result=self.batch()
        first=self.group(result,"PR-26-00031");second=self.group(result,"PR-26-00034")
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.approve(result,first),range(2)))
        self.assertEqual([r[0] for r in responses],[200,200])
        self.assertEqual(psql("SELECT count(*) FROM corvo.document_lines"),"1")
        status,other=self.approve(result,second);self.assertEqual(status,200,other)
        self.assertEqual(other["approval"]["document_id"],responses[0][1]["approval"]["document_id"])
        self.assertEqual(psql("SELECT count(*) FROM corvo.purchase_documents"),"1")
        self.assertEqual(psql("SELECT count(*) FROM corvo.workspace_events WHERE action='approved'"),"2")

    def test_override_comment_original_and_actor(self):
        result=self.batch();gid=self.group(result,"PR-26-00146")
        payload=dict(run_id=result["run_id"],group_id=gid,revision=0,route="RFQ",comment="")
        self.assertEqual(self.call("/api/buyer/override",payload)[0],400)
        payload.update(comment="Buyer requests competitive quotes",actor="forged-user")
        status,group=self.call("/api/buyer/override",payload)
        self.assertEqual(status,200,group)
        self.assertEqual(group["original"]["route"],"SPOT_PO")
        self.assertEqual(group["effective"]["route"],"RFQ")
        self.assertEqual(self.approve(result,gid)[0],409)
        status,approved=self.approve(result,gid,revision=1)
        self.assertEqual(status,200,approved)
        self.assertEqual(approved["approval"]["actor"],"buyer1")
        self.assertEqual(psql("SELECT kind FROM corvo.purchase_documents"),"SOURCING_REQUEST")
        self.assertEqual(self.call("/api/buyer/override",payload)[0],409)

    def test_blocked_supplier_and_contract_exhaustion_rechecked(self):
        result=self.batch();gid=self.group(result,"PR-26-00019")
        original=psql("SELECT status FROM corvo.suppliers WHERE supplier_id='S001'")
        try:
            psql("UPDATE corvo.suppliers SET status='Blocked' WHERE supplier_id='S001'")
            self.assertEqual(self.approve(result,gid)[0],409)
        finally:psql("UPDATE corvo.suppliers SET status='Active' WHERE supplier_id='S001'")
        opening=psql("SELECT consumed_value FROM corvo.contracts WHERE contract_id='RC-2025-011'")
        try:
            psql("UPDATE corvo.contracts SET consumed_value=value_cap WHERE contract_id='RC-2025-011'")
            self.assertEqual(self.approve(result,gid)[0],409)
            self.assertEqual(psql("SELECT count(*) FROM corvo.purchase_documents"),"0")
        finally:psql(f"UPDATE corvo.contracts SET consumed_value={opening} WHERE contract_id='RC-2025-011'")

    def test_capex_approval_and_duplicate_across_runs(self):
        result=self.batch();gid=self.group(result,"PR-26-00047")
        self.assertEqual(self.approve(result,gid)[0],400)
        self.assertEqual(self.approve(result,gid,capex_approved=True)[0],200)
        # Changed input byte order creates a separate run but still carries the same PR identity.
        import csv,io
        rows=list(csv.DictReader(io.StringIO(self.csv.decode())))
        out=io.StringIO();writer=csv.DictWriter(out,rows[0].keys());writer.writeheader();writer.writerows(reversed(rows))
        status,other=self.call("/api/process-batch",raw=out.getvalue().encode())
        self.assertEqual(status,200)
        self.assertEqual(self.approve(other,self.group(other,"PR-26-00047"),capex_approved=True)[0],409)

    def test_actual_spending_blocks_second_award(self):
        import csv,io
        rows=list(csv.DictReader(io.StringIO(self.csv.decode())))
        template=next(r for r in rows if r["pr_no"]=="PR-26-00019")
        cap=psql("SELECT value_cap FROM corvo.contracts WHERE contract_id='RC-2025-011'")
        try:
            psql("UPDATE corvo.contracts SET value_cap=consumed_value+137530.65 WHERE contract_id='RC-2025-011'")
            results=[]
            for key in ["LEDGER-1","LEDGER-2"]:
                row={**template,"pr_no":key}
                out=io.StringIO();w=csv.DictWriter(out,row.keys());w.writeheader();w.writerow(row)
                status,result=self.call("/api/process-batch",raw=out.getvalue().encode())
                self.assertEqual(status,200);results.append(result)
            self.assertEqual(self.approve(results[0],results[0]["decisions"][0]["group_id"])[0],200)
            self.assertEqual(self.approve(results[1],results[1]["decisions"][0]["group_id"])[0],409)
            self.assertEqual(psql("SELECT count(*) FROM corvo.document_lines"),"1")
        finally:psql(f"UPDATE corvo.contracts SET value_cap={cap} WHERE contract_id='RC-2025-011'")

    def test_independent_simulation_after_live_approval(self):
        result=self.batch();gid=self.group(result,"PR-26-00019")
        self.assertEqual(self.approve(result,gid)[0],200)
        replay=self.batch()
        self.assertEqual(result,replay)
        status,documents=self.call("/api/documents")
        self.assertEqual(status,200);self.assertEqual(len(documents),1)


if __name__=="__main__":
    KIT=Path(sys.argv.pop())
    unittest.main(verbosity=2)
