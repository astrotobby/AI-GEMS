#!/usr/bin/env python3
"""Read-only preflight over the complete configured Kling API key pool."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.influencer_kling_pipeline import (
    mask_keys,
    parse_key_pool,
    select_eligible_accounts,
)


def main() -> int:
    keys = parse_key_pool(os.environ.get("KLING_API_KEYS", ""))
    mask_keys(keys)
    print(f"SECRET_ENTRIES={len(keys)}")
    if len(keys) != 11:
        print(f"WARNING_EXPECTED_11_KEYS; found={len(keys)}")
    accounts = select_eligible_accounts(keys)
    print(f"ELIGIBLE_ACCOUNT_COUNT={len(accounts)}")
    print(f"FIRST_ELIGIBLE_KEY_INDEX={accounts[0]['credential_index']}")
    print(f"FIRST_ELIGIBLE_MODEL={accounts[0]['model_name']}")
    print("SUPPORTED_MODELS=Kling 4.0; Kling 3.0 Omni")
    print("GENERATION_TASKS_SUBMITTED=0")
    print("PREFLIGHT_RESULT=ELIGIBLE")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write("eligible=true\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"PREFLIGHT_ERROR: {type(error).__name__}: {str(error)[:240]}", file=sys.stderr)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("eligible=false\n")
        raise SystemExit(1)
