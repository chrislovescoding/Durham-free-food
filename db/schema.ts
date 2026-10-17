import { index, integer, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';

export const eventSuggestions = sqliteTable('event_suggestions', {
  id: text('id').primaryKey(),
  idempotencyKey: text('idempotency_key').notNull(),
  payloadHash: text('payload_hash').notNull(),
  title: text('title').notNull(),
  sourceUrl: text('source_url').notNull(),
  details: text('details').notNull(),
  status: text('status').notNull().default('pending'),
  createdAt: text('created_at').notNull(),
}, table => [uniqueIndex('event_suggestions_idempotency_unique').on(table.idempotencyKey), index('event_suggestions_status_created').on(table.status, table.createdAt)]);

export const suggestionRateLimits = sqliteTable('suggestion_rate_limits', {
  bucket: text('bucket').primaryKey(),
  count: integer('count').notNull(),
  expiresAt: integer('expires_at').notNull(),
}, table => [index('suggestion_rate_expiry').on(table.expiresAt)]);

export const suggestionSalts = sqliteTable('suggestion_salts', {
  day: integer('day').primaryKey(),
  salt: text('salt').notNull(),
  expiresAt: integer('expires_at').notNull(),
});
