#!/usr/bin/env python3
"""Read-only sequential preflight for the stored Kling API key pool."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.influencer_kling_pipeline import (  # noqa: E402
    mask_keys,
    parse_key_pool,
    select_eligible_accounts,
)


def main() -> int:
    keys = parse_key_pool(os.environ.get("KLING_API_KEYS", ""))
    mask_keys(keys)
    print(f"SECRET_PRESENT=yes; credential_entries={len(keys)}")
    if len(keys) != 11:
        print(f"WARNING_EXPECTED_11_KEYS; found={len(keys)}")
    accounts = select_eligible_accounts(keys)
    print(f"KLING_ELIGIBLE_ACCOUNTS={len(accounts)}")
    first = accounts[0]
    print(f"FIRST_READY_KEY_INDEX={first['credential_index']}")
    print(f"FIRST_READY_MODEL={first['model_name']}")
    print("MODEL_CANDIDATES=Kling 4.0 and Kling 3.0 Omni")
    print("NO_VIDEO_TASK_SUBMITTED; NO_GENERATION_CREDITS_SPENT.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"KLING_API_PREFLIGHT=ERROR ({type(exc).__name__}): {str(exc)[:240]}")
        raise SystemExit(1)
