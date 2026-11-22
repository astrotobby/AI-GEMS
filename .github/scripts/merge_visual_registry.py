"""Merge independently rendered cross-channel visual-history registries without dropping history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read visual registry {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError(f"Visual registry {path} must contain an object with an items array")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw["items"]):
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise ValueError(f"Visual registry {path} has an invalid item at index {index}")
        hashes = item.get("visual_hashes", [])
        if hashes is not None and not isinstance(hashes, list):
            raise ValueError(f"Visual registry {path} item {item['id']} has invalid visual_hashes")
        items.append(dict(item))
    return items


def _timestamp(item: dict[str, Any]) -> float:
    try:
        return float(item.get("used_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def merge_items(*registries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union by permanent asset ID; union every fingerprint for duplicate IDs."""
    merged: dict[str, dict[str, Any]] = {}
    for items in registries:
        for item in items:
            asset_id = str(item["id"]).strip()
            current = merged.get(asset_id)
            if current is None:
                merged[asset_id] = dict(item)
                continue

            # Keep the newest non-empty metadata, while never discarding hashes
            # learned by another render or by the historical ledger seeder.
            item_priority = (_timestamp(item), json.dumps(item, ensure_ascii=False, sort_keys=True))
            current_priority = (_timestamp(current), json.dumps(current, ensure_ascii=False, sort_keys=True))
            newer, older = (item, current) if item_priority >= current_priority else (current, item)
            combined = dict(older)
            combined.update(newer)
            fingerprints = set(str(value) for value in (current.get("visual_hashes") or []) if value)
            fingerprints.update(str(value) for value in (item.get("visual_hashes") or []) if value)
            combined["visual_hashes"] = sorted(fingerprints)
            merged[asset_id] = combined

    return sorted(merged.values(), key=lambda item: (-_timestamp(item), str(item["id"])))


def merge_files(inputs: list[Path], output: Path) -> int:
    if not inputs:
        raise ValueError("At least one input registry is required")
    registries = [_load(path) for path in inputs]
    items = merge_items(*registries)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"items": items}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(f"Merged {len(inputs)} registries into {output}: {len(items)} unique permanent asset IDs")
    return len(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        merge_files(args.inputs, args.output)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Refusing to write an incomplete visual registry: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
