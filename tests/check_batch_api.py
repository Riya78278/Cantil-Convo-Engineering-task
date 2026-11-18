"""Real HTTP and PostgreSQL tests in a disposable database."""
import csv
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.imports.load import import_data, psql
from src.engine import service
from src.api.server import Handler
from src.workspace import auth


class BatchAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db="corvo_api_"+uuid.uuid4().hex
        subprocess.run(["createdb",cls.db],check=True)
        cls.addClassCleanup(subprocess.run,["dropdb",cls.db],check=True)
        previous=os.environ.get("PGDATABASE")
        cls.addClassCleanup(lambda: os.environ.pop("PGDATABASE",None) if previous is None
                            else os.environ.__setitem__("PGDATABASE",previous))
        os.environ["PGDATABASE"]=cls.db
        import_data("kit",KIT)
        service.initialize();auth.initialize()
        auth.create_user("testproc","Test Procurement","procurement","Synthetic-test-password-123")
        cls.token,_=auth.login("testproc","Synthetic-test-password-123")
        with ZipFile(KIT) as z:
            cls.content=z.read(next(n for n in z.namelist() if n.endswith("/sample_pr_batch.csv")))
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop)

    @classmethod
    def stop(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self,content,content_type="text/csv",accept="application/json"):
        connection=HTTPConnection("127.0.0.1",self.server.server_port,timeout=10)
        connection.request("POST","/api/process-batch",body=content,
                           headers={"Content-Type":content_type,"Accept":accept,"Cookie":"corvo_session="+self.token})
        response=connection.getresponse()
        status,body=response.status,response.read()
        connection.close()
        return status,body

    def test_real_request_replay_and_persisted_run(self):
        status,body=self.call(self.content)
        self.assertEqual(status,200)
        result=json.loads(body)
        self.assertEqual(len(result["decisions"]),186)
        self.assertEqual(self.call(self.content),(status,body))
        self.assertEqual(psql("SELECT count(*) FROM corvo.runs"),"1")
        self.assertEqual(psql("SELECT snapshot->>'input_csv' FROM corvo.runs").splitlines()[0],
                         self.content.decode().splitlines()[0])
        self.assertEqual(psql("SELECT consumed_value FROM corvo.contracts WHERE contract_id='RC-2025-011'"),"4100000")

    def test_csv_response(self):
        status,body=self.call(self.content,accept="text/csv")
        self.assertEqual(status,200)
        rows=list(csv.DictReader(io.StringIO(body.decode())))
        self.assertEqual(len(rows),186)
        self.assertEqual(sum(r["route"]=="NOT_PROCESSED" for r in rows),2)

    def test_bad_input_is_rejected_without_run(self):
        before=psql("SELECT count(*) FROM corvo.runs")
        self.assertEqual(self.call(b"bad,header\n1,2\n")[0],400)
        self.assertEqual(self.call(self.content,content_type="application/json")[0],415)
        self.assertEqual(psql("SELECT count(*) FROM corvo.runs"),before)


if __name__=="__main__":
    KIT=Path(sys.argv.pop())
    unittest.main(verbosity=2)
