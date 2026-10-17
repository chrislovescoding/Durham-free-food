from copy import deepcopy
import os
import unittest
from unittest.mock import patch

from scripts.apify_posts import ApifyError, load_accounts
from scripts.plan_instagram_sweep import create_plan
from scripts.reserve_instagram_sweep import reserve
from datetime import datetime, timezone


class UsageClient:
    def __init__(self, used=0.2724):
        self.used, self.calls = used, []

    def request(self, method, path):
        self.calls.append((method, path))
        if path == '/users/me/limits':
            return {'data': {'current': {'monthlyUsageUsd': self.used},
                             'limits': {'maxMonthlyUsageUsd': 5}}}
        if path == '/users/me':
            return {'data': {'plan': {'monthlyUsageCreditsUsd': 5}}}
        raise AssertionError('Unexpected request')


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.plan = create_plan(load_accounts(), datetime(2026, 10, 17, tzinfo=timezone.utc))
        self.env = patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_sufficient_credit_reserves_once_using_only_gets(self):
        client = UsageClient()
        ledger = reserve(self.plan, client, {}, 'run-one')
        self.assertEqual(ledger[self.plan['plan_id']]['sum_batch_caps_usd'], 4.06)
        self.assertTrue(all(method == 'GET' for method, _ in client.calls))
        calls = deepcopy(client.calls)
        reserve(self.plan, client, ledger, 'run-one')
        self.assertEqual(client.calls, calls)
        with self.assertRaises(ApifyError):
            reserve(self.plan, client, ledger, 'run-two')
        self.assertEqual(client.calls, calls)

    def test_insufficient_or_unknown_credit_does_not_reserve(self):
        for used in (1, None, True):
            ledger = {}
            with self.assertRaises(ApifyError):
                reserve(self.plan, UsageClient(used), ledger, 'run-one')
            self.assertEqual(ledger, {})

    def test_rerun_cannot_create_new_reservation(self):
        client = UsageClient()
        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}):
            with self.assertRaises(ApifyError):
                reserve(self.plan, client, {}, 'run-one')
        self.assertEqual(client.calls, [])
