"""Regenerate a compact, synthetic offline evidence fixture."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.fake_business_system import Store

OUTPUT = ROOT / "docs/demo/offline-scenarios.json"
JS = r"""
const fs=require('fs');
const code=fs.readFileSync('workflow_code/policy.js','utf8');
const {ticket,response}=JSON.parse(fs.readFileSync(0,'utf8'));
const fn=new Function('$input','$',code);
const result=fn({first:()=>({json:response})},name=>({first:()=>({json:{ticket_id:ticket.ticket_id,attempt_id:0,ticket}})}))[0].json;
process.stdout.write(JSON.stringify(result));
"""


def ticket(ticket_id, subject, body="Synthetic question about an order"):
    return {"ticket_id": ticket_id, "customer_email": "customer@example.test",
            "subject": subject, "body": body, "source": "form"}


def classify(value, category="order_status", confidence=0.95, *, malformed=False):
    content = "{broken" if malformed else json.dumps({
        "category": category, "priority": "normal", "summary": "Synthetic request",
        "suggested_action": "Review the request", "draft_reply": "Thank you. We will follow up.",
        "confidence": confidence, "requires_human_review": False,
    })
    response = {"statusCode": 200, "body": {"message": {"content": content}}}
    completed = subprocess.run(["node", "-e", JS], input=json.dumps({"ticket": value, "response": response}),
                               text=True, capture_output=True, cwd=ROOT, check=True)
    return json.loads(completed.stdout)


def build():
    results = []
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "demo.db")
        cases = [
            ("safe_order", ticket("DEMO-AUTO", "Order status"), "order_status", False),
            ("forced_review", ticket("DEMO-REFUND", "Refund request"), "refund", False),
            ("ai_failure", ticket("DEMO-AI-FAIL", "Order status"), "order_status", True),
        ]
        for label, value, category, malformed in cases:
            assert store.register(value)["disposition"] == "new"
            decision = classify(value, category, malformed=malformed)
            store.triage(decision)
            kind = "crm" if decision["route"] == "AUTO" else "review"
            write = store.write(kind, decision)
            results.append({"scenario": label, "ticket_id": value["ticket_id"],
                            "route": decision["route"], "reason": decision["reason"],
                            "status": store.get(value["ticket_id"])["status"],
                            "record_id": write["record_id"]})
        duplicate = store.register(cases[0][1])["disposition"]
        assert duplicate == "duplicate" and len(store.list_records("crm")) == 1
        results.append({"scenario": "duplicate", "ticket_id": "DEMO-AUTO", "disposition": duplicate,
                        "crm_count_for_ticket": 1})
        failed = ticket("DEMO-WRITE-FAIL", "Order status")
        store.register(failed)
        decision = classify(failed)
        store.triage(decision)
        unresolved = store.write_failed(failed["ticket_id"], 0)
        assert unresolved["status"] == "WRITE_FAILED"
        assert store.register(failed)["disposition"] == "retry"
        decision["attempt_id"] = 1
        store.triage(decision)
        store.write("crm", decision)
        results.append({"scenario": "write_failure_retry", "ticket_id": failed["ticket_id"],
                        "first_status": unresolved["status"], "final_status": store.get(failed["ticket_id"])["status"],
                        "crm_count_for_ticket": 1})
    return {"scope": "Synthetic offline Store and actual workflow policy code; n8n execution is reported separately",
            "scenarios": results}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != rendered.encode("utf-8"):
            raise SystemExit("Demo artifact differs from deterministic generator")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(rendered.encode("utf-8"))
