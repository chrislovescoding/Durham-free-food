from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.apify_posts import ApifyError
from scripts.plan_instagram_sweep import (
    CUTOFF, PRIORITY_USERNAMES, _identity, create_plan, validate_plan, write_plan,
)


NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)


def accounts(count=290):
    names = list(PRIORITY_USERNAMES) + [f"society_{number:03d}" for number in range(count - len(PRIORITY_USERNAMES))]
    return {f"https://www.instagram.com/{name}/": f"Society {name}" for name in names}


class SweepPlanTests(unittest.TestCase):
    def test_full_coverage_five_posts_and_budget(self):
        registry = accounts()
        plan = create_plan(registry, NOW)
        self.assertEqual(plan["planned_accounts"], 290)
        self.assertEqual(plan["requested_posts"], 1450)
        self.assertEqual(plan["batch_count"], 29)
        self.assertEqual(plan["sum_batch_caps_usd"], 4.06)
        self.assertEqual(plan["max_charge_usd"], 4.10)
        self.assertEqual(plan["batches"][0]["usernames"], list(PRIORITY_USERNAMES))
        for batch in plan["batches"]:
            self.assertEqual(batch["requested_posts"], 50)
            self.assertEqual(batch["config"]["only_posts_newer_than"], CUTOFF)
            self.assertEqual(batch["config"]["data_detail_level"], "detailedData")
            self.assertEqual(batch["config"]["max_charge_usd"], 0.14)
        validate_plan(plan, registry)

    def test_documented_bad_mapping_excluded_but_real_society_retained(self):
        registry = accounts(288)
        registry["https://www.instagram.com/d/"] = "Dance Troupe"
        registry["https://www.instagram.com/du_troupe/"] = "Dance Troupe"
        plan = create_plan(registry, NOW)
        self.assertEqual(plan["registry_account_count"], 290)
        self.assertEqual(plan["planned_accounts"], 289)
        self.assertEqual(plan["requested_posts"], 1445)
        self.assertEqual(plan["batches"][-1]["requested_posts"], 45)
        self.assertNotIn("https://www.instagram.com/d/", plan["account_urls"])
        self.assertIn("https://www.instagram.com/du_troupe/", plan["account_urls"])
        self.assertIn("@du_troupe", plan["excluded_accounts"][0]["reason"])

    def test_plan_identity_stable_across_time_and_registry_order(self):
        registry = accounts()
        first = create_plan(registry, NOW)
        second = create_plan(dict(reversed(list(registry.items()))), "2026-10-17T18:00:00+01:00")
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["batches"], second["batches"])
        self.assertNotEqual(first["generated_at"], second["generated_at"])

    def test_excess_accounts_fail_before_any_collection(self):
        with self.assertRaisesRegex(ApifyError, "overall cap"):
            create_plan(accounts(291), NOW)

    def test_empty_unknown_or_noncanonical_inputs_fail(self):
        for registry in ({}, {"https://instagram.com/example/": "Example"},
                         {"https://www.instagram.com/p/example/": "Example"},
                         {"https://www.instagram.com/Example/": "Example"}):
            with self.subTest(registry=registry), self.assertRaises(ApifyError):
                create_plan(registry, NOW)
        with self.assertRaises(ApifyError):
            create_plan(accounts(10), NOW.replace(tzinfo=None))
        with self.assertRaises(ApifyError):
            create_plan(accounts(10), NOW, cutoff="2027-01-01T00:00:00Z")

    def test_tampering_caps_cutoff_or_duplicate_accounts_fails_validation(self):
        registry = accounts(20)
        changes = [
            lambda plan: plan["batches"][0]["config"].update(max_charge_usd=0.25),
            lambda plan: plan["batches"][0]["config"].update(only_posts_newer_than=None),
            lambda plan: plan["batches"][0].update(config_path="../outside/config.json"),
            lambda plan: plan.update(max_charge_usd=5),
            lambda plan: plan["batches"][1]["config"].update(usernames=plan["batches"][0]["usernames"]),
            lambda plan: plan.update(requested_posts=1),
        ]
        for change in changes:
            plan = deepcopy(create_plan(registry, NOW))
            change(plan)
            # Even recomputing the checksum cannot bypass the policy validation.
            plan["plan_id"] = _identity(plan)
            with self.subTest(change=change), self.assertRaises(ApifyError):
                validate_plan(plan, registry)

    def test_write_is_reusable_and_does_not_replace_different_sweep(self):
        plan = create_plan(accounts(12), NOW)
        with tempfile.TemporaryDirectory() as temp:
            first = write_plan(plan, temp)
            repeated = write_plan(create_plan(accounts(12), "2026-10-17T17:00:00Z"), temp)
            self.assertEqual(first, repeated)
            manifest = json.loads((Path(temp) / "plan.json").read_text())
            self.assertEqual(manifest, first)
            for batch in plan["batches"]:
                self.assertEqual(json.loads((Path(temp) / batch["config_path"]).read_text()), batch["config"])
            with self.assertRaisesRegex(ApifyError, "different sweep"):
                write_plan(create_plan(accounts(11), NOW), temp)

    def test_saved_manifest_cannot_change_paths_under_old_identity(self):
        plan = create_plan(accounts(10), NOW)
        with tempfile.TemporaryDirectory() as temp:
            write_plan(plan, temp)
            path = Path(temp) / "plan.json"
            manifest = json.loads(path.read_text())
            manifest["batches"][0]["config_path"] = "../outside/config.json"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ApifyError, "different sweep"):
                write_plan(plan, temp)


if __name__ == "__main__":
    unittest.main()
