"""Poster OCR coverage, unsafe-network rejection and reusable cache contracts."""

from io import BytesIO
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image


SPEC = importlib.util.spec_from_file_location("post_images", Path(__file__).parents[1] / "scripts/post_images.py")
images = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(images)
URL = "https://scontent.cdninstagram.com/poster.jpg?expires=123"
URL2 = "https://scontent.fbcdn.net/carousel.jpg?signature=abc"
PUBLIC_DNS = [(2, 1, 6, "", ("157.240.1.1", 443))]


def post(urls=None, identity="post-1"):
    return {"id": identity, "source_url": "https://www.instagram.com/p/Example/", "society": "Example",
            "text": "See the poster for details!", "media_urls": [URL] if urls is None else urls}


def png():
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="PNG")
    return output.getvalue()


def response(data=None, status=200, headers=None):
    result = Mock(status_code=status)
    result.headers = {"Content-Type": "image/png", **(headers or {})}
    result.iter_content.return_value = [png() if data is None else data]
    return result


def fixture_download(url, path, **kwargs):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(png())
    return 100


class ImageUrlTests(unittest.TestCase):
    def test_only_public_meta_https_cdn_urls(self):
        for value in (URL, URL2, "https://instagram.fdel99-1.fna.fbcdn.net/image.jpg"):
            self.assertIsNotNone(images.safe_image_url(value))
        for value in ("http://scontent.cdninstagram.com/image.jpg", "https://localhost/a.png",
                      "https://127.0.0.1/a.png", "https://10.0.0.1/a.png", "https://example.com/a.png",
                      "https://cdninstagram.com.evil.test/a.png", "https://evilcdninstagram.com/a.png",
                      "https://x@cdninstagram.com/a.png", "https://cdninstagram.com:444/a.png",
                      "https://cdninstagram.com:bad/a.png", "https://cdninstagram.com/\\a.png",
                      "https://cdninstagram.com/\na.png", None, {}):
            self.assertIsNone(images.safe_image_url(value), str(value))

    @patch.object(images.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))])
    def test_private_dns_stops_before_request(self, resolve):
        session = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(images.ImageError, "private"):
                images.download_image(URL, Path(directory) / "image.png", session=session)
        session.get.assert_not_called()

    @patch.object(images.socket, "getaddrinfo", return_value=PUBLIC_DNS)
    def test_redirect_to_non_cdn_is_rejected(self, resolve):
        session = Mock()
        redirected = response(status=302, headers={"Location": "http://127.0.0.1/private"})
        session.get.return_value = redirected
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(images.ImageError, "allowed public"):
                images.download_image(URL, Path(directory) / "image.png", session=session)
        self.assertEqual(session.get.call_count, 1)
        redirected.close.assert_called_once()
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    @patch.object(images.socket, "getaddrinfo", side_effect=[PUBLIC_DNS, [(2, 1, 6, "", ("127.0.0.1", 443))]])
    def test_redirect_dns_is_rechecked(self, resolve):
        session = Mock()
        session.get.return_value = response(status=302, headers={"Location": URL2})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(images.ImageError, "private"):
                images.download_image(URL, Path(directory) / "image.png", session=session)
        self.assertEqual(session.get.call_count, 1)


class DownloadBoundsTests(unittest.TestCase):
    @patch.object(images.socket, "getaddrinfo", return_value=PUBLIC_DNS)
    def test_real_image_is_decoded_and_saved_for_visual_review(self, resolve):
        session = Mock()
        session.get.return_value = response()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested/image.png"
            size = images.download_image(URL, target, session=session)
            self.assertEqual(size, len(png()))
            with Image.open(target) as picture:
                self.assertEqual(picture.mode, "RGB")
        session.get.return_value.close.assert_called_once()

    @patch.object(images.socket, "getaddrinfo", return_value=PUBLIC_DNS)
    def test_header_and_stream_byte_limits(self, resolve):
        for reply in (response(headers={"Content-Length": "1001"}), response(data=b"x" * 1001)):
            session = Mock()
            session.get.return_value = reply
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(images.ImageError, "byte limit"):
                    images.download_image(URL, Path(directory) / "image.png", session=session, remaining_bytes=1000)
            reply.close.assert_called_once()

    @patch.object(images.socket, "getaddrinfo", return_value=PUBLIC_DNS)
    def test_reject_html_svg_invalid_and_overlarge_image(self, resolve):
        cases = [response(headers={"Content-Type": "text/html"}), response(headers={"Content-Type": "image/svg+xml"}),
                 response(data=b"not a valid image")]
        with tempfile.TemporaryDirectory() as directory:
            for reply in cases:
                session = Mock()
                session.get.return_value = reply
                with self.assertRaises(images.ImageError):
                    images.download_image(URL, Path(directory) / "image.png", session=session)
            session.get.return_value = response()
            with patch.object(images, "MAX_IMAGE_PIXELS", 50):
                with self.assertRaisesRegex(images.ImageError, "pixel limit"):
                    images.download_image(URL, Path(directory) / "image.png", session=session)


class InspectionTests(unittest.TestCase):
    def test_all_distinct_carousel_images_and_caption_kept_separate(self):
        downloader = Mock(side_effect=fixture_download)
        ocr = Mock(side_effect=["Free pizza", "19 October 18:00 Room A"])
        with tempfile.TemporaryDirectory() as directory:
            enriched, report = images.inspect_posts([post([URL, URL, URL2])], directory, downloader=downloader, ocr=ocr)
            result = enriched[0]
            self.assertEqual(result["text"], "See the poster for details!")
            self.assertEqual(result["image_ocr"]["status"], "complete")
            self.assertEqual(result["image_ocr"]["expected_images"], 2)
            self.assertIn("Free pizza", result["image_ocr"]["text"])
            self.assertIn("Room A", result["image_ocr"]["text"])
            self.assertTrue(report["coverage_complete"])
            self.assertEqual(report["images_downloaded"], 2)
            self.assertEqual(report["downloaded_bytes"], 200)
            self.assertFalse(downloader.call_args.kwargs["session"].trust_env)
            self.assertNotIn("signature", str(report["manifest"]))

    def test_success_cache_ignores_rotating_signed_query(self):
        downloader = Mock(side_effect=fixture_download)
        ocr = Mock(return_value="Free cake")
        with tempfile.TemporaryDirectory() as directory:
            images.inspect_posts([post()], directory, downloader=downloader, ocr=ocr)
            enriched, report = images.inspect_posts([post([URL.replace("123", "456")])], directory, downloader=downloader, ocr=ocr)
            self.assertEqual(report["images_cached"], 1)
            self.assertTrue(enriched[0]["image_ocr"]["images"][0]["cached"])
            self.assertEqual(downloader.call_count, 1)
            self.assertEqual(ocr.call_count, 1)

    def test_one_failed_carousel_image_leaves_explicit_partial_coverage(self):
        downloader = Mock(side_effect=[100, images.ImageError("Image CDN returned an unsuccessful status")])
        with tempfile.TemporaryDirectory() as directory:
            enriched, report = images.inspect_posts([post([URL, URL2])], directory, downloader=downloader,
                                                    ocr=Mock(return_value="Free pizza"))
        result = enriched[0]["image_ocr"]
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["inspected_images"], 1)
        self.assertEqual(report["images_failed_or_skipped"], 1)

    def test_image_post_and_total_limits_are_reported_without_dropping_posts(self):
        with tempfile.TemporaryDirectory() as directory:
            enriched, report = images.inspect_posts([post([URL, URL2]), post(identity="post-2")], directory,
                                                    max_posts=1, max_images_per_post=1,
                                                    downloader=fixture_download, ocr=Mock(return_value="Free food"))
        self.assertEqual(len(enriched), 2)
        self.assertEqual(enriched[0]["image_ocr"]["status"], "partial")
        self.assertIn("Per-post", enriched[0]["image_ocr"]["images"][1]["error"])
        self.assertIn("Post inspection", enriched[1]["image_ocr"]["images"][0]["error"])
        with tempfile.TemporaryDirectory() as directory:
            enriched, _ = images.inspect_posts([post([URL, URL2])], directory, max_images=1,
                                                downloader=fixture_download, ocr=Mock(return_value="Free food"))
        self.assertIn("Total image inspection", enriched[0]["image_ocr"]["images"][1]["error"])

    def test_byte_budget_counts_failed_downloads(self):
        def fail_download(url, path, **kwargs):
            kwargs["on_download"](100)
            raise images.ImageError("Image could not be safely decoded")
        downloader = Mock(side_effect=fail_download)
        with tempfile.TemporaryDirectory() as directory:
            enriched, report = images.inspect_posts([post([URL, URL2])], directory, max_download_bytes=100,
                                                    downloader=downloader, ocr=Mock())
        self.assertEqual(report["downloaded_bytes"], 100)
        self.assertEqual(downloader.call_count, 1)
        self.assertIn("Total image download byte", enriched[0]["image_ocr"]["images"][1]["error"])

    def test_no_images_and_empty_ocr_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            enriched, report = images.inspect_posts([post([]), post(identity="post-2")], directory,
                                                    downloader=fixture_download, ocr=Mock(return_value=""))
        self.assertEqual(enriched[0]["image_ocr"]["status"], "no_images")
        self.assertFalse(enriched[0]["image_ocr"]["coverage_complete"])
        self.assertEqual(enriched[1]["image_ocr"]["images"][0]["status"], "no_text")
        self.assertTrue(enriched[1]["image_ocr"]["coverage_complete"])
        self.assertFalse(report["coverage_complete"])

    def test_failure_is_retried_not_cached(self):
        ocr = Mock(side_effect=[images.ImageError("OCR time limit reached"), "Free snacks"])
        with tempfile.TemporaryDirectory() as directory:
            first, _ = images.inspect_posts([post()], directory, downloader=fixture_download, ocr=ocr)
            second, report = images.inspect_posts([post()], directory, downloader=fixture_download, ocr=ocr)
        self.assertEqual(first[0]["image_ocr"]["status"], "failed")
        self.assertEqual(second[0]["image_ocr"]["status"], "complete")
        self.assertEqual(report["images_cached"], 0)

    def test_unsafe_url_does_not_reach_injected_downloader(self):
        downloader = Mock()
        with tempfile.TemporaryDirectory() as directory:
            enriched, _ = images.inspect_posts([post(["https://localhost/private"])], directory, downloader=downloader)
        downloader.assert_not_called()
        self.assertEqual(enriched[0]["image_ocr"]["status"], "failed")

    def test_missing_tesseract_and_timeout_fail_explicitly(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("tesseract", 25)):
            with patch.object(images.subprocess, "run", side_effect=error):
                with self.assertRaises(images.ImageError):
                    images.tesseract_ocr(Path("poster.png"))

    def test_video_and_missing_carousel_panels_are_not_complete(self):
        for extra in ({"media_type": "Video", "media_coverage": "cover_only"},
                      {"media_type": "Sidecar", "media_coverage": "cover_only"},
                      {"media_type": "Sidecar", "media_expected_count": 3}):
            with tempfile.TemporaryDirectory() as directory:
                enriched, report = images.inspect_posts([{**post(), **extra}], directory,
                                                        downloader=fixture_download, ocr=Mock(return_value="Free food"))
            self.assertEqual(enriched[0]["image_ocr"]["status"], "partial")
            self.assertIn("review", enriched[0]["image_ocr"]["coverage_note"])
            self.assertFalse(report["coverage_complete"])


if __name__ == "__main__":
    unittest.main()
