#!/usr/bin/env python3
"""Private local-to-Kling influencer animation runner.

Uses exactly one explicitly selected API key from KLING_API_KEYS for each run.
Never rotates between accounts. Keeps source images/videos and generated MP4s local.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

API = "https://api-singapore.klingai.com"
# Model-specific Omni endpoints supported by the current Kling API.
# Kling 3.0 uses the Omni endpoint because this workflow supplies a motion video
# plus identity and scene images as references.
MODEL_ENDPOINTS = (
    ("kling-4.0", "/omni-video/kling-4.0"),
    ("kling-4.0-flash", "/omni-video/kling-4.0-flash"),
    ("kling-3.0-omni", "/omni-video/kling-3.0-omni"),
)
MODEL_PRIORITY = {name: index for index, (name, _) in enumerate(MODEL_ENDPOINTS)}
CHARACTER_REFERENCE_VIDEO_NAME = "kling_20261124_VIDEO_Subtle_slo_4117_0.mp4"


def parse_key_pool(raw: str) -> list[str]:
    if not raw or not raw.strip():
        raise RuntimeError("KLING_API_KEYS secret is empty.")
    values: list[str] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            values = [str(x) for x in parsed]
        elif isinstance(parsed, dict):
            for name in ("keys", "api_keys", "KLING_API_KEYS"):
                candidate = parsed.get(name)
                if isinstance(candidate, list):
                    values = [str(x) for x in candidate]
                    break
            if not values:
                values = [str(v) for v in parsed.values() if isinstance(v, str)]
        elif isinstance(parsed, str):
            values = parsed.splitlines()
    except json.JSONDecodeError:
        values = re.split(r"[\r\n,;]+", raw)

    cleaned = []
    for value in values:
        key = value.strip().strip('"').strip("'")
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        if key and key not in cleaned:
            cleaned.append(key)
    if not cleaned:
        raise RuntimeError("No API keys could be parsed from KLING_API_KEYS.")
    return cleaned


def mask_keys(keys: list[str]) -> None:
    # GitHub Actions masks each individual item in a newline/JSON secret.
    for key in keys:
        print(f"::add-mask::{key}", flush=True)


def require_billing_authorization(mode: str, allow_cash_billing: bool) -> None:
    if mode == "animate" and not allow_cash_billing:
        raise RuntimeError(
            "Generation is blocked until paid API-balance use is explicitly authorized. "
            "Kling may prioritize monetary account balance even when a resource package exists."
        )


def supported_model_endpoints(pack: dict[str, Any]) -> list[tuple[str, str]]:
    """Return only Omni models explicitly listed as applicable to this package."""
    raw = str(pack.get("resource_pack_applicable_models", "")).lower()
    compact = re.sub(r"[^a-z0-9.]+", "", raw)
    result: list[tuple[str, str]] = []
    if re.search(r"kling\s*4\.0(?!\s*flash)", raw):
        result.append(("kling-4.0", "/omni-video/kling-4.0"))
    if re.search(r"kling\s*4\.0\s*flash", raw):
        result.append(("kling-4.0-flash", "/omni-video/kling-4.0-flash"))
    if any(token in compact for token in ("kling3.0omni", "klingv3omni", "klingvideo3.0omni")):
        result.append(("kling-3.0-omni", "/omni-video/kling-3.0-omni"))
    return result


def account_video_pack(key: str) -> list[dict[str, Any]]:
    """Find all active resource-pack/model combinations available to one key."""
    now_ms = int(time.time() * 1000)
    params = {
        "start_time": now_ms - 90 * 24 * 60 * 60 * 1000,
        "end_time": now_ms,
    }
    response = requests.get(
        f"{API}/account/costs",
        params=params,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"Kling account check failed: code={payload.get('code')} "
            f"message={str(payload.get('message', ''))[:160]}"
        )
    data = payload.get("data") or {}
    if data.get("code") not in (None, 0):
        raise RuntimeError(
            f"Kling account-costs response code={data.get('code')}: "
            f"{str(data.get('msg', ''))[:160]}"
        )

    best_by_model: dict[str, dict[str, Any]] = {}
    for pack in data.get("resource_pack_subscribe_infos") or []:
        status = str(pack.get("status", "")).lower()
        remaining = float(pack.get("remaining_quantity") or 0)
        if status != "online" or remaining <= 0:
            continue
        for model_name, endpoint in supported_model_endpoints(pack):
            current = best_by_model.get(model_name)
            if current is None or remaining > float(current["remaining_quantity"] or 0):
                best_by_model[model_name] = {
                    "pack": pack,
                    "model_name": model_name,
                    "endpoint": endpoint,
                    "remaining_quantity": pack.get("remaining_quantity"),
                }

    if not best_by_model:
        raise RuntimeError(
            "This account has no active video resource package for a supported "
            "Kling Omni model (Kling 4.0, 4.0 Flash, or 3.0 Omni)."
        )
    return sorted(
        best_by_model.values(),
        key=lambda item: MODEL_PRIORITY.get(item["model_name"], 99),
    )


def select_eligible_accounts(keys: list[str]) -> list[dict[str, Any]]:
    """Check each stored key in order and keep every usable key/model pairing."""
    selections: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, key in enumerate(keys, start=1):
        if index > 1:
            # Kling documents QPS <= 1 for the account resource-pack endpoint.
            time.sleep(1.1)
        try:
            models = account_video_pack(key)
            for selected in models:
                selections.append({
                    "credential_index": index,
                    "key": key,
                    **selected,
                })
                print(
                    f"KLING_ACCOUNT_READY index={index} model={selected['model_name']} "
                    f"remaining_units={selected['remaining_quantity']}",
                    flush=True,
                )
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                raise RuntimeError(
                    f"Kling rate-limited account check at key index {index}; "
                    "stopping instead of rotating around a rate limit."
                ) from exc
            if status is not None and 500 <= status <= 599:
                raise RuntimeError(
                    f"Kling account check returned HTTP {status}; outcome is uncertain, "
                    "so key rotation is paused."
                ) from exc
            failures.append(f"key {index}: HTTP {status or 'unknown'}")
            print(f"KLING_ACCOUNT_SKIPPED index={index} reason=HTTP_{status}", flush=True)
        except requests.Timeout as exc:
            raise RuntimeError(
                f"Kling account check timed out at key index {index}; stopping safely."
            ) from exc
        except requests.ConnectionError as exc:
            raise RuntimeError(
                f"Network connection failed while checking key index {index}; stopping safely."
            ) from exc
        except RuntimeError as exc:
            failures.append(f"key {index}: {str(exc)[:160]}")
            print(
                f"KLING_ACCOUNT_SKIPPED index={index} reason={str(exc)[:160]}",
                flush=True,
            )

    if not selections:
        detail = "; ".join(failures[-11:])
        raise RuntimeError(
            "None of the stored Kling accounts has an active supported Omni "
            f"video package. Checked {len(keys)} keys. {detail}"
        )
    return selections


class AccountFallbackError(RuntimeError):
    """Explicit per-account rejection received before a task was accepted."""


class OneFileHandler(BaseHTTPRequestHandler):
    file_path: Path
    secret_path: str

    def _send_headers(self) -> bool:
        if self.path != self.secret_path:
            self.send_error(404)
            return False
        try:
            stat = self.file_path.stat()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(stat.st_size))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return True
        except FileNotFoundError:
            self.send_error(404)
            return False

    def do_HEAD(self) -> None:
        self._send_headers()

    def do_GET(self) -> None:
        if not self._send_headers():
            return
        try:
            with self.file_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, fmt: str, *args: Any) -> None:
        # Don't log the one-time bearer-like path token.
        return


class TemporaryCharacterVideoTunnel:
    def __init__(self, video_file: Path, temp_dir: Path):
        self.video_file = video_file
        self.temp_dir = temp_dir
        self.secret_path = "/" + secrets.token_urlsafe(32) + ".mp4"
        self.httpd: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.url: str | None = None

    def __enter__(self) -> str:
        handler = type(
            "BoundOneFileHandler",
            (OneFileHandler,),
            {"file_path": self.video_file, "secret_path": self.secret_path},
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.server_thread.start()
        port = self.httpd.server_address[1]

        exe = self.temp_dir / "cloudflared.exe"
        if not exe.exists():
            download = requests.get(
                "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
                timeout=120,
            )
            download.raise_for_status()
            exe.write_bytes(download.content)

        self.process = subprocess.Popen(
            [str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output_queue: queue.Queue[str] = queue.Queue()

        def read_lines() -> None:
            assert self.process and self.process.stdout
            for line in self.process.stdout:
                output_queue.put(line)

        threading.Thread(target=read_lines, daemon=True).start()
        deadline = time.time() + 90
        domain_pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Temporary media tunnel exited before becoming ready.")
            try:
                line = output_queue.get(timeout=1)
            except queue.Empty:
                continue
            match = domain_pattern.search(line)
            if match:
                self.url = match.group(0) + self.secret_path
                check = requests.get(self.url, timeout=20, stream=True)
                if check.status_code != 200:
                    raise RuntimeError("Kling motion-reference tunnel health check failed.")
                check.close()
                return self.url
        raise RuntimeError("Timed out waiting for temporary motion-reference tunnel.")

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def split_content_segments(content: str, count: int) -> list[str]:
    """Split narration into balanced, non-overlapping visual beats."""
    text = re.sub(r"\s+", " ", content).strip()
    if not text:
        raise RuntimeError("A video script or content brief is required.")
    if len(text) > 10000:
        raise RuntimeError("Content brief is too long; limit it to 10,000 characters per batch.")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [x.strip() for x in sentences if x.strip()]
    if len(sentences) < count:
        words = text.split()
        chunk_size = max(1, (len(words) + count - 1) // count)
        return [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)][:count]

    chunks: list[list[str]] = [[] for _ in range(count)]
    for index, sentence in enumerate(sentences):
        target = min(count - 1, (index * count) // len(sentences))
        chunks[target].append(sentence)
    return [" ".join(x).strip() for x in chunks if x]


def build_content_prompt(content_segment: str, clip_index: int, clip_count: int) -> str:
    """Create a specific live-action visual plan from a narration segment."""
    return (
        f"Create a photorealistic live-action video, clip {clip_index} of {clip_count}, "
        "for a polished YouTube documentary/explainer. This must be moving footage, "
        "not a still image, slideshow, photo montage, collage, poster, or static avatar. "
        "VISUAL STORY TO DEPICT: "
        f"{content_segment} "
        "Treat the narration topic above as the actual subject of the footage. "
        "Show concrete, recognisable actions, places, objects, people, and consequences "
        "that directly illustrate the topic. Do not substitute unrelated stock-looking "
        "visuals or keep the host sitting at a desk. "
        "CHARACTER CONTINUITY: @host is the recurring main presenter from the approved "
        "character element. Preserve his exact face, age, skin tone, hairstyle, hairline, "
        "facial proportions, build, and recognisable wardrobe style. Never redesign, "
        "beautify, age, replace, duplicate, or morph him. Include @host naturally in "
        "the story where it makes visual sense; he may walk, investigate, demonstrate, "
        "interact with objects, react, or address the camera. When a cutaway is needed, "
        "return to the same @host in a coherent continuation. "
        "CAMERA AND MOTION: create a cinematic sequence with several distinct moving "
        "shots: an establishing view, active medium framing, and close detail/action "
        "coverage. Use purposeful tracking, pans, dolly movement, natural handheld "
        "movement where appropriate, realistic body mechanics, believable physics, "
        "natural lighting, realistic depth and environmental motion. Each shot must "
        "add new visual information and advance the story. Maintain spatial and "
        "temporal continuity across cuts. "
        "STYLE: high-end photoreal documentary footage, lifelike textures, natural "
        "motion blur, authentic environments, 16:9 landscape composition. "
        "No frozen frames, no Ken Burns movement on photos, no image-to-slideshow "
        "presentation, no title cards, no captions, no visible text, no logos, no "
        "watermarks, no artificial face changes, and no generated dialogue/music. "
        "Silent footage is required because narration and sound design are added later."
    )


def validate_character_video(path: Path) -> dict[str, Any]:
    """Validate the approved character seed clip against Kling video-element limits."""
    if not path.exists():
        raise RuntimeError(f"Approved Kling character reference video is missing: {path.name}")
    if path.stat().st_size > 200 * 1024 * 1024:
        raise RuntimeError("Character reference video exceeds Kling's 200 MB element limit.")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to validate the character reference video.")
    probe = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if probe.returncode != 0:
        raise RuntimeError("Could not inspect the approved character reference video.")
    payload = json.loads(probe.stdout)
    stream = (payload.get("streams") or [{}])[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    ratio = width / height if height else 0
    if min(width, height) < 1080 or min(abs(ratio - 16/9), abs(ratio - 9/16)) > 0.015:
        raise RuntimeError(
            f"Character video must be 1080p in 16:9 or 9:16; found {width}x{height}."
        )
    if not 3 <= duration <= 8:
        raise RuntimeError(
            f"Character video must be 3–8 seconds for video-character creation; found {duration:.2f}s."
        )
    return {"width": width, "height": height, "duration": duration, "bytes": path.stat().st_size}


def _element_id_from_payload(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") or {}
    elements = ((data.get("task_result") or {}).get("elements") or [])
    if elements and elements[0].get("element_id") is not None:
        return str(elements[0]["element_id"])
    direct = data.get("element_id")
    return str(direct) if direct is not None else None


def create_character_element(key: str, character_video_url: str) -> str:
    """Create or reuse one account-scoped Kling video-character element."""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    base = f"{API}/v1/general/advanced-custom-elements"
    fingerprint = __import__("hashlib").sha256(key.encode("utf-8")).hexdigest()[:12]
    external_id = f"astro-host-{fingerprint}"

    response = requests.get(f"{base}/{urllib.parse.quote(external_id)}", headers=headers, timeout=30)
    if response.status_code == 200:
        payload = response.json()
        if payload.get("code") == 0:
            existing = _element_id_from_payload(payload)
            status = ((payload.get("data") or {}).get("task_status") or "").lower()
            if existing and status in ("succeed", "succeeded"):
                print(f"CHARACTER_ELEMENT_REUSED account_fingerprint={fingerprint}", flush=True)
                return existing
            task_id = (payload.get("data") or {}).get("task_id")
            if task_id:
                poll_id = str(task_id)
            else:
                poll_id = external_id
        else:
            poll_id = ""
    elif response.status_code == 404:
        poll_id = ""
    elif response.status_code in (401, 402, 403):
        raise AccountFallbackError(
            f"Kling rejected this account during character-element lookup (HTTP {response.status_code})."
        )
    else:
        response.raise_for_status()
        poll_id = ""

    if not poll_id:
        body = {
            "element_name": "AstroTobbyHost",
            "element_description": (
                "Photoreal recurring male host; preserve exact face, skin tone, hairstyle and build"
            ),
            "reference_type": "video_refer",
            "element_video_list": {
                "refer_videos": [{"video_url": character_video_url}]
            },
            "external_task_id": external_id,
            "tag_list": [{"tag_id": "o_102"}],
        }
        created = requests.post(base, headers=headers, json=body, timeout=90)
        if created.status_code in (401, 402, 403):
            raise AccountFallbackError(
                f"Kling rejected this account during character-element creation (HTTP {created.status_code})."
            )
        created.raise_for_status()
        result = created.json()
        if result.get("code") != 0:
            message = str(result.get("message", ""))[:200]
            if any(term in message.lower() for term in ("unauthorized", "invalid", "balance", "quota")):
                raise AccountFallbackError(
                    f"Kling rejected this account during character-element creation: {message}"
                )
            raise RuntimeError(f"Kling character-element creation failed: {message}")
        poll_id = str((result.get("data") or {}).get("task_id") or external_id)
        print(f"CHARACTER_ELEMENT_CREATION_STARTED account_fingerprint={fingerprint}", flush=True)

    deadline = time.time() + 8 * 60
    while time.time() < deadline:
        check = requests.get(
            f"{base}/{urllib.parse.quote(poll_id)}",
            headers=headers,
            timeout=30,
        )
        if check.status_code in (401, 402, 403):
            raise AccountFallbackError(
                f"Kling rejected this account while retrieving its character element (HTTP {check.status_code})."
            )
        check.raise_for_status()
        payload = check.json()
        if payload.get("code") != 0:
            raise RuntimeError(
                f"Kling character-element query failed: {str(payload.get('message', ''))[:180]}"
            )
        data = payload.get("data") or {}
        status = str(data.get("task_status") or "").lower()
        element_id = _element_id_from_payload(payload)
        if status in ("succeed", "succeeded") and element_id:
            print(
                f"CHARACTER_ELEMENT_READY account_fingerprint={fingerprint}",
                flush=True,
            )
            return element_id
        if status == "failed":
            raise RuntimeError(
                f"Kling character-element creation failed: {str(data.get('task_status_msg', ''))[:180]}"
            )
        time.sleep(5)
    raise TimeoutError("Timed out waiting for Kling to create the reusable character element.")


def create_task(
    key: str,
    content_prompt: str,
    character_element_id: str,
    external_id: str,
    model_endpoint: str,
    model_name: str,
) -> str:
    """Submit a script-driven moving video using the persistent character element."""
    contents = [
        {"type": "prompt", "text": content_prompt},
        {
            "type": "element",
            "element_id": str(character_element_id),
            "id": "host",
        },
    ]
    settings: dict[str, Any] = {
        "resolution": "720p" if model_name == "kling-4.0-flash" else "1080p",
        "aspect_ratio": "16:9",
        "duration": 15,
        "audio": "off",
    }
    if model_name == "kling-3.0-omni":
        settings["multi_shot"] = True
        settings["shot_type"] = "intelligence"
    body = {
        "contents": contents,
        "settings": settings,
        "options": {
            "watermark_info": {"enabled": False},
            "external_task_id": external_id,
        },
    }
    response = requests.post(
        f"{API}{model_endpoint}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=90,
    )
    if response.status_code == 429:
        raise RuntimeError(
            "Kling returned HTTP 429 during task submission; stopping instead of "
            "rotating around a rate limit."
        )
    if response.status_code in (401, 403, 402):
        raise AccountFallbackError(
            f"Kling rejected this account before accepting a task (HTTP {response.status_code})."
        )
    if response.status_code >= 500:
        raise RuntimeError(
            f"Kling returned HTTP {response.status_code}; task acceptance is uncertain, "
            "so no second submission was attempted."
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        message = str(payload.get("message", ""))[:240]
        normalized = message.lower()
        if any(term in normalized for term in ("rate limit", "too many requests", "qps")):
            raise RuntimeError(
                f"Kling rate-limited task submission: {message}; no key rotation attempted."
            )
        if any(term in normalized for term in (
            "unauthorized", "invalid api key", "invalid token", "insufficient balance",
            "insufficient resource", "resource package", "no available resource",
            "quota exhausted", "account disabled",
        )):
            raise AccountFallbackError(
                f"Kling rejected this account before accepting a task: {message}"
            )
        raise RuntimeError(
            f"Kling task submission failed: code={payload.get('code')} message={message}"
        )
    task_id = (payload.get("data") or {}).get("id")
    if not task_id:
        raise RuntimeError("Kling accepted the request but returned no task ID.")
    return str(task_id)


def wait_for_task(key: str, task_id: str, out_file: Path) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    deadline = time.time() + 25 * 60
    while time.time() < deadline:
        response = requests.get(
            f"{API}/tasks",
            params={"task_ids": task_id},
            headers=headers,
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Kling task query failed: {payload.get('message', '')}")
        rows = payload.get("data") or []
        task = rows[0] if rows else {}
        status = task.get("status")
        if status == "succeeded":
            videos = [x for x in task.get("outputs", []) if x.get("type") == "video"]
            if not videos or not videos[0].get("url"):
                raise RuntimeError("Kling task succeeded but returned no video URL.")
            download = requests.get(videos[0]["url"], timeout=180)
            download.raise_for_status()
            out_file.write_bytes(download.content)
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg is required to strip Kling-generated audio before narration is added.")
            silent_file = out_file.with_name(out_file.stem + ".silent.mp4")
            stripped = subprocess.run(
                [ffmpeg, "-y", "-i", str(out_file), "-c:v", "copy", "-an", "-movflags", "+faststart", str(silent_file)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if stripped.returncode != 0:
                raise RuntimeError("Could not remove generated audio from the Kling reference clip.")
            silent_file.replace(out_file)
            return {
                "task_id": task_id,
                "status": status,
                "video_file": out_file.name,
                "bytes": out_file.stat().st_size,
                "duration": videos[0].get("duration"),
                "usage": task.get("usage"),
                "billing": task.get("billing", []),
            }
        if status == "failed":
            raise RuntimeError(
                f"Kling task failed: {str(task.get('message', ''))[:300]}"
            )
        time.sleep(10)
    raise TimeoutError(f"Kling task {task_id} did not finish within 25 minutes.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "animate"), default="check")
    parser.add_argument("--max-jobs", type=int, default=2)
    parser.add_argument("--content-brief", required=True)
    parser.add_argument(
        "--allow-cash-billing",
        action="store_true",
        help="Explicitly authorize Kling to use an account's monetary API balance.",
    )
    args = parser.parse_args()
    if args.max_jobs < 1 or args.max_jobs > 10:
        raise RuntimeError("max-jobs must be between 1 and 10.")
    segments = split_content_segments(args.content_brief, args.max_jobs)
    if max(len(segment) for segment in segments) > 900:
        raise RuntimeError(
            "A content segment is too long for a reliable Kling Omni prompt. "
            "Increase max_jobs so each clip contains a shorter part of the script."
        )

    raw_secret = os.environ.get("KLING_API_KEYS", "")
    keys = parse_key_pool(raw_secret)
    mask_keys(keys)
    if len(keys) != 11:
        print(f"KLING_KEY_POOL_COUNT={len(keys)} (expected 11)", flush=True)
    accounts = select_eligible_accounts(keys)
    print(
        f"KLING_ELIGIBLE_MODEL_PAIRS={len(accounts)}; "
        f"KEY_POOL_ENTRIES={len(keys)}; MODEL_FALLBACK=4.0, 4.0 Flash, 3.0 Omni",
        flush=True,
    )

    root_text = os.environ.get("INFLUENCER_ROOT", "").strip()
    root = Path(root_text).expanduser() if root_text else (
        Path.home() / "OneDrive" / "Desktop" / "INFLUENCER"
    )
    root = root.resolve()
    output_dir = root / "Kling_Animations"
    output_dir.mkdir(parents=True, exist_ok=True)
    character_video = root / CHARACTER_REFERENCE_VIDEO_NAME
    character_video_info = validate_character_video(character_video)
    print(
        f"CHARACTER_SEED_VIDEO_OK {character_video_info['width']}x"
        f"{character_video_info['height']} duration={character_video_info['duration']:.2f}s",
        flush=True,
    )
    require_billing_authorization(args.mode, args.allow_cash_billing)

    report_path = output_dir / "last_run_report.json"
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "key_pool_entries": len(keys),
        "account_rotation": True,
        "eligible_model_pairs": [
            {
                "credential_index": a["credential_index"],
                "model": a["model_name"],
            }
            for a in accounts
        ],
        "paid_balance_authorized": args.allow_cash_billing,
        "character_reference_video": character_video.name,
        "character_element": "Kling video-character element",
        "content_brief": args.content_brief,
        "scene_strategy": "script-driven live-action multi-shot video; no still-image output",
        "results": [],
    }
    if args.mode == "check":
        report["status"] = "preflight_passed_no_generation_tasks_submitted"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PREFLIGHT_PASSED; CHARACTER VIDEO VALID; NO GENERATION TASKS SUBMITTED.")
        return 0

    temp_dir = Path(os.environ.get("TEMP", str(output_dir)))
    external_prefix = "influencer-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    element_by_account: dict[int, str] = {}
    account_element_failures: set[int] = set()
    with TemporaryCharacterVideoTunnel(character_video, temp_dir) as character_video_url:
        print("CHARACTER_VIDEO_TUNNEL_READY; URL VALUE REDACTED.", flush=True)
        for index, segment in enumerate(segments, start=1):
            content_prompt = build_content_prompt(segment, index, len(segments))
            external_id = f"{external_prefix}-{index}"
            task_id = None
            selected_account = None
            account_errors: list[str] = []

            # Attempt accounts/models in order. An account-specific rejection before
            # task acceptance permits fallback; ambiguous timeouts/5xx and 429 do not.
            for account in accounts:
                account_index = int(account["credential_index"])
                if account_index in account_element_failures:
                    continue
                try:
                    if account_index not in element_by_account:
                        element_by_account[account_index] = create_character_element(
                            account["key"], character_video_url
                        )
                    task_id = create_task(
                        account["key"],
                        content_prompt,
                        element_by_account[account_index],
                        external_id,
                        account["endpoint"],
                        account["model_name"],
                    )
                    selected_account = account
                    break
                except AccountFallbackError as exc:
                    account_element_failures.add(account_index)
                    account_errors.append(
                        f"key {account_index}: {str(exc)[:160]}"
                    )
                    print(
                        f"KLING_ACCOUNT_FALLBACK index={account_index} "
                        f"reason={str(exc)[:160]}",
                        flush=True,
                    )
            if task_id is None or selected_account is None:
                raise RuntimeError(
                    "All eligible Kling accounts rejected the character element or "
                    "video task before acceptance. " + "; ".join(account_errors)
                )

            active_key = selected_account["key"]
            report["last_credential_index"] = selected_account["credential_index"]
            report["last_model"] = selected_account["model_name"]
            report["results"].append({
                "segment": index,
                "content": segment,
                "task_id": task_id,
                "credential_index": selected_account["credential_index"],
                "model": selected_account["model_name"],
                "status": "submitted",
            })
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"KLING_TASK_SUBMITTED clip={index} "
                f"key_index={selected_account['credential_index']} "
                f"model={selected_account['model_name']} task_id={task_id}",
                flush=True,
            )

            out_file = output_dir / f"content_scene_{index:02d}.mp4"
            try:
                result = wait_for_task(active_key, task_id, out_file)
            except Exception as exc:
                report["status"] = "failed"
                report["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                raise
            report["results"][-1].update(result)
            report["results"][-1]["status"] = "completed"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"KLING_TASK_COMPLETE clip={index} "
                f"output={out_file.name} duration={result.get('duration')}",
                flush=True,
            )
            charge_types = {
                str(item.get("charge_type", "")).lower()
                for item in (result.get("billing") or [])
                if isinstance(item, dict)
            }
            if "cash" in charge_types:
                report["warning"] = (
                    "Kling used monetary API balance; this was explicitly authorized for this run."
                )
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print("CASH_BALANCE_USED; PAID_USAGE_RECORDED_IN_REPORT.", flush=True)

    report["status"] = "completed"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"ANIMATION_BATCH_COMPLETE count={len(report['results'])}")
    print(f"REPORT={report_path}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Keep error messages useful but never include headers, request objects, or secrets.
        print(f"PIPELINE_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
