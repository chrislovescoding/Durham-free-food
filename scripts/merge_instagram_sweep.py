"""Merge saved sweep artifacts without collecting posts or calling model APIs.

Each worker starts with a repository snapshot. Only that worker's actual posts
may replace AI results; its unrelated snapshot entries must never be imported.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

try:
    from scripts.ai_events import evidence_signature
    from scripts.ai_posts import THRESHOLD
    from scripts.import_apify import merge_batch
    from scripts.refresh import load_json, write_json
except ImportError:
    from ai_events import evidence_signature
    from ai_posts import THRESHOLD
    from import_apify import merge_batch
    from refresh import load_json, write_json

ROOT = Path(__file__).resolve().parents[1]
COUNTS = ('posts_considered', 'cache_hits', 'decision_requests', 'extraction_requests',
          'images_sent_to_decisions', 'events_extracted')


def _money(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'Invalid {label}')
    return value


def _time(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Artifact timestamp must contain a UTC offset')
    return parsed.astimezone(timezone.utc)


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'Invalid {label}')
    return value


def _read_batches(folder):
    """Validate the complete receipt set before changing any repository data."""
    plan = load_json(folder / 'plan.json', {})
    specs = plan.get('batches')
    if not isinstance(specs, list) or not specs:
        raise ValueError('A saved plan.json with planned batches is required')
    total_cap = _money(plan.get('max_charge_usd'), 'plan charge cap')
    expected, batches, sum_caps = set(), [], 0.0
    for spec in specs:
        identity = spec.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', identity) or identity in expected:
            raise ValueError('Invalid or duplicate planned batch ID')
        expected.add(identity)
        batch_folder = folder / 'batches' / identity
        config_path = (folder / spec.get('config_path', f'batches/{identity}/config.json')).resolve()
        if not config_path.is_relative_to(folder.resolve()):
            raise ValueError('Batch configuration must remain inside the sweep folder')
        config = load_json(config_path, {})
        cap = _money(spec.get('max_charge_usd', config.get('max_charge_usd')), 'batch charge cap')
        if not 0 < cap <= 0.25:
            raise ValueError('Each collector batch must have a cap at most $0.25')
        if config and abs(_money(config.get('max_charge_usd'), 'configuration charge cap') - cap) > 1e-9:
            raise ValueError('Batch configuration disagrees with planned cap')
        sum_caps += cap
        receipt = load_json(batch_folder / 'run.json', {})
        run = receipt.get('run', {})
        charge = run.get('usageTotalUsd')
        if charge is not None:
            charge = _money(charge, 'receipt charge')
            if charge > cap + 1e-9:
                raise ValueError(f'Batch {identity} receipt exceeds its planned charge cap')
        for actual_cap in (receipt.get('max_charge_usd'), run.get('max_charge_usd')):
            if actual_cap is not None and _money(actual_cap, 'receipt cap') > cap + 1e-9:
                raise ValueError(f'Batch {identity} ran with a larger cap than planned')
        if config and receipt.get('input'):
            actual = receipt['input']
            for actual_key, config_key in (('username', 'usernames'), ('resultsLimit', 'posts_per_account'),
                                            ('dataDetailLevel', 'data_detail_level')):
                if config_key in config and actual.get(actual_key) != config[config_key]:
                    raise ValueError(f'Batch {identity} receipt does not match its planned configuration')
            cutoff = config.get('only_posts_newer_than')
            if cutoff and _time(actual.get('onlyPostsNewerThan')) != _time(cutoff):
                raise ValueError(f'Batch {identity} receipt does not match the planned date cutoff')
        image_path = batch_folder / 'posts-with-images.json'
        batch = load_json(image_path if image_path.exists() else batch_folder / 'posts.json', {})
        posts = batch.get('posts', [])
        if not isinstance(posts, list) or any(not isinstance(post, dict) or not post.get('id') or not post.get('source_url') for post in posts):
            raise ValueError(f'Batch {identity} contains invalid post records')
        if len(posts) > 50 or len({post['id'] for post in posts}) != len(posts):
            raise ValueError(f'Batch {identity} contains too many or duplicate posts')
        report = load_json(batch_folder / 'ai-report.json', {})
        considered = _count(report.get('posts_considered', 0), 'posts considered')
        ids = report.get('processed_post_ids', [post['id'] for post in posts[:considered]])
        if (not isinstance(ids, list) or len(ids) != len(set(ids)) or len(ids) != considered
                or not set(ids).issubset({post['id'] for post in posts})):
            raise ValueError(f'Batch {identity} AI report contains unprocessed or unknown posts')
        batches.append({'id': identity, 'folder': batch_folder, 'cap': cap, 'charge': charge,
                        'receipt': receipt, 'batch': batch, 'ai': report, 'processed_ids': ids,
                        'images': load_json(batch_folder / 'images.json', {}), 'has_images': image_path.exists()})
    unexpected = [path.name for path in (folder / 'batches').glob('*') if path.is_dir() and path.name not in expected]
    if unexpected:
        raise ValueError('Unexpected batch folders are not in the saved plan: ' + ', '.join(sorted(unexpected)))
    if sum_caps > total_cap + 1e-9:
        raise ValueError('Sum of batch caps exceeds the sweep charge cap')
    if 'sum_batch_caps_usd' in plan and abs(_money(plan['sum_batch_caps_usd'], 'sum of batch caps') - sum_caps) > 1e-8:
        raise ValueError('Planned batch caps do not match their declared total')
    if sum(item['charge'] or 0 for item in batches) > sum_caps + 1e-9:
        raise ValueError('Recorded sweep charges exceed the sum of planned caps')
    return plan, batches, sum_caps


def _merge_ai(item, cache, stored, social_posts):
    checkpoint = item['folder'] / 'ai-checkpoints'
    incoming_cache = load_json(checkpoint / 'ai-post-cache.json', {}).get('posts', {})
    incoming = load_json(checkpoint / 'ai-events.json', {})
    signatures = incoming.get('processed_signatures', {})
    completed_ids = set(incoming.get('processed_post_ids', []))
    accepted, stale = [], []
    for post_id in item['processed_ids']:
        if post_id not in completed_ids or post_id not in signatures:
            stale.append(post_id)
            continue
        signature = signatures[post_id]
        if post_id not in social_posts or evidence_signature(social_posts[post_id]) != signature:
            stale.append(post_id)
            continue
        previous_entry = cache['posts'].get(post_id, {})
        incoming_entry = incoming_cache.get(post_id, {})
        previous_at = stored.get('processed_at_by_post', {}).get(post_id, previous_entry.get('updated_at'))
        incoming_at = incoming.get('updated_at') or item['ai'].get('finished_at')
        if not incoming_at or _time(incoming_at) < _time(previous_at):
            stale.append(post_id)
            continue
        if incoming_entry and _time(incoming_entry.get('updated_at')) >= _time(previous_entry.get('updated_at')):
            cache['posts'][post_id] = deepcopy(incoming_entry)
        for field in ('events', 'queue'):
            selected = [deepcopy(record) for record in incoming.get(field, []) if record.get('post_id') == post_id]
            if any(record.get('input_signature') != signature for record in selected):
                raise ValueError(f'Batch {item["id"]} AI results do not match their input signature')
            stored[field] = [record for record in stored.get(field, []) if record.get('post_id') != post_id] + selected
        stored.setdefault('processed_signatures', {})[post_id] = signature
        stored.setdefault('processed_at_by_post', {})[post_id] = incoming_at
        accepted.append(post_id)
    stored['processed_post_ids'] = sorted(set(stored.get('processed_post_ids', [])) | set(accepted))
    if accepted and _time(incoming.get('updated_at')) > _time(stored.get('updated_at')):
        stored['updated_at'] = incoming['updated_at']
    return accepted, stale


def _current_success(post, cache, stored, social_posts):
    """A newer saved success can satisfy an older batch's identical evidence.

    A processed ID alone is insufficient: failed API requests also checkpoint
    IDs/signatures. Require an actual completed decision and, when positive, an
    extraction, with no unresolved cache error or mismatched stored evidence.
    """
    post_id = post['id']
    signature = evidence_signature(post)
    if (post_id not in social_posts or evidence_signature(social_posts[post_id]) != signature
            or post_id not in stored.get('processed_post_ids', [])
            or stored.get('processed_signatures', {}).get(post_id) != signature):
        return False
    entry = cache['posts'].get(post_id, {})
    probability = entry.get('decision', {}).get('probability')
    if (not entry.get('fingerprint') or entry.get('error') or isinstance(probability, bool)
            or not isinstance(probability, (float, int)) or not math.isfinite(probability)
            or not 0 <= probability <= 1):
        return False
    if probability >= THRESHOLD:
        payload = entry.get('extraction')
        if (not isinstance(payload, dict) or set(payload) != {'events', 'review_reason'}
                or not isinstance(payload['events'], list)
                or payload['review_reason'] is not None and not isinstance(payload['review_reason'], str)):
            return False
    records = [record for field in ('events', 'queue') for record in stored.get(field, [])
               if record.get('post_id') == post_id]
    return all(record.get('input_signature') == signature for record in records)


def _superseded_success(post, cache, stored, social_posts):
    """A completed newer observation replaces an older view of the same post."""
    current = social_posts.get(post['id'])
    if (not current or current.get('id') != post['id']
            or current.get('source_url', '').rstrip('/') != post.get('source_url', '').rstrip('/')
            or not current.get('observed_at') or not post.get('observed_at')
            or _time(current['observed_at']) <= _time(post['observed_at'])
            or evidence_signature(current) == evidence_signature(post)):
        return False
    return _current_success(current, cache, stored, social_posts)


def merge_sweep(folder, root=ROOT):
    folder, root = Path(folder), Path(root)
    plan, batches, sum_caps = _read_batches(folder)
    social_path = root / 'dist/data/social-posts.json'
    cache_path, events_path = root / 'data/ai-post-cache.json', root / 'data/ai-events.json'
    social = load_json(social_path, {'posts': [], 'accounts': [], 'status': {}})
    social.setdefault('status', {})
    cache = load_json(cache_path, {'version': 'grubr-vision-v1', 'posts': {}})
    stored = load_json(events_path, {'events': [], 'queue': [], 'processed_post_ids': []})
    report = {'updated_at': datetime.now(timezone.utc).isoformat(), 'status': 'complete',
              'expected_batches': len(batches), 'completed_batches': 0, 'max_charge_usd': plan['max_charge_usd'],
              'sum_batch_caps_usd': round(sum_caps, 6), 'run_charge_usd': 0, 'returned_items': 0,
              'accepted_posts': 0, 'images_inspected': 0, 'images_failed_or_skipped': 0,
              'errors': [], 'superseded_errors': [], 'batches': [], **{key: 0 for key in COUNTS}}
    seen, processed, completed, retained, superseded = set(), set(), set(), set(), set()
    # Merge all observations first, so stale results cannot replace newer evidence
    # when the same coauthored post appears in two different account batches.
    for item in batches:
        if 'posts' in item['batch'] and item['receipt'].get('run', {}).get('id'):
            previous_updated_at = social.get('updated_at')
            social = merge_batch(social, item['batch'], item['receipt'], item['images'])
            if _time(previous_updated_at) > _time(social.get('updated_at')):
                social['updated_at'] = previous_updated_at
    current_posts = {post['id']: post for post in social['posts']}
    merged = {}
    for item in batches:
        merged[item['id']] = _merge_ai(item, cache, stored, current_posts)
    # Assess final coverage only after every checkpoint has been merged. Shared
    # posts can be recovered by a later batch, irrespective of batch ordering.
    for item in batches:
        run, ai = item['receipt'].get('run', {}), item['ai']
        accepted, _ = merged[item['id']]
        posts = item['batch'].get('posts', [])
        successful = {post['id'] for post in posts if _current_success(post, cache, stored, current_posts)}
        superseded_ids = {post['id'] for post in posts
                          if _superseded_success(post, cache, stored, current_posts)}
        successful.update(superseded_ids)
        retained_ids = successful - set(accepted)
        stale = sorted({post['id'] for post in posts} - successful)
        seen.update(post['id'] for post in posts)
        processed.update(accepted)
        completed.update(successful)
        retained.update(retained_ids)
        superseded.update(superseded_ids)
        historical_errors = [{'batch_id': item['id'], **error} for error in ai.get('errors', [])]
        errors = [error for error in historical_errors if error.get('post_id') not in successful]
        superseded_errors = [error for error in historical_errors if error.get('post_id') in successful]
        report['errors'].extend(errors)
        report['superseded_errors'].extend(superseded_errors)
        complete = (run.get('status') == 'SUCCEEDED' and item['charge'] is not None
                    and 'posts' in item['batch'] and (not posts or (item['has_images']
                    and ai.get('finished_at') and len(successful) == len(posts) and not errors and not stale)))
        status = {'id': item['id'], 'status': 'complete' if complete else 'partial',
                  'collection_status': run.get('status', 'missing'), 'run_id': run.get('id'),
                  'run_charge_usd': item['charge'], 'max_charge_usd': item['cap'],
                  'returned_items': _count(item['receipt'].get('returned_items', 0), 'returned items'),
                  'accepted_posts': len(posts), 'ai_posts_merged': len(accepted),
                  'ai_posts_completed': len(successful), 'ai_posts_retained': len(retained_ids),
                  'retained_ai_post_ids': sorted(retained_ids),
                  'superseded_observation_post_ids': sorted(superseded_ids),
                  'stale_or_missing_ai_post_ids': stale, 'errors': errors,
                  'superseded_errors': superseded_errors}
        report['batches'].append(status)
        report['completed_batches'] += int(complete)
        report['run_charge_usd'] += item['charge'] or 0
        report['returned_items'] += status['returned_items']
        report['accepted_posts'] += len(posts)
        for key in ('images_inspected', 'images_failed_or_skipped'):
            report[key] += _count(item['images'].get(key, 0), key)
        for key in COUNTS:
            report[key] += _count(ai.get(key, 0), key)
    report['run_charge_usd'] = round(report['run_charge_usd'], 6)
    report.update(unique_posts=len(seen), unique_ai_posts_merged=len(processed),
                  unique_ai_posts_completed=len(completed), unique_ai_posts_retained=len(retained),
                  unique_superseded_observation_posts=len(superseded),
                  planned_accounts=plan.get('planned_accounts'), requested_posts=plan.get('requested_posts'))
    if report['completed_batches'] != report['expected_batches']:
        report['status'] = 'partial'
    social['status']['instagram_sweep'] = {key: value for key, value in report.items() if key not in ('batches', 'errors')}
    # Keep human-reviewed records untouched; refresh.py applies their precedence.
    write_json(social_path, social)
    write_json(cache_path, cache)
    write_json(events_path, stored)
    write_json(root / 'data/instagram-sweep-report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=ROOT / 'work/instagram-sweep')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = merge_sweep(args.folder, args.root)
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
