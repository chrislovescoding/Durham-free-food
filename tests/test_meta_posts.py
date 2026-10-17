"""Offline contract tests; no live Meta access is implied by passing tests."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


SPEC = importlib.util.spec_from_file_location("meta_posts", Path(__file__).parents[1] / "scripts/meta_posts.py")
meta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(meta)
NOW = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)
ENV = {"META_ACCESS_TOKEN": "secret-example-token", "META_INSTAGRAM_USER_ID": "123456", "META_GRAPH_API_VERSION": "v24.0"}
IG = {"name": "Test Society", "instagram_url": "https://www.instagram.com/test.society/?hl=en"}
FB = {"name": "Test Society", "facebook_url": "https://www.facebook.com/TestSociety"}


def ig_post(code="ABC", **overrides):
    return {"id": "98765", "caption": "Free pizza tomorrow!", "permalink": "https://www.instagram.com/reel/" + code + "/",
            "timestamp": "2026-10-17T12:00:00+0000", "media_url": "https://scontent.cdninstagram.com/photo.jpg", **overrides}


def fb_post(code="98765", **overrides):
    return {"id": "111_" + code, "message": "Free food at our talk.", "is_published": True, "from": {"id": "111"},
            "permalink_url": "https://www.facebook.com/story.php?story_fbid=" + code + "&id=111",
            "created_time": "2026-10-17T12:00:00+0000", "full_picture": "https://scontent.fbcdn.net/photo.jpg", **overrides}


def ig_response(posts=None, paging=None):
    return {"business_discovery": {"media": {"data": [ig_post()] if posts is None else posts, "paging": paging or {}}}}


class MetaCollectorTests(unittest.TestCase):
    def collect(self, societies=None, responses=None, environ=None, previous=None, **settings):
        self.calls = []
        response_list = list(responses or [ig_response()])

        def fetch(url, **kwargs):
            self.calls.append((url, kwargs))
            response = response_list.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        config = {"meta": {"enabled": True, **settings}}
        return meta.collect_meta_posts([IG] if societies is None else societies, config, previous or {}, NOW,
                                       ENV if environ is None else environ, fetch=fetch)

    def test_instagram_normalizes_caption_permalink_timestamp_and_media(self):
        result = self.collect()
        post = result["posts"][0]
        self.assertEqual(post["source_url"], "https://www.instagram.com/p/ABC/")
        self.assertEqual(post["text"], "Free pizza tomorrow!")
        self.assertEqual(post["published_at"], "2026-10-17T12:00:00Z")
        self.assertEqual(post["observed_at"], "2026-10-17T19:00:00Z")
        self.assertNotIn("starts_at", post)
        self.assertEqual(post["media_urls"], ["https://scontent.cdninstagram.com/photo.jpg"])
        self.assertEqual(post["origin"], "meta_api")
        self.assertEqual(result["accounts"][0]["status"], "ok")
        url, arguments = self.calls[0]
        self.assertEqual(url, "https://graph.facebook.com/v24.0/123456")
        self.assertIn("business_discovery.username(test.society)", arguments["params"]["fields"])
        self.assertEqual(arguments["headers"]["Authorization"], "Bearer " + ENV["META_ACCESS_TOKEN"])
        self.assertNotIn(ENV["META_ACCESS_TOKEN"], url + json.dumps(arguments["params"]))

    def test_facebook_requires_explicit_page_id_and_keeps_published_page_posts(self):
        posts = [fb_post(), fb_post("2", is_published=False), fb_post("3", **{"from": {"id": "222"}}), fb_post("4", is_published=None)]
        result = self.collect([FB], [{"data": posts}], facebook_page_ids={FB["facebook_url"]: "111"})
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(result["posts"][0]["source_url"], "https://www.facebook.com/111/posts/98765/")
        self.assertEqual(self.calls[0][0], "https://graph.facebook.com/v24.0/111/feed")
        self.assertEqual(result["posts"][0]["media_urls"], ["https://scontent.fbcdn.net/photo.jpg"])

    def test_credentials_and_pinned_version_are_required(self):
        for env, status in [({}, "needs_credentials"), ({**ENV, "META_INSTAGRAM_USER_ID": ""}, "needs_credentials"),
                            ({**ENV, "META_GRAPH_API_VERSION": ""}, "needs_configuration"),
                            ({**ENV, "META_GRAPH_API_VERSION": "v24.0/../../me"}, "needs_configuration")]:
            with self.subTest(env=env):
                result = self.collect(environ=env)
                self.assertEqual(result["accounts"][0]["status"], status)
                self.assertEqual(self.calls, [])

    def test_disabled_and_unmapped_accounts_do_not_make_requests(self):
        result = self.collect(enabled=False)
        self.assertEqual(result["status"]["status"], "disabled")
        self.assertEqual(self.calls, [])
        result = self.collect([FB])
        self.assertEqual(result["accounts"][0]["status"], "unmapped")
        self.assertEqual(self.calls, [])

    def test_permission_failures_never_persist_server_error_or_credentials(self):
        result = self.collect(responses=[{"error": {"code": 10, "message": "denied " + ENV["META_ACCESS_TOKEN"]}}])
        self.assertEqual(result["accounts"][0]["status"], "permission_denied")
        self.assertNotIn(ENV["META_ACCESS_TOKEN"], json.dumps(result))
        result = self.collect(responses=[RuntimeError("request failed ?access_token=" + ENV["META_ACCESS_TOKEN"])])
        self.assertEqual(result["accounts"][0]["status"], "error")
        self.assertNotIn(ENV["META_ACCESS_TOKEN"], json.dumps(result))

    def test_missing_business_discovery_is_reported_as_unsupported(self):
        result = self.collect(responses=[{"id": "123456"}])
        self.assertEqual(result["accounts"][0]["status"], "unsupported")
        self.assertEqual(result["posts"], [])

    def test_groups_are_explicitly_unsupported_and_public_success_is_not_api_success(self):
        result = self.collect([{"name": "A group", "facebook_url": "https://www.facebook.com/groups/society/"}])
        self.assertEqual(result["accounts"][0]["status"], "unsupported")
        self.assertEqual(self.calls, [])
        previous = {"accounts": [{"url": "https://www.instagram.com/test.society/", "last_success_at": "2026-10-17T12:00:00Z", "origin": "public_html"}]}
        result = self.collect(previous=previous, environ={})
        self.assertIsNone(result["accounts"][0]["last_success_at"])

    def test_instagram_cursor_pagination_is_rebuilt_without_next_url(self):
        result = self.collect(responses=[ig_response(paging={"cursors": {"after": "CURSOR_1="}}), ig_response([ig_post("DEF")])])
        self.assertEqual(len(result["posts"]), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertIn(".after(CURSOR_1=)", self.calls[1][1]["params"]["fields"])

    def test_facebook_next_url_token_is_discarded(self):
        next_url = "https://graph.facebook.com/v24.0/111/feed?after=NEXT&access_token=DO_NOT_FORWARD"
        result = self.collect([FB], [{"data": [fb_post()], "paging": {"next": next_url}}, {"data": [fb_post("next")]}],
                              facebook_page_ids={FB["facebook_url"]: "111"})
        self.assertEqual(len(result["posts"]), 2)
        self.assertEqual(self.calls[1][0], "https://graph.facebook.com/v24.0/111/feed")
        self.assertEqual(self.calls[1][1]["params"]["after"], "NEXT")
        self.assertNotIn("DO_NOT_FORWARD", json.dumps(self.calls))

    def test_external_or_different_account_next_urls_are_rejected(self):
        urls = ["https://attacker.example/v24.0/111/feed?after=NEXT",
                "https://graph.facebook.com@attacker.example/v24.0/111/feed?after=NEXT",
                "http://graph.facebook.com/v24.0/111/feed?after=NEXT",
                "https://graph.facebook.com/v24.0/222/feed?after=NEXT",
                "https://graph.facebook.com:443/v24.0/111/feed?after=NEXT"]
        for url in urls:
            with self.subTest(url=url):
                result = self.collect([FB], [{"data": [fb_post()], "paging": {"next": url}}],
                                      facebook_page_ids={FB["facebook_url"]: "111"})
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(result["accounts"][0]["status"], "error")
                self.assertEqual(len(result["posts"]), 1)  # Keep the valid first page.

    def test_injection_in_handles_ids_and_cursors_is_rejected(self):
        result = self.collect([{"name": "Bad", "instagram_url": "https://www.instagram.com/foo){id}"}])
        self.assertEqual(self.calls, [])
        self.assertEqual(result["posts"], [])
        result = self.collect([FB], facebook_page_ids={FB["facebook_url"]: "111/../../me"})
        self.assertEqual(result["accounts"][0]["status"], "unmapped")
        self.assertEqual(self.calls, [])
        result = self.collect(responses=[ig_response(paging={"cursors": {"after": "CURSOR){id}"}})])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["accounts"][0]["status"], "error")

    def test_requested_limits_bound_pages_posts_and_accounts(self):
        result = self.collect(responses=[ig_response([ig_post("A"), ig_post("B")])], posts_per_account=1)
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(len(self.calls), 1)
        result = self.collect(responses=[ig_response(paging={"cursors": {"after": "NEXT"}})], max_pages_per_account=1)
        self.assertEqual(len(self.calls), 1)
        result = self.collect([IG, {"name": "Another", "instagram_url": "https://www.instagram.com/another/"}], max_accounts=1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["accounts"][1]["status"], "deferred")

    def test_last_success_and_posts_survive_failed_refresh_until_retention_expires(self):
        previous = self.collect()
        previous["posts"].append({**previous["posts"][0], "source_url": "https://www.instagram.com/p/OLD/", "published_at": "2020-01-01T00:00:00Z"})
        result = self.collect(previous=previous, responses=[{"error": {"code": 190}}])
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(result["accounts"][0]["last_success_at"], "2026-10-17T19:00:00Z")
        self.assertEqual(result["accounts"][0]["status"], "needs_credentials")

    def test_removing_society_or_account_discards_retained_posts(self):
        previous = self.collect()
        for societies in ([], [{"name": "Test Society"}]):
            with self.subTest(societies=societies):
                result = self.collect(societies=societies, previous=previous)
                self.assertEqual(result["posts"], [])
                self.assertEqual(result["accounts"], [])
                self.assertEqual(result["status"]["status"], "no_accounts")
                self.assertEqual(result["status"]["accounts_checked"], 0)
                self.assertEqual(self.calls, [])

    def test_allowlist_removal_or_disabled_collector_discards_retained_posts(self):
        previous = self.collect()
        for settings in ({"instagram_handles": []}, {"instagram_handles": ["another"]}, {"enabled": False}):
            with self.subTest(settings=settings):
                result = self.collect(previous=previous, **settings)
                self.assertEqual(result["posts"], [])
                self.assertEqual(self.calls, [])

    def test_removing_facebook_mapping_discards_retained_posts(self):
        previous = self.collect([FB], [{"data": [fb_post()]}], facebook_page_ids={FB["facebook_url"]: "111"})
        self.assertEqual(len(previous["posts"]), 1)
        result = self.collect([FB], previous=previous, facebook_page_ids={})
        self.assertEqual(result["posts"], [])
        self.assertEqual(result["accounts"][0]["status"], "unmapped")
        self.assertEqual(self.calls, [])

    def test_failed_refresh_retains_only_current_eligible_account_history(self):
        previous = self.collect([IG, {"name": "Another", "instagram_url": "https://www.instagram.com/another/"}],
                                responses=[ig_response(), ig_response([ig_post("OTHER")])])
        self.assertEqual(len(previous["posts"]), 2)
        result = self.collect(previous=previous, responses=[{"error": {"code": 190}}])
        self.assertEqual([post["source_url"] for post in result["posts"]], ["https://www.instagram.com/p/ABC/"])
        self.assertEqual(result["accounts"][0]["status"], "needs_credentials")

    def test_missing_optional_media_and_invalid_timestamp_are_not_invented(self):
        result = self.collect(responses=[ig_response([ig_post(media_url=None, timestamp="tomorrow")])])
        self.assertEqual(result["posts"][0]["media_urls"], [])
        self.assertIsNone(result["posts"][0]["published_at"])

    def test_attempts_rotate_to_never_checked_then_oldest_accounts(self):
        societies = [{"name": name, "instagram_url": f"https://www.instagram.com/{name}/"} for name in ("a", "b", "c")]
        previous = {"accounts": [
            {"url": society["instagram_url"], "origin": "meta_api", "last_attempt_at": timestamp}
            for society, timestamp in zip(societies, [(NOW - timedelta(hours=1)).isoformat(), None,
                                                      (NOW - timedelta(hours=2)).isoformat()])]}
        first = self.collect(societies, previous=previous, max_accounts=1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("username(b)", self.calls[0][1]["params"]["fields"])
        by_url = {account["url"]: account for account in first["accounts"]}
        self.assertEqual(by_url[societies[0]["instagram_url"]]["last_attempt_at"], previous["accounts"][0]["last_attempt_at"])
        self.assertEqual(by_url[societies[2]["instagram_url"]]["last_attempt_at"], previous["accounts"][2]["last_attempt_at"])
        second = self.collect(societies, previous=first, max_accounts=1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("username(c)", self.calls[0][1]["params"]["fields"])
        self.assertEqual(second["status"]["accounts_checked"], 1)

    def test_unattempted_accounts_never_receive_new_attempt_timestamps(self):
        for settings, environment in [({"enabled": False}, ENV), ({}, {}),
                                      ({}, {**ENV, "META_GRAPH_API_VERSION": ""})]:
            result = self.collect(environ=environment, **settings)
            self.assertEqual(self.calls, [])
            self.assertIsNone(result["accounts"][0]["last_attempt_at"])
            self.assertIsNone(result["status"]["last_attempt_at"])
        result = self.collect([FB])
        self.assertIsNone(result["accounts"][0]["last_attempt_at"])

    def test_expired_token_or_rate_limit_stops_remaining_meta_accounts(self):
        societies = [IG, FB]
        for code, expected in [(190, "needs_credentials"), (4, "rate_limited"), (17, "rate_limited"), (613, "rate_limited")]:
            with self.subTest(code=code):
                result = self.collect(societies, responses=[{"error": {"code": code, "message": ENV["META_ACCESS_TOKEN"]}}],
                                      facebook_page_ids={FB["facebook_url"]: "111"})
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(result["accounts"][0]["status"], expected)
                self.assertEqual(result["accounts"][1]["status"], "deferred")
                self.assertIsNone(result["accounts"][1]["last_attempt_at"])
                self.assertEqual(result["status"]["stopped_after"], expected)
                self.assertEqual(result["status"]["accounts_checked"], 1)
                self.assertNotIn(ENV["META_ACCESS_TOKEN"], json.dumps(result))

    def test_http_429_stops_collection_without_reading_error_body(self):
        response = MagicMock()
        response.status_code = 429
        with patch.object(meta.requests, "get", return_value=response) as request:
            result = meta.collect_meta_posts([IG, {"name": "Another", "instagram_url": "https://instagram.com/other/"}],
                {"meta": {"enabled": True}}, {}, NOW, ENV)
        self.assertEqual(request.call_count, 1)
        response.iter_content.assert_not_called()
        self.assertEqual([account["status"] for account in result["accounts"]], ["rate_limited", "deferred"])
        self.assertIsNone(result["accounts"][1]["last_attempt_at"])

    def test_pagination_rate_limit_keeps_first_page_and_defers_other_accounts(self):
        result = self.collect([IG, {"name": "Another", "instagram_url": "https://instagram.com/other/"}],
            responses=[ig_response(paging={"cursors": {"after": "NEXT"}}), {"error": {"code": 4}}])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual([account["status"] for account in result["accounts"]], ["rate_limited", "deferred"])
        self.assertIsNone(result["accounts"][0]["last_success_at"])

    def test_multiple_account_list_is_collected_and_canonically_deduplicated(self):
        society = {**IG, "social_accounts": [
            {"platform": "instagram", "url": "https://instagram.com/TEST.SOCIETY/"},
            {"platform": "instagram", "url": "https://instagram.com/other/"},
            {"platform": "unknown", "url": "https://example.org"}, None]}
        result = self.collect([society], responses=[ig_response(), ig_response([ig_post("OTHER")])])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(result["accounts"]), 2)
        self.assertEqual(len(result["posts"]), 2)
        retained = self.collect([society], previous=result, environ={})
        self.assertEqual(len(retained["posts"]), 2)

    def test_account_ceiling_is_1000_but_default_remains_100(self):
        societies = [{"name": str(index), "instagram_url": f"https://www.instagram.com/society{index}/"} for index in range(1001)]
        result = self.collect(societies, responses=[ig_response([])] * 1000, max_accounts=9999)
        self.assertEqual(len(self.calls), 1000)
        self.assertEqual(result["status"]["accounts_checked"], 1000)
        self.assertEqual(result["accounts"][-1]["status"], "deferred")
        self.assertIsNone(result["accounts"][-1]["last_attempt_at"])
        self.collect(societies[:101], responses=[ig_response([])] * 100)
        self.assertEqual(len(self.calls), 100)


if __name__ == "__main__":
    unittest.main()
