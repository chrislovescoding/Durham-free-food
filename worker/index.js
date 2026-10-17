// Sites Worker ESM entry point. scripts/build-worker.mjs embeds the public assets.
const ASSETS = /* GRUBR_ASSETS */ {};
const MAX_BODY_BYTES = 8192;
const MAX_CHECKPOINT_BYTES = 128 * 1024;
const LEASE_MS = 30 * 60 * 1000;
const DAILY_NEW_JOB_LIMIT = 5;
const TERMINAL_STATUSES = new Set(['published', 'rejected', 'needs_review', 'failed']);
const JSON_HEADERS = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };

function json(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), { status, headers: { ...JSON_HEADERS, ...headers } });
}

function sameOrigin(request) {
  const origin = request.headers.get('Origin');
  if (request.headers.get('Sec-Fetch-Site') === 'cross-site') return false;
  return origin === new URL(request.url).origin;
}

async function boundedJson(request, limit = MAX_BODY_BYTES) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') || '')) {
    throw Object.assign(new Error('Please send the suggestion as JSON.'), { status: 415 });
  }
  if (Number(request.headers.get('Content-Length')) > limit) {
    throw Object.assign(new Error('This suggestion is too long.'), { status: 413 });
  }
  const reader = request.body?.getReader();
  if (!reader) throw Object.assign(new Error('Please fill in the event details.'), { status: 400 });
  const chunks = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) {
      await reader.cancel();
      throw Object.assign(new Error('This suggestion is too long.'), { status: 413 });
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw Object.assign(new Error('Please check the event details and try again.'), { status: 400 }); }
}

function validationError(message) { throw Object.assign(new Error(message), { status: 400 }); }

export function canonicalInstagramUrl(source) {
  let url;
  try { url = new URL(source); } catch { validationError('Add a link to an Instagram post or reel.'); }
  if (url.protocol !== 'https:' || !['instagram.com', 'www.instagram.com', 'm.instagram.com'].includes(url.hostname)
    || url.username || url.password || url.port || !/^\/(?:p|reel|tv)\/[a-zA-Z0-9_-]{1,64}\/?$/.test(url.pathname)) {
    validationError('Use a public Instagram post or reel link, starting with https://www.instagram.com/.');
  }
  return `https://www.instagram.com/p/${url.pathname.split('/')[2]}/`;
}

export function validateSuggestion(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) validationError('Please fill in the event details.');
  if (input.website !== undefined && input.website !== '') validationError('Please leave the website field empty.');
  const title = typeof input.title === 'string' ? input.title.trim() : '';
  const details = typeof input.details === 'string' ? input.details.trim() : '';
  const source = typeof input.source_url === 'string' ? input.source_url.trim() : '';
  const key = input.idempotency_key;
  if (title.length < 3 || title.length > 160) validationError('Use an event name between 3 and 160 characters.');
  if (details.length > 3000 || (input.details !== undefined && typeof input.details !== 'string')) validationError('Keep the extra details under 3,000 characters.');
  if (source.length > 2048) validationError('The event link is too long.');
  const sourceUrl = canonicalInstagramUrl(source);
  if (typeof key !== 'string' || !/^[a-zA-Z0-9_-]{16,80}$/.test(key)) validationError('Please refresh the page and try again.');
  return { title, source_url: sourceUrl, details, idempotency_key: key };
}

async function digest(value) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}

async function rateLimit(request, db, now) {
  // A random, daily database salt prevents recovering addresses from stored hashes.
  // Raw addresses are used only in memory and are never logged or persisted.
  const day = Math.floor(now / 86400000);
  await db.batch([
    db.prepare('INSERT INTO suggestion_salts (day, salt, expires_at) VALUES (?, ?, ?) ON CONFLICT(day) DO NOTHING').bind(day, crypto.randomUUID(), (day + 2) * 86400000),
    db.prepare('DELETE FROM suggestion_salts WHERE expires_at < ?').bind(now),
    db.prepare('DELETE FROM suggestion_rate_limits WHERE expires_at < ?').bind(now),
  ]);
  const record = await db.prepare('SELECT salt FROM suggestion_salts WHERE day = ?').bind(day).first();
  const address = request.headers.get('CF-Connecting-IP');
  const bucket = await digest(`${record.salt}:${Math.floor(now / 3600000)}:${address || 'unknown'}`);
  const row = await db.prepare('INSERT INTO suggestion_rate_limits (bucket, count, expires_at) VALUES (?, 1, ?) ON CONFLICT(bucket) DO UPDATE SET count = count + 1 RETURNING count')
    .bind(bucket, now + 7200000).first();
  // Hosts that omit the address header share a generous fallback bucket.
  return row.count <= (address ? 5 : 50);
}

function knownEvents(sourceUrl) {
  try {
    const events = JSON.parse(ASSETS['/data/events.json']?.body || '{}').events || [];
    return events.filter(event => {
      try { return canonicalInstagramUrl(event.source_url) === sourceUrl; } catch { return false; }
    });
  } catch { return []; }
}

function publicStatus(row) {
  const events = JSON.parse(row.events_json || '[]');
  return { id: row.id, status: row.job_status || row.status, reason: row.job_reason || row.reason || null,
    event_ids: events.map(event => event.id) };
}

function suggestionStatement(db, column, value) {
  // Column names are constants owned by the caller, never request input.
  return db.prepare(`SELECT s.id, s.status, s.reason, s.payload_hash,
    j.status AS job_status, j.reason AS job_reason, j.events_json
    FROM event_suggestions s LEFT JOIN submission_jobs j ON j.id = s.job_id WHERE s.${column} = ?`).bind(value);
}

async function ensureJob(db, sourceUrl, now) {
  const events = knownEvents(sourceUrl);
  await db.prepare(`INSERT INTO submission_jobs
    (id, source_url, status, reason, events_json, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_url) DO NOTHING`)
    .bind(crypto.randomUUID(), sourceUrl, events.length ? 'published' : 'pending',
      events.length ? 'This event is already listed on Grubr.' : null, JSON.stringify(events), now, now).run();
  return db.prepare('SELECT id, status, reason FROM submission_jobs WHERE source_url = ?').bind(sourceUrl).first();
}

async function submitSuggestion(request, env) {
  if (!sameOrigin(request)) return json({ error: 'Please submit the form on Grubr.' }, 403);
  const input = validateSuggestion(await boundedJson(request));
  if (!env.DB) return json({ error: 'Suggestions are temporarily unavailable. Please try again shortly.' }, 503);
  const payloadHash = await digest(JSON.stringify([input.title, input.source_url, input.details]));
  const existing = await suggestionStatement(env.DB, 'idempotency_key', input.idempotency_key).first();
  if (existing) {
    if (existing.payload_hash !== payloadHash) return json({ error: 'This submission has changed. Please start a new suggestion.' }, 409);
    return json({ ...publicStatus(existing), duplicate: true }, 200);
  }
  const now = Date.now();
  // Every new receipt is rate limited; repeated source URLs still share one paid job.
  if (!await rateLimit(request, env.DB, now)) return json({ error: 'You have sent a few suggestions recently. Please try again in an hour.' }, 429, { 'Retry-After': '3600' });
  const job = await ensureJob(env.DB, input.source_url, new Date(now).toISOString());
  const id = crypto.randomUUID();
  await env.DB.prepare('INSERT INTO event_suggestions (id, idempotency_key, payload_hash, title, source_url, details, status, job_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(idempotency_key) DO NOTHING')
    .bind(id, input.idempotency_key, payloadHash, input.title, input.source_url, input.details, job.status, job.id, job.reason, new Date(now).toISOString()).run();
  // Read the durable row before confirming; concurrent retries return the same ID.
  const saved = await suggestionStatement(env.DB, 'idempotency_key', input.idempotency_key).first();
  if (!saved) throw new Error('Suggestion was not persisted');
  if (saved.payload_hash !== payloadHash) return json({ error: 'This submission has changed. Please start a new suggestion.' }, 409);
  return json({ ...publicStatus(saved), duplicate: saved.id !== id }, saved.id === id ? 201 : 200);
}

async function authorizeCollector(request, env) {
  const configured = typeof env.GRUBR_QUEUE_TOKEN === 'string' ? env.GRUBR_QUEUE_TOKEN : '';
  const supplied = request.headers.get('Authorization')?.match(/^Bearer ([^\s]+)$/)?.[1] || '';
  // Equal-length digests avoid directly comparing a secret prefix or length.
  const expected = await digest(configured), received = await digest(supplied);
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) mismatch |= expected.charCodeAt(i) ^ received.charCodeAt(i);
  if (configured.length < 32 || !supplied || mismatch) throw Object.assign(new Error('Collector authentication required.'), { status: 401 });
}

async function joinLegacySuggestions(db, now) {
  const rows = (await db.prepare("SELECT id, source_url FROM event_suggestions WHERE job_id IS NULL AND status = 'pending' ORDER BY created_at LIMIT 100").all()).results;
  for (const row of rows) {
    let sourceUrl;
    try { sourceUrl = canonicalInstagramUrl(row.source_url); } catch {
      await db.prepare("UPDATE event_suggestions SET status = 'needs_review', reason = ? WHERE id = ? AND job_id IS NULL")
        .bind('Automatic checks currently support public Instagram post and reel links. This link needs a person to check it.', row.id).run();
      continue;
    }
    const job = await ensureJob(db, sourceUrl, now);
    await db.prepare('UPDATE event_suggestions SET job_id = ?, source_url = ?, status = ?, reason = ? WHERE id = ? AND job_id IS NULL')
      .bind(job.id, sourceUrl, job.status, job.reason, row.id).run();
  }
}

async function claimJobs(request, env) {
  const input = await boundedJson(request);
  if (!input || typeof input !== 'object' || !Number.isInteger(input.limit) || input.limit < 1 || input.limit > 2) validationError('Claim limit must be one or two.');
  const now = Date.now(), stamp = new Date(now).toISOString(), day = Math.floor(now / 86400000);
  await joinLegacySuggestions(env.DB, stamp);
  const jobs = [];
  for (let i = 0; i < input.limit; i++) {
    // One atomic statement chooses and leases the job. Concurrent workers cannot
    // claim the same job or overshoot the five first-starts-per-day budget.
    const row = await env.DB.prepare(`UPDATE submission_jobs SET status = 'processing',
      lease_token = ?, lease_expires_at = ?, started_day = COALESCE(started_day, ?),
      attempts = attempts + 1, updated_at = ?
      WHERE id = (SELECT id FROM submission_jobs
        WHERE EXISTS (SELECT 1 FROM event_suggestions WHERE job_id = submission_jobs.id)
          AND ((status = 'processing' AND lease_expires_at <= ?)
           OR (status = 'pending' AND (started_day IS NOT NULL OR
              (SELECT COUNT(*) FROM submission_jobs WHERE started_day = ?) < ?)))
        ORDER BY CASE WHEN started_day IS NOT NULL THEN 0 ELSE 1 END, created_at, id LIMIT 1)
      RETURNING id, source_url, lease_token, checkpoint, attempts`)
      .bind(crypto.randomUUID(), now + LEASE_MS, day, stamp, now, day, DAILY_NEW_JOB_LIMIT).first();
    if (!row) break;
    jobs.push({ ...row, checkpoint: JSON.parse(row.checkpoint) });
  }
  return json({ jobs });
}

function leaseInput(input) {
  if (!input || typeof input !== 'object' || typeof input.job_id !== 'string' || typeof input.lease_token !== 'string'
    || input.job_id.length > 80 || input.lease_token.length > 80) validationError('A job and its current lease token are required.');
}

async function saveCheckpoint(request, env) {
  const input = await boundedJson(request, MAX_CHECKPOINT_BYTES + 8192);
  leaseInput(input);
  if (!input.checkpoint || typeof input.checkpoint !== 'object' || Array.isArray(input.checkpoint)) validationError('Checkpoint must be an object.');
  const checkpoint = JSON.stringify(input.checkpoint);
  if (new TextEncoder().encode(checkpoint).byteLength > MAX_CHECKPOINT_BYTES) throw Object.assign(new Error('Checkpoint is too large.'), { status: 413 });
  const now = Date.now();
  const row = await env.DB.prepare(`UPDATE submission_jobs SET checkpoint = ?, lease_expires_at = ?, updated_at = ?
    WHERE id = ? AND lease_token = ? AND status = 'processing' AND lease_expires_at > ? RETURNING id`)
    .bind(checkpoint, now + LEASE_MS, new Date(now).toISOString(), input.job_id, input.lease_token, now).first();
  if (!row) return json({ error: 'This job lease is no longer current.' }, 409);
  return json({ ok: true });
}

function requiredText(value, name, max = 6000, nullable = false, allowEmpty = false) {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || value.length > max || (!allowEmpty && !value.trim())) validationError(`Invalid event ${name}.`);
  return value.trim();
}

function eventTimestamp(value, name, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || value.length > 40 || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) validationError(`Invalid event ${name}; use a date and time with a timezone.`);
  const [year, month, day] = value.slice(0, 10).split('-').map(Number);
  const checked = new Date(Date.UTC(year, month - 1, day));
  if (checked.getUTCFullYear() !== year || checked.getUTCMonth() !== month - 1 || checked.getUTCDate() !== day || Number(value.slice(11, 13)) > 23) validationError(`Invalid event ${name}.`);
  return value;
}

export function validatePublishedEvents(events, job, checkpoint, now = Date.now()) {
  if (!Array.isArray(events) || !events.length || events.length > 20 || new TextEncoder().encode(JSON.stringify(events)).byteLength > 96 * 1024) validationError('Publish between one and twenty verified events.');
  const known = knownEvents(job.source_url);
  if (checkpoint.validation?.cached_published === true && known.length) return known;
  const verification = checkpoint.validation;
  if (!verification || canonicalInstagramUrl(verification.source_url) !== job.source_url || verification.coverage_complete !== true
    || !Number.isInteger(verification.images_expected) || verification.images_expected < 1 || verification.images_expected > 20
    || verification.images_inspected !== verification.images_expected
    || typeof verification.durham_uk_probability !== 'number' || verification.durham_uk_probability < 0.9 || verification.durham_uk_probability > 1
    || typeof verification.free_food_probability !== 'number' || verification.free_food_probability < 0.9 || verification.free_food_probability > 1) {
    validationError('Publication requires complete image review and confident free-food and Durham checks.');
  }
  const publishedAt = Date.parse(eventTimestamp(verification.post_published_at, 'source publication date'));
  if (publishedAt < now - 30 * 86400000 || publishedAt > now + 5 * 60000) validationError('The source post must have been published within the last 30 days.');
  const londonDate = value => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
  const earliestEventDate = londonDate(now - 14 * 86400000);
  const seen = new Set();
  return events.map(input => {
    if (!input || typeof input !== 'object' || input.food_is_free !== true || input.needs_review === true) validationError('Only confirmed free-food events can be published.');
    if (canonicalInstagramUrl(input.source_url) !== job.source_url) validationError('The event must come from the submitted Instagram post.');
    const event = {};
    for (const [field, max] of Object.entries({ id: 128, title: 160, society: 200, location: 500, food: 1500, post_id: 160 })) event[field] = requiredText(input[field], field, max);
    if (!/^[a-zA-Z0-9_-]+$/.test(event.id)) validationError('Invalid event ID.');
    event.description = requiredText(input.description, 'description', 6000, false, true);
    event.starts_at = eventTimestamp(input.starts_at, 'start');
    if (londonDate(event.starts_at) < earliestEventDate) validationError('The event must be upcoming or within the last two weeks.');
    event.ends_at = eventTimestamp(input.ends_at, 'end', true);
    event.signup_deadline = eventTimestamp(input.signup_deadline, 'signup deadline', true);
    if (event.ends_at && Date.parse(event.ends_at) <= Date.parse(event.starts_at)) validationError('The event must end after it starts.');
    if (event.signup_deadline && Date.parse(event.signup_deadline) > Date.parse(event.starts_at)) validationError('Signup must close before the event starts.');
    if (input.price !== null && (typeof input.price !== 'number' || !Number.isFinite(input.price) || input.price < 0 || input.price > 10000)) validationError('Invalid event entry price.');
    if (input.signup_required !== null && typeof input.signup_required !== 'boolean') validationError('Invalid signup requirement.');
    event.eligibility = requiredText(input.eligibility, 'eligibility', 3000, true);
    if (!Array.isArray(input.dietary) || input.dietary.length > 20) validationError('Invalid dietary information.');
    event.dietary = input.dietary.map(value => requiredText(value, 'dietary information', 100));
    if (!input.evidence || typeof input.evidence !== 'object') validationError('Source evidence is required.');
    event.evidence = {};
    for (const field of ['food', 'date', 'time', 'location', 'eligibility']) event.evidence[field] = requiredText(input.evidence[field], `evidence.${field}`, 6000, field === 'eligibility');
    if (Boolean(event.eligibility) !== Boolean(event.evidence.eligibility)) validationError('Eligibility conditions and their evidence must be retained.');
    // Evidence stays in its original language. Multilingual Decisions checks and
    // the explicit food_is_free flag establish meaning, not an English regex.
    const identity = `${job.source_url}|${new Date(event.starts_at).toISOString()}`;
    if (seen.has(identity) || seen.has(event.id)) validationError('Duplicate event in publication.');
    seen.add(identity); seen.add(event.id);
    if (event.eligibility && !event.food.toLowerCase().includes(event.eligibility.toLowerCase())) event.food += ` · ${event.eligibility}`;
    return { ...event, source_url: job.source_url, source_id: 'ai-instagram', source_label: 'Instagram · AI checked', food_is_free: true,
      price: input.price, signup_required: input.signup_required, verified_at: new Date(now).toISOString(),
      post_published_at: verification.post_published_at,
      extraction: { method: 'ai', model: requiredText(input.extraction?.model, 'extraction model', 100), human_reviewed: false } };
  });
}

async function completeJob(request, env) {
  const input = await boundedJson(request, 128 * 1024);
  leaseInput(input);
  if (!TERMINAL_STATUSES.has(input.status)) validationError('Choose a final review status.');
  const reason = requiredText(input.reason ?? null, 'review reason', 1000, true);
  if (!Array.isArray(input.events) || (input.status !== 'published' && input.events.length)) validationError('Only published jobs may contain events.');
  const completionHash = await digest(JSON.stringify([input.status, reason, input.events]));
  const row = await env.DB.prepare('SELECT * FROM submission_jobs WHERE id = ?').bind(input.job_id).first();
  if (!row || row.lease_token !== input.lease_token) return json({ error: 'This job lease is no longer current.' }, 409);
  if (TERMINAL_STATUSES.has(row.status)) {
    if (row.completion_hash !== completionHash) return json({ error: 'This job has already been completed with a different result.' }, 409);
    return json({ ok: true, status: row.status, event_ids: JSON.parse(row.events_json).map(event => event.id) });
  }
  const now = Date.now();
  if (row.status !== 'processing' || row.lease_expires_at <= now) return json({ error: 'This job lease is no longer current.' }, 409);
  const events = input.status === 'published' ? validatePublishedEvents(input.events, row, JSON.parse(row.checkpoint), now) : [];
  const saved = await env.DB.prepare(`UPDATE submission_jobs SET status = ?, reason = ?, events_json = ?, completion_hash = ?, updated_at = ?
    WHERE id = ? AND lease_token = ? AND status = 'processing' AND lease_expires_at > ? RETURNING id`)
    .bind(input.status, reason, JSON.stringify(events), completionHash, new Date(now).toISOString(), input.job_id, input.lease_token, now).first();
  if (!saved) {
    const completed = await env.DB.prepare('SELECT status, lease_token, completion_hash, events_json FROM submission_jobs WHERE id = ?').bind(input.job_id).first();
    if (completed?.lease_token === input.lease_token && completed.completion_hash === completionHash && TERMINAL_STATUSES.has(completed.status)) {
      return json({ ok: true, status: completed.status, event_ids: JSON.parse(completed.events_json).map(event => event.id) });
    }
    return json({ error: 'This job lease is no longer current.' }, 409);
  }
  await env.DB.prepare('UPDATE event_suggestions SET status = ?, reason = ? WHERE job_id = ?').bind(input.status, reason, input.job_id).run();
  return json({ ok: true, status: input.status, event_ids: events.map(event => event.id) });
}

async function submittedEvents(env) {
  const rows = (await env.DB.prepare("SELECT events_json, updated_at FROM submission_jobs WHERE status = 'published' ORDER BY updated_at DESC LIMIT 1000").all()).results;
  return json({ updated_at: rows[0]?.updated_at || null, events: rows.flatMap(row => JSON.parse(row.events_json)) });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/api/suggestions') {
        if (request.method !== 'POST') return json({ error: 'Use the suggestion form to send an event.' }, 405, { Allow: 'POST' });
        return await submitSuggestion(request, env);
      }
      if (/^\/api\/suggestions\/[a-f0-9-]{36}$/.test(url.pathname)) {
        if (request.method !== 'GET') return json({ error: 'Method not allowed.' }, 405, { Allow: 'GET' });
        if (!env.DB) return json({ error: 'Event checks are temporarily unavailable.' }, 503);
        const row = await suggestionStatement(env.DB, 'id', url.pathname.split('/').at(-1)).first();
        return row ? json(publicStatus(row)) : json({ error: 'Submission not found.' }, 404);
      }
      if (url.pathname === '/api/submitted-events') {
        if (request.method !== 'GET') return json({ error: 'Method not allowed.' }, 405, { Allow: 'GET' });
        if (!env.DB) return json({ error: 'Events are temporarily unavailable.' }, 503);
        return await submittedEvents(env);
      }
      if (url.pathname.startsWith('/api/collector/')) {
        if (request.method !== 'POST') return json({ error: 'Method not allowed.' }, 405, { Allow: 'POST' });
        await authorizeCollector(request, env);
        if (!env.DB) return json({ error: 'Event checks are temporarily unavailable.' }, 503);
        if (url.pathname === '/api/collector/claim') return await claimJobs(request, env);
        if (url.pathname === '/api/collector/checkpoint') return await saveCheckpoint(request, env);
        if (url.pathname === '/api/collector/complete') return await completeJob(request, env);
        return json({ error: 'Not found.' }, 404);
      }
      if (url.pathname === '/api/health') {
        if (!['GET', 'HEAD'].includes(request.method)) return json({ error: 'Method not allowed.' }, 405, { Allow: 'GET, HEAD' });
        if (!env.DB) return json({ ready: false }, 503);
        // Validate every migration-owned table without exposing suggestions.
        await env.DB.batch([
          env.DB.prepare('SELECT id FROM event_suggestions LIMIT 0'),
          env.DB.prepare('SELECT bucket FROM suggestion_rate_limits LIMIT 0'),
          env.DB.prepare('SELECT day FROM suggestion_salts LIMIT 0'),
          env.DB.prepare('SELECT id, checkpoint, started_day FROM submission_jobs LIMIT 0'),
        ]);
        return json({ ready: true });
      }
      if (!['GET', 'HEAD'].includes(request.method)) return json({ error: 'Method not allowed.' }, 405, { Allow: 'GET, HEAD' });
      const path = url.pathname === '/' ? '/index.html' : url.pathname;
      const asset = Object.hasOwn(ASSETS, path) ? ASSETS[path] : null;
      if (!asset) return new Response('Not found', { status: 404 });
      return new Response(request.method === 'HEAD' ? null : asset.body, { headers: {
        'Content-Type': asset.type, 'Cache-Control': 'public, max-age=0, must-revalidate',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin',
      } });
    } catch (error) {
      if (error.status >= 400 && error.status < 500) return json({ error: error.message }, error.status);
      // Do not log submitted details, links, client addresses or storage internals.
      console.error('Grubr request could not complete', { path: url.pathname, name: error.name });
      return json({ error: 'We could not save your suggestion. Please try again shortly.' }, 503);
    }
  },
};
