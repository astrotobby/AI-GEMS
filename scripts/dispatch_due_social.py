#!/usr/bin/env python3
"""Dispatch published AI-GEMS long-form videos to the existing astro-blog social relay."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "whop-clipper" / "state" / "longform" / "ledger.json"
DISPATCH_LEDGER = ROOT / "whop-clipper" / "state" / "longform" / "social_dispatch_ledger.json"
REPO = "astrotobby/AI-GEMS"
BLOG_REPO = "astrotobby/astro-blog"


def log(message: str) -> None:
    print(f"[longform-social-dispatch] {message}", flush=True)


def run(command: list[str], *, check: bool = True, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, cwd=cwd, check=False)
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command[0]} "
            f"{(result.stderr or result.stdout or '')[:500]}"
        )
    return result


def parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_dispatch_state(state: dict) -> None:
    DISPATCH_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = DISPATCH_LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DISPATCH_LEDGER)
    run(["git", "config", "user.name", "longform-social-dispatch[bot]"])
    run(["git", "config", "user.email", "longform-social-dispatch@users.noreply.github.com"])
    run(["git", "add", str(DISPATCH_LEDGER.relative_to(ROOT))])
    staged = run(["git", "diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        return
    run(["git", "commit", "-m", "Record long-form social relay dispatches"])
    branch = run(["git", "branch", "--show-current"]).stdout.strip() or "main"
    run(["git", "push", "origin", f"HEAD:{branch}"])


def safe_release_tag(job_id: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", job_id.lower()).strip("-")
    return "longform-social-" + slug[:70]


def download_run_video(run_id: str, artifact_name: str, workdir: Path) -> Path:
    result = run([
        "gh", "run", "download", str(run_id),
        "--repo", REPO,
        "--name", artifact_name,
        "--dir", str(workdir),
    ])
    candidates = list(workdir.rglob("video_16x9.mp4"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one video_16x9.mp4 in run artifact {artifact_name}; "
            f"found {len(candidates)}"
        )
    video = candidates[0]
    if not video.is_file() or video.stat().st_size < 1_000_000:
        raise RuntimeError("Downloaded long-form video is missing or unexpectedly small")
    return video


def publish_release_asset(job_id: str, title: str, video: Path) -> str:
    tag = safe_release_tag(job_id)
    existing = run(["gh", "release", "view", tag, "--repo", REPO], check=False)
    if existing.returncode != 0:
        run([
            "gh", "release", "create", tag, str(video),
            "--repo", REPO,
            "--title", title[:90],
            "--notes", (
                "Long-form video source for the connected social publishing workflow. "
                "Published only after the matching YouTube scheduled video was verified public."
            ),
        ])
    else:
        run(["gh", "release", "upload", tag, str(video), "--repo", REPO, "--clobber"])
    return f"https://github.com/{REPO}/releases/download/{tag}/video_16x9.mp4"


def dispatch_to_astro_blog(payload: dict) -> None:
    token = os.environ.get("ASTRO_BLOG_DISPATCH_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Missing ASTRO_BLOG_DISPATCH_TOKEN in AI-GEMS Actions secrets; "
            "no social destination was triggered."
        )
    response = requests.post(
        f"https://api.github.com/repos/{BLOG_REPO}/dispatches",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"event_type": "longform_video_ready", "client_payload": payload},
        timeout=30,
    )
    if response.status_code != 204:
        raise RuntimeError(
            f"astro-blog repository_dispatch failed ({response.status_code}): "
            f"{(response.text or '')[:350]}"
        )


def verify_video_is_public(channel: str, video_id: str) -> bool:
    # The same OAuth validation helper as the production uploader ensures the
    # token belongs to the correct YouTube channel before reading its video state.
    import sys

    sys.path.insert(0, str(ROOT / "whop-clipper"))
    sys.path.insert(0, str(ROOT / "astro-blog" / "scripts"))
    import longform_pipeline

    previous_cwd = Path.cwd()
    try:
        # yt_service resolves config.toml and the OAuth cache relative to whop-clipper.
        os.chdir(ROOT / "whop-clipper")
        yt, _target, _creds, identity = longform_pipeline.yt_service(channel)
        items = yt.videos().list(part="status,snippet", id=video_id).execute().get("items") or []
    finally:
        os.chdir(previous_cwd)
    if not items:
        log(f"{video_id}: YouTube returned no video record yet")
        return False
    status = items[0].get("status") or {}
    privacy = status.get("privacyStatus")
    if privacy != "public":
        log(f"{video_id}: YouTube still reports privacyStatus={privacy!r}; waiting")
        return False
    title = (items[0].get("snippet") or {}).get("title") or ""
    log(f"YouTube confirmed PUBLIC: {identity['title']} / {title}")
    return True


def main() -> int:
    ledger = load_json(LEDGER)
    jobs = ledger.get("jobs", {}) if isinstance(ledger, dict) else {}
    state = load_json(DISPATCH_LEDGER)
    dispatched = state.setdefault("dispatched", {})
    now = datetime.now(timezone.utc)

    due = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or job.get("status") != "scheduled":
            continue
        if job_id in dispatched:
            continue
        publish_at = parse_time(job.get("publish_at"))
        if not publish_at or publish_at > now:
            continue
        video_id = str(job.get("video_id") or "").strip()
        channel = str(job.get("channel") or "").strip()
        run_id = str(job.get("workflow_run_id") or "").strip()
        artifact_name = str(job.get("artifact_name") or "").strip()
        if not video_id or channel not in {"astrotobby", "digital_nomad"} or not run_id:
            log(f"{job_id}: missing video/run metadata; not dispatched")
            continue
        if not artifact_name:
            artifact_name = f"longform-{channel}-{run_id}"
        due.append((job_id, job, video_id, channel, run_id, artifact_name))

    if not due:
        log("No due, not-yet-dispatched long-form videos.")
        return 0

    for job_id, job, video_id, channel, run_id, artifact_name in due:
        try:
            if not verify_video_is_public(channel, video_id):
                continue

            token = os.environ.get("ASTRO_BLOG_DISPATCH_TOKEN", "").strip()
            if not token:
                raise RuntimeError("ASTRO_BLOG_DISPATCH_TOKEN is not configured")

            with tempfile.TemporaryDirectory(prefix="longform-social-") as temp:
                video = download_run_video(run_id, artifact_name, Path(temp))
                video_url = publish_release_asset(job_id, job.get("title") or "Long-form video", video)

            package = job.get("package") or {}
            tags = package.get("tags") or []
            payload = {
                "job_id": job_id,
                "channel": channel,
                "channel_name": job.get("channel_title") or (
                    "Astro Tobby" if channel == "astrotobby" else "DIJITOL NOMAD"
                ),
                "title": job.get("title") or package.get("title") or "New video",
                "description": package.get("description") or "",
                "tags": tags,
                "video_id": video_id,
                "youtube_url": job.get("url") or f"https://youtu.be/{video_id}",
                "video_url": video_url,
            }
            dispatch_to_astro_blog(payload)
            dispatched[job_id] = {
                "channel": channel,
                "video_id": video_id,
                "video_url": video_url,
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            }
            save_dispatch_state(state)
            log(f"Dispatched {job_id} to astro-blog social relay.")
        except Exception as exc:
            log(f"{job_id}: dispatch not completed: {type(exc).__name__}: {str(exc)[:450]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
