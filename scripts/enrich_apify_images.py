"""Recover carousel panels for an existing pilot within its remaining 25-cent budget.

Only exact post URLs already returned by the original run can be requested. The
supplement has one durable paid-start intent, an exclusive lock, and a separate
server-enforced cap. Restored artifacts and run IDs resume without buying again.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import json
import os
from pathlib import Path
import sys
import time

try:
    from scripts.apify_posts import (ROOT, IDENTIFIER, TERMINAL, ApifyError, Client,
        load_accounts, cached_items, normalize_items, safe_run, stamp, write_json)
    from scripts.social_posts import canonical_account_url, canonical_post_url
except ImportError:
    from apify_posts import (ROOT, IDENTIFIER, TERMINAL, ApifyError, Client,
        load_accounts, cached_items, normalize_items, safe_run, stamp, write_json)
    from social_posts import canonical_account_url, canonical_post_url


def money(value):
    if isinstance(value, bool) or value is None:
        raise ApifyError("A confirmed finite Apify charge is required.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ApifyError("A confirmed finite Apify charge is required.") from None
    if not amount.is_finite() or amount < 0:
        raise ApifyError("A confirmed finite Apify charge is required.")
    return amount


def remaining_cap(original_charge, authorized_cap):
    ceiling = money(authorized_cap)
    if not Decimal("0") < ceiling <= Decimal("0.25"):
        raise ApifyError("The original pilot must have a confirmed cap at or below $0.25.")
    # Reserve half a cent for post-run storage reads; never round a cap upwards.
    result = min(Decimal("0.16"), ceiling - money(original_charge) - Decimal("0.005"))
    result = result.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if result <= 0:
        raise ApifyError("No authorized budget remains for carousel recovery.")
    return result


def source_url(item):
    source = canonical_post_url(item.get("url"))
    if not source and isinstance(item.get("shortCode"), str):
        source = canonical_post_url("https://www.instagram.com/p/" + item["shortCode"] + "/")
    return source if source and source.startswith("https://www.instagram.com/") else None


def choose_posts(dataset, accounts):
    if not isinstance(dataset, list) or not 0 < len(dataset) <= 50:
        raise ApifyError("Recovery requires the original cache of at most fifty posts.")
    urls = []
    for item in dataset:
        if not isinstance(item, dict) or item.get("error") or item.get("type") != "Sidecar":
            continue
        owner = item.get("ownerUsername")
        account = canonical_account_url("https://www.instagram.com/" + owner + "/") if isinstance(owner, str) else None
        source = source_url(item)
        if account in accounts and source and source not in urls:
            urls.append(source)
    return urls


def read_input(client, run):
    store = run.get("defaultKeyValueStoreId", "")
    if not isinstance(store, str) or not IDENTIFIER.fullmatch(store):
        raise ApifyError("Run has no valid input store; recovery stopped.")
    result = client.request("GET", f"/key-value-stores/{store}/records/INPUT")
    if not isinstance(result, dict):
        raise ApifyError("Run input could not be verified.")
    return result


def matches_input(actual, expected):
    return isinstance(expected, dict) and all(actual.get(key) == value for key, value in expected.items())


def merge_images(original, details, allowed):
    """Supplement media only: never replace source ownership, caption, or date."""
    indexed = {}
    for item in details:
        if isinstance(item, dict) and not item.get("error") and source_url(item) in allowed:
            indexed[source_url(item)] = item
    result, recovered = [], 0
    for record in original:
        item = dict(record)
        detail = indexed.get(source_url(item))
        if detail:
            filtered = cached_items([detail])[0]
            images = filtered.get("images")
            children = filtered.get("childPosts")
            if isinstance(images, list) and any(isinstance(url, str) and url.startswith("https://") for url in images):
                item["images"] = images
            if isinstance(children, list) and any(isinstance(child.get("displayUrl"), str) and child["displayUrl"].startswith("https://") for child in children):
                item["childPosts"] = children
            if isinstance(filtered.get("displayUrl"), str) and filtered["displayUrl"].startswith("https://"):
                item["displayUrl"] = filtered["displayUrl"]
            if item.get("images") or item.get("childPosts"):
                recovered += 1
        result.append(item)
    return result, recovered


def enrich(client, output, accounts, *, resume_run_id=None, sleep=time.sleep):
    output = Path(output)
    lock = output / "image-enrichment.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ApifyError("Image enrichment is active or interrupted; reconcile its journal before retrying.") from None
    try:
        with handle:
            handle.write(stamp())
        return _enrich_locked(client, output, accounts, resume_run_id=resume_run_id, sleep=sleep)
    finally:
        lock.unlink(missing_ok=True)


def _enrich_locked(client, output, accounts, *, resume_run_id=None, sleep=time.sleep):
    report_path, dataset_path = output / "run.json", output / "dataset-source-fields.json"
    journal_path = output / "image-enrichment-run.json"
    details_path = output / "image-enrichment-source-fields.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    original = json.loads(dataset_path.read_text(encoding="utf-8"))
    journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else None
    if journal and journal.get("phase") == "dataset_saved" and report.get("image_enrichment") == journal:
        return json.loads((output / "posts.json").read_text(encoding="utf-8")), report

    receipt = report.get("run") or {}
    original_id = receipt.get("id")
    if not isinstance(original_id, str) or not IDENTIFIER.fullmatch(original_id):
        raise ApifyError("Original run receipt is missing.")
    # A fresh workflow may restore an older artifact. The committed report is a
    # second durable receipt: resume its supplementary run instead of rebuying.
    tracked_path = ROOT / "data/apify-pilot-report.json"
    if tracked_path.exists():
        tracked = json.loads(tracked_path.read_text(encoding="utf-8")).get("collection", {})
        saved_enrichment = tracked.get("image_enrichment") or tracked.get("enrichment")
        if (tracked.get("run") or {}).get("id") == original_id and saved_enrichment:
            tracked_id = (saved_enrichment.get("run") or {}).get("id")
            local_id = (journal.get("run") or {}).get("id") if journal else None
            if local_id and tracked_id != local_id:
                raise ApifyError("Local and committed enrichment runs differ; reconcile before continuing.")
            if not tracked_id:
                raise ApifyError("Committed enrichment has an unconfirmed start; reconcile its run before continuing.")
            journal = journal or saved_enrichment
            resume_run_id = tracked_id
    primary = client.run(original_id)
    if primary.get("status") != "SUCCEEDED" or any(primary.get(key) != receipt.get(key) for key in ("id", "actId", "defaultDatasetId")):
        raise ApifyError("Original run does not match the successful saved pilot.")
    if not matches_input(read_input(client, primary), report.get("input")):
        raise ApifyError("Original run input does not match the saved pilot.")
    original_cost = money(primary.get("usageTotalUsd"))
    authorized_cap = money(receipt.get("max_charge_usd"))
    if money((primary.get("options") or {}).get("maxTotalChargeUsd")) != authorized_cap:
        raise ApifyError("Original run cap does not match its receipt.")
    cap = remaining_cap(original_cost, authorized_cap)
    urls = choose_posts(original, accounts)
    if not urls:
        raise ApifyError("No verified carousel posts in the original batch need recovery.")
    body = {"username": urls, "dataDetailLevel": "detailedData", "includeMusicData": False}
    if journal and (journal.get("input") != body or journal.get("original_run_id") != original_id):
        raise ApifyError("Saved enrichment input differs; resume the original artifact.")
    if journal:
        resume_run_id = (journal.get("run") or {}).get("id") or resume_run_id
        if not resume_run_id:
            raise ApifyError("An unconfirmed enrichment start exists. Reconcile it and supply --resume-run-id; never start again.")
        # Never expand the original supplementary authorization when resuming.
        cap = min(cap, money(journal.get("max_charge_usd")))
    if resume_run_id:
        run = client.run(resume_run_id)
        if run.get("actId") != primary.get("actId") or not matches_input(read_input(client, run), body):
            raise ApifyError("Supplementary run does not match the carousel recovery input.")
        journal = journal or {"created_at": stamp(), "original_run_id": original_id, "input": body, "max_charge_usd": float(cap)}
    else:
        if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
            raise ApifyError("Workflow reruns cannot launch paid image recovery; resume the existing run.")
        journal = {"created_at": stamp(), "original_run_id": original_id, "input": body,
            "max_charge_usd": float(cap), "original_charge_usd": float(original_cost), "phase": "start_unconfirmed"}
        write_json(journal_path, journal)
        run = client.start(body, float(cap))
    journal.update(phase="started", run=safe_run(run))
    write_json(journal_path, journal)
    try:
        confirmed_cap = money((run.get("options") or {}).get("maxTotalChargeUsd"))
        if not Decimal("0") < confirmed_cap <= cap:
            raise ApifyError("Supplementary server cap exceeds the remaining pilot budget.")
    except ApifyError:
        if isinstance(run.get("id"), str) and IDENTIFIER.fullmatch(run["id"]) and run.get("status") not in TERMINAL:
            client.request("POST", f"/actor-runs/{run['id']}/abort")
        raise
    for _ in range(130):
        if run.get("status") in TERMINAL:
            break
        sleep(5)
        run = client.run(run["id"])
        journal["run"] = safe_run(run)
        write_json(journal_path, journal)
    else:
        raise ApifyError("Image recovery monitoring ended; resume its saved run ID.")
    sleep(10)
    run = client.run(run["id"])
    combined = original_cost + money(run.get("usageTotalUsd"))
    journal.update(phase="finished", run=safe_run(run), combined_charge_usd=float(combined))
    write_json(journal_path, journal)
    if combined > authorized_cap:
        raise ApifyError("Reported combined charge exceeds the pilot cap; no further calls were started.")
    details = json.loads(details_path.read_text(encoding="utf-8")) if details_path.exists() else client.items(run["defaultDatasetId"])
    if not isinstance(details, list) or len(details) > 50:
        raise ApifyError("Supplementary dataset is invalid.")
    details = cached_items(details)
    write_json(details_path, details)
    merged, recovered = merge_images(original, details, set(urls))
    observed = stamp()
    posts, rejected = normalize_items(merged, accounts, observed, original_id)
    account_urls = sorted({post["account_url"] for post in posts})
    payload = {"updated_at": observed, "posts": posts, "accounts": [
        {"url": url, "platform": "instagram", "society": accounts[url], "provider": "apify",
         "status": "ok", "posts_collected": sum(post["account_url"] == url for post in posts),
         "last_attempt_at": observed, "last_success_at": observed, "window_limited": True}
        for url in account_urls]}
    journal.update(phase="dataset_saved", requested_posts=len(urls), returned_items=len(details),
        recovered_carousels=recovered, note="Only cached post URLs were enriched. Caption, owner and timestamp remain from the original run.")
    report.update(image_enrichment=journal, combined_charge_usd=float(combined), accepted_posts=len(posts), rejected_items=rejected)
    write_json(dataset_path, merged)
    write_json(output / "posts.json", payload)
    write_json(journal_path, journal)
    write_json(report_path, report)
    return payload, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/apify-pilot")
    parser.add_argument("--resume-run-id")
    args = parser.parse_args()
    try:
        posts, report = enrich(Client(os.environ.get("APIFY_TOKEN")), args.output, load_accounts(), resume_run_id=args.resume_run_id)
        print(json.dumps({"posts": len(posts["posts"]), "image_enrichment": report["image_enrichment"]}, indent=2))
        return 0
    except (ApifyError, KeyError, ValueError, OSError) as error:
        print(str(error) if isinstance(error, ApifyError) else "Image recovery validation failed; inspect the saved journal.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
