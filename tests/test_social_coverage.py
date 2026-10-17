"""Society coverage is based on actual stored post observations, never bios."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("social_coverage", Path(__file__).parents[1] / "scripts/social_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)
NOW = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)
IG = "https://www.instagram.com/durhamsoc/"
FB = "https://www.facebook.com/durhamsoc/"
SOCIETY = {"name": "A society", "url": "https://durhamsu.com/groups/studentgroup/1", "instagram_url": IG}


def post(**changes):
    return {"id": "social-abc", "platform": "instagram", "account_url": IG,
            "source_url": "https://www.instagram.com/p/ABC/", "text": "A real post",
            "published_at": "2026-01-01T00:00:00Z", "observed_at": NOW.isoformat(),
            "origin": "public_html", **changes}


class CoverageTests(unittest.TestCase):
    def test_every_directory_society_gets_a_row(self):
        societies = [{"name": f"Society {index}", "url": f"https://durhamsu.com/groups/studentgroup/{index}"} for index in range(319)]
        result = coverage.build_coverage(societies, {}, NOW)
        self.assertEqual(len(result["societies"]), 319)
        self.assertEqual([row["name"] for row in result["societies"]], [society["name"] for society in societies])
        self.assertEqual(result["summary"], {"total_societies": 319, "societies_with_accounts": 0,
            "societies_with_recent_posts": 0, "societies_needing_accounts": 319,
            "societies_needing_attention": 0, "societies_stale": 0, "post_count": 0})
        self.assertEqual(result["societies"][0]["status"], "missing_accounts")

    def test_account_status_success_without_posts_stays_needs_attention(self):
        social = {"accounts": [{"url": IG, "platform": "instagram", "provider": "public_html", "status": "ok",
                               "last_success_at": NOW.isoformat(), "posts_collected": 0, "description": "Free food in profile bio"}]}
        result = coverage.build_coverage([SOCIETY], social, NOW)
        row = result["societies"][0]
        self.assertEqual(row["status"], "needs_attention")
        self.assertIsNone(row["latest_observation_at"])
        self.assertEqual(row["accounts"][0]["providers"][0]["status"], "ok")
        self.assertEqual(result["summary"]["societies_with_recent_posts"], 0)

    def test_fresh_observation_counts_even_if_post_was_published_long_ago(self):
        result = coverage.build_coverage([SOCIETY], {"posts": [post()]}, NOW)
        row = result["societies"][0]
        self.assertEqual(row["status"], "collected")
        self.assertEqual(row["posts_stored"], 1)
        self.assertEqual(row["latest_observation_at"], "2026-10-17T19:00:00Z")
        self.assertEqual(result["summary"]["societies_with_recent_posts"], 1)
        self.assertNotIn("complete", str(result))

    def test_retained_posts_older_than_36_hours_are_stale(self):
        old = post(observed_at=(NOW - timedelta(hours=36, seconds=1)).isoformat(), published_at=NOW.isoformat())
        result = coverage.build_coverage([SOCIETY], {"posts": [old]}, NOW)
        self.assertEqual(result["societies"][0]["status"], "stale")
        self.assertEqual(result["summary"]["societies_stale"], 1)
        self.assertEqual(result["summary"]["societies_with_recent_posts"], 0)
        boundary = post(observed_at=(NOW - timedelta(hours=36)).isoformat())
        self.assertEqual(coverage.build_coverage([SOCIETY], {"posts": [boundary]}, NOW)["societies"][0]["status"], "collected")

    def test_unsupported_facebook_group_remains_a_visible_gap(self):
        group = "https://m.facebook.com/groups/DurhamSociety/?ref=share"
        society = {"name": "Group society", "facebook_url": group}
        social = {"accounts": [{"url": "https://www.facebook.com/groups/durhamsociety/", "platform": "facebook",
                               "provider": "meta_api", "status": "unsupported", "error": "Group access unsupported"}],
                  "posts": [post(platform="facebook", account_url=group, source_url=FB + "posts/123/")]}
        result = coverage.build_coverage([society], social, NOW)
        row = result["societies"][0]
        self.assertEqual(row["status"], "needs_attention")
        self.assertEqual(row["account_count"], 1)
        self.assertEqual(row["posts_stored"], 0)
        self.assertEqual(row["accounts"][0]["status"], "unsupported")
        self.assertEqual(row["accounts"][0]["providers"][0]["status"], "unsupported")
        self.assertEqual(result["summary"]["societies_with_accounts"], 1)

    def test_provider_variants_match_canonical_accounts_and_posts_deduplicate(self):
        social = {"accounts": [{"url": "http://instagram.com/DurhamSoc?hl=en", "provider": "public_html", "status": "ok"},
                               {"url": IG, "provider": "meta_api", "status": "needs_credentials"}],
                  "posts": [post(account_url="https://instagram.com/DURHAMSOC", observed_at=(NOW - timedelta(days=2)).isoformat()),
                            post(source_url="https://www.instagram.com/reel/ABC/", origin="meta_api")]}
        result = coverage.build_coverage([SOCIETY], social, NOW)
        row = result["societies"][0]
        self.assertEqual(row["account_count"], 1)
        self.assertEqual(row["posts_stored"], 1)
        self.assertEqual(row["status"], "collected")
        self.assertEqual(len(row["accounts"][0]["providers"]), 2)
        self.assertEqual(result["summary"]["post_count"], 1)

    def test_shared_accounts_belong_to_each_society_but_global_posts_count_once(self):
        other = {**SOCIETY, "name": "Another society", "url": "https://durhamsu.com/groups/studentgroup/2"}
        result = coverage.build_coverage([SOCIETY, other], {"posts": [post(society="Unrelated display name")]}, NOW)
        self.assertEqual([row["posts_stored"] for row in result["societies"]], [1, 1])
        self.assertEqual(result["summary"]["societies_with_recent_posts"], 2)
        self.assertEqual(result["summary"]["post_count"], 1)

    def test_names_do_not_attribute_unlinked_posts(self):
        result = coverage.build_coverage([SOCIETY], {"posts": [post(account_url="https://www.instagram.com/other/", society=SOCIETY["name"])]}, NOW)
        self.assertEqual(result["societies"][0]["status"], "needs_attention")
        self.assertEqual(result["summary"]["post_count"], 0)

    def test_profile_bio_and_invalid_observations_never_claim_recent_collection(self):
        invalid_times = [None, "unknown", "2026-10-17T19:00:00", (NOW + timedelta(days=1)).isoformat()]
        for value in invalid_times:
            with self.subTest(value=value):
                result = coverage.build_coverage([SOCIETY], {"posts": [post(observed_at=value)]}, NOW)
                self.assertEqual(result["societies"][0]["status"], "needs_attention")
                self.assertIsNone(result["societies"][0]["latest_observation_at"])
        result = coverage.build_coverage([SOCIETY], {"posts": [post(source_url=IG)]}, NOW)
        self.assertEqual(result["societies"][0]["posts_stored"], 0)
        self.assertEqual(result["societies"][0]["status"], "needs_attention")

    def test_mixed_account_coverage_is_collected_with_failed_account_visible(self):
        society = {**SOCIETY, "facebook_url": FB}
        result = coverage.build_coverage([society], {"posts": [post()], "accounts": [
            {"platform": "facebook", "url": FB, "provider": "public_html", "status": "blocked", "error": "Login wall"}]}, NOW)
        row = result["societies"][0]
        self.assertEqual(row["status"], "collected")
        self.assertEqual(row["account_count"], 2)
        self.assertEqual(row["accounts"][1]["status"], "needs_attention")
        self.assertEqual(row["accounts"][1]["providers"][0]["status"], "blocked")

    def test_empty_directory_and_naive_clock(self):
        result = coverage.build_coverage([], {"posts": [post()]}, NOW)
        self.assertEqual(result["societies"], [])
        self.assertEqual(result["summary"]["post_count"], 0)
        with self.assertRaises(ValueError):
            coverage.build_coverage([SOCIETY], {}, datetime(2026, 10, 17))

    def test_multiple_discovered_accounts_and_legacy_fields_are_canonically_deduped(self):
        second = "https://www.instagram.com/secondsoc/"
        society = {**SOCIETY, "social_accounts": [
            {"platform": "instagram", "url": "http://instagram.com/DurhamSoc?hl=en",
             "evidence_url": SOCIETY["url"], "method": "su_profile"},
            {"platform": "instagram", "url": second, "evidence_url": "https://society.example/about", "method": "official_website"},
            {"platform": "facebook", "url": FB}],
            "discovery_status": "partial", "discovery_errors": [{"url": "https://society.example", "error": "Timeout"}]}
        social = {"posts": [post(), post(account_url=second, source_url="https://www.instagram.com/p/SECOND/")]}
        result = coverage.build_coverage([society], social, NOW)
        row = result["societies"][0]
        self.assertEqual(row["account_count"], 3)
        self.assertEqual(row["posts_stored"], 2)
        self.assertEqual(row["status"], "collected")
        self.assertEqual(row["accounts"][0]["url"], IG)
        self.assertEqual(row["accounts"][0]["method"], "su_profile")
        self.assertEqual(row["accounts"][1]["evidence_url"], "https://society.example/about")
        self.assertEqual(row["discovery_status"], "partial")
        self.assertEqual(row["discovery_errors"], society["discovery_errors"])

    def test_list_only_accounts_and_unsupported_groups_count_as_configured(self):
        society = {"name": "List-only", "social_accounts": [
            {"platform": "instagram", "url": IG},
            {"platform": "facebook", "url": "https://www.facebook.com/groups/example/"},
            {"platform": "instagram", "url": "https://www.facebook.com/wrongplatform/"},
            None, {"platform": "other", "url": "https://example.org"}]}
        result = coverage.build_coverage([society], {}, NOW)
        row = result["societies"][0]
        self.assertEqual(row["account_count"], 2)
        self.assertEqual(row["status"], "needs_attention")
        self.assertEqual(row["accounts"][1]["status"], "unsupported")
        self.assertEqual(result["summary"]["societies_needing_accounts"], 0)


if __name__ == "__main__":
    unittest.main()
