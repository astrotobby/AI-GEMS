import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "influencer_kling_pipeline.py"
SPEC = importlib.util.spec_from_file_location("influencer_kling_pipeline", SCRIPT)
PIPELINE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PIPELINE)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class KlingPipelineTests(unittest.TestCase):
    def test_parse_newline_key_pool_without_logging_values(self):
        keys = PIPELINE.parse_key_pool("alpha-key\nbeta-key\nalpha-key\n")
        self.assertEqual(keys, ["alpha-key", "beta-key"])

    def test_parse_json_key_pool(self):
        keys = PIPELINE.parse_key_pool(json.dumps({"keys": ["one", "two"]}))
        self.assertEqual(keys, ["one", "two"])

    def test_parse_comma_delimited_key_pool(self):
        keys = PIPELINE.parse_key_pool("one,two,three")
        self.assertEqual(keys, ["one", "two", "three"])

    def test_selects_one_explicit_key_only(self):
        keys = ["first", "second", "third"]
        self.assertEqual(PIPELINE.select_one_key(keys, 2), "second")
        with self.assertRaises(RuntimeError):
            PIPELINE.select_one_key(keys, 0)
        with self.assertRaises(RuntimeError):
            PIPELINE.select_one_key(keys, 4)

    @patch.object(PIPELINE.requests, "get")
    def test_accepts_active_flagship_kling_4_pack(self, mocked_get):
        mocked_get.return_value = FakeResponse({
            "code": 0,
            "data": {
                "resource_pack_subscribe_infos": [{
                    "resource_pack_tier": "flagship",
                    "resource_pack_applicable_models": "Available only for Kling 4.0 and Kling 4.0 Flash",
                    "remaining_quantity": 12,
                    "status": "online"
                }]
            }
        })
        result = PIPELINE.account_video_pack("masked-test-key")
        self.assertEqual(result["resource_pack_tier"], "flagship")
        self.assertEqual(result["remaining_quantity"], 12)

    @patch.object(PIPELINE.requests, "get")
    def test_blocks_when_no_active_flagship_pack(self, mocked_get):
        mocked_get.return_value = FakeResponse({
            "code": 0,
            "data": {"resource_pack_subscribe_infos": []}
        })
        with self.assertRaisesRegex(RuntimeError, "no active Flagship"):
            PIPELINE.account_video_pack("masked-test-key")

    def test_validates_reference_image_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.jpg"
            Image.new("RGB", (600, 900), "white").save(path)
            self.assertEqual(PIPELINE.validate_image(path), (600, 900))

    def test_rejects_too_small_reference_image(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tiny.jpg"
            Image.new("RGB", (100, 100), "white").save(path)
            with self.assertRaisesRegex(RuntimeError, "at least 300"):
                PIPELINE.validate_image(path)


if __name__ == "__main__":
    unittest.main()
