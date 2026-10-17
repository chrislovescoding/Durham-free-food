"""Optional, read-only Meta API collection; never scrapes logged-in sessions.

Pass the social configuration block, with a ``meta`` object. The API version
must be pinned in META_GRAPH_API_VERSION. Tokens are sent in headers only.
Instagram Business Discovery supports public professional accounts, subject to
the caller's granted permissions; it cannot read personal/private accounts.
Facebook URLs require explicit ``facebook_page_ids`` mappings and API access.

References checked 2026-10-17:
https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery
https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media
https://developers.facebook.com/docs/graph-api/reference/page/feed/
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from urllib.parse import parse_qs, urlparse

import requests


GRAPH = "https://graph.facebook.com"
IG_FIELDS = "id,caption,permalink,timestamp,media_url,thumbnail_url"
FB_FIELDS = "id,message,permalink_url,created_time,full_picture,is_published,from"
HANDLE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]{0,29}\Z")
NODE_ID = re.compile(r"[0-9]{1,40}\Z")
CURSOR = re.compile(r"[A-Za-z0-9_+/=-]{1,2048}\Z")


class MetaError(Exception):
    """Only fixed, credential-free messages may be stored or displayed."""

    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _stamp(parsed) if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def _bounded(value, default, maximum):
    return min(max(value, 1), maximum) if isinstance(value, int) and not isinstance(value, bool) else default


def _https_url(value, hosts=None):
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        parsed = urlparse(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return None
        if hosts and parsed.hostname.lower() not in hosts:
            return None
        if any(key.lower() in {"access_token", "token", "authorization"} for key in parse_qs(parsed.query)):
            return None
        return value
    except ValueError:
        return None


def _profile(value, platform):
    hosts = {"instagram.com", "www.instagram.com"} if platform == "instagram" else {
        "facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "www.fb.com"}
    if not _https_url(value, hosts):
        return None, None
    parsed = urlparse(value)
    path = parsed.path.strip("/")
    if platform == "instagram":
        if not HANDLE.fullmatch(path) or path.lower() in {"p", "reel", "reels", "stories", "explore", "accounts"}:
            return None, None
        return "https://www.instagram.com/" + path.lower() + "/", path.lower()
    if path == "profile.php":
        identity = parse_qs(parsed.query).get("id", [""])[0]
        if NODE_ID.fullmatch(identity):
            return "https://www.facebook.com/profile.php?id=" + identity, None
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,150}", path) and path.lower() not in {"groups", "events", "login", "watch", "share", "sharer.php"}:
        return "https://www.facebook.com/" + path + "/", None
    return None, None


def _request(url, *, params, headers):
    # Redirects are deliberately disabled so credentials never leave Graph.
    response = requests.get(url, params=params, headers=headers, timeout=(10, 25),
                            allow_redirects=False, stream=True)
    with response:
        if response.status_code == 429:
            raise MetaError("rate_limited", "Meta rate limit reached; retry on the next scheduled refresh.")
        if response.status_code == 401:
            raise MetaError("needs_credentials", "Meta access token is invalid or expired.")
        if 300 <= response.status_code < 400:
            raise MetaError("error", "Meta API redirected unexpectedly; request stopped.")
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > 2_000_000:
                raise MetaError("error", "Meta API response exceeded the size limit.")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise MetaError("error", "Meta API returned an invalid response.") from None
        if response.status_code >= 400 and not (isinstance(result, dict) and result.get("error")):
            raise MetaError("error", "Meta API request failed.")
        return result


def _checked_response(value):
    if not isinstance(value, dict):
        raise MetaError("error", "Meta API returned an invalid response.")
    if value.get("error"):
        error = value["error"] if isinstance(value["error"], dict) else {}
        code = error.get("code")
        if code == 190:
            raise MetaError("needs_credentials", "Meta access token is invalid or expired.")
        if code in (10, 200):
            raise MetaError("permission_denied", "Meta has not granted access to this account or operation.")
        if code == 100:
            raise MetaError("unsupported", "Account or fields unavailable to this token; verify account type, access and API version.")
        if code in (4, 17, 32, 613):
            raise MetaError("rate_limited", "Meta rate limit reached; retry on the next scheduled refresh.")
        raise MetaError("error", "Meta API rejected the request.")
    return value


def _next_cursor(edge, expected_url, *, instagram):
    paging = edge.get("paging") or {}
    if not isinstance(paging, dict):
        raise MetaError("error", "Meta API returned invalid pagination.")
    next_url = paging.get("next")
    if next_url:
        # A returned token is discarded. Only a cursor is extracted, then the
        # next request is reconstructed against the original account endpoint.
        try:
            parsed, expected = urlparse(next_url), urlparse(expected_url)
            if (parsed.scheme != "https" or parsed.netloc != "graph.facebook.com"
                    or parsed.path != expected.path or parsed.fragment):
                raise ValueError()
            cursor = parse_qs(parsed.query).get("after", [None])[0]
        except (TypeError, ValueError):
            raise MetaError("error", "Meta API returned an unsafe pagination URL.") from None
    elif instagram:
        cursors = paging.get("cursors") or {}
        cursor = cursors.get("after") if isinstance(cursors, dict) else None
    else:
        # Facebook cursors alone do not indicate that another page exists.
        return None
    if cursor is not None and (not isinstance(cursor, str) or not CURSOR.fullmatch(cursor)):
        raise MetaError("error", "Meta API returned an invalid pagination cursor.")
    return cursor


def _post(item, account, now, token, page_id=None):
    if not isinstance(item, dict):
        return None
    platform = account["platform"]
    if platform == "facebook":
        # /feed can include visitors' posts and unpublished content. Publish
        # only explicit public posts authored by the configured society Page.
        if item.get("is_published") is not True or (item.get("from") or {}).get("id") != page_id:
            return None
        source = _https_url(item.get("permalink_url"), {"www.facebook.com", "facebook.com", "m.facebook.com"})
        text, published = item.get("message", ""), item.get("created_time")
        media = [item.get("full_picture")]
    else:
        source = _https_url(item.get("permalink"), {"www.instagram.com", "instagram.com"})
        text, published = item.get("caption", ""), item.get("timestamp")
        media = [item.get("media_url"), item.get("thumbnail_url")]
    if not source or not isinstance(text, str) or token in source:
        return None
    # Standardize Instagram reels and Facebook story permalinks for deduping.
    parsed = urlparse(source)
    if platform == "instagram":
        match = re.fullmatch(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)/?", parsed.path)
        if not match:
            return None
        source = "https://www.instagram.com/p/" + match[1] + "/"
    else:
        query = parse_qs(parsed.query)
        match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/posts/([A-Za-z0-9_-]+)/?", parsed.path)
        if match:
            source = "https://www.facebook.com/" + match[1] + "/posts/" + match[2] + "/"
        elif parsed.path in {"/story.php", "/permalink.php"} and query.get("id") and query.get("story_fbid"):
            owner, post_id = query["id"][0], query["story_fbid"][0]
            if not NODE_ID.fullmatch(owner) or not re.fullmatch(r"[A-Za-z0-9_-]{1,250}", post_id):
                return None
            source = "https://www.facebook.com/" + owner + "/posts/" + post_id + "/"
        else:
            source = "https://www.facebook.com" + parsed.path
    return {"id": "social-" + hashlib.sha256(source.encode()).hexdigest()[:14],
            "platform": platform, "account_url": account["url"], "society": account["society"],
            "source_url": source, "text": text[:20000].replace(token, "[redacted]"),
            "published_at": _timestamp(published), "observed_at": _stamp(now),
            "media_urls": list(dict.fromkeys(url for url in media if _https_url(url) and token not in url)),
            "origin": "meta_api"}


def _society_accounts(society):
    extra = society.get("social_accounts")
    if isinstance(extra, list):
        for account in extra:
            if isinstance(account, dict) and account.get("platform") in {"instagram", "facebook"}:
                yield account["platform"], account.get("url")
    for platform in ("instagram", "facebook"):
        if society.get(platform + "_url"):
            yield platform, society[platform + "_url"]


def collect_meta_posts(societies, config, previous, now, environ=None, fetch=None):
    """Return {updated_at, posts, accounts, status}; fetch returns decoded JSON.

    config['meta']: enabled, facebook_page_ids (profile URL -> numeric ID),
    instagram_handles (optional allowlist), max_accounts (100 default; 1000 max),
    max_pages_per_account (3 max), posts_per_account (100 max), retention_days.
    API responses enforce Meta permissions; this function requests no grants.
    """
    environ = os.environ if environ is None else environ
    fetch = fetch or _request
    settings = config.get("meta") or {}
    stamp = _stamp(now)
    token = environ.get("META_ACCESS_TOKEN", "").strip()
    version = environ.get("META_GRAPH_API_VERSION", "").strip()
    instagram_id = environ.get("META_INSTAGRAM_USER_ID", "").strip()
    enabled = settings.get("enabled") is True
    max_accounts = _bounded(settings.get("max_accounts"), 100, 1000)
    max_pages = _bounded(settings.get("max_pages_per_account"), 2, 3)
    post_limit = _bounded(settings.get("posts_per_account"), 25, 100)
    page_size = min(post_limit, 25)
    cutoff = now - timedelta(days=_bounded(settings.get("retention_days"), 30, 90))
    history = previous.get("posts", []) if isinstance(previous, dict) else []
    posts = {}
    old_accounts = {item.get("url"): item for item in previous.get("accounts", [])
                    if item.get("origin") == "meta_api"} if isinstance(previous, dict) else {}
    page_ids = {}
    for url, identity in (settings.get("facebook_page_ids") or {}).items():
        normalized, _ = _profile(url, "facebook")
        if normalized and isinstance(identity, str) and NODE_ID.fullmatch(identity):
            page_ids[normalized] = identity
    allowed_handles = settings.get("instagram_handles")
    if isinstance(allowed_handles, list):
        allowed_handles = {str(handle).lower() for handle in allowed_handles}
    else:
        allowed_handles = None
    society_list = societies.get("societies", []) if isinstance(societies, dict) else societies
    candidates = [(society, platform, raw_url) for society in society_list
                  for platform, raw_url in _society_accounts(society) if raw_url]
    # Retention protects against temporary fetch failures, not removal from the
    # configured source set. Recompute eligibility before merging any history.
    eligible_accounts = set()
    if enabled:
        for _, platform, raw_url in candidates:
            url, handle = _profile(raw_url, platform)
            if not url:
                continue
            if platform == "facebook" and url not in page_ids:
                continue
            if platform == "instagram" and allowed_handles is not None and handle not in allowed_handles:
                continue
            eligible_accounts.add(url)
    for post in history:
        if post.get("origin") == "meta_api" and post.get("account_url") in eligible_accounts:
            timestamp = _timestamp(post.get("published_at") or post.get("observed_at"))
            if timestamp and datetime.fromisoformat(timestamp.replace("Z", "+00:00")) >= cutoff:
                posts[post["source_url"]] = post
    # Missing/old attempts go first; sorting is stable for equally old records.
    # Limits therefore defer accounts fairly across runs instead of always
    # selecting the same alphabetical prefix of the society directory.
    def last_attempt(candidate):
        _, platform, raw_url = candidate
        url, _ = _profile(raw_url, platform)
        return _timestamp(old_accounts.get(url, {}).get("last_attempt_at")) or ""

    candidates.sort(key=last_attempt)
    accounts, seen_accounts, requests_made, attempted = [], set(), 0, 0
    stop_reason = None
    for society, platform, raw_url in candidates:
        url, handle = _profile(raw_url, platform)
        # Keep unsupported groups/post links visible in collection health,
        # but do not persist malformed URLs that could embed secrets.
        supported_profile = bool(url)
        if not url:
            hosts = {"instagram.com", "www.instagram.com"} if platform == "instagram" else {
                "facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "www.fb.com"}
            if not _https_url(raw_url, hosts) or (token and token in raw_url):
                continue
            parsed = urlparse(raw_url)
            url = "https://www." + platform + ".com" + parsed.path
        if url in seen_accounts:
            continue
        seen_accounts.add(url)
        account = {"url": url, "platform": platform, "society": society.get("name", "Unknown society"),
                   "status": "disabled", "last_attempt_at": old_accounts.get(url, {}).get("last_attempt_at"),
                   "last_success_at": old_accounts.get(url, {}).get("last_success_at"), "posts_collected": 0,
                   "origin": "meta_api"}
        accounts.append(account)
        if not enabled:
            continue
        if not supported_profile:
            account.update(status="unsupported", error="This URL is not a supported Instagram professional profile or Facebook Page.")
            continue
        if platform == "facebook" and url not in page_ids:
            account.update(status="unmapped", error="Configure this Facebook Page's numeric ID and grant API access.")
            continue
        if platform == "instagram" and allowed_handles is not None and handle not in allowed_handles:
            account.update(status="unsupported", error="Account is outside the configured Instagram allowlist.")
            continue
        if not token or (platform == "instagram" and not NODE_ID.fullmatch(instagram_id)):
            account.update(status="needs_credentials", error="Configure a Meta access token and, for Instagram, a caller Instagram user ID.")
            continue
        if not re.fullmatch(r"v[1-9][0-9]?\.0", version):
            account.update(status="needs_configuration", error="Pin META_GRAPH_API_VERSION to a supported version, such as vNN.0.")
            continue
        if stop_reason:
            message = "Remaining Meta requests deferred after a rate limit." if stop_reason == "rate_limited" else "Remaining Meta requests deferred because the access token is invalid or expired."
            account.update(status="deferred", error=message)
            continue
        if attempted >= max_accounts:
            account.update(status="deferred", error="Account limit reached for this refresh.")
            continue
        attempted += 1
        account["last_attempt_at"] = stamp
        page_id = page_ids.get(url)
        endpoint = GRAPH + "/" + version + "/" + (instagram_id if platform == "instagram" else page_id + "/feed")
        cursor, seen_cursors, batch = None, set(), {}
        try:
            for _ in range(max_pages):
                if platform == "instagram":
                    after = ".after(" + cursor + ")" if cursor else ""
                    fields = "business_discovery.username(" + handle + "){media.limit(" + str(page_size) + ")" + after + "{" + IG_FIELDS + "}}"
                    params = {"fields": fields}
                else:
                    params = {"fields": FB_FIELDS, "limit": page_size}
                    if cursor:
                        params["after"] = cursor
                requests_made += 1
                response = _checked_response(fetch(endpoint, params=params, headers={"Authorization": "Bearer " + token}))
                if platform == "instagram":
                    discovery = response.get("business_discovery")
                    if not isinstance(discovery, dict) or not isinstance(discovery.get("media"), dict):
                        raise MetaError("unsupported", "Meta did not return accessible public professional-account media.")
                    edge = discovery["media"]
                else:
                    edge = response
                if not isinstance(edge.get("data"), list):
                    raise MetaError("error", "Meta API returned invalid media data.")
                for item in edge["data"][:100]:
                    post = _post(item, account, now, token, page_id)
                    if not post:
                        continue
                    if post["published_at"] and datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")) < cutoff:
                        continue
                    batch[post["source_url"]] = post
                    if len(batch) >= post_limit:
                        break
                if len(batch) >= post_limit or not edge["data"]:
                    break
                cursor = _next_cursor(edge, endpoint, instagram=platform == "instagram")
                if not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
            posts.update(batch)
            account.update(status="ok", posts_collected=len(batch), last_success_at=stamp)
        except MetaError as error:
            posts.update(batch)
            account.update(status=error.status, error=str(error), posts_collected=len(batch))
            if error.status in {"rate_limited", "needs_credentials"}:
                stop_reason = error.status
        except Exception:
            # requests exceptions can include query strings or credentials;
            # never persist exception text, response bodies or request URLs.
            posts.update(batch)
            account.update(status="error", error="Meta API request failed; check connectivity and account configuration.", posts_collected=len(batch))
    states = {account["status"] for account in accounts}
    state = "disabled" if not enabled else ("no_accounts" if not accounts else "ok" if states == {"ok"} else "partial" if "ok" in states else "needs_credentials" if "needs_credentials" in states else "unavailable")
    summary = {"id": "social-meta", "type": "social_posts", "label": "Meta Graph API", "status": state,
               "last_attempt_at": stamp if attempted else max((account["last_attempt_at"] for account in accounts if account.get("last_attempt_at")), default=None),
               "accounts_checked": attempted, "request_count": requests_made,
               "posts_collected": sum(account["posts_collected"] for account in accounts),
               "parsed_count": sum(account["posts_collected"] for account in accounts)}
    if stop_reason:
        summary["stopped_after"] = stop_reason
    return {"updated_at": stamp, "posts": list(posts.values())[:5000], "accounts": accounts, "status": summary}
