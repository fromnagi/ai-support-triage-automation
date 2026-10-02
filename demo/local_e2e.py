"""Exercise the published localhost n8n webhook with synthetic requests."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(method, url, body=None):
    raw = None if body is None else json.dumps(body).encode()
    req = Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    try:
        response = urlopen(req, timeout=45)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read().decode('utf-8', errors='replace')
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {'raw_body': raw}
        return response.status, result


def ticket(ticket_id, subject, body="Where is my order?"):
    return {"ticket_id": ticket_id, "customer_email": "person@example.test",
            "subject": subject, "body": body, "source": "form"}


def run(webhook, demo, prefix):
    scenarios = [
        ("auto", ticket(prefix + "-AUTO", "Order status"), "AUTO_ROUTED", "AUTO"),
        ("review", ticket(prefix + "-REVIEW", "Refund request"), "HUMAN_REVIEW", "REVIEW"),
        ("ai_failure", ticket(prefix + "-FAIL", "[FAKE:malformed_json] Order status"), "HUMAN_REVIEW", "REVIEW"),
    ]
    results = {}
    for label, payload, status, route in scenarios:
        http, result = request("POST", webhook, payload)
        assert http == 200, (label, http, result)
        assert result["ticket_id"] == payload["ticket_id"], (label, result)
        assert result["status"] == status and result["route"] == route, (label, result)
        _, audit = request("GET", demo + "/state/tickets/" + payload["ticket_id"])
        assert audit["status"] == status and audit["ai_called"] == 1, (label, audit)
        results[label] = {"response": result, "audit": audit}

    duplicate_payload = scenarios[0][1]
    http, duplicate = request("POST", webhook, duplicate_payload)
    assert http == 200 and duplicate["disposition"] == "duplicate", (http, duplicate)
    _, cases = request("GET", demo + "/crm/cases")
    _, reviews = request("GET", demo + "/reviews")
    assert len([c for c in cases if c["ticket_id"] == duplicate_payload["ticket_id"]]) == 1
    for payload in (scenarios[1][1], scenarios[2][1]):
        assert len([r for r in reviews if r["ticket_id"] == payload["ticket_id"]]) == 1
    assert not any(c["ticket_id"] in {scenarios[1][1]["ticket_id"], scenarios[2][1]["ticket_id"]} for c in cases)
    print(json.dumps({"scenarios": results, "duplicate": duplicate,
                      "case_count_for_auto": 1, "review_count_for_review_and_failure": 2}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--webhook", default="http://127.0.0.1:5678/webhook/support-triage")
    p.add_argument("--demo", default="http://127.0.0.1:8765")
    p.add_argument("--prefix", default="BASIC-" + str(int(time.time())))
    a = p.parse_args()
    run(a.webhook, a.demo, a.prefix)
