# Durham Free Food

A hackathon prototype for finding free food at Durham student events, with a source-linked event feed and an evening data collector.

**[Open the prototype](https://durham-free-food-chris.chris9p.chatgpt.site)** · **[Try the clearly labelled example feed](https://durham-free-food-chris.chris9p.chatgpt.site/?demo=1)**

The hosted preview is private to its owner. The source repository is public. The default live feed contains only source-backed, explicitly confirmed free-food events. If none are found, it shows an honest empty state. Demo listings are fictional, marked on every card and in every calendar download, and kept separate from real data.

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
python -m unittest discover -s tests -p 'test_ingestion.py' -v
```

## What works

- Search by event, food, society or location; filter by today, tomorrow, the next seven days, entry price and registration.
- Event details show food, entry cost, date, time, place, sign-up rules/deadline, eligibility, dietary information and original source. Unknown information stays unknown.
- Started events remain grey for the rest of their London calendar day, then disappear. Date filtering is repeated every minute and when returning to the tab.
- Save events on the current browser and download an `.ics` calendar entry. No account is required; saved events do not sync across devices.
- Browse the real SU society directory and the social-profile links exposed on each public society page.
- Suggest an event through a pre-filled GitHub issue. The visitor reviews and submits it; it is not automatically published.
- The refresh workflow runs around **20:00 Europe/London** each day, handling British Summer Time. GitHub may delay scheduled runs. It can also be run manually from the Actions tab.

The frontend checks the public repository's latest `main/dist/data/*.json` data when opened, falling back to its bundled snapshot if the repository is unavailable. This lets the scheduled workflow update the hosted site's content without redeploying the frontend. A running tab recalculates dates every minute; refresh the page to fetch newly collected data.

## Real sources and current limits

The first crawl found **319 societies** and **77 society profiles with a public Instagram or Facebook link** in the [Durham SU directory](https://www.durhamsu.com/groups/studentgroups/). Supported sources include public ICS calendars, JSON-LD event pages and the SU's public Native event listings. The [DAISI site](https://durhamaisafety.uk/) links a public Luma calendar. The [SU Pantry](https://www.durhamsu.com/support/pantry/) is also linked as an ongoing food resource; its opening hours are not invented.

No verified upcoming free-food event was found in the initial source checks on 17 October 2026. An event with free entry or a mention of food is insufficient to assert that the food is free. Ambiguous entries go into a review queue.

**Instagram and Facebook posts are not automatically scraped in this prototype.** Their public profile links are discovered, but feed access is inconsistent and often requires platform permissions. To extend coverage, add supported organiser feeds or review original social posts into `data/reviewed-events.json`. A future Meta integration should use appropriately authorised accounts and API access. It should not depend on scraping login pages.

The collector uses conservative text rules, not a language-model service. Nuanced captions, image-only posters and recurrence require human review. Failed sources keep their last good records and verification timestamps, and the site reports incomplete coverage. Source assertions are not a guarantee that an organiser will run the event unchanged.

See [the ingestion guide](scripts/README-backend.md) for source configuration, review format, status fields and recovery behavior.

## Structure

```text
dist/                       Static frontend, ready to host
  app.js                    Views, filters, saved events and calendar downloads
  events.js                 London dates, eligibility filters and calendar helpers
  data/                     Generated public event and society feeds
scripts/refresh.py          Public-source collector
data/sources.json           Supported source configuration
data/reviewed-events.json   Explicitly reviewed real event inputs
data/review-queue.json      Candidates requiring human review
tests/                     Date, feed and parser regression checks
.github/workflows/          Evening data refresh
.openai/hosting.json        Private Sites preview configuration
```

The frontend is ordinary HTML, CSS and JavaScript, so it can be hosted on any static host. The prototype uses Durham-inspired purple but no official University or SU branding.
