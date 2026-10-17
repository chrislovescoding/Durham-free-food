"""Run: python -m unittest discover -s tests -p 'test_ingestion.py' -v"""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("refresh", Path(__file__).parents[1] / "scripts/refresh.py")
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)

NOW = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)
SOURCE = {"id": "test-calendar", "type": "ics", "url": "https://example.org/events.ics", "society": "Test Society", "timezone": "Europe/London"}


def calendar(body):
    return "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n" + body + "\nEND:VEVENT\nEND:VCALENDAR"


def event(**changes):
    return {"id": "reviewed-1", "reviewed": True, "title": "Pizza evening", "society": "Test Society",
            "starts_at": "2026-10-18T18:00:00+01:00", "location": "Student room", "food": "Free pizza",
            "food_is_free": True, "source_url": "https://example.org/event", "verified_at": "2026-10-17T12:00:00Z", **changes}


def native_html(detail):
    tree = {"queries": [{"queryKey": ["EventDetails", 288, str(detail["id"])], "state": {"data": detail}}]}
    return '<script>self.__next_f.push([1,' + json.dumps("12:" + json.dumps(tree)) + '])</script>'


class ParsingTests(unittest.TestCase):
    def test_ics_preserves_real_url_timezone_and_unknown_entry_price(self):
        text = calendar("UID:abc\nDTSTART;TZID=Europe/London:20261018T183000\nDTEND;TZID=Europe/London:20261018T193000\nSUMMARY:Talk (+ Free Pizza!)\nDESCRIPTION:Visit https://luma.com/abcdefgh for details.\\nOpen to students.\nLOCATION:Room A\\, Science Site")
        events, candidates, seen = refresh.parse_ics(text, SOURCE, NOW)
        self.assertEqual(seen, 1)
        self.assertEqual(candidates, [])
        self.assertEqual(events[0]["starts_at"], "2026-10-18T18:30:00+01:00")
        self.assertEqual(events[0]["source_url"], "https://luma.com/abcdefgh")
        self.assertEqual(events[0]["location"], "Room A, Science Site")
        self.assertTrue(events[0]["food_is_free"])
        self.assertIsNone(events[0]["price"])
        self.assertIsNone(events[0]["signup_required"])

    def test_food_mentions_do_not_imply_free_food(self):
        for text in ("Free talk and pizza sold afterwards", "Free entry with pizza", "No free food", "There is no longer free pizza", "We hope free food will be available", "There will not be free pizza.", "Free pizza is no longer available.", "We might provide free food.", "A chance to win free pizza!"):
            with self.subTest(text=text):
                self.assertIsNone(refresh.food_evidence(text))
        self.assertEqual(refresh.food_evidence("Free vegan pizza afterwards"), "Free vegan pizza")

    def test_all_day_and_recurring_events_go_for_review(self):
        for time_fields in ("DTSTART;VALUE=DATE:20261018", "DTSTART:20261018T183000Z\nRRULE:FREQ=WEEKLY"):
            events, candidates, _ = refresh.parse_ics(calendar(time_fields + "\nSUMMARY:Free pizza"), SOURCE, NOW)
            self.assertEqual(events, [])
            self.assertEqual(len(candidates), 1)

    def test_cancelled_event_is_not_published(self):
        events, _, _ = refresh.parse_ics(calendar("DTSTART:20261018T183000Z\nSUMMARY:Free pizza\nSTATUS:CANCELLED"), SOURCE, NOW)
        self.assertEqual(events, [])

    def test_html_is_not_accepted_as_calendar(self):
        with self.assertRaises(ValueError):
            refresh.parse_ics("<html>Please sign in</html>", SOURCE, NOW)

    def test_jsonld_extracts_price_only_with_explicit_currency(self):
        html = '''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Event","name":"Free pizza","startDate":"2026-10-18T18:30:00+01:00","url":"https://example.org/pizza","location":{"name":"Room B"},"offers":{"price":"2.00","priceCurrency":"GBP"}}]}</script>'''
        events, _, _ = refresh.parse_jsonld(html, SOURCE, NOW)
        self.assertEqual(events[0]["price"], 2)
        self.assertEqual(events[0]["location"], "Room B")

    def test_ambiguous_autumn_clock_time_rejected(self):
        with self.assertRaises(ValueError):
            refresh.parse_datetime("2026-10-25T01:30:00", "Europe/London")
        self.assertEqual(refresh.parse_datetime("2026-10-25T01:30:00+00:00").hour, 1)

    def test_missing_timezone_rejected(self):
        with self.assertRaises(ValueError):
            refresh.parse_datetime("2026-10-18T18:30:00")

    def test_society_directory_deduplicates_and_skips_empty_images(self):
        html = '<a href="/groups/studentgroup/1/"><img></a><a href="/groups/studentgroup/1/">Society A</a><a href="https://other.example/groups/studentgroup/2/">Wrong site</a>'
        self.assertEqual(refresh.directory_societies(html, "https://www.durhamsu.com/groups/studentgroups/"), [{"name": "Society A", "url": "https://www.durhamsu.com/groups/studentgroup/1/"}])

    def test_society_links_exclude_su_chrome_and_social_sharing(self):
        html = '<header><a href="https://instagram.com/durham.su">SU</a></header><main><a href="https://www.instagram.com/example_society/">Society</a><a href="https://facebook.com/sharer/sharer.php">Share</a></main><footer><a href="https://facebook.com/durhamsu">SU</a></footer>'
        self.assertEqual(refresh.social_links(html, "https://www.durhamsu.com/groups/studentgroup/1/"), {"instagram_url": "https://www.instagram.com/example_society/"})

    def test_native_reads_public_data_without_executing_javascript(self):
        detail = {"id": 1, "name": "Talk", "description": "Free pizza afterwards!"}
        self.assertEqual(refresh.native_detail(native_html(detail)), detail)
        with self.assertRaises(ValueError):
            refresh.native_detail('<script>alert("fake data")</script>')

    def test_native_resolves_long_utf8_description_references(self):
        description = "<p>Free pizza 🍕 afterwards!</p>"
        detail = {"id": 1, "description": "$46"}
        text_record = "46:T" + format(len(description.encode("utf-8")), "x") + "," + description
        html = '<script>self.__next_f.push([1,' + json.dumps(text_record) + '])</script>' + native_html(detail)
        self.assertEqual(refresh.native_detail(html)["description"], description)

    def test_native_paginates_and_checks_descriptions_not_just_titles(self):
        source = {**SOURCE, "id": "native", "type": "native", "url": "https://example.org/events", "api_url": "https://api.example.org/events/"}
        first = {"id": 1, "slug": "talk", "name": "Talk", "startAt": "2026-10-18T18:00:00+01:00", "finishAt": "2026-10-18T19:00:00+01:00", "location": "Room A"}
        second = {**first, "id": 2, "slug": "noodles", "name": "Noodle challenge"}
        detail = {"id": 1, "description": "Free pizza afterwards!", "promoter": {"name": "Test Society"}, "currencyCode": "GBP", "priceFrom": "2.00"}
        responses = {"https://api.example.org/events/?page=2": json.dumps({"count": 2, "nextPage": None, "data": [second]}),
                     "https://example.org/event/talk/1": native_html(detail),
                     "https://example.org/event/noodles/2": native_html({"id": 2, "description": "A noodle competition"})}
        with patch.object(refresh, "fetch", side_effect=lambda url: responses[url]), patch.object(refresh.time, "sleep"):
            events, candidates, count = refresh.parse_native(json.dumps({"count": 2, "nextPage": 2, "data": [first]}), source, NOW)
        self.assertEqual(count, 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Talk")
        self.assertEqual(events[0]["society"], "Test Society")
        self.assertIsNone(events[0]["price"])
        self.assertEqual(events[0]["price_from"], 2)
        self.assertEqual(candidates[0]["kind"], "needs_food_confirmation")


class RefreshTests(unittest.TestCase):
    def test_keep_started_today_remove_yesterday_using_london_date(self):
        midnight_london = datetime(2026, 6, 2, 0, 10, tzinfo=refresh.LONDON)
        self.assertTrue(refresh.visible_today_or_future({"starts_at": "2026-06-01T23:05:00Z"}, midnight_london))
        self.assertFalse(refresh.visible_today_or_future({"starts_at": "2026-06-01T22:59:00Z"}, midnight_london))

    def test_failed_fetch_retains_good_events_and_original_verification(self):
        old_event = {**refresh.reviewed_event(event(), NOW), "source_id": SOURCE["id"]}
        previous = {"events": [old_event], "sources": [{"id": SOURCE["id"], "last_success_at": "2026-10-16T19:00:00Z"}]}
        with patch.object(refresh, "fetch", side_effect=RuntimeError("HTTP 503")):
            result, _ = refresh.refresh_events({"feeds": [SOURCE]}, previous, {"events": []}, NOW)
        self.assertEqual(result["events"], [old_event])
        self.assertEqual(result["sources"][0]["status"], "error")
        self.assertEqual(result["sources"][0]["last_success_at"], "2026-10-16T19:00:00Z")
        self.assertEqual(result["status"]["failed_sources"], 1)

    def test_successful_empty_calendar_removes_withdrawn_events(self):
        old_event = {**refresh.reviewed_event(event(), NOW), "source_id": SOURCE["id"]}
        with patch.object(refresh, "fetch", return_value="BEGIN:VCALENDAR\nEND:VCALENDAR"):
            result, _ = refresh.refresh_events({"feeds": [SOURCE]}, {"events": [old_event]}, {"events": []}, NOW)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["status"]["state"], "ok")

    def test_parse_error_preserves_that_events_last_good_details(self):
        old_event = {**refresh.reviewed_event(event(), NOW), "source_id": SOURCE["id"]}
        changed = calendar("DTSTART;VALUE=DATE:20261018\nSUMMARY:Free pizza\nURL:https://example.org/event")
        with patch.object(refresh, "fetch", return_value=changed):
            result, _ = refresh.refresh_events({"feeds": [SOURCE]}, {"events": [old_event]}, {"events": []}, NOW)
        self.assertEqual(result["events"], [old_event])
        self.assertEqual(result["status"]["state"], "partial")
        self.assertEqual(result["status"]["parse_error_count"], 1)

    def test_failed_directory_keeps_previous_directory(self):
        old = {"updated_at": "2026-10-16T19:00:00Z", "societies": [{"name": "Test", "url": "https://example.org"}]}
        with patch.object(refresh, "fetch", side_effect=RuntimeError("HTTP 503")):
            result, status = refresh.refresh_societies({"society_directory": "https://example.org"}, old, NOW)
        self.assertEqual(result, old)
        self.assertEqual(status["status"], "error")

    def test_reviewed_manual_price_and_validation(self):
        self.assertEqual(refresh.reviewed_event(event(price=3), NOW)["price"], 3)
        for changes in ({"reviewed": False}, {"food_is_free": None}, {"price": -1}, {"price": float("nan")}, {"dietary": "vegan"}, {"starts_at": "2026-10-18"}, {"verified_at": "2026-10-20T12:00:00Z"}, {"source_url": "javascript:alert(1)"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                refresh.reviewed_event(event(**changes), NOW)

    def test_invalid_manual_edit_preserves_previous_reviewed_events(self):
        old_event = refresh.reviewed_event(event(), NOW)
        result, _ = refresh.refresh_events({"feeds": []}, {"events": [old_event]}, {"events": [event(price=-2)]}, NOW)
        self.assertEqual(result["events"], [old_event])
        self.assertEqual(result["status"]["state"], "partial")


if __name__ == "__main__":
    unittest.main()
