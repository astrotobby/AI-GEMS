"""Read-only YouTube channel credential validation using AI-GEMS' token/client cache.

This script never uploads, edits, or schedules a video. Token values are never logged.
Run from the whop-clipper directory so clipper.publish can read work/yt_tokens/*.json.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from clipper import publish


OWNER_BY_CHANNEL = {
    "ASTRO_TOBBY": "goodlucktobby@gmail.com",
    "DIJITAL_NOMAD": "chanzera3@gmail.com",
}


def channel_identity(creds: tuple[str, str, str]) -> tuple[str, str]:
    token = publish._yt_access_token(creds)
    req = urllib.request.Request(
        "https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true",
        headers={"Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        items = json.load(response).get("items") or []
    if not items:
        raise RuntimeError("channels.list(mine=true) returned no channel")
    item = items[0]
    return str(item.get("id") or ""), str((item.get("snippet") or {}).get("title") or "")


def main() -> int:
    cfg_path = Path("config.toml")
    if not cfg_path.exists():
        raise SystemExit("Run this script from the whop-clipper directory")

    import tomllib
    with cfg_path.open("rb") as handle:
        cfg = tomllib.load(handle)

    targets = [target for target in cfg.get("targets", []) if target.get("platform") == "youtube"]
    if not targets:
        raise SystemExit("No YouTube targets are configured in config.toml")

    failures = 0
    for target in targets:
        expected_title = str(target.get("channel") or "").strip()
        slug = publish._slug(target)
        owner = OWNER_BY_CHANNEL.get(slug, "owner account supplied by user")
        try:
            creds = publish._yt_creds(target)
            if not creds:
                raise publish.yt_not_configured(target)
            verified_title = publish.yt_verify_channel(target, creds)
            channel_id, actual_title = channel_identity(creds)
            if actual_title.strip().casefold() != expected_title.casefold():
                raise RuntimeError(
                    f"expected channel {expected_title!r}, authenticated channel is {actual_title!r}"
                )
            print(
                f"VERIFIED channel={verified_title!r} channel_id={channel_id!r} "
                f"owner_account_mapping={owner!r}"
            )
        except Exception as exc:  # noqa: never expose the credential material
            failures += 1
            print(f"NOT VERIFIED channel={expected_title!r} owner_account_mapping={owner!r}: {str(exc)[:280]}")

    if failures:
        print(f"Credential validation failed for {failures} YouTube channel(s). No uploads were attempted.")
        return 1
    print("Both YouTube channel credentials validated successfully. No uploads were attempted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
