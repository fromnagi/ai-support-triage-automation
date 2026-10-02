"""Build the tracked n8n export from short, reviewable node code."""
import json
import argparse
import re
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def code(name, file, x, y):
    return {"name": name, "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [x, y], "parameters": {"jsCode": (ROOT / "workflow_code" / file).read_text(encoding="utf-8")}}


def http(name, path, x, y, body, *, timeout=5000, never_error=False):
    options = {"timeout": timeout}
    if never_error:
        options["response"] = {"response": {"fullResponse": True, "neverError": True}}
    return {"name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
            **({"onError": "continueRegularOutput"} if never_error else {}),
            "position": [x, y], "parameters": {"method": "POST", "url": "={{ $('Demo Config').first().json.config.demo_base_url + '" + path + "' }}",
            "sendBody": True, "specifyBody": "json", "jsonBody": body, "options": options}}


def switch(name, field, value, x, y):
    return {"name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2,
            "position": [x, y], "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "combinator": "and", "conditions": [{"id": name, "leftValue": "={{ $json." + field + " }}", "rightValue": value, "operator": {"type": "string" if isinstance(value, str) else "boolean", "operation": "equals"}}]}}}


def respond(name, expression, x, y, status=200):
    return {"name": name, "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1,
            "position": [x, y], "parameters": {"respondWith": "json", "responseBody": expression,
            "options": {"responseCode": status}}}


parser = argparse.ArgumentParser()
parser.add_argument("--demo-base-url", default="http://127.0.0.1:8765")
parser.add_argument("--llm-url", default="http://127.0.0.1:8765/fake-llm/classify")
parser.add_argument("--llm-model", default="qwen3:8b")
parser.add_argument("--llm-timeout-ms", type=int, default=60000)
parser.add_argument("--llm-num-gpu", type=int, default=None, help="Optional Ollama GPU layer setting; 0 forces CPU for a local observation")
parser.add_argument("--output", default="workflows/support-triage.json")
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
if not 100 <= args.llm_timeout_ms <= 120000:
    parser.error("LLM timeout must be between 100 and 120000 ms")
if args.llm_num_gpu is not None and not 0 <= args.llm_num_gpu <= 100:
    parser.error("LLM GPU layer setting must be between 0 and 100")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", args.llm_model):
    parser.error("Invalid local model name")
for value in (args.demo_base_url, args.llm_url):
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        parser.error("Invalid localhost port")
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or not port or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)):
        parser.error("Only credential-free localhost HTTP endpoints are allowed")
if urlparse(args.demo_base_url).path not in {"", "/"}:
    parser.error("Demo base URL must have no path")
if urlparse(args.llm_url).path not in {"/fake-llm/classify", "/api/chat"}:
    parser.error("LLM URL must target the local fake classifier or Ollama chat API")
config = {"demo_base_url": args.demo_base_url.rstrip("/"), "llm_url": args.llm_url,
          "llm_model": args.llm_model, "llm_num_gpu": args.llm_num_gpu}

nodes = [
    {"name": "Support Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
     "position": [0, 0], "webhookId": "support-triage", "parameters": {"httpMethod": "POST", "path": "support-triage", "responseMode": "responseNode", "options": {}}},
    {"name": "Demo Config", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [110, 0], "parameters": {"jsCode": "return [{json:{...$input.first().json,config:" + json.dumps(config) + "}}];"}},
    code("Validate Input", "validate.js", 220, 0),
    switch("Input Valid?", "valid", True, 440, 0),
    respond("Invalid Input", '={{ { error: "INVALID_INPUT" } }}', 660, 180, 400),
    http("Register Ticket", "/state/tickets", 660, -100, '={{ JSON.stringify($json.ticket) }}', never_error=True),
    code("Check Registration", "registration.js", 880, -100),
    switch("Process Ticket?", "process", True, 1100, -100),
    respond("Duplicate Or Conflict", '={{ { ticket_id: $json.ticket_id, disposition: $json.disposition, status:$json.status, attempt_id:$json.attempt_id, record_id:$json.record_id, needs_attention:$json.needs_attention } }}', 1320, 100,
            '={{ $json.disposition === "conflict" ? 409 : ($json.needs_attention ? 202 : 200) }}'),
    code("Build LLM Request", "llm_request.js", 1320, -200),
    {"name": "Call LLM", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
     "position": [1540, -200], "onError": "continueRegularOutput",
     "parameters": {"method": "POST", "url": "={{ $('Demo Config').first().json.config.llm_url }}", "sendBody": True, "specifyBody": "json",
                    "jsonBody": '={{ JSON.stringify($json.llm_request) }}', "options": {"timeout": args.llm_timeout_ms, "response": {"response": {"fullResponse": True, "neverError": True}}}}},
    code("Validate AI And Route", "policy.js", 1760, -200),
    http("Record Triage", "/state/triage", 1980, -200, '={{ JSON.stringify({ticket_id:$json.ticket_id,attempt_id:$json.attempt_id,route:$json.route,reason:$json.reason,category:$json.category,confidence:$json.confidence}) }}', never_error=True),
    code("Check Triage", "triage.js", 2200, -200),
    {"name": "Write Business Record", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
     "position": [2420, -200], "onError": "continueRegularOutput",
     "parameters": {"method": "POST", "url": "={{ $('Demo Config').first().json.config.demo_base_url + ($json.route === 'AUTO' ? '/crm/cases' : '/reviews') }}",
                    "sendBody": True, "specifyBody": "json",
                    "jsonBody": '={{ JSON.stringify({ticket_id:$json.ticket_id,attempt_id:$json.attempt_id,draft_reply:$json.draft_reply,reason:$json.reason}) }}',
                    "options": {"timeout": 5000, "response": {"response": {"fullResponse": True, "neverError": True}}}}},
    code("Verify Business Write", "verify_write.js", 2640, -200),
    switch("Business Write Failed?", "status", "WRITE_FAILED", 2860, -200),
    http("Record Write Failure", "/state/write-failed", 3080, -100, '={{ JSON.stringify({ticket_id:$json.ticket_id,attempt_id:$json.attempt_id}) }}', never_error=True),
    code("Check Write Failure", "reconcile_write.js", 3300, -100),
    respond("Result", '={{ $json }}', 3520, -200,
            '={{ $json.status === "WRITE_UNRESOLVED" ? 503 : ($json.status === "WRITE_FAILED" ? 502 : 200) }}'),
    respond("Processing Error", '={{ { status:"PROCESSING_UNRESOLVED", reason:"WORKFLOW_ERROR", needs_attention:true } }}', 2200, 350, 503),
]

edges = [
    ("Support Webhook", "Demo Config", 0), ("Demo Config", "Validate Input", 0), ("Validate Input", "Input Valid?", 0),
    ("Input Valid?", "Register Ticket", 0), ("Input Valid?", "Invalid Input", 1),
    ("Register Ticket", "Check Registration", 0), ("Check Registration", "Process Ticket?", 0),
    ("Process Ticket?", "Build LLM Request", 0), ("Process Ticket?", "Duplicate Or Conflict", 1),
    ("Build LLM Request", "Call LLM", 0), ("Call LLM", "Validate AI And Route", 0),
    ("Validate AI And Route", "Record Triage", 0), ("Record Triage", "Check Triage", 0),
    ("Check Triage", "Write Business Record", 0), ("Write Business Record", "Verify Business Write", 0),
    ("Verify Business Write", "Business Write Failed?", 0),
    ("Business Write Failed?", "Record Write Failure", 0),
    ("Business Write Failed?", "Result", 1),
    ("Record Write Failure", "Check Write Failure", 0),
    ("Check Write Failure", "Result", 0),
]
connections = {}
for src, dst, branch in edges:
    outputs = connections.setdefault(src, {"main": []})["main"]
    while len(outputs) <= branch:
        outputs.append([])
    outputs[branch].append({"node": dst, "type": "main", "index": 0})

for node in nodes:
    node["id"] = str(uuid5(NAMESPACE_URL, "support-triage:" + node["name"]))
    if node['type'] == 'n8n-nodes-base.code':
        node['onError'] = 'continueErrorOutput'
        connections[node['name']]['main'].append([{'node':'Processing Error','type':'main','index':0}])

workflow = {"id": "SupportTriageDemo", "name": "Support Triage Demo", "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1"}, "active": False, "pinData": {}, "tags": []}
out = ROOT / args.output
out.parent.mkdir(exist_ok=True)
rendered = json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"
if args.check:
    if not out.exists() or out.read_bytes() != rendered.encode("utf-8"):
        raise SystemExit("Workflow export differs from builder output")
else:
    out.write_bytes(rendered.encode("utf-8"))
