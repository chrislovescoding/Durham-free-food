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
    checkpoint = folder / 'ai-checkpoints'
    if not (checkpoint / 'ai-post-cache.json').is_file():
        raise ValueError('Preflight requires the saved AI artifact with its matching model checkpoints')
    cache = json.loads((checkpoint / 'ai-post-cache.json').read_text(encoding='utf-8'))['posts']
    # Historical fixtures have a fixed assessment date; the regression check
    # must still work after the dinners themselves have passed.
    now = datetime(2026, 10, 17, 13, tzinfo=timezone.utc)
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
            shutil.copy2(checkpoint / filename, root / 'data' / filename)
        with redirect_stdout(StringIO()):
            report = process(folder, root=root, client=NoNetwork())
        assert report['posts_considered'] == 49 and report['cache_hits'] == 49
        assert not report['errors'] and report['decision_requests'] == report['extraction_requests'] == 0
        replayed = json.loads((root / 'data/ai-events.json').read_text(encoding='utf-8'))
        assert any(e['post_id'] == bridge['id'] and e['starts_at'].startswith('2026-10-08')
                   for e in replayed['events']), 'Validated Bridge history was not retained'
        assert report['events_extracted'] >= 9, 'Saved real-post history replay failed'
    print(json.dumps({'status': 'passed', 'real_posts_replayed': 49,
        'positive_controls': ['Bridge free pizza retained as history', 'Seven first-visit dinners', 'Saved images and responses reused'],
        'new_api_requests': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    preflight(parser.parse_args().folder)
