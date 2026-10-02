const response = $input.first().json;
const context = $('Build LLM Request').first().json;
let reason = 'POLICY_REVIEW', parsed = null;
const status = response.statusCode;
if (response.error || !status) reason = /timed?\s*out|timeout/i.test(JSON.stringify(response)) ? 'AI_TIMEOUT' : 'AI_SERVICE_ERROR';
else if (status === 429) reason = 'AI_RATE_LIMIT';
else if (status >= 500 || status < 200 || status >= 300) reason = 'AI_SERVICE_ERROR';
else {
  try {
    const content = response.body?.message?.content;
    parsed = JSON.parse(content);
    const keys = ['category','priority','summary','suggested_action','draft_reply','confidence','requires_human_review'];
    const exact = parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      && Object.keys(parsed).sort().join(',') === keys.sort().join(',');
    const bounded = (k,max) => typeof parsed[k] === 'string' && parsed[k].trim().length > 0 && parsed[k].length <= max;
    const valid = exact && ['order_status','billing','refund','technical','account_access','other'].includes(parsed.category)
      && ['low','normal','high'].includes(parsed.priority)
      && bounded('summary',300) && bounded('suggested_action',300) && bounded('draft_reply',1200)
      && typeof parsed.confidence === 'number' && Number.isFinite(parsed.confidence) && parsed.confidence >= 0 && parsed.confidence <= 1
      && typeof parsed.requires_human_review === 'boolean';
    if (!valid) { parsed = null; reason = 'AI_INVALID_OUTPUT'; }
    else if (['refund','account_access'].includes(parsed.category)) reason = 'HIGH_RISK_CATEGORY';
    else if (!['order_status','technical'].includes(parsed.category)) reason = 'CATEGORY_REVIEW';
    else if (parsed.confidence < 0.85) reason = 'LOW_CONFIDENCE';
    else if (parsed.requires_human_review) reason = 'MODEL_REQUESTED_REVIEW';
    else reason = 'AUTO_ALLOWED';
  } catch { parsed = null; reason = 'AI_INVALID_OUTPUT'; }
}
const route = reason === 'AUTO_ALLOWED' ? 'AUTO' : 'REVIEW';
return [{json:{ticket_id:context.ticket_id,attempt_id:context.attempt_id,route,reason,category:parsed?.category ?? null,
  confidence:parsed?.confidence ?? null,draft_reply:parsed?.draft_reply ?? ''}}];
