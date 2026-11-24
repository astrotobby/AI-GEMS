#!/usr/bin/env python3
"""Read-only preflight for the first designated Kling API credential.

The key is sourced only from GitHub Actions KLING_API_KEYS. No value is printed,
no account rotation is performed, and no video-generation task is submitted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repository root importable when this file is invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.influencer_kling_pipeline import (
    account_video_pack,
    mask_keys,
    parse_key_pool,
    select_one_key,
)


def main() -> int:
    raw = os.environ.get("KLING_API_KEYS", "")
    keys = parse_key_pool(raw)
    mask_keys(keys)
    print(f"SECRET_PRESENT=yes; credential_entries={len(keys)}")
    # Use only the first designated account; do not pool or rotate across accounts.
    key = select_one_key(keys, 1)
    try:
        pack = account_video_pack(key)
    except RuntimeError as exc:
        message = str(exc)
        if "no active Flagship" in message:
            print("KLLING_API_PREFLIGHT=AUTHENTICATED_BUT_NO_ELIGIBLE_FLAGSHIP_PACKAGE")
        else:
            print("KLING_API_PREFLIGHT=BLOCKED")
        print(f"DETAIL={message[:240]}")
        return 4
    print("KLING_API_PREFLIGHT=READY")
    print(f"RESOURCE_PACK_TIER={pack.get('resource_pack_tier')}")
    print(f"RESOURCE_PACK_STATUS={pack.get('status')}")
    print(f"REMAINING_UNITS={pack.get('remaining_quantity')}")
    print("NO_VIDEO_TASK_SUBMITTED; NO_GENERATION_CREDITS_SPENT.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"KLING_API_PREFLIGHT=ERROR ({type(exc).__name__})")
        raise SystemExit(1)
