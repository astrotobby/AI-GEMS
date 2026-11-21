#!/usr/bin/env python3
"""Create missing social handoff releases from the original long-form render artifacts.

This is a backfill-only utility: it does not upload to YouTube or alter video visibility,
schedule, descriptions, thumbnails, OAuth state, or the production publishing workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "whop-clipper" / "state" / "longform" / "ledger.json"
REPO = "astrotobby/AI-GEMS"
VALID_CHANNELS = {"astrotobby", "digital_nomad"}


def log(message: str) -> None:
    print(f"[social-handoff-backfill] {message}", flush=True)


def run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command[:4])}: "
            f"{(result.stderr or result.stdout or '')[-500:]}"
        )
    return result


def brief_description(raw: str) -> str:
    text = str(raw or "").replace("\r", "\n")
    lower = text.casefold()
    markers = (
        "\nsources and further reading:", "\nsources:", "\nreferences:",
        "\ncitations:", "\nfurther reading:",
    )
    positions = [lower.find(marker) for marker in markers if lower.find(marker) >= 0]
    if positions:
        text = text[:min(positions)]
    text = re.sub(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]+\)", r"\1", text, flags=re.I)
    text = re.sub(r"https?://\S+|www\.\S+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" \t\n-–—;:,.")
    text = " ".join(re.split(r"(?<=[.!?])\s+", text)[:2]).strip()
    if len(text) > 280:
        text = text[:280].rsplit(" ", 1)[0].rstrip(" ,;:-") + "."
    return text


def parse_mappings(value: str) -> list[tuple[str, int]]:
    pairs = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if "=" not in raw:
            raise ValueError(f"Invalid mapping {raw!r}; expected job_id=workflow_run_id")
        job_id, run_id = (part.strip() for part in raw.split("=", 1))
        if not re.fullmatch(r"\d{8}_(?:astrotobby|digital_nomad)_\d{4}", job_id):
            raise ValueError(f"Unexpected long-form job ID: {job_id!r}")
        if not run_id.isdigit():
            raise ValueError(f"Invalid workflow run ID for {job_id}: {run_id!r}")
        pairs.append((job_id, int(run_id)))
    if not pairs:
        raise ValueError("No job_id=workflow_run_id mappings were supplied")
    if len({job for job, _ in pairs}) != len(pairs):
        raise ValueError("Duplicate job IDs were supplied")
    return pairs


def download_original_artifact(run_id: int, artifact_name: str, folder: Path) -> Path:
    run([
        "gh", "run", "download", str(run_id),
        "--repo", REPO,
        "--name", artifact_name,
        "--dir", str(folder),
    ])
    matches = list(folder.rglob("video_16x9.mp4"))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one video_16x9.mp4 in {artifact_name}; found {len(matches)}"
        )
    video = matches[0]
    if video.stat().st_size < 1_000_000:
        raise RuntimeError(f"Downloaded original MP4 is too small: {video.stat().st_size} bytes")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type", "-of", "json", str(video)],
        capture_output=True, text=True, check=False, timeout=90,
    )
    if probe.returncode:
        raise RuntimeError(f"Original artifact is not a readable MP4: {probe.stderr[-300:]}")
    data = json.loads(probe.stdout or "{}")
    streams = {item.get("codec_type") for item in data.get("streams", [])}
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not {"video", "audio"}.issubset(streams) or duration < 60:
        raise RuntimeError("Original artifact failed the video/audio/duration integrity check")
    return video


def release_manifest(job: dict, job_id: str, run_id: int) -> dict:
    channel = str(job.get("channel") or "")
    if channel not in VALID_CHANNELS:
        raise RuntimeError(f"{job_id}: unexpected channel {channel!r}")
    if job.get("status") not in {"scheduled", "published"}:
        raise RuntimeError(f"{job_id}: refusing a job with status {job.get('status')!r}")
    video_id = str(job.get("video_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise RuntimeError(f"{job_id}: missing/invalid existing YouTube video ID")
    publish_at = str(job.get("publish_at") or "")
    if not publish_at or not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", publish_at):
        raise RuntimeError(f"{job_id}: publish_at must include a timezone")
    package = job.get("package") or {}
    channel_title = str(job.get("channel_title") or (
        "Astro Tobby" if channel == "astrotobby" else "DIJITOL NOMAD"
    ))
    return {
        "job_id": job_id,
        "channel": channel,
        "channel_title": channel_title,
        "title": str(package.get("title") or job.get("title") or "")[:95],
        "description": brief_description(package.get("description", "")),
        "tags": list(package.get("tags") or [])[:15],
        "video_id": video_id,
        "youtube_url": str(job.get("url") or f"https://youtu.be/{video_id}"),
        "publish_at": publish_at,
        "workflow_run_id": str(run_id),
        "artifact_name": f"longform-{channel}-{run_id}",
        "created_at": str(job.get("scheduled_at") or job.get("updated_at") or ""),
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
    }


def create_release(job_id: str, manifest: dict, video: Path, workdir: Path) -> dict:
    tag = f"social-handoff-{job_id}"
    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    existing = subprocess.run(
        ["gh", "release", "view", tag, "--repo", REPO, "--json", "tagName,assets"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if existing.returncode == 0:
        current_dir = workdir / "existing"
        current_dir.mkdir()
        check = subprocess.run(
            ["gh", "release", "download", tag, "--repo", REPO,
             "--pattern", "manifest.json", "--dir", str(current_dir)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        current_manifest_path = current_dir / "manifest.json"
        if check.returncode == 0 and current_manifest_path.exists():
            current = json.loads(current_manifest_path.read_text(encoding="utf-8"))
            if str(current.get("video_id")) != str(manifest["video_id"]):
                raise RuntimeError(
                    f"{tag} already points to a different YouTube video; refusing to overwrite"
                )
            log(f"{job_id}: matching handoff already exists; no duplicate release created")
            return {"job_id": job_id, "tag": tag, "status": "already_exists"}

    run([
        "gh", "release", "create", tag, str(video), str(manifest_path),
        "--repo", REPO,
        "--title", tag,
        "--notes", (
            "Backfilled social handoff for an existing scheduled YouTube video. "
            "The original YouTube video, visibility and publish time are unchanged."
        ),
    ])
    return {
        "job_id": job_id,
        "tag": tag,
        "status": "created",
        "video_id": manifest["video_id"],
        "publish_at": manifest["publish_at"],
        "video_bytes": video.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mappings", required=True,
                        help="Comma-separated job_id=workflow_run_id pairs")
    args = parser.parse_args()
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise SystemExit("GH_TOKEN/GITHUB_TOKEN is missing")
    mappings = parse_mappings(args.mappings)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    jobs = ledger.get("jobs") or {}
    output = []

    for job_id, run_id in mappings:
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            raise RuntimeError(f"Job missing from ledger: {job_id}")
        expected_artifact = f"longform-{job.get('channel')}-{run_id}"
        manifest = release_manifest(job, job_id, run_id)
        with tempfile.TemporaryDirectory(prefix="social-handoff-backfill-") as temp:
            folder = Path(temp)
            video = download_original_artifact(run_id, expected_artifact, folder)
            result = create_release(job_id, manifest, video, folder)
        output.append(result)
        print(json.dumps(result, ensure_ascii=False))

    print(json.dumps({
        "result": "SOCIAL_HANDOFF_BACKFILL_COMPLETE",
        "created": sum(item["status"] == "created" for item in output),
        "already_exists": sum(item["status"] == "already_exists" for item in output),
        "items": output,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
