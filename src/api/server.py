"""Local procurement application with authenticated chat, buyer and policy workflows."""
import argparse
import base64
import csv
import json
import logging
import os
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from src.engine import service
from src.engine.core import export_csv
from src.engine.preferred_export import render as preferred_csv
from src.policy import store
from src.workspace import auth, chat, buyer

WEB = Path(__file__).resolve().parents[1] / "web"


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, content_type="application/json", cookie=None):
        body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if cookie:self.send_header("Set-Cookie",cookie)
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get("Cookie",""))
        except Exception:return ""
        return cookie["corvo_session"].value if "corvo_session" in cookie else ""

    def session_cookie(self,token,age=43200):
        secure="; Secure" if os.environ.get("CORVO_SECURE_COOKIES")=="1" else ""
        return f"corvo_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}"+secure

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            assets = {"/": ("workspace.html", "text/html; charset=utf-8"),
                      "/policy": ("policy.html", "text/html; charset=utf-8"),
                      "/workspace.js": ("workspace.js","text/javascript; charset=utf-8"),
                      "/workspace.css": ("workspace.css","text/css; charset=utf-8"),
                      "/policy.js": ("policy.js", "text/javascript; charset=utf-8"),
                      "/policy.css": ("policy.css", "text/css; charset=utf-8")}
            if path in assets:
                name, mime = assets[path]
                self.send(200, (WEB / name).read_text(), mime)
                return
            user=auth.authenticate(self.token())
            if path=="/api/me":result=user
            elif path=="/api/catalogue":
                data=service.snapshot()
                result=dict(items=data["items"],plants=data["plants"],
                            suppliers=[r for r in data["suppliers"] if r["status"]=="Active"]
                            if user["role"]!="employee" else [])
            elif path=="/api/my-requests":result=buyer.my_requests(user)
            elif path.startswith("/api/conversations/"):result=chat.get(path.split("/")[-1],user)
            else:
                auth.require(user,{"buyer","procurement"})
                if path=="/api/preferred-suppliers.csv":
                    query = parse_qs(urlsplit(self.path).query)
                    result = preferred_csv(service.snapshot(), query.get("as_of", [None])[0],
                                           query.get("policy_version", [None])[0])
                    self.send(200, result, "text/csv; charset=utf-8")
                    return
                elif path=="/api/buyer/runs":result=buyer.list_runs()
                elif path.startswith("/api/buyer/runs/"):result=buyer.details(path.split("/")[-1])
                elif path=="/api/documents":result=buyer.documents()
                elif path=="/api/policies":result=store.list_all()
                elif path.startswith("/api/policy-drafts/"):result=store.get(path.split("/")[-1])
                else:
                    self.send(404, {"error": "Unknown endpoint"})
                    return
            self.send(200,result)
        except Exception as exc:self.failure(exc)

    def failure(self, exc):
        if isinstance(exc,auth.Unauthorized):self.send(401,{"error":str(exc)})
        elif isinstance(exc,auth.Forbidden):self.send(403,{"error":str(exc)})
        elif isinstance(exc, store.Conflict):self.send(409, {"error": str(exc)})
        elif isinstance(exc, (ValueError, UnicodeDecodeError, csv.Error, KeyError, TypeError)):
            if "ERROR:" in str(exc) or "psql:" in str(exc):
                logging.exception("Database processing failed")
                self.send(500, {"error": "Database processing failed"})
            else:self.send(400, {"error": str(exc)})
        else:
            logging.exception("Request failed")
            self.send(500, {"error": "Request failed"})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.send(403, {"error": "Cross-origin mutations are not allowed"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 10 * 1024 * 1024:
                self.send(413, {"error": "Supply a nonempty body of at most 10 MiB"})
                return
            self.connection.settimeout(30)
            content = self.rfile.read(size)
            if len(content) != size:raise ValueError("Incomplete request body")
            if path=="/api/login":
                payload=json.loads(content)
                token,user=auth.login(payload.get("username"),payload.get("password"))
                self.send(200,user,cookie=self.session_cookie(token))
                return
            user=auth.authenticate(self.token())
            if path=="/api/logout":
                auth.logout(self.token());self.send(200,{"signed_out":True},cookie=self.session_cookie("",0));return
            if path == "/api/process-batch":
                auth.require(user,{"buyer","procurement"})
                if self.headers.get_content_type() != "text/csv":
                    self.send(415, {"error": "Supply the PR CSV as a text/csv request body"});return
                version = parse_qs(urlsplit(self.path).query).get("policy_version", [None])[0]
                result = service.process(content, version)
                buyer.register_run(result,user,"batch")
                if self.headers.get("Accept") == "text/csv":self.send(200, export_csv(result["decisions"]), "text/csv; charset=utf-8")
                else:self.send(200, result)
                return
            if self.headers.get_content_type() != "application/json":
                self.send(415, {"error": "Supply JSON"});return
            payload = json.loads(content)
            if not isinstance(payload, dict):raise ValueError("Expected a JSON object")
            if path=="/api/conversations":result=chat.create(user)
            elif path.startswith("/api/conversations/"):
                parts=path.strip("/").split("/")
                if len(parts)!=4:raise ValueError("Unknown conversation action")
                cid,action=parts[2:]
                if action=="message":result=chat.send(cid,user,payload["revision"],payload["message"])
                elif action=="confirm":
                    result=chat.confirm(cid,user,payload["revision"])
                    try:
                        evaluated=buyer.evaluate_queue(user)
                        result["recommendation"]=next((r for r in evaluated["decisions"] if r["pr_no"]==result["pr_no"]),None)
                    except ValueError:
                        result["evaluation_note"]="Request recorded. The buyer will evaluate it before approval."
                else:raise ValueError("Unknown conversation action")
            elif path.startswith("/api/policy-drafts"):
                auth.require(user,{"procurement"})
                if path=="/api/policy-drafts":
                    file_data = payload.get("file_base64")
                    data = base64.b64decode(file_data, validate=True) if file_data is not None else None
                    result = store.create(user["username"], payload["base_version"], payload["version"],
                                          payload["effective_date"], text=payload.get("text"),
                                          data=data, filename=payload.get("filename"))
                else:
                    parts=path.strip("/").split("/")
                    if len(parts)!=4:raise ValueError("Unknown draft action")
                    identifier,action=parts[2:]
                    if action=="save":result=store.revise(identifier,payload["revision"],user["username"],payload["candidate"],payload["reviewed"])
                    elif action=="approve":result=store.approve(identifier,payload["revision"],user["username"])
                    else:raise ValueError("Unknown draft action")
            else:
                auth.require(user,{"buyer","procurement"})
                if path=="/api/replay-run":result=service.replay(payload["run_id"])
                elif path=="/api/buyer/evaluate":result=buyer.evaluate_queue(user)
                elif path=="/api/buyer/override":
                    result=buyer.override(payload["run_id"],payload["group_id"],user,payload["revision"],
                                          payload["route"],payload["comment"],payload.get("supplier_id",""),
                                          payload.get("unit_price",""),payload.get("contract_id",""))
                elif path=="/api/buyer/approve":
                    result=buyer.approve(payload["run_id"],payload["group_id"],user,payload["revision"],payload.get("capex_approved",False))
                else:self.send(404,{"error":"Unknown endpoint"});return
            self.send(200,result)
        except Exception as exc:self.failure(exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    service.initialize();store.initialize();auth.initialize()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
