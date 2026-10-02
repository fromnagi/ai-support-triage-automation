"""Additional deterministic n8n guards against the local fake services."""
import argparse
import json
import time

from local_e2e import request, ticket


def run(webhook, demo, prefix):
    checked = []

    invalid = ticket(prefix + "-BAD", "Order status")
    invalid["customer_email"] = "invalid-address"
    status, result = request("POST", webhook, invalid)
    assert status == 400 and result["error"] == "INVALID_INPUT", (status, result)
    checked.append("malformed inbound input")

    scenarios = [
        ("account", "Account access issue", "HIGH_RISK_CATEGORY"),
        ("low", "[FAKE:valid_low_confidence] Order status", "LOW_CONFIDENCE"),
        ("model_review", "[FAKE:model_review] Order status", "MODEL_REQUESTED_REVIEW"),
        ("invalid_enum", "[FAKE:invalid_enum] Order status", "AI_INVALID_OUTPUT"),
        ("invalid_confidence", "[FAKE:invalid_confidence] Order status", "AI_INVALID_OUTPUT"),
        ("rate_limit", "[FAKE:http_429] Order status", "AI_RATE_LIMIT"),
        ("service_error", "[FAKE:http_500] Order status", "AI_SERVICE_ERROR"),
    ]
    for label, subject, reason in scenarios:
        payload = ticket(prefix + "-" + label, subject)
        status, result = request("POST", webhook, payload)
        assert status == 200 and result["status"] == "HUMAN_REVIEW" and result["reason"] == reason, (label, status, result)
        _, audit = request("GET", demo + "/state/tickets/" + payload["ticket_id"])
        assert audit["ai_called"] == 1 and audit["record_id"] == "REVIEW-" + payload["ticket_id"]
        checked.append(label)

    original = ticket(prefix + "-account", "Account access issue")
    status, result = request("POST", webhook, dict(original, body="Materially different request"))
    assert status == 409 and result["disposition"] == "conflict", (status, result)
    checked.append("ticket ID content conflict")

    failed = ticket(prefix + "-CRMFAIL", "Order status")
    status, armed = request("POST", demo + "/control/crm-fail-once", {"ticket_id": failed["ticket_id"]})
    assert status == 200 and armed["armed"] is True
    status, result = request("POST", webhook, failed)
    assert status == 502 and result["status"] == "WRITE_FAILED", (status, result)
    _, audit = request("GET", demo + "/state/tickets/" + failed["ticket_id"])
    assert audit["status"] == "WRITE_FAILED" and audit["record_id"] is None
    status, result = request("POST", webhook, failed)
    assert status == 200 and result["status"] == "AUTO_ROUTED", (status, result)
    _, audit = request("GET", demo + "/state/tickets/" + failed["ticket_id"])
    _, cases = request("GET", demo + "/crm/cases")
    assert audit["attempts"] == 1
    assert len([c for c in cases if c["ticket_id"] == failed["ticket_id"]]) == 1
    checked.append("CRM 503 visibility and retry")
    print(json.dumps({"checks": checked, "count": len(checked)}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--webhook", default="http://127.0.0.1:5678/webhook/support-triage")
    p.add_argument("--demo", default="http://127.0.0.1:8765")
    p.add_argument("--prefix", default="EXT-" + str(int(time.time())))
    a = p.parse_args()
    run(a.webhook, a.demo, a.prefix)
