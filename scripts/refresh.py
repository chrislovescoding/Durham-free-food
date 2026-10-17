"""Conservative public-source ingestion. Run from any directory with Python 3.11+."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


ROOT = Path(__file__).resolve().parents[1]
LONDON = ZoneInfo("Europe/London")
FOODS = r"(?:pizza|food|snacks?|refreshments?|lunch|dinner|breakfast|buffet|meals?|sandwich(?:es)?|cake|cookies|biscuits|doughnuts|donuts|pastries|noodles?|curry|soup|coffee|tea|hot chocolate)"
FREE_FOOD = re.compile(
    rf"\b(?:free|complimentary)\s+(?:(?:vegan|vegetarian|hot|fresh|cheese|delicious|tasty)\s+){{0,2}}{FOODS}\b|"
    rf"\b{FOODS}\s+(?:(?:will\s+be|is|are|for)\s+)?free\b", re.I
)
SOCIAL_HOSTS = {"instagram.com": "instagram_url", "facebook.com": "facebook_url", "fb.com": "facebook_url"}
USER_AGENT = "DurhamFreeFood/0.1 (+https://github.com/chrislovescoding/Durham-free-food)"


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str, default_timezone: str | None = None) -> datetime:
    if not isinstance(value, str) or not re.search(r"[T ]\d\d:\d\d", value):
        raise ValueError("An explicit date and time are required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not default_timezone:
            raise ValueError("An explicit UTC offset or source timezone is required")
        local_zone = ZoneInfo(default_timezone)
        parsed = parsed.replace(tzinfo=local_zone)
        # Ambiguous/nonexistent clock times must be reviewed, never guessed.
        if parsed.utcoffset() != parsed.replace(fold=1).utcoffset():
            raise ValueError("Ambiguous or nonexistent local clock time; include UTC offset")
    return parsed


def http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"https", "http"} and parsed.netloc else None


def plain(value: Any) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def fetch(url: str) -> str:
    response = requests.get(url, timeout=(10, 25), headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError("Source exceeds the 5 MB ingestion limit")
    return response.text


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def directory_societies(html: str, url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    directory_host = urlparse(url).hostname
    for anchor in soup.select("a[href]"):
        href = urljoin(url, anchor.get("href", ""))
        name = anchor.get_text(" ", strip=True)
        if name and urlparse(href).hostname == directory_host and re.search(r"/groups/studentgroup/\d+/?$", urlparse(href).path):
            found[href] = {"name": name, "url": href}
    if not found:
        raise ValueError("No society links found; directory markup may have changed")
    return sorted(found.values(), key=lambda item: item["name"].casefold())


def social_links(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    # The SU footer/header contain SU-wide social links, not society accounts.
    for element in soup.select("header, footer, nav, #footer, .footer, #header"):
        element.decompose()
    found = {}
    for anchor in soup.select("a[href]"):
        url = urljoin(page_url, anchor.get("href", ""))
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
        key = SOCIAL_HOSTS.get(host)
        if not key or not parsed.path.strip("/"):
            continue
        if re.search(r"/(?:sharer|share|intent|login|accounts)(?:[/.?]|$)", parsed.path, re.I):
            continue
        if re.search(r"durham(?:studentsunion|[_.]?su)", parsed.path, re.I):
            continue
        found.setdefault(key, url)
    return found


def refresh_societies(config: dict, previous: dict, now: datetime, *, directory_only: bool = False, workers: int = 4) -> tuple[dict, dict]:
    url = config["society_directory"]
    stamp = iso(now)
    status = {"id": "durham-su", "type": "directory", "url": url, "label": "Durham Students' Union", "name": "Durham Students' Union", "last_attempt_at": stamp}
    try:
        societies = directory_societies(fetch(url), url)
    except Exception as error:
        status.update(status="error", error=str(error), last_success_at=previous.get("updated_at"))
        return previous, status
    previous_by_url = {item["url"]: item for item in previous.get("societies", [])}
    for society in societies:
        for key in ("instagram_url", "facebook_url", "social_accounts", "socials_checked_at", "discovery_status", "discovery_errors", "discovery_review_links"):
            if key in previous_by_url.get(society["url"], {}):
                society[key] = previous_by_url[society["url"]][key]
    failures = []
    if not directory_only:
        def discover(society):
            try:
                from scripts.society_discovery import discover_accounts, safe_discovery_fetch
            except ModuleNotFoundError:
                from society_discovery import discover_accounts, safe_discovery_fetch
            time.sleep(0.25)
            html = fetch(society["url"])
            return discover_accounts(html, society["url"], safe_discovery_fetch)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(discover, society): society for society in societies}
            for future in as_completed(futures):
                society = futures[future]
                try:
                    # A successful profile fetch replaces old accounts if removed.
                    links = future.result()
                    failed_pages = {error.get("url", "").rstrip("/") for error in links.get("discovery_errors", [])}
                    # A failed linked website is not evidence its account vanished.
                    # Retain only mappings previously evidenced by that failed page.
                    existing = {a.get("url", "").rstrip("/") for a in links.get("social_accounts", [])}
                    for account in society.get("social_accounts", []):
                        if account.get("evidence_url", "").rstrip("/") in failed_pages and account.get("url", "").rstrip("/") not in existing:
                            links.setdefault("social_accounts", []).append({**account, "discovery_stale": True,
                                "last_confirmed_at": account.get("last_confirmed_at") or society.get("socials_checked_at")})
                            links.setdefault(account["platform"] + "_url", account["url"])
                            links["discovery_status"] = "partial"
                    society.pop("instagram_url", None)
                    society.pop("facebook_url", None)
                    society.pop("social_accounts", None)
                    society.update(links, socials_checked_at=stamp)
                except Exception as error:
                    failures.append({"url": society["url"], "error": str(error)})
                    society["discovery_status"] = "stale" if society.get("socials_checked_at") else "fetch_failed"
    payload = {"updated_at": stamp, "source_url": url, "societies": societies}
    discovery_partial = sum(bool(s.get("discovery_errors")) for s in societies)
    status.update(status="partial" if failures or discovery_partial else "ok", last_success_at=stamp, society_count=len(societies),
                  parsed_count=len(societies),
                  profiles_failed=len(failures), profile_errors=failures,
                  societies_with_accounts=sum(bool(s.get("instagram_url") or s.get("facebook_url") or s.get("social_accounts")) for s in societies),
                  discovery_partial=discovery_partial)
    return payload, status


def food_evidence(text: str) -> str | None:
    evidence = []
    for match in FREE_FOOD.finditer(text):
        # Keep uncertain/negated promises for a person to review. This deliberately
        # favours missing an ambiguous listing over inventing a confirmed offer.
        before = max(text.rfind(mark, 0, match.start()) for mark in (".", "!", "?", "\n")) + 1
        after_positions = [text.find(mark, match.end()) for mark in (".", "!", "?", "\n")]
        after = min([position for position in after_positions if position >= 0] or [len(text)])
        sentence = text[before:after].lower()
        if re.search(r"\b(?:no|not|never|without|unavailable|cancelled|canceled|might|may|maybe|perhaps|hopefully|hope|hoping|possibly|possible|potentially|chance|win|raffle|if)\b|\bsubject to\b|\bsold out\b", sentence):
            return None
        evidence.append(match.group(0).strip())
    return evidence[0] if evidence else None


def stable_id(source_id: str, identity: str) -> str:
    return source_id + "-" + hashlib.sha256(identity.encode()).hexdigest()[:14]


def base_event(source: dict, identity: str, title: str, starts: datetime, ends: datetime | None, description: str,
               location: str, source_url: str, now: datetime, **extra) -> dict | None:
    food = food_evidence(title + " " + description)
    if not food:
        return None
    if ends is not None and ends <= starts:
        raise ValueError("Event end must follow its start")
    return {
        "id": stable_id(source["id"], identity), "source_id": source["id"], "title": title,
        "society": source["society"], "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat() if ends else None, "location": location or "Location not provided",
        "food": food, "food_is_free": True, "price": None, "signup_required": None,
        "signup_deadline": None, "eligibility": None,
        "source_url": source_url, "source_label": source.get("source_label", source["society"]),
        "description": description, "dietary": [], "verified_at": iso(now), **extra,
    }


def decode_ics(value: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda match: "\n" if match.group(1).lower() == "n" else match.group(1), value)


def ics_datetime(value: str, params: dict, default_timezone: str) -> datetime:
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        raise ValueError("All-day event has no explicit start time")
    if not re.fullmatch(r"\d{8}T\d{6}Z?", value):
        raise ValueError("Unsupported ICS datetime")
    parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return parsed.replace(tzinfo=timezone.utc)
    return parse_datetime(parsed.isoformat(), params.get("TZID", default_timezone))


def parse_ics(text: str, source: dict, now: datetime) -> tuple[list[dict], list[dict], int]:
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ValueError("Source did not return a complete iCalendar document")
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    records, current = [], None
    for line in unfolded.splitlines():
        if line.strip() == "BEGIN:VEVENT":
            current = {}
        elif line.strip() == "END:VEVENT":
            if current is not None:
                records.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            bits = key.split(";")
            params = dict(bit.split("=", 1) for bit in bits[1:] if "=" in bit)
            current[bits[0].upper()] = (decode_ics(value), params)
    events, candidates = [], []
    for record in records:
        get = lambda key, default="": record.get(key, (default, {}))[0]
        title, description = get("SUMMARY"), plain(get("DESCRIPTION"))
        if get("STATUS").upper() == "CANCELLED":
            continue
        url = http_url(get("URL"))
        if not url:
            match = re.search(r"https://(?:lu\.ma|luma\.com)/[A-Za-z0-9_-]+", description)
            url = match.group(0) if match else source["url"]
        try:
            if "RRULE" in record or "RDATE" in record:
                raise ValueError("Recurring event requires review; recurrence is not expanded")
            start_value, start_params = record["DTSTART"]
            start = ics_datetime(start_value, start_params, source.get("timezone", "Europe/London"))
            end = ics_datetime(*record["DTEND"], source.get("timezone", "Europe/London")) if "DTEND" in record else None
            event = base_event(source, get("UID") or url + start.isoformat(), title, start, end, description, get("LOCATION"), url, now)
            if event:
                events.append(event)
            elif re.search(FOODS, title + " " + description, re.I):
                candidates.append({"title": title, "source_url": url, "kind": "needs_food_confirmation", "reason": "Food mentioned without an explicit free-food promise"})
        except (ValueError, KeyError) as error:
            candidates.append({"title": title, "source_url": url, "kind": "parse_error", "reason": str(error)})
    return events, candidates, len(records)


def jsonld_nodes(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from jsonld_nodes(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in value:
                yield from jsonld_nodes(value[key])


def parse_jsonld(html: str, source: dict, now: datetime) -> tuple[list[dict], list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    nodes = []
    for script in soup.find_all("script", type="application/ld+json"):
        nodes.extend(jsonld_nodes(json.loads(script.string or script.get_text())))
    records = [node for node in nodes if any(str(kind).split("/")[-1] == "Event" for kind in (node.get("@type", []) if isinstance(node.get("@type"), list) else [node.get("@type")]))]
    if not records:
        raise ValueError("No structured Event data found; source may have changed or require login")
    events, candidates = [], []
    for record in records:
        title, description = plain(record.get("name")), plain(record.get("description"))
        url = http_url(record.get("url")) or source["url"]
        if str(record.get("eventStatus", "")).endswith(("EventCancelled", "EventPostponed")):
            continue
        try:
            starts = parse_datetime(record["startDate"], source.get("timezone"))
            ends = parse_datetime(record["endDate"], source.get("timezone")) if record.get("endDate") else None
            location = record.get("location", {})
            if isinstance(location, list):
                location = location[0] if location else {}
            if isinstance(location, dict):
                location = plain(location.get("name") or "")
            event = base_event(source, url + starts.isoformat(), title, starts, ends, description, plain(location), url, now)
            if event:
                offers = record.get("offers", [])
                offers = [offers] if isinstance(offers, dict) else offers
                prices = [float(offer["price"]) for offer in offers if isinstance(offer, dict) and str(offer.get("priceCurrency", "")).upper() == "GBP" and re.fullmatch(r"\d+(?:\.\d{1,2})?", str(offer.get("price", "")))]
                # Multiple prices/tier eligibility remain unknown until reviewed.
                event["price"] = prices[0] if prices and len(set(prices)) == 1 else None
                events.append(event)
            elif re.search(FOODS, title + " " + description, re.I):
                candidates.append({"title": title, "source_url": url, "kind": "needs_food_confirmation", "reason": "Food mentioned without an explicit free-food promise"})
        except (ValueError, KeyError, TypeError) as error:
            candidates.append({"title": title, "source_url": url, "kind": "parse_error", "reason": str(error)})
    return events, candidates, len(records)


def native_detail(html: str) -> dict:
    """Read public JSON hydration data without evaluating any page JavaScript."""
    def objects(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from objects(item)
        elif isinstance(value, list):
            for item in value:
                yield from objects(item)

    # The web page itself supplies these JSON strings in self.__next_f.push.
    stream = "".join(json.loads(raw) for raw in re.findall(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', html))
    # Long descriptions are RSC text records referenced as "$46". Their length
    # prefix is UTF-8 bytes, not Python characters (emoji are common here).
    encoded = stream.encode("utf-8")
    text_records = {}
    for match in re.finditer(rb"([0-9a-f]+):T([0-9a-f]+),", encoded):
        length = int(match.group(2), 16)
        text_records[match.group(1).decode()] = encoded[match.end():match.end() + length].decode("utf-8")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[0-9a-f]+:(?=[\[{])", stream):
        try:
            tree, _ = decoder.raw_decode(stream, match.end())
        except json.JSONDecodeError:
            continue
        for value in objects(tree):
            key = value.get("queryKey")
            if isinstance(key, list) and key and key[0] == "EventDetails":
                detail = value.get("state", {}).get("data")
                if isinstance(detail, dict) and "description" in detail:
                    description = detail["description"]
                    if isinstance(description, str) and re.fullmatch(r"\$[0-9a-f]+", description):
                        if description[1:] not in text_records:
                            raise ValueError("Native description text reference could not be resolved")
                        detail = {**detail, "description": text_records[description[1:]]}
                    return detail
    raise ValueError("Native event details not found; public page markup may have changed")


def parse_native(text: str, source: dict, now: datetime) -> tuple[list[dict], list[dict], int]:
    """Paginate the same public JSON endpoint used by Durham SU's What's On page."""
    page = json.loads(text)
    records = {}
    visited = {1}
    for _ in range(25):
        if not isinstance(page, dict) or not isinstance(page.get("data"), list) or "count" not in page:
            raise ValueError("Native event index changed its response schema")
        for record in page["data"]:
            records[str(record["id"])] = record
        next_page = page.get("nextPage")
        if next_page is None:
            break
        if not isinstance(next_page, int) or next_page in visited:
            raise ValueError("Native pagination repeated or returned an invalid page")
        visited.add(next_page)
        page = json.loads(fetch(source["api_url"] + "?page=" + str(next_page)))
    else:
        raise ValueError("Native event index exceeded the 25-page safety limit")
    events, candidates = [], []

    def inspect(record):
        url = urljoin(source["url"], "/event/" + str(record["slug"]) + "/" + str(record["id"]))
        title = plain(record.get("name"))
        if record.get("isSoldOut"):
            return None, None
        try:
            starts = parse_datetime(record["startAt"])
            if starts.astimezone(LONDON).date() < now.astimezone(LONDON).date():
                return None, None
            time.sleep(0.25)
            detail = native_detail(fetch(url))
            if detail.get("isCancelled") or detail.get("isSoldOut"):
                return None, None
            description = plain(detail.get("description"))
            ends = parse_datetime(record["finishAt"]) if record.get("finishAt") else None
            organiser = detail.get("promoterGroup") or detail.get("promoter") or {}
            event_source = {**source, "society": organiser.get("name") or source["society"]}
            event = base_event(event_source, str(record["id"]), title, starts, ends, description,
                               plain(record.get("location")), url, now)
            if event:
                # Native supplies a starting ticket price; that is not an exact price.
                price_from = detail.get("priceFrom", record.get("priceFrom"))
                currency = detail.get("currencyCode", record.get("currencyCode"))
                if currency == "GBP" and re.fullmatch(r"\d+(?:\.\d{1,2})?", str(price_from)):
                    event["price_from"] = float(price_from)
                return event, None
            if re.search(FOODS, title + " " + description, re.I):
                return None, {"title": title, "source_url": url, "kind": "needs_food_confirmation", "reason": "Food mentioned without an explicit free-food promise"}
            return None, None
        except Exception as error:
            return None, {"title": title, "source_url": url, "kind": "parse_error", "reason": str(error)}

    with ThreadPoolExecutor(max_workers=4) as executor:
        for event, candidate in executor.map(inspect, records.values()):
            if event:
                events.append(event)
            if candidate:
                candidates.append(candidate)
    return events, candidates, len(records)


def reviewed_event(record: dict, now: datetime) -> dict:
    required = ("id", "title", "society", "starts_at", "location", "food", "source_url", "verified_at")
    if not record.get("reviewed"):
        raise ValueError("Manual entries require reviewed: true")
    for field in required:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"Missing {field}")
    if not http_url(record["source_url"]):
        raise ValueError("Manual event needs a public http(s) source URL")
    if record.get("food_is_free") is not True:
        raise ValueError("Only explicitly confirmed free food belongs in the live feed")
    starts = parse_datetime(record["starts_at"])
    ends = parse_datetime(record["ends_at"]) if record.get("ends_at") else None
    if ends is not None and ends <= starts:
        raise ValueError("Event end must follow its start")
    verified = parse_datetime(record["verified_at"])
    if verified > now:
        raise ValueError("Verification time cannot be in the future")
    deadline = parse_datetime(record["signup_deadline"]) if record.get("signup_deadline") else None
    if deadline is not None and deadline > starts:
        raise ValueError("Signup deadline cannot follow event start")
    price = record.get("price")
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0):
        raise ValueError("Price must be a nonnegative GBP number or null")
    signup = record.get("signup_required")
    if signup is not None and not isinstance(signup, bool):
        raise ValueError("signup_required must be true, false or null")
    dietary = record.get("dietary", [])
    if not isinstance(dietary, list) or not all(isinstance(item, str) for item in dietary):
        raise ValueError("dietary must be a list of confirmed dietary labels")
    result = {"ends_at": None, "price": None, "signup_required": None, "signup_deadline": None,
              "eligibility": None, "source_label": "Reviewed source", "description": "", "dietary": [], **record,
              "source_id": "manual", "starts_at": starts.isoformat(), "ends_at": ends.isoformat() if ends else None}
    result.pop("reviewed", None)
    return result


def visible_today_or_future(event: dict, now: datetime) -> bool:
    return parse_datetime(event["starts_at"]).astimezone(LONDON).date() >= now.astimezone(LONDON).date()


def refresh_events(config: dict, previous: dict, reviewed: dict, now: datetime, *, offline=False) -> tuple[dict, dict]:
    events, candidates, sources = [], [], []
    previous_status = {source["id"]: source for source in previous.get("sources", [])}
    for source in config.get("feeds", []):
        if offline:
            events.extend(event for event in previous.get("events", []) if event.get("source_id") == source["id"])
            sources.append(previous_status.get(source["id"], {
                "id": source["id"], "type": source["type"], "url": source["url"],
                "name": source.get("source_label", source["society"]), "status": "unavailable",
                "error": "No saved source result; offline rebuild made no requests"}))
            continue
        status = {"id": source["id"], "type": source["type"], "url": source["url"],
                  "label": source.get("source_label", source["society"]), "name": source.get("source_label", source["society"]), "last_attempt_at": iso(now)}
        try:
            content = fetch(source.get("api_url", source["url"]))
            parser = {"ics": parse_ics, "jsonld": parse_jsonld, "native": parse_native}.get(source["type"])
            if parser is None:
                raise ValueError("Unsupported feed type; use ics, jsonld or native")
            found, pending, seen = parser(content, source, now)
            events.extend(found)
            candidates.extend({**item, "source_id": source["id"]} for item in pending)
            parse_errors = [item for item in pending if item.get("kind") == "parse_error"]
            failed_urls = {item["source_url"] for item in parse_errors}
            retained = [event for event in previous.get("events", []) if event.get("source_id") == source["id"] and event.get("source_url") in failed_urls]
            events.extend(retained)
            status.update(status="partial" if parse_errors else "ok", last_success_at=iso(now), events_seen=seen,
                          parsed_count=seen,
                          eligible_count=len(found), candidate_count=len(pending), parse_error_count=len(parse_errors), retained_count=len(retained))
        except Exception as error:
            retained = [event for event in previous.get("events", []) if event.get("source_id") == source["id"]]
            events.extend(retained)
            status.update(status="error", error=str(error), retained_count=len(retained),
                          last_success_at=previous_status.get(source["id"], {}).get("last_success_at"))
        sources.append(status)
    manual_errors = []
    manual_events = []
    for record in reviewed.get("events", []):
        try:
            if not isinstance(record, dict):
                raise ValueError("Manual entry must be an object")
            manual_events.append(reviewed_event(record, now))
        except (ValueError, KeyError, TypeError) as error:
            manual_errors.append({"id": record.get("id") if isinstance(record, dict) else None, "error": str(error)})
    if manual_errors:
        # Do not discard a formerly valid reviewed feed because of an edit error.
        manual_events = [event for event in previous.get("events", []) if event.get("source_id") == "manual"]
    events.extend(manual_events)
    unique = {}
    expired = 0
    for event in events:
        try:
            if not visible_today_or_future(event, now):
                expired += 1
                continue
            if event.get("food_is_free") is not True:
                continue
            key = (event["source_url"].rstrip("/"), parse_datetime(event["starts_at"]).astimezone(timezone.utc))
            unique[key] = event
        except (ValueError, KeyError, TypeError):
            continue
    published = sorted(unique.values(), key=lambda event: parse_datetime(event["starts_at"]))
    failed = sum(source["status"] == "error" for source in sources)
    partial = sum(source["status"] == "partial" for source in sources)
    state = "partial" if failed or partial or manual_errors else "ok"
    if sources and failed == len(sources) and not manual_events:
        state = "error"
    status = {"state": state, "source_count": len(sources), "successful_sources": len(sources) - failed,
              "failed_sources": failed, "partial_sources": partial, "parse_error_count": sum(source.get("parse_error_count", 0) for source in sources),
              "live_event_count": len(published), "reviewed_event_count": len(manual_events),
              "candidate_count": len(candidates), "expired_count": expired, "manual_errors": manual_errors,
              "social_ingestion": "Social post collection is merged in a separate pipeline step."}
    payload = {"updated_at": iso(now), "timezone": "Europe/London", "events": published, "sources": sources, "status": status}
    review_queue = {"updated_at": iso(now), "candidates": candidates}
    return payload, review_queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--now", help="ISO timestamp, for reproducible checks")
    parser.add_argument("--offline", action="store_true", help="Rebuild events and review queue from saved posts/feeds and reviewed events without network requests")
    parser.add_argument("--skip-societies", action="store_true")
    parser.add_argument("--skip-social", action="store_true", help="Keep the previous social snapshot without making social requests")
    parser.add_argument("--social-only", action="store_true", help="Collect social posts using the saved society directory")
    parser.add_argument("--directory-only", action="store_true", help="Refresh names without visiting profile pages")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.offline and args.social_only:
        parser.error("--offline already rebuilds saved social posts and reviewed events; do not combine with --social-only")
    now = parse_datetime(args.now) if args.now else datetime.now(timezone.utc)
    config = load_json(args.root / "data/sources.json", {})
    previous_events = load_json(args.root / "dist/data/events.json", {})
    reviewed = load_json(args.root / "data/reviewed-events.json", {"events": []})
    if args.social_only:
        events = {**previous_events, "updated_at": iso(now), "events": [e for e in previous_events.get("events", []) if not e.get("source_id", "").startswith("social-") and visible_today_or_future(e, now)], "sources": [s for s in previous_events.get("sources", []) if not s.get("id", "").startswith("social-")], "status": dict(previous_events.get("status", {}))}
        old_queue = load_json(args.root / "data/review-queue.json", {"candidates": []})
        queue = {"updated_at": iso(now), "candidates": [c for c in old_queue.get("candidates", []) if not c.get("source_id", "").startswith("social-")]}
    else:
        events, queue = refresh_events(config, previous_events, reviewed, now, offline=args.offline)
        if args.offline:
            old_queue = load_json(args.root / "data/review-queue.json", {"candidates": []})
            queue["candidates"] = [c for c in old_queue.get("candidates", []) if not c.get("source_id", "").startswith("social-")]
    if not args.offline and not args.skip_societies and not args.social_only:
        previous_societies = load_json(args.root / "dist/data/societies.json", {"updated_at": None, "societies": []})
        societies, directory_status = refresh_societies(config, previous_societies, now, directory_only=args.directory_only, workers=max(1, min(args.workers, 4)))
        events["sources"].append(directory_status)
        events["status"]["society_count"] = len(societies.get("societies", []))
        events["status"]["directory_status"] = directory_status["status"]
        if directory_status["status"] != "ok" and events["status"]["state"] == "ok":
            events["status"]["state"] = "partial"
        write_json(args.root / "dist/data/societies.json", societies)
    else:
        for source in previous_events.get("sources", []):
            if source.get("type") == "directory":
                source = {**source, "name": source.get("name", source.get("label", "Society directory")),
                          "parsed_count": source.get("parsed_count", source.get("society_count", 0))}
                if not any(s.get("id") == source.get("id") for s in events["sources"]):
                    events["sources"].append(source)
                events["status"]["society_count"] = source.get("society_count", 0)
                events["status"]["directory_status"] = source["status"]
    societies = load_json(args.root / "dist/data/societies.json", {"societies": []})
    try:
        from scripts.social_registry import apply_registry
        from scripts.social_coverage import build_coverage
    except ModuleNotFoundError:
        from social_registry import apply_registry
        from social_coverage import build_coverage
    registry = load_json(args.root / "data/society-accounts.json", {"accounts": []})
    effective_societies, registry_errors = apply_registry(societies.get("societies", []), registry, now)
    previous_social = load_json(args.root / "dist/data/social-posts.json", {"posts": [], "accounts": []})
    if args.skip_social or args.offline:
        social = previous_social
    else:
        social = refresh_social_sources(config.get("social", {}), effective_societies, previous_social, now)
    social["coverage"] = build_coverage(effective_societies, social, now)
    social["registry_errors"] = registry_errors
    write_json(args.root / "dist/data/social-posts.json", social)
    integrate_social(events, queue, social, now)
    write_json(args.root / "dist/data/events.json", events)
    write_json(args.root / "data/review-queue.json", queue)
    print(json.dumps(events["status"], indent=2))
    return 0


def merge_post_evidence(previous, incoming):
    """Prefer the latest post, reusing OCR only for exactly unchanged evidence.

    Keep the review requirement when another collector returns a thinner or
    edited version, so losing OCR cannot accidentally enable auto-publication.
    """
    if previous is None:
        return deepcopy(incoming)
    if str(incoming.get("observed_at") or "") < str(previous.get("observed_at") or ""):
        previous, incoming = incoming, previous
    result = deepcopy(incoming)
    unchanged = (str(previous.get("text") or "") == str(incoming.get("text") or "")
                 and previous.get("media_urls", []) == incoming.get("media_urls", []))
    if unchanged and "image_ocr" not in incoming and "image_ocr" in previous:
        result["image_ocr"] = deepcopy(previous["image_ocr"])
    if (previous.get("origin") == "apify" or previous.get("image_ocr")
            or previous.get("image_review_required") is True):
        result["image_review_required"] = True
        if not unchanged and "image_ocr" not in incoming:
            result["image_ocr"] = {"status": "invalidated", "coverage_complete": False,
                                   "text": "", "expected_images": len(incoming.get("media_urls") or []),
                                   "inspected_images": 0, "images": []}
    return result


def refresh_social_sources(config, societies, previous, now):
    try:
        from scripts.social_posts import collect_social_posts, canonical_account_url, canonical_post_url, platform_for
        from scripts.meta_posts import collect_meta_posts
        from scripts.social_registry import expand_accounts
    except ModuleNotFoundError:
        from social_posts import collect_social_posts, canonical_account_url, canonical_post_url, platform_for
        from meta_posts import collect_meta_posts
        from social_registry import expand_accounts
    def prior(provider):
        return {**previous, "posts": [p for p in previous.get("posts", []) if p.get("origin", "public_html") == provider],
                "accounts": [a for a in previous.get("accounts", []) if a.get("provider", "public_html") == provider]}
    expanded = expand_accounts(societies)
    registered = {url for row in expanded for platform in ("instagram", "facebook")
                  if (url := canonical_account_url(row.get(platform + "_url")))}
    cutoff = now.astimezone(timezone.utc) - timedelta(days=60)
    retained = []
    for post in previous.get("posts", []):
        if post.get("origin") != "apify":
            continue
        account = canonical_account_url(post.get("account_url"))
        url = canonical_post_url(post.get("source_url"))
        try:
            dated = parse_datetime(post.get("published_at") or post.get("observed_at"))
        except (ValueError, TypeError):
            continue
        if account in registered and url and platform_for(url) == platform_for(account) and cutoff <= dated <= now:
            retained.append({**post, "account_url": account, "source_url": url})
    public = collect_social_posts(expanded, config, prior("public_html"), now)
    meta = collect_meta_posts(expanded, config, prior("meta_api"), now)
    posts = {}
    old_posts = {p.get("source_url"): p for p in previous.get("posts", [])}
    for post in retained + public.get("posts", []) + meta.get("posts", []):
        # Prefer fresh official API data at equal observation times while
        # preserving OCR when neither the caption nor media has changed.
        if post.get("source_url"):
            url = post["source_url"]
            posts[url] = merge_post_evidence(posts.get(url, old_posts.get(url)), post)
    accounts = [{**a, "provider": "public_html"} for a in public.get("accounts", [])] + [{**a, "provider": "meta_api"} for a in meta.get("accounts", [])]
    accounts.extend(a for a in previous.get("accounts", []) if a.get("provider") == "apify" and canonical_account_url(a.get("url")) in registered)
    return {**previous, "updated_at": iso(now), "posts": sorted(posts.values(), key=lambda p: p.get("published_at") or "", reverse=True),
            "accounts": accounts, "status": {**previous.get("status", {}), "public": public.get("status", {}), "meta": meta.get("status", {}),
            "post_count": len(posts), "account_count": len({canonical_account_url(a.get("url")) or str(a.get("url", "")).lower().rstrip("/") for a in accounts}),
            "note": "Public collection and configured provider snapshots are merged. Apify runs are separately budgeted; retained posts do not imply a new paid run."}}


def integrate_social(events, queue, social, now):
    try:
        from scripts.social_events import posts_to_events
    except ModuleNotFoundError:
        from social_events import posts_to_events
    found, pending = posts_to_events(social.get("posts", []), now, food_evidence)
    queue["candidates"] = [c for c in queue.get("candidates", []) if not c.get("source_id", "").startswith("social-")] + pending
    identity = lambda e: (e["source_url"].rstrip("/"), parse_datetime(e["starts_at"]).astimezone(timezone.utc))
    unique = {identity(e): e for e in events.get("events", []) if not e.get("source_id", "").startswith("social-")}
    for event in found:
        unique.setdefault(identity(event), event)
    events["events"] = sorted(unique.values(), key=lambda e: parse_datetime(e["starts_at"]))
    events["sources"] = [s for s in events.get("sources", []) if not s.get("id", "").startswith("social-")]
    for platform in ("instagram", "facebook"):
        accounts = [a for a in social.get("accounts", []) if a.get("platform") == platform and a.get("provider") == "public_html"]
        posts = [p for p in social.get("posts", []) if p.get("platform") == platform]
        observed = [p for p in posts if p.get("observed_at") == social.get("updated_at")]
        checked = [a for a in accounts if a.get("last_attempt_at") == social.get("updated_at")]
        accessible = [a for a in accounts if a.get("posts_collected", 0) > 0]
        healthy = accounts and len(checked) == len(accounts) and all(a.get("status") == "ok" for a in accounts)
        events["sources"].append({"id": "social-" + platform, "type": "social", "name": platform.capitalize() + " society posts",
            "url": "https://www." + platform + ".com/", "status": "ok" if healthy else "partial" if posts else "unavailable",
            "parsed_count": len(observed), "posts_stored": len(posts), "account_count": len(accounts), "accounts_checked": len(checked), "accounts_with_posts": len(accessible),
            "last_attempt_at": social.get("updated_at"), "last_success_at": max((p.get("observed_at") or "" for p in posts), default=None)})
    events["status"].update(live_event_count=len(events["events"]), candidate_count=len(queue["candidates"]),
        social_post_count=len(social.get("posts", [])), social_account_count=social.get("status", {}).get("account_count", 0),
        social_ingestion="Collected Instagram/Facebook captions and image text are reviewed for food events; full-directory coverage and provider status are in social-posts.json.")
    coverage = social.get("coverage", {}).get("summary", {})
    events["status"]["society_coverage"] = coverage
    source_states = [s.get("status", "error") for s in events["sources"] if s.get("type") != "directory"]
    failed = sum(s in ("error", "unavailable") for s in source_states)
    partial = sum(s == "partial" for s in source_states)
    events["status"].update(source_count=len(source_states), successful_sources=len(source_states) - failed,
        failed_sources=failed, partial_sources=partial)
    incomplete = any(coverage.get(key, 0) for key in ("societies_needing_accounts", "societies_needing_attention", "societies_stale"))
    events["status"]["state"] = "error" if failed == len(source_states) and not events["events"] else "partial" if failed or partial or incomplete or social.get("registry_errors") or events["status"].get("manual_errors") or events["status"].get("directory_status", "ok") != "ok" else "ok"


if __name__ == "__main__":
    raise SystemExit(main())
