import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { canonicalInstagramUrl } from '../worker/index.js';
import { localD1 } from '../scripts/local-d1.mjs';

const origin = 'https://grubr.example';
const token = 'test-queue-token-with-at-least-32-characters';
const source = code => `https://www.instagram.com/p/${code}/`;
function fixture(t) { const DB = localD1(); t.after(() => DB.close()); return { DB, GRUBR_QUEUE_TOKEN: token }; }
function api(path, body, auth = token) {
  return new Request(`${origin}/api/collector/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth}` }, body: JSON.stringify(body) });
}
async function submit(env, code = 'ExamplePost', overrides = {}) {
  const input = { title: 'Society pizza night', source_url: source(code), details: 'Private submission note', idempotency_key: crypto.randomUUID(), website: '', ...overrides };
  const response = await worker.fetch(new Request(`${origin}/api/suggestions`, { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: JSON.stringify(input) }), env);
  return { response, input, result: await response.json() };
}
async function claim(env, limit = 2) { return (await (await worker.fetch(api('claim', { limit }), env)).json()).jobs; }
const relativeDate = days => new Date(Date.now() + days * 86400000).toISOString();
function validation(job) { return { source_url: job.source_url, post_published_at: relativeDate(-1), coverage_complete: true, images_expected: 2, images_inspected: 2, durham_uk_probability: 0.99, free_food_probability: 0.99 }; }
async function checkpoint(env, job, extra = {}) {
  const checkpoint = { validation: validation(job), ...extra };
  return worker.fetch(api('checkpoint', { job_id: job.id, lease_token: job.lease_token, checkpoint }), env);
}
function event(job) {
  return { id: 'ai-instagram-example', title: 'Free pizza social', society: 'Durham Pizza Society', location: 'Dunelm House, Durham',
    food: 'Free pizza', description: 'Come and meet the society.', post_id: 'social-example', source_url: job.source_url,
    starts_at: relativeDate(3), ends_at: null, signup_deadline: null, signup_required: null, price: null,
    food_is_free: true, eligibility: null, dietary: [], evidence: { food: 'Free pizza!', date: '20 October 2026', time: '6pm', location: 'Dunelm House', eligibility: null },
    extraction: { method: 'ai', model: 'gpt-6-luna', human_reviewed: false } };
}
function completion(job, status = 'published', events = [event(job)]) { return { job_id: job.id, lease_token: job.lease_token, status, reason: null, events }; }

test('canonical post links strip tracking and p/reel/tv aliases deduplicate into one paid job', async t => {
  const env = fixture(t);
  const first = await submit(env, 'AliasPost', { source_url: 'https://instagram.com/reel/AliasPost/?igsh=tracking' });
  const second = await submit(env, 'AliasPost', { source_url: 'https://m.instagram.com/tv/AliasPost/#caption' });
  assert.equal(first.response.status, 201);
  assert.equal(second.response.status, 201);
  assert.equal((await env.DB.prepare('SELECT count(*) AS n FROM event_suggestions').first()).n, 2);
  assert.equal((await env.DB.prepare('SELECT count(*) AS n FROM submission_jobs').first()).n, 1);
  assert.equal((await claim(env)).length, 1);
  for (const value of ['http://instagram.com/p/thing/', 'https://instagram.com/society/', 'https://instagram.com.evil.test/p/x/', 'https://facebook.com/events/123/', 'https://www.instagram.com:444/p/x/', 'https://user:pass@instagram.com/p/x/']) assert.throws(() => canonicalInstagramUrl(value));
});

test('collector endpoints require the configured secret and public receipts expose no notes or queue internals', async t => {
  const env = fixture(t), receipt = await submit(env);
  for (const path of ['claim', 'checkpoint', 'complete']) {
    assert.equal((await worker.fetch(api(path, { limit: 2 }, 'wrong-token'), env)).status, 401);
    assert.equal((await worker.fetch(api(path, { limit: 2 }), { DB: env.DB })).status, 401);
  }
  const response = await worker.fetch(new Request(`${origin}/api/suggestions/${receipt.result.id}`), env);
  assert.deepEqual(await response.json(), { id: receipt.result.id, status: 'pending', reason: null, event_ids: [] });
  assert.equal((await worker.fetch(new Request(`${origin}/api/suggestions/${crypto.randomUUID()}`), env)).status, 404);
  assert.deepEqual(await (await worker.fetch(new Request(`${origin}/api/submitted-events`), env)).json(), { updated_at: null, events: [] });
});

test('concurrent claims have exclusive leases and cannot exceed five new paid jobs per UTC day', async t => {
  const env = fixture(t);
  for (let i = 0; i < 8; i++) await submit(env, `Budget${i}`);
  const responses = await Promise.all(Array.from({ length: 5 }, () => claim(env)));
  const jobs = responses.flat();
  assert.equal(jobs.length, 5);
  assert.equal(new Set(jobs.map(job => job.id)).size, 5);
  assert.equal((await claim(env)).length, 0);
  assert.equal((await env.DB.prepare("SELECT count(*) AS n FROM submission_jobs WHERE status = 'pending'").first()).n, 3);
});

test('expired leases resume checkpoints without another budget slot and reject stale writes and completions', async t => {
  const env = fixture(t);
  for (let i = 0; i < 6; i++) await submit(env, `Resume${i}`);
  const claimed = [...await claim(env), ...await claim(env), ...await claim(env)];
  const first = claimed[0];
  const paidIntent = { apify: { phase: 'start_unconfirmed', max_charge_usd: 0.05 } };
  assert.equal((await checkpoint(env, first, paidIntent)).status, 200);
  await env.DB.prepare('UPDATE submission_jobs SET lease_expires_at = ? WHERE id = ?').bind(Date.now() - 1, first.id).run();
  const resumed = await claim(env);
  assert.equal(resumed.length, 1);
  assert.equal(resumed[0].id, first.id);
  assert.equal(resumed[0].attempts, 2);
  assert.notEqual(resumed[0].lease_token, first.lease_token);
  assert.deepEqual(resumed[0].checkpoint.apify, paidIntent.apify, 'an ambiguous paid intent survives for the processor to avoid restarting it');
  assert.equal((await checkpoint(env, first)).status, 409);
  assert.equal((await worker.fetch(api('complete', completion(first, 'failed', [])), env)).status, 409);
  assert.equal((await claim(env)).length, 0);
});

test('publication checks full image coverage, exact source, event dates, free food and evidence', async t => {
  const env = fixture(t);
  await submit(env);
  const [job] = await claim(env), valid = event(job);
  assert.equal((await worker.fetch(api('complete', completion(job)), env)).status, 400, 'missing checkpoint cannot publish');
  assert.equal((await checkpoint(env, job, { validation: { ...validation(job), images_inspected: 1 } })).status, 200);
  assert.equal((await worker.fetch(api('complete', completion(job)), env)).status, 400);
  await checkpoint(env, job);
  for (const patch of [
    { source_url: source('DifferentPost') }, { food_is_free: false }, { starts_at: '2026-02-30T18:00:00Z' },
    { starts_at: '2026-10-20T18:00:00' }, { ends_at: relativeDate(-1) },
    { signup_deadline: relativeDate(6) }, { price: -1 }, { starts_at: relativeDate(-16) }, { evidence: { ...valid.evidence, date: '' } },
    { eligibility: 'Members only' }, { needs_review: true },
  ]) assert.equal((await worker.fetch(api('complete', completion(job, 'published', [{ ...valid, ...patch }])), env)).status, 400, JSON.stringify(patch));
  assert.deepEqual((await (await worker.fetch(new Request(`${origin}/api/submitted-events`), env)).json()).events, []);
});

test('successful completion publishes once, supports identical retries and updates all receipts', async t => {
  const env = fixture(t), first = await submit(env), second = await submit(env);
  const [job] = await claim(env);
  await checkpoint(env, job);
  const payload = completion(job);
  const concurrent = await Promise.all([worker.fetch(api('complete', payload), env), worker.fetch(api('complete', payload), env)]);
  assert.ok(concurrent.every(response => response.status === 200));
  assert.equal((await worker.fetch(api('complete', payload), env)).status, 200);
  assert.equal((await worker.fetch(api('complete', { ...payload, reason: 'changed' }), env)).status, 409);
  const feed = await (await worker.fetch(new Request(`${origin}/api/submitted-events`), env)).json();
  assert.equal(feed.events.length, 1);
  assert.equal(feed.events[0].food_is_free, true);
  assert.equal(feed.events[0].extraction.human_reviewed, false);
  assert.ok(feed.updated_at);
  for (const receipt of [first, second]) assert.deepEqual(await (await worker.fetch(new Request(`${origin}/api/suggestions/${receipt.result.id}`), env)).json(), { id: receipt.result.id, status: 'published', reason: null, event_ids: ['ai-instagram-example'] });
  assert.equal((await checkpoint(env, job)).status, 409);
});

test('multilingual evidence remains publishable, but stale source posts are rejected', async t => {
  const env = fixture(t);
  await submit(env);
  const [job] = await claim(env);
  await checkpoint(env, job, { validation: { ...validation(job), post_published_at: relativeDate(-31) } });
  assert.equal((await worker.fetch(api('complete', completion(job)), env)).status, 400);
  await checkpoint(env, job);
  const multilingual = event(job);
  multilingual.evidence.food = '免费食物，欢迎所有学生参加！';
  assert.equal((await worker.fetch(api('complete', completion(job, 'published', [multilingual])), env)).status, 200);
});

test('rejected or uncertain results stay off the public feed and malformed checkpoints cannot overwrite progress', async t => {
  const env = fixture(t), receipt = await submit(env);
  const [job] = await claim(env);
  const base = { job_id: job.id, lease_token: job.lease_token };
  assert.equal((await worker.fetch(api('checkpoint', { ...base, checkpoint: [] }), env)).status, 400);
  assert.equal((await worker.fetch(api('checkpoint', { ...base, checkpoint: { value: 'x'.repeat(128 * 1024) } }), env)).status, 413);
  assert.equal((await worker.fetch(api('complete', completion(job, 'rejected')), env)).status, 400);
  const payload = { ...completion(job, 'rejected', []), reason: 'The post does not offer free food.' };
  assert.equal((await worker.fetch(api('complete', payload), env)).status, 200);
  assert.deepEqual((await (await worker.fetch(new Request(`${origin}/api/submitted-events`), env)).json()).events, []);
  const status = await (await worker.fetch(new Request(`${origin}/api/suggestions/${receipt.result.id}`), env)).json();
  assert.equal(status.reason, payload.reason);
  assert.equal(status.status, 'rejected');
  assert.equal((await claim(env)).length, 0);
});

test('legacy Instagram suggestions join the queue while generic links require a person', async t => {
  const env = fixture(t);
  for (const [i, link] of [source('LegacyPost'), 'https://example.com/event'].entries()) {
    await env.DB.prepare('INSERT INTO event_suggestions (id, idempotency_key, payload_hash, title, source_url, details, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
      .bind(crypto.randomUUID(), `legacy-key-${i}`, 'old-hash', 'Old suggestion', link, 'Private note', 'pending', '2026-10-10T10:00:00Z').run();
  }
  const jobs = await claim(env);
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].source_url, source('LegacyPost'));
  const generic = await env.DB.prepare('SELECT status, reason, job_id FROM event_suggestions WHERE source_url = ?').bind('https://example.com/event').first();
  assert.equal(generic.status, 'needs_review');
  assert.equal(generic.job_id, null);
  assert.match(generic.reason, /Instagram/);
});

test('operator-confirmed nonstarts can be retried without replacing receipts or budget reservations', async t => {
  const env = fixture(t), receipt = await submit(env);
  const [job] = await claim(env);
  const savedCheckpoint = { source_url: job.source_url, apify: { phase: 'start_unconfirmed', actor: 'apify~instagram-scraper', max_charge_usd: 0.05 },
    result: { status: 'needs_review', reason: 'Collection start was unconfirmed.', events: [] } };
  assert.equal((await worker.fetch(api('checkpoint', { job_id: job.id, lease_token: job.lease_token, checkpoint: savedCheckpoint }), env)).status, 200);
  assert.equal((await worker.fetch(api('complete', { ...completion(job, 'needs_review', []), reason: 'Collection start was unconfirmed.' }), env)).status, 200);
  const before = await env.DB.prepare('SELECT * FROM submission_jobs WHERE id = ?').bind(job.id).first();
  const payload = { job_id: job.id, expected_updated_at: before.updated_at, reset_unstarted: true };
  assert.equal((await worker.fetch(api('retry', payload, 'wrong-token'), env)).status, 401);
  assert.equal((await worker.fetch(api('retry', { ...payload, reset_unstarted: false }), env)).status, 400);
  assert.equal((await worker.fetch(api('retry', { ...payload, expected_updated_at: '2026-01-01T00:00:00Z' }), env)).status, 409);
  const concurrent = await Promise.all([worker.fetch(api('retry', payload), env), worker.fetch(api('retry', payload), env)]);
  assert.deepEqual(concurrent.map(response => response.status).sort(), [200, 409]);
  const after = await env.DB.prepare('SELECT * FROM submission_jobs WHERE id = ?').bind(job.id).first();
  assert.equal(after.started_day, before.started_day);
  assert.equal(after.attempts, before.attempts);
  assert.equal(after.lease_token, null);
  assert.equal(after.completion_hash, null);
  assert.equal(after.status, 'pending');
  assert.equal(JSON.parse(after.checkpoint).apify, undefined);
  assert.equal(JSON.parse(after.checkpoint).operator_recovery.confirmed_unstarted, true);
  assert.deepEqual(await (await worker.fetch(new Request(`${origin}/api/suggestions/${receipt.result.id}`), env)).json(), { id: receipt.result.id, status: 'pending', reason: null, event_ids: [] });
  const [resumed] = await claim(env);
  assert.equal(resumed.id, job.id);
  assert.equal(resumed.attempts, job.attempts + 1);
  assert.equal((await worker.fetch(api('complete', completion(job, 'failed', [])), env)).status, 409, 'the previous lease cannot complete the retried job');
});

test('operator recovery refuses checkpoints with existing paid work or published/rejected jobs', async t => {
  const env = fixture(t);
  for (const [index, extra] of [
    { apify: { phase: 'start_unconfirmed', run_id: 'existing-run' } },
    { apify: { phase: 'start_unconfirmed', dataset_id: 'existing-dataset' } },
    { apify: { phase: 'started' } },
    { decision_started: true }, { decision: { free_food: 0.99 } }, { extraction_started: true },
    { extraction: { events: [] } }, { validation: {} },
  ].entries()) {
    await submit(env, `RecoverBlocked${index}`);
    const row = await env.DB.prepare('SELECT * FROM submission_jobs WHERE source_url = ?').bind(source(`RecoverBlocked${index}`)).first();
    const checkpoint = { apify: { phase: 'start_unconfirmed' }, ...extra };
    await env.DB.prepare("UPDATE submission_jobs SET status = 'failed', checkpoint = ? WHERE id = ?").bind(JSON.stringify(checkpoint), row.id).run();
    const response = await worker.fetch(api('retry', { job_id: row.id, expected_updated_at: row.updated_at, reset_unstarted: true }), env);
    assert.equal(response.status, 409, JSON.stringify(extra));
    assert.equal((await env.DB.prepare('SELECT status FROM submission_jobs WHERE id = ?').bind(row.id).first()).status, 'failed');
  }
  for (const status of ['pending', 'processing', 'published', 'rejected']) {
    await submit(env, `StatusBlocked${status}`);
    const row = await env.DB.prepare('SELECT * FROM submission_jobs WHERE source_url = ?').bind(source(`StatusBlocked${status}`)).first();
    await env.DB.prepare('UPDATE submission_jobs SET status = ?, checkpoint = ? WHERE id = ?').bind(status, JSON.stringify({ apify: { phase: 'start_unconfirmed' } }), row.id).run();
    assert.equal((await worker.fetch(api('retry', { job_id: row.id, expected_updated_at: row.updated_at, reset_unstarted: true }), env)).status, 409);
  }
});
