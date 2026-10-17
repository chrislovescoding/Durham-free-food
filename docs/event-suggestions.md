# Automatic event submissions

Visitors paste a public Instagram post link into Grubr. The website saves a durable receipt and a deduplicated job in D1. The Check submitted event links GitHub Actions workflow checks the queue every five minutes (GitHub may delay scheduled runs). Valid events appear in the website's live feed without a redeployment. The form remembers the latest receipt and shows queued, checking, published, rejected, or needs-attention status.

## Verification and publication

1. Fetch the exact post through Apify, preserving the caption, timestamp and every available carousel image.
2. Reject posts older than 30 days. Incomplete image coverage cannot be automatically published.
3. Send the caption and actual images to the Decisions API. Both explicit free food and Durham, UK relevance must reach 90% confidence.
4. Use gpt-6-luna with a strict schema to extract dates, venue, food, price, eligibility, sign-up requirements and source evidence. User-entered hints are not evidence.
5. Validate dates, original source, required fields and evidence again at the publication endpoint. Events can be upcoming or within the last 14 calendar days; dates more than a year ahead are not published automatically.
6. Publish approved records to /api/submitted-events. The frontend merges them with existing listings, preserving manually checked records and removing duplicates. Open pages refresh once a minute.

Unclear, inaccessible or incomplete posts receive an explanation and are held for attention; unsupported posts are rejected. Facebook links are not supported by this submission flow yet.

## Cost and retry controls

Each new post scrape has an Apify maximum total charge of $0.05. The queue permits five new jobs per UTC day, reserving a conservative $0.25 daily scraper allowance. Additional submissions wait for capacity. OpenAI usage is separate. Existing listed posts and repeated links reuse their saved result.

Exclusive 30-minute leases prevent concurrent processing. Checkpoints save paid-request intent, Apify run IDs and completed AI results before continuing. An ambiguous paid request is not blindly repeated. Captions, downloaded images and AI results are retained as GitHub workflow artifacts for 90 days, with signed image URL query strings removed from JSON artifacts. This supports investigation without scraping again.

## Deployment and secrets

GitHub repository secrets: APIFY_TOKEN, OPENAI_API_KEY, GRUBR_QUEUE_TOKEN. The same GRUBR_QUEUE_TOKEN is a Sites runtime secret; only its authenticated worker can claim jobs or publish results. API keys remain in GitHub Actions. Do not commit credentials or put them in frontend assets.

Run manually with: gh workflow run process-suggestions.yml

The site uses the existing public Sites project and D1 binding DB. Schema changes are generated from db/schema.ts using npm run db:generate; never edit an applied migration. Sites applies checked-in schema migrations during deployment.

## API and privacy

POST /api/suggestions accepts {title, source_url, details, idempotency_key, website: ""} with the website's exact Origin. Links must identify a specific HTTPS Instagram post. Retries with identical content reuse their receipt; conflicting idempotency keys return 409. Body and field lengths, a honeypot, prepared SQL and per-address submission limits are enforced. IP addresses are hashed with private rotating salts.

GET /api/suggestions/<receipt UUID> returns only status, an explanation and event IDs. Submitted notes and private queue checkpoints are not public. GET /api/submitted-events returns approved events only.

Authenticated processor routes are /api/collector/claim, /api/collector/checkpoint and /api/collector/complete. Writes require the current lease token; completed writes are idempotent.

## Local verification

Run node scripts/build-worker.mjs, node scripts/validate-artifact.mjs, npm test, and python -m unittest discover -s tests -p 'test_*.py'. Start the preview with node scripts/preview-worker.mjs --port 4177.

Tests use fake external services and do not spend API credits. The preview uses persistent local SQLite in .sites-local/suggestions.sqlite, separate from production D1. GET /api/health checks storage availability.
