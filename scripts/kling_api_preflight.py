#!/usr/bin/env python3
"""Zero-credit check of one explicitly selected Kling API credential."""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

API = "https://api-singapore.klingai.com"


def parse_keys(raw: str) -> list[str]:
    if not raw.strip():
        raise RuntimeError("KLING_API_KEYS secret is empty.")
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            values = [str(item) for item in value]
        elif isinstance(value, dict):
            values = [str(item) for item in value.values() if isinstance(item, str)]
        elif isinstance(value, str):
            values = value.splitlines()
        else:
            values = []
    except json.JSONDecodeError:
        values = re.split(r"[\r\n,;]+", raw)
    return list(dict.fromkeys(x.strip().strip('"').strip("'") for x in values if x.strip()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credential-index", type=int, default=1)
    args = parser.parse_args()

    keys = parse_keys(os.environ.get("KLING_API_KEYS", ""))
    for key in keys:
        print(f"::add-mask::{key}")
    if not 1 <= args.credential_index <= len(keys):
        raise RuntimeError(
            f"credential_index must be 1..{len(keys)}; found {len(keys)} secret entries."
        )
    key = keys[args.credential_index - 1]

    now_ms = int(time.time() * 1000)
    response = requests.get(
        f"{API}/account/costs",
        params={
            "start_time": now_ms - 90 * 24 * 60 * 60 * 1000,
            "end_time": now_ms,
        },
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"Kling returned code={payload.get('code')}: "
            f"{str(payload.get('message', ''))[:180]}"
        )
    data = payload.get("data") or {}
    if data.get("code") not in (None, 0):
        raise RuntimeError(
            f"Kling account-costs response code={data.get('code')}: "
            f"{str(data.get('msg', ''))[:180]}"
        )

    packs = data.get("resource_pack_subscribe_infos") or []
    video_packs = [
        p for p in packs
        if "video" in str(p.get("resource_pack_name", "")).lower()
    ]
    eligible = [
        p for p in video_packs
        if str(p.get("resource_pack_tier", "")).lower() == "flagship"
        and str(p.get("status", "")).lower() == "online"
        and float(p.get("remaining_quantity") or 0) > 0
        and "kling 4.0" in str(p.get("resource_pack_applicable_models", "")).lower()
    ]
    print(f"SECRET_ENTRIES={len(keys)}")
    print(f"SELECTED_CREDENTIAL_INDEX={args.credential_index}")
    print(f"VIDEO_PACKS_FOUND={len(video_packs)}")
    print(f"FLAGSHIP_KLING_4_PACKS_ACTIVE={len(eligible)}")
    for i, pack in enumerate(eligible, start=1):
        expiry = pack.get("invalid_time")
        expiry_text = (
            datetime.fromtimestamp(expiry / 1000, timezone.utc).isoformat()
            if expiry else "not specified"
        )
        print(
            f"ELIGIBLE_PACK_{i}: name={pack.get('resource_pack_name')} "
            f"tier={pack.get('resource_pack_tier')} status={pack.get('status')} "
            f"remaining_units={pack.get('remaining_quantity')} "
            f"expires_utc={expiry_text}"
        )
    print("BALANCE_FALLBACK=DISABLED")
    print("GENERATION_TASKS_SUBMITTED=0")
    if not eligible:
        print("PREFLIGHT_RESULT=BLOCKED_NO_ACTIVE_FLAGSHIP_KLING_4_VIDEO_PACK")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("eligible=false\n")
        return 0
    print(
        "PREFLIGHT_RESULT=ELIGIBLE; Kling reports remaining-quantity statistics "
        "may be delayed by up to 12 hours."
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write("eligible=true\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"PREFLIGHT_ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
