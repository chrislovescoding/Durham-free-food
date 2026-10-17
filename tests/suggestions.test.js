import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import worker from '../worker/index.js';
import { localD1 } from '../scripts/local-d1.mjs';

const origin = 'https://grubr.example';
const suggestion = () => ({ title: 'Bridge Club pizza night', source_url: 'https://www.instagram.com/p/example/', details: 'Free pizza for beginners. Date is on the poster.', idempotency_key: crypto.randomUUID(), website: '' });
function request(body, headers = {}) {
  return new Request(`${origin}/api/suggestions`, { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json', 'CF-Connecting-IP': '192.0.2.1', ...headers }, body: JSON.stringify(body) });
}
function fixture(t) { const DB = localD1(); t.after(() => DB.close()); return { DB }; }

test('suggestions are durable pending records and retries return their original ID', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'grubr-suggestions-'));
  const filename = join(directory, 'suggestions.sqlite');
  let DB = localD1(filename);
  t.after(() => { DB.close(); rmSync(directory, { recursive: true, force: true }); });
  const input = suggestion();
  const response = await worker.fetch(request(input), { DB });
  assert.equal(response.status, 201);
  const first = await response.json();
  assert.equal(first.status, 'pending');
  DB.close();
  DB = localD1(filename);
  const retry = await worker.fetch(request(input), { DB });
  assert.equal(retry.status, 200);
  assert.equal((await retry.json()).id, first.id);
  assert.equal((await DB.prepare('SELECT count(*) AS count FROM event_suggestions').first()).count, 1);
  const saved = await DB.prepare('SELECT title, details, source_url FROM event_suggestions WHERE id = ?').bind(first.id).first();
  assert.equal(saved.title, input.title);
  assert.equal(saved.details, input.details);
  assert.equal(saved.source_url, input.source_url);
});

test('same submission ID with different content conflicts instead of changing a saved suggestion', async t => {
  const env = fixture(t), input = suggestion();
  await worker.fetch(request(input), env);
  const response = await worker.fetch(request({ ...input, title: 'Changed event name' }), env);
  assert.equal(response.status, 409);
  assert.equal((await env.DB.prepare('SELECT title FROM event_suggestions').first()).title, input.title);
});

test('public users cannot read the pending queue and health exposes no records', async t => {
  const env = fixture(t);
  await worker.fetch(request(suggestion()), env);
  assert.equal((await worker.fetch(new Request(`${origin}/api/suggestions`), env)).status, 405);
  const health = await worker.fetch(new Request(`${origin}/api/health`), env);
  assert.deepEqual(await health.json(), { ready: true });
});

test('cross-site requests and requests without origin never write', async t => {
  const env = fixture(t);
  for (const headers of [{ Origin: 'https://elsewhere.example' }, { 'Sec-Fetch-Site': 'cross-site' }, { Origin: '' }]) {
    assert.equal((await worker.fetch(request(suggestion(), headers), env)).status, 403);
  }
  assert.equal((await env.DB.prepare('SELECT count(*) AS count FROM event_suggestions').first()).count, 0);
});

test('invalid links, fields and honeypot input are rejected', async t => {
  const env = fixture(t);
  for (const patch of [{ source_url: 'javascript:alert(1)' }, { source_url: 'https://name:password@example.com' }, { title: 'a' }, { details: 'a'.repeat(3001) }, { idempotency_key: 'short' }, { website: 'bot.example' }]) {
    assert.equal((await worker.fetch(request({ ...suggestion(), ...patch }), env)).status, 400);
  }
  assert.equal((await env.DB.prepare('SELECT count(*) AS count FROM event_suggestions').first()).count, 0);
});

test('oversized and non-JSON bodies are rejected before storage', async t => {
  const env = fixture(t);
  assert.equal((await worker.fetch(request(suggestion(), { 'Content-Type': 'text/plain' }), env)).status, 415);
  assert.equal((await worker.fetch(request({ ...suggestion(), details: 'a'.repeat(9000) }), env)).status, 413);
});

test('a failed or missing database never returns a successful submission', async () => {
  assert.equal((await worker.fetch(request(suggestion()), {})).status, 503);
  const DB = { prepare() { throw new Error('Database unavailable'); } };
  assert.equal((await worker.fetch(request(suggestion()), { DB })).status, 503);
});

test('rate limits are durable, bounded and do not store the client address', async t => {
  const env = fixture(t);
  let first;
  for (let i = 0; i < 5; i++) {
    const input = suggestion();
    if (!i) first = input;
    assert.equal((await worker.fetch(request(input), env)).status, 201);
  }
  assert.equal((await worker.fetch(request(suggestion()), env)).status, 429);
  assert.equal((await worker.fetch(request(first), env)).status, 200, 'safe retry works even after the rate limit');
  assert.equal((await worker.fetch(request(suggestion(), { 'CF-Connecting-IP': '192.0.2.2' }), env)).status, 201);
  const rows = (await env.DB.prepare('SELECT * FROM suggestion_rate_limits').all()).results;
  assert.equal(rows.length, 2);
  assert.ok(rows.every(row => /^[a-f0-9]{64}$/.test(row.bucket)));
  assert.ok(!JSON.stringify(rows).includes('192.0.2.'));
});
