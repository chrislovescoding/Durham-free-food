"""Bounded, cached OCR of organiser post images; never a publication decision.

Captions remain unchanged. ``image_ocr`` records machine-read poster text and
explicit per-image coverage for the review queue. A successful OCR pass means
the image was inspected, not that its words, dates or eligibility are verified.
Requires Pillow and the local ``tesseract`` executable (English language data).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urljoin, urlparse, urlunparse
import warnings

from PIL import Image, ImageOps
import requests


USER_AGENT = "DurhamFreeFood/0.3 (+https://github.com/chrislovescoding/Durham-free-food)"
CDN_DOMAINS = {"cdninstagram.com", "fbcdn.net"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
CACHE_VERSION = 1


class ImageError(ValueError):
    """Fixed, public-safe error text, without signed URLs or response bodies."""


def safe_image_url(value):
    """Only HTTPS images on Meta's CDN; credentials, custom ports and IPs fail."""
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.path or ".." in host
                or not any(host == domain or host.endswith("." + domain) for domain in CDN_DOMAINS)):
            return None
        return urlunparse(("https", host, parsed.path, "", parsed.query, ""))
    except (ValueError, TypeError):
        return None


def _public_dns(url):
    try:
        addresses = socket.getaddrinfo(urlparse(url).hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ImageError("Image host resolves to a private or reserved address")
    except OSError as error:
        raise ImageError("Image host could not be resolved") from error


def image_identity(url):
    """Expiry/signature query changes should reuse the same already-inspected image."""
    parsed = urlparse(url)
    return hashlib.sha256((parsed.netloc.lower() + parsed.path).encode()).hexdigest()[:24]


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def download_image(url, destination, *, session, remaining_bytes=MAX_IMAGE_BYTES, deadline=None, on_download=None):
    """Check every redirect, bound compressed bytes/pixels/time, save safe RGB PNG.

    Returns the number of downloaded bytes. No ambient proxy or netrc credentials
    should be used; ``inspect_posts`` creates a session with trust_env=False.
    """
    if deadline is None:
        deadline = time.monotonic() + 35
    maximum = min(MAX_IMAGE_BYTES, remaining_bytes)
    if maximum <= 0:
        raise ImageError("Total image download byte limit reached")
    for redirect in range(4):
        if time.monotonic() >= deadline:
            raise ImageError("Image inspection time limit reached")
        target = safe_image_url(url)
        if not target:
            raise ImageError("Image URL is not an allowed public Meta CDN HTTPS address")
        _public_dns(target)
        remaining = max(0.1, deadline - time.monotonic())
        try:
            response = session.get(target, headers={"User-Agent": USER_AGENT}, stream=True,
                                   allow_redirects=False, timeout=(min(5, remaining), min(10, remaining)))
        except requests.RequestException as error:
            raise ImageError("Image download failed") from error
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                if redirect == 3 or not response.headers.get("Location"):
                    raise ImageError("Image redirect limit reached or destination missing")
                url = urljoin(target, response.headers["Location"])
                continue
            if response.status_code >= 400 or response.status_code < 200 or response.status_code >= 300:
                raise ImageError("Image CDN returned an unsuccessful status")
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type not in IMAGE_TYPES:
                raise ImageError("Image response has an unsupported content type")
            try:
                length = int(response.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                raise ImageError("Image response has an invalid content length") from None
            if length > maximum:
                raise ImageError("Image exceeds the download byte limit")
            data = bytearray()
            for chunk in response.iter_content(65536):
                if on_download:
                    on_download(len(chunk))
                data.extend(chunk)
                if len(data) > maximum:
                    raise ImageError("Image exceeds the download byte limit")
                if time.monotonic() >= deadline:
                    raise ImageError("Image download time limit reached")
        except requests.RequestException as error:
            raise ImageError("Image download failed") from error
        finally:
            response.close()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as source:
                    if source.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                        raise ImageError("Image format is not supported")
                    if source.width * source.height > MAX_IMAGE_PIXELS:
                        raise ImageError("Image exceeds the pixel limit")
                    if getattr(source, "n_frames", 1) > 1:
                        raise ImageError("Animated image needs manual inspection")
                    picture = ImageOps.exif_transpose(source).convert("RGB")
                    picture.thumbnail((2400, 2400))
                    Path(destination).parent.mkdir(parents=True, exist_ok=True)
                    picture.save(destination, format="PNG")
        except ImageError:
            raise
        except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
            raise ImageError("Image could not be safely decoded") from error
        return len(data)
    raise ImageError("Image redirect limit reached")


def tesseract_ocr(path, *, timeout=25):
    """Sparse poster layout; no network calls or paid vision service."""
    try:
        result = subprocess.run(["tesseract", str(path), "stdout", "-l", "eng", "--psm", "11"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, check=False)
    except FileNotFoundError as error:
        raise ImageError("Tesseract is not installed; image inspection is incomplete") from error
    except subprocess.TimeoutExpired as error:
        raise ImageError("OCR time limit reached") from error
    if result.returncode:
        raise ImageError("Tesseract could not read this image")
    return result.stdout.strip()[:24000]


def _cached_result(path):
    try:
        if path.stat().st_size > 100_000:
            return None
        result = json.loads(path.read_text(encoding="utf-8"))
        if (result.get("cache_version") == CACHE_VERSION and result.get("status") in {"ok", "no_text"}
                and isinstance(result.get("text"), str)):
            return result
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def inspect_posts(posts, cache_dir, *, max_posts=50, max_images_per_post=20,
                  max_images=250, max_download_bytes=200 * 1024 * 1024,
                  max_seconds=600, downloader=None, ocr=None):
    """Return (enriched_posts, report) without changing or dropping source posts.

    Limits produce explicit incomplete statuses. Cache successes across reruns;
    failures are retried. Each post retains its caption under ``text`` and gets
    ``image_ocr.text`` separately for cautious review. OCR is never verification.
    """
    if not isinstance(posts, list) or any(not isinstance(post, dict) for post in posts):
        raise ValueError("Posts must be a list of objects")
    for name, value, ceiling in (("max_posts", max_posts, 50), ("max_images_per_post", max_images_per_post, 20),
                                  ("max_images", max_images, 1000), ("max_download_bytes", max_download_bytes, 500 * 1024 * 1024),
                                  ("max_seconds", max_seconds, 1800)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or value > ceiling:
            raise ValueError(f"{name} must be greater than zero and at most {ceiling}")
    downloader = downloader or download_image
    ocr = ocr or tesseract_ocr
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max_seconds
    report = {"generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "engine": "tesseract-eng-psm11", "posts": len(posts), "posts_complete": 0,
              "images_expected": 0, "images_inspected": 0, "images_cached": 0,
              "images_downloaded": 0, "downloaded_bytes": 0, "images_failed_or_skipped": 0,
              "notice": "OCR is machine reading, not verification. Read image text together with the organiser caption before publishing.",
              "manifest": []}
    enriched = []
    attempted = 0
    def record_download(size):
        report["downloaded_bytes"] += size

    with requests.Session() as session:
        session.trust_env = False
        for post_index, post in enumerate(posts):
            original_urls = post.get("media_urls", [])
            original_urls = original_urls if isinstance(original_urls, list) else []
            urls, identities = [], set()
            for url in original_urls:
                identity = image_identity(url) if safe_image_url(url) else hashlib.sha256(str(url).encode()).hexdigest()[:24]
                if identity not in identities:
                    urls.append((url, identity))
                    identities.add(identity)
            post_key = hashlib.sha256(str(post.get("id") or post.get("source_url") or post_index).encode()).hexdigest()[:24]
            image_results = []
            for index, (url, identity) in enumerate(urls):
                result = {"image_id": identity, "index": index, "status": "error", "text": ""}
                # Do not copy CDN signatures into the manifest/error reports.
                if safe_image_url(url):
                    result["source_image"] = urlunparse(urlparse(url)._replace(query="", fragment=""))
                folder = cache_dir / post_key
                json_path, image_path = folder / (identity + ".json"), folder / (identity + ".png")
                try:
                    if post_index >= max_posts:
                        raise ImageError("Post inspection limit reached")
                    if index >= max_images_per_post:
                        raise ImageError("Per-post image inspection limit reached")
                    if not safe_image_url(url):
                        raise ImageError("Image URL is not an allowed public Meta CDN HTTPS address")
                    cached = _cached_result(json_path)
                    if cached:
                        result.update({"status": cached["status"], "text": cached["text"], "cached": True})
                        if image_path.is_file():
                            result["local_path"] = str(image_path)
                        report["images_cached"] += 1
                    else:
                        if attempted >= max_images:
                            raise ImageError("Total image inspection limit reached")
                        if time.monotonic() >= deadline:
                            raise ImageError("Image inspection time limit reached")
                        remaining_bytes = max_download_bytes - report["downloaded_bytes"]
                        if remaining_bytes <= 0:
                            raise ImageError("Total image download byte limit reached")
                        attempted += 1
                        previous_bytes = report["downloaded_bytes"]
                        size = downloader(url, image_path, session=session, remaining_bytes=remaining_bytes,
                                          deadline=min(deadline, time.monotonic() + 35), on_download=record_download)
                        if report["downloaded_bytes"] == previous_bytes:
                            # Fixture/custom downloaders may only return the received size.
                            report["downloaded_bytes"] += size
                        report["images_downloaded"] += 1
                        result["local_path"] = str(image_path)
                        remaining_seconds = deadline - time.monotonic()
                        if remaining_seconds <= 0:
                            raise ImageError("Image inspection time limit reached")
                        text = ocr(image_path, timeout=min(25, remaining_seconds))
                        result.update({"status": "ok" if text else "no_text", "text": text, "cached": False})
                        _write_json(json_path, {"cache_version": CACHE_VERSION, "status": result["status"], "text": text})
                    report["images_inspected"] += 1
                except ImageError as error:
                    result["error"] = str(error)
                    report["images_failed_or_skipped"] += 1
                except OSError:
                    result["error"] = "Image cache could not be read or written"
                    result["status"] = "error"
                    report["images_failed_or_skipped"] += 1
                image_results.append(result)
            inspected = sum(item["status"] in {"ok", "no_text"} for item in image_results)
            coverage_note = None
            media_type = str(post.get("media_type") or "").lower()
            if media_type in {"video", "reel", "reels"}:
                coverage_note = "Only the video cover image was inspected; video frames and audio need review"
            elif media_type in {"sidecar", "carousel"} and post.get("media_coverage") == "cover_only":
                coverage_note = "Only the carousel cover was supplied; remaining panels need review"
            expected = post.get("media_expected_count")
            if isinstance(expected, int) and not isinstance(expected, bool) and expected > len(urls):
                coverage_note = "The source lists more images than were supplied; missing panels need review"
            else:
                expected = len(urls)
            complete = bool(urls) and inspected == expected and not coverage_note
            image_ocr = {"status": "complete" if complete else ("partial" if inspected else ("no_images" if not urls else "failed")),
                         "coverage_complete": complete, "expected_images": expected, "inspected_images": inspected,
                         "text": "\n\n".join(item["text"] for item in image_results if item["text"]),
                         "images": image_results, "engine": report["engine"], "verification": "machine_read_only"}
            if coverage_note:
                image_ocr["coverage_note"] = coverage_note
            enriched.append({**post, "image_ocr": image_ocr})
            report["images_expected"] += expected
            report["posts_complete"] += int(complete)
            report["manifest"].append({"post_id": post.get("id"), "source_url": post.get("source_url"),
                                       "society": post.get("society"), "status": image_ocr["status"], "images": image_results})
    report["coverage_complete"] = bool(posts) and report["posts_complete"] == len(posts)
    return enriched, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--max-posts", type=int, default=50)
    parser.add_argument("--max-images", type=int, default=250)
    args = parser.parse_args()
    payload = json.loads(args.posts.read_text(encoding="utf-8-sig"))
    posts = payload if isinstance(payload, list) else payload["posts"]
    enriched, report = inspect_posts(posts, args.cache_dir, max_posts=args.max_posts, max_images=args.max_images)
    output = enriched if isinstance(payload, list) else {**payload, "posts": enriched}
    _write_json(args.output, output)
    _write_json(args.report, report)
    print(json.dumps({key: value for key, value in report.items() if key != "manifest"}, indent=2))


if __name__ == "__main__":
    main()
