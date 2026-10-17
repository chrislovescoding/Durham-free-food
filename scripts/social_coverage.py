"""Evidence-based society coverage, including societies with no linked accounts.

"Collected" means at least one real post was observed within the last 36 hours;
it does not imply a complete timeline or coverage of every linked account.
This module reads stored results only and never makes network requests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

try:
    from scripts.social_posts import canonical_account_url, canonical_post_url, platform_for
except ModuleNotFoundError:
    from social_posts import canonical_account_url, canonical_post_url, platform_for


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _observed(value, now):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > now:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _account_key(value, expected_platform=None):
    """Return a safe matching URL and whether the collector supports it.

    Preserve unsupported social links such as Facebook groups in coverage.
    They must remain visible gaps, rather than becoming 'missing accounts'.
    """
    try:
        platform = platform_for(value)
        if not platform or (expected_platform and platform != expected_platform):
            return None, False
        normalized = canonical_account_url(value)
        if normalized:
            return normalized, True
        parsed = urlparse(value)
        # Unsupported routes are display-only. Drop queries and fragments,
        # and never carry arbitrary hosts or credentials into coverage links.
        path = "/" + parsed.path.strip("/").lower() + "/"
        return "https://www." + platform + ".com" + path, False
    except (TypeError, ValueError):
        return None, False


def _collection_state(posts, now, cutoff):
    observations = [observed for post in posts if (observed := _observed(post.get("observed_at"), now))]
    latest = max(observations, default=None)
    if latest is None:
        return "needs_attention", None
    return ("collected" if latest >= cutoff else "stale"), _iso(latest)


def _society_accounts(society):
    # Prefer richer discovered/reviewed entries when a legacy field repeats the
    # same account. Canonical deduplication happens in the coverage row builder.
    extra = society.get("social_accounts")
    if isinstance(extra, list):
        for account in extra:
            if isinstance(account, dict) and account.get("platform") in {"instagram", "facebook"}:
                yield account
    for platform in ("instagram", "facebook"):
        if society.get(platform + "_url"):
            yield {"platform": platform, "url": society[platform + "_url"]}


def build_coverage(societies: list, social: dict, now: datetime) -> dict:
    """Build one coverage row for every input society, retaining input order.

    Posts match canonical account URLs, not names. Shared accounts contribute
    to each linked society, but summary.post_count counts each post only once.
    Missing timestamps and profile descriptions never establish collection.
    """
    if now.tzinfo is None:
        raise ValueError("Coverage requires a timezone-aware current time")
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(hours=36)
    providers_by_account = {}
    for record in social.get("accounts", []):
        key, _ = _account_key(record.get("url"), record.get("platform"))
        if not key:
            continue
        provider = record.get("provider") or record.get("origin") or "public_html"
        provider_status = {"provider": provider, "status": record.get("status", "unknown"),
                           "last_attempt_at": record.get("last_attempt_at"),
                           "last_success_at": record.get("last_success_at"),
                           "posts_collected": record.get("posts_collected", 0)}
        if record.get("error"):
            provider_status["error"] = record["error"]
        providers_by_account.setdefault(key, {})[provider] = provider_status

    posts_by_account = {}
    for post in social.get("posts", []):
        account_key, supported = _account_key(post.get("account_url"), post.get("platform"))
        try:
            post_key = canonical_post_url(post.get("source_url"))
        except (TypeError, ValueError):
            post_key = None
        # Only concrete, platform-matching post permalinks count. A successful
        # profile fetch or profile description is not post collection evidence.
        if not supported or not post_key or platform_for(post_key) != platform_for(account_key):
            continue
        existing = posts_by_account.setdefault(account_key, {}).get(post_key)
        observation = _observed(post.get("observed_at"), now)
        old_observation = _observed(existing.get("observed_at"), now) if existing else None
        if existing is None or (observation and (not old_observation or observation > old_observation)):
            posts_by_account[account_key][post_key] = post

    rows, all_posts = [], set()
    counts = {"missing_accounts": 0, "needs_attention": 0, "stale": 0, "collected": 0}
    for society in societies:
        accounts, seen_accounts, society_posts = [], set(), {}
        for configured_account in _society_accounts(society):
            platform = configured_account["platform"]
            key, supported = _account_key(configured_account.get("url"), platform)
            if not key or key in seen_accounts:
                continue
            seen_accounts.add(key)
            account_posts = posts_by_account.get(key, {}) if supported else {}
            society_posts.update(account_posts)
            account_state, account_latest = _collection_state(account_posts.values(), now, cutoff)
            account = {"platform": platform, "url": key,
                       "status": account_state if supported else "unsupported",
                       "posts_stored": len(account_posts), "latest_observation_at": account_latest,
                       "providers": list(providers_by_account.get(key, {}).values())}
            for field in ("evidence_url", "method"):
                if configured_account.get(field):
                    account[field] = configured_account[field]
            accounts.append(account)
        if accounts:
            status, latest = _collection_state(society_posts.values(), now, cutoff)
        else:
            status, latest = "missing_accounts", None
        all_posts.update(society_posts)
        counts[status] += 1
        row = {"name": society.get("name", "Unknown society"), "url": society.get("url"),
               "account_count": len(accounts), "posts_stored": len(society_posts),
               "latest_observation_at": latest, "status": status, "accounts": accounts}
        for field in ("discovery_status", "discovery_errors"):
            if field in society:
                row[field] = society[field]
        rows.append(row)

    return {"updated_at": _iso(now), "summary": {
        "total_societies": len(rows), "societies_with_accounts": len(rows) - counts["missing_accounts"],
        "societies_with_recent_posts": counts["collected"], "societies_needing_accounts": counts["missing_accounts"],
        "societies_needing_attention": counts["needs_attention"], "societies_stale": counts["stale"],
        "post_count": len(all_posts)}, "societies": rows}
