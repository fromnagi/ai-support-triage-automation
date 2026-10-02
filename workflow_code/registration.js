const response = $input.first().json;
const result = response.body;
const inbound = $('Validate Input').first().json.ticket;
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === keys.slice().sort().join(',');
const sameTicket = exact(result?.ticket, Object.keys(inbound))
  && Object.keys(inbound).every(k => result.ticket[k] === inbound[k]);
if (response.statusCode !== 200 || !result || !['new', 'retry', 'duplicate', 'conflict'].includes(result.disposition)
  || !exact(result, ['disposition','ticket','ticket_id','status','attempt_id','record_id'])
  || !Number.isSafeInteger(result.attempt_id) || result.attempt_id < 0
  || result.ticket_id !== inbound.ticket_id || !sameTicket
  || !['RECEIVED','AI_CLASSIFIED','AI_FAILED','AUTO_ROUTED','HUMAN_REVIEW','WRITE_FAILED'].includes(result.status)
  || (['new','retry'].includes(result.disposition) && result.status !== 'RECEIVED')
  || (['AUTO_ROUTED','HUMAN_REVIEW'].includes(result.status)
    ? result.record_id !== (result.status === 'AUTO_ROUTED' ? 'CASE-' : 'REVIEW-') + result.ticket_id
    : result.record_id !== null))
  throw new Error('REGISTER_CONTRACT_FAILED');
return [{json: {...result, needs_attention: !['AUTO_ROUTED','HUMAN_REVIEW'].includes(result.status),
  process: result.disposition === 'new' || result.disposition === 'retry'}}];
