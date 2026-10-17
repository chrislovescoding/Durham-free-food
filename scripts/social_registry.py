"""Reviewed account mappings supplement discovery without guessing handles."""

from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from scripts.social_posts import canonical_account_url, platform_for
except ModuleNotFoundError:
    from social_posts import canonical_account_url, platform_for


def apply_registry(societies, registry, now):
    result = deepcopy(societies)
    by_url = {s['url'].rstrip('/'): s for s in result}
    errors = []
    for index, record in enumerate(registry.get('accounts', [])):
        try:
            society = by_url.get(str(record.get('society_url', '')).rstrip('/'))
            if society is None:
                raise ValueError('Society must match a current SU directory URL')
            account = canonical_account_url(record.get('url'))
            platform = record.get('platform')
            if not account or platform_for(account) != platform:
                raise ValueError('A supported Instagram profile or Facebook Page URL is required')
            evidence = urlparse(record.get('evidence_url', ''))
            if evidence.scheme != 'https' or not evidence.hostname or evidence.username or evidence.password:
                raise ValueError('An HTTPS ownership evidence URL is required')
            verified = datetime.fromisoformat(record.get('verified_at', '').replace('Z', '+00:00'))
            if verified.tzinfo is None or verified > now:
                raise ValueError('Verification needs an actual past timestamp with timezone')
            if record.get('reviewed') is not True:
                raise ValueError('Mapping must be reviewed against the ownership evidence')
            entries = society.setdefault('social_accounts', [])
            entries.append({'platform': platform, 'url': account, 'evidence_url': record['evidence_url'],
                            'method': 'reviewed_registry', 'verified_at': verified.astimezone(timezone.utc).isoformat()})
            society.setdefault(platform + '_url', account)
        except (ValueError, TypeError, AttributeError) as error:
            errors.append({'index': index, 'reason': str(error)})
    return result, errors


def expand_accounts(societies):
    """Adapt a multi-account registry for providers expecting one URL per row."""
    expanded = []
    for society in societies:
        seen = set()
        entries = society.get('social_accounts', []) + [
            {'platform': platform, 'url': society.get(platform + '_url')}
            for platform in ('instagram', 'facebook')]
        for account in entries:
            platform, url = account.get('platform'), account.get('url')
            if platform not in ('instagram', 'facebook') or not url:
                continue
            key = (platform, canonical_account_url(url) or url.rstrip('/'))
            if key in seen:
                continue
            seen.add(key)
            expanded.append({'name': society['name'], 'url': society['url'], platform + '_url': url})
    return expanded
