"""Manual, resumable Instagram pilot. One paid start, <=50 posts, <=$0.25/run.

APIFY_TOKEN is read from the environment and sent only in an Authorization
header to api.apify.com. Failed/ambiguous starts are never automatically retried.
The saved journal and normalized posts let OCR/review be repeated without buying
the same posts again. This module is deliberately absent from the nightly job.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

import requests

try:
    from scripts.social_posts import canonical_account_url, canonical_post_url, _timestamp
except ImportError:
    from social_posts import canonical_account_url, canonical_post_url, _timestamp

ROOT = Path(__file__).resolve().parents[1]
ACTOR = "apify~instagram-post-scraper"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
IDENTIFIER = re.compile(r"[A-Za-z0-9]{1,100}\Z")


class ApifyError(RuntimeError):
    pass


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def account_map(societies):
    result = {}
    for society in societies.get("societies", []):
        references = society.get("social_accounts", []) + [{"url": society.get("instagram_url")}]
        for reference in references:
            url = canonical_account_url(reference.get("url"))
            if url and urlparse(url).hostname == "www.instagram.com":
                result[url] = society.get("name", "Durham society")
    return result


def load_accounts(root=ROOT):
    try:
        from scripts.social_registry import apply_registry
    except ImportError:
        from social_registry import apply_registry
    societies = json.loads((root / 'dist/data/societies.json').read_text(encoding='utf-8'))
    registry = json.loads((root / 'data/society-accounts.json').read_text(encoding='utf-8'))
    societies['societies'], _ = apply_registry(societies['societies'], registry, datetime.now(timezone.utc))
    return account_map(societies)


def validate_config(config, accounts):
    usernames = config.get("usernames")
    count, cap = config.get("posts_per_account"), config.get("max_charge_usd")
    if not isinstance(usernames, list) or not usernames or len(usernames) > 50:
        raise ApifyError("Choose between one and fifty verified usernames.")
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.]{1,64}", name) for name in usernames):
        raise ApifyError("Only Instagram usernames are accepted, never post URLs.")
    usernames = [name.lower() for name in usernames]
    if len(set(usernames)) != len(usernames):
        raise ApifyError("Duplicate usernames would waste the pilot allowance.")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 50 or len(usernames) * count > 50:
        raise ApifyError("This pilot is limited to fifty requested posts in total.")
    if isinstance(cap, bool) or not isinstance(cap, (float, int)) or not math.isfinite(cap) or not 0 < cap <= 0.25:
        raise ApifyError("This pilot requires a maximum charge between $0 and $0.25.")
    selected = {f"https://www.instagram.com/{name}/": accounts.get(f"https://www.instagram.com/{name}/") for name in usernames}
    if not all(selected.values()):
        raise ApifyError("Every selected account must exist in the verified society registry.")
    detail = config.get("data_detail_level", "basicData")
    if detail not in {"basicData", "detailedData"}:
        raise ApifyError("Choose basicData or detailedData explicitly.")
    body = {"username": usernames, "resultsLimit": count, "dataDetailLevel": detail,
            "includeMusicData": False, "skipPinnedPosts": True}
    cutoff = config.get("only_posts_newer_than")
    if cutoff:
        if not isinstance(cutoff, str) or not _timestamp(cutoff):
            raise ApifyError("Use an absolute ISO timestamp with UTC offset for the cutoff.")
        body["onlyPostsNewerThan"] = _timestamp(cutoff)
    return body, selected


class Client:
    def __init__(self, token, session=None):
        if not token or not token.strip():
            raise ApifyError("APIFY_TOKEN is missing; add the repository Actions secret.")
        self.token = token.strip()
        self.session = session or requests.Session()

    def request(self, method, path, *, params=None, body=None):
        if not path.startswith("/") or "?" in path or ".." in path:
            raise ApifyError("Invalid Apify API path.")
        # requests does not retry by default. In particular NEVER retry POST.
        try:
            response = self.session.request(method, "https://api.apify.com/v2" + path,
                params=params, json=body, headers={"Authorization": "Bearer " + self.token},
                timeout=(15, 55), allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise ApifyError(f"Apify request failed (HTTP {response.status_code}); response omitted to protect credentials.")
            result = response.json()
            # Reject unexpected reflection rather than persisting token-bearing data.
            if self.token in json.dumps(result):
                raise ApifyError("Apify response unexpectedly contained a credential; it was discarded.")
            return result
        except ApifyError:
            raise
        except (requests.RequestException, ValueError):
            raise ApifyError("Apify request did not return a confirmed response. A start may have succeeded; inspect the saved journal and Apify console before starting again.") from None

    def start(self, body, cap):
        return self.request("POST", f"/actors/{ACTOR}/runs", body=body,
            params={"maxTotalChargeUsd": cap, "restartOnError": "false", "timeout": 600})["data"]

    def run(self, run_id):
        if not IDENTIFIER.fullmatch(run_id):
            raise ApifyError("Invalid Apify run ID.")
        return self.request("GET", f"/actor-runs/{run_id}")["data"]

    def items(self, dataset_id):
        if not IDENTIFIER.fullmatch(dataset_id):
            raise ApifyError("Invalid Apify dataset ID.")
        return self.request("GET", f"/datasets/{dataset_id}/items",
            params={"format": "json", "clean": "true", "limit": 50})


def safe_run(run):
    keys = ("id", "actId", "status", "startedAt", "finishedAt", "defaultDatasetId", "usageTotalUsd", "chargedEventCounts")
    value = {key: run.get(key) for key in keys}
    value["max_charge_usd"] = (run.get("options") or {}).get("maxTotalChargeUsd")
    return value


def normalize_items(items, selected, observed_at, run_id):
    posts, rejected = {}, 0
    for item in items[:50]:
        if not isinstance(item, dict) or item.get("error"):
            rejected += 1
            continue
        owner = item.get("ownerUsername")
        account = canonical_account_url("https://www.instagram.com/" + owner + "/") if isinstance(owner, str) else canonical_account_url(item.get("inputUrl"))
        source = canonical_post_url(item.get("url"))
        if not source and isinstance(item.get("shortCode"), str):
            source = canonical_post_url("https://www.instagram.com/p/" + item["shortCode"] + "/")
        if account not in selected or not source or not source.startswith("https://www.instagram.com/"):
            rejected += 1
            continue
        caption = item.get("caption") or ""
        if not isinstance(caption, str):
            rejected += 1
            continue
        media = []
        # Prefer every carousel panel; displayUrl is also a useful video cover.
        children = item.get("childPosts") if isinstance(item.get("childPosts"), list) else []
        urls = list(item.get("images") or []) if isinstance(item.get("images"), list) else []
        urls += [child.get("displayUrl") for child in children if isinstance(child, dict)]
        urls = [url for url in urls if isinstance(url, str) and url.startswith("https://")]
        has_panels = bool(urls)
        if not urls:
            urls = [item.get("displayUrl")]
        for url in urls:
            if isinstance(url, str) and url.startswith("https://") and url not in media:
                media.append(url)
        posts[source] = {"id": "social-" + hashlib.sha256(source.encode()).hexdigest()[:18],
            "platform": "instagram", "account_url": account, "society": selected[account],
            "source_url": source, "text": caption[:12000], "published_at": _timestamp(item.get("timestamp")),
            "observed_at": observed_at, "media_urls": media, "origin": "apify", "apify_run_id": run_id,
            "media_type": str(item.get("type") or "unknown")[:30],
            "media_coverage": "carousel_panels" if has_panels else "cover_only"}
    return list(posts.values()), rejected


def collect(client, config, accounts, output, *, resume_run_id=None, sleep=time.sleep):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    lock = output / "collection.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ApifyError("This output directory has an active or interrupted collection lock. Reconcile its saved run before retrying.") from None
    try:
        with handle:
            handle.write(stamp())
        return _collect_locked(client, config, accounts, output, resume_run_id=resume_run_id, sleep=sleep)
    finally:
        lock.unlink(missing_ok=True)


def cached_items(items):
    """Keep only source fields needed for reprocessing, excluding comments/users."""
    fields = {"id", "url", "inputUrl", "shortCode", "ownerUsername", "caption", "timestamp", "displayUrl", "images", "type"}
    result = []
    for item in items[:50]:
        if not isinstance(item, dict):
            result.append({"invalid": True})
            continue
        record = {key: item[key] for key in fields if key in item}
        if item.get("error"):
            record["error"] = "Provider returned an error for this input."
        record["childPosts"] = [{key: child[key] for key in ("id", "displayUrl", "type") if key in child}
            for child in item.get("childPosts", []) if isinstance(child, dict)] if isinstance(item.get("childPosts"), list) else []
        result.append(record)
    return result


def _collect_locked(client, config, accounts, output, *, resume_run_id=None, sleep=time.sleep):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    journal_path, posts_path = output / "run.json", output / "posts.json"
    body, selected = validate_config(config, accounts)
    journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else None
    if journal and journal.get("input") != body:
        raise ApifyError("Saved run uses a different input. Resume it with its original configuration.")
    if journal and posts_path.exists():
        return json.loads(posts_path.read_text(encoding="utf-8")), journal
    if journal:
        resume_run_id = journal.get("run", {}).get("id") or resume_run_id
        if not resume_run_id:
            raise ApifyError("An unconfirmed start is already journaled. Reconcile it in Apify and supply --resume-run-id; do not start another run.")
    if resume_run_id:
        run = client.run(resume_run_id)
        # Read the stored INPUT to ensure this is this pilot, not an unrelated run.
        store_id = run.get("defaultKeyValueStoreId", "")
        if not IDENTIFIER.fullmatch(store_id):
            raise ApifyError("The existing run has no readable input store.")
        stored_input = client.request("GET", f"/key-value-stores/{store_id}/records/INPUT")
        if any(stored_input.get(key) != value for key, value in body.items()):
            raise ApifyError("Existing run input does not match this pilot configuration.")
        journal = {"created_at": stamp(), "input": body, "requested_posts": len(body["username"]) * body["resultsLimit"], "phase": "resumed"}
    else:
        if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
            raise ApifyError("Workflow reruns cannot launch another paid collection. Dispatch with the existing Apify run ID to resume.")
        journal = {"created_at": stamp(), "input": body, "requested_posts": len(body["username"]) * body["resultsLimit"],
                   "max_charge_usd": config["max_charge_usd"], "phase": "start_unconfirmed"}
        write_json(journal_path, journal)  # durable intent before the only paid POST
        run = client.start(body, config["max_charge_usd"])
    journal.update(phase="started", run=safe_run(run))
    write_json(journal_path, journal)
    actual_cap = (run.get("options") or {}).get("maxTotalChargeUsd")
    if not isinstance(actual_cap, (int, float)) or actual_cap > config["max_charge_usd"]:
        run_id = run.get("id", "")
        if IDENTIFIER.fullmatch(run_id) and run.get("status") not in TERMINAL:
            client.request("POST", f"/actor-runs/{run_id}/abort")
        raise ApifyError("Apify did not confirm the requested charge cap; collection was stopped.")
    for _ in range(130):
        if run.get("status") in TERMINAL:
            break
        sleep(5)
        run = client.run(run["id"])
        journal.update(run=safe_run(run))
        write_json(journal_path, journal)
    else:
        raise ApifyError("Monitoring ended. Resume the saved run ID; no replacement run was started.")
    sleep(10)  # final billed-event accounting can lag terminal status
    run = client.run(run["id"])
    journal.update(phase="finished", run=safe_run(run))
    write_json(journal_path, journal)
    dataset_path = output / "dataset-source-fields.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8")) if dataset_path.exists() else client.items(run["defaultDatasetId"])
    if not isinstance(dataset, list):
        raise ApifyError("Dataset did not contain a JSON array.")
    write_json(dataset_path, cached_items(dataset))
    observed_at = stamp()
    posts, rejected = normalize_items(dataset, selected, observed_at, run["id"])
    payload = {"updated_at": observed_at, "posts": posts, "accounts": [
        {"url": account, "platform": "instagram", "society": society, "provider": "apify",
         "status": "ok" if any(p["account_url"] == account for p in posts) and run["status"] == "SUCCEEDED" else "partial",
         "posts_collected": sum(p["account_url"] == account for p in posts), "last_attempt_at": observed_at,
         "last_success_at": observed_at if any(p["account_url"] == account for p in posts) else None, "window_limited": True}
        for account, society in selected.items()]}
    journal.update(phase="dataset_saved", returned_items=len(dataset), accepted_posts=len(posts), rejected_items=rejected,
                   note="Actor run is capped. Post-run dataset access has separate platform costs. No paid image/model service is used.")
    write_json(posts_path, payload)
    write_json(journal_path, journal)
    return payload, journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "data/apify-pilot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "work/apify-pilot")
    parser.add_argument("--resume-run-id")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        payload, report = collect(Client(os.environ.get("APIFY_TOKEN")), config, load_accounts(), args.output, resume_run_id=args.resume_run_id)
        print(json.dumps({"posts": len(payload["posts"]), "run": report["run"]}, indent=2))
        return 0
    except (ApifyError, KeyError, ValueError) as error:
        print(str(error) if isinstance(error, ApifyError) else "Pilot input/response validation failed; inspect the saved journal.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
