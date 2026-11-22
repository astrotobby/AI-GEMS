#!/usr/bin/env python3
"""Recover missed next-day long-form YouTube slots without duplicating uploads.

This runs as a lightweight hourly GitHub Actions heartbeat. It checks the
committed production ledger and the main publishing workflow's active runs,
then dispatches at most one missing slot. The publishing workflow remains the
single writer for uploads and the shared visual-history registry.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "astrotobby/AI-GEMS")
MAIN_WORKFLOW_ID = os.environ.get("LONGFORM_WORKFLOW_ID", "422299065")
DEFAULT_BRANCH = "main"
API_ROOT = "https://api.github.com"
LEDGER_PATH = Path(
    os.environ.get("LONGFORM_LEDGER_PATH", "whop-clipper/state/longform/ledger.json")
)
EAT = ZoneInfo("Africa/Nairobi")
STALE_JOB_AFTER = timedelta(hours=3)
SLOTS = (
    ("astrotobby", "10:00"),
    ("digital_nomad", "12:00"),
    ("astrotobby", "15:00"),
    ("digital_nomad", "18:00"),
)
DONE_STATUSES = {"scheduled", "published"}
IN_PROGRESS_STATUSES = {"scripted", "rendering", "rendered", "validated"}


def job_id(target_date: date, channel: str, slot: str) -> str:
    return f"{target_date:%Y%m%d}_{channel}_{slot.replace(':', '')}"


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EAT)
    return parsed.astimezone(EAT)


def read_ledger(path: Path = LEDGER_PATH) -> dict:
    if not path.exists():
        return {"jobs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Long-form ledger cannot be read safely: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs", {}), dict):
        raise RuntimeError("Long-form ledger has an invalid structure; refusing automatic dispatch")
    return data


def select_next_slot(
    ledger: dict,
    target_date: date,
    now: datetime | None = None,
) -> dict:
    """Return dispatch/wait/blocked/complete for the first unfilled target slot."""
    current = (now or datetime.now(EAT)).astimezone(EAT)
    jobs = ledger.get("jobs") or {}

    for channel, slot in SLOTS:
        key = job_id(target_date, channel, slot)
        job = jobs.get(key) or {}
        status = str(job.get("status") or "").strip().lower()

        if status in DONE_STATUSES:
            continue

        # An interrupted upload is intentionally not auto-retried. It needs a
        # manual YouTube check so the recovery service can never duplicate it.
        if status == "uploading":
            return {
                "action": "blocked",
                "channel": channel,
                "slot": slot,
                "job_id": key,
                "reason": "uploading state is ambiguous; inspect the private YouTube upload before retrying",
            }

        # A recent non-terminal ledger record may belong to a run that has not
        # appeared in the Actions run list yet, or has just failed. Allow three
        # hours for the normal renderer; stale records can then be retried.
        if status in IN_PROGRESS_STATUSES:
            updated = parse_timestamp(job.get("updated_at") or job.get("created_at"))
            if updated is None or current - updated < STALE_JOB_AFTER:
                return {
                    "action": "wait",
                    "channel": channel,
                    "slot": slot,
                    "job_id": key,
                    "reason": f"ledger status is {status}; waiting for the active render window",
                }

        return {
            "action": "dispatch",
            "channel": channel,
            "slot": slot,
            "job_id": key,
        }

    return {"action": "complete", "reason": "all four next-day slots are already scheduled"}


def api_request(method: str, path: str, token: str, payload: dict | None = None) -> dict | None:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        API_ROOT + path,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        # Do not print request headers or tokens.
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"GitHub API {method} failed with HTTP {exc.code}: {detail}") from None
    except Exception as exc:
        raise RuntimeError(f"GitHub API {method} failed: {type(exc).__name__}") from exc


def active_main_runs(token: str) -> list[dict]:
    path = f"/repos/{REPOSITORY}/actions/workflows/{MAIN_WORKFLOW_ID}/runs?per_page=100"
    response = api_request("GET", path, token) or {}
    runs = response.get("workflow_runs") or []
    active_statuses = {"queued", "in_progress", "waiting", "requested", "pending"}
    return [run for run in runs if run.get("status") in active_statuses]


def write_summary(message: str) -> None:
    print(message)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"## Long-form schedule recovery\n\n{message}\n")


def main() -> int:
    if os.environ.get("LONGFORM_LIVE_ENABLED", "").strip().lower() != "true":
        write_summary("No action: LONGFORM_LIVE_ENABLED is not true.")
        return 0
    if os.environ.get("GLOBAL_VISUAL_GATES_APPROVED", "").strip().lower() != "true":
        write_summary("No action: GLOBAL_VISUAL_GATES_APPROVED is not true.")
        return 0

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GH_TOKEN is required for the recovery workflow")

    active = active_main_runs(token)
    if active:
        ids = ", ".join(str(run.get("id")) for run in active[:5])
        write_summary(f"No action: main long-form workflow already has an active or queued run ({ids}).")
        return 0

    target_date = datetime.now(EAT).date() + timedelta(days=1)
    decision = select_next_slot(read_ledger(), target_date)

    if decision["action"] == "complete":
        write_summary(f"No action for {target_date}: {decision['reason']}.")
        return 0
    if decision["action"] == "wait":
        write_summary(
            f"Waiting on {decision['channel']} {decision['slot']} for {target_date}: "
            f"{decision['reason']}."
        )
        return 0
    if decision["action"] == "blocked":
        write_summary(
            f"Blocked {decision['channel']} {decision['slot']} for {target_date}: "
            f"{decision['reason']}. No upload was retried."
        )
        return 0

    dispatch_path = f"/repos/{REPOSITORY}/actions/workflows/{MAIN_WORKFLOW_ID}/dispatches"
    api_request(
        "POST",
        dispatch_path,
        token,
        {
            "ref": DEFAULT_BRANCH,
            "inputs": {
                "channel": decision["channel"],
                "slot": decision["slot"],
                "target_date": target_date.isoformat(),
                "dry_run": "false",
            },
        },
    )
    write_summary(
        f"Dispatched missing slot: {decision['channel']} at {decision['slot']} EAT "
        f"for {target_date} (job {decision['job_id']})."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        write_summary(f"Recovery failed closed: {type(exc).__name__}: {str(exc)[:500]}")
        raise SystemExit(1)
