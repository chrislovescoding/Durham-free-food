from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.refresh import integrate_social, merge_post_evidence, refresh_social_sources, refresh_societies, main

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
POST = {'id': 'p1', 'platform': 'instagram', 'society': 'Example', 'account_url': 'https://www.instagram.com/example/',
        'source_url': 'https://www.instagram.com/p/EXAMPLE/', 'text': 'Free pizza social! 18 October 2026 at 6pm\nLocation: Durham SU',
        'published_at': '2026-10-17T10:00:00Z', 'observed_at': '2026-10-17T12:00:00Z', 'origin': 'public_html'}


class IntegrationTests(unittest.TestCase):
    def test_apify_image_posts_survive_daily_refresh_without_paid_requests(self):
        apify = {**POST, 'origin': 'apify', 'text': '', 'media_urls': ['https://example.org/poster.jpg'],
                 'image_ocr': {'status': 'ok', 'coverage_complete': True, 'text': 'Free pizza'}}
        account = {'provider': 'apify', 'url': POST['account_url'], 'platform': 'instagram', 'status': 'ok',
                   'last_attempt_at': '2026-10-16T12:00:00Z'}
        previous = {'posts': [apify], 'accounts': [account], 'apify_runs': [{'id': 'receipt'}],
                    'status': {'apify': {'status': 'ok', 'posts_collected': 1}}}
        societies = [{'name': 'Example', 'url': 'https://example.org/', 'instagram_url': POST['account_url']}]
        empty = {'posts': [], 'accounts': [], 'status': {}}
        with patch('scripts.social_posts.collect_social_posts', return_value=empty) as public, patch('scripts.meta_posts.collect_meta_posts', return_value=empty) as meta:
            result = refresh_social_sources({}, societies, previous, NOW)
        self.assertEqual(result['posts'], [{**apify, 'image_review_required': True}])
        self.assertEqual(result['accounts'], [account])
        self.assertEqual(result['apify_runs'], [{'id': 'receipt'}])
        self.assertEqual(result['status']['apify'], previous['status']['apify'])
        self.assertFalse(public.call_args.args[2]['posts'])
        self.assertFalse(meta.call_args.args[2]['posts'])

    def test_removed_accounts_and_expired_apify_posts_are_not_retained(self):
        previous = {'posts': [{**POST, 'origin': 'apify', 'published_at': '2026-01-01T12:00:00Z'},
                              {**POST, 'origin': 'apify', 'account_url': 'https://www.instagram.com/removed/'}],
                    'accounts': [{'provider': 'apify', 'url': 'https://www.instagram.com/removed/'}]}
        societies = [{'name': 'Example', 'url': 'https://example.org/', 'instagram_url': POST['account_url']}]
        empty = {'posts': [], 'accounts': [], 'status': {}}
        with patch('scripts.social_posts.collect_social_posts', return_value=empty), patch('scripts.meta_posts.collect_meta_posts', return_value=empty):
            result = refresh_social_sources({}, societies, previous, NOW)
        self.assertFalse(result['posts'])
        self.assertFalse(result['accounts'])

    def test_ocr_survives_identical_post_and_is_invalidated_by_caption_or_media_edits(self):
        old = {**POST, 'origin': 'apify', 'media_urls': ['https://example.org/old.jpg'],
               'image_ocr': {'status': 'ok', 'coverage_complete': True, 'text': 'Free pizza'}}
        incoming = {**old, 'origin': 'public_html', 'observed_at': '2026-10-17T13:00:00Z'}
        incoming.pop('image_ocr')
        result = merge_post_evidence(old, incoming)
        self.assertEqual(result['image_ocr'], old['image_ocr'])
        self.assertEqual(result['origin'], 'public_html')
        result['image_ocr']['text'] = 'Changed copy'
        self.assertEqual(old['image_ocr']['text'], 'Free pizza')
        for change in ({'text': 'Updated caption'}, {'media_urls': ['https://example.org/new.jpg']}):
            with self.subTest(change=change):
                result = merge_post_evidence(old, {**incoming, **change})
                self.assertEqual(result['image_ocr']['status'], 'invalidated')
                self.assertEqual(result['image_ocr']['text'], '')
                self.assertTrue(result['image_review_required'])

    def test_offline_rebuild_uses_saved_posts_and_new_reviewed_events_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reviewed = {**POST, 'id': 'reviewed', 'reviewed': True, 'title': 'Pizza social',
                        'starts_at': '2026-10-18T17:00:00Z', 'location': 'Durham SU', 'food': 'Free pizza',
                        'food_is_free': True, 'verified_at': '2026-10-17T11:00:00Z'}
            files = {
                'data/sources.json': {'feeds': [{'id': 'calendar', 'type': 'ics', 'url': 'https://example.org/feed', 'society': 'Example'}]},
                'data/reviewed-events.json': {'events': [reviewed]},
                'data/review-queue.json': {'candidates': [{'source_id': 'calendar', 'title': 'Saved candidate'}]},
                'dist/data/events.json': {'events': [], 'sources': [{'id': 'calendar', 'status': 'ok', 'last_attempt_at': '2026-10-16T12:00:00Z'}]},
                'dist/data/societies.json': {'societies': []},
                'dist/data/social-posts.json': {'posts': [{**POST, 'origin': 'apify'}], 'accounts': []},
            }
            for relative, value in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding='utf-8')
            with patch('sys.argv', ['refresh.py', '--root', str(root), '--offline', '--now', NOW.isoformat()]), \
                    patch('scripts.refresh.fetch', side_effect=AssertionError('Offline must not fetch')), \
                    patch('scripts.refresh.refresh_societies', side_effect=AssertionError('Offline must not discover societies')), \
                    patch('scripts.refresh.refresh_social_sources', side_effect=AssertionError('Offline must not collect posts')), \
                    patch('builtins.print'):
                self.assertEqual(main(), 0)
            events = json.loads((root / 'dist/data/events.json').read_text())
            queue = json.loads((root / 'data/review-queue.json').read_text())
            self.assertEqual([e['id'] for e in events['events']], ['reviewed'])
            self.assertEqual(events['sources'][0]['last_attempt_at'], '2026-10-16T12:00:00Z')
            self.assertEqual(len(queue['candidates']), 2)
            self.assertTrue(queue['candidates'][1]['needs_visual_review'])

    def test_failed_linked_website_keeps_mapping_with_stale_provenance(self):
        directory = 'https://www.durhamsu.com/groups/studentgroups/'
        society_url = 'https://www.durhamsu.com/groups/studentgroup/1/'
        prior = {'societies': [{'name': 'Example', 'url': society_url, 'socials_checked_at': '2026-10-16T12:00:00Z',
            'social_accounts': [{'platform': 'instagram', 'url': POST['account_url'], 'evidence_url': 'https://example.org/'}]}]}
        discovery = {'social_accounts': [], 'discovery_status': 'error',
                     'discovery_errors': [{'url': 'https://example.org/', 'error': 'Timed out'}]}
        with patch('scripts.refresh.fetch', return_value='<a href="/groups/studentgroup/1/">Example</a>'), patch('scripts.society_discovery.discover_accounts', return_value=deepcopy(discovery)):
            result, status = refresh_societies({'society_directory': directory}, prior, NOW)
        account = result['societies'][0]['social_accounts'][0]
        self.assertTrue(account['discovery_stale'])
        self.assertEqual(account['last_confirmed_at'], '2026-10-16T12:00:00Z')
        self.assertEqual(status['status'], 'partial')

    def test_successful_removal_does_not_preserve_old_account_mapping(self):
        prior = {'societies': [{'name': 'Example', 'url': 'https://www.durhamsu.com/groups/studentgroup/1/',
            'instagram_url': POST['account_url'], 'social_accounts': [{'platform': 'instagram', 'url': POST['account_url'], 'evidence_url': 'https://example.org/'}]}]}
        discovery = {'social_accounts': [], 'discovery_status': 'not_listed', 'discovery_errors': []}
        with patch('scripts.refresh.fetch', return_value='<a href="/groups/studentgroup/1/">Example</a>'), patch('scripts.society_discovery.discover_accounts', return_value=discovery):
            result, status = refresh_societies({'society_directory': 'https://www.durhamsu.com/groups/studentgroups/'}, prior, NOW)
        self.assertFalse(result['societies'][0]['social_accounts'])
        self.assertNotIn('instagram_url', result['societies'][0])

    def test_social_merge_deduplicates_post_and_keeps_newest_observation(self):
        previous = {'posts': [POST, {**POST, 'origin': 'meta_api'}], 'accounts': []}
        public = {'posts': [POST], 'accounts': [], 'status': {'status': 'ok'}}
        official = {'posts': [{**POST, 'origin': 'meta_api', 'text': POST['text'] + '\nLatest details.'}], 'accounts': [], 'status': {'status': 'ok'}}
        societies = [{'name': 'Example', 'url': 'https://example.org/', 'social_accounts': [
            {'platform': 'instagram', 'url': 'https://instagram.com/example/'},
            {'platform': 'instagram', 'url': 'https://instagram.com/second/'}]}]
        with patch('scripts.social_posts.collect_social_posts', return_value=public) as public_call, patch('scripts.meta_posts.collect_meta_posts', return_value=official) as meta_call:
            result = refresh_social_sources({}, societies, previous, NOW)
        self.assertEqual(len(result['posts']), 1)
        self.assertEqual(result['posts'][0]['origin'], 'meta_api')
        self.assertEqual(len(public_call.call_args.args[0]), 2)
        self.assertEqual(len(meta_call.call_args.args[0]), 2)
        self.assertEqual(len(public_call.call_args.args[2]['posts']), 1)

    def test_manual_event_precedes_same_social_event_across_timezone_formats(self):
        manual = {'id': 'reviewed', 'source_id': 'manual', 'source_url': POST['source_url'], 'starts_at': '2026-10-18T17:00:00Z'}
        events = {'events': [manual], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z', 'posts': [POST], 'accounts': [], 'status': {}}
        queue = {'candidates': []}
        integrate_social(events, queue, social, NOW)
        self.assertEqual([e['id'] for e in events['events']], ['reviewed'])

    def test_reintegration_does_not_duplicate_review_candidates_or_fake_success(self):
        events = {'events': [], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z', 'posts': [{**POST, 'text': 'Pizza social!'}], 'accounts': [], 'status': {},
                  'coverage': {'summary': {'societies_needing_accounts': 242}}}
        queue = {'candidates': [{'title': 'Calendar candidate', 'source_id': 'calendar'}]}
        integrate_social(events, queue, social, NOW)
        integrate_social(events, queue, social, NOW)
        self.assertEqual(len(queue['candidates']), 2)
        self.assertEqual(events['status']['candidate_count'], 2)
        self.assertNotEqual(events['status']['state'], 'ok')
        self.assertEqual(events['status']['source_count'], 2)
        self.assertEqual(events['status']['society_coverage']['societies_needing_accounts'], 242)

    def test_a_retained_post_does_not_make_a_deferred_account_freshly_checked(self):
        events = {'events': [], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z', 'posts': [{**POST, 'observed_at': '2026-10-16T12:00:00Z'}], 'accounts': [
            {'platform': 'instagram', 'provider': 'public_html', 'status': 'deferred', 'posts_collected': 0,
             'last_attempt_at': '2026-10-16T12:00:00Z'}], 'status': {}}
        integrate_social(events, {'candidates': []}, social, NOW)
        source = next(s for s in events['sources'] if s['id'] == 'social-instagram')
        self.assertEqual(source['accounts_checked'], 0)
        self.assertEqual(source['status'], 'partial')
        self.assertEqual(source['parsed_count'], 0)
        self.assertEqual(source['posts_stored'], 1)

    def test_partially_fetched_accounts_do_not_become_healthy_sources(self):
        events = {'events': [], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z', 'posts': [POST], 'accounts': [
            {'platform': 'instagram', 'provider': 'public_html', 'status': 'partial', 'posts_collected': 1,
             'last_attempt_at': '2026-10-17T12:00:00Z'}], 'status': {}}
        integrate_social(events, {'candidates': []}, social, NOW)
        self.assertEqual(events['sources'][0]['status'], 'partial')
        self.assertNotEqual(events['status']['state'], 'ok')

    def test_apify_provider_success_counts_once_despite_old_public_denial(self):
        events = {'events': [], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z', 'posts': [{**POST, 'origin': 'apify'}],
                  'accounts': [
                      {'platform': 'instagram', 'url': POST['account_url'], 'provider': 'public_html',
                       'status': 'login_required', 'last_attempt_at': '2026-10-16T12:00:00Z'},
                      {'platform': 'instagram', 'url': 'https://instagram.com/EXAMPLE', 'provider': 'apify',
                       'status': 'ok', 'posts_collected': 1, 'last_attempt_at': '2026-10-17T13:00:00+01:00'},
                      {'platform': 'instagram', 'url': POST['account_url'], 'provider': 'meta_api',
                       'status': 'disabled', 'last_attempt_at': None}], 'status': {'account_count': 3}}
        integrate_social(events, {'candidates': []}, social, NOW)
        source = events['sources'][0]
        self.assertEqual(source['status'], 'ok')
        self.assertEqual(source['account_count'], 1)
        self.assertEqual(source['accounts_checked'], 1)
        self.assertEqual(source['accounts_with_posts'], 1)
        self.assertEqual(source['parsed_count'], 1)
        self.assertEqual(events['status']['social_account_count'], 1)
        self.assertEqual(social['accounts'][0]['status'], 'login_required')

    def test_partial_apify_batch_keeps_unchecked_registry_accounts_in_denominator(self):
        events = {'events': [], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z', 'posts': [{**POST, 'origin': 'apify'}],
                  'accounts': [{'platform': 'instagram', 'url': POST['account_url'], 'provider': 'apify',
                                'status': 'ok', 'posts_collected': 1, 'last_attempt_at': '2026-10-17T12:00:00Z'}],
                  'coverage': {'summary': {'societies_needing_attention': 1}, 'societies': [{'accounts': [
                      {'platform': 'instagram', 'url': POST['account_url']},
                      {'platform': 'instagram', 'url': 'https://www.instagram.com/unchecked/'}]}]}, 'status': {}}
        integrate_social(events, {'candidates': []}, social, NOW)
        source = events['sources'][0]
        self.assertEqual(source['status'], 'partial')
        self.assertEqual(source['account_count'], 2)
        self.assertEqual(source['accounts_checked'], 1)
        self.assertEqual(source['parsed_count'], 1)
        self.assertEqual(events['status']['social_account_count'], 2)

    def test_old_apify_success_does_not_make_current_denial_healthy(self):
        events = {'events': [], 'sources': [], 'status': {}}
        social = {'updated_at': '2026-10-17T12:00:00Z',
                  'posts': [{**POST, 'origin': 'apify', 'observed_at': '2026-10-16T12:00:00Z'}],
                  'accounts': [
                      {'platform': 'instagram', 'url': POST['account_url'], 'provider': 'public_html',
                       'status': 'login_required', 'last_attempt_at': '2026-10-17T12:00:00Z'},
                      {'platform': 'instagram', 'url': POST['account_url'], 'provider': 'apify',
                       'status': 'ok', 'posts_collected': 1, 'last_attempt_at': '2026-10-16T12:00:00Z'}], 'status': {}}
        integrate_social(events, {'candidates': []}, social, NOW)
        source = events['sources'][0]
        self.assertEqual(source['status'], 'partial')
        self.assertEqual(source['account_count'], 1)
        self.assertEqual(source['accounts_checked'], 1)
        self.assertEqual(source['parsed_count'], 0)
        self.assertEqual(source['posts_stored'], 1)


if __name__ == '__main__':
    unittest.main()
