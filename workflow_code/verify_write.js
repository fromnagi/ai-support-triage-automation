const response = $input.first().json;
const expected = $('Check Triage').first().json;
const body = response.body ?? response;
if (response.statusCode === 200 && body && typeof body === 'object' && !Array.isArray(body)
  && Object.keys(body).sort().join(',') === 'attempt_id,record_id,route,status,ticket_id'
  && body.attempt_id === expected.attempt_id
  && body.ticket_id === expected.ticket_id && body.route === expected.route
  && body.record_id === (expected.route === 'AUTO' ? 'CASE-' : 'REVIEW-') + expected.ticket_id
  && body.status === (expected.route === 'AUTO' ? 'AUTO_ROUTED' : 'HUMAN_REVIEW')) {
  return [{json:{ticket_id:expected.ticket_id,attempt_id:expected.attempt_id,status:body.status,route:expected.route,reason:expected.reason,
    category:expected.category,confidence:expected.confidence,record_id:body.record_id}}];
}
return [{json:{ticket_id:expected.ticket_id,attempt_id:expected.attempt_id,status:'WRITE_FAILED',route:expected.route,reason:'BUSINESS_WRITE_FAILED'}}];
