# Grubr

A hackathon prototype for finding free food at Durham student events, with a source-linked event feed and an evening data collector. The public source repository is **Durham-free-food**.

**[Open the prototype](https://durham-free-food-chris.chris9p.chatgpt.site)** · **[Try the clearly labelled example feed](https://durham-free-food-chris.chris9p.chatgpt.site/?demo=1)**

The hosted Grubr website and the source repository are public. The default live feed contains only source-backed, explicitly confirmed free-food events. If none are found, it shows an honest empty state. Demo listings are fictional, marked on every card and in every calendar download, and kept separate from real data.

## Run locally

Requires Python 3.11+; Node 20+ is used only for frontend tests and optional npm commands. There is no frontend build or package installation.

```sh
python -m http.server 4173 --directory dist --bind 127.0.0.1
```

Open `http://127.0.0.1:4173` for live listings or `http://127.0.0.1:4173/?demo=1` for demo events.

```sh
python -m pip install -r requirements.txt
python scripts/refresh.py
node --test tests/events.test.js
python -m unittest discover -s tests -p 'test_*.py' -v
```

## What works

- Search by event, food, society or location; filter by today, tomorrow, the next seven days, entry price and registration.
- Event details show food, entry cost, date, time, place, sign-up rules/deadline, eligibility, dietary information and original source. Unknown information stays unknown.
- Started events remain grey for the rest of their London calendar day, then disappear. Date filtering is repeated every minute and when returning to the tab.
- Save events on the current browser and download an `.ics` calendar entry. No account is required; saved events do not sync across devices.
- Browse every SU society, filter collection coverage, and suggest missing or corrected account links with ownership evidence.
- Collect recent public Instagram captions, publication times and original post links through public profile embeds; inspect per-account results under **How it works**. Facebook public HTML and authorised Meta API adapters are also included.
- Suggest an event through a pre-filled GitHub issue. The visitor reviews and submits it; it is not automatically published.
- The refresh workflow runs around **20:00 Europe/London** each day, handling British Summer Time. GitHub may delay scheduled runs. It can also be run manually from the Actions tab.

The frontend checks the public repository's latest `main/dist/data/*.json` data when opened, falling back to its bundled snapshot if the repository is unavailable. This lets the scheduled workflow update the hosted site's content without redeploying the frontend. A running tab recalculates dates every minute; refresh the page to fetch newly collected data.

## Real sources and current limits

The initial crawl found **319 societies** and **77 society profiles with a direct social link** in the [Durham SU directory](https://www.durhamsu.com/groups/studentgroups/). Supported sources include public ICS calendars, JSON-LD event pages and the SU's public Native event listings. The [DAISI site](https://durhamaisafety.uk/) links a public Luma calendar. The [SU Pantry](https://www.durhamsu.com/support/pantry/) is also linked as an ongoing food resource; its opening hours are not invented.

The initial calendar checks found no qualifying upcoming event. Reviewing a collected Instagram caption and its original poster subsequently confirmed **Computing Society’s Career-Ready Night, 19 October 2026, 18:00–20:00, TLC113, with free pizza**. Entry price and signup requirements remain unknown. It is published through the reviewed-event input with the original post link. An event with free entry or a mention of food is insufficient to assert that the food is free.

Account research on 17 October 2026 covered **all 206 societies** that lacked verified account links after the earlier crawl. Public ownership evidence supported **300 additional account mappings: 177 Instagram and 123 Facebook**, covering **181** of those societies. Combined with the earlier 113 societies, **294 of all 319 societies now have at least one verified account reference**; **25 remain unresolved on both platforms**. Some societies have only one platform identified. Unknown handles were not guessed, and similarly named groups without an ownership link were excluded.

The reviewed mappings are stored in [the account registry](data/society-accounts.json), with evidence URLs and review timestamps. [The research audit](data/account-research-audit.json) records researched societies and unresolved cases. Facebook group links are retained as unsupported references and are **not fetched**. Account discovery measures verified references, not successful post collection.

Before this account expansion, the 17 October 2026 collector snapshot stored **417 real Instagram posts from 90 societies**, with **no Facebook posts collected**. Those are the earlier collection results, not a claim that the newly researched accounts have all been collected. They are social posts, not 417 confirmed free-food events. The website's **How it works** view reports the latest collection results separately.

**Instagram post collection now works without credentials for accounts whose public embeds expose posts.** It collects the society's captions, publication dates and permalinks, not bios, comments or followers. A public embed usually exposes only six recent posts; daily collection retains observed posts for 60 days. It cannot guarantee complete history or catch a post that leaves that window between runs. The public Facebook pages tested did not expose captions; their coverage is marked unavailable rather than reported as an empty successful feed.

The first GitHub-hosted collection test completed successfully but Instagram rate-limited its cloud runner. The collector stopped that platform and retained previously collected posts with their original timestamps. This validates failure handling, not reliable unattended Instagram access.

An official Meta API adapter is included for accounts with authorised access. It has not been connected because no API credentials are configured. See [the social access guide](scripts/README-social.md) for repository secrets, account mapping and the difference between public professional Instagram accounts and Facebook Page permissions. No password, session cookie or access token belongs in the source repository.

The social caption parser only publishes unambiguous date/time/location and free-food offers. Publication time is never used as event time. Relative wording is anchored to the original post's timestamp. Multiple event times, poster-only details, unsupported language or ambiguous signup deadlines require manual review; the automated candidate queue only detects supported caption phrases. The site displays collected posts separately from confirmed events.

The collector uses conservative text rules, not a language-model service. Nuanced captions, image-only posters and recurrence require human review. Failed sources keep their last good records and verification timestamps, and the site reports incomplete coverage. Source assertions are not a guarantee that an organiser will run the event unchanged.

Coverage is explicitly measured across **all 319 societies**, including those without discoverable accounts. Discovery follows direct links, plain-text handles and linked society websites; a separate reviewed account registry fills verified gaps. Public scraping cannot promise every post from every society.

See [the ingestion guide](scripts/README-backend.md) for source configuration, review format, status fields and recovery behavior.

## Structure

```text
dist/                       Static frontend, ready to host
  app.js                    Views, filters, saved events and calendar downloads
  events.js                 London dates, eligibility filters and calendar helpers
  data/                     Generated public event and society feeds
scripts/refresh.py          Public-source collector
scripts/social_posts.py     Public Instagram/Facebook post collection
scripts/meta_posts.py       Optional authorised Meta Graph API adapter
scripts/social_events.py    Conservative caption-to-event extraction
data/sources.json           Supported source configuration
data/society-accounts.json  Reviewed social-account mappings and ownership evidence
data/account-research-audit.json  Research coverage and unresolved account cases
data/reviewed-events.json   Explicitly reviewed real event inputs
data/review-queue.json      Candidates requiring human review
tests/                     Date, feed and parser regression checks
.github/workflows/          Evening data refresh
.openai/hosting.json        Sites hosting configuration
```

The frontend is ordinary HTML, CSS and JavaScript, so it can be hosted on any static host.

The post-expansion live collection run on 17 October checked 405 configured public profiles/Pages: 290 redirected to login and 115 exposed no owned post captions. Another 39 configured references were unsupported groups and were not fetched. No new posts were observed; the 417 earlier Instagram posts were retained with their original observation times. This is an account-discovery improvement, not proof of reliable social-platform access.
