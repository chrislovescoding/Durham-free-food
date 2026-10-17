"""Merge an already purchased and image-inspected batch; never calls Apify."""
import argparse
from copy import deepcopy
from pathlib import Path

try:
    from scripts.refresh import load_json, write_json, merge_post_evidence
except ImportError:
    from refresh import load_json, write_json, merge_post_evidence

ROOT = Path(__file__).resolve().parents[1]


def merge_batch(previous, batch, run_report, image_report):
    result = deepcopy(previous)
    image_report = {key: value for key, value in image_report.items() if key != 'manifest'}
    posts = {post['source_url']: post for post in result.get('posts', [])}
    for original in batch['posts']:
        post = deepcopy(original)
        post['image_review_required'] = True
        for item in post.get('image_ocr', {}).get('images', []):
            item.pop('local_path', None)
        posts[post['source_url']] = merge_post_evidence(posts.get(post['source_url'], {}), post)
    result['posts'] = sorted(posts.values(), key=lambda p: p.get('published_at') or '', reverse=True)
    accounts = {(a.get('url'), a.get('provider', 'public_html')): a for a in result.get('accounts', [])}
    for account in batch.get('accounts', []):
        accounts[(account['url'], 'apify')] = account
    result['accounts'] = list(accounts.values())
    result['updated_at'] = batch['updated_at']
    result['status']['apify'] = {'status': 'ok' if run_report['run']['status'] == 'SUCCEEDED' else 'partial',
        'posts_collected': len(batch['posts']), 'run_id': run_report['run']['id'],
        'run_charge_usd': run_report['run'].get('usageTotalUsd'), 'max_charge_usd': run_report['run'].get('max_charge_usd'),
        'images': image_report}
    result['status']['posts_stored'] = len(result['posts'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, default=ROOT / 'work/apify-pilot/posts-with-images.json')
    parser.add_argument('--run-report', type=Path, default=ROOT / 'work/apify-pilot/run.json')
    parser.add_argument('--image-report', type=Path, default=ROOT / 'work/apify-pilot/images.json')
    args = parser.parse_args()
    path = ROOT / 'dist/data/social-posts.json'
    run_report, image_report = load_json(args.run_report, {}), load_json(args.image_report, {})
    batch = load_json(args.batch, {})
    if 'posts' not in batch or 'run' not in run_report:
        raise SystemExit('A saved batch and run receipt are required; nothing was imported.')
    result = merge_batch(load_json(path, {'posts': [], 'accounts': [], 'status': {}}), batch, run_report, image_report)
    write_json(path, result)
    write_json(ROOT / 'data/apify-pilot-report.json', {'collection': run_report,
        'image_inspection': {key: value for key, value in image_report.items() if key != 'manifest'}})
    print(f"Imported {len(batch['posts'])} inspected posts; {len(result['posts'])} total distinct stored posts.")


if __name__ == '__main__':
    main()
