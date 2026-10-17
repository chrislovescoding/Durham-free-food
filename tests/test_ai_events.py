from copy import deepcopy
from datetime import datetime, timezone
import unittest

from scripts.ai_events import EXTRACTION_SCHEMA, evidence_signature, integrate_ai, validate_extraction


NOW = datetime(2026, 10, 17, 13, tzinfo=timezone.utc)
POST = {"id": "post-1", "platform": "instagram", "society": "Example Society",
        "source_url": "https://www.instagram.com/p/EXAMPLE/", "published_at": "2026-10-16T12:00:00Z",
        "extraction_model": "gpt-6-luna"}
EVENT = {
    "title": "Dinner", "starts_at": "2026-10-23T18:30:00+01:00", "ends_at": None,
    "location": "Elvet Methodist Church", "food": "Free dinner", "food_is_free": True,
    "price": None, "signup_required": None, "signup_deadline": None,
    "eligibility": "Free on your first visit; returning visitors pay £4.", "dietary": [],
    "description": "Dinner and fellowship.", "needs_review": False,
    "evidence": {"food": "First dinner is free", "date": "23 October 2026", "time": "18:30",
                 "location": "Elvet Methodist Church", "eligibility": "£4 afterwards"},
}


def extract(event=None, post=None):
    return validate_extraction({"events": [deepcopy(event if event is not None else EVENT)], "review_reason": None},
                               post or POST, NOW)


class AiEventTests(unittest.TestCase):
    def test_eligible_free_food_is_visible_and_provenance_is_honest(self):
        events, queue = extract()
        self.assertFalse(queue)
        event = events[0]
        self.assertIn("returning visitors pay £4", event["food"])
        self.assertEqual(event["society"], POST["society"])
        self.assertEqual(event["source_url"], POST["source_url"])
        self.assertEqual(event["source_id"], "ai-instagram")
        self.assertFalse(event["extraction"]["human_reviewed"])
        self.assertEqual(event["extraction"]["model"], "gpt-6-luna")
        self.assertIsNone(event["price"])

    def test_schema_and_validator_require_all_fields(self):
        self.assertEqual(set(EVENT), set(EXTRACTION_SCHEMA["properties"]["events"]["items"]["required"]))
        for field in EVENT:
            with self.subTest(field=field):
                event = deepcopy(EVENT)
                del event[field]
                events, queue = extract(event)
                self.assertFalse(events)
                self.assertTrue(queue)

    def test_model_cannot_assign_source_identity(self):
        event = {**EVENT, "society": "Invented society", "source_url": "https://example.org/"}
        self.assertFalse(extract(event)[0])
        self.assertFalse(extract(post={**POST, "source_url": "https://instagram.com.evil.example/p/ABC/"})[0])

    def test_nonfree_uncertain_or_missing_evidence_go_to_review(self):
        for change in ({"food_is_free": False}, {"needs_review": True},
                       {"evidence": {**EVENT["evidence"], "time": ""}},
                       {"evidence": {**EVENT["evidence"], "eligibility": None}},
                       {"eligibility": None}):
            with self.subTest(change=change):
                events, queue = extract({**EVENT, **change})
                self.assertFalse(events)
                self.assertEqual(len(queue), 1)

    def test_post_level_uncertainty_prevents_publication(self):
        events, queue = validate_extraction({"events": [EVENT], "review_reason": "Year is unclear"}, POST, NOW)
        self.assertFalse(events)
        self.assertEqual(queue[0]["reason"], "Year is unclear")

    def test_invalid_datetime_ranges_prices_and_types_are_queued(self):
        for change in ({"starts_at": "2026-10-23T18:30:00"}, {"ends_at": "2026-10-23T17:00:00Z"},
                       {"signup_deadline": "2026-10-24T12:00:00Z"}, {"price": True}, {"price": -1},
                       {"price": float("nan")}, {"signup_required": "unknown"}, {"dietary": [None]}):
            with self.subTest(change=change):
                events, queue = extract({**EVENT, **change})
                self.assertFalse(events)
                self.assertTrue(queue)

    def test_yesterday_removed_but_started_today_kept(self):
        self.assertEqual(extract({**EVENT, "starts_at": "2026-10-16T18:30:00+01:00"}), ([], []))
        self.assertEqual(len(extract({**EVENT, "starts_at": "2026-10-17T10:00:00+01:00"})[0]), 1)

    def test_multiple_dated_occurrences_and_duplicates(self):
        payload = {"events": [EVENT, EVENT, {**EVENT, "starts_at": "2026-10-30T18:30:00+00:00"}], "review_reason": None}
        events, queue = validate_extraction(payload, POST, NOW)
        self.assertFalse(queue)
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["id"], events[1]["id"])

    def test_manual_precedence_queue_replacement_and_repeated_merge(self):
        ai, _ = extract()
        manual = {**ai[0], "id": "reviewed", "source_id": "manual", "title": "Human reviewed dinner"}
        events = {"events": [manual], "status": {}}
        queue = {"candidates": [{"source_id": "social-instagram", "post_id": POST["id"]},
                                {"source_id": "manual", "post_id": POST["id"]},
                                {"source_id": "social-instagram", "post_id": "unprocessed"},
                                {"source_id": "social-instagram", "post_id": "negative"}]}
        stored = {"events": ai, "queue": [], "processed_post_ids": [POST["id"], "negative"]}
        integrate_ai(events, queue, stored, NOW)
        integrate_ai(events, queue, stored, NOW)
        self.assertEqual(events["events"], [manual])
        self.assertEqual(queue["candidates"], [{"source_id": "manual", "post_id": POST["id"]},
                                               {"source_id": "social-instagram", "post_id": "unprocessed"}])
        self.assertEqual(events["status"]["ai_event_count"], 0)

    def test_stored_ai_records_revalidated_and_expired(self):
        ai, _ = extract()
        stored = {"events": [ai[0], {**ai[0], "food_is_free": False}], "queue": []}
        events, queue = {"events": [], "status": {}}, {"candidates": []}
        integrate_ai(events, queue, stored, NOW)
        self.assertEqual(len(events["events"]), 1)
        self.assertEqual(len(queue["candidates"]), 1)
        self.assertEqual(events["events"][0]["food"].count(EVENT["eligibility"]), 1)
        integrate_ai(events, queue, {"events": ai, "queue": []}, datetime(2026, 10, 24, tzinfo=timezone.utc))
        self.assertFalse(events["events"])

    def test_changed_posts_do_not_publish_or_hide_review_queue(self):
        post = {**POST, "text": "Free dinner", "media_urls": ["https://cdn.example/post.png?expires=1"]}
        signature = evidence_signature(post)
        ai, _ = extract(post=post)
        ai[0]["input_signature"] = signature
        stored = {"events": ai, "queue": [], "processed_post_ids": [post["id"]],
                  "processed_signatures": {post["id"]: signature}}
        events, queue = {"events": [], "status": {}}, {"candidates": [{"source_id": "social-instagram", "post_id": post["id"]}]}
        integrate_ai(events, queue, stored, NOW, posts=[{**post, "text": "Dinner is cancelled"}])
        self.assertFalse(events["events"])
        self.assertEqual(len(queue["candidates"]), 1)
        self.assertEqual(events["status"]["ai_stale_post_count"], 1)
        renewed = {**post, "media_urls": ["https://cdn.example/post.png?expires=2"]}
        self.assertEqual(signature, evidence_signature(renewed))
        integrate_ai(events, queue, stored, NOW, posts=[renewed])
        self.assertEqual(len(events["events"]), 1)
        self.assertFalse(queue["candidates"])
        self.assertEqual(events["events"][0]["input_signature"], signature)


if __name__ == "__main__":
    unittest.main()
