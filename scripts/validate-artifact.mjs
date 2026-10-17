import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const source = await readFile(resolve(root, 'dist/server/index.js'), 'utf8');
const manifest = JSON.parse(await readFile(resolve(root, 'dist/.openai/hosting.json'), 'utf8'));
assert.equal(manifest.project_id, 'appgprj_6ad3557782d88191b431c0d023ac7ec6');
assert.equal(manifest.d1, 'DB');
assert.equal(manifest.static, undefined);
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const { default: worker } = await import(moduleUrl);
assert.equal(typeof worker.fetch, 'function');
for (const path of ['/', '/app.js', '/events.js', '/styles.css', '/hero-mascot.js', '/hero-mascot.css', '/promotions.js', '/promotions.css', '/favicon.svg', '/data/events.json']) {
  const response = await worker.fetch(new Request(`https://grubr.example${path}`), {});
  assert.equal(response.status, 200, `${path} is served from the Worker`);
  assert.ok((await response.text()).length > 0);
}
assert.equal((await worker.fetch(new Request('https://grubr.example/data/social-posts.json'), {})).status, 404);
const journal = JSON.parse(await readFile(resolve(root, 'dist/.openai/drizzle/meta/_journal.json'), 'utf8'));
assert.ok(journal.entries.length > 0, 'Migration journal is packaged');
console.log('Artifact is valid Worker ESM; public assets and migration metadata are present.');
