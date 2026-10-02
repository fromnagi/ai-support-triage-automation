"""Synthetic, local business-system APIs. n8n owns orchestration and policy."""
import argparse
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

SOURCES = {"form", "email", "helpdesk"}
FIELDS = {"ticket_id", "customer_email", "subject", "body", "source"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# ECMAScript trim whitespace, shared with validate.js. Python strip differs.
TRIM = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
EMAIL = re.compile(r'^[^' + TRIM + r'@]+@[^' + TRIM + r'@]+\.[^' + TRIM + r'@]+$')


def js_length(value):
    return len(value.encode('utf-16-le', errors='surrogatepass')) // 2


def normalize_ticket(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("INVALID_INPUT")
    if any(not isinstance(value[k], str) for k in FIELDS):
        raise ValueError("INVALID_INPUT")
    t = {k: value[k].strip(TRIM) for k in FIELDS}
    # Avoid Unicode case-folding differences between Python and JavaScript.
    t["customer_email"] = t["customer_email"].translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))
    if not ID.fullmatch(t["ticket_id"]) or not EMAIL.fullmatch(t["customer_email"]):
        raise ValueError("INVALID_INPUT")
    if t["source"] not in SOURCES or not 1 <= js_length(t["subject"]) <= 200 or not 1 <= js_length(t["body"]) <= 5000:
        raise ValueError("INVALID_INPUT")
    if js_length(t["customer_email"]) > 254:
        raise ValueError("INVALID_INPUT")
    return t


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.fail_once_ids = set()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY, content TEXT NOT NULL, status TEXT NOT NULL,
                    ai_called INTEGER NOT NULL DEFAULT 0, category TEXT, confidence REAL,
                    route TEXT, reason TEXT, record_id TEXT, attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS crm_cases (
                    ticket_id TEXT PRIMARY KEY, case_id TEXT UNIQUE NOT NULL, draft_reply TEXT NOT NULL,
                    attempt_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    ticket_id TEXT PRIMARY KEY, review_id TEXT UNIQUE NOT NULL, reason TEXT NOT NULL,
                    draft_reply TEXT NOT NULL, attempt_id INTEGER NOT NULL
                );
            """)
            for table in ('crm_cases', 'reviews'):
                if 'attempt_id' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                    # Existing records have no attempt provenance; leave it unknown.
                    db.execute(f'ALTER TABLE {table} ADD COLUMN attempt_id INTEGER')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, raw):
        t = normalize_ticket(raw)
        encoded = json.dumps(t, sort_keys=True, separators=(",", ":"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT content,status,attempts,record_id FROM tickets WHERE ticket_id=?", (t["ticket_id"],)).fetchone()
            if row is None:
                db.execute("INSERT INTO tickets(ticket_id,content,status) VALUES(?,?,'RECEIVED')", (t["ticket_id"], encoded))
                disposition = "new"
            elif row["content"] != encoded:
                disposition = "conflict"
            elif row["status"] == "WRITE_FAILED":
                db.execute("UPDATE tickets SET status='RECEIVED',attempts=attempts+1,ai_called=0,category=NULL,confidence=NULL,route=NULL,reason=NULL,record_id=NULL WHERE ticket_id=?", (t["ticket_id"],))
                disposition = "retry"
            else:
                if row['status'] in {'AUTO_ROUTED', 'HUMAN_REVIEW'}:
                    table = 'crm_cases' if row['status'] == 'AUTO_ROUTED' else 'reviews'
                    record = db.execute(f'SELECT attempt_id FROM {table} WHERE ticket_id=?', (t['ticket_id'],)).fetchone()
                    if record is None or record['attempt_id'] != row['attempts']:
                        raise ValueError('INVALID_ATTEMPT')
                disposition = "duplicate"
            status = "RECEIVED" if disposition in {"new", "retry"} else row["status"]
            current = db.execute('SELECT attempts,record_id FROM tickets WHERE ticket_id=?', (t['ticket_id'],)).fetchone()
        return {"disposition": disposition, "ticket": t, "ticket_id": t["ticket_id"], "status": status,
                "attempt_id": current['attempts'], "record_id": current['record_id']}

    @staticmethod
    def attempt(data):
        value = data.get('attempt_id')
        if type(value) is not int or value < 0 or value > 9007199254740991:
            raise ValueError('INVALID_ATTEMPT')
        return value

    def triage(self, data):
        attempt = self.attempt(data)
        ticket_id = data["ticket_id"]
        route = data["route"]
        reason = data["reason"]
        if route not in {"AUTO", "REVIEW"} or not isinstance(reason, str) or len(reason) > 40:
            raise ValueError("INVALID_TRIAGE")
        category = data.get("category")
        confidence = data.get("confidence")
        status = "AI_FAILED" if reason.startswith("AI_") else "AI_CLASSIFIED"
        with self.connect() as db:
            cur = db.execute("UPDATE tickets SET status=?,ai_called=1,category=?,confidence=?,route=?,reason=? WHERE ticket_id=? AND status='RECEIVED' AND attempts=?", (status, category, confidence, route, reason, ticket_id, attempt))
            if cur.rowcount != 1:
                raise ValueError("INVALID_STATE")
        return {"ticket_id": ticket_id, "status": status, "route": route, "attempt_id": attempt}

    def write(self, kind, data):
        attempt = self.attempt(data)
        ticket_id = data["ticket_id"]
        route = "AUTO" if kind == "crm" else "REVIEW"
        draft = data.get("draft_reply") or ""
        reason = data.get("reason") or ""
        if not isinstance(draft, str) or len(draft) > 1200 or not isinstance(reason, str) or len(reason) > 40:
            raise ValueError("INVALID_WRITE")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT route,status,attempts FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            if row is None or row['attempts'] != attempt or row["route"] != route or row["status"] not in {"AI_CLASSIFIED", "AI_FAILED", "AUTO_ROUTED", "HUMAN_REVIEW"}:
                raise ValueError("INVALID_STATE")
            if kind == "crm":
                record_id = "CASE-" + ticket_id
                db.execute("INSERT OR IGNORE INTO crm_cases(ticket_id,case_id,draft_reply,attempt_id) VALUES(?,?,?,?)", (ticket_id, record_id, draft, attempt))
                status = "AUTO_ROUTED"
            else:
                record_id = "REVIEW-" + ticket_id
                db.execute("INSERT OR IGNORE INTO reviews(ticket_id,review_id,reason,draft_reply,attempt_id) VALUES(?,?,?,?,?)", (ticket_id, record_id, reason, draft, attempt))
                status = "HUMAN_REVIEW"
            table = 'crm_cases' if kind == 'crm' else 'reviews'
            record = db.execute(f'SELECT attempt_id FROM {table} WHERE ticket_id=?', (ticket_id,)).fetchone()
            if record['attempt_id'] != attempt:
                raise ValueError('INVALID_ATTEMPT')
            db.execute("UPDATE tickets SET status=?,record_id=? WHERE ticket_id=?", (status, record_id, ticket_id))
        return {"ticket_id": ticket_id, "record_id": record_id, "route": route, "status": status, "attempt_id": attempt}

    def write_failed(self, ticket_id, attempt_id, reason="BUSINESS_WRITE_FAILED"):
        attempt = self.attempt({'attempt_id': attempt_id})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT route,status,record_id,attempts FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            if row is None or row['attempts'] != attempt or row["route"] not in {"AUTO", "REVIEW"}:
                raise ValueError("INVALID_STATE")
            route = row["route"]
            expected = ("CASE-" if route == "AUTO" else "REVIEW-") + ticket_id
            table, column = ("crm_cases", "case_id") if route == "AUTO" else ("reviews", "review_id")
            record = db.execute(f"SELECT {column},attempt_id FROM {table} WHERE ticket_id=?", (ticket_id,)).fetchone()
            if record:
                status = "AUTO_ROUTED" if route == "AUTO" else "HUMAN_REVIEW"
                if record[column] != expected:
                    raise ValueError("INVALID_STATE")
                if record['attempt_id'] != attempt:
                    raise ValueError('INVALID_ATTEMPT')
                db.execute("UPDATE tickets SET status=?,record_id=? WHERE ticket_id=?", (status, expected, ticket_id))
                return {"ticket_id": ticket_id, "route": route, "status": status, "record_id": expected, "attempt_id": attempt}
            if row["status"] not in {"AI_CLASSIFIED", "AI_FAILED", "WRITE_FAILED"}:
                raise ValueError("INVALID_STATE")
            db.execute("UPDATE tickets SET status='WRITE_FAILED',reason=?,record_id=NULL WHERE ticket_id=?", (reason, ticket_id))
            return {"ticket_id": ticket_id, "route": route, "status": "WRITE_FAILED", "record_id": None, "attempt_id": attempt}

    def get(self, ticket_id):
        with self.connect() as db:
            row = db.execute("SELECT ticket_id,status,ai_called,category,confidence,route,reason,record_id,attempts,attempts AS attempt_id FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            return dict(row) if row else None

    def list_records(self, kind):
        table = "crm_cases" if kind == "crm" else "reviews"
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY ticket_id")]


def fake_llm(data):
    content = data["messages"][-1]["content"]
    ticket = json.loads(content)
    match = re.search(r"\[FAKE:([a-z0-9_]+)\]", ticket["subject"])
    mode = match.group(1) if match else "valid_high_confidence"
    if mode == "timeout":
        time.sleep(4)
    if mode == "http_429":
        return 429, {"error": "rate limited"}
    if mode == "http_500":
        return 500, {"error": "service error"}
    category = "refund" if "refund" in ticket["subject"].lower() else "account_access" if "account" in ticket["subject"].lower() else "order_status"
    result = {"category": category, "priority": "normal", "summary": "Synthetic support request", "suggested_action": "Review case details", "draft_reply": "Thanks for contacting us. A team member will follow up.", "confidence": 0.95, "requires_human_review": False}
    if mode == "valid_low_confidence": result["confidence"] = 0.5
    if mode == "model_review": result["requires_human_review"] = True
    if mode == "invalid_enum": result["category"] = "wire_money"
    if mode == "invalid_confidence": result["confidence"] = 1.5
    if mode == "malformed_json": return 200, {"message": {"content": "{broken"}}
    return 200, {"message": {"content": json.dumps(result)}}


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, value):
            raw = json.dumps(value).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except ConnectionError:
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/state/tickets/"):
                row = store.get(path.rsplit("/", 1)[-1])
                return self.send(200 if row else 404, row or {"error": "NOT_FOUND"})
            if path == "/crm/cases": return self.send(200, store.list_records("crm"))
            if path == "/reviews": return self.send(200, store.list_records("review"))
            self.send(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 20000: raise ValueError("INVALID_INPUT")
                data = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/state/tickets": return self.send(200, store.register(data))
                if path == "/state/triage": return self.send(200, store.triage(data))
                if path == "/state/write-failed":
                    if set(data) != {"ticket_id", "attempt_id"} or not isinstance(data["ticket_id"], str) or not ID.fullmatch(data["ticket_id"]):
                        raise ValueError("INVALID_INPUT")
                    return self.send(200, store.write_failed(data["ticket_id"], data['attempt_id']))
                if path == "/control/crm-fail-once":
                    ticket_id = data.get("ticket_id")
                    if not isinstance(ticket_id, str) or not ID.fullmatch(ticket_id):
                        raise ValueError("INVALID_INPUT")
                    store.fail_once_ids.add(ticket_id)
                    return self.send(200, {"ticket_id": ticket_id, "armed": True})
                if path == "/crm/cases":
                    if data.get("simulate_failure") or data.get("ticket_id") in store.fail_once_ids:
                        store.fail_once_ids.discard(data["ticket_id"])
                        store.write_failed(data["ticket_id"], data.get('attempt_id'))
                        return self.send(503, {"error": "BUSINESS_WRITE_FAILED"})
                    return self.send(200, store.write("crm", data))
                if path == "/reviews": return self.send(200, store.write("review", data))
                if path == "/fake-llm/classify":
                    status, body = fake_llm(data)
                    return self.send(status, body)
                self.send(404, {"error": "NOT_FOUND"})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                code = str(exc) if str(exc) in {"INVALID_INPUT", "INVALID_TRIAGE", "INVALID_STATE", "INVALID_WRITE", "INVALID_ATTEMPT"} else "INVALID_INPUT"
                self.send(400, {"error": code})

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=".local/demo.db")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(Store(args.db))).serve_forever()
