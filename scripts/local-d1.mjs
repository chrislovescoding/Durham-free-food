// SQLite adapter for API tests and the local preview only; hosted code uses D1.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));

export function localD1(filename = ':memory:') {
  const sqlite = new DatabaseSync(filename);
  sqlite.exec('PRAGMA journal_mode = WAL');
  sqlite.exec('CREATE TABLE IF NOT EXISTS _grubr_preview_migrations (name TEXT PRIMARY KEY)');
  const journal = JSON.parse(readFileSync(resolve(root, 'drizzle/meta/_journal.json'), 'utf8'));
  for (const entry of journal.entries) {
    if (sqlite.prepare('SELECT name FROM _grubr_preview_migrations WHERE name = ?').get(entry.tag)) continue;
    sqlite.exec('BEGIN');
    try {
      sqlite.exec(readFileSync(resolve(root, `drizzle/${entry.tag}.sql`), 'utf8'));
      sqlite.prepare('INSERT INTO _grubr_preview_migrations (name) VALUES (?)').run(entry.tag);
      sqlite.exec('COMMIT');
    } catch (error) { sqlite.exec('ROLLBACK'); throw error; }
  }
  function statement(sql, values = []) {
    return {
      bind(...params) { return statement(sql, params); },
      async first(column) {
        const row = sqlite.prepare(sql).get(...values) || null;
        return column && row ? row[column] : row;
      },
      async run() {
        const result = sqlite.prepare(sql).run(...values);
        return { success: true, meta: { changes: Number(result.changes) }, results: [] };
      },
      async all() { return { success: true, results: sqlite.prepare(sql).all(...values) }; },
    };
  }
  return {
    sqlite,
    prepare: statement,
    async batch(statements) {
      sqlite.exec('BEGIN');
      try {
        const results = [];
        for (const prepared of statements) results.push(await prepared.all());
        sqlite.exec('COMMIT');
        return results;
      } catch (error) { sqlite.exec('ROLLBACK'); throw error; }
    },
    close() { sqlite.close(); },
  };
}
