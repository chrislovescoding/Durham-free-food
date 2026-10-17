import { createServer } from 'node:http';
import { Readable } from 'node:stream';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import worker from '../dist/server/index.js';
import { localD1 } from './local-d1.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const portIndex = process.argv.indexOf('--port');
const port = portIndex >= 0 ? Number(process.argv[portIndex + 1]) : 4177;
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Choose a port between 1024 and 65535.');
await mkdir(resolve(root, '.sites-local'), { recursive: true });
const db = localD1(resolve(root, '.sites-local/suggestions.sqlite'));
const server = createServer(async (incoming, outgoing) => {
  try {
    const headers = new Headers();
    for (const [key, value] of Object.entries(incoming.headers)) {
      if (value !== undefined) headers.set(key, Array.isArray(value) ? value.join(', ') : value);
    }
    headers.set('CF-Connecting-IP', incoming.socket.remoteAddress || '127.0.0.1');
    const request = new Request(`http://127.0.0.1:${port}${incoming.url}`, {
      method: incoming.method, headers,
      ...(['GET', 'HEAD'].includes(incoming.method) ? {} : { body: Readable.toWeb(incoming), duplex: 'half' }),
    });
    const response = await worker.fetch(request, { DB: db }, {});
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    if (response.body) Readable.fromWeb(response.body).pipe(outgoing);
    else outgoing.end();
  } catch {
    outgoing.writeHead(500, { 'Content-Type': 'text/plain' });
    outgoing.end('Local preview could not complete the request.');
  }
});
server.listen(port, '127.0.0.1', () => console.log(`Grubr Worker preview: http://127.0.0.1:${port} (persistent local SQLite; production D1 is separate)`));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => { db.close(); process.exit(0); }));
