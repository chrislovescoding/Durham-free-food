"""Discover society accounts from explicit public links, with a bounded one-hop crawl.

Discovery records evidence; a discovered account is not proof its posts are accessible.
No account names or websites are guessed, and social sites themselves are never
requested by this module. Use safe_discovery_fetch for untrusted external links.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from typing import Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, NavigableString
import requests


USER_AGENT = "DurhamFreeFood/0.1 (+https://github.com/chrislovescoding/Durham-free-food)"
MAX_EXTERNAL_PAGES = 2
MAX_BYTES = 2_000_000
URL_TEXT = re.compile(r"https?://[^\s<>\"']+", re.I)
IG_HANDLE = re.compile(r"\b(?:instagram|insta)(?:\s+(?:account|handle))?\s*[:\-–—]?\s*@([a-z0-9._]{1,30})(?![a-z0-9._])", re.I)
SU_ACCOUNTS = {"durhamsu", "durham.su", "durham_su", "durhamsu.freshers", "durhamstudentsunion"}
SOCIAL_DOMAINS = {"instagram.com", "facebook.com", "fb.com"}
SKIP_DOMAINS = {
    "twitter.com", "x.com", "youtube.com", "youtu.be", "tiktok.com", "linkedin.com",
    "discord.com", "discord.gg", "whatsapp.com", "wa.me", "wechat.com", "snapchat.com",
    "eventbrite.com", "eventbrite.co.uk", "native.fm", "lu.ma", "luma.com", "fixr.co",
    "ticketmaster.com", "ticketmaster.co.uk", "ticketsource.co.uk", "ti.to",
    "paypal.com", "stripe.com", "ukmsl.com", "durhamsu.com", "durham.ac.uk", "dur.ac.uk",
    "docs.google.com", "forms.google.com", "forms.gle", "microsoft.com", "office.com",
}
SKIP_PATH = re.compile(r"(?:^|/)(?:news|blog|article|tickets?|events?|checkout|shop|store|donate|login|signin|signup|sign-up|register|sharer|share|intent|account|admin|wp-admin)(?:/|\.|$)", re.I)
NON_OWNER = re.compile(r"\b(?:sponsors?|sponsored|sponsorship|partners?|partnership|careers?|employers?|recruitment|recruiting|parent\s+organi[sz]ation|national\s+organi[sz]ation|umbrella\s+organi[sz]ation)\b", re.I)
OWN_WEBSITE = re.compile(r"\b(?:our|official|society(?:'s)?|club(?:'s)?|group(?:'s)?)\s+(?:website|homepage|web\s*site|linktree)\b|\b(?:visit|find|follow|join)\s+us\b|\b(?:our|all(?:\s+of)?(?:\s+our)?)\s+social(?:\s+media)?\s+(?:accounts?|links?)\b", re.I)


def _domain(host: str) -> str:
    return host.lower().removeprefix("www.").removeprefix("m.")


def _matches_domain(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _https_url(value: str, base: str = "") -> str | None:
    """Reject local, credential-bearing and non-HTTPS URLs before DNS or I/O."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        url = urlparse(urljoin(base, value.strip()))
        host = (url.hostname or "").rstrip(".").lower()
        if url.scheme.lower() != "https" or not host or url.username or url.password or url.port not in (None, 443):
            return None
        if "." not in host or host.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".test", ".invalid")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host:
                return None
        return urlunparse(("https", host, url.path or "/", "", url.query, ""))
    except (ValueError, TypeError):
        return None


def _assert_public_dns(url: str) -> None:
    host = urlparse(url).hostname
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("Could not resolve the public website") from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Website resolves to a private or reserved network address")


def _assert_page_accessible(html: str) -> None:
    """Recognize explicit access interstitials without treating login links as gates."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, template, [hidden], [aria-hidden='true']"):
        element.decompose()
    # Navigation and footer login/CAPTCHA notices do not describe page access.
    for element in soup.select("nav, footer, aside"):
        element.decompose()
    prominent = " ".join(element.get_text(" ", strip=True) for element in soup.select("title, h1, h2"))
    visible = " ".join(soup.get_text(" ", strip=True).split())
    challenge = re.compile(
        r"\b(?:just a moment|verify (?:that )?you(?: are|'re|\u2019re) (?:a )?human|"
        r"checking your browser|verifying your browser|human verification required|"
        r"complete (?:the |a )?(?:captcha|security check)|attention required.*cloudflare)\b", re.I)
    access_gate = re.compile(
        r"\b(?:sign[ -]?in|log[ -]?in) (?:is required |required |to |before (?:you can )?)"
        r"(?:continue|access|view|read|proceed)\b", re.I)
    login_title = re.compile(r"^(?:member |account )?(?:sign[ -]?in|log[ -]?in)(?:\s*[-|:]|$)", re.I)
    has_password = soup.select_one('form input[type="password"]') is not None
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    # Body-only matching is limited to short interstitials. Longer articles may
    # legitimately discuss CAPTCHA or authentication while remaining readable.
    if (challenge.search(prominent) or access_gate.search(prominent)
            or (len(visible) < 1500 and (challenge.search(visible) or access_gate.search(visible)))
            or (has_password and login_title.search(title))):
        raise ValueError("Website access unavailable: public page requires an access challenge or sign-in")


def safe_discovery_fetch(url: str) -> str:
    """Fetch <=2 MB of public HTTPS HTML, checking every redirect (at most three).

    An injected fetcher in discover_accounts may return fixture HTML. Production
    callers should use this helper so redirects cannot turn a public link into a
    request to a local service. Cookies and credentials are not persisted.
    """
    started = time.monotonic()
    with requests.Session() as session:
        # Avoid ambient netrc credentials and proxy settings for discovered URLs.
        session.trust_env = False
        for redirects in range(4):
            target = _https_url(url)
            if not target:
                raise ValueError("Discovery only accepts public HTTPS URLs")
            parsed_target = urlparse(target)
            if _matches_domain(_domain(parsed_target.hostname or ""), SOCIAL_DOMAINS | SKIP_DOMAINS) or SKIP_PATH.search(parsed_target.path):
                raise ValueError("Discovery destination is not a public society website")
            _assert_public_dns(target)
            response = session.get(target, headers={"User-Agent": USER_AGENT}, timeout=(5, 10), stream=True, allow_redirects=False)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirects == 3 or not response.headers.get("Location"):
                        raise ValueError("Website redirect limit reached or destination missing")
                    url = urljoin(target, response.headers["Location"])
                    continue
                response.raise_for_status()
                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if media_type and media_type not in ("text/html", "application/xhtml+xml", "text/plain"):
                    raise ValueError("Discovery source is not an HTML or text page")
                if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
                    raise ValueError("Discovery source exceeds 2 MB")
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_BYTES or time.monotonic() - started > 25:
                        raise ValueError("Discovery source exceeds its size or time limit")
                    chunks.append(chunk)
                html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                _assert_page_accessible(html)
                return html
            finally:
                response.close()
    raise ValueError("Website redirect limit reached")


def _social_account(value: str, base: str) -> tuple[str, str] | None:
    """Canonicalize profile links without accepting login, share or post URLs."""
    candidate = urljoin(base, value.strip())
    # Old official links may use HTTP; social account links are returned as HTTPS.
    parsed = urlparse(candidate)
    host = _domain(parsed.hostname or "")
    if host not in SOCIAL_DOMAINS or parsed.scheme not in ("https", "http"):
        return None
    candidate = _https_url(urlunparse(parsed._replace(scheme="https")))
    if not candidate:
        return None
    parts = [part for part in urlparse(candidate).path.split("/") if part]
    if not parts:
        return None
    if host == "instagram.com":
        if len(parts) != 1 or parts[0].lower() in {"p", "reel", "reels", "stories", "explore", "accounts", "direct", "about", "developer"}:
            return None
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", parts[0]) or parts[0].lower() in SU_ACCOUNTS:
            return None
        return "instagram", "https://www.instagram.com/" + parts[0].lower() + "/"
    if parts[0].lower() in SU_ACCOUNTS or parts[0].lower() in {"sharer", "sharer.php", "share", "login", "login.php", "dialog", "events", "watch", "reel", "photo.php", "photos", "plugins", "help", "settings", "privacy"}:
        return None
    if parts[0].lower() == "profile.php":
        identifier = parse_qs(parsed.query).get("id", [""])[0]
        if not identifier.isdigit():
            return None
        return "facebook", "https://www.facebook.com/profile.php?" + urlencode({"id": identifier})
    if len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9._-]+", parts[0]):
        path = parts[0]
    elif len(parts) == 2 and parts[0].lower() == "groups" and re.fullmatch(r"[A-Za-z0-9._-]+", parts[1]):
        path = "groups/" + parts[1]
    elif len(parts) == 3 and parts[0].lower() == "pages" and parts[2].isdigit():
        path = "/".join(parts)
    else:
        return None
    return "facebook", "https://www.facebook.com/" + path + "/"


def _anchor_context(anchor) -> tuple[str, bool]:
    """Keep ownership cues local to the link, not the whole society description."""
    block = anchor.find_parent(["p", "li", "dd", "dt"]) or anchor.parent
    before = []
    for node in block.descendants:
        if node is anchor:
            break
        if isinstance(node, NavigableString):
            before.append(str(node))
    context = (" ".join(before)[-150:] + " " + anchor.get_text(" ", strip=True)).strip()
    metadata = " ".join(str(parent.get("id", "")) + " " + " ".join(parent.get("class", [])) for parent in [anchor, *list(anchor.parents)[:5]])
    return context, bool(NON_OWNER.search(context) or NON_OWNER.search(metadata.replace("-", "_ ").replace("_", " ")))


def _page_evidence(html: str, page_url: str) -> tuple[list[dict], list[str], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, noscript, template, [hidden], [aria-hidden='true']"):
        element.decompose()
    # A society website's own footer often contains its only social links.
    if _matches_domain(_domain(urlparse(page_url).hostname or ""), {"durhamsu.com"}):
        for element in soup.select("header, footer, nav, #footer, .footer, #header"):
            element.decompose()
    is_su = _matches_domain(_domain(urlparse(page_url).hostname or ""), {"durhamsu.com"})
    links = []
    for anchor in soup.select("a[href]"):
        context, non_owner = _anchor_context(anchor)
        # This is the SU's dedicated per-society website field, verified in its
        # public markup. A generic external link/class is not an ownership claim.
        dedicated = is_su and "website" in anchor.get("class", []) and anchor.find_parent(class_="socialmedia-c") is not None
        links.append((anchor.get("href", ""), "link", context, dedicated, non_owner))
    visible_text = soup.get_text(" ", strip=True)
    # Read each visible text node separately, so an affirmative caption on one
    # link cannot silently confer ownership on the following unrelated link.
    for node in soup.find_all(string=True):
        if node.find_parent("a") is not None:
            continue
        text = str(node)
        for match in URL_TEXT.finditer(text):
            context = text[max(0, match.start() - 150):match.start()]
            links.append((match.group(0).rstrip(".,;:!?)]}"), "text_url", context, False, bool(NON_OWNER.search(context))))
    records, external, review_links, seen = [], [], [], set()
    for value, method, context, dedicated, non_owner in links:
        account = _social_account(value, page_url)
        if account and account not in seen and not non_owner:
            seen.add(account)
            records.append({"platform": account[0], "url": account[1], "evidence_url": page_url, "method": method})
        target = _https_url(value, page_url)
        if not target:
            continue
        parsed = urlparse(target)
        host = _domain(parsed.hostname or "")
        if host == _domain(urlparse(page_url).hostname or "") or _matches_domain(host, SOCIAL_DOMAINS | SKIP_DOMAINS) or SKIP_PATH.search(parsed.path):
            continue
        # Linktree pages and expressly linked homepages, never arbitrary articles.
        is_linktree = host == "linktr.ee" and len(parsed.path.strip("/").split("/")) == 1
        is_home = parsed.path.rstrip("/").lower() in ("", "/home", "/index.html", "/index.htm", "/index.php")
        hosted_home = host.endswith(".github.io") and len(parsed.path.strip("/").split("/")) <= 2
        labelled_home = bool(OWN_WEBSITE.search(context))
        if parsed.query or not (is_linktree or is_home or hosted_home or labelled_home):
            continue
        if non_owner or not (dedicated or labelled_home):
            review_links.append({"url": target, "evidence_url": page_url,
                                 "reason": "Third-party sponsor, partner, careers or parent-organisation link" if non_owner else "Society ownership is not explicit; account discovery requires review"})
            continue
        if target not in external:
            external.append(target)
    for match in IG_HANDLE.finditer(visible_text):
        account = _social_account("https://instagram.com/" + match.group(1).rstrip("."), page_url)
        context = visible_text[max(0, match.start() - 90):match.end()]
        if account and account not in seen and not NON_OWNER.search(context):
            seen.add(account)
            records.append({"platform": account[0], "url": account[1], "evidence_url": page_url, "method": "labelled_handle"})
    # Link directories tend to resolve both platforms in a single request.
    external.sort(key=lambda url: _domain(urlparse(url).hostname or "") != "linktr.ee")
    owned_destinations = set(external)
    review_links = list({item["url"]: item for item in review_links if item["url"] not in owned_destinations}.values())
    return records, external, review_links


def discover_accounts(html: str, page_url: str, fetcher: Callable[[str], str] = safe_discovery_fetch) -> dict:
    """Discover explicit accounts and inspect at most two linked pages, one hop.

    External pages are fetched only while at least one platform is missing. Their
    outbound websites are not recursively visited. Missing links remain unresolved.
    """
    records, destinations, review_links = _page_evidence(html, page_url)
    errors, checked = [], []
    for destination in destinations[:MAX_EXTERNAL_PAGES]:
        if {record["platform"] for record in records} == {"instagram", "facebook"}:
            break
        checked.append(destination)
        try:
            discovered, _, _ = _page_evidence(fetcher(destination), destination)
            existing = {(record["platform"], record["url"]) for record in records}
            for record in discovered:
                if (record["platform"], record["url"]) not in existing:
                    records.append({**record, "method": "linked_page_" + record["method"]})
                    existing.add((record["platform"], record["url"]))
        except Exception as error:
            # Errors are actionable coverage evidence, not an empty successful crawl.
            errors.append({"url": destination, "error": str(error)[:300]})
    status = "partial" if records and errors else "found" if records else "error" if errors else "not_listed"
    result = {"social_accounts": records, "discovery_status": status, "discovery_errors": errors,
              "discovery_pages_checked": checked, "discovery_review_links": review_links}
    for record in records:
        result.setdefault(record["platform"] + "_url", record["url"])
    return result
