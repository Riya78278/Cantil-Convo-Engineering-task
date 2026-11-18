"""Password authentication and expiring, database-backed sessions."""
import argparse
import getpass
import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path

from src.engine.service import document
from src.imports.load import psql


class Unauthorized(ValueError):
    pass


class Forbidden(ValueError):
    pass


def initialize():
    psql((Path(__file__).resolve().parents[2] / "db/workspace.sql").read_text())


def scalar(value):
    return f"({document(value)} #>> '{{}}')"


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def create_user(username, name, role, password):
    if not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_.@-]{3,80}", username):
        raise ValueError("Username must contain 3–80 letters, numbers, dots, @, underscores or hyphens")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Enter a display name")
    if role not in {"employee", "buyer", "procurement"}:
        raise ValueError("Unknown role")
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12–256 characters")
    salt = secrets.token_hex(16)
    psql("INSERT INTO corvo.app_users(username,display_name,role,password_hash,salt) VALUES ("
         f"{scalar(username)},{scalar(name.strip())},{scalar(role)},"
         f"{scalar(password_hash(password,salt))},{scalar(salt)});")


def login(username, password):
    if not isinstance(username,str) or not isinstance(password,str) or len(password)>256:
        raise Unauthorized("Invalid username or password")
    raw = psql("SELECT json_build_object('username',username,'display_name',display_name,'role',role,"
               "'password_hash',password_hash,'salt',salt,'locked',COALESCE(locked_until>now(),false))"
               f" FROM corvo.app_users WHERE username={scalar(username)};")
    user = json.loads(raw) if raw else None
    salt = user["salt"] if user else "00"*16
    actual = password_hash(password, salt)
    if not user or user["locked"] or not hmac.compare_digest(actual, user["password_hash"]):
        if user and not user["locked"]:
            psql("UPDATE corvo.app_users SET failures=failures+1,"
                 "locked_until=CASE WHEN failures+1>=5 THEN now()+interval '5 minutes' ELSE locked_until END"
                 f" WHERE username={scalar(username)};")
        raise Unauthorized("Invalid username or password, or account temporarily locked")
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    psql(f"BEGIN; UPDATE corvo.app_users SET failures=0,locked_until=NULL WHERE username={scalar(username)};"
         f"INSERT INTO corvo.sessions VALUES ('{digest}',{scalar(username)},now()+interval '12 hours'); COMMIT;")
    return token, {k:user[k] for k in ["username","display_name","role"]}


def authenticate(token):
    if not isinstance(token,str) or not token:
        raise Unauthorized("Please sign in")
    digest = hashlib.sha256(token.encode()).hexdigest()
    raw = psql("SELECT json_build_object('username',u.username,'display_name',u.display_name,'role',u.role)"
               " FROM corvo.sessions s JOIN corvo.app_users u USING(username)"
               f" WHERE s.token_hash='{digest}' AND s.expires_at>now();")
    if not raw:
        raise Unauthorized("Session expired. Please sign in.")
    return json.loads(raw)


def logout(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    psql(f"DELETE FROM corvo.sessions WHERE token_hash='{digest}';")


def require(user, roles):
    if user["role"] not in roles:
        raise Forbidden("Your role cannot perform this action")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Create a local Corvo account")
    parser.add_argument("username");parser.add_argument("--name",required=True)
    parser.add_argument("--role",choices=["employee","buyer","procurement"],required=True)
    args=parser.parse_args()
    from src.engine import service
    from src.policy import store
    service.initialize();store.initialize();initialize()
    password=getpass.getpass("Password (at least 12 characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords did not match")
    create_user(args.username,args.name,args.role,password)
    print("Account created.")
