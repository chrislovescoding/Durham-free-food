"""Recover validated historical events from saved model responses, without APIs.

Requires the original PNG archives to prove the cached response used the exact
caption and images. An edited post, changed model prompt, missing image, partial
video coverage or unresolved model error is never promoted into the archive.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

try:
    from scripts.ai_events import evidence_signature, validate_extraction
    from scripts.ai_posts import MODEL, ROOT, THRESHOLD, VERSION, prepare_input
    from scripts.refresh import load_json, parse_datetime, write_json
except ImportError:
    from ai_events import evidence_signature, validate_extraction
    from ai_posts import MODEL, ROOT, THRESHOLD, VERSION, prepare_input
    from refresh import load_json, parse_datetime, write_json


def recover_history(root=ROOT, archive_roots=None, *, now=None, write=False):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    cache = load_json(root / 'data/ai-post-cache.json', {})
    stored = load_json(root / 'data/ai-events.json', {})
    current = {p['id']: p for p in load_json(root / 'dist/data/social-posts.json', {}).get('posts', [])}
    if cache.get('version') != VERSION:
        raise ValueError('The saved model cache uses a different pipeline version')
    result = deepcopy(stored)
    report = {'recovered_at': now.isoformat(), 'apify_requests': 0, 'openai_requests': 0,
              'candidate_posts': 0, 'replayed_posts': 0, 'events_recovered': 0,
              'skipped': [], 'replayed_post_ids': [], 'written': write}
    candidates = {}
    for post_id, entry in cache.get('posts', {}).items():
        if isinstance(entry.get('extraction'), dict) and entry['extraction'].get('events'):
            candidates[post_id] = entry
    report['candidate_posts'] = len(candidates)
    archives = {}
    for folder in archive_roots or [root / 'work']:
        folder = Path(folder)
        manifests = [folder] if folder.is_file() else folder.rglob('posts-with-images.json')
        for path in manifests:
            for post in load_json(path, {}).get('posts', []):
                if post.get('id') in candidates:
                    archives.setdefault(post['id'], []).append((path.parent, post))
    for post_id, entry in candidates.items():
        post = current.get(post_id)
        reason = None
        signature = evidence_signature(post) if post else None
        probability = entry.get('decision', {}).get('probability')
        if (not post or post_id not in stored.get('processed_post_ids', [])
                or stored.get('processed_signatures', {}).get(post_id) != signature):
            reason = 'Missing or changed current post evidence'
        elif (entry.get('post_id') != post_id or entry.get('source_url', '').rstrip('/') != post.get('source_url', '').rstrip('/')
                or entry.get('model') != MODEL or entry.get('error')
                or isinstance(probability, bool) or not isinstance(probability, (int, float))
                or not math.isfinite(probability) or not THRESHOLD <= probability <= 1):
            reason = 'The cached decision/extraction is not a completed positive result'
        verified = None
        if reason is None:
            try:
                verified = parse_datetime(entry.get('updated_at'))
                if verified > now:
                    raise ValueError('Future cache timestamp')
            except (ValueError, TypeError):
                reason = 'Invalid cache timestamp'
        matched = False
        if reason is None:
            for folder, archived in archives.get(post_id, []):
                if evidence_signature(archived) != signature:
                    continue
                try:
                    _, fingerprint, complete, _ = prepare_input(archived, folder)
                except (OSError, ValueError, TypeError, KeyError):
                    continue
                if fingerprint == entry.get('fingerprint') and complete:
                    matched = True
                    break
            if not matched:
                reason = 'No complete saved image archive matches the cached fingerprint'
        if reason:
            report['skipped'].append({'post_id': post_id, 'reason': reason})
            continue
        events, queue = validate_extraction(entry['extraction'], {**post, 'extraction_model': MODEL}, now, include_past=True)
        for record in events + queue:
            record['input_signature'] = signature
        for event in events:
            event['verified_at'] = verified.isoformat()
            event['decision_probability'] = probability
        for field, incoming in (('events', events), ('queue', queue)):
            result[field] = [r for r in result.get(field, []) if r.get('post_id') != post_id] + incoming
        report['replayed_posts'] += 1
        report['events_recovered'] += len(events)
        report['replayed_post_ids'].append(post_id)
    result['history_recovered_at'] = now.isoformat()
    result['updated_at'] = now.isoformat()
    if write:
        write_json(root / 'data/ai-events.json', result)
        write_json(root / 'data/event-history-report.json', report)
    return result, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--archive-root', action='append', type=Path,
                        help='Search saved collector folders; repeat for multiple locations')
    parser.add_argument('--now', help='ISO timestamp for reproducible checks')
    parser.add_argument('--write', action='store_true', help='Save recovered events; default is a dry run')
    args = parser.parse_args()
    _, report = recover_history(args.root, args.archive_root,
                                now=parse_datetime(args.now) if args.now else None, write=args.write)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
