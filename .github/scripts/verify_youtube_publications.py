#!/usr/bin/env python3
"""Verify due long-form YouTube releases and safely repair overdue private videos.

The ledger is the source of the intended channel and release time. The YouTube
Data API is the source of actual ownership, processing and privacy state.
A video is promoted only when every safety check passes.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "whop-clipper" / "state" / "longform" / "ledger.json"
GRACE = timedelta(minutes=10)
MAX_LATE = timedelta(hours=24)
MAX_JOBS_PER_RUN = 20

CHANNELS = {
    "astrotobby": {
        "title": "Astro Tobby",
        "channel_id": "UCxndvpcFaVhN1bJxgWIjG_A",
    },
    "digital_nomad": {
        "title": "DIJITOL NOMAD",
        "channel_id": "UCE_cklDludc6nkU1_4K6JzA",
    },
}


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def same_instant(left: Any, right: Any) -> bool:
    a, b = parse_utc(left), parse_utc(right)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= 1


def decide_video_action(
    job: dict[str, Any],
    video: dict[str, Any],
    expected_channel_id: str,
    now: datetime,
) -> tuple[str, str]:
    """Return (action, reason): verify_public, promote_public, wait, or manual_review."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    expected_publish = parse_utc(job.get("publish_at"))
    if expected_publish is None:
        return "manual_review", "ledger publish_at is missing or invalid"
    current = now.astimezone(timezone.utc)
    if current < expected_publish + GRACE:
        return "wait", "release time has not passed the ten-minute grace period"
    if current - expected_publish > MAX_LATE:
        return "manual_review", "release is more than 24 hours overdue; automatic promotion is disabled"

    snippet = video.get("snippet") or {}
    status = video.get("status") or {}
    if snippet.get("channelId") != expected_channel_id:
        return "manual_review", "YouTube video channel ID does not match the scheduled channel"

    privacy = str(status.get("privacyStatus") or "").casefold()
    if privacy == "public":
        return "verify_public", "YouTube already reports the video as public"
    if privacy != "private":
        return "manual_review", f"privacyStatus is {privacy or 'missing'}, not private"

    if not same_instant(status.get("publishAt"), job.get("publish_at")):
        return "manual_review", "YouTube publishAt does not exactly match the ledger release time"

    processing = video.get("processingDetails") or {}
    processing_status = str(processing.get("processingStatus") or "").casefold()
    upload_status = str(status.get("uploadStatus") or "").casefold()
    if processing_status in {"failed", "terminated"} or upload_status in {"failed", "rejected", "deleted"}:
        return "manual_review", (
            f"YouTube processing/upload failed (processing={processing_status or 'unknown'}, "
            f"upload={upload_status or 'unknown'})"
        )
    if processing_status != "succeeded" or upload_status != "processed":
        return "wait", (
            f"YouTube processing is not complete "
            f"(processing={processing_status or 'unknown'}, upload={upload_status or 'unknown'})"
        )
    return "promote_public", "channel, publishAt and completed-processing checks all passed"


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("Long-form ledger is missing; refusing publication changes")
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Long-form ledger cannot be read safely ({type(exc).__name__})") from exc
    if not isinstance(ledger, dict) or not isinstance(ledger.get("jobs"), dict):
        raise RuntimeError("Long-form ledger has an invalid structure")
    return ledger


def save_ledger(ledger: dict[str, Any], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _youtube_client(channel_key: str):
    sys.path.insert(0, str(ROOT / "whop-clipper"))
    from clipper import publish
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    spec = CHANNELS[channel_key]
    creds = publish._yt_creds({"channel": spec["title"]})
    if not creds:
        raise RuntimeError(f"No restored OAuth credentials for {spec['title']}")
    access = publish._yt_access_token(creds)
    client_id, client_secret, refresh_token = creds
    credentials = Credentials(
        token=access,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube"],
    )
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    channels = youtube.channels().list(part="id,snippet", mine=True).execute(num_retries=2).get("items") or []
    if not any(item.get("id") == spec["channel_id"] for item in channels):
        raise RuntimeError(f"OAuth identity does not match {spec['title']}")
    return youtube


def _list_video(youtube, video_id: str) -> dict[str, Any]:
    rows = youtube.videos().list(
        part="id,snippet,status,processingDetails",
        id=video_id,
    ).execute(num_retries=2).get("items") or []
    if len(rows) != 1 or rows[0].get("id") != video_id:
        raise RuntimeError("YouTube did not return the exact ledger video ID")
    return rows[0]


def run_verification(
    ledger: dict[str, Any],
    youtube_clients: dict[str, Any],
    now: datetime,
    apply_publication: bool,
) -> dict[str, Any]:
    jobs = ledger["jobs"]
    due: list[tuple[str, dict[str, Any], str]] = []
    stale_count = 0
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or str(job.get("status") or "").casefold() != "scheduled":
            continue
        channel_key = str(job.get("channel") or "").casefold()
        if channel_key not in CHANNELS or not job.get("video_id"):
            continue
        target = parse_utc(job.get("publish_at"))
        if target is None or target > now.astimezone(timezone.utc) - GRACE:
            continue
        if now.astimezone(timezone.utc) - target > MAX_LATE:
            job["publication_check"] = {
                "state": "manual_review",
                "checked_at": now.astimezone(timezone.utc).isoformat(),
                "reason": "release is more than 24 hours overdue; no automatic privacy change",
            }
            stale_count += 1
            continue
        due.append((job_id, job, channel_key))

    due.sort(key=lambda item: parse_utc(item[1].get("publish_at")) or datetime.max.replace(tzinfo=timezone.utc))
    due = due[:MAX_JOBS_PER_RUN]

    counts = {
        "checked": 0, "already_public": 0, "promoted_public": 0,
        "waiting": 0, "manual_review": 0, "would_promote": 0, "errors": 0,
        "stale": stale_count,
    }
    reports: list[dict[str, Any]] = []
    for job_id, job, channel_key in due:
        spec = CHANNELS[channel_key]
        video_id = str(job["video_id"])
        checked_at = now.astimezone(timezone.utc).isoformat()
        try:
            youtube = youtube_clients[channel_key]
            video = _list_video(youtube, video_id)
            action, reason = decide_video_action(job, video, spec["channel_id"], now)
            counts["checked"] += 1

            if action == "verify_public":
                job["status"] = "published"
                job["published_verified_at"] = checked_at
                job["publication_check"] = {"state": "public_verified", "checked_at": checked_at, "reason": reason}
                counts["already_public"] += 1
            elif action == "promote_public" and not apply_publication:
                job["publication_check"] = {"state": "would_promote", "checked_at": checked_at, "reason": reason}
                counts["would_promote"] += 1
            elif action == "promote_public":
                current_status = video.get("status") or {}
                youtube.videos().update(
                    part="status",
                    body={
                        "id": video_id,
                        "status": {
                            "privacyStatus": "public",
                            "selfDeclaredMadeForKids": bool(current_status.get("selfDeclaredMadeForKids", False)),
                        },
                    },
                ).execute(num_retries=2)
                verified = _list_video(youtube, video_id)
                verified_status = verified.get("status") or {}
                verified_snippet = verified.get("snippet") or {}
                if (
                    verified_snippet.get("channelId") != spec["channel_id"]
                    or str(verified_status.get("privacyStatus") or "").casefold() != "public"
                ):
                    job["publication_check"] = {
                        "state": "verification_pending",
                        "checked_at": checked_at,
                        "reason": "privacy update was sent but YouTube has not yet confirmed public visibility",
                    }
                    counts["waiting"] += 1
                else:
                    job["status"] = "published"
                    job["published_verified_at"] = checked_at
                    job["publication_check"] = {
                        "state": "public_verified",
                        "checked_at": checked_at,
                        "reason": "overdue private video promoted and verified public",
                    }
                    counts["promoted_public"] += 1
            elif action == "wait":
                job["publication_check"] = {"state": "waiting", "checked_at": checked_at, "reason": reason}
                counts["waiting"] += 1
            else:
                job["publication_check"] = {"state": "manual_review", "checked_at": checked_at, "reason": reason}
                counts["manual_review"] += 1
            reports.append({
                "job_id": job_id,
                "channel": spec["title"],
                "video_id": video_id,
                "action": action,
                "reason": reason,
            })
        except Exception as exc:
            job["publication_check"] = {
                "state": "api_error",
                "checked_at": checked_at,
                "reason": f"YouTube API verification failed ({type(exc).__name__})",
            }
            counts["errors"] += 1
            reports.append({
                "job_id": job_id,
                "channel": spec["title"],
                "video_id": video_id,
                "action": "api_error",
                "reason": type(exc).__name__,
            })

    return {"counts": counts, "reports": reports}


def write_summary(result: dict[str, Any], apply_publication: bool) -> None:
    counts = result["counts"]
    lines = [
        "## YouTube publication verification",
        "",
        f"- Mode: {'automatic repair enabled' if apply_publication else 'read-only'}",
    ]
    for key in ("checked", "already_public", "promoted_public", "waiting", "manual_review", "would_promote", "stale", "errors"):
        lines.append(f"- {key.replace('_', ' ').title()}: {counts[key]}")
    lines.append("")
    for report in result["reports"]:
        lines.append(
            f"- {report['channel']} / {report['job_id']} / {report['video_id']}: "
            f"{report['action']} — {report['reason']}"
        )
    summary = "\n".join(lines) + "\n"
    print(summary)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)


def main() -> int:
    ledger = load_ledger()
    now = datetime.now(timezone.utc)
    due_channels = set()
    for job in ledger["jobs"].values():
        if not isinstance(job, dict) or str(job.get("status") or "").casefold() != "scheduled":
            continue
        channel_key = str(job.get("channel") or "").casefold()
        target = parse_utc(job.get("publish_at"))
        if (
            channel_key in CHANNELS
            and job.get("video_id")
            and target is not None
            and now >= target + GRACE
            and now - target <= MAX_LATE
        ):
            due_channels.add(channel_key)

    clients = {channel: _youtube_client(channel) for channel in sorted(due_channels)}
    apply_publication = os.environ.get("LONGFORM_LIVE_ENABLED", "").strip().lower() == "true"
    result = run_verification(ledger, clients, now, apply_publication)
    save_ledger(ledger)
    write_summary(result, apply_publication)
    return 1 if result["counts"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
