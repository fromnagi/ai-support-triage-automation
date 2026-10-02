"""Concurrent synthetic submissions through a published localhost n8n webhook."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from local_e2e import request, ticket


def run(webhook, demo, ticket_id):
    value = ticket(ticket_id, "Order status")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: request("POST", webhook, value), range(12)))
    assert all(http in {200, 202} for http, _ in results), results
    assert sum(body.get("status") == "AUTO_ROUTED" and body.get("disposition") != "duplicate" for _, body in results) == 1, results
    assert sum(body.get("disposition") == "duplicate" for _, body in results) == 11, results
    _, audit = request("GET", demo + "/state/tickets/" + ticket_id)
    _, cases = request("GET", demo + "/crm/cases")
    assert audit["status"] == "AUTO_ROUTED"
    assert len([case for case in cases if case["ticket_id"] == ticket_id]) == 1
    changed = dict(value, body="Materially different synthetic content")
    http, conflict = request("POST", webhook, changed)
    assert http == 409 and conflict["disposition"] == "conflict"
    print(json.dumps({"ticket_id": ticket_id, "auto": 1, "duplicates": 11,
                      "crm_records": 1, "changed_content": "conflict"}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--webhook", default="http://127.0.0.1:5678/webhook/support-triage")
    p.add_argument("--demo", default="http://127.0.0.1:8765")
    p.add_argument("--ticket-id", required=True)
    a = p.parse_args()
    run(a.webhook, a.demo, a.ticket_id)
