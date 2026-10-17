# Event suggestions

The public form sends `POST /api/suggestions` on the Grubr website. The server stores a durable pending record in the Sites-managed D1 binding `DB` before returning confirmation. Suggestions do not appear in the confirmed event feed until reviewed. The form needs no user account and does not ask for a name or email address.

Request JSON:

```json
{
  "title": "Event name",
  "source_url": "https://www.instagram.com/p/the-original-post/",
  "details": "Food, place, date, and any eligibility rules.",
  "idempotency_key": "a-client-generated-UUID",
  "website": ""
}
```

Successful first submissions return HTTP 201 with `{ "id": "saved-UUID", "status": "pending", "duplicate": false }`. A retry using the same key and content returns HTTP 200 with the same ID; changed content under an existing key returns 409. The client should keep the key while retrying an uncertain submission. Validation errors, rate limits and storage failures have distinct non-success responses. The browser must never confirm an unsuccessful write.

The API accepts only JSON requests with the website's exact Origin, checks a hidden honeypot field, bounds the body to 8 KiB and validates title, URL and detail lengths. SQL uses prepared parameters. Five new submissions per address per hour are allowed; an idempotent retry does not consume the limit. If the host omits the address header, a shared fallback allows 50 per hour. Client addresses are never stored: the database holds a hash mixed with a private, random daily salt, and rate-limit records and salts expire and are cleaned during subsequent submissions. No submitted content is written to logs. Public GET requests cannot retrieve suggestions.

## Reviewing the queue

The authenticated owner can use the Sites connector's database overview and table-row tools for project `appgprj_6ad3557782d88191b431c0d023ac7ec6` to read `event_suggestions`. This is an authenticated management operation, not a public website endpoint. Review pending suggestions against their original posts, check dates, location, explicit free food and eligibility, and then add accepted listings to the existing reviewed-events collector input. No suggestion is automatically trusted or published. The public frontend does not expose the queue or the database management tools.

## Build, storage and local verification

The site retains its original project ID and public audience. It uses the supported Sites Worker ESM artifact: `dist/server/index.js`, plus `.openai/hosting.json` and Drizzle migration metadata. `scripts/build-worker.mjs` embeds an explicit allowlist of frontend files and the confirmed event snapshot into the Worker; it does not serve the raw social-post archive. Original frontend and collector files remain at their existing paths. The scheduled event collector and the frontend's latest-feed fetch continue to work.

Run:

```sh
node scripts/build-worker.mjs
node scripts/validate-artifact.mjs
node --test tests/*.test.js
node scripts/preview-worker.mjs --port 4177
```

The preview runs the built Worker against persistent SQLite in `.sites-local/suggestions.sqlite`, applying only pending checked-in migrations. Restarting the preview preserves submitted rows. Production runs against real D1; Sites applies the schema-only migrations before deploying the Worker. `GET /api/health` checks all required tables and returns only `{ "ready": true }` when storage is usable.

When the schema changes, update `db/schema.ts`, install the pinned development dependencies, run `npm run db:generate`, inspect the generated SQL and metadata, then build and publish. Never edit an already-applied migration. No API keys, new accounts or runtime secrets are needed for submissions.
