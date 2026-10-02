import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_builder_rejects_configuration_injection(self):
        cases = [
            ["--demo-base-url", "http://user:pass@127.0.0.1:8765"],
            ["--demo-base-url", "http://127.0.0.1:8765/evil"],
            ["--demo-base-url", "http://127.0.0.1:8765?endpoint=evil"],
            ["--llm-url", "http://evil.example.test/api/chat"],
            ["--llm-url", "http://127.0.0.1:11434/other"],
            ["--llm-url", "http://127.0.0.1:99999/api/chat"],
            ["--llm-model", "qwen3:8b;rm"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            for args in cases:
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_workflow.py"),
                                             "--output", str(Path(directory) / "workflow.json"), *args],
                                            capture_output=True, text=True, cwd=ROOT)
                    self.assertNotEqual(result.returncode, 0)

    def test_export_contract(self):
        workflow = json.loads((ROOT / "workflows/support-triage.json").read_text(encoding="utf-8"))
        nodes = {node["name"]: node for node in workflow["nodes"]}
        self.assertEqual(nodes["Support Webhook"]["parameters"]["path"], "support-triage")
        self.assertEqual(nodes["Call LLM"]["type"], "n8n-nodes-base.httpRequest")
        self.assertEqual(nodes["Write Business Record"]["type"], "n8n-nodes-base.httpRequest")
        self.assertIn("Validate AI And Route", nodes)
        self.assertFalse(any("credentials" in node for node in workflow["nodes"]))
        text = json.dumps(workflow)
        self.assertNotIn("api_key", text.lower())
        self.assertNotIn("password", text.lower())
        self.assertIn("Business Write Failed?", nodes)
        self.assertIn("Record Write Failure", nodes)
        self.assertEqual(nodes['Processing Error']['parameters']['options']['responseCode'], 503)
        for name, node in nodes.items():
            if node['type'] == 'n8n-nodes-base.code':
                self.assertEqual(node['onError'], 'continueErrorOutput')
                self.assertEqual(workflow['connections'][name]['main'][1][0]['node'], 'Processing Error')
        self.assertNotIn("num_gpu:0", nodes["Build LLM Request"]["parameters"]["jsCode"])
        for name, filename in [("Validate Input", "validate.js"), ("Build LLM Request", "llm_request.js"),
                               ("Validate AI And Route", "policy.js"), ("Check Registration", "registration.js"),
                               ("Check Triage", "triage.js"), ("Verify Business Write", "verify_write.js"),
                               ("Check Write Failure", "reconcile_write.js")]:
            self.assertEqual(nodes[name]["parameters"]["jsCode"], (ROOT / "workflow_code" / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
