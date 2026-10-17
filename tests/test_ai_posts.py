import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import requests

from scripts.ai_posts import APIError, OpenAIClient, process


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
