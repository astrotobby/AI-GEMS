#!/usr/bin/env python3
"""Safely finish thumbnail QA and scheduling for the known private DIJITOL NOMAD upload."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image

LEDGER = Path("whop-clipper/state/longform/ledger.json")
JOB_ID = "20261125_digital_nomad_1800"
EXPECTED_CHANNEL_ID = "UCE_cklDludc6nkU1_4K6JzA"
EXPECTED_TITLE = "US to Blacklist Megaspeed After Nvidia Scandal: The Full Inside Story"
VIDEO_ID = "LMv7PLnHDXI"
EXPECTED_ARTIFACT_THUMB = "thumb-20261125_digital_nomad_1800.jpg"


def same_time(left: str | None, right: str) -> bool:
    if not left:
        return False
    def parse(value: str):
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    try:
        return parse(left) == parse(right)
    except (TypeError, ValueError):
        return False


def main() -> int:
    raw = os.environ.get("YOUTUBE_OAUTH_DIJITOL_NOMAD_JSON", "").strip()
    if not raw:
        raise RuntimeError("Missing encrypted DIJITOL NOMAD OAuth secret")
    oauth = json.loads(raw)
    required = ("client_id", "client_secret", "refresh_token")
    if not all(str(oauth.get(k) or "").strip() for k in required):
        raise RuntimeError("DIJITOL NOMAD OAuth secret is incomplete")
    if oauth.get("channel_id") != EXPECTED_CHANNEL_ID:
        raise RuntimeError("OAuth secret channel ID does not match DIJITOL NOMAD")

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    job = (ledger.get("jobs") or {}).get(JOB_ID)
    if not job:
        raise RuntimeError(f"Missing saved long-form job {JOB_ID}")
    if job.get("title") != EXPECTED_TITLE or job.get("channel_id") != EXPECTED_CHANNEL_ID:
        raise RuntimeError("Saved ledger title/channel identity does not match the expected private upload")
    if job.get("video_id") != VIDEO_ID:
        raise RuntimeError("Ledger video ID does not match the already-discovered private upload")

    target = datetime.fromisoformat(str(job["publish_at"]).replace("Z", "+00:00"))
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    target = target.astimezone(timezone.utc).replace(microsecond=0)
    publish_at = target.isoformat().replace("+00:00", "Z")
    if target <= datetime.now(timezone.utc) + timedelta(minutes=5):
        raise RuntimeError("The original 18:00 EAT release slot is no longer safely in the future; leaving the video private")

    credentials = Credentials(
        token=None,
        refresh_token=oauth["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=oauth["client_id"],
        client_secret=oauth["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube"],
    )
    credentials.refresh(Request())
    yt = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    channel_rows = yt.channels().list(part="id,snippet", mine=True).execute().get("items") or []
    if not any(row.get("id") == EXPECTED_CHANNEL_ID for row in channel_rows):
        raise RuntimeError("Refreshed OAuth identity is not the DIJITOL NOMAD channel")

    rows = yt.videos().list(
        part="snippet,status,processingDetails,contentDetails",
        id=VIDEO_ID,
    ).execute().get("items") or []
    if len(rows) != 1:
        raise RuntimeError("YouTube did not return the exact previously uploaded video")
    video = rows[0]
    snippet = video.get("snippet") or {}
    status = video.get("status") or {}
    if snippet.get("channelId") != EXPECTED_CHANNEL_ID or snippet.get("title") != EXPECTED_TITLE:
        raise RuntimeError("Video identity does not match the saved DIJITOL NOMAD job")
    if status.get("privacyStatus") != "private":
        raise RuntimeError("The video is not private; refusing to modify its release state")
    current_publish = status.get("publishAt")
    if current_publish and not same_time(current_publish, publish_at):
        raise RuntimeError("The video already has a different publishAt; leaving it unchanged")

    processing = (video.get("processingDetails") or {}).get("processingStatus")
    for _ in range(18):
        if not processing or processing == "succeeded":
            break
        if processing in ("failed", "terminated"):
            raise RuntimeError(f"YouTube video processing ended with status {processing}")
        time.sleep(10)
        reread = yt.videos().list(part="processingDetails,status,snippet", id=VIDEO_ID).execute().get("items") or []
        if not reread:
            raise RuntimeError("Video disappeared while checking its processing status")
        video = reread[0]
        processing = (video.get("processingDetails") or {}).get("processingStatus")
    if processing and processing != "succeeded":
        raise RuntimeError(f"YouTube processing is not complete yet (status={processing}); video remains private")

    artifact_dir = Path(os.environ["LONGFORM_ARTIFACT_DIR"])
    matches = list(artifact_dir.rglob(EXPECTED_ARTIFACT_THUMB))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one saved thumbnail in the original run artifact; found {len(matches)}")
    thumbnail = matches[0]
    with Image.open(thumbnail) as image:
        if image.format != "JPEG" or image.size != (1280, 720):
            raise RuntimeError("Saved thumbnail is not a 1280x720 JPEG")
        image.verify()
    if thumbnail.stat().st_size > 2_000_000:
        raise RuntimeError("Saved thumbnail exceeds YouTube's 2 MB limit")

    if not job.get("thumbnail_verified"):
        response = yt.thumbnails().set(
            videoId=VIDEO_ID,
            media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg", resumable=False),
        ).execute()
        if response.get("kind") != "youtube#thumbnailSetResponse" or not response.get("items"):
            raise RuntimeError("YouTube did not return a valid thumbnailSetResponse")
        refreshed = yt.videos().list(part="snippet,status", id=VIDEO_ID).execute().get("items") or []
        if not refreshed:
            raise RuntimeError("Could not read the video after thumbnail upload")
        thumbs = (refreshed[0].get("snippet") or {}).get("thumbnails") or {}
        if not any(thumbs.get(size, {}).get("url") for size in ("maxres", "standard", "high", "medium")):
            raise RuntimeError("YouTube thumbnail response could not be verified")
        job["thumbnail_verified"] = True
        job["thumbnail_verified_at"] = datetime.now(timezone.utc).isoformat()
        print("THUMBNAIL_VERIFIED=TRUE")
    else:
        print("THUMBNAIL_ALREADY_VERIFIED=TRUE")

    if not same_time(current_publish, publish_at):
        yt.videos().update(
            part="status",
            body={"id": VIDEO_ID, "status": {"privacyStatus": "private", "publishAt": publish_at}},
        ).execute()

    verified = {}
    for attempt in range(18):
        refreshed = yt.videos().list(part="status,snippet", id=VIDEO_ID).execute().get("items") or []
        if not refreshed:
            raise RuntimeError("Could not verify scheduled video status")
        verified = refreshed[0].get("status") or {}
        if verified.get("privacyStatus") == "private" and same_time(verified.get("publishAt"), publish_at):
            break
        if attempt < 17:
            time.sleep(5)
    if verified.get("privacyStatus") != "private" or not same_time(verified.get("publishAt"), publish_at):
        raise RuntimeError("YouTube did not confirm the original private scheduled release; no public status was applied")

    now = datetime.now(timezone.utc).isoformat()
    job.update({
        "video_id": VIDEO_ID,
        "url": f"https://youtu.be/{VIDEO_ID}",
        "status": "scheduled",
        "publish_at": publish_at,
        "reconciled": True,
        "existing_private_upload": True,
        "thumbnail_verified": True,
        "scheduled_at": job.get("scheduled_at") or now,
        "updated_at": now,
    })
    LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": "scheduled_confirmed",
        "channel": "DIJITOL NOMAD",
        "video_id": VIDEO_ID,
        "url": f"https://youtu.be/{VIDEO_ID}",
        "privacy_status": verified.get("privacyStatus"),
        "publish_at": verified.get("publishAt"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RECOVERY_BLOCKED: {type(exc).__name__}: {str(exc)[:500]}", file=sys.stderr)
        raise SystemExit(1)
