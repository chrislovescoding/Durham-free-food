# Grubr: Instagram and Facebook collection

## Working without credentials

The collector starts with every society in the SU directory. It reads direct social links, visible description URLs and explicitly labelled Instagram handles, and follows up to two explicitly identified society websites/Linktree pages by one hop. External fetches validate public HTTPS destinations and redirects. Each discovery stores its evidence URL; account names are not guessed. Dedicated SU website fields and affirmative ownership context identify society sites. Ambiguous/sponsor links are left in discovery_review_links without promoting their social accounts. Multiple accounts per society are supported. For Instagram it reads the normal public profile, then the ordinary public profile embed when the profile is only an empty public shell. It parses the platform's JSON without executing JavaScript. An actual access denial, login wall or rate limit stops that route; there are no proxies, login cookies or CAPTCHA workarounds.

Public Instagram embeds were verified with real posts from Durham Christian Union, VegSoc, Chinese Christian Fellowship, PPE and Bar Society. Embeds usually contain six recent posts. Collection runs daily and merges previously seen posts for the configured retention window. This is recent-post coverage, not a complete historical archive. Accounts with private/restricted or unavailable embeds cannot be collected through this route.

Facebook public JSON-LD and Story records are supported only when their content is exposed and ownership matches the society Page. The Facebook pages and public Page Plugin timelines tested exposed profile information but no actual captions. This is reported per account. Profile biographies are never counted as posts. Verified Facebook group links remain in the registry as unsupported references; the collector does not fetch group content.

Run the collector using the saved directory:

```sh
python scripts/refresh.py --social-only
```

The generated `dist/data/social-posts.json` contains platform, society, account URL, canonical post URL, caption, original publication time, observation time and available media URLs. Missing timestamps remain null. Comments/followers are excluded. The website's **How it works** section shows coverage and recent real captions; those posts are separate from verified events.

The public path has a bounded request budget, modest delay and least-recently-attempted ordering so accounts are not permanently skipped. HTTP 429 defers the rest of that platform for the run. Old records keep their original observation timestamps when retained after a failure. The snapshot reports partial collection honestly.

## Connect the official API when access is available

No API credentials are configured by default. The adapter is implemented and tested with fixtures; it has not been tested against a live authorised account. An API token alone does not grant access to every account.

1. Set up a Meta developer app and obtain appropriate approved permissions. Instagram Business Discovery applies to public professional accounts, using an authorised caller Instagram account. Facebook collection requires Page access or approved Page Public Content Access for other Pages. The implementation does not collect private Facebook groups.
2. In this GitHub repository's **Settings → Secrets and variables → Actions**, add secrets `META_ACCESS_TOKEN` and `META_INSTAGRAM_USER_ID`. Keep values out of chat, commits and workflow files.
3. Add the repository Actions variable `META_GRAPH_API_VERSION` with an explicitly chosen supported version (`vNN.0`). The code deliberately requires a pinned version rather than silently choosing one.
4. In `data/sources.json` under `social.meta.facebook_page_ids`, map each authorised Facebook profile URL to its exact numeric Page ID. Example shape: `{"https://www.facebook.com/yourSociety/": "123456789"}`. The example ID is a placeholder, not a real configured Page.
5. Run **Actions → Refresh public food events → Run workflow**. Inspect per-account statuses and the persisted posts. A successful workflow can still have denied/missing-credential accounts; the source report records that distinction.

Tokens are sent only in the Authorization header to the fixed `graph.facebook.com` origin. Pagination URLs are validated and reconstructed, redirects are rejected, and API/exception text is not stored because it may contain credentials. GitHub's token is used only to commit the resulting public data.

Official references: [Instagram Business Discovery](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery), [Instagram media fields](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media), [Facebook Page feed permissions](https://developers.facebook.com/docs/graph-api/reference/page/feed/).

## From a post to an event

Captions are collected before event eligibility is assessed. Auto-publication requires an explicit free-food promise, one unambiguous event date and start time, and a labelled location. Unknown entry prices, dietary requirements and registration rules stay unknown. The publication timestamp anchors phrases such as “tomorrow”; it is never substituted for the event date. Explicit yearless dates must fit a short window after publication, and weekday/date mismatches go for review.

Image-only event details, languages unsupported by the English phrase/date rules, time ranges or multiple dates require a person to inspect the original post/poster and add a reviewed record. OCR and model-based poster extraction are not implemented. Candidates are stored in `data/review-queue.json`; approved data goes in `data/reviewed-events.json` using the format in the ingestion guide.

## Coverage across all societies

Collection cannot be guaranteed for every society by a universal scraper. Some SU profiles omit their accounts, some timelines require permissions, and public embeds expose a small window. Facebook groups are explicitly unsupported. A successful collection run is not complete coverage.

The 17 October 2026 research pass investigated **all 206 societies** that had no verified accounts after the earlier discovery run. It added **300 mappings (177 Instagram and 123 Facebook)** for **181 societies**. Together with the existing 113, that establishes at least one verified account reference for **294 of the 319 directory societies**. **25 societies remain unresolved on both platforms**; additional societies still lack one platform. Counts include unsupported Facebook group references and do not imply that their posts can be collected.

The evidence-audited mappings live in [`data/society-accounts.json`](../data/society-accounts.json), including public evidence URLs, review timestamps and evidence notes. [`data/account-research-audit.json`](../data/account-research-audit.json) records research coverage and unresolved cases. Searches included public society profiles, linked websites and self-identifying social profiles. Ambiguous national accounts, other universities and similarly named societies were left unresolved; handles were not guessed.

The **pre-expansion** collector snapshot on 17 October 2026 contained **417 real Instagram posts from 90 societies and zero Facebook posts**. This historical result predates the new mappings. Use the generated coverage report and the website's **How it works** view for current collection results; a verified account reference is distinct from a successful post fetch.

Every directory society appears in `social-posts.json.coverage.societies`, including missing accounts. The website's **Societies** view can filter account gaps, inaccessible posts and stale collections. A recently observed real post establishes limited coverage, not a complete timeline. Account-level records preserve provider failures and last-attempt times. GitHub Actions writes these counts in its run summary.

For an account absent from the SU profile/linked website, use **Add / correct account** on the society card. A maintainer checks the public ownership evidence and adds a mapping to `data/society-accounts.json`:

```json
{
  "accounts": [{
    "society_url": "https://www.durhamsu.com/groups/studentgroup/EXAMPLE/",
    "platform": "instagram",
    "url": "https://www.instagram.com/example_society/",
    "evidence_url": "https://example.org/society/",
    "verified_at": "2026-10-17T12:00:00Z",
    "reviewed": true,
    "evidence_note": "The society's public website directly links this account."
  }]
}
```

These are placeholder URLs: replace them with verified evidence and the current directory entry. Reviewed mappings are separate from discovery, so a later empty SU profile does not erase them. Invalid or removed-society mappings are reported and skipped. Removing the reviewed mapping removes its contribution on the next run.

For societies whose posts cannot be accessed, the supported fallback is an organiser-submitted original event link and reviewed event record. No organiser outreach is sent automatically. Obtaining authorised API access and maintaining these verified account/submission paths is required to approach whole-directory coverage.
