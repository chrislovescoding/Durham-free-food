from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

from scripts.ai_posts import APIError
from scripts.process_suggestions import (Processor, Deferred, QueueError, canonical_source,
    artifact_safe, normalize_post, ReviewRequired, DirectPostClient, CompletionRejected, ApifyHTTPError, CAP)

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
SOURCE = 'https://www.instagram.com/p/TestPost/'


def source_post():
    return {'url': SOURCE, 'caption': 'Free pizza. 23 October 2026, 18:30. Durham University.',
        'timestamp': '2026-10-16T11:00:00Z', 'ownerUsername': 'durhamsoc', 'type': 'Sidecar',
        'childPosts': [{'type': 'Image', 'displayUrl': f'https://scontent.cdninstagram.com/panel{i}.jpg?signature=secret'} for i in range(2)],
        'images': []}


def extraction():
    return {'events': [{'title': 'Pizza social', 'starts_at': '2026-10-23T18:30:00+01:00',
        'ends_at': None, 'location': 'Durham University Students Union', 'food': 'Free pizza',
        'food_is_free': True, 'price': 0, 'signup_required': True, 'signup_deadline': None,
        'eligibility': 'Members only', 'dietary': [], 'description': 'Meet other students.',
        'evidence': {'food': 'Free pizza', 'date': '23 October 2026', 'time': '18:30',
            'location': 'Durham University Students Union', 'eligibility': 'Members only'},
        'needs_review': False}], 'review_reason': None}


class FakeQueue:
    def __init__(self):
        self.saved, self.results = [], []
        self.fail_checkpoint = False

    def checkpoint(self, job, checkpoint):
        if self.fail_checkpoint:
            raise QueueError('test')
        self.saved.append(deepcopy(checkpoint))

    def complete(self, job, result):
        self.results.append(deepcopy(result))


class FakeApify:
    def __init__(self, queue):
        self.queue, self.starts = queue, []
        self.item = source_post()
        self.ambiguous = False

    def start(self, source, cutoff):
        assert self.queue.saved[-1]['apify']['phase'] == 'start_unconfirmed'
        self.starts.append((source, cutoff))
        if self.ambiguous:
            raise Deferred('test ambiguous start')
        return self.run('run123')

    def run(self, identity):
        return {'id': identity, 'status': 'SUCCEEDED', 'options': {'maxTotalChargeUsd': CAP},
                'defaultDatasetId': 'dataset123', 'usageTotalUsd': 0.003}

    def items(self, identity):
        return [deepcopy(self.item)]


class FakeAI:
    def __init__(self, queue):
        self.queue, self.calls = queue, []
        self.free_food, self.durham = 0.99, 0.99
        self.payload = extraction()
        self.ambiguous = False

    def post(self, endpoint, body):
        assert self.queue.saved[-1][('decision' if endpoint == 'decisions' else 'extraction') + '_started'] is True
        self.calls.append((endpoint, deepcopy(body)))
        if self.ambiguous:
            raise APIError('OpenAI request did not complete; no automatic retry')
        if endpoint == 'decisions':
            return {'answers': [{'name': 'free_food', 'type': 'predicate', 'probability': self.free_food},
                                {'name': 'durham_uk', 'type': 'predicate', 'probability': self.durham}]}
        return {'status': 'completed', 'output': [{'type': 'message',
            'content': [{'type': 'output_text', 'text': json.dumps(self.payload)}]}]}


def inspect_fixture(posts, cache, **kwargs):
    post = deepcopy(posts[0])
    records = []
    for i, _ in enumerate(post['media_urls']):
        identity = str(i) * 24
        path = cache / 'post' / (identity + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (10, 10), 'red').save(path)
        records.append({'image_id': identity, 'status': 'no_text'})
    post['image_ocr'] = {'coverage_complete': True, 'expected_images': len(records), 'images': records}
    return [post], {'coverage_complete': True}


class SubmissionProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.queue = FakeQueue()
        self.apify = FakeApify(self.queue)
        self.ai = FakeAI(self.queue)
        self.processor = Processor(self.queue, self.apify, self.ai, root=self.root,
            now=NOW, inspector=inspect_fixture, sleep=lambda _: None)
        self.job = {'id': 'test-job', 'lease_token': 'must-not-save', 'source_url': SOURCE, 'checkpoint': {}}

    def test_valid_post_checks_caption_and_every_panel_then_publishes(self):
        result = self.processor.process(self.job)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['source_url'], SOURCE)
        self.assertEqual(result['events'][0]['signup_required'], True)
        self.assertIn('Members only', result['events'][0]['food'])
        for endpoint, request in self.ai.calls:
            self.assertEqual(len(request['input'][0]['content']), 3)
            self.assertIn('Free pizza', request['input'][0]['content'][0]['text'])
            self.assertTrue(all(i['image_url'].startswith('data:image/png;base64,') for i in request['input'][0]['content'][1:]))
        self.assertEqual(self.queue.saved[-1]['validation']['images_inspected'], 2)
        self.assertEqual(len(self.apify.starts), 1)
        artifact_text = '\n'.join(p.read_text(encoding='utf-8') for p in self.root.rglob('*.json'))
        self.assertNotIn('signature=secret', artifact_text)
        self.assertNotIn('must-not-save', artifact_text)

    def test_negative_food_check_rejects_without_extraction(self):
        self.ai.free_food = 0.01
        result = self.processor.process(self.job)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual([c[0] for c in self.ai.calls], ['decisions'])
        self.assertEqual(result['events'], [])

    def test_non_uk_durham_is_not_published(self):
        self.ai.durham = 0.02
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')
        self.assertEqual(len(self.ai.calls), 1)

    def test_wrong_source_is_rejected_before_any_model_call(self):
        self.apify.item['url'] = 'https://www.instagram.com/p/WrongPost/'
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')
        self.assertFalse(self.ai.calls)

    def test_missing_carousel_panels_never_publish(self):
        self.apify.item['childPosts'] = self.apify.item['childPosts'][:1]
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')
        self.assertFalse(self.ai.calls)

    def test_video_carousel_panel_needs_review(self):
        self.apify.item['childPosts'][1]['type'] = 'Video'
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')
        self.assertFalse(self.ai.calls)

    def test_repo_only_published_source_cannot_bypass_server_verification(self):
        path = self.root / 'dist/data/events.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'events': [{'id': 'existing', 'source_url': SOURCE}]}))
        result = self.processor.process(self.job)
        self.assertEqual(result['status'], 'published')
        self.assertNotEqual(result['events'][0]['id'], 'existing')
        self.assertEqual(len(self.apify.starts), 1)
        self.assertEqual(len(self.ai.calls), 2)

    def test_completed_checkpoint_replay_needs_no_paid_requests(self):
        self.job['checkpoint'] = {'source_url': SOURCE, 'result': {'status': 'published',
            'reason': 'already checked', 'events': [{'id': 'existing'}]}}
        result = self.processor.process(self.job)
        self.assertEqual(result['events'][0]['id'], 'existing')
        self.assertEqual(self.apify.starts, [])
        self.assertEqual(self.ai.calls, [])

    def test_repo_cdn_url_cache_does_not_block_fresh_bounded_collection(self):
        path = self.root / 'dist/data/social-posts.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'posts': [{'source_url': SOURCE, 'media_type': 'Image',
            'media_urls': ['https://scontent.cdninstagram.com/expired.jpg?expired=yes']}]}))
        self.assertEqual(self.processor.process(self.job)['status'], 'published')
        self.assertEqual(len(self.apify.starts), 1)

    def test_restored_actual_images_and_ai_checkpoint_need_no_paid_work(self):
        self.processor.process(self.job)
        # Durable validation checkpoint before completion still includes both
        # confirmed AI results; the image artifact carries actual PNG bytes.
        checkpoint = next(c for c in reversed(self.queue.saved)
                          if c.get('validation') and c.get('extraction'))
        self.job['checkpoint'] = checkpoint
        self.apify.run = lambda identity: self.fail('Saved actual evidence should avoid all scraper access')
        self.processor.inspector = lambda *a, **kw: self.fail('Saved PNGs should not be downloaded again')
        self.assertEqual(self.processor.process(self.job)['status'], 'published')
        self.assertEqual(len(self.apify.starts), 1)
        self.assertEqual(len(self.ai.calls), 2)

    def test_large_extraction_does_not_get_stuck_replaying_invalid_publication(self):
        self.ai.payload['events'][0]['title'] = 'x' * 161
        result = self.processor.process(self.job)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(self.queue.saved[-1]['result']['status'], 'needs_review')

    def test_server_validation_rejection_moves_to_review(self):
        original_complete = self.queue.complete
        def complete(job, result):
            if result['status'] == 'published':
                raise CompletionRejected('test')
            return original_complete(job, result)
        self.queue.complete = complete
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')
        self.assertEqual(self.queue.results[-1]['status'], 'needs_review')

    def test_ambiguous_paid_start_does_not_start_again(self):
        self.apify.ambiguous = True
        result = self.processor.process(self.job)
        self.assertEqual(result['status'], 'needs_review')
        self.job['checkpoint'] = self.queue.saved[-1]
        self.processor.process(self.job)
        self.assertEqual(len(self.apify.starts), 1)
        # Also cover a runner killed before it could complete the failure.
        self.job['checkpoint'] = self.queue.saved[0]
        self.processor.process(self.job)
        self.assertEqual(len(self.apify.starts), 1)

    def test_resumes_existing_apify_run_without_start(self):
        self.job['checkpoint'] = {'apify': {'run_id': 'run123', 'phase': 'started', 'max_charge_usd': CAP}}
        self.assertEqual(self.processor.process(self.job)['status'], 'published')
        self.assertEqual(self.apify.starts, [])

    def test_queue_checkpoint_failure_stops_before_spending(self):
        self.queue.fail_checkpoint = True
        with self.assertRaises(QueueError):
            self.processor.process(self.job)
        self.assertEqual(self.apify.starts, [])

    def test_ambiguous_ai_request_is_not_retried(self):
        self.ai.ambiguous = True
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')
        self.job['checkpoint'] = next(c for c in self.queue.saved if c.get('decision_started'))
        self.processor.process(self.job)
        self.assertEqual(len(self.ai.calls), 1)
        self.assertEqual(len(self.apify.starts), 1)

    def test_older_post_and_old_event_do_not_shift_into_future(self):
        self.apify.item['timestamp'] = '2026-08-01T11:00:00Z'
        self.assertEqual(self.processor.process(self.job)['status'], 'rejected')
        self.assertEqual(self.ai.calls, [])
        self.apify.item = source_post()
        self.ai.payload['events'][0]['starts_at'] = '2026-09-23T18:30:00+01:00'
        self.assertEqual(self.processor.process(self.job)['status'], 'rejected')

    def test_missing_date_or_eligibility_evidence_is_not_published(self):
        self.ai.payload['events'][0]['evidence']['eligibility'] = None
        self.assertEqual(self.processor.process(self.job)['status'], 'needs_review')

    def test_url_canonicalization_does_not_accept_external_targets(self):
        self.assertEqual(canonical_source(SOURCE + '?img_index=2'), SOURCE)
        self.assertEqual(canonical_source('https://www.instagram.com/tv/TestPost/'), SOURCE)
        self.assertEqual(canonical_source('https://www.instagram.com/reel/TestPost/'), SOURCE)
        self.assertIsNone(canonical_source('https://www.instagram.com/reels/TestPost/'))
        self.assertIsNone(canonical_source('https://instagram.com.evil.test/p/TestPost/'))
        self.assertIsNone(canonical_source('https://user:secret@instagram.com/p/TestPost/'))

    def test_apify_secret_whitespace_is_trimmed_before_request_headers(self):
        session = Mock()
        response = Mock(status_code=201)
        response.iter_content.return_value = [json.dumps({'data': {'id': 'run123'}}).encode()]
        session.request.return_value = response
        client = DirectPostClient('  test-token\r\n', session=session)
        client.start(SOURCE, '2026-09-17T12:00:00+00:00')
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-token')
        self.assertEqual(kwargs['json']['onlyPostsNewerThan'], '2026-09-17T12:00:00Z')
        self.assertEqual(kwargs['params']['maxTotalChargeUsd'], 0.05)
        self.assertNotIn('maxItems', kwargs['params'])

    def test_confirmed_apify_error_keeps_status_type_without_raw_message(self):
        session = Mock()
        response = Mock(status_code=400)
        response.iter_content.return_value = [json.dumps({'error': {
            'type': 'invalid-input', 'message': 'Do not expose this raw response'}}).encode()]
        session.request.return_value = response
        client = DirectPostClient('test-token', session=session)
        with self.assertRaises(ApifyHTTPError) as raised:
            client.start(SOURCE, '2026-09-17T12:00:00Z')
        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(raised.exception.error_type, 'invalid-input')
        self.assertNotIn('raw response', str(raised.exception))
        self.assertEqual(session.request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
