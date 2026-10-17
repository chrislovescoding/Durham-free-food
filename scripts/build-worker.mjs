import { readFile, writeFile, mkdir, cp, readdir } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const required = ['index.html', 'app.js', 'events.js', 'styles.css', 'favicon.svg', 'data/events.json'];
const optional = ['event-details.js', 'event-details.css'];
const files = [...required];
for (const filename of optional) {
  try { await readFile(resolve(root, 'dist', filename)); files.push(filename); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}
for (const filename of await readdir(resolve(root, 'dist/assets'))) {
  if (/^[a-zA-Z0-9._-]+\.svg$/.test(filename)) files.push(`assets/${filename}`);
}
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8' };
const assets = {};
for (const filename of files.sort()) {
  const body = await readFile(resolve(root, 'dist', filename), 'utf8');
  if (Buffer.byteLength(body) > 2 * 1024 * 1024) throw new Error(`Unexpectedly large public asset: ${filename}`);
  assets[`/${filename}`] = { type: types[extname(filename)], body };
}
const manifest = JSON.parse(await readFile(resolve(root, '.openai/hosting.json'), 'utf8'));
if (manifest.static || manifest.d1 !== 'DB') throw new Error('Worker hosting requires d1: DB and no static configuration.');
const source = await readFile(resolve(root, 'worker/index.js'), 'utf8');
const marker = '/* GRUBR_ASSETS */ {}';
if (source.split(marker).length !== 2) throw new Error('Expected exactly one asset marker.');
await mkdir(resolve(root, 'dist/server'), { recursive: true });
await mkdir(resolve(root, 'dist/.openai'), { recursive: true });
await writeFile(resolve(root, 'dist/server/index.js'), source.replace(marker, () => JSON.stringify(assets)));
await cp(resolve(root, '.openai/hosting.json'), resolve(root, 'dist/.openai/hosting.json'));
await cp(resolve(root, 'drizzle'), resolve(root, 'dist/.openai/drizzle'), { recursive: true });
console.log(`Built Sites Worker with ${files.length} embedded public assets and D1 migrations.`);
