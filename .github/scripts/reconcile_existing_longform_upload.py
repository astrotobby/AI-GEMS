"""Safely reconcile one existing private long-form YouTube upload without re-uploading it."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# This script runs from the whop-clipper working directory in GitHub Actions.
# Add that directory explicitly because Python otherwise prioritizes .github/scripts.
sys.path.insert(0, str(Path.cwd().resolve()))
from clipper import publish


CHANNELS = {
    "astrotobby": {
        "title": "Astro Tobby",
        "channel_id": "UCxndvpcFaVhN1bJxgWIjG_A",
        "owner": "goodlucktobby@gmail.com",
    },
    "digital_nomad": {
        "title": "DIJITOL NOMAD",
        "channel_id": "UCE_cklDludc6nkU1_4K6JzA",
        "owner": "chanzera3@gmail.com",
    },
}
PRESERVED_STATUS_FIELDS = (
    "license",
    "embeddable",
    "publicStatsViewable",
    "selfDeclaredMadeForKids",
    "containsSyntheticMedia",
)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def same_time(actual: str | None, expected: str) -> bool:
    if not actual:
        return False
    try:
        return parse_time(actual) == parse_time(expected)
    except (TypeError, ValueError, OverflowError):
        return False


def private_unscheduled_status(current: dict) -> dict:
    status = {
        key: current[key]
        for key in PRESERVED_STATUS_FIELDS
        if key in current and current[key] is not None
    }
    status["privacyStatus"] = "private"
    # Intentionally omit publishAt. With part=status, omitted mutable fields are removed.
    return status


def main() -> int:
    channel_key = os.environ["CHANNEL"].strip()
    job_id = os.environ["JOB_ID"].strip()
    video_id = os.environ["VIDEO_ID"].strip()
    source_run_id = os.environ["SOURCE_RUN_ID"].strip()
    if channel_key not in CHANNELS:
        raise SystemExit("Unknown channel")
    if not job_id or not video_id or not source_run_id:
        raise SystemExit("JOB_ID, VIDEO_ID, and SOURCE_RUN_ID are required")

    expected_channel = CHANNELS[channel_key]
    ledger_path = Path("state/longform/ledger.json")
    if not ledger_path.is_file():
        raise SystemExit("Long-form ledger not found")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    job = (ledger.get("jobs") or {}).get(job_id)
    if not job:
        raise SystemExit(f"Job {job_id} is not present in the long-form ledger")
    if job.get("channel") != channel_key:
        raise SystemExit("Ledger channel does not match the selected channel")
    if job.get("video_id") != video_id:
        raise SystemExit("Supplied video ID does not match the ledger; refusing to touch another video")
    if job.get("workflow_run_id") and str(job["workflow_run_id"]) != source_run_id:
        raise SystemExit("Source workflow run ID does not match the ledger")
    if job.get("status") not in ("uploading", "scheduled"):
        raise SystemExit(f"Job status {job.get('status')!r} is not eligible for reconciliation")
    expected_publish_at = str(job.get("publish_at") or "")
    if not expected_publish_at:
        raise SystemExit("Ledger has no intended publish_at value")
    expected_dt = parse_time(expected_publish_at)
    if expected_dt <= datetime.now(timezone.utc).replace(microsecond=0):
        raise SystemExit("The intended release time has passed; refusing to schedule a past time")

    import tomllib
    with Path("config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    target = next(
        (
            item for item in config.get("targets", [])
            if item.get("platform") == "youtube"
            and (item.get("channel") or "").strip().casefold() == expected_channel["title"].casefold()
        ),
        None,
    )
    if not target:
        raise SystemExit(f"Configured YouTube target not found for {expected_channel['title']}")

    creds = publish._yt_creds(target)
    if not creds:
        raise SystemExit("No saved OAuth credentials for the selected channel")
    access_token = publish._yt_access_token(creds)
    client_id, client_secret, refresh_token = creds
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube"],
    )
    yt = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    channels = yt.channels().list(part="id,snippet", mine=True).execute().get("items") or []
    if not any(item.get("id") == expected_channel["channel_id"] for item in channels):
        raise SystemExit("OAuth identity does not match the selected YouTube channel")

    rows = yt.videos().list(part="id,snippet,status", id=video_id).execute().get("items") or []
    if not rows:
        raise SystemExit("YouTube did not return the existing video")
    video = rows[0]
    snippet = video.get("snippet") or {}
    status = video.get("status") or {}
    if snippet.get("channelId") != expected_channel["channel_id"]:
        raise SystemExit("Existing video belongs to a different channel")
    if str(snippet.get("title") or "").strip().casefold() != str(job.get("title") or "").strip().casefold():
        raise SystemExit("Existing video title does not match its ledger job")
    if status.get("privacyStatus") != "private":
        raise SystemExit(
            f"Safety stop: existing video privacy is {status.get('privacyStatus')!r}; "
            "this recovery workflow never modifies public or unlisted videos"
        )
    if status.get("uploadStatus") not in (None, "processed"):
        raise SystemExit(f"Video processing is not complete: {status.get('uploadStatus')!r}")

    artifact_root = Path("../recovery-artifact")
    thumbnails = sorted(artifact_root.rglob("thumb-*.jpg"))
    if len(thumbnails) != 1:
        raise SystemExit(f"Expected exactly one saved thumbnail in the source run artifact; found {len(thumbnails)}")
    thumbnail_path = thumbnails[0]
    thumb_bytes = thumbnail_path.read_bytes()
    if len(thumb_bytes) < 1024 or len(thumb_bytes) > 2_000_000 or not thumb_bytes.startswith(b"\xff\xd8\xff"):
        raise SystemExit("Saved thumbnail is not a valid JPEG within YouTube's 2 MB limit")

    # Re-apply the exact thumbnail produced by the original render, never a new or guessed image.
    yt.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg", resumable=False),
    ).execute()

    # Verify via videos.list, matching the production pipeline's thumbnail QA gate.
    rows = yt.videos().list(part="snippet,status", id=video_id).execute().get("items") or []
    if not rows:
        raise SystemExit("Video could not be read back after thumbnail verification")
    current_video = rows[0]
    current_snippet = current_video.get("snippet") or {}
    current_status = current_video.get("status") or {}
    current_thumbnails = current_snippet.get("thumbnails") or {}
    if not any(current_thumbnails.get(size, {}).get("url") for size in ("maxres", "standard", "high", "medium", "default")):
        raise SystemExit("YouTube custom thumbnail upload could not be verified")
    if current_snippet.get("channelId") != expected_channel["channel_id"]:
        raise SystemExit("Post-thumbnail channel identity check failed")
    if current_status.get("privacyStatus") != "private":
        raise SystemExit("Video is no longer private; refusing to modify its schedule")

    actual_publish_at = current_status.get("publishAt")
    if actual_publish_at and not same_time(actual_publish_at, expected_publish_at):
        raise SystemExit(
            f"Video already has a different publishAt value ({actual_publish_at!r}); "
            "no schedule change was made"
        )

    if not same_time(actual_publish_at, expected_publish_at):
        scheduled_status = {
            key: current_status[key]
            for key in PRESERVED_STATUS_FIELDS
            if key in current_status and current_status[key] is not None
        }
        scheduled_status.update({
            "privacyStatus": "private",
            "publishAt": parse_time(expected_publish_at).isoformat().replace("+00:00", "Z"),
        })
        yt.videos().update(
            part="status",
            body={"id": video_id, "status": scheduled_status},
        ).execute()

    verified = yt.videos().list(part="snippet,status", id=video_id).execute().get("items") or []
    if not verified:
        raise SystemExit("YouTube schedule update returned but could not be read back")
    final_video = verified[0]
    final_snippet = final_video.get("snippet") or {}
    final_status = final_video.get("status") or {}
    if final_snippet.get("channelId") != expected_channel["channel_id"]:
        raise SystemExit("Post-schedule channel identity check failed")
    if final_status.get("privacyStatus") != "private" or not same_time(
        final_status.get("publishAt"), expected_publish_at
    ):
        # Fail closed and verify rollback. Omit publishAt instead of sending null.
        yt.videos().update(
            part="status",
            body={"id": video_id, "status": private_unscheduled_status(final_status)},
        ).execute()
        after = yt.videos().list(part="status", id=video_id).execute().get("items") or []
        after_status = (after[0].get("status") or {}) if after else {}
        if after_status.get("privacyStatus") != "private" or after_status.get("publishAt"):
            raise SystemExit(
                "Schedule verification failed and YouTube did not confirm private-unscheduled rollback"
            )
        raise SystemExit("YouTube did not retain the exact requested release time; upload remains private-unscheduled")

    job.update({
        "status": "scheduled",
        "url": f"https://youtu.be/{video_id}",
        "video_id": video_id,
        "publish_at": parse_time(expected_publish_at).isoformat(),
        "thumbnail_verified": True,
        "thumbnail_verified_at": datetime.now(timezone.utc).isoformat(),
        "scheduled_at": datetime.now(timezone.utc).isoformat(),
        "reconciled": True,
        "reconciled_by_workflow_run": os.environ.get("GITHUB_RUN_ID", ""),
    })
    tmp = ledger_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(ledger_path)
    print(json.dumps({
        "result": "scheduled_verified",
        "job_id": job_id,
        "channel": expected_channel["title"],
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "publish_at": job["publish_at"],
        "privacy_status": "private",
        "thumbnail_verified": True,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
