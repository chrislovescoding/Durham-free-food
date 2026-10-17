"""Conservative public-source ingestion. Run from any directory with Python 3.11+."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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
FOODS = r"(?:pizza|food|snacks?|refreshments?|lunch|dinner|breakfast|buffet|meals?|sandwich(?:es)?|cake|cookies|biscuits|doughnuts|donuts|pastries|noodles?|curry|soup)"
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
        for key in ("instagram_url", "facebook_url", "socials_checked_at"):
            if key in previous_by_url.get(society["url"], {}):
                society[key] = previous_by_url[society["url"]][key]
    failures = []
    if not directory_only:
        def discover(society):
            time.sleep(0.25)
            html = fetch(society["url"])
            return social_links(html, society["url"])

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(discover, society): society for society in societies}
            for future in as_completed(futures):
                society = futures[future]
                try:
                    # A successful profile fetch replaces old accounts if removed.
                    links = future.result()
                    society.pop("instagram_url", None)
                    society.pop("facebook_url", None)
                    society.update(links, socials_checked_at=stamp)
                except Exception as error:
                    failures.append({"url": society["url"], "error": str(error)})
    payload = {"updated_at": stamp, "source_url": url, "societies": societies}
    status.update(status="partial" if failures else "ok", last_success_at=stamp, society_count=len(societies),
                  parsed_count=len(societies),
                  profiles_failed=len(failures), profile_errors=failures,
                  social_account_count=sum(bool(s.get("instagram_url") or s.get("facebook_url")) for s in societies))
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


def refresh_events(config: dict, previous: dict, reviewed: dict, now: datetime) -> tuple[dict, dict]:
    events, candidates, sources = [], [], []
    previous_status = {source["id"]: source for source in previous.get("sources", [])}
    for source in config.get("feeds", []):
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
              "social_ingestion": "Public social account links are discovered; Instagram and Facebook posts are not automatically ingested."}
    payload = {"updated_at": iso(now), "timezone": "Europe/London", "events": published, "sources": sources, "status": status}
    review_queue = {"updated_at": iso(now), "candidates": candidates}
    return payload, review_queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--now", help="ISO timestamp, for reproducible checks")
    parser.add_argument("--skip-societies", action="store_true")
    parser.add_argument("--directory-only", action="store_true", help="Refresh names without visiting profile pages")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    now = parse_datetime(args.now) if args.now else datetime.now(timezone.utc)
    config = load_json(args.root / "data/sources.json", {})
    previous_events = load_json(args.root / "dist/data/events.json", {})
    reviewed = load_json(args.root / "data/reviewed-events.json", {"events": []})
    events, queue = refresh_events(config, previous_events, reviewed, now)
    if not args.skip_societies:
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
                events["sources"].append(source)
                events["status"]["society_count"] = source.get("society_count", 0)
                events["status"]["directory_status"] = source["status"]
    write_json(args.root / "dist/data/events.json", events)
    write_json(args.root / "data/review-queue.json", queue)
    print(json.dumps(events["status"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
