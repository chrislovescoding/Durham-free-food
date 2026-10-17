# Full Instagram sweep and recovery

The manual [Instagram sweep workflow](../.github/workflows/instagram-sweep.yml) collects up to **five recent posts per mapped account**, reads captions and actual images, and publishes validated free-food events. It does not promise five results from a quiet, private or inaccessible account, or five posts inside the date window.

The approved 17 October 2026 plan covers **289 Instagram accounts for 285 societies**, up to **1,445 posts** in 29 batches. The erroneous `@d` discovery result is excluded with its evidence recorded in the plan; Dance Troupe remains included as `@du_troupe`.

## Collection and spending controls

1. Preflight checks both repository secrets, `APIFY_TOKEN` and `OPENAI_API_KEY`, and runs the test suite. It then replays 49 saved real posts and their images with an API client that refuses network calls. Controls include the expired Bridge Club pizza event, seven conditional free dinners, and their eligibility wording. This checks the saved evidence, extraction validation and cache; it is not a new live API health check.
2. `scripts/plan_instagram_sweep.py` writes `work/instagram-sweep/plan.json` and `batches/NN/config.json`. Each batch requests at most 50 posts with full carousel details and skips pinned posts. Both Apify's request and our normalizer enforce the publication cutoff.
3. `scripts/reserve_instagram_sweep.py` verifies available Apify credit and records the plan ID and workflow run in `data/instagram-sweep-reservations.json`. The workflow commits this reservation **before** starting any paid actor. A separate dispatch cannot buy the same reserved plan again.
4. Each batch has a server-side Apify charge cap of **$0.14**. The 29 caps total **$4.06**, below the approved **$4.10** plan ceiling. The credit check additionally requires $0.05 of available platform-cost reserve. These are Apify limits; OpenAI inference and platform storage charges are separate.
5. The collector saves its intent before starting an actor, then its run ID, receipt and source data. An ambiguous start is never automatically repeated. Each batch can resume its saved run; a GitHub rerun cannot silently start a new paid collection.

## Automatic analysis and publication

`post_images.py --skip-ocr` saves the actual downloadable poster and carousel images as PNGs. OCR is not required for this sweep. `ai_posts.py` sends the caption, publication date and every available cached image directly to OpenAI:

- **Decisions API:** `POST /v1/decisions` with `gpt-6-luna` tests whether the post explicitly offers free food, including conditional offers. A probability of at least 0.20 goes on to extraction; this threshold favors checking potential matches.
- **Luna extraction:** `POST /v1/responses` with `gpt-6-luna` produces a strict event schema. One post can produce several dated events. The instructions require source evidence, correct UK dates, and clear conditions such as “free on your first visit; £4 otherwise.” Unknown optional details remain unknown.
- **Validation:** `ai_events.py` checks required fields, evidence, source URLs, date ordering and eligibility. It excludes events before the current Europe/London date, preserves same-day events for the website's started-event treatment, and sends uncertain results to `data/review-queue.json`. Model-generated evidence is still model output, not an independent guarantee of accuracy.
- **Merge:** `merge_instagram_sweep.py` validates receipts and merges completed batches. Results must match the saved post evidence. Human-reviewed entries win duplicate events, so existing reviews are preserved. `refresh.py --offline` rebuilds the feed, and the workflow commits it for the website.

The pipeline records AI provenance rather than claiming human review. Missing images, contradictory information, uncertain dates or locations, and incomplete video coverage are held for review. Videos receive **cover-image analysis only**, not audio or full-frame analysis. Stories, private accounts and posts outside the latest-five/30-day window are not covered. Posts containing more than 20 images or exceeding request size limits also need review. These limits can leave relevant events unpublished even when collection succeeds.

## Purchased posts remain available if analysis fails

Collection and analysis are separate. A failed classifier, extraction bug or website issue does **not** require buying those posts again, provided the saved artifacts are retained.

Each batch artifact contains its collector configuration, `run.json` receipt, `dataset-source-fields.json` with the relevant original source fields, normalized `posts.json`, enriched `posts-with-images.json`, `images.json`, cached image PNGs, and available AI reports/checkpoints. The archive intentionally omits unrelated data such as comments and follower lists. Artifacts are uploaded even after a later processing failure when the files are available.

The sweep workflow requests **90-day GitHub artifact retention**. Download the artifacts locally before that expires; a local copy also protects against manual artifact deletion. Original Instagram CDN URLs can expire, so keep the image-cache files alongside the post JSON. Do not rely on those URLs, GitHub artifacts or Apify's own dataset retention as permanent storage.

For the initial full sweep, the workflow run is `39040881025`. Save all artifacts from that run using GitHub's run page, or:

```powershell
gh run download 39040881025 --repo chrislovescoding/Durham-free-food --pattern 'instagram-sweep-*' --dir work/instagram-sweep-archive/39040881025
```

This command preserves each artifact in its own directory. To use `merge_instagram_sweep.py`, restore the plan artifact and batch artifacts into one recovery folder, preserving the shared `plan.json` and `batches/NN/` layout. Missing batch artifacts must be reported as missing rather than replaced with a fresh collection.

## Rerun without repurchasing

Use **Re-run failed jobs on the existing GitHub run**, not a fresh dispatch of the reserved sweep. Batch jobs restore their saved journals, images and checkpoints. If a paid start's outcome is unknown or its artifact is missing, stop and reconcile the run in Apify; never delete its journal or reservation merely to get past the guard.

To rebuild results from an already restored sweep, these commands make no Apify or model calls:

```powershell
python scripts/merge_instagram_sweep.py --folder work/instagram-sweep-recovery
python scripts/refresh.py --offline
```

To reprocess a specific saved batch with a fix, use the same `ai_posts.py` entry point:

```powershell
python scripts/ai_posts.py --folder work/instagram-sweep-recovery/batches/01 --limit 50
python scripts/refresh.py --offline
```

This second example assumes the source posts were already merged and `OPENAI_API_KEY` is configured in the environment. It never calls Apify. Unchanged captions, image bytes, model, prompts and schema reuse cached model responses. Changing those inputs can cause new OpenAI charges. Failed or unknown API outcomes require the explicit `--retry-errors` option; that option can charge again if an earlier response was lost. Keep `data/ai-post-cache.json`, `data/ai-events.json` and the per-batch checkpoints with your recovery copy.

## Planning a future sweep

**The current cutoff is fixed, not rolling:** `2026-09-16T23:00:00Z`, which is midnight on 17 September in Europe/London, for the 17 October sweep. Running the workflow on a later date does not automatically move that cutoff.

Before another approved collection, update the planner's default cutoff or pass a new explicit `--cutoff` in the workflow, computed from the intended Europe/London 30-day window. Review current account coverage, pricing and credit before changing the batch caps. Preserve previous reservations and artifacts; a changed date window creates a different plan ID. The preflight's historical positive controls also contain fixed expected event counts and must be revised once those example dinners expire, while keeping checks for correct past-event filtering. No paid sweep is scheduled automatically.
