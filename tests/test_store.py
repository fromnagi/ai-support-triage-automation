import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from demo.fake_business_system import Store, fake_llm, normalize_ticket


def ticket(ticket_id="T-1", subject="Order status"):
    return {"ticket_id": ticket_id, "customer_email": "Person@Example.Test", "subject": subject,
            "body": "Where is my order?", "source": "form"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_validation_and_identity(self):
        self.assertEqual(self.store.register(ticket())["disposition"], "new")
        self.assertEqual(self.store.register(ticket())["disposition"], "duplicate")
        self.assertEqual(self.store.register(ticket(subject="Different"))["disposition"], "conflict")
        self.assertEqual(self.store.get("T-1")["status"], "RECEIVED")
        with self.assertRaisesRegex(ValueError, "INVALID_INPUT"):
            normalize_ticket({**ticket("T-2"), "source": "unknown"})
        with self.assertRaisesRegex(ValueError, "INVALID_INPUT"):
            normalize_ticket({**ticket("T-2"), "extra": "x"})

    def test_crm_idempotency_and_contract(self):
        self.store.register(ticket())
        self.store.triage({"ticket_id": "T-1", "attempt_id": 0, "route": "AUTO", "reason": "AUTO_ALLOWED", "category": "order_status", "confidence": 0.95})
        data = {"ticket_id": "T-1", "attempt_id": 0, "draft_reply": "Draft only", "reason": "AUTO_ALLOWED"}
        first = self.store.write("crm", data)
        second = self.store.write("crm", data)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.list_records("crm")), 1)
        self.assertEqual(len(self.store.list_records("review")), 0)
        self.assertEqual(self.store.get("T-1")["record_id"], "CASE-T-1")

    def test_review_idempotency(self):
        self.store.register(ticket())
        self.store.triage({"ticket_id": "T-1", "attempt_id": 0, "route": "REVIEW", "reason": "HIGH_RISK_CATEGORY", "category": "refund", "confidence": 0.95})
        data = {"ticket_id": "T-1", "attempt_id": 0, "draft_reply": "Draft only", "reason": "HIGH_RISK_CATEGORY"}
        self.store.write("review", data)
        self.store.write("review", data)
        self.assertEqual(len(self.store.list_records("review")), 1)
        with self.assertRaisesRegex(ValueError, "INVALID_STATE"):
            self.store.write("crm", data)

    def test_failure_visible_and_retry(self):
        self.store.register(ticket())
        self.store.triage({"ticket_id": "T-1", "attempt_id": 0, "route": "AUTO", "reason": "AUTO_ALLOWED", "category": "order_status", "confidence": 0.95})
        self.store.write_failed("T-1", 0)
        self.assertEqual(self.store.get("T-1")["status"], "WRITE_FAILED")
        self.assertEqual(self.store.register(ticket())["disposition"], "retry")
        self.assertEqual(self.store.get("T-1")["attempts"], 1)

    def test_concurrent_identity_and_write_reconciliation(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            dispositions = list(pool.map(self.store.register, [ticket()] * 16))
        self.assertEqual([x["disposition"] for x in dispositions].count("new"), 1)
        self.assertEqual([x["disposition"] for x in dispositions].count("duplicate"), 15)
        self.store.triage({"ticket_id": "T-1", "attempt_id": 0, "route": "AUTO", "reason": "AUTO_ALLOWED", "category": "order_status", "confidence": 0.95})
        failed = self.store.write_failed("T-1", 0)
        self.assertEqual(failed, {"ticket_id":"T-1","route":"AUTO","status":"WRITE_FAILED","record_id":None,"attempt_id":0})
        self.assertEqual(self.store.register(ticket())["disposition"], "retry")
        self.store.triage({"ticket_id": "T-1", "attempt_id": 1, "route": "AUTO", "reason": "AUTO_ALLOWED", "category": "order_status", "confidence": 0.95})
        data = {"ticket_id": "T-1", "attempt_id": 1, "draft_reply": "Draft only", "reason": "AUTO_ALLOWED"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            writes = list(pool.map(lambda _: self.store.write("crm", data), range(16)))
        self.assertEqual(len(self.store.list_records("crm")), 1)
        self.assertTrue(all(w == writes[0] for w in writes))
        self.assertEqual(self.store.write_failed("T-1", 1)["status"], "AUTO_ROUTED")
        self.assertEqual(self.store.get("T-1")["status"], "AUTO_ROUTED")

    def test_fake_modes(self):
        for mode, expected in [("valid_high_confidence", 200), ("valid_low_confidence", 200),
                               ("malformed_json", 200), ("invalid_enum", 200),
                               ("invalid_confidence", 200), ("http_429", 429), ("http_500", 500)]:
            t = ticket(subject=f"[FAKE:{mode}] Refund")
            status, body = fake_llm({"messages": [{"content": json.dumps(t)}]})
            self.assertEqual(status, expected, mode)
            self.assertLess(len(json.dumps(body)), 2000)


if __name__ == "__main__":
    unittest.main()
