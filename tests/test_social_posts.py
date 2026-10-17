"""Public social collection contract and conservative attribution tests."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("social_posts", Path(__file__).parents[1] / "scripts/social_posts.py")
social = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(social)
NOW = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)
IG = "https://www.instagram.com/durhamsoc/"
FB = "https://www.facebook.com/durhamsoc/"
SOCIETY = {"name": "Durham Test Society", "instagram_url": IG, "facebook_url": FB}


def script(data):
    return '<script type="application/json">' + json.dumps(data) + '</script>'


def instagram_node(**changes):
    return {"shortcode": "Example42", "owner": {"username": "durhamsoc"},
            "edge_media_to_caption": {"edges": [{"node": {"text": "Free pizza on 18 October 2026 at 18:00 in Room A."}}]},
            "taken_at_timestamp": int(NOW.timestamp()), **changes}


def facebook_post(**changes):
    return {"@type": "SocialMediaPosting", "url": FB + "posts/123/",
            "author": {"url": FB}, "articleBody": "Free cake tomorrow!",
            "datePublished": "2026-10-17T15:00:00+01:00", **changes}


def parse(data, url=IG):
    return social.parse_public_posts(script(data), url, "Durham Test Society", NOW)


class PublicParserTests(unittest.TestCase):
    def test_instagram_captions_preserve_source_and_publication_timestamp(self):
        result = parse({"graphql": {"shortcode_media": instagram_node()}})
        post = result["posts"][0]
        self.assertEqual(post["source_url"], "https://www.instagram.com/p/Example42/")
        self.assertEqual(post["published_at"], "2026-10-17T19:00:00Z")
        self.assertIn("18 October", post["text"])
        self.assertNotIn("starts_at", post)
        self.assertEqual(post["origin"], "public_html")

    def test_instagram_owner_timeline_without_per_media_owner(self):
        node = instagram_node()
        del node["owner"]
        data = {"user": {"username": "durhamsoc", "edge_owner_to_timeline_media": {"edges": [{"node": node}]}}}
        self.assertEqual(len(parse(data)["posts"]), 1)
        data["user"]["username"] = "othersoc"
        self.assertEqual(parse(data)["posts"], [])

    def test_owner_mismatch_not_overridden_by_timeline(self):
        node = instagram_node(owner={"username": "othersoc"})
        data = {"username": "durhamsoc", "edge_owner_to_timeline_media": {"edges": [{"node": node}]}}
        self.assertEqual(parse(data)["posts"], [])

    def test_ordinary_public_embed_wrapped_json_is_decoded_without_javascript(self):
        context = {"context": {"username": "durhamsoc", "graphql_media": [{"shortcode_media": instagram_node()}]}}
        wrapper = {"require": [["PolarisEmbedSimple", "init", [], [{"contextJSON": json.dumps(context)}]]]}
        html = '<script>requireLazy(["ServerJS"],function(ServerJS){var s=(new ServerJS());s.handle(' + json.dumps(wrapper) + ');});</script>'
        result = social.parse_public_posts(html, IG, "Durham Test Society", NOW)
        self.assertEqual(len(result["posts"]), 1)

    def test_profile_meta_and_bio_are_never_posts(self):
        html = '<title>Durham Test (@durhamsoc)</title><meta property="og:description" content="Free food every week!"><script type="application/ld+json">' + json.dumps({"@type": "ProfilePage", "description": "Free pizza", "url": IG}) + '</script>'
        self.assertEqual(social.parse_public_posts(html, IG, "Test", NOW)["posts"], [])
        self.assertEqual(parse({"user": {"username": "durhamsoc", "biography": "Free pizza"}})["posts"], [])

    def test_login_wall_distinct_from_an_empty_public_shell(self):
        html = '<title>Log in • Instagram</title><form action="/accounts/login/"></form>'
        self.assertEqual(social.parse_public_posts(html, IG, "Test", NOW)["access"], "login_required")
        self.assertEqual(social.parse_public_posts('<title>Durham Society</title>', IG, "Test", NOW)["access"], "public")

    def test_malformed_json_and_javascript_are_not_executed_or_guessed(self):
        for html in ('<script type="application/json">{broken</script>', '<script>window._sharedData = alert("free pizza")</script>', '<script>eval("free pizza")</script>'):
            self.assertEqual(social.parse_public_posts(html, IG, "Test", NOW)["posts"], [])
        self.assertEqual(parse(instagram_node(edge_media_to_caption=["bad"]))["posts"], [])
        self.assertEqual(parse(instagram_node(edge_media_to_caption={"edges": [{"node": {"text": 22}}]}))["posts"], [])
        self.assertEqual(parse({"@type": {"invalid": True}})["posts"], [])

    def test_comments_and_followers_are_excluded_even_with_post_like_fields(self):
        data = {"comments": [instagram_node(shortcode="Comment42")], "edge_media_to_parent_comment": {"node": instagram_node()}, "followers": [instagram_node()]}
        self.assertEqual(parse(data)["posts"], [])

    def test_actual_caption_only_not_location_or_alt_text(self):
        node = instagram_node(edge_media_to_caption={"edges": []}, accessibility_caption="free food", location={"name": "free pizza"})
        self.assertEqual(parse(node)["posts"], [])

    def test_facebook_jsonld_has_verifiable_page_authorship(self):
        result = parse(facebook_post(), FB)
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(result["posts"][0]["published_at"], "2026-10-17T14:00:00Z")
        self.assertEqual(parse(facebook_post(author={"url": "https://www.facebook.com/another/"}), FB)["posts"], [])
        self.assertEqual(parse(facebook_post(url="https://www.facebook.com/another/posts/123/"), FB)["posts"], [])

    def test_facebook_url_can_establish_page_owner_without_an_author(self):
        data = facebook_post()
        del data["author"]
        self.assertEqual(len(parse(data, FB)["posts"]), 1)
        data["url"] = "https://www.facebook.com/another/posts/123/"
        self.assertEqual(parse(data, FB)["posts"], [])

    def test_facebook_hydration_message_but_not_comment_text(self):
        story = {"__typename": "Story", "url": FB + "posts/432/", "actors": [{"url": FB}], "message": {"text": "Free cake"}, "creation_time": int(NOW.timestamp()), "feedback": {"comments": [facebook_post()]}}
        result = parse(story, FB)
        self.assertEqual([post["text"] for post in result["posts"]], ["Free cake"])

    def test_no_guess_for_missing_or_naive_timestamps(self):
        for timestamp in (None, "tomorrow", "2026-10-17T14:00:00"):
            self.assertIsNone(parse(facebook_post(datePublished=timestamp), FB)["posts"][0]["published_at"])

    def test_instagram_reels_and_tracking_urls_are_canonical(self):
        self.assertEqual(social.canonical_post_url("https://instagram.com/reel/ABC_12/?igsh=abc"), "https://www.instagram.com/p/ABC_12/")
        self.assertEqual(social.canonical_post_url("https://facebook.com/permalink.php?story_fbid=999&id=123"), "https://www.facebook.com/123/posts/999/")
        self.assertEqual(social.canonical_post_url("https://m.facebook.com/story.php?id=123&story_fbid=999"), "https://www.facebook.com/123/posts/999/")

    def test_untrusted_urls_and_private_groups_not_fetched(self):
        for url in ("https://instagram.com.attacker.example/p/ABC/", "http://localhost/p/ABC/", "https://someone:secret@instagram.com/p/ABC/", "https://instagram.com:1234/p/ABC/"):
            self.assertIsNone(social.canonical_post_url(url))
        self.assertIsNone(social.canonical_account_url("https://facebook.com/groups/123/"))
        self.assertIsNone(social.canonical_account_url("https://instagram.com/p/ABC/"))

    def test_named_facebook_pages_normalize_to_stable_numeric_identity(self):
        for url, identity in (
            ("https://www.facebook.com/p/Durham-Poker-Society-100089072551814/", "100089072551814"),
            ("https://www.facebook.com/p/DU-Expedition-Society-61552033799388/", "61552033799388"),
            ("https://m.facebook.com/p/Durham-Poker-Society-100089072551814?ref=search#about", "100089072551814"),
        ):
            with self.subTest(url=url):
                self.assertEqual(social.canonical_account_url(url), "https://www.facebook.com/" + identity + "/")

    def test_named_facebook_page_route_rejects_untrusted_or_nonprofile_urls(self):
        for url in (
            "https://facebook.com/p/Durham-Poker-Society/",
            "https://facebook.com/p/Durham-Poker-Society-abc/",
            "https://facebook.com/p/-100089072551814/",
            "https://facebook.com/p/Durham-Poker-Society-100089072551814/posts/123/",
            "https://facebook.com/p/Durham-Poker-Society-100089072551814/../",
            "https://facebook.com/p/Durham%2FPoker-Society-100089072551814/",
            "https://facebook.com//p/Durham-Poker-Society-100089072551814/",
            "https://facebook.com/p/Durham-Poker-Society-" + "1" * 41 + "/",
            "https://facebook.com.attacker.example/p/Durham-Poker-Society-100089072551814/",
            "https://someone:secret@facebook.com/p/Durham-Poker-Society-100089072551814/",
            "https://facebook.com:9999/p/Durham-Poker-Society-100089072551814/",
            "https://instagram.com/p/Durham-Poker-Society-100089072551814/",
        ):
            with self.subTest(url=url):
                self.assertIsNone(social.canonical_account_url(url))


class CollectionTests(unittest.TestCase):
    def collect(self, fetch, societies=None, previous=None, **settings):
        return social.collect_social_posts(societies or [SOCIETY], {"public_embeds": False, **settings}, previous or {}, NOW, fetch=fetch)

    def test_named_facebook_page_deduplicates_and_retains_page_owned_posts(self):
        named = "https://www.facebook.com/p/Durham-Poker-Society-100089072551814/"
        canonical = "https://www.facebook.com/100089072551814/"
        seen = []
        def fetch(url):
            seen.append(url)
            return script(facebook_post(url=canonical + "posts/123/", author={"url": named}))
        society = {"name": "Durham Poker Society", "facebook_url": named,
                   "social_accounts": [{"platform": "facebook", "url": canonical}]}
        result = self.collect(fetch, [society])
        self.assertEqual(seen, [canonical])
        self.assertEqual(result["status"]["accounts_total"], 1)
        self.assertEqual(result["accounts"][0]["status"], "ok")
        self.assertEqual(result["posts"][0]["account_url"], canonical)
        self.assertEqual(result["posts"][0]["source_url"], canonical + "posts/123/")

    def test_rate_limit_stops_platform_but_other_platform_can_complete(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return {"status_code": 429} if "instagram" in url else script(facebook_post())
        societies = [SOCIETY, {"name": "Other", "instagram_url": "https://instagram.com/other/"}]
        result = self.collect(fetch, societies)
        self.assertEqual(seen, [IG, FB])
        self.assertEqual([account["status"] for account in result["accounts"]], ["rate_limited", "ok", "deferred"])
        self.assertEqual(result["status"]["request_count"], 2)

    def test_multiaccount_list_and_legacy_links_are_canonically_deduplicated(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return '<title>Public profile shell</title>'
        society = {**SOCIETY, "social_accounts": [
            {"platform": "instagram", "url": "https://instagram.com/durhamsoc/?igsh=tracking"},
            {"platform": "instagram", "url": "https://instagram.com/durhamsoc_events/"},
            {"platform": "facebook", "url": "https://m.facebook.com/durhamsoc/?ref=tracking"},
            {"platform": "facebook", "url": "https://facebook.com/durhamsoc_events/"},
            {"platform": "facebook", "url": "https://instagram.com/wrong_platform/"},
        ]}
        result = self.collect(fetch, [society])
        self.assertEqual(seen, [IG, FB, "https://www.instagram.com/durhamsoc_events/", "https://www.facebook.com/durhamsoc_events/"])
        self.assertEqual(result["status"]["accounts_total"], 4)
        self.assertEqual(result["status"]["accounts_checked"], 4)
        self.assertTrue(result["status"]["window_limited"])

    def test_unsupported_groups_are_reported_without_fetching_or_using_budget(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return script(instagram_node())
        society = {"name": "Test", "facebook_url": "https://m.facebook.com/groups/TestGroup/?invite=secret#members",
                   "social_accounts": [{"platform": "facebook", "url": "https://facebook.com/groups/testgroup/posts/123/?ref=secret"},
                                       {"platform": "instagram", "url": IG}]}
        result = self.collect(fetch, [society], max_accounts=1)
        self.assertEqual(seen, [IG])
        self.assertEqual(result["status"]["accounts_total"], 2)
        self.assertEqual(result["status"]["accounts_unsupported"], 1)
        group = next(account for account in result["accounts"] if account["status"] == "unsupported")
        self.assertEqual(group["url"], "https://www.facebook.com/groups/testgroup/")
        self.assertEqual(group["posts_collected"], 0)
        self.assertIsNone(group["last_attempt_at"])
        self.assertNotIn("secret", json.dumps(result))

    def test_config_can_cover_319_societies_on_both_platforms(self):
        societies = [{"name": f"Society {index}", "social_accounts": [
            {"platform": "instagram", "url": f"https://instagram.com/society{index}/"},
            {"platform": "facebook", "url": f"https://facebook.com/society{index}/"}]} for index in range(319)]
        result = self.collect(lambda url: '<title>Public shell</title>', societies, max_accounts=1000, max_requests=2500)
        self.assertEqual(result["status"]["accounts_checked"], 638)
        self.assertEqual(result["status"]["request_count"], 638)
        self.assertTrue(all(account["status"] == "no_public_posts" for account in result["accounts"]))

    def test_large_config_still_has_a_hard_account_ceiling(self):
        societies = [{"name": f"Society {index}", "instagram_url": f"https://instagram.com/society{index}/"} for index in range(1001)]
        result = self.collect(lambda url: '<title>Public shell</title>', societies, max_accounts=100_000, max_requests=100_000)
        self.assertEqual(result["status"]["accounts_checked"], 1000)
        self.assertEqual(result["accounts"][-1]["status"], "deferred")

    def test_failures_retain_last_good_posts_without_claiming_success(self):
        previous = self.collect(lambda url: script(instagram_node()) if "instagram" in url else script(facebook_post()))
        result = self.collect(lambda url: {"status_code": 403}, previous=previous)
        self.assertEqual(len(result["posts"]), 2)
        self.assertTrue(all(account["status"] == "access_denied" for account in result["accounts"]))
        self.assertTrue(all(account["posts_collected"] == 0 for account in result["accounts"]))
        self.assertEqual(result["status"]["posts_collected"], 0)

    def test_empty_shell_is_no_public_posts_not_success(self):
        result = self.collect(lambda url: '<meta property="og:description" content="free pizza every Tuesday">')
        self.assertEqual(result["posts"], [])
        self.assertTrue(all(account["status"] == "no_public_posts" for account in result["accounts"]))
        self.assertEqual(result["status"]["accounts_successful"], 0)

    def test_embed_fallback_collects_real_caption_when_public_profile_is_empty(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return script(instagram_node()) if url.endswith("/embed/") else '<title>Durham Society</title>'
        result = self.collect(fetch, [{"name": "Society", "instagram_url": IG}], public_embeds=True)
        self.assertEqual(seen, [IG, IG + "embed/"])
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(result["accounts"][0]["status"], "ok")

    def test_embed_posts_are_not_refetched_via_profile_permalink_links(self):
        seen = []
        def fetch(url):
            seen.append(url)
            if url == IG:
                return ''.join(f'<a href="/p/Example{i}/">Post</a>' for i in range(6))
            if url == IG + "embed/":
                return script([instagram_node(shortcode=f"Example{i}") for i in range(6)])
            return {"status_code": 403}
        result = self.collect(fetch, [{"name": "Society", "instagram_url": IG}], public_embeds=True)
        self.assertEqual(seen, [IG, IG + "embed/"])
        self.assertEqual(len(result["posts"]), 6)
        self.assertEqual(result["accounts"][0]["status"], "ok")

    def test_denial_does_not_trigger_embed_or_permalink_requests(self):
        for code in (401, 403, 429):
            seen = []
            def fetch(url):
                seen.append(url)
                return {"status_code": code}
            self.collect(fetch, [{"name": "Society", "instagram_url": IG}], public_embeds=True)
            self.assertEqual(seen, [IG])

    def test_permalink_following_requires_matching_post_and_owner(self):
        html = '<a href="/p/Expected/">post</a>'
        def fetch(url):
            return html if url == IG else script([instagram_node(shortcode="Expected", owner={"username": "other"}), instagram_node(shortcode="Recommended")])
        result = self.collect(fetch, [{"name": "Society", "instagram_url": IG}])
        self.assertEqual(result["posts"], [])

    def test_current_and_previous_reel_duplicates_merge(self):
        old = social._make_post("https://www.instagram.com/reel/Example42/", "Old caption", NOW.timestamp(), IG, "Test", NOW)
        result = self.collect(lambda url: script(instagram_node()), [{"name": "Test", "instagram_url": IG}], previous={"posts": [old]})
        self.assertEqual(len(result["posts"]), 1)
        self.assertIn("Free pizza", result["posts"][0]["text"])

    def test_successful_short_window_retains_previously_seen_posts(self):
        old_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        old = social._make_post("https://www.instagram.com/p/Older/", "Free pizza next month", old_time.timestamp(), IG, "Test", old_time)
        result = self.collect(lambda url: script(instagram_node()), [{"name": "Test", "instagram_url": IG}], previous={"posts": [old]})
        self.assertEqual(len(result["posts"]), 2)
        saved = next(post for post in result["posts"] if post["source_url"].endswith("/Older/"))
        self.assertEqual(saved["observed_at"], "2026-10-15T12:00:00Z")
        self.assertTrue(result["accounts"][0]["window_limited"])

    def test_storage_window_is_separate_from_eight_post_refresh_window(self):
        old_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        previous = {"posts": [social._make_post(f"https://www.instagram.com/p/Older{i}/", "Free pizza next month", old_time.timestamp(), IG, "Test", old_time) for i in range(12)]}
        result = self.collect(lambda url: script(instagram_node()), [{"name": "Test", "instagram_url": IG}], previous=previous)
        self.assertEqual(len(result["posts"]), 13)
        self.assertEqual(result["accounts"][0]["retained_count"], 13)
        limited = self.collect(lambda url: script(instagram_node()), [{"name": "Test", "instagram_url": IG}], previous=previous, max_stored_posts_per_account=10)
        self.assertEqual(len(limited["posts"]), 10)
        self.assertTrue(limited["accounts"][0]["storage_limit_reached"])

    def test_retention_limits_remove_old_posts_and_unlisted_accounts(self):
        old = social._make_post("https://www.instagram.com/p/Old/", "Free pizza", "2025-01-01T12:00:00Z", IG, "Test", NOW)
        foreign = social._make_post("https://www.instagram.com/p/Foreign/", "Free pizza", NOW.timestamp(), "https://www.instagram.com/removed/", "Removed", NOW)
        result = self.collect(lambda url: {"status_code": 403}, previous={"posts": [old, foreign]})
        self.assertEqual(result["posts"], [])

    def test_request_limit_and_disabled_setting_issue_no_extra_requests(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return script(instagram_node())
        result = self.collect(fetch, max_requests=1)
        self.assertEqual(seen, [IG])
        self.assertEqual(result["accounts"][1]["status"], "deferred")
        self.collect(fetch, enabled=False)
        self.assertEqual(seen, [IG])

    def test_daily_limit_rotates_to_previously_deferred_accounts(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return script(instagram_node()) if "instagram" in url else script(facebook_post())
        previous = self.collect(fetch, max_requests=1)
        self.collect(fetch, previous=previous, max_requests=1)
        self.assertEqual(seen, [IG, FB])

    def test_redirect_to_login_records_access_requirement(self):
        result = self.collect(lambda url: {"text": "<html></html>", "url": "https://www.instagram.com/accounts/login/"}, [{"name": "Society", "instagram_url": IG}])
        self.assertEqual(result["accounts"][0]["status"], "login_required")

    def test_malformed_fetch_failures_are_sanitized_and_not_fatal(self):
        def fetch(url):
            raise RuntimeError("sensitive internal payload")
        result = self.collect(fetch)
        self.assertEqual(len(result["accounts"]), 2)
        self.assertNotIn("sensitive", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
