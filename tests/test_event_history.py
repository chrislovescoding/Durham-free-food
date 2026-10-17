from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from scripts.ai_events import evidence_signature
from scripts.ai_posts import MODEL, VERSION, prepare_input
from scripts.recover_event_history import recover_history
from scripts.refresh import annotate_event_history, refresh_events, write_json


NOW = datetime(2026, 10, 17, 13, tzinfo=timezone.utc)
EVENT = {
    'title': 'Pizza social', 'starts_at': '2026-10-08T19:30:00+01:00',
    'ends_at': None, 'location': 'Durham SU', 'food': 'Free pizza',
    'food_is_free': True, 'price': None, 'signup_required': None,
    'signup_deadline': None, 'eligibility': None, 'dietary': [],
    'description': 'Beginner session', 'needs_review': False,
    'evidence': {'food': 'Free pizza', 'date': '8 October 2026',
                 'time': '7:30pm', 'location': 'Durham SU', 'eligibility': None},
}


class HistoryReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'work/artifact'
        self.image = self.folder / 'image-cache/post' / ('a' * 24 + '.png')
        self.image.parent.mkdir(parents=True)
        Image.new('RGB', (10, 10), 'red').save(self.image)
        self.post = {'id': 'post-1', 'platform': 'instagram', 'society': 'Bridge Club',
                     'source_url': 'https://www.instagram.com/p/EXAMPLE/',
                     'text': 'Free pizza! 8 October 2026 at 7:30pm, Durham SU',
                     'published_at': '2026-10-06T13:00:00Z',
                     'media_urls': ['https://scontent.cdninstagram.com/pizza.jpg'],
                     'image_ocr': {'coverage_complete': True, 'expected_images': 1,
                                   'images': [{'image_id': 'a' * 24, 'status': 'no_text'}]}}
        self.entry = {'post_id': self.post['id'], 'source_url': self.post['source_url'],
                      'model': MODEL, 'fingerprint': prepare_input(self.post, self.folder)[1],
                      'updated_at': '2026-10-17T12:00:00Z', 'decision': {'probability': 0.9},
                      'extraction': {'events': [deepcopy(EVENT)], 'review_reason': None}}
        self.stored = {'events': [], 'queue': [], 'processed_post_ids': ['post-1'],
                       'processed_signatures': {'post-1': evidence_signature(self.post)}}
        self.save()

    def save(self, current=None):
        write_json(self.folder / 'posts-with-images.json', {'posts': [self.post]})
        write_json(self.root / 'dist/data/social-posts.json', {'posts': [current or self.post]})
        write_json(self.root / 'data/ai-post-cache.json', {'version': VERSION, 'posts': {'post-1': self.entry}})
        write_json(self.root / 'data/ai-events.json', self.stored)

    def replay(self, **kwargs):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Network access forbidden')):
            return recover_history(self.root, now=NOW, **kwargs)

    def test_dry_replay_and_write_restore_history_without_changing_model_cache(self):
        before = (self.root / 'data/ai-post-cache.json').read_bytes()
        recovered, report = self.replay()
        self.assertEqual(len(recovered['events']), 1)
        self.assertEqual(recovered['events'][0]['starts_at'], EVENT['starts_at'])
        self.assertEqual(recovered['events'][0]['verified_at'], '2026-10-17T12:00:00+00:00')
        self.assertEqual(report['openai_requests'], 0)
        self.assertEqual(json.loads((self.root / 'data/ai-events.json').read_text())['events'], [])
        self.replay(write=True)
        self.assertEqual((self.root / 'data/ai-post-cache.json').read_bytes(), before)
        self.assertEqual(len(json.loads((self.root / 'data/ai-events.json').read_text())['events']), 1)

    def test_caption_edit_invalidates_signature_even_with_an_old_complete_archive(self):
        self.save(current={**self.post, 'text': 'Event cancelled'})
        recovered, report = self.replay()
        self.assertEqual(recovered['events'], [])
        self.assertIn('changed', report['skipped'][0]['reason'])

    def test_changed_image_bytes_fail_fingerprint_check(self):
        Image.new('RGB', (10, 10), 'blue').save(self.image)
        recovered, report = self.replay()
        self.assertEqual(recovered['events'], [])
        self.assertIn('fingerprint', report['skipped'][0]['reason'])

    def test_partial_video_missing_image_and_api_error_cannot_recover(self):
        for change in ('partial', 'missing', 'error'):
            with self.subTest(change=change):
                self.post['image_ocr']['coverage_complete'] = change != 'partial'
                self.entry.pop('error', None)
                if change == 'error':
                    self.entry['error'] = 'Outcome unknown'
                Image.new('RGB', (10, 10), 'red').save(self.image)
                self.save()
                if change == 'missing':
                    self.image.unlink()
                recovered, report = self.replay()
                self.assertFalse(recovered['events'])
                self.assertEqual(len(report['skipped']), 1)

    def test_uncertain_or_nonfree_past_entries_stay_in_review(self):
        for change in ({'food_is_free': False}, {'needs_review': True},
                       {'evidence': {**EVENT['evidence'], 'time': ''}}):
            with self.subTest(change=change):
                self.entry['extraction']['events'] = [{**EVENT, **change}]
                self.save()
                recovered, report = self.replay()
                self.assertFalse(recovered['events'])
                self.assertEqual(len(recovered['queue']), 1)
                self.assertFalse(report['skipped'])


class HistoryFeedTests(unittest.TestCase):
    def test_absent_past_calendar_event_remains_but_current_cancellation_removes_it(self):
        source = {'id': 'calendar', 'type': 'ics', 'url': 'https://example.org/calendar', 'society': 'Test'}
        old = {**EVENT, 'id': 'calendar-old', 'source_id': 'calendar', 'society': 'Test',
               'source_url': 'https://example.org/event', 'verified_at': '2026-10-09T12:00:00Z'}
        with patch('scripts.refresh.fetch', return_value='BEGIN:VCALENDAR\nEND:VCALENDAR'):
            retained, _ = refresh_events({'feeds': [source]}, {'events': [old]}, {}, NOW, include_past=True)
        self.assertEqual(retained['events'], [old])
        for description in ('STATUS:CANCELLED\nSUMMARY:Free pizza', 'SUMMARY:Paid dinner'):
            calendar = ('BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261008T183000Z\n'
                        'URL:https://example.org/event\nLOCATION:Durham SU\n' + description +
                        '\nEND:VEVENT\nEND:VCALENDAR')
            with self.subTest(description=description), patch('scripts.refresh.fetch', return_value=calendar):
                removed, _ = refresh_events({'feeds': [source]}, {'events': [old]}, {}, NOW, include_past=True)
            self.assertFalse(removed['events'])

    def test_current_historical_source_edit_wins_over_archived_details(self):
        source = {'id': 'calendar', 'type': 'ics', 'url': 'https://example.org/calendar', 'society': 'Test'}
        old = {**EVENT, 'id': 'calendar-old', 'source_id': 'calendar', 'society': 'Test',
               'source_url': 'https://example.org/event', 'verified_at': '2026-10-09T12:00:00Z'}
        calendar = ('BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261008T183000Z\n'
                    'URL:https://example.org/event\nLOCATION:New venue\nSUMMARY:Free pizza\n'
                    'END:VEVENT\nEND:VCALENDAR')
        with patch('scripts.refresh.fetch', return_value=calendar):
            updated, _ = refresh_events({'feeds': [source]}, {'events': [old]}, {}, NOW, include_past=True)
        self.assertEqual(len(updated['events']), 1)
        self.assertEqual(updated['events'][0]['location'], 'New venue')

    def test_history_counts_follow_london_calendar_days_and_known_end_times(self):
        # UTC 23:10 is the next calendar day in London during BST.
        now = datetime(2026, 10, 17, 23, 10, tzinfo=timezone.utc)
        entries = [
            {'starts_at': '2026-10-03T23:00:00Z'},  # 4 Oct: inclusive 14-day boundary
            {'starts_at': '2026-10-03T22:59:00Z'},  # 3 Oct: older
            {'starts_at': '2026-10-17T23:00:00Z', 'ends_at': '2026-10-17T23:05:00Z'},
            {'starts_at': '2026-10-17T23:00:00Z'},
            {'starts_at': '2026-10-18T12:00:00Z'},
        ]
        feed = {'events': entries}
        annotate_event_history(feed, now)
        self.assertEqual([e['temporal_status'] for e in entries], ['past', 'past', 'ended', 'started', 'upcoming'])
        self.assertEqual(feed['status']['past_event_count'], 2)
        self.assertEqual(feed['status']['past_14_day_event_count'], 1)
        self.assertEqual(feed['status']['live_event_count'], 2)
        self.assertEqual(feed['status']['total_event_count'], 5)

    def test_history_refresh_keeps_reviewed_events_and_still_rejects_nonfree(self):
        reviewed = {**EVENT, 'id': 'manual-old', 'reviewed': True,
                    'society': 'Bridge Club', 'source_url': 'https://www.instagram.com/p/EXAMPLE/',
                    'verified_at': '2026-10-17T12:00:00Z'}
        events, _ = refresh_events({'feeds': []}, {}, {'events': [reviewed]}, NOW, offline=True, include_past=True)
        self.assertEqual(len(events['events']), 1)
        rejected, _ = refresh_events({'feeds': []}, {}, {'events': [{**reviewed, 'food_is_free': False}]}, NOW,
                                    offline=True, include_past=True)
        self.assertFalse(rejected['events'])


if __name__ == '__main__':
    unittest.main()
