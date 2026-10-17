"""Validate model extractions and merge them without replacing human reviews."""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata
from urllib.parse import urlparse, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


LONDON = ZoneInfo("Europe/London")
STRING = {"type": "string"}
NULLABLE_STRING = {"type": ["string", "null"]}
EVIDENCE_FIELDS = ("food", "date", "time", "location", "eligibility")
EVENT_PROPERTIES = {
    "title": STRING, "starts_at": STRING, "ends_at": NULLABLE_STRING,
    "location": STRING, "food": STRING, "food_is_free": {"type": "boolean"},
    "price": {"type": ["number", "null"]},
    "signup_required": {"type": ["boolean", "null"]},
    "signup_deadline": NULLABLE_STRING, "eligibility": NULLABLE_STRING,
    "dietary": {"type": "array", "items": STRING}, "description": STRING,
    "evidence": {"type": "object", "additionalProperties": False,
                 "properties": {key: NULLABLE_STRING if key == "eligibility" else STRING for key in EVIDENCE_FIELDS},
                 "required": list(EVIDENCE_FIELDS)},
    "needs_review": {"type": "boolean"},
}
EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": EVENT_PROPERTIES, "required": list(EVENT_PROPERTIES)}},
        "review_reason": NULLABLE_STRING,
    },
    "required": ["events", "review_reason"],
}


def evidence_signature(post):
    """Identify the post evidence; expiring CDN query strings are not edits."""
    def canonical(value):
        parts = urlsplit(str(value or ""))
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    value = {
        "text": post.get("text", ""), "published_at": post.get("published_at"),
        "source_url": canonical(post.get("source_url")), "society": post.get("society"),
        "media_urls": [canonical(url) for url in post.get("media_urls", [])],
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _timestamp(value):
    if not isinstance(value, str) or not re.search(r"T\d{2}:\d{2}", value):
        raise ValueError("An explicit date and time are required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("An explicit UTC offset is required")
    return parsed


def _text(value, field, *, nullable=False, allow_empty=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 6000 or (not value.strip() and not allow_empty):
        raise ValueError(f"Missing or invalid {field}")
    return value.strip()


def _queue(post, reason, index=None):
    identity = str(post.get("id", "")) + ":" + str(index) + ":" + reason
    return {
        "id": "ai-review-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
        "source_id": "ai-instagram", "kind": "ai_extraction_review",
        "post_id": post.get("id"), "source_url": post.get("source_url"),
        "society": post.get("society"), "reason": reason,
    }


def _validate_event(record, post, now):
    if not isinstance(record, dict) or set(record) != set(EVENT_PROPERTIES):
        raise ValueError("Event fields do not match the extraction schema")
    if record["needs_review"] is not False:
        raise ValueError("The model flagged uncertain event details")
    if record["food_is_free"] is not True:
        raise ValueError("The post does not explicitly confirm free food")
    result = {key: _text(record[key], key) for key in ("title", "location", "food")}
    result["description"] = _text(record["description"], "description", allow_empty=True)
    eligibility = _text(record["eligibility"], "eligibility", nullable=True)
    evidence = record["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_FIELDS):
        raise ValueError("Source evidence is required for food, date, time and location")
    evidence = {key: _text(evidence[key], "evidence." + key, nullable=key == "eligibility") for key in EVIDENCE_FIELDS}
    if eligibility and not evidence["eligibility"]:
        raise ValueError("Eligibility needs its own source evidence")
    if evidence["eligibility"] and not eligibility:
        raise ValueError("Source eligibility conditions must be shown")
    starts = _timestamp(record["starts_at"])
    ends = _timestamp(record["ends_at"]) if record["ends_at"] is not None else None
    deadline = _timestamp(record["signup_deadline"]) if record["signup_deadline"] is not None else None
    if ends is not None and ends <= starts:
        raise ValueError("Event end must follow its start")
    if deadline is not None and deadline > starts:
        raise ValueError("Signup deadline cannot follow event start")
    price = record["price"]
    if price is not None and (isinstance(price, bool) or not isinstance(price, (float, int)) or not math.isfinite(price) or price < 0):
        raise ValueError("Entry price must be a nonnegative GBP number or null")
    signup = record["signup_required"]
    if signup is not None and not isinstance(signup, bool):
        raise ValueError("Signup requirement must be a boolean or null")
    dietary = record["dietary"]
    if not isinstance(dietary, list) or len(dietary) > 20:
        raise ValueError("Dietary labels must be a list")
    dietary = [_text(item, "dietary") for item in dietary]
    source_url = _text(post.get("source_url"), "source_url")
    parsed_url = urlparse(source_url)
    if (parsed_url.scheme != "https" or parsed_url.hostname not in {"instagram.com", "www.instagram.com"}
            or not re.fullmatch(r"/(?:p|reel|tv)/[A-Za-z0-9_-]+/?", parsed_url.path)
            or parsed_url.username or parsed_url.password or parsed_url.port not in (None, 443)):
        raise ValueError("A canonical Instagram post URL is required")
    source_url = "https://www.instagram.com" + parsed_url.path.rstrip("/") + "/"
    post_id = _text(post.get("id"), "post_id")
    society = _text(post.get("society"), "society")
    if post.get("platform") != "instagram":
        raise ValueError("This extraction pipeline requires Instagram posts")
    # Any restriction appears on the card, not just in its details dialog.
    if eligibility and eligibility.casefold() not in result["food"].casefold():
        result["food"] += " · " + eligibility
    identity = source_url + starts.astimezone(timezone.utc).isoformat()
    result.update(
        id="ai-instagram-" + hashlib.sha256(identity.encode()).hexdigest()[:18],
        source_id="ai-instagram", source_label="Instagram · AI extracted",
        post_id=post_id, society=society, source_url=source_url,
        starts_at=starts.isoformat(), ends_at=ends.isoformat() if ends else None,
        food_is_free=True, price=price, signup_required=signup,
        signup_deadline=deadline.isoformat() if deadline else None,
        eligibility=eligibility, dietary=dietary, evidence=evidence,
        verified_at=now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        post_published_at=post.get("published_at"),
        extraction={"method": "ai", "model": post.get("extraction_model"), "human_reviewed": False},
    )
    return result


def validate_extraction(payload, post, now, *, include_past=False):
    """Return (publishable events, review queue); never publish partial guesses."""
    events, queue = [], []
    if (not isinstance(payload, dict) or set(payload) != {"events", "review_reason"}
            or not isinstance(payload.get("events"), list) or len(payload["events"]) > 100
            or (payload["review_reason"] is not None and not isinstance(payload["review_reason"], str))):
        return [], [_queue(post, "Invalid structured extraction response")]
    if payload["review_reason"]:
        # A post-level uncertainty applies to every extracted event.
        return [], [_queue(post, payload["review_reason"][:1000])]
    seen = set()
    for index, record in enumerate(payload["events"]):
        try:
            event = _validate_event(record, post, now)
            if not include_past and _timestamp(event["starts_at"]).astimezone(LONDON).date() < now.astimezone(LONDON).date():
                continue
            if event["id"] not in seen:
                events.append(event)
                seen.add(event["id"])
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            queue.append(_queue(post, str(error), index))
    return events, queue


def merge_same_occurrences(records):
    """Collapse repeat posts only for the same society, instant and exact venue.

    Punctuation/case are insignificant in venue names; different rooms, dates
    and societies remain separate. Keep the chosen source's details intact.
    """
    groups = {}
    for record in records:
        location = ' '.join(re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', record['location']).casefold()).split())
        key = (record['society'], _timestamp(record['starts_at']).astimezone(timezone.utc), location)
        groups.setdefault(key, []).append(record)

    def priority(record):
        published = record.get('post_published_at') or record.get('verified_at')
        try:
            stamp = _timestamp(published).astimezone(timezone.utc)
        except (TypeError, ValueError):
            stamp = datetime.min.replace(tzinfo=timezone.utc)
        return record.get('source_id') == 'manual', stamp, record.get('source_url', '')

    merged = []
    for group in groups.values():
        ordered = sorted(group, key=priority, reverse=True)
        winner = deepcopy(ordered[0])
        winner.pop('corroborating_sources', None)
        other_sources = {}
        for other in ordered[1:]:
            url = other['source_url'].rstrip('/')
            if url != winner['source_url'].rstrip('/'):
                other_sources.setdefault(url, {key: deepcopy(other[key]) for key in
                    ('source_url', 'source_label', 'post_id', 'post_published_at', 'evidence') if key in other})
        if other_sources:
            winner['corroborating_sources'] = list(other_sources.values())
        merged.append(winner)
    return merged


def integrate_ai(events, queue, stored, now, posts=None, *, include_past=False):
    """Merge stored validated extractions; human entries always win duplicates."""
    def identity(event):
        return event["source_url"].rstrip("/"), _timestamp(event["starts_at"]).astimezone(timezone.utc)

    unique = {identity(event): event for event in events.get("events", []) if event.get("source_id") != "ai-instagram"}
    signatures = {post.get("id"): evidence_signature(post) for post in posts} if posts is not None else None
    processed_signatures = stored.get("processed_signatures", {})

    def fresh(item):
        return (signatures is None or bool(item.get("input_signature"))
                and item["input_signature"] == signatures.get(item.get("post_id")))

    # An editor can explicitly resolve an entire post, rather than accidentally
    # dismiss every occurrence after reviewing just one date on a term poster.
    resolved_urls = {event['source_url'].rstrip('/') for event in unique.values()
                     if event.get('source_id') == 'manual' and event.get('resolves_post_review') is True}
    pending = [item for item in stored.get("queue", []) if fresh(item)
               and str(item.get('source_url', '')).rstrip('/') not in resolved_urls]
    accepted_posts = {post_id for post_id in stored.get("processed_post_ids", [])
                      if signatures is None or (processed_signatures.get(post_id)
                                                and processed_signatures[post_id] == signatures.get(post_id))}
    stale = set(stored.get("processed_post_ids", [])) - accepted_posts
    for event in stored.get("events", []):
        try:
            if not fresh(event):
                stale.add(event.get("post_id"))
                continue
            if event.get("source_id") != "ai-instagram":
                raise ValueError("Stored extraction has invalid provenance")
            model_record = {key: event.get(key) for key in EVENT_PROPERTIES}
            model_record["needs_review"] = False
            post = {"id": event.get("post_id"), "society": event.get("society"), "source_url": event.get("source_url"),
                    "platform": "instagram", "published_at": event.get("post_published_at"),
                    "extraction_model": event.get("extraction", {}).get("model")}
            validated = _validate_event(model_record, post, now)
            verified = _timestamp(event.get("verified_at"))
            if verified > now:
                raise ValueError("Extraction timestamp cannot be in the future")
            validated["verified_at"] = event["verified_at"]
            if event.get("input_signature"):
                validated["input_signature"] = event["input_signature"]
            if not include_past and _timestamp(validated["starts_at"]).astimezone(LONDON).date() < now.astimezone(LONDON).date():
                continue
            key = identity(validated)
            if key not in unique or unique[key].get("source_id", "").startswith("social-"):
                unique[key] = validated
            accepted_posts.add(validated["post_id"])
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as error:
            pending.append(_queue(event if isinstance(event, dict) else {}, "Stored AI extraction: " + str(error)))
    events["events"] = sorted(merge_same_occurrences(unique.values()), key=lambda item: _timestamp(item["starts_at"]))
    queue["candidates"] = [item for item in queue.get("candidates", [])
                           if item.get("source_id") != "ai-instagram"
                           and not (item.get("source_id", "").startswith("social-")
                                    and item.get("post_id") in accepted_posts)] + pending
    events.setdefault("status", {}).update(
        live_event_count=len(events["events"]), candidate_count=len(queue["candidates"]),
        ai_event_count=sum(item.get("source_id") == "ai-instagram" for item in events["events"]),
        ai_review_count=len(pending), ai_processed_at=stored.get("updated_at"),
        ai_stale_post_count=len(stale),
    )
