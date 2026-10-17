"""Plan the approved Instagram sweep without making any network or paid calls.

Every batch stays within the existing collector's fifty-post limit and has its
own Apify server-side cap. The sum of those caps is checked against the overall
allowance before any collection can begin.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts.apify_posts import ApifyError, load_accounts, validate_config, write_json
except ImportError:
    from apify_posts import ApifyError, load_accounts, validate_config, write_json


CUTOFF = "2026-09-16T23:00:00Z"
FULL_SWEEP_CAP_USD = Decimal("4.10")
BATCH_CAP_USD = Decimal("0.14")
POSTS_PER_ACCOUNT = 5
ACCOUNTS_PER_BATCH = 10
PRIORITY_USERNAMES = (
    "ducompsoc", "duisoc", "ais_durham", "dubridgeclub", "durham_cu",
    "durhamvegsoc", "durhamccf", "dmscf.du", "du.abacus",
    "du_healthandwellbeing_soc",
)
DEFAULT_EXCLUSIONS = {
    "https://www.instagram.com/d/": {
        "reason": "Spurious truncated handle from discovery. The society's official SU page names @du_troupe, which remains included.",
        "evidence_url": "https://www.durhamsu.com/groups/studentgroup/6345/",
    },
}


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ApifyError("The plan needs an ISO timestamp with a timezone.") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ApifyError("The plan needs a timezone-aware timestamp.")
    return value.astimezone(timezone.utc)


def _money(value):
    if isinstance(value, bool):
        raise ApifyError("Invalid plan charge limit.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ApifyError("Invalid plan charge limit.") from None
    if not result.is_finite() or result <= 0:
        raise ApifyError("Invalid plan charge limit.")
    return result


def _identity(plan):
    # Timestamp is excluded so rerunning the planner cannot create a new paid
    # run identity for exactly the same accounts and collection settings.
    payload = {key: value for key, value in plan.items() if key not in {"generated_at", "plan_id"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def create_plan(accounts, now, *, cutoff=CUTOFF, excluded_accounts=None):
    """Return deterministic exact account coverage and capped collector configs.

    ``accounts`` is the verified URL-to-society map from ``load_accounts``.
    Exclusions are URL-to-{reason,evidence_url} mappings and are recorded in the
    manifest. Passing an empty mapping explicitly includes every valid account.
    """
    now, cutoff_time = _utc(now), _utc(cutoff)
    if cutoff_time >= now:
        raise ApifyError("The publication cutoff must precede the plan time.")
    cutoff = cutoff_time.isoformat().replace("+00:00", "Z")
    exclusions = DEFAULT_EXCLUSIONS if excluded_accounts is None else excluded_accounts
    if not isinstance(accounts, dict) or not accounts:
        raise ApifyError("No verified Instagram accounts are available.")
    if not isinstance(exclusions, dict):
        raise ApifyError("Account exclusions require reasons and evidence URLs.")
    excluded = []
    for url, evidence in sorted(exclusions.items()):
        if url not in accounts:
            continue
        if not isinstance(evidence, dict) or not evidence.get("reason") or not evidence.get("evidence_url"):
            raise ApifyError("Every excluded account requires a reason and evidence URL.")
        excluded.append({"url": url, "society": accounts[url], "reason": evidence["reason"],
                         "evidence_url": evidence["evidence_url"]})
    selected = {url: society for url, society in accounts.items() if url not in exclusions}
    if not selected:
        raise ApifyError("The plan cannot exclude every account.")
    usernames = {}
    for url, society in selected.items():
        if not isinstance(url, str) or not isinstance(society, str) or not society:
            raise ApifyError("Each verified account needs a URL and society name.")
        name = urlparse(url).path.strip("/")
        if url != f"https://www.instagram.com/{name}/" or name != name.lower():
            raise ApifyError("The plan requires canonical Instagram account URLs.")
        # Reuse the paid collector's allowlist and username checks before writing
        # any config so the preflight and paid run cannot disagree about inputs.
        validate_config({"usernames": [name], "posts_per_account": POSTS_PER_ACCOUNT,
                         "max_charge_usd": float(BATCH_CAP_USD)}, accounts)
        usernames[name] = url
    ordered = [name for name in PRIORITY_USERNAMES if name in usernames]
    ordered += sorted(set(usernames) - set(ordered))
    batches = []
    for offset in range(0, len(ordered), ACCOUNTS_PER_BATCH):
        batch_id = f"{len(batches) + 1:02d}"
        names = ordered[offset:offset + ACCOUNTS_PER_BATCH]
        config = {
            "description": f"Approved full Instagram sweep, batch {batch_id}; up to five recent posts per account.",
            "usernames": names,
            "posts_per_account": POSTS_PER_ACCOUNT,
            "data_detail_level": "detailedData",
            "max_charge_usd": float(BATCH_CAP_USD),
            "only_posts_newer_than": cutoff,
        }
        batches.append({"id": batch_id, "config_path": f"batches/{batch_id}/config.json",
                        "usernames": names, "account_count": len(names),
                        "requested_posts": len(names) * POSTS_PER_ACCOUNT,
                        "max_charge_usd": float(BATCH_CAP_USD), "config": config})
    total = len(batches) * BATCH_CAP_USD
    if total > FULL_SWEEP_CAP_USD:
        raise ApifyError("The account list exceeds the approved $4.10 overall cap; no collection was started.")
    plan = {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "only_posts_newer_than": cutoff,
        "posts_per_account": POSTS_PER_ACCOUNT,
        "accounts_per_batch": ACCOUNTS_PER_BATCH,
        "registry_account_count": len(accounts),
        "planned_accounts": len(selected),
        "planned_societies": len(set(selected.values())),
        "account_urls": [usernames[name] for name in ordered],
        "requested_posts": len(selected) * POSTS_PER_ACCOUNT,
        "max_charge_usd": float(FULL_SWEEP_CAP_USD),
        "sum_batch_caps_usd": float(total),
        "batch_count": len(batches),
        "batch_ids": [batch["id"] for batch in batches],
        "excluded_accounts": excluded,
        "batches": batches,
    }
    plan["plan_id"] = _identity(plan)
    validate_plan(plan, accounts)
    return plan


def validate_plan(plan, accounts):
    """Fail closed if a saved plan changes coverage, config, paths, or caps."""
    try:
        if plan["schema_version"] != 1 or plan["plan_id"] != _identity(plan):
            raise ApifyError("The sweep plan identity is invalid.")
        if _money(plan["max_charge_usd"]) != FULL_SWEEP_CAP_USD:
            raise ApifyError("The sweep plan changed the approved overall cap.")
        if plan["posts_per_account"] != POSTS_PER_ACCOUNT or plan["accounts_per_batch"] != ACCOUNTS_PER_BATCH:
            raise ApifyError("The sweep plan changed the approved account/post limits.")
        excluded = {record["url"] for record in plan["excluded_accounts"]}
        if not excluded <= accounts.keys():
            raise ApifyError("The sweep plan excludes unknown accounts.")
        expected = set(accounts) - excluded
        covered, ids = [], []
        total = Decimal("0")
        for index, batch in enumerate(plan["batches"], 1):
            batch_id, config = f"{index:02d}", batch["config"]
            if batch["id"] != batch_id or batch["config_path"] != f"batches/{batch_id}/config.json":
                raise ApifyError("Invalid batch order or config path.")
            body, selected = validate_config(config, accounts)
            if (len(selected) > ACCOUNTS_PER_BATCH or body["resultsLimit"] != POSTS_PER_ACCOUNT
                    or body["dataDetailLevel"] != "detailedData"
                    or body.get("onlyPostsNewerThan") != plan["only_posts_newer_than"]
                    or batch["usernames"] != body["username"]
                    or batch["account_count"] != len(selected)
                    or batch["requested_posts"] != len(selected) * POSTS_PER_ACCOUNT
                    or _money(batch["max_charge_usd"]) != BATCH_CAP_USD
                    or _money(config["max_charge_usd"]) != BATCH_CAP_USD):
                raise ApifyError("A sweep batch differs from the approved settings.")
            covered.extend(selected)
            ids.append(batch_id)
            total += _money(config["max_charge_usd"])
        if not covered or len(covered) != len(set(covered)) or set(covered) != expected:
            raise ApifyError("The sweep must cover every included account exactly once.")
        if (plan["account_urls"] != covered or plan["planned_accounts"] != len(covered)
                or plan["planned_societies"] != len({accounts[url] for url in covered})
                or plan["registry_account_count"] != len(accounts)
                or plan["requested_posts"] != len(covered) * POSTS_PER_ACCOUNT
                or plan["batch_count"] != len(ids) or plan["batch_ids"] != ids
                or _money(plan["sum_batch_caps_usd"]) != total or total > FULL_SWEEP_CAP_USD):
            raise ApifyError("The sweep totals or account coverage are inconsistent.")
    except (KeyError, TypeError, AttributeError):
        raise ApifyError("The sweep manifest is incomplete or malformed.") from None
    return plan


def write_plan(plan, output):
    if plan.get("plan_id") != _identity(plan):
        raise ApifyError("The sweep plan identity is invalid.")
    output = Path(output)
    path = output / "plan.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("plan_id") != plan["plan_id"] or existing.get("plan_id") != _identity(existing):
            raise ApifyError("This output folder already contains a different sweep. Preserve it and use another folder.")
        plan = existing
    for batch in plan["batches"]:
        write_json(output / batch["config_path"], batch["config"])
    write_json(path, plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="work/instagram-sweep")
    parser.add_argument("--cutoff", default=CUTOFF)
    parser.add_argument("--now", help="ISO timestamp; defaults to the current UTC time.")
    args = parser.parse_args()
    try:
        plan = create_plan(load_accounts(), args.now or datetime.now(timezone.utc), cutoff=args.cutoff)
        plan = write_plan(plan, args.output)
    except (ApifyError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps({key: plan[key] for key in ("plan_id", "planned_accounts", "planned_societies", "requested_posts", "batch_count", "sum_batch_caps_usd", "max_charge_usd", "batch_ids")}))


if __name__ == "__main__":
    main()
