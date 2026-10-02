// Direct local-model diagnostic. This does not exercise the n8n webhook.
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const ticket = {ticket_id:'MODEL-SAMPLE',customer_email:'person@example.test',subject:'Order status',
  body:'Could you tell me whether order 123 has shipped?',source:'form'};
const model = process.env.LLM_MODEL || 'qwen3:8b';
const build = new Function('$input','$',fs.readFileSync(path.join(root,'workflow_code','llm_request.js'),'utf8'));
const request = build({first:()=>({json:{ticket}})},name=>({first:()=>({json:{config:{llm_model:model}}})}))[0].json;
const endpoint = process.env.LLM_URL || 'http://127.0.0.1:11434/api/chat';
async function main() {
  const result = await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(request.llm_request),signal:AbortSignal.timeout(120000)});
  const body = await result.json();
  const policy = new Function('$input','$',fs.readFileSync(path.join(root,'workflow_code','policy.js'),'utf8'));
  const routed = policy({first:()=>({json:{statusCode:result.status,body}})},()=>({first:()=>({json:request})}))[0].json;
  console.log(JSON.stringify({model,http_status:result.status,input_scenario:'order_status',
    raw_content:body.message?.content?.slice(0,1500) ?? null,validated_result:routed},null,2));
}
main().catch(error => {console.error(error.message);process.exitCode=1;});
