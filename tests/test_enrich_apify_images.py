from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.apify_posts import ApifyError, safe_run, write_json
from scripts.enrich_apify_images import choose_posts, enrich, merge_images, remaining_cap

ACCOUNT = 'https://www.instagram.com/example/'
ACCOUNTS = {ACCOUNT: 'Example', 'https://www.instagram.com/cohost/': 'Cohost'}
SOURCE = 'https://www.instagram.com/p/POST1/'
INPUT = {'username': ['example'], 'resultsLimit': 5, 'dataDetailLevel': 'basicData', 'includeMusicData': False}
PRIMARY = {'id': 'Original123', 'actId': 'Actor123', 'status': 'SUCCEEDED', 'options': {'maxTotalChargeUsd': .25},
           'defaultDatasetId': 'OriginalDataset', 'defaultKeyValueStoreId': 'OriginalStore', 'usageTotalUsd': .0833}
SUPPLEMENT = {'id': 'Supplement123', 'actId': 'Actor123', 'status': 'SUCCEEDED', 'options': {'maxTotalChargeUsd': .16},
              'defaultDatasetId': 'SupplementDataset', 'defaultKeyValueStoreId': 'SupplementStore', 'usageTotalUsd': .054}
ITEM = {'url': SOURCE, 'ownerUsername': 'cohost', 'caption': 'Original caption', 'timestamp': '2026-10-17T12:00:00Z',
        'type': 'Sidecar', 'displayUrl': 'https://scontent.cdninstagram.com/cover.jpg', 'images': [], 'childPosts': []}
DETAIL = {**ITEM, 'caption': 'Do not replace caption', 'ownerUsername': 'outsider', 'timestamp': '2030-01-01T00:00:00Z',
          'images': ['https://scontent.cdninstagram.com/one.jpg', 'https://scontent.cdninstagram.com/two.jpg']}


def fixture(directory):
    write_json(Path(directory) / 'run.json', {'run': safe_run(PRIMARY), 'input': INPUT, 'max_charge_usd': .25})
    write_json(Path(directory) / 'dataset-source-fields.json', [ITEM])
    client = Mock()
    client.run.side_effect = lambda identity: deepcopy(PRIMARY if identity == PRIMARY['id'] else SUPPLEMENT)
    client.start.return_value = deepcopy(SUPPLEMENT)
    client.request.return_value = deepcopy(INPUT)
    client.items.return_value = [deepcopy(DETAIL)]
    return client


class ImageEnrichmentTests(unittest.TestCase):
    def test_remaining_budget_reserves_overhead_and_rounds_down(self):
        self.assertEqual(remaining_cap(.0833, .25), Decimal('.16'))
        self.assertEqual(remaining_cap(.1999, .25), Decimal('.04'))
        for cost, cap in [(.25, .25), (.0833, .26), (float('nan'), .25), (-1, .25), (True, .25)]:
            with self.subTest(cost=cost, cap=cap), self.assertRaises(ApifyError):
                remaining_cap(cost, cap)

    def test_only_existing_registered_sidecars_are_selected(self):
        items = [ITEM, {**ITEM, 'type': 'Image', 'url': 'https://www.instagram.com/p/IMAGE/'},
                 {**ITEM, 'ownerUsername': 'unregistered', 'url': 'https://www.instagram.com/p/OTHER/'},
                 {**ITEM, 'url': 'https://malicious.example/p/POST1/', 'shortCode': None}, ITEM]
        self.assertEqual(choose_posts(items, ACCOUNTS), [SOURCE])

    def test_media_merge_preserves_original_evidence_and_excludes_new_posts(self):
        records, recovered = merge_images([ITEM], [DETAIL, {**DETAIL, 'url': 'https://www.instagram.com/p/NEW/'}], {SOURCE})
        self.assertEqual(len(records), 1)
        self.assertEqual(recovered, 1)
        for key in ['caption', 'ownerUsername', 'timestamp', 'url']:
            self.assertEqual(records[0][key], ITEM[key])
        self.assertEqual(records[0]['images'], DETAIL['images'])

    def test_paid_start_cap_source_urls_and_cached_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fixture(directory)
            first, report = enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            second, _ = enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            self.assertEqual(first, second)
            self.assertEqual(first['posts'][0]['society'], 'Cohost')
            self.assertEqual(first['posts'][0]['media_urls'], DETAIL['images'])
            self.assertEqual(report['run']['id'], PRIMARY['id'])
            self.assertEqual(report['combined_charge_usd'], .1373)
            client.start.assert_called_once_with({'username': [SOURCE], 'dataDetailLevel': 'detailedData', 'includeMusicData': False}, .16)
            client.items.assert_called_once()
            self.assertEqual(client.run.call_count, 2)

    def test_ambiguous_start_is_never_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fixture(directory)
            client.start.side_effect = ApifyError('unconfirmed start')
            for _ in range(2):
                with self.assertRaises(ApifyError):
                    enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            client.start.assert_called_once()
            journal = json.loads((Path(directory) / 'image-enrichment-run.json').read_text())
            self.assertEqual(journal['phase'], 'start_unconfirmed')

    def test_workflow_rerun_cannot_start(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}):
            client = fixture(directory)
            with self.assertRaises(ApifyError):
                enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            client.start.assert_not_called()

    def test_wrong_original_input_blocks_before_payment(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fixture(directory)
            client.request.return_value = {**INPUT, 'username': ['someoneelse']}
            with self.assertRaises(ApifyError):
                enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            client.start.assert_not_called()

    def test_missing_supplement_cap_aborts(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fixture(directory)
            client.start.return_value = {**SUPPLEMENT, 'status': 'RUNNING', 'options': {}}
            with self.assertRaises(ApifyError):
                enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            self.assertEqual(client.request.call_args.args, ('POST', '/actor-runs/Supplement123/abort'))
            client.items.assert_not_called()

    def test_existing_lock_blocks_start(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fixture(directory)
            (Path(directory) / 'image-enrichment.lock').write_text('active')
            with self.assertRaises(ApifyError):
                enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            client.start.assert_not_called()

    def test_old_artifact_resumes_committed_supplement_without_rebuying(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fixture(directory)
            body = {'username': [SOURCE], 'dataDetailLevel': 'detailedData', 'includeMusicData': False}
            saved = {'created_at': '2026-10-17T12:00:00Z', 'original_run_id': PRIMARY['id'],
                     'input': body, 'max_charge_usd': .16, 'run': safe_run(SUPPLEMENT), 'phase': 'dataset_saved'}
            write_json(Path(directory) / 'data/apify-pilot-report.json',
                       {'collection': {'run': safe_run(PRIMARY), 'image_enrichment': saved}})
            client.request.side_effect = lambda method, path: body if 'SupplementStore' in path else INPUT
            with patch('scripts.enrich_apify_images.ROOT', Path(directory)):
                posts, report = enrich(client, directory, ACCOUNTS, sleep=lambda _: None)
            client.start.assert_not_called()
            self.assertEqual(len(posts['posts']), 1)
            self.assertEqual(report['image_enrichment']['run']['id'], SUPPLEMENT['id'])


if __name__ == '__main__':
    unittest.main()
