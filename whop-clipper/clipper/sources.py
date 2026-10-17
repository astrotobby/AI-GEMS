"""Download the campaign's source footage (Google Drive files/folders, YouTube, etc.)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _videos(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXT)


def fetch(url: str, dest: Path, max_files: int = 2, skip: set[str] | None = None) -> list[Path]:
    """Download one source link into dest and return the video files it produced.

    Drive folders can hold ~100 videos, so only `max_files` not-yet-used ones are pulled.
    """
    dest.mkdir(parents=True, exist_ok=True)
    skip = skip or set()
    before = set(_videos(dest))
    if "drive.google.com" in url:
        import gdown
        if "/folders/" in url:
            listing = gdown.download_folder(url=url, output=str(dest), quiet=True,
                                            skip_download=True, retries=2) or []
            fresh = [f for f in listing if Path(f.path).suffix.lower() in VIDEO_EXT
                     and Path(f.path).name not in skip]
            for f in fresh[:max_files]:
                target = dest / Path(f.path).name
                if not target.exists():
                    gdown.download(id=f.id, output=str(target), quiet=True, retries=2)
            return [dest / Path(f.path).name for f in fresh[:max_files] if (dest / Path(f.path).name).exists()]
        gdown.download(url=url, output=str(dest) + "/", quiet=True, fuzzy=True, retries=2)
    else:
        cmd = ["yt-dlp", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
               "--merge-output-format", "mp4", "-o", str(dest / "%(id)s.%(ext)s")]
        # YouTube blocks most cloud/datacenter IPs ("Sign in to confirm you're not a bot"):
        # export cookies from a logged-in browser and point YTDLP_COOKIES at the file.
        cookies = os.environ.get("YTDLP_COOKIES")
        if cookies and Path(cookies).exists():
            cmd += ["--cookies", cookies]
        subprocess.run(cmd + [url], check=True)
    new = [p for p in _videos(dest) if p not in before]
    return (new or _videos(dest))[:max_files]


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip() or 0)
