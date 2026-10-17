"""Check available credit and reserve one approved plan; no actor starts here."""
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path

try:
    from scripts.apify_posts import Client, ApifyError, load_accounts, write_json, stamp, ROOT
    from scripts.plan_instagram_sweep import validate_plan
except ImportError:
    from apify_posts import Client, ApifyError, load_accounts, write_json, stamp, ROOT
    from plan_instagram_sweep import validate_plan


def reserve(plan, client, ledger, workflow_run_id):
    validate_plan(plan, load_accounts())
    prior = ledger.get(plan['plan_id'])
    if prior:
        if prior.get('workflow_run_id') != workflow_run_id:
            raise ApifyError('This exact sweep was already reserved. Resume that workflow; do not purchase it again.')
        return ledger
    if os.environ.get('GITHUB_RUN_ATTEMPT', '1') != '1':
        raise ApifyError('A rerun cannot reserve a new sweep.')
    limits = client.request('GET', '/users/me/limits')['data']
    user = client.request('GET', '/users/me')['data']
    credits = user.get('plan', {}).get('monthlyUsageCreditsUsd')
    used = limits.get('current', {}).get('monthlyUsageUsd')
    ceiling = limits.get('limits', {}).get('maxMonthlyUsageUsd')
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (credits, used, ceiling)):
        raise ApifyError('Available Apify credit could not be verified; no sweep reserved.')
    available = min(Decimal(str(credits)), Decimal(str(ceiling))) - Decimal(str(used))
    if available < Decimal(str(plan['sum_batch_caps_usd'])) + Decimal('0.05'):
        raise ApifyError('Insufficient remaining credit for all batch caps plus the platform-cost reserve.')
    ledger[plan['plan_id']] = {'workflow_run_id': workflow_run_id, 'reserved_at': stamp(),
        'accounts': plan['planned_accounts'], 'requested_posts': plan['requested_posts'],
        'sum_batch_caps_usd': plan['sum_batch_caps_usd'], 'batch_ids': plan['batch_ids']}
    return ledger


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=ROOT / 'work/instagram-sweep')
    args = parser.parse_args()
    plan = json.loads((args.folder / 'plan.json').read_text(encoding='utf-8'))
    path = ROOT / 'data/instagram-sweep-reservations.json'
    ledger = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    try:
        result = reserve(plan, Client(os.environ.get('APIFY_TOKEN')), ledger, os.environ['GITHUB_RUN_ID'])
        write_json(path, result)
        print('Available credit covers the approved plan; reservation prepared. No actor started.')
    except ApifyError as error:
        raise SystemExit(str(error)) from None
