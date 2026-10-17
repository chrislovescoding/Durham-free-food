from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.apify_posts import ApifyError, Client, cached_items, collect, normalize_items, validate_config
from scripts.import_apify import merge_batch

ACCOUNTS = {'https://www.instagram.com/example/': 'Example society'}
CONFIG = {'usernames': ['example'], 'posts_per_account': 5, 'max_charge_usd': 0.25}
NOW = '2026-10-17T12:00:00Z'
RUN = {'id': 'Run123', 'status': 'SUCCEEDED', 'options': {'maxTotalChargeUsd': 0.25},
       'defaultDatasetId': 'Dataset123', 'usageTotalUsd': 0.0085, 'defaultKeyValueStoreId': 'Store123'}
ITEM = {'url': 'https://www.instagram.com/p/POST1/', 'ownerUsername': 'example', 'caption': '',
        'timestamp': NOW, 'type': 'Sidecar', 'displayUrl': 'https://scontent.cdninstagram.com/cover.jpg',
        'images': ['https://scontent.cdninstagram.com/1.jpg', 'https://scontent.cdninstagram.com/2.jpg']}


class PilotTests(unittest.TestCase):
    def test_cap_and_post_budget_fail_closed(self):
        for change in [{'max_charge_usd': 0.26}, {'max_charge_usd': float('nan')}, {'posts_per_account': 51},
                       {'usernames': ['example', 'example']}, {'usernames': ['https://instagram.com/p/POST1/']}, {'usernames': ['unknown']}]:
            with self.subTest(change=change), self.assertRaises(ApifyError):
                validate_config({**CONFIG, **change}, ACCOUNTS)

    def test_explicit_basic_settings_and_server_cap(self):
        session = Mock()
        session.request.return_value.status_code = 201
        session.request.return_value.json.return_value = {'data': RUN}
        body, _ = validate_config(CONFIG, ACCOUNTS)
        Client('private-test-token', session).start(body, 0.25)
        call = session.request.call_args
        self.assertEqual(call.kwargs['params']['maxTotalChargeUsd'], 0.25)
        self.assertEqual(call.kwargs['params']['restartOnError'], 'false')
        self.assertEqual(call.kwargs['json']['dataDetailLevel'], 'basicData')
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertNotIn('private-test-token', call.args[1])

    def test_image_only_carousel_kept_conflicting_owners_rejected(self):
        posts, rejected = normalize_items([ITEM, {**ITEM, 'ownerUsername': 'outsider', 'inputUrl': next(iter(ACCOUNTS))}], ACCOUNTS, NOW, 'Run123')
        self.assertEqual(len(posts), 1)
        self.assertEqual(rejected, 1)
        self.assertEqual(posts[0]['media_urls'], ITEM['images'])
        self.assertEqual(posts[0]['text'], '')

    def test_offset_cutoff_is_normalized_to_utc(self):
        body, _ = validate_config({**CONFIG, 'only_posts_newer_than': '2026-10-17T13:00:00+01:00'}, ACCOUNTS)
        self.assertEqual(body['onlyPostsNewerThan'], NOW)

    def test_missing_carousel_panels_preserve_cover_with_partial_marker(self):
        posts, _ = normalize_items([{**ITEM, 'images': [], 'childPosts': [{'displayUrl': None}]}], ACCOUNTS, NOW, 'Run123')
        self.assertEqual(posts[0]['media_urls'], [ITEM['displayUrl']])
        self.assertEqual(posts[0]['media_coverage'], 'cover_only')

    def test_uncertain_start_is_not_retried(self):
        client = Mock()
        client.start.side_effect = ApifyError('Uncertain start')
        with tempfile.TemporaryDirectory() as temp:
            for _ in range(2):
                with self.assertRaises(ApifyError):
                    collect(client, CONFIG, ACCOUNTS, temp, sleep=lambda _: None)
            self.assertEqual(client.start.call_count, 1)
            self.assertEqual(json.loads((Path(temp) / 'run.json').read_text())['phase'], 'start_unconfirmed')

    def test_cached_batch_does_not_rebuy_or_reread_dataset(self):
        client = Mock()
        client.start.return_value = deepcopy(RUN)
        client.run.return_value = deepcopy(RUN)
        client.items.return_value = [deepcopy(ITEM)]
        with tempfile.TemporaryDirectory() as temp:
            first, report = collect(client, CONFIG, ACCOUNTS, temp, sleep=lambda _: None)
            second, _ = collect(client, CONFIG, ACCOUNTS, temp, sleep=lambda _: None)
            self.assertEqual(first, second)
            self.assertEqual(report['accepted_posts'], 1)
            client.start.assert_called_once()
            client.items.assert_called_once()

    def test_workflow_rerun_does_not_start_paid_work(self):
        client = Mock()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}), self.assertRaises(ApifyError):
            collect(client, CONFIG, ACCOUNTS, temp, sleep=lambda _: None)
        client.start.assert_not_called()

    def test_existing_lock_blocks_concurrent_paid_start(self):
        client = Mock()
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'collection.lock').write_text('active')
            with self.assertRaises(ApifyError):
                collect(client, CONFIG, ACCOUNTS, temp, sleep=lambda _: None)
        client.start.assert_not_called()

    def test_missing_provider_cap_aborts_run(self):
        client = Mock()
        client.start.return_value = {**RUN, 'status': 'RUNNING', 'options': {}}
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ApifyError):
            collect(client, CONFIG, ACCOUNTS, temp, sleep=lambda _: None)
        client.request.assert_called_once_with('POST', '/actor-runs/Run123/abort')
        client.items.assert_not_called()

    def test_source_cache_excludes_comments(self):
        cached = cached_items([{**ITEM, 'latestComments': [{'text': 'private detail'}]}])
        self.assertNotIn('latestComments', cached[0])

    def test_import_deduplicates_and_does_not_publish_local_paths(self):
        post = normalize_items([ITEM], ACCOUNTS, NOW, 'Run123')[0][0]
        post['image_ocr'] = {'status': 'complete', 'text': 'Free pizza', 'images': [{'local_path': '/runner/cache/image.jpg'}]}
        batch = {'updated_at': NOW, 'posts': [post], 'accounts': []}
        result = merge_batch({'posts': [post], 'accounts': [], 'status': {}}, batch, {'run': RUN}, {})
        self.assertEqual(len(result['posts']), 1)
        self.assertNotIn('local_path', result['posts'][0]['image_ocr']['images'][0])


if __name__ == '__main__':
    unittest.main()
