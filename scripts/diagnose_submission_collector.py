"""Read-only Apify diagnosis: metadata, existing runs and input validation only.

Never starts, resurrects or changes a run. Emits only allowlisted machine fields.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from process_suggestions import DirectPostClient, ACTOR, ApifyHTTPError, Deferred


def main():
    token = os.environ.get('APIFY_TOKEN')
    client = DirectPostClient(token)
    report = {'configuration': {'token_has_surrounding_whitespace': token != token.strip()}}
    for label, method, path, body, params in [
        ('actor', 'GET', f'/actors/{ACTOR}', None, None),
        ('recent_runs', 'GET', f'/actors/{ACTOR}/runs', None, {'limit': 10, 'desc': 'true'}),
        ('validate_original_input', 'POST', f'/actors/{ACTOR}/validate-input', {
            'directUrls': ['https://www.instagram.com/p/DdEDsJjN80W/'], 'resultsType': 'posts',
            'resultsLimit': 1, 'onlyPostsNewerThan': (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}, None),
    ]:
        try:
            result = client.request(method, path, body=body, params=params)
            data = result.get('data', result)
            if label == 'actor':
                pricing = data.get('pricingInfos', [])
                latest = pricing[-1] if pricing else {}
                report[label] = {'id': data.get('id'), 'name': data.get('name'),
                    'pricing_model': latest.get('pricingModel'), 'minimum_cap_usd': latest.get('minimalMaxTotalChargeUsd')}
            elif label == 'recent_runs':
                report[label] = [{k: run.get(k) for k in ('id', 'status', 'startedAt', 'finishedAt', 'usageTotalUsd')}
                    for run in data.get('items', [])]
            else:
                report[label] = {'valid': result.get('valid') is True}
        except ApifyHTTPError as error:
            report[label] = {'http_status': error.status, 'error_type': error.error_type}
        except Deferred:
            report[label] = {'error': 'unconfirmed-response'}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
