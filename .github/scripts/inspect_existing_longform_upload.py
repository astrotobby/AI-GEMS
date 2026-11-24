"""Read-only inspection for an existing long-form YouTube upload."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# The workflow executes from whop-clipper; make that package directory importable.
sys.path.insert(0, str(Path.cwd().resolve()))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from clipper import publish


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


def main() -> int:
    import argparse
    import tomllib

    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, choices=sorted(CHANNELS))
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()

    expected = CHANNELS[args.channel]
    with Path("config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    target = next(
        (
            item for item in config.get("targets", [])
            if item.get("platform") == "youtube"
            and (item.get("channel") or "").strip().casefold() == expected["title"].casefold()
        ),
        None,
    )
    if not target:
        raise SystemExit("Configured YouTube target was not found")

    creds = publish._yt_creds(target)
    if not creds:
        raise SystemExit("No saved OAuth credentials for the selected channel")
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
    yt = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    channels = yt.channels().list(part="id,snippet", mine=True).execute().get("items") or []
    actual_channel = channels[0] if channels else {}
    if actual_channel.get("id") != expected["channel_id"]:
        raise SystemExit(
            f"OAuth channel mismatch: expected {expected['channel_id']}, "
            f"received {actual_channel.get('id')}"
        )

    rows = yt.videos().list(
        part="id,snippet,status,processingDetails",
        id=args.video_id,
    ).execute().get("items") or []
    if not rows:
        raise SystemExit("YouTube did not return the requested video")
    video = rows[0]
    snippet = video.get("snippet") or {}
    status = video.get("status") or {}
    processing = video.get("processingDetails") or {}

    output = {
        "inspection": "read_only",
        "job_id": args.job_id,
        "video_id": args.video_id,
        "channel_id": snippet.get("channelId"),
        "channel_matches_expected": snippet.get("channelId") == expected["channel_id"],
        "title": snippet.get("title"),
        "privacy_status": status.get("privacyStatus"),
        "publish_at": status.get("publishAt"),
        "upload_status": status.get("uploadStatus"),
        "processing_status": processing.get("processingStatus"),
        "thumbnail_sizes_present": sorted((snippet.get("thumbnails") or {}).keys()),
    }
    print(json.dumps(output, indent=2))
    if snippet.get("channelId") != expected["channel_id"]:
        raise SystemExit("Existing video belongs to a different channel")
    if status.get("privacyStatus") != "private":
        raise SystemExit("Inspection stop: the video is not private; no changes were made")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
