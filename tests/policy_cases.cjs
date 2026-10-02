const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '..', 'workflow_code', 'policy.js'), 'utf8');
const ticket = {ticket_id:'T-1', customer_email:'person@example.test',subject:'Order status',body:'Where is my order?',source:'form'};
const context = {ticket_id:'T-1',attempt_id:0,ticket};
const base = {category:'order_status',priority:'normal',summary:'Order inquiry',suggested_action:'Check status',draft_reply:'We will check.',confidence:0.95,requires_human_review:false};
function run(response) {
  const fn = new Function('$input','$',code);
  return fn({first:()=>({json:response})}, name=>({first:()=>({json:context})}))[0].json;
}
function model(overrides={}) {return {statusCode:200,body:{message:{content:JSON.stringify({...base,...overrides})}}};}
const cases = [
  ['safe auto',model(),'AUTO','AUTO_ALLOWED'],
  ['refund',model({category:'refund'}),'REVIEW','HIGH_RISK_CATEGORY'],
  ['account',model({category:'account_access'}),'REVIEW','HIGH_RISK_CATEGORY'],
  ['low confidence',model({confidence:0.84}),'REVIEW','LOW_CONFIDENCE'],
  ['model review',model({requires_human_review:true}),'REVIEW','MODEL_REQUESTED_REVIEW'],
  ['billing',model({category:'billing'}),'REVIEW','CATEGORY_REVIEW'],
  ['malformed JSON',{statusCode:200,body:{message:{content:'{broken'}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['unsupported enum',model({category:'unsafe'}),'REVIEW','AI_INVALID_OUTPUT'],
  ['invalid confidence',model({confidence:1.5}),'REVIEW','AI_INVALID_OUTPUT'],
  ['missing field',{statusCode:200,body:{message:{content:JSON.stringify({category:'order_status'})}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['extra field',model({endpoint:'http://bad.example.test'}),'REVIEW','AI_INVALID_OUTPUT'],
  ['wrong priority',model({priority:'urgent'}),'REVIEW','AI_INVALID_OUTPUT'],
  ['negative confidence',model({confidence:-0.1}),'REVIEW','AI_INVALID_OUTPUT'],
  ['string confidence',model({confidence:'0.95'}),'REVIEW','AI_INVALID_OUTPUT'],
  ['review string',model({requires_human_review:'false'}),'REVIEW','AI_INVALID_OUTPUT'],
  ['empty summary',model({summary:''}),'REVIEW','AI_INVALID_OUTPUT'],
  ['oversized summary',model({summary:'x'.repeat(301)}),'REVIEW','AI_INVALID_OUTPUT'],
  ['oversized draft',model({draft_reply:'x'.repeat(1201)}),'REVIEW','AI_INVALID_OUTPUT'],
  ['nested extra',model({command:{run:'unsafe'}}),'REVIEW','AI_INVALID_OUTPUT'],
  ['array JSON',{statusCode:200,body:{message:{content:'[]'}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['markdown JSON',{statusCode:200,body:{message:{content:'```json\n'+JSON.stringify(base)+'\n```'}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['prose before JSON',{statusCode:200,body:{message:{content:'Here is JSON: '+JSON.stringify(base)}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['NaN in runtime',{statusCode:200,body:{message:{content:JSON.stringify(base).replace('0.95','NaN')}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['Infinity in runtime',{statusCode:200,body:{message:{content:JSON.stringify(base).replace('0.95','Infinity')}}},'REVIEW','AI_INVALID_OUTPUT'],
  ['connection refused',{error:'connect ECONNREFUSED 127.0.0.1'},'REVIEW','AI_SERVICE_ERROR'],
  ['timeout',{error:'Request timed out'},'REVIEW','AI_TIMEOUT'],
  ['rate limit',{statusCode:429,body:{error:'rate limited'}},'REVIEW','AI_RATE_LIMIT'],
  ['service error',{statusCode:500,body:{error:'fail'}},'REVIEW','AI_SERVICE_ERROR'],
];
for (const [name,input,route,reason] of cases) {
  const got = run(input);
  assert.equal(got.route,route,name);
  assert.equal(got.reason,reason,name);
  assert.equal(got.ticket_id,'T-1',name);
  assert.ok(!JSON.stringify(got).includes('bad.example.test'),name);
}
console.log(`${cases.length} policy cases passed`);

const validateCode = fs.readFileSync(path.join(__dirname, '..', 'workflow_code', 'validate.js'), 'utf8');
const validate = body => new Function('$input',validateCode)({first:()=>({json:{body}})})[0].json;
assert.equal(validate(ticket).valid,true);
assert.equal(validate({...ticket,customer_email:'bad'}).valid,false);
assert.equal(validate({...ticket,extra:'unexpected'}).valid,false);

const verifyCode = fs.readFileSync(path.join(__dirname, '..', 'workflow_code', 'verify_write.js'), 'utf8');
const expected = {attempt_id:0,ticket_id:'T-1',route:'AUTO',reason:'AUTO_ALLOWED',category:'order_status',confidence:0.95};
const verify = response => new Function('$input','$',verifyCode)({first:()=>({json:response})},()=>({first:()=>({json:expected})}))[0].json;
assert.equal(verify({statusCode:200,body:{attempt_id:0,ticket_id:'T-1',route:'AUTO',record_id:'CASE-T-1',status:'AUTO_ROUTED'}}).status,'AUTO_ROUTED');
assert.equal(verify({statusCode:200,body:{attempt_id:0,ticket_id:'other',route:'AUTO',record_id:'CASE-T-1',status:'AUTO_ROUTED'}}).status,'WRITE_FAILED');
assert.equal(verify({statusCode:503,body:{error:'BUSINESS_WRITE_FAILED'}}).status,'WRITE_FAILED');
for (const body of [
  {attempt_id:0,ticket_id:'T-1',route:'AUTO',record_id:'WRONG',status:'AUTO_ROUTED'},
  {attempt_id:0,ticket_id:'T-1',route:'REVIEW',record_id:'CASE-T-1',status:'AUTO_ROUTED'},
  {attempt_id:0,ticket_id:'T-1',route:'AUTO',record_id:'CASE-T-1',status:'HUMAN_REVIEW'},
  {attempt_id:0,ticket_id:'T-1',route:'AUTO',record_id:'CASE-T-1'},
  {attempt_id:0,ticket_id:'T-1',route:'AUTO',record_id:'CASE-T-1',status:'AUTO_ROUTED',extra:'misleading'},
  '{broken'
]) assert.equal(verify({statusCode:200,body}).status,'WRITE_FAILED');
for (const statusCode of [201,202,204,409,429,500])
  assert.equal(verify({statusCode,body:{attempt_id:0,ticket_id:'T-1',route:'AUTO',record_id:'CASE-T-1',status:'AUTO_ROUTED'}}).status,'WRITE_FAILED');
console.log('Input and business write contract checks passed');

const registrationCode = fs.readFileSync(path.join(__dirname, '..', 'workflow_code', 'registration.js'), 'utf8');
const registered = result => {
  const fn = new Function('$input','$',registrationCode);
  const previous = () => ({first:()=>({json:{ticket}})});
  return fn({first:()=>({json:{statusCode:200,body:result}})},previous)[0].json;
};
const reg = (disposition, status='RECEIVED') => ({disposition,ticket_id:'T-1',ticket,status,attempt_id:0,record_id:null});
assert.equal(registered(reg('new')).process,true);
assert.equal(registered(reg('duplicate')).process,false);
assert.equal(registered(reg('duplicate')).needs_attention,true);
assert.equal(registered({...reg('duplicate','AUTO_ROUTED'),record_id:'CASE-T-1'}).needs_attention,false);
for (const bad of [
  {...reg('new'),ticket_id:'other'},
  {...reg('new'),status:'AI_CLASSIFIED'},
  {...reg('new'),ticket:{...ticket,body:'changed'}},
  {...reg('new'),ticket:{...ticket,extra:'x'}},
  {...reg('new'),extra:'misleading'},
  {...reg('unknown')},
  {...reg('new'),attempt_id:-1},
  {...reg('new'),attempt_id:'0'},
  {...reg('new'),record_id:'CASE-T-1'},
  {...reg('duplicate','AUTO_ROUTED'),record_id:null},
  (({status,...rest})=>rest)(reg('new')),
]) assert.throws(()=>registered(bad),/REGISTER_CONTRACT_FAILED/);
console.log('Registration contract checks passed');

const triageCode = fs.readFileSync(path.join(__dirname, '..', 'workflow_code', 'triage.js'), 'utf8');
const triageExpected = {attempt_id:0,ticket_id:'T-1',route:'AUTO',reason:'AUTO_ALLOWED'};
const triage = response => new Function('$input','$',triageCode)({first:()=>({json:{statusCode:200,body:response}})},()=>({first:()=>({json:triageExpected})}))[0].json;
assert.equal(triage({attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'AI_CLASSIFIED'}).route,'AUTO');
for (const bad of [
  {attempt_id:0,ticket_id:'wrong',route:'AUTO',status:'AI_CLASSIFIED'},
  {attempt_id:0,ticket_id:'T-1',route:'REVIEW',status:'AI_CLASSIFIED'},
  {attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'AI_FAILED'},
  {attempt_id:0,ticket_id:'T-1',route:'AUTO'},
  {attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'AI_CLASSIFIED',extra:'x'},
  {attempt_id:1,ticket_id:'T-1',route:'AUTO',status:'AI_CLASSIFIED'},
]) assert.throws(()=>triage(bad),/TRIAGE_CONTRACT_FAILED/);
console.log('Triage contract checks passed');

const reconcileCode = fs.readFileSync(path.join(__dirname, '..', 'workflow_code', 'reconcile_write.js'), 'utf8');
const failedWrite = {attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'WRITE_FAILED',reason:'BUSINESS_WRITE_FAILED'};
const reconcile = response => new Function('$input','$',reconcileCode)({first:()=>({json:response})},()=>({first:()=>({json:failedWrite})}))[0].json;
assert.equal(reconcile({statusCode:200,body:{attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'WRITE_FAILED',record_id:null}}).status,'WRITE_FAILED');
assert.equal(reconcile({statusCode:200,body:{attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'AUTO_ROUTED',record_id:'CASE-T-1'}}).status,'AUTO_ROUTED');
for (const response of [
  {error:'connect ECONNREFUSED'},
  {statusCode:500,body:{error:'failure'}},
  {statusCode:200,body:{attempt_id:0,ticket_id:'other',route:'AUTO',status:'WRITE_FAILED',record_id:null}},
  {statusCode:200,body:{attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'AUTO_ROUTED',record_id:'WRONG'}},
  {statusCode:200,body:{attempt_id:0,ticket_id:'T-1',route:'AUTO',status:'WRITE_FAILED',record_id:null,extra:'x'}},
]) assert.equal(reconcile(response).status,'WRITE_UNRESOLVED');
console.log('Write reconciliation contract checks passed');

const stale = {attempt_id:1,ticket_id:'T-1',route:'AUTO',status:'AUTO_ROUTED',record_id:'CASE-T-1'};
assert.equal(verify({statusCode:200,body:stale}).status,'WRITE_FAILED');
assert.equal(reconcile({statusCode:200,body:stale}).status,'WRITE_UNRESOLVED');
for (const helper of [verify, reconcile]) {
  const body = {...stale,attempt_id:0};
  delete body.attempt_id;
  assert.ok(['WRITE_FAILED','WRITE_UNRESOLVED'].includes(helper({statusCode:200,body}).status));
}
// Deliberately wrong but well-formed model label: documents option A's limit.
context.ticket = {...ticket,subject:'Refund request',body:'Please refund my payment.'};
assert.equal(run(model()).route,'AUTO');
console.log('Attempt response fencing and misclassification limit checks passed');
