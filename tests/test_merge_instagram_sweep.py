from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.ai_events import evidence_signature
from scripts.merge_instagram_sweep import merge_sweep, _superseded_success


EARLY = '2026-10-17T12:00:00Z'
LATE = '2026-10-17T14:00:00Z'


class MergeInstagramSweepTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / 'work/instagram-sweep'
        self.plan = {'max_charge_usd': 0.28, 'sum_batch_caps_usd': 0.28,
                     'planned_accounts': 2, 'requested_posts': 10,
                     'batches': [{'id': identity, 'max_charge_usd': 0.14,
                                  'config_path': f'batches/{identity}/config.json'} for identity in ('01', '02')]}
        self.write(self.folder / 'plan.json', self.plan)
        self.manual = {'events': [{'id': 'human-reviewed', 'reviewed': True}]}
        self.write(self.root / 'data/reviewed-events.json', self.manual)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def read(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def post(self, identity, observed=LATE, text='Free pizza'):
        return {'id': identity, 'source_url': f'https://www.instagram.com/p/{identity}/',
                'society': 'Test Society', 'platform': 'instagram', 'text': text,
                'published_at': '2026-10-16T12:00:00Z', 'observed_at': observed,
                'media_urls': ['https://scontent.cdninstagram.com/photo.jpg'], 'origin': 'apify'}

    def batch(self, identity, posts=None, *, ai=True, charge=0.01, at=LATE, processed=None):
        posts = [self.post('post' + identity)] if posts is None else posts
        folder = self.folder / 'batches' / identity
        config = {'usernames': ['test' + identity], 'posts_per_account': 5,
                  'max_charge_usd': 0.14, 'data_detail_level': 'detailedData',
                  'only_posts_newer_than': '2026-09-17T12:00:00Z'}
        self.write(folder / 'config.json', config)
        self.write(folder / 'run.json', {'run': {'id': 'run' + identity, 'status': 'SUCCEEDED',
                                              'usageTotalUsd': charge, 'max_charge_usd': 0.14},
                                      'max_charge_usd': 0.14, 'returned_items': len(posts),
                                      'input': {'username': config['usernames'], 'resultsLimit': 5,
                                                'dataDetailLevel': 'detailedData',
                                                'onlyPostsNewerThan': config['only_posts_newer_than']}})
        self.write(folder / 'posts.json', {'posts': posts, 'accounts': [], 'updated_at': at})
        if ai:
            processed = [post['id'] for post in posts] if processed is None else processed
            self.write(folder / 'posts-with-images.json', {'posts': posts, 'accounts': [], 'updated_at': at})
            self.write(folder / 'images.json', {'images_inspected': len(posts), 'images_failed_or_skipped': 0})
            self.write(folder / 'ai-report.json', {'posts_considered': len(processed), 'processed_post_ids': processed,
                                                 'decision_requests': len(processed), 'extraction_requests': len(processed),
                                                 'events_extracted': len(processed), 'errors': [], 'finished_at': at})
            events = [{'id': 'event' + post['id'], 'post_id': post['id'], 'input_signature': evidence_signature(post)}
                      for post in posts if post['id'] in processed]
            self.write(folder / 'ai-checkpoints/ai-events.json', {'events': events, 'queue': [],
                       'processed_post_ids': processed, 'processed_signatures': {
                           post['id']: evidence_signature(post) for post in posts if post['id'] in processed}, 'updated_at': at})
            self.write(folder / 'ai-checkpoints/ai-post-cache.json', {'posts': {post['id']: {
                'fingerprint': 'fingerprint-' + post['id'], 'updated_at': at,
                'decision': {'probability': 0.9}, 'extraction': {'events': [], 'review_reason': None}}
                for post in posts if post['id'] in processed}})
        return folder

    def test_full_merge_preserves_human_reviews_and_unique_posts(self):
        self.batch('01')
        self.batch('02')
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['run_charge_usd'], 0.02)
        self.assertEqual(report['unique_posts'], 2)
        self.assertEqual(report['unique_ai_posts_merged'], 2)
        self.assertEqual(self.read(self.root / 'data/reviewed-events.json'), self.manual)
        self.assertEqual(len(self.read(self.root / 'data/ai-events.json')['events']), 2)

    def test_worker_snapshot_cannot_overwrite_unprocessed_posts(self):
        first = self.batch('01')
        self.batch('02')
        self.write(self.root / 'data/ai-post-cache.json', {'posts': {'unrelated': {'fingerprint': 'new', 'updated_at': LATE}}})
        self.write(self.root / 'data/ai-events.json', {'events': [{'post_id': 'unrelated', 'title': 'Latest result'}],
                   'queue': [], 'processed_post_ids': ['unrelated'], 'processed_signatures': {'unrelated': 'latest'}, 'updated_at': LATE})
        cache = self.read(first / 'ai-checkpoints/ai-post-cache.json')
        cache['posts']['unrelated'] = {'fingerprint': 'old', 'updated_at': EARLY}
        self.write(first / 'ai-checkpoints/ai-post-cache.json', cache)
        events = self.read(first / 'ai-checkpoints/ai-events.json')
        events['events'].append({'post_id': 'unrelated', 'title': 'Old snapshot'})
        events['processed_post_ids'].append('unrelated')
        events['processed_signatures']['unrelated'] = 'old'
        self.write(first / 'ai-checkpoints/ai-events.json', events)
        merge_sweep(self.folder, self.root)
        result = self.read(self.root / 'data/ai-events.json')
        self.assertEqual(next(event for event in result['events'] if event['post_id'] == 'unrelated')['title'], 'Latest result')
        self.assertEqual(self.read(self.root / 'data/ai-post-cache.json')['posts']['unrelated']['fingerprint'], 'new')

    def test_partial_batch_keeps_posts_without_claiming_ai_completion(self):
        self.batch('01', ai=False)
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['completed_batches'], 0)
        self.assertEqual(report['unique_posts'], 1)
        self.assertEqual(report['batches'][1]['collection_status'], 'missing')
        self.assertEqual(self.read(self.root / 'data/ai-events.json')['events'], [])

    def test_unknown_processed_id_rejects_before_writing(self):
        folder = self.batch('01')
        self.batch('02')
        report = self.read(folder / 'ai-report.json')
        report['processed_post_ids'] = ['not-in-batch']
        self.write(folder / 'ai-report.json', report)
        with self.assertRaisesRegex(ValueError, 'unknown posts'):
            merge_sweep(self.folder, self.root)
        self.assertFalse((self.root / 'dist/data/social-posts.json').exists())

    def test_over_cap_receipt_rejects_before_writing(self):
        self.batch('01', charge=0.15)
        self.batch('02')
        with self.assertRaisesRegex(ValueError, 'charge cap'):
            merge_sweep(self.folder, self.root)
        self.assertFalse((self.root / 'data/ai-events.json').exists())

    def test_unplanned_batch_rejected(self):
        self.batch('01')
        self.batch('02')
        self.batch('03')
        with self.assertRaisesRegex(ValueError, 'Unexpected batch'):
            merge_sweep(self.folder, self.root)

    def test_newer_post_evidence_blocks_stale_ai_results(self):
        post = self.post('post01', EARLY)
        self.batch('01', [post], at=EARLY)
        self.batch('02')
        newer = self.post('post01', LATE, text='Event cancelled')
        self.write(self.root / 'dist/data/social-posts.json', {'posts': [newer], 'accounts': [], 'status': {}, 'updated_at': LATE})
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['batches'][0]['stale_or_missing_ai_post_ids'], ['post01'])
        self.assertEqual([event['post_id'] for event in self.read(self.root / 'data/ai-events.json')['events']], ['post02'])
        posts = self.read(self.root / 'dist/data/social-posts.json')['posts']
        self.assertEqual(next(post for post in posts if post['id'] == 'post01')['text'], 'Event cancelled')

    def test_newer_processed_result_is_not_replaced_by_older_checkpoint(self):
        self.batch('01', at=EARLY)
        self.batch('02')
        self.write(self.root / 'data/ai-post-cache.json', {'posts': {'post01': {'updated_at': LATE, 'fingerprint': 'newer'}}})
        self.write(self.root / 'data/ai-events.json', {'events': [], 'queue': [{'post_id': 'post01', 'reason': 'Newer review'}],
                   'processed_post_ids': ['post01'], 'processed_at_by_post': {'post01': LATE}})
        merge_sweep(self.folder, self.root)
        result = self.read(self.root / 'data/ai-events.json')
        self.assertEqual(result['queue'], [{'post_id': 'post01', 'reason': 'Newer review'}])
        self.assertEqual(self.read(self.root / 'data/ai-post-cache.json')['posts']['post01']['fingerprint'], 'newer')

    def test_same_coauthored_post_in_two_batches_is_not_duplicated(self):
        post = self.post('shared')
        self.batch('01', [post])
        self.batch('02', [post])
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['accepted_posts'], 2)
        self.assertEqual(report['unique_posts'], 1)
        self.assertEqual(len(self.read(self.root / 'data/ai-events.json')['events']), 1)
        second_report = merge_sweep(self.folder, self.root)
        self.assertEqual(second_report['unique_posts'], 1)
        self.assertEqual(len(self.read(self.root / 'data/ai-events.json')['events']), 1)

    def test_legacy_report_falls_back_only_to_considered_post_prefix(self):
        folder = self.batch('01', [self.post('one'), self.post('two')], processed=['one'])
        self.batch('02', posts=[])
        report = self.read(folder / 'ai-report.json')
        del report['processed_post_ids']
        self.write(folder / 'ai-report.json', report)
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['unique_ai_posts_merged'], 1)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['batches'][1]['status'], 'complete')

    def test_wrong_cutoff_receipt_is_rejected(self):
        folder = self.batch('01')
        self.batch('02')
        run = self.read(folder / 'run.json')
        run['input']['onlyPostsNewerThan'] = '2026-09-01T12:00:00Z'
        self.write(folder / 'run.json', run)
        with self.assertRaisesRegex(ValueError, 'date cutoff'):
            merge_sweep(self.folder, self.root)

    def saved_success(self, post, *, error=None, signature=None, probability=0.9):
        signature = signature or evidence_signature(post)
        entry = {'fingerprint': 'newer-result', 'updated_at': LATE, 'decision': {'probability': probability},
                 'extraction': {'events': [], 'review_reason': None}}
        if error:
            entry['error'] = error
        self.write(self.root / 'data/ai-post-cache.json', {'posts': {post['id']: entry}})
        result = {'events': [], 'queue': [], 'processed_post_ids': [post['id']],
                  'processed_signatures': {post['id']: signature}, 'processed_at_by_post': {post['id']: LATE},
                  'updated_at': LATE}
        self.write(self.root / 'data/ai-events.json', result)
        return entry, result

    def test_newer_same_evidence_success_counts_complete_without_overwriting(self):
        post = self.post('post01')
        self.batch('01', [post], at=EARLY)
        self.batch('02', [])
        entry, _ = self.saved_success(post)
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['batches'][0]['ai_posts_retained'], 1)
        self.assertEqual(report['batches'][0]['ai_posts_merged'], 0)
        self.assertEqual(report['unique_ai_posts_completed'], 1)
        self.assertEqual(report['batches'][0]['stale_or_missing_ai_post_ids'], [])
        self.assertEqual(self.read(self.root / 'data/ai-post-cache.json')['posts']['post01'], entry)
        self.assertEqual(self.read(self.root / 'data/ai-events.json')['events'], [])

    def test_newer_error_or_changed_signature_does_not_count_as_success(self):
        for mode in ('error', 'signature', 'missing_extraction'):
            with self.subTest(mode=mode):
                post = self.post('post01')
                self.batch('01', [post], at=EARLY)
                self.batch('02', [])
                self.saved_success(post, error='Unknown API outcome' if mode == 'error' else None,
                                   signature='different-evidence' if mode == 'signature' else None)
                if mode == 'missing_extraction':
                    cache = self.read(self.root / 'data/ai-post-cache.json')
                    del cache['posts']['post01']['extraction']
                    self.write(self.root / 'data/ai-post-cache.json', cache)
                report = merge_sweep(self.folder, self.root)
                self.assertEqual(report['status'], 'partial')
                self.assertEqual(report['batches'][0]['ai_posts_retained'], 0)
                self.assertEqual(report['batches'][0]['stale_or_missing_ai_post_ids'], ['post01'])

    def test_negative_decision_is_a_completed_result_without_extraction(self):
        post = self.post('post01')
        self.batch('01', [post], at=EARLY)
        self.batch('02', [])
        self.saved_success(post, probability=0.05)
        cache = self.read(self.root / 'data/ai-post-cache.json')
        del cache['posts']['post01']['extraction']
        self.write(self.root / 'data/ai-post-cache.json', cache)
        self.assertEqual(merge_sweep(self.folder, self.root)['status'], 'complete')

    def test_later_batch_recovery_supersedes_earlier_error_in_final_report(self):
        post = self.post('shared')
        failed = self.batch('01', [post], at=EARLY)
        self.batch('02', [post], at=LATE)
        cache = self.read(failed / 'ai-checkpoints/ai-post-cache.json')
        cache['posts']['shared']['error'] = 'HTTP 413'
        self.write(failed / 'ai-checkpoints/ai-post-cache.json', cache)
        report = self.read(failed / 'ai-report.json')
        report['errors'] = [{'post_id': 'shared', 'reason': 'HTTP 413'}]
        self.write(failed / 'ai-report.json', report)
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['superseded_errors']), 1)
        self.assertEqual(report['unique_ai_posts_completed'], 1)

    def test_newer_failed_batch_makes_shared_post_incomplete_in_both_reports(self):
        post = self.post('shared')
        self.batch('01', [post], at=EARLY)
        failed = self.batch('02', [post], at=LATE)
        cache = self.read(failed / 'ai-checkpoints/ai-post-cache.json')
        cache['posts']['shared']['error'] = 'Unknown API outcome'
        self.write(failed / 'ai-checkpoints/ai-post-cache.json', cache)
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['unique_ai_posts_completed'], 0)
        self.assertEqual([batch['status'] for batch in report['batches']], ['partial', 'partial'])

    def test_newer_different_evidence_success_supersedes_older_observation(self):
        old = self.post('post01', observed=EARLY)
        current = self.post('post01', observed=LATE, text='Updated time: free pizza at 7pm')
        self.batch('01', [old], at=EARLY)
        self.batch('02', [])
        entry, _ = self.saved_success(current)
        self.write(self.root / 'dist/data/social-posts.json', {
            'posts': [current], 'accounts': [], 'status': {}, 'updated_at': LATE})
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['batches'][0]['superseded_observation_post_ids'], ['post01'])
        self.assertEqual(report['batches'][0]['stale_or_missing_ai_post_ids'], [])
        self.assertEqual(report['unique_superseded_observation_posts'], 1)
        self.assertEqual(self.read(self.root / 'data/ai-post-cache.json')['posts']['post01'], entry)

    def test_newer_different_evidence_error_does_not_supersede_older_observation(self):
        old = self.post('post01', observed=EARLY)
        current = self.post('post01', observed=LATE, text='Changed event details')
        self.batch('01', [old], at=EARLY)
        self.batch('02', [])
        self.saved_success(current, error='API request failed')
        self.write(self.root / 'dist/data/social-posts.json', {
            'posts': [current], 'accounts': [], 'status': {}, 'updated_at': LATE})
        report = merge_sweep(self.folder, self.root)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['batches'][0]['superseded_observation_post_ids'], [])
        self.assertEqual(report['batches'][0]['stale_or_missing_ai_post_ids'], ['post01'])

    def test_superseding_observation_requires_same_identity_source_and_later_time(self):
        old = self.post('post01', observed=EARLY)
        for change in ({'id': 'another-post'}, {'source_url': 'https://www.instagram.com/p/OTHER/'},
                       {'observed_at': EARLY}, {'observed_at': None}):
            with self.subTest(change=change):
                current = {**self.post('post01', observed=LATE, text='New evidence'), **change}
                entry, stored = self.saved_success(current)
                cache = {'posts': {current['id']: entry}}
                self.assertFalse(_superseded_success(old, cache, stored, {'post01': current}))


if __name__ == '__main__':
    unittest.main()
