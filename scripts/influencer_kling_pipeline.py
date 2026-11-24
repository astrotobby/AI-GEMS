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
from PIL import Image

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
MOTION_NAME = "kling_20261124_VIDEO_Subtle_slo_4117_0.mp4"
IDENTITY_IMAGES = [
    "IMG_20261124_135816.jpg",
    "IMG_20261124_135845.jpg",
]
APPROVED_SCENES = [
    "master_studio_reference.jpg",
    "office_studio_reference.jpg",
]


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
    if "kling4.0" in compact:
        result.append(("kling-4.0", "/omni-video/kling-4.0"))
    if "kling4.0flash" in compact:
        result.append(("kling-4.0-flash", "/omni-video/kling-4.0-flash"))
    if any(token in compact for token in ("kling3.0omni", "klingv3omni")):
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


class TemporaryMotionTunnel:
    def __init__(self, motion_file: Path, temp_dir: Path):
        self.motion_file = motion_file
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
            {"file_path": self.motion_file, "secret_path": self.secret_path},
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


def validate_image(path: Path) -> tuple[int, int]:
    if not path.exists():
        raise RuntimeError(f"Approved scene image is missing: {path.name}")
    if path.stat().st_size > 30 * 1024 * 1024:
        raise RuntimeError(f"Image exceeds Kling's 30 MB limit: {path.name}")
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        width, height = im.size
    if min(width, height) < 300:
        raise RuntimeError(f"Image must be at least 300 px in each dimension: {path.name}")
    ratio = width / height
    if ratio < 0.4 or ratio > 2.5:
        raise RuntimeError(f"Image aspect ratio is outside Kling's supported range: {path.name}")
    return width, height


def image_data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def create_task(
    key: str,
    scene_file: Path,
    identity_one: Path,
    identity_two: Path,
    motion_url: str,
    external_id: str,
    model_endpoint: str,
) -> str:
    scene_data = image_data_url(scene_file)
    identity_one_data = image_data_url(identity_one)
    identity_two_data = image_data_url(identity_two)
    prompt = (
        "Create a photorealistic five-second presenter animation. The person in "
        "@identity_a and @identity_b is the ONLY identity authority. Preserve his "
        "actual face exactly: same face shape and proportions, eyes, nose, lips, "
        "jawline, skin tone, short haircut and natural hairline, and subtle facial "
        "hair. Do not beautify, broaden his face, thicken facial hair, age him, or "
        "change his identity. @scene is the required opening composition and set; "
        "keep its clothing, framing, desk and background. @motion is ONLY a guide "
        "for restrained presenter movement and pacing; do not copy the person, "
        "face, clothes, or background from @motion. Animate subtle natural speech "
        "mouth movement, two soft blinks, a gentle head nod and minimal shoulder "
        "movement as if narrating a YouTube explainer. Keep the camera locked, "
        "natural human proportions, one continuous take, no cuts, no zoom, no "
        "new people, no exaggerated gestures, no face warping, no added text, "
        "no generated dialogue or music. Silent footage for later narration."
    )
    body = {
        "contents": [
            {"type": "prompt", "text": prompt},
            {"type": "feature_video", "url": motion_url, "id": "motion"},
            {"type": "first_frame", "url": scene_data, "id": "scene"},
            {"type": "refer_image", "url": identity_one_data, "id": "identity_a"},
            {"type": "refer_image", "url": identity_two_data, "id": "identity_b"},
        ],
        "settings": {
            "resolution": "720p",
            "aspect_ratio": "16:9",
            "duration": 5,
            **(
                {"audio": "off", "multi_shot": False}
                if "kling-3.0-omni" in model_endpoint
                else {}
            ),
        },
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
    parser.add_argument(
        "--allow-cash-billing",
        action="store_true",
        help="Explicitly authorize Kling to use the selected account's monetary API balance.",
    )
    args = parser.parse_args()
    if args.max_jobs < 1 or args.max_jobs > 2:
        raise RuntimeError("max-jobs must be 1 or 2 for the initial controlled batch.")

    raw_secret = os.environ.get("KLING_API_KEYS", "")
    keys = parse_key_pool(raw_secret)
    mask_keys(keys)
    if len(keys) != 11:
        print(f"KLING_KEY_POOL_COUNT={len(keys)} (expected 11)", flush=True)
    accounts = select_eligible_accounts(keys)
    print(
        f"KLING_ELIGIBLE_ACCOUNTS={len(accounts)}; "
        f"KEY_POOL_ENTRIES={len(keys)}; MODEL_FALLBACK=Kling 4.0 then Kling 3.0 Omni",
        flush=True,
    )

    root_text = os.environ.get("INFLUENCER_ROOT", "").strip()
    root = Path(root_text).expanduser() if root_text else (Path.home() / "OneDrive" / "Desktop" / "INFLUENCER")
    root = root.resolve()
    approved = root / "Kling_Animations" / "ApprovedImages"
    output_dir = root / "Kling_Animations"
    output_dir.mkdir(parents=True, exist_ok=True)
    scenes = [approved / name for name in APPROVED_SCENES]
    identities = [root / name for name in IDENTITY_IMAGES]
    motion_file = root / MOTION_NAME

    for path in scenes:
        validate_image(path)
    for path in identities:
        validate_image(path)
    if not motion_file.exists() or motion_file.stat().st_size > 300 * 1024 * 1024:
        raise RuntimeError("The approved local Kling motion-reference video is missing or exceeds 300 MB.")

    print(f"LOCAL_ASSETS_OK scenes={len(scenes)} identity_photos={len(identities)} motion_video=yes")
    print(f"SELECTED_CREDENTIAL_INDEX={args.credential_index}; total_stored_entries={len(keys)}; one_account_for_this_run=yes")
    first_account = accounts[0]
    pack = first_account["pack"]
    print(
        "API_RESOURCE_PACKAGE_OK "
        f"credential_index={first_account['credential_index']} "
        f"model={first_account['model_name']} "
        f"tier={pack.get('resource_pack_tier')} "
        f"status={pack.get('status')} "
        f"remaining_units={pack.get('remaining_quantity')}"
    )
    print("NOTE: Kling states resource-package remaining-quantity statistics may be delayed by up to 12 hours.")
    require_billing_authorization(args.mode, args.allow_cash_billing)

    report_path = output_dir / "last_run_report.json"
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "credential_index": first_account["credential_index"],
        "account_rotation": True,
        "eligible_accounts": [
            {"credential_index": a["credential_index"], "model": a["model_name"]}
            for a in accounts
        ],
        "paid_balance_authorized": args.allow_cash_billing,
        "scene_files": [x.name for x in scenes],
        "identity_files": [x.name for x in identities],
        "motion_reference": motion_file.name,
        "results": [],
    }
    if args.mode == "check":
        report["status"] = "preflight_passed_no_credits_spent"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PREFLIGHT_PASSED; NO GENERATION TASKS SUBMITTED; NO CREDITS SPENT.")
        return 0

    temp_dir = Path(os.environ.get("TEMP", str(output_dir)))
    external_prefix = "influencer-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with TemporaryMotionTunnel(motion_file, temp_dir) as motion_url:
        print("TEMPORARY_MOTION_REFERENCE_TUNNEL_READY; URL VALUE REDACTED.")
        for index, scene in enumerate(scenes[: args.max_jobs], start=1):
            external_id = f"{external_prefix}-{index}"
            task_id = None
            selected_account = None
            account_errors: list[str] = []
            # Rotate only after a definite per-account rejection received before
            # Kling returns a task ID. Never retry on 429 or ambiguous timeouts/5xx.
            for account in accounts:
                try:
                    task_id = create_task(
                        account["key"], scene, identities[0], identities[1],
                        motion_url, external_id, account["endpoint"]
                    )
                    selected_account = account
                    break
                except AccountFallbackError as exc:
                    account_errors.append(
                        f"key {account['credential_index']}: {str(exc)[:160]}"
                    )
                    print(
                        f"KLING_SUBMISSION_FALLBACK index={account['credential_index']} "
                        f"reason={str(exc)[:160]}",
                        flush=True,
                    )
            if task_id is None or selected_account is None:
                raise RuntimeError(
                    "All eligible Kling accounts explicitly rejected the task before "
                    "acceptance. " + "; ".join(account_errors)
                )
            active_key = selected_account["key"]
            report["credential_index"] = selected_account["credential_index"]
            report["selected_model"] = selected_account["model_name"]
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"KLING_TASK_SUBMITTED scene={scene.name} "
                f"key_index={selected_account['credential_index']} "
                f"model={selected_account['model_name']} task_id={task_id}"
            )
            out_file = output_dir / f"{scene.stem}_speaking.mp4"
            try:
                result = wait_for_task(active_key, task_id, out_file)
            except Exception as exc:
                report["status"] = "failed"
                report["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                raise
            report["results"].append(result)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            charge_types = {
                str(item.get("charge_type", "")).lower()
                for item in (result.get("billing") or [])
                if isinstance(item, dict)
            }
            print(
                f"KLING_TASK_COMPLETE scene={scene.name} "
                f"output={out_file.name} duration={result.get('duration')}"
            )
            if "cash" in charge_types:
                report["warning"] = "Kling used monetary API balance; this was explicitly authorized for this run."
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print("CASH_BALANCE_USED; PAID_USAGE_RECORDED_IN_REPORT.")
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
