const body = $input.first().json.body;
const keys = ['ticket_id', 'customer_email', 'subject', 'body', 'source'];
const normalized = body && keys.every(k => typeof body[k] === 'string')
  ? Object.fromEntries(keys.map(k => [k, k === 'customer_email'
    ? body[k].trim().replace(/[A-Z]/g, c => c.toLowerCase()) : body[k].trim()])) : null;
const valid = body && typeof body === 'object' && !Array.isArray(body)
  && Object.keys(body).sort().join(',') === keys.sort().join(',')
  && keys.every(k => typeof body[k] === 'string')
  && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(body.ticket_id.trim())
  && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.customer_email.trim())
  && normalized.customer_email.length <= 254
  && body.subject.trim().length >= 1 && body.subject.trim().length <= 200
  && body.body.trim().length >= 1 && body.body.trim().length <= 5000
  && ['form', 'email', 'helpdesk'].includes(body.source.trim());
return [{json: valid ? {valid: true, ticket: normalized} : {valid: false}}];
