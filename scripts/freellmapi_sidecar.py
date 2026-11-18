#!/usr/bin/env python3
"""Start a loopback-only, disposable FreeLLMAPI router for manual dry-runs.

The service is intentionally not started for scheduled/live publishing. It uses
only anonymous OVH and AI Horde providers; Kilo is explicitly disabled because
its provider notes say prompts and outputs may be used for training.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONTAINER = "freellmapi-longform"
IMAGE = "ghcr.io/tashfeenahmed/freellmapi:v0.16.34"
BASE_URL = "http://127.0.0.1:3001"
MODEL_COUNT_REQUIRED = 1


def docker(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def request_json(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
    bearer: str | None = None,
    timeout: int = 15,
) -> dict:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"FreeLLMAPI returned HTTP {exc.code} for {path}") from None


def write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def cleanup() -> None:
    docker(["rm", "--force", CONTAINER], timeout=30)
    temp_root = os.environ.get("RUNNER_TEMP")
    if temp_root:
        shutil.rmtree(Path(temp_root) / "freellmapi-longform", ignore_errors=True)


def bootstrap() -> bool:
    if os.environ.get("FREELLMAPI_ENABLED", "true").strip().lower() not in {"1", "true", "yes"}:
        print("FreeLLMAPI sidecar disabled by configuration.")
        return False

    runner_temp = os.environ.get("RUNNER_TEMP")
    github_env = os.environ.get("GITHUB_ENV")
    if not runner_temp or not github_env:
        print("::warning::FreeLLMAPI skipped: this bootstrap is intended for GitHub Actions.")
        return False

    work_dir = Path(runner_temp) / "freellmapi-longform"
    work_dir.mkdir(parents=True, exist_ok=True)
    config_path = work_dir / "config.json"
    env_path = work_dir / "container.env"
    admin_password = secrets.token_urlsafe(36)
    admin_email = f"longform-{secrets.token_hex(6)}@example.com"
    encryption_key = secrets.token_hex(32)

    # No provider keys are injected. These are the two documented anonymous
    # chat providers. Kilo gets a disabled row so FreeLLMAPI's first-install
    # keyless auto-enable cannot turn on a provider that logs prompts/outputs
    # for training.
    config = {
        "admin": {"email": admin_email, "password": admin_password},
        "keys": [
            {"platform": "ovh", "label": "anonymous-dry-run"},
            {"platform": "aihorde", "label": "anonymous-dry-run"},
            {
                "platform": "kilo",
                "key": "disabled-by-policy",
                "label": "excluded-privacy-policy",
                "enabled": False,
            },
        ],
        "routing": {"strategy": "reliable"},
    }
    write_private(config_path, json.dumps(config, separators=(",", ":")) + "\n")
    write_private(
        env_path,
        "\n".join(
            [
                f"ENCRYPTION_KEY={encryption_key}",
                "NODE_ENV=production",
                "PORT=3001",
                "FREEAPI_CONFIG_PATH=/app/freellmapi.config.json",
                "",
            ]
        ),
    )

    try:
        docker(["rm", "--force", CONTAINER], timeout=30)
        pulled = docker(["pull", IMAGE], timeout=420)
        if pulled.returncode != 0:
            raise RuntimeError("pinned FreeLLMAPI container image could not be pulled")

        started = docker(
            [
                "run",
                "--detach",
                "--name",
                CONTAINER,
                "--publish",
                "127.0.0.1:3001:3001",
                "--env-file",
                str(env_path),
                "--mount",
                f"type=bind,source={config_path},target=/app/freellmapi.config.json,readonly",
                IMAGE,
            ],
            timeout=90,
        )
        if started.returncode != 0:
            raise RuntimeError("FreeLLMAPI container failed to start")

        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            try:
                request_json("/api/ping", timeout=5)
                break
            except Exception:
                time.sleep(3)
        else:
            raise RuntimeError("FreeLLMAPI health check did not become ready")

        login = request_json(
            "/api/auth/login",
            method="POST",
            payload={"email": admin_email, "password": admin_password},
        )
        session = login.get("token")
        if not session:
            raise RuntimeError("FreeLLMAPI dashboard session could not be created")

        profile = request_json(
            "/api/client-profiles",
            method="POST",
            payload={"name": "longform-dry-run"},
            bearer=session,
        )
        api_key = profile.get("key")
        if not api_key:
            raise RuntimeError("FreeLLMAPI client key could not be created")

        # Do not hand the model an API key unless this fresh catalog actually
        # contains models. It avoids routing into an empty/free catalog snapshot.
        models_deadline = time.monotonic() + 180
        model_count = 0
        while time.monotonic() < models_deadline:
            try:
                model_list = request_json("/v1/models", bearer=api_key, timeout=20)
                model_count = len(model_list.get("data") or [])
                if model_count >= MODEL_COUNT_REQUIRED:
                    break
            except Exception:
                pass
            time.sleep(5)
        if model_count < MODEL_COUNT_REQUIRED:
            raise RuntimeError("FreeLLMAPI catalog did not expose any usable models")

        print(f"::add-mask::{api_key}")
        with open(github_env, "a", encoding="utf-8") as stream:
            stream.write(f"FREELLMAPI_API_KEY={api_key}\n")
            stream.write(f"FREELLMAPI_BASE_URL={BASE_URL}/v1\n")
            stream.write("FREELLMAPI_MODEL=auto:smart\n")

        # Once the server has loaded its boot-time config, remove the mounted
        # host copies. The database and generated client key remain only inside
        # the disposable container until the always() cleanup step.
        config_path.unlink(missing_ok=True)
        env_path.unlink(missing_ok=True)
        print(
            f"FreeLLMAPI ready for this dry-run: {model_count} catalog models visible; "
            "OVH and AI Horde anonymous routes only; Kilo disabled."
        )
        return True
    except Exception as exc:
        print(
            "::warning::FreeLLMAPI sidecar unavailable; existing Gemini/OpenCode/"
            f"Anthropic fallbacks remain active ({type(exc).__name__})."
        )
        docker(["rm", "--force", CONTAINER], timeout=30)
        return False
    finally:
        config_path.unlink(missing_ok=True)
        env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        cleanup()
    else:
        bootstrap()
