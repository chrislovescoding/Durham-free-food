"""Replay saved real posts with network calls disabled before purchasing a sweep."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile

try:
    from scripts.ai_posts import process, ROOT
    from scripts.ai_events import validate_extraction
except ImportError:
    from ai_posts import process, ROOT
    from ai_events import validate_extraction


class NoNetwork:
    def post(self, *args, **kwargs):
        raise AssertionError('Preflight attempted an unexpected paid API request')


def preflight(folder):
    folder = Path(folder)
    posts = json.loads((folder / 'posts-with-images.json').read_text(encoding='utf-8'))['posts']
    cache = json.loads((ROOT / 'data/ai-post-cache.json').read_text(encoding='utf-8'))['posts']
    now = datetime.now(timezone.utc)
    by_url = {post['source_url'].rstrip('/'): post for post in posts}
    bridge = by_url['https://www.instagram.com/p/DeK21vgN4x0']
    bridge_ai = cache[bridge['id']]
    assert bridge_ai['decision']['probability'] >= 0.2, 'Known free pizza was missed'
    assert bridge_ai['extraction']['events'][0]['starts_at'].startswith('2026-10-08'), 'Bridge poster date was misread'
    assert not validate_extraction(bridge_ai['extraction'], bridge, now)[0], 'Expired Bridge event was published'
    dinner = by_url['https://www.instagram.com/p/DdlyySICnU4']
    dinner_events, dinner_queue = validate_extraction(cache[dinner['id']]['extraction'], dinner, now)
    assert len(dinner_events) == 7 and not dinner_queue, 'Seven dated dinners did not survive validation'
    assert all('4' in event['food'] and event['eligibility'] for event in dinner_events), 'Conditional meal eligibility disappeared'
    with tempfile.TemporaryDirectory(dir=ROOT / 'work') as temporary:
        root = Path(temporary)
        (root / 'data').mkdir()
        for filename in ('ai-post-cache.json', 'ai-events.json'):
            shutil.copy2(ROOT / 'data' / filename, root / 'data' / filename)
        with redirect_stdout(StringIO()):
            report = process(folder, root=root, client=NoNetwork())
        assert report['posts_considered'] == 49 and report['cache_hits'] == 49
        assert not report['errors'] and report['decision_requests'] == report['extraction_requests'] == 0
        assert report['events_extracted'] == 8, 'Saved real-post replay failed'
    print(json.dumps({'status': 'passed', 'real_posts_replayed': 49,
        'positive_controls': ['Bridge free pizza detected then expired', 'Seven first-visit dinners', 'Eight upcoming events'],
        'new_api_requests': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    preflight(parser.parse_args().folder)
