const response = $input.first().json;
const result = response.body;
const expected = $('Validate AI And Route').first().json;
const status = expected.reason.startsWith('AI_') ? 'AI_FAILED' : 'AI_CLASSIFIED';
if (response.statusCode !== 200 || !result || typeof result !== 'object' || Array.isArray(result)
  || Object.keys(result).sort().join(',') !== 'attempt_id,route,status,ticket_id'
  || result.attempt_id !== expected.attempt_id
  || result.ticket_id !== expected.ticket_id || result.route !== expected.route
  || result.status !== status) throw new Error('TRIAGE_CONTRACT_FAILED');
return [{json:expected}];
