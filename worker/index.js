// Sites Worker ESM entry point. scripts/build-worker.mjs embeds the public assets.
const ASSETS = /* GRUBR_ASSETS */ {};
const MAX_BODY_BYTES = 8192;
const JSON_HEADERS = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };

function json(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), { status, headers: { ...JSON_HEADERS, ...headers } });
}

function sameOrigin(request) {
  const origin = request.headers.get('Origin');
  if (request.headers.get('Sec-Fetch-Site') === 'cross-site') return false;
  return origin === new URL(request.url).origin;
}

async function boundedJson(request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') || '')) {
    throw Object.assign(new Error('Please send the suggestion as JSON.'), { status: 415 });
  }
  if (Number(request.headers.get('Content-Length')) > MAX_BODY_BYTES) {
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
    if (length > MAX_BODY_BYTES) {
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
  let url;
  try { url = new URL(source); } catch { validationError('Add a valid link to the original event.'); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) validationError('Add a valid http or https event link.');
  if (typeof key !== 'string' || !/^[a-zA-Z0-9_-]{16,80}$/.test(key)) validationError('Please refresh the page and try again.');
  return { title, source_url: url.href, details, idempotency_key: key };
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

async function submitSuggestion(request, env) {
  if (!sameOrigin(request)) return json({ error: 'Please submit the form on Grubr.' }, 403);
  const input = validateSuggestion(await boundedJson(request));
  if (!env.DB) return json({ error: 'Suggestions are temporarily unavailable. Please try again shortly.' }, 503);
  const payloadHash = await digest(JSON.stringify([input.title, input.source_url, input.details]));
  const existing = await env.DB.prepare('SELECT id, status, payload_hash FROM event_suggestions WHERE idempotency_key = ?')
    .bind(input.idempotency_key).first();
  if (existing) {
    if (existing.payload_hash !== payloadHash) return json({ error: 'This submission has changed. Please start a new suggestion.' }, 409);
    return json({ id: existing.id, status: existing.status, duplicate: true }, 200);
  }
  const now = Date.now();
  if (!await rateLimit(request, env.DB, now)) return json({ error: 'You have sent a few suggestions recently. Please try again in an hour.' }, 429, { 'Retry-After': '3600' });
  const id = crypto.randomUUID();
  await env.DB.prepare('INSERT INTO event_suggestions (id, idempotency_key, payload_hash, title, source_url, details, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(idempotency_key) DO NOTHING')
    .bind(id, input.idempotency_key, payloadHash, input.title, input.source_url, input.details, 'pending', new Date(now).toISOString()).run();
  // Read the durable row before confirming; concurrent retries return the same ID.
  const saved = await env.DB.prepare('SELECT id, status, payload_hash FROM event_suggestions WHERE idempotency_key = ?')
    .bind(input.idempotency_key).first();
  if (!saved) throw new Error('Suggestion was not persisted');
  if (saved.payload_hash !== payloadHash) return json({ error: 'This submission has changed. Please start a new suggestion.' }, 409);
  return json({ id: saved.id, status: saved.status, duplicate: saved.id !== id }, saved.id === id ? 201 : 200);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/api/suggestions') {
        if (request.method !== 'POST') return json({ error: 'Use the suggestion form to send an event.' }, 405, { Allow: 'POST' });
        return await submitSuggestion(request, env);
      }
      if (url.pathname === '/api/health') {
        if (!['GET', 'HEAD'].includes(request.method)) return json({ error: 'Method not allowed.' }, 405, { Allow: 'GET, HEAD' });
        if (!env.DB) return json({ ready: false }, 503);
        // Validate every migration-owned table without exposing suggestions.
        await env.DB.batch([
          env.DB.prepare('SELECT id FROM event_suggestions LIMIT 0'),
          env.DB.prepare('SELECT bucket FROM suggestion_rate_limits LIMIT 0'),
          env.DB.prepare('SELECT day FROM suggestion_salts LIMIT 0'),
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
