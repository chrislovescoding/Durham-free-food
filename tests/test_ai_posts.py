import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import requests

from scripts.ai_posts import APIError, OpenAIClient, compact_image_body, prepare_input, process


def extraction():
    return {"events": [{
        "title": "Free pizza social", "starts_at": "2030-10-23T18:30:00+01:00",
        "ends_at": None, "location": "Elvet Methodist Church, Durham",
        "food": "Free pizza", "food_is_free": True, "price": None,
        "signup_required": None, "signup_deadline": None, "eligibility": None,
        "dietary": [], "description": "Society social with free pizza.",
        "evidence": {"food": "Free pizza", "date": "23 October 2030",
                     "time": "18:30", "location": "Elvet Methodist Church, Durham",
                     "eligibility": None}, "needs_review": False,
    }], "review_reason": None}


class FakeClient:
    def __init__(self, probability=0.9, error=None):
        self.probability = probability
        self.error = error
        self.calls = []

    def post(self, endpoint, body):
        self.calls.append((endpoint, deepcopy(body)))
        if self.error:
            raise APIError(self.error)
        if endpoint == "decisions":
            return {"id": "decision-test", "answers": [{"type": "predicate",
                    "name": "free_food", "probability": self.probability}]}
        return {"id": "response-test", "status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text",
                                             "text": json.dumps(extraction())}]}]}


class AIPostPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "artifact"
        self.image_id = "a" * 24
        self.image_path = self.folder / "image-cache" / "post-test" / (self.image_id + ".png")
        self.image_path.parent.mkdir(parents=True)
        Image.new("RGB", (10, 10), "red").save(self.image_path)
        self.post = {
            "id": "ig:test", "platform": "instagram", "society": "Durham Test Society",
            "source_url": "https://www.instagram.com/p/TESTpost/",
            "published_at": "2030-10-20T12:00:00Z", "text": "Free pizza! 23 October 2030, 18:30, Elvet Methodist Church, Durham",
            "media_urls": ["https://scontent.cdninstagram.com/photo.jpg"],
            "image_ocr": {"coverage_complete": True, "expected_images": 1,
                          "images": [{"image_id": self.image_id, "status": "ok",
                                      "local_path": "/ignored/arbitrary/path.png"}]},
        }
        self.write_post()

    def write_post(self):
        (self.folder / "posts-with-images.json").write_text(json.dumps({"posts": [self.post]}), encoding="utf-8")

    def stored(self):
        return json.loads((self.root / "data/ai-events.json").read_text(encoding="utf-8"))

    def test_actual_png_is_sent_to_both_api_stages(self):
        client = FakeClient()
        report = process(self.folder, root=self.root, client=client)
        self.assertEqual([call[0] for call in client.calls], ["decisions", "responses"])
        for endpoint, body in client.calls:
            image = body["input"][0]["content"][1]
            self.assertEqual(image["type"], "input_image")
            self.assertEqual(base64.b64decode(image["image_url"].split(",", 1)[1]), self.image_path.read_bytes())
            self.assertIn("Free pizza!", body["input"][0]["content"][0]["text"])
        self.assertEqual(report["events_extracted"], 1)
        self.assertFalse(self.stored()["events"][0]["extraction"]["human_reviewed"])

    def test_downloaded_image_without_local_ocr_is_still_complete_vision_input(self):
        self.post['image_ocr']['images'][0]['status'] = 'no_text'
        self.write_post()
        client = FakeClient()
        report = process(self.folder, root=self.root, client=client)
        self.assertEqual(report['events_extracted'], 1)
        self.assertEqual(report['processed_post_ids'], [self.post['id']])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self.stored()['queue'], [])

    def test_pipeline_retains_validated_past_events_for_the_archive(self):
        payload = extraction()
        payload['events'][0]['starts_at'] = '2000-10-23T18:30:00+01:00'
        with patch(__name__ + '.extraction', return_value=payload):
            report = process(self.folder, root=self.root, client=FakeClient())
        self.assertEqual(report['events_extracted'], 1)
        self.assertEqual(self.stored()['events'][0]['starts_at'], '2000-10-23T18:30:00+01:00')

    def test_empty_recent_collection_needs_no_api_request(self):
        (self.folder / 'posts-with-images.json').write_text(json.dumps({'posts': []}), encoding='utf-8')
        client = FakeClient()
        report = process(self.folder, root=self.root, client=client)
        self.assertEqual(client.calls, [])
        self.assertEqual(report['posts_considered'], 0)
        self.assertEqual(report['processed_post_ids'], [])
        self.assertEqual(report['errors'], [])

    def test_negative_decision_skips_extraction_and_is_cached(self):
        client = FakeClient(probability=0.05)
        first = process(self.folder, root=self.root, client=client)
        second = process(self.folder, root=self.root, client=client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(first["extraction_requests"], 0)
        self.assertEqual(second["cache_hits"], 1)
        self.assertEqual(self.stored()["processed_post_ids"], [self.post["id"]])
        self.assertEqual(self.stored()["events"], [])

    def test_cached_extraction_is_reused_without_api_calls(self):
        client = FakeClient()
        process(self.folder, root=self.root, client=client)
        second = process(self.folder, root=self.root, client=client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(second["decision_requests"], 0)
        self.assertEqual(second["extraction_requests"], 0)
        self.assertEqual(len(self.stored()["events"]), 1)

    def test_artifact_checkpoint_is_persisted_when_every_post_is_cached(self):
        process(self.folder, root=self.root, client=FakeClient())
        cache_path = self.root / "data/ai-post-cache.json"
        checkpoint = cache_path.read_text(encoding="utf-8")
        checkpoint_path = self.folder / "ai-checkpoints/ai-post-cache.json"
        checkpoint_path.parent.mkdir()
        checkpoint_path.write_text(checkpoint, encoding="utf-8")
        cache_path.unlink()
        client = FakeClient()
        report = process(self.folder, root=self.root, client=client)
        self.assertEqual(client.calls, [])
        self.assertEqual(report["cache_hits"], 1)
        self.assertEqual(report["decision_requests"], 0)
        self.assertEqual(report["extraction_requests"], 0)
        self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), json.loads(checkpoint))
        self.assertEqual(len(self.stored()["events"]), 1)

    def test_changed_caption_invalidates_both_cached_stages(self):
        client = FakeClient()
        process(self.folder, root=self.root, client=client)
        self.post["text"] += " Please arrive promptly."
        self.write_post()
        process(self.folder, root=self.root, client=client)
        self.assertEqual(len(client.calls), 4)

    def test_changed_image_invalidates_both_cached_stages(self):
        client = FakeClient()
        process(self.folder, root=self.root, client=client)
        Image.new("RGB", (10, 10), "blue").save(self.image_path)
        process(self.folder, root=self.root, client=client)
        self.assertEqual(len(client.calls), 4)

    def test_partial_media_cannot_publish_an_extracted_event(self):
        self.post["image_ocr"]["coverage_complete"] = False
        self.post["image_ocr"]["expected_images"] = 2
        self.write_post()
        report = process(self.folder, root=self.root, client=FakeClient())
        self.assertEqual(report["events_extracted"], 0)
        self.assertEqual(self.stored()["events"], [])
        self.assertIn("Incomplete", self.stored()["queue"][0]["reason"])

    def test_negative_partial_media_still_requires_review(self):
        self.post["image_ocr"]["coverage_complete"] = False
        self.write_post()
        process(self.folder, root=self.root, client=FakeClient(probability=0.01))
        self.assertEqual(len(self.stored()["queue"]), 1)

    def test_missing_image_makes_no_paid_request(self):
        self.image_path.unlink()
        client = FakeClient()
        report = process(self.folder, root=self.root, client=client)
        self.assertEqual(client.calls, [])
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(len(self.stored()["queue"]), 1)

    def test_source_carousel_above_thirty_megabytes_can_be_compacted(self):
        original_png = self.image_path.read_bytes()
        records = []
        for number in range(4):
            identity = f'{number:024x}'
            path = self.image_path.parent / (identity + '.png')
            path.write_bytes(original_png + b'\0' * (8 * 1024 * 1024))
            records.append({'image_id': identity, 'status': 'ok'})
        self.post['image_ocr'].update(expected_images=4, images=records)
        self.post['media_urls'] = [f'https://scontent.cdninstagram.com/{number}.jpg' for number in range(4)]
        inputs, fingerprint, complete, count = prepare_input(self.post, self.folder)
        compact, metadata = compact_image_body({'input': inputs})
        self.assertTrue(complete)
        self.assertEqual(count, 4)
        self.assertEqual(metadata['images'], 4)
        self.assertGreater(metadata['image_bytes_before'], 30 * 1024 * 1024)
        self.assertLessEqual(metadata['image_bytes_after'], 4 * 1024 * 1024)
        self.assertEqual(len(compact['input'][0]['content']), 5)
        self.assertEqual(prepare_input(self.post, self.folder)[1], fingerprint)

    def test_repeated_error_is_not_automatically_retried(self):
        client = FakeClient(error="OpenAI decisions HTTP 429; no automatic retry")
        process(self.folder, root=self.root, client=client)
        process(self.folder, root=self.root, client=client)
        self.assertEqual(len(client.calls), 1)
        process(self.folder, root=self.root, client=client, retry_errors=True)
        self.assertEqual(len(client.calls), 2)

    def test_luna_failure_keeps_decision_when_explicitly_retried(self):
        class FailLunaOnce(FakeClient):
            failed = False
            def post(self, endpoint, body):
                if endpoint == "responses" and not self.failed:
                    self.failed = True
                    self.calls.append((endpoint, deepcopy(body)))
                    raise APIError("OpenAI request did not complete; no automatic retry")
                return super().post(endpoint, body)
        client = FailLunaOnce()
        process(self.folder, root=self.root, client=client)
        process(self.folder, root=self.root, client=client)
        self.assertEqual([call[0] for call in client.calls], ["decisions", "responses"])
        process(self.folder, root=self.root, client=client, retry_errors=True)
        self.assertEqual([call[0] for call in client.calls], ["decisions", "responses", "responses"])
        self.assertEqual(len(self.stored()["events"]), 1)


class OpenAIClientTests(unittest.TestCase):
    @staticmethod
    def image_body():
        images = []
        for colour in ('red', 'blue'):
            buffer = BytesIO()
            Image.new('RGB', (12, 18), colour).save(buffer, format='PNG')
            images.append({'type': 'input_image', 'detail': 'high', 'image_url':
                           'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')})
        return {'model': 'gpt-6-luna', 'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'Caption, dates and eligibility stay exactly unchanged.'}, *images]}]}

    @staticmethod
    def session_with_statuses(statuses):
        class Response:
            def __init__(self, status):
                self.status_code = status
            def json(self):
                return {'id': 'test-result'} if self.status_code == 200 else {'error': {'code': 'test_error'}}
        class Session:
            def __init__(self):
                self.calls = []
            def post(self, *args, **kwargs):
                self.calls.append(deepcopy(kwargs))
                return Response(statuses[len(self.calls) - 1])
        return Session()

    def test_confirmed_413_retries_once_preserving_every_image_and_caption(self):
        session = self.session_with_statuses([413, 200])
        client = OpenAIClient('sk-private-secret', session=session)
        body = self.image_body()
        original = deepcopy(body)
        self.assertEqual(client.post('decisions', body), {'id': 'test-result'})
        self.assertEqual(body, original)
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(client.transport_retries), 1)
        first, second = [call['json'] for call in session.calls]
        self.assertEqual(first, original)
        first_content, second_content = first['input'][0]['content'], second['input'][0]['content']
        self.assertEqual(first_content[0], second_content[0])
        self.assertEqual(len(first_content), len(second_content))
        total = 0
        for original_image, compact_image in zip(first_content[1:], second_content[1:]):
            self.assertTrue(compact_image['image_url'].startswith('data:image/jpeg;base64,'))
            self.assertEqual(compact_image['detail'], original_image['detail'])
            data = base64.b64decode(compact_image['image_url'].split(',', 1)[1])
            total += len(data)
            with Image.open(BytesIO(data)) as image:
                self.assertEqual(image.size, (12, 18))
                self.assertEqual(image.format, 'JPEG')
        self.assertLessEqual(total, 4 * 1024 * 1024)
        self.assertLessEqual(len(json.dumps(second).encode()), 6 * 1024 * 1024)

    def test_second_413_is_not_retried(self):
        session = self.session_with_statuses([413, 413])
        client = OpenAIClient('test-key', session=session)
        with self.assertRaisesRegex(APIError, 'HTTP 413'):
            client.post('responses', self.image_body())
        self.assertEqual(client.calls, 2)

    def test_other_http_failures_are_never_automatically_retried(self):
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                session = self.session_with_statuses([status])
                client = OpenAIClient('test-key', session=session)
                with self.assertRaisesRegex(APIError, f'HTTP {status}'):
                    client.post('decisions', self.image_body())
                self.assertEqual(client.calls, 1)
                self.assertEqual(client.transport_retries, [])

    def test_payload_without_compactable_images_is_not_retried(self):
        session = self.session_with_statuses([413])
        client = OpenAIClient('test-key', session=session)
        with self.assertRaisesRegex(APIError, 'could not be safely compacted'):
            client.post('decisions', {'input': []})
        self.assertEqual(client.calls, 1)

    def test_compact_retry_counts_towards_hard_request_limit(self):
        session = self.session_with_statuses([413])
        client = OpenAIClient('test-key', session=session)
        client.calls = 99
        with self.assertRaisesRegex(APIError, 'Maximum 100'):
            client.post('decisions', self.image_body())
        self.assertEqual(client.calls, 100)
        self.assertEqual(len(session.calls), 1)

    def test_oversize_compacted_payload_does_not_drop_images_or_retry(self):
        session = self.session_with_statuses([413])
        client = OpenAIClient('test-key', session=session)
        body = self.image_body()
        def too_large(image, fp, **kwargs):
            fp.write(b'x' * (3 * 1024 * 1024))
        with patch.object(Image.Image, 'save', too_large):
            with self.assertRaisesRegex(APIError, 'all images cannot fit'):
                client.post('decisions', body)
        self.assertEqual(client.calls, 1)

    def test_large_request_is_compacted_before_first_http_call(self):
        session = self.session_with_statuses([200])
        client = OpenAIClient('test-key', session=session)
        body = self.image_body()
        for part in body['input'][0]['content'][1:]:
            original_png = base64.b64decode(part['image_url'].split(',', 1)[1])
            part['image_url'] = 'data:image/png;base64,' + base64.b64encode(original_png + b'\0' * (3 * 1024 * 1024)).decode()
        original = deepcopy(body)
        self.assertEqual(client.post('decisions', body), {'id': 'test-result'})
        self.assertEqual(client.calls, 1)
        self.assertEqual(body, original)
        self.assertEqual(len(client.transport_compactions), 1)
        self.assertEqual(client.transport_retries, [])
        sent = session.calls[0]['json']['input'][0]['content']
        self.assertEqual(sent[0], original['input'][0]['content'][0])
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(part['image_url'].startswith('data:image/jpeg;base64,') for part in sent[1:]))

    def test_compaction_reduces_dimensions_only_when_full_size_cannot_fit(self):
        body = self.image_body()
        real_save = Image.Image.save
        qualities = []
        def first_too_large(image, fp, **kwargs):
            qualities.append(kwargs['quality'])
            if kwargs['quality'] == 90:
                fp.write(b'x' * (3 * 1024 * 1024))
            else:
                real_save(image, fp, **kwargs)
        with patch.object(Image.Image, 'save', first_too_large):
            compact, metadata = compact_image_body(body)
        self.assertEqual(qualities, [90, 90, 80, 80])
        self.assertEqual(metadata['max_edge'], 1600)
        self.assertEqual(len(compact['input'][0]['content']), 3)

    def test_network_exception_does_not_expose_api_key(self):
        class Session:
            def post(self, *args, **kwargs):
                raise requests.ConnectionError("Bearer sk-private-secret")
        with self.assertRaises(APIError) as caught:
            OpenAIClient("sk-private-secret", session=Session()).post("decisions", {})
        self.assertNotIn("sk-private-secret", str(caught.exception))
        self.assertIn("no automatic retry", str(caught.exception))

    def test_error_response_body_and_auth_are_not_logged(self):
        class Response:
            status_code = 401
            def json(self):
                return {"error": {"message": "Key sk-private-secret is invalid",
                                  "code": "invalid_api_key"}}
        class Session:
            def post(self, *args, **kwargs):
                self.arguments = args, kwargs
                return Response()
        session = Session()
        with self.assertRaises(APIError) as caught:
            OpenAIClient("sk-private-secret", session=session).post("decisions", {})
        self.assertNotIn("sk-private-secret", str(caught.exception))
        self.assertIn("401", str(caught.exception))
        self.assertFalse(session.arguments[1]["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
