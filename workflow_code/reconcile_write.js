const response = $input.first().json;
const expected = $('Verify Business Write').first().json;
const body = response.body ?? response;
const finalStatus = expected.route === 'AUTO' ? 'AUTO_ROUTED' : 'HUMAN_REVIEW';
const recordId = (expected.route === 'AUTO' ? 'CASE-' : 'REVIEW-') + expected.ticket_id;
if (response.statusCode === 200 && body && typeof body === 'object' && !Array.isArray(body)
  && Object.keys(body).sort().join(',') === 'attempt_id,record_id,route,status,ticket_id'
  && body.attempt_id === expected.attempt_id
  && body.ticket_id === expected.ticket_id && body.route === expected.route) {
  if (body.status === 'WRITE_FAILED' && body.record_id === null)
    return [{json:expected}];
  if (body.status === finalStatus && body.record_id === recordId)
    return [{json:{ticket_id:expected.ticket_id,attempt_id:expected.attempt_id,status:finalStatus,route:expected.route,
      reason:'WRITE_CONFIRMED_BY_STATE',record_id:recordId}}];
}
return [{json:{ticket_id:expected.ticket_id,attempt_id:expected.attempt_id,status:'WRITE_UNRESOLVED',route:expected.route,
  reason:'STATE_CONFIRMATION_FAILED'}}];
