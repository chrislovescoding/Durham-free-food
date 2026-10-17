"""Collect society-owned posts exposed in public Instagram/Facebook HTML.

No login, undocumented API calls, cookies, proxies or access-control workarounds.
An account's profile description is never treated as a post. Sparse logged-out
HTML is reported as unavailable coverage, not as evidence that no events exist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import requests


USER_AGENT = "DurhamFreeFood/0.2 (+https://github.com/chrislovescoding/Durham-free-food)"
MAX_HTML_BYTES = 4_000_000
EXCLUDED_BRANCHES = {"comments", "edge_media_to_comment", "edge_media_to_parent_comment", "comment", "replies", "feedback", "followers", "edge_followed_by", "edge_follow", "likers", "edge_liked_by"}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def platform_for(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    try:
        if parsed.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return "instagram"
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com", "fb.com", "www.fb.com"}:
        return "facebook"
    return None


def canonical_account_url(url: Any) -> str | None:
    platform = platform_for(url)
    if not platform:
        return None
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if platform == "instagram":
        if len(parts) != 1 or parts[0].lower() in {"p", "reel", "reels", "explore", "accounts", "direct", "stories", "about"}:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.]{1,64}", parts[0]):
            return None
        return "https://www.instagram.com/" + parts[0].lower() + "/"
    if parsed.path.rstrip("/") == "/profile.php":
        identity = parse_qs(parsed.query).get("id", [""])[0]
        return "https://www.facebook.com/" + identity + "/" if identity.isdigit() else None
    if len(parts) == 3 and parts[0].lower() in {"pages", "people"} and parts[2].isdigit():
        return "https://www.facebook.com/" + parts[2] + "/"
    # Facebook's named Page route ends in its stable numeric Page identity.
    # Match the complete path so posts and malformed routes cannot be profiles.
    named_page = re.fullmatch(r"/p/[A-Za-z0-9][A-Za-z0-9._-]{0,199}-([0-9]{1,40})/?", parsed.path, re.IGNORECASE)
    if named_page:
        return "https://www.facebook.com/" + named_page[1] + "/"
    if len(parts) != 1 or parts[0].lower() in {"login", "login.php", "home.php", "groups", "watch", "share", "sharer", "sharer.php", "posts", "reel", "reels", "permalink.php", "story.php", "help", "privacy", "photo.php"}:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", parts[0]):
        return None
    return "https://www.facebook.com/" + parts[0].lower() + "/"


def _unsupported_group_url(url: Any) -> str | None:
    """Keep a safe group reference for coverage reporting, never for fetching."""
    if platform_for(url) != "facebook":
        return None
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "groups" and parts[1] not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9._-]{1,200}", parts[1]):
        return "https://www.facebook.com/groups/" + parts[1].lower() + "/"
    return None


def canonical_post_url(url: Any) -> str | None:
    platform = platform_for(url)
    if not platform:
        return None
    parsed = urlparse(url)
    if platform == "instagram":
        match = re.fullmatch(r"/(?:p|reel|reels)/([A-Za-z0-9_-]{1,100})/?", parsed.path)
        return f"https://www.instagram.com/p/{match[1]}/" if match else None
    match = re.fullmatch(r"/([A-Za-z0-9._-]+)/posts/([A-Za-z0-9_-]{1,200})/?", parsed.path)
    if match:
        return f"https://www.facebook.com/{match[1].lower()}/posts/{match[2]}/"
    if parsed.path in {"/permalink.php", "/story.php"}:
        query = parse_qs(parsed.query)
        owner, post = query.get("id", [""])[0], query.get("story_fbid", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", post) and owner.isdigit():
            return f"https://www.facebook.com/{owner}/posts/{post}/"
    return None


def _timestamp(value: Any) -> str | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            result = datetime.fromtimestamp(number, timezone.utc)
        elif isinstance(value, str) and re.search(r"[T ]\d\d:\d\d", value):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None:
                return None
        else:
            return None
        return iso(result) if result.year >= 2004 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)[:12000]


def _documents(soup: BeautifulSoup):
    """Decode JSON data, including a few explicit assignment wrappers; never eval JS."""
    decoder = json.JSONDecoder()
    for script in soup.find_all("script", limit=300):
        raw = script.string or script.get_text()
        if not raw or len(raw) > MAX_HTML_BYTES:
            continue
        raw = raw.strip()
        try:
            if raw.startswith(("{", "[")):
                yield json.loads(raw)
                continue
            match = re.match(r"(?:window\.)?_(?:sharedData)\s*=\s*", raw)
            if match:
                value, end = decoder.raw_decode(raw[match.end():])
                if raw[match.end() + end:].strip() in {"", ";"}:
                    yield value
                continue
            match = re.match(r"window\.__additionalDataLoaded\(\s*['\"][^'\"]+['\"]\s*,\s*", raw)
            if match:
                value, end = decoder.raw_decode(raw[match.end():])
                if raw[match.end() + end:].strip() in {");", ")"}:
                    yield value
                continue
            # Instagram's ordinary public embed wraps JSON in ServerJS.handle.
            # Decode only the JSON argument, without executing the surrounding JS.
            if "ServerJS" in raw and "PolarisEmbed" in raw:
                match = re.search(r"\bs\.handle\(\s*(?=\{)", raw)
                if match:
                    value, _ = decoder.raw_decode(raw[match.end():])
                    yield value
        except (ValueError, TypeError, RecursionError):
            continue


def _walk(value: Any):
    stack, remaining = [(value, 0)], 15000
    while stack and remaining:
        current, depth = stack.pop()
        remaining -= 1
        if depth > 45:
            continue
        if isinstance(current, dict):
            yield current
            for key, child in current.items():
                if key.lower() not in EXCLUDED_BRANCHES and "comment" not in key.lower() and "follower" not in key.lower():
                    if key == "contextJSON" and isinstance(child, str):
                        try:
                            child = json.loads(child)
                        except (ValueError, RecursionError):
                            continue
                    stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current[:2000])


def _author_matches(author: Any, account_url: str) -> bool:
    expected = urlparse(account_url).path.strip("/")
    platform = platform_for(account_url)
    if isinstance(author, list):
        return bool(author) and _author_matches(author[0], account_url)
    if isinstance(author, str):
        return canonical_account_url(author) == account_url
    if not isinstance(author, dict):
        return False
    for field in ("url", "@id", "profile_url"):
        if canonical_account_url(author.get(field)) == account_url:
            return True
    username = author.get("username") or author.get("alternateName")
    if isinstance(username, str) and username.lstrip("@").lower() == expected:
        return True
    return platform == "facebook" and expected.isdigit() and str(author.get("id", author.get("identifier", ""))) == expected


def _belongs(url: str, author: Any, account_url: str, inherited: bool = False) -> bool:
    if platform_for(url) != platform_for(account_url):
        return False
    # Explicit conflicting authorship is never overridden by a matching URL path.
    if author is not None:
        if not _author_matches(author, account_url):
            return False
        if platform_for(url) == "facebook":
            url_owner = urlparse(url).path.split("/")[1].lower()
            expected = urlparse(account_url).path.strip("/")
            # Numeric Page IDs may be aliases of a slug verified by author.url;
            # a different named Page in a permalink is an explicit mismatch.
            return url_owner == expected or url_owner.isdigit()
        return True
    if platform_for(url) == "facebook":
        return urlparse(url).path.split("/")[1].lower() == urlparse(account_url).path.strip("/")
    return inherited


def _make_post(url: str, text: Any, published: Any, account: str, society: str, now: datetime, media: Any = None) -> dict | None:
    content = _clean_text(text)
    if not content:
        return None
    media_urls = []
    for item in media if isinstance(media, list) else [media]:
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl")
        if isinstance(item, str) and urlparse(item).scheme == "https" and urlparse(item).hostname:
            media_urls.append(item)
    return {"id": "social-" + hashlib.sha256(url.encode()).hexdigest()[:18],
            "platform": platform_for(account), "account_url": account, "society": society,
            "source_url": url, "text": content, "published_at": _timestamp(published),
            "observed_at": iso(now), "media_urls": list(dict.fromkeys(media_urls))[:5], "origin": "public_html"}


def _instagram_node(node: dict, account: str, society: str, now: datetime, inherited: bool = False) -> dict | None:
    code = node.get("shortcode") or node.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", code):
        return None
    url = f"https://www.instagram.com/p/{code}/"
    author = node.get("owner") or node.get("user")
    if not _belongs(url, author, account, inherited):
        return None
    caption = node.get("caption")
    if isinstance(caption, dict):
        caption = caption.get("text")
    if not isinstance(caption, str):
        caption_data = node.get("edge_media_to_caption")
        edges = caption_data.get("edges", []) if isinstance(caption_data, dict) else []
        edges = edges if isinstance(edges, list) else []
        caption = "\n".join(edge["node"]["text"] for edge in edges[:5] if isinstance(edge, dict) and isinstance(edge.get("node"), dict) and isinstance(edge["node"].get("text"), str))
    return _make_post(url, caption, node.get("taken_at_timestamp", node.get("taken_at", node.get("timestamp"))), account, society, now,
                      node.get("display_url") or node.get("thumbnail_src"))


def parse_public_posts(html: str, account_url: str, society: str, now: datetime) -> dict:
    """Return owned posts, potential permalink links, and public-page access state."""
    account = canonical_account_url(account_url)
    if account is None:
        raise ValueError("Not a supported public society account URL")
    soup = BeautifulSoup(html[:MAX_HTML_BYTES], "html.parser")
    posts, links = {}, set()
    platform = platform_for(account)
    for document in _documents(soup):
        for node in _walk(document):
            found = None
            kind = node.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(isinstance(value, str) and value in {"SocialMediaPosting", "BlogPosting"} for value in kinds):
                raw_url = node.get("url") or node.get("mainEntityOfPage") or node.get("@id")
                if isinstance(raw_url, dict):
                    raw_url = raw_url.get("@id") or raw_url.get("url")
                url = canonical_post_url(raw_url)
                if url and _belongs(url, node.get("author"), account):
                    found = _make_post(url, node.get("articleBody") or node.get("text") or node.get("caption"), node.get("datePublished"), account, society, now, node.get("image"))
            if platform == "instagram":
                found = found or _instagram_node(node, account, society, now)
                # A verified profile's own timeline can omit owner on each media.
                if _author_matches(node, account):
                    timeline = node.get("edge_owner_to_timeline_media") or node.get("edge_felix_video_timeline")
                    if isinstance(timeline, dict):
                        edges = timeline.get("edges", [])
                        for edge in edges[:100] if isinstance(edges, list) else []:
                            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                                item = _instagram_node(edge["node"], account, society, now, inherited=True)
                                if item:
                                    posts[item["source_url"]] = item
            elif node.get("__typename") in {"Story", "FeedStory", "CometStory"}:
                url = canonical_post_url(node.get("permalink_url") or node.get("url"))
                author = node.get("actors") or node.get("author")
                message = node.get("message")
                if url and _belongs(url, author, account) and isinstance(message, dict):
                    found = _make_post(url, message.get("text"), node.get("creation_time") or node.get("publish_time"), account, society, now)
            if found:
                posts[found["source_url"]] = found
    for anchor in soup.select("a[href]")[:3000]:
        url = canonical_post_url(urljoin(account, anchor.get("href", "")))
        if url and platform_for(url) == platform:
            # Instagram shortcode ownership is checked in the fetched post data.
            if platform == "instagram" or _belongs(url, None, account):
                links.add(url)
    # Only detect a login wall when no actual owned posts were exposed.
    text = soup.get_text(" ", strip=True)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    login = bool(re.search(r"log\s*in\s*(?:[•|·-]\s*instagram|to facebook)|facebook\s*[–—-]\s*log in", title, re.I)
                 or re.search(r"log in to (?:continue|see more)|you must log in|log into facebook|login to continue", text, re.I)
                 or soup.select_one('form[action*="login"]'))
    unavailable = bool(re.search(r"this (?:page|content) isn['’]t available|sorry, this page isn['’]t available|page not found", text, re.I))
    return {"posts": list(posts.values()), "permalinks": sorted(links - posts.keys()),
            "access": "public" if posts else "login_required" if login else "unavailable" if unavailable else "public"}


def _public_fetch(url: str) -> dict:
    """Read bounded public HTML and follow only same-platform redirects."""
    original_platform = platform_for(url)
    for _ in range(4):
        with requests.get(url, timeout=(8, 18), headers={"User-Agent": USER_AGENT}, allow_redirects=False, stream=True) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                next_url = urljoin(url, response.headers.get("Location", ""))
                if platform_for(next_url) != original_platform:
                    return {"text": "", "status_code": 403, "url": url}
                url = next_url
                continue
            if response.status_code >= 400:
                return {"text": "", "status_code": response.status_code, "url": url}
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_HTML_BYTES:
                    raise ValueError("Social source exceeds the 4 MB limit")
            return {"text": content.decode(response.encoding or "utf-8", errors="replace"), "status_code": response.status_code, "url": url}
    raise ValueError("Too many public-page redirects")


def _read_response(fetcher: Callable, url: str) -> dict:
    result = fetcher(url)
    if isinstance(result, str):
        return {"text": result, "status_code": 200, "url": url}
    if isinstance(result, dict):
        return {"text": result.get("text", ""), "status_code": result.get("status_code", 200), "url": result.get("url", url)}
    return {"text": result.text, "status_code": result.status_code, "url": result.url or url}


def collect_social_posts(societies: list[dict] | dict, config: dict, previous: dict, now: datetime, fetch: Callable | None = None) -> dict:
    """Collect bounded public posts; preserve last good posts after source failures.

    ``fetch(url)`` can return HTML, a requests.Response, or {text,status_code,url}.
    Requests run sequentially so a 429 immediately defers the rest of that host.
    ``config`` accepts either the social subsection or a full sources config.
    """
    settings = config.get("social", config.get("social_posts", config))
    stamp = iso(now)
    def limit(name, default, maximum):
        try:
            return max(0, min(int(settings.get(name, default)), maximum))
        except (TypeError, ValueError):
            return default
    max_accounts = limit("max_accounts", 80, 1000)
    max_posts = limit("max_posts_per_account", 8, 40)
    max_stored_posts = limit("max_stored_posts_per_account", 100, 500)
    max_links = limit("max_permalink_requests_per_account", 2, 5)
    max_requests = limit("max_requests", 120, 2500)
    retention_days = limit("retention_days", 45, 365)
    try:
        interval = max(0, min(float(settings.get("request_interval_seconds", 0.25)), 5))
    except (TypeError, ValueError):
        interval = 0.25
    fetcher = fetch or _public_fetch
    entries = societies.get("societies", []) if isinstance(societies, dict) else societies
    accounts = {}
    for society in entries:
        if not isinstance(society, dict):
            continue
        references = [{"platform": platform, "url": society.get(platform + "_url")} for platform in ("instagram", "facebook")]
        extra_accounts = society.get("social_accounts", [])
        if isinstance(extra_accounts, list):
            references.extend(item for item in extra_accounts if isinstance(item, dict))
        for reference in references:
            platform = reference.get("platform")
            if not isinstance(platform, str) or platform not in {"instagram", "facebook"}:
                continue
            raw_url = reference.get("url")
            account = canonical_account_url(raw_url) or _unsupported_group_url(raw_url)
            if account and platform_for(account) == platform:
                details = {"url": account, "platform": platform, "society": society.get("name") or "Durham society"}
                if _unsupported_group_url(account):
                    details["unsupported_reason"] = "Facebook groups are not supported by the public Page collector"
                accounts.setdefault(account, details)
    previous_accounts = {canonical_account_url(item.get("url")) or _unsupported_group_url(item.get("url")): item for item in previous.get("accounts", []) if isinstance(item, dict)}
    retained = {account: {} for account in accounts}
    cutoff = now.astimezone(timezone.utc) - timedelta(days=retention_days)
    for item in previous.get("posts", []):
        if not isinstance(item, dict):
            continue
        account = canonical_account_url(item.get("account_url"))
        url = canonical_post_url(item.get("source_url"))
        dated = _timestamp(item.get("published_at")) or _timestamp(item.get("observed_at"))
        if account in accounts and url and platform_for(url) == accounts[account]["platform"] and dated and datetime.fromisoformat(dated.replace("Z", "+00:00")) >= cutoff and _clean_text(item.get("text")):
            retained[account][url] = {**item, "account_url": account, "source_url": url}
    blocked, statuses, request_count, checked = set(), [], 0, 0

    def read(url):
        nonlocal request_count
        if request_count >= max_requests:
            return {"status": "deferred", "error": "Per-run public request limit reached"}
        platform = platform_for(url)
        if platform in blocked:
            return {"status": "deferred", "error": "Platform deferred after an HTTP 429 response"}
        if request_count and interval and fetch is None:
            time.sleep(interval)
        request_count += 1
        try:
            response = _read_response(fetcher, url)
            code = int(response["status_code"])
            if code == 429:
                blocked.add(platform)
                return {"status": "rate_limited", "error": "HTTP 429; remaining platform requests deferred"}
            if code in {401, 403}:
                return {"status": "login_required" if code == 401 else "access_denied", "error": f"HTTP {code}; public access unavailable"}
            if code >= 400:
                return {"status": "error", "error": f"HTTP {code}"}
            final_url = response.get("url") or url
            if platform_for(final_url) != platform:
                return {"status": "access_denied", "error": "Redirect outside the requested platform"}
            if re.search(r"/(?:accounts/login|login(?:\.php)?|checkpoint|challenge)(?:/|$)", urlparse(final_url).path):
                return {"status": "login_required", "error": "Source redirected to a login or verification page"}
            if not isinstance(response["text"], str) or len(response["text"].encode("utf-8")) > MAX_HTML_BYTES:
                return {"status": "error", "error": "Invalid or oversized public HTML response"}
            return {"status": "ok", "html": response["text"]}
        except Exception as error:
            response = getattr(error, "response", None)
            code = getattr(response, "status_code", None)
            if code == 429:
                blocked.add(platform)
            state = {429: "rate_limited", 401: "login_required", 403: "access_denied"}.get(code, "error")
            return {"status": state, "error": f"HTTP {code}" if code else type(error).__name__ + ": public source request failed"}

    # Least-recently-attempted accounts go first, so a daily request cap cannot
    # permanently starve societies at the end of the directory.
    ordered_accounts = sorted(accounts.items(), key=lambda pair: previous_accounts.get(pair[0], {}).get("last_attempt_at") or "")
    for url, account in ordered_accounts:
        old = previous_accounts.get(url, {})
        status = {**account, "status": "deferred", "last_attempt_at": old.get("last_attempt_at"), "last_success_at": old.get("last_success_at"), "posts_collected": 0,
                  "window_limited": True}
        if account.get("unsupported_reason"):
            status.update(status="unsupported", error=account["unsupported_reason"])
        elif settings.get("enabled", True) is False:
            status.update(status="disabled", error="Public social collection is disabled")
        elif checked >= max_accounts or request_count >= max_requests or account["platform"] in blocked:
            status["error"] = "Platform deferred after HTTP 429" if account["platform"] in blocked else "Per-run collection limit reached"
        else:
            checked += 1
            status["last_attempt_at"] = stamp
            result = read(url)
            status.update({key: value for key, value in result.items() if key != "html"})
            if result["status"] == "ok":
                parsed = parse_public_posts(result["html"], url, account["society"], now)
                found = {post["source_url"]: post for post in parsed["posts"]}
                errors = []
                # The platform's public profile embed often exposes recent posts
                # even when the normal public profile is only an empty shell.
                # Do not attempt it following a denial, login wall or rate limit.
                if account["platform"] == "instagram" and not found and parsed["access"] == "public" and settings.get("public_embeds", True):
                    embedded = read(url + "embed/")
                    if embedded["status"] == "ok":
                        embed_posts = parse_public_posts(embedded["html"], url, account["society"], now)
                        found.update({post["source_url"]: {**post, "retrieval_method": "profile_embed"} for post in embed_posts["posts"]})
                        parsed["permalinks"] = list(dict.fromkeys(parsed["permalinks"] + embed_posts["permalinks"]))
                        if embed_posts["access"] != "public":
                            errors.append({"status": embed_posts["access"], "error": "Public embed requires access"})
                    else:
                        errors.append(embedded)
                if parsed["access"] == "public" and not any(error["status"] in {"login_required", "access_denied", "rate_limited", "deferred"} for error in errors):
                    missing_links = [link for link in parsed["permalinks"] if link not in found]
                    for link in missing_links[:max_links]:
                        if len(found) >= max_posts:
                            break
                        detail = read(link)
                        if detail["status"] != "ok":
                            errors.append(detail)
                            if detail["status"] in {"rate_limited", "deferred", "access_denied", "login_required"}:
                                break
                            continue
                        detail_posts = parse_public_posts(detail["html"], url, account["society"], now)
                        for post in detail_posts["posts"]:
                            # A post page must describe the requested post, not recommendations.
                            if post["source_url"] == link:
                                found[link] = post
                        if detail_posts["access"] != "public":
                            errors.append({"status": detail_posts["access"], "error": "Public permalink requires access"})
                fresh = sorted(found.values(), key=lambda item: item.get("published_at") or item["observed_at"], reverse=True)[:max_posts]
                recent = {post["source_url"]: post for post in fresh if not post["published_at"] or datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")) >= cutoff}
                retained[url].update(recent)
                status["posts_collected"] = len(recent)
                if found:
                    status.update(status="partial" if errors else "ok", last_success_at=stamp)
                    if errors:
                        status["error"] = errors[0].get("error", errors[0]["status"])
                elif errors:
                    status.update(errors[0])
                else:
                    state = parsed["access"] if parsed["access"] != "public" else "no_public_posts"
                    status.update(status=state, error="No owned post captions are exposed in this public response" if state == "no_public_posts" else "Public posts require login or are unavailable")
        status["retained_count"] = min(len(retained[url]), max_stored_posts)
        status["storage_limit_reached"] = len(retained[url]) > max_stored_posts
        statuses.append(status)
    posts = []
    for account_posts in retained.values():
        ordered = sorted(account_posts.values(), key=lambda item: item.get("published_at") or item.get("observed_at") or "", reverse=True)
        posts.extend(ordered[:max_stored_posts])
    posts.sort(key=lambda item: item.get("published_at") or item.get("observed_at") or "", reverse=True)
    successful = sum(item["status"] == "ok" for item in statuses)
    state = "disabled" if settings.get("enabled", True) is False else "ok" if successful == len(statuses) else "partial" if successful or posts else "unavailable"
    summary = {"id": "social-public", "type": "social_posts", "state": state, "status": state,
               "last_attempt_at": stamp, "accounts_total": len(accounts), "accounts_checked": checked,
               "accounts_unsupported": sum(item["status"] == "unsupported" for item in statuses),
               "accounts_successful": successful, "accounts_blocked": sum(item["status"] in {"access_denied", "login_required", "rate_limited"} for item in statuses),
               "posts_collected": sum(item["posts_collected"] for item in statuses), "posts_stored": len(posts),
               "parsed_count": sum(item["posts_collected"] for item in statuses), "request_count": request_count,
               "rate_limited_platforms": sorted(blocked), "window_limited": True}
    return {"updated_at": stamp, "posts": posts, "accounts": statuses, "status": summary}
