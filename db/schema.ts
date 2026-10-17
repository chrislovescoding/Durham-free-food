import { index, integer, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';

export const eventSuggestions = sqliteTable('event_suggestions', {
  id: text('id').primaryKey(),
  idempotencyKey: text('idempotency_key').notNull(),
  payloadHash: text('payload_hash').notNull(),
  title: text('title').notNull(),
  sourceUrl: text('source_url').notNull(),
  details: text('details').notNull(),
  status: text('status').notNull().default('pending'),
  jobId: text('job_id'),
  reason: text('reason'),
  createdAt: text('created_at').notNull(),
}, table => [uniqueIndex('event_suggestions_idempotency_unique').on(table.idempotencyKey), index('event_suggestions_status_created').on(table.status, table.createdAt)]);

export const submissionJobs = sqliteTable('submission_jobs', {
  id: text('id').primaryKey(),
  sourceUrl: text('source_url').notNull(),
  status: text('status').notNull().default('pending'),
  reason: text('reason'),
  eventsJson: text('events_json').notNull().default('[]'),
  checkpoint: text('checkpoint').notNull().default('{}'),
  leaseToken: text('lease_token'),
  leaseExpiresAt: integer('lease_expires_at'),
  startedDay: integer('started_day'),
  attempts: integer('attempts').notNull().default(0),
  completionHash: text('completion_hash'),
  createdAt: text('created_at').notNull(),
  updatedAt: text('updated_at').notNull(),
}, table => [uniqueIndex('submission_jobs_source_unique').on(table.sourceUrl), index('submission_jobs_queue').on(table.status, table.leaseExpiresAt, table.createdAt), index('submission_jobs_budget_day').on(table.startedDay)]);

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
