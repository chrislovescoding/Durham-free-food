from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from scripts.refresh import integrate_social, refresh_social_sources, refresh_societies

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
POST = {'id': 'p1', 'platform': 'instagram', 'society': 'Example', 'account_url': 'https://www.instagram.com/example/',
        'source_url': 'https://www.instagram.com/p/EXAMPLE/', 'text': 'Free pizza social! 18 October 2026 at 6pm\nLocation: Durham SU',
        'published_at': '2026-10-17T10:00:00Z', 'observed_at': '2026-10-17T12:00:00Z', 'origin': 'public_html'}


class IntegrationTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
