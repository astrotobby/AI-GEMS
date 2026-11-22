"""Offline tests for safe cross-run visual registry reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from merge_visual_registry import merge_files, merge_items


class MergeVisualRegistryTests(unittest.TestCase):
    def test_union_preserves_distinct_ids_from_both_renders(self):
        result = merge_items(
            [{"id": "asset-a", "visual_hashes": ["aaaa"], "used_at": 1}],
            [{"id": "asset-b", "visual_hashes": ["bbbb"], "used_at": 2}],
        )
        self.assertEqual({item["id"] for item in result}, {"asset-a", "asset-b"})

    def test_same_asset_unions_hashes_and_keeps_newest_metadata(self):
        result = merge_items(
            [{"id": "asset-a", "title": "older", "visual_hashes": ["aaaa"], "used_at": 1}],
            [{"id": "asset-a", "title": "newer", "visual_hashes": ["bbbb"], "used_at": 2}],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "newer")
        self.assertEqual(set(result[0]["visual_hashes"]), {"aaaa", "bbbb"})

    def test_duplicate_hashless_ledger_record_does_not_erase_fingerprints(self):
        result = merge_items(
            [{"id": "asset-a", "visual_hashes": ["aaaa", "bbbb"], "used_at": 2}],
            [{"id": "asset-a", "visual_hashes": [], "used_at": 3}],
        )
        self.assertEqual(set(result[0]["visual_hashes"]), {"aaaa", "bbbb"})

    def test_merge_is_deterministic_and_sorted_newest_first(self):
        first = [{"id": "b", "used_at": 1}, {"id": "a", "used_at": 4}]
        second = [{"id": "c", "used_at": 2}]
        self.assertEqual(
            [item["id"] for item in merge_items(first, second)],
            ["a", "c", "b"],
        )
        self.assertEqual(merge_items(first, second), merge_items(second, first))

    def test_bad_registry_is_rejected_without_overwriting_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root / "bad.json"
            output = root / "out.json"
            bad.write_text(json.dumps({"items": [{"id": ""}]}), encoding="utf-8")
            output.write_text('{"items":[{"id":"keep-me"}]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_files([bad], output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["items"][0]["id"], "keep-me")

    def test_same_timestamp_conflicts_resolve_deterministically(self):
        left = [{"id": "asset-a", "provider": "A", "visual_hashes": ["aaaa"], "used_at": 1}]
        right = [{"id": "asset-a", "provider": "B", "visual_hashes": ["bbbb"], "used_at": 1}]
        self.assertEqual(merge_items(left, right), merge_items(right, left))

    def test_file_merge_unions_every_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            left, right, output = root / "left.json", root / "right.json", root / "out.json"
            left.write_text(json.dumps({"items": [{"id": "left", "visual_hashes": ["11"]}]}), encoding="utf-8")
            right.write_text(json.dumps({"items": [{"id": "right", "visual_hashes": ["22"]}]}), encoding="utf-8")
            count = merge_files([left, right], output)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(count, 2)
            self.assertEqual({item["id"] for item in data["items"]}, {"left", "right"})


if __name__ == "__main__":
    unittest.main()
