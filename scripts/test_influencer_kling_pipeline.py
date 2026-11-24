import unittest
from unittest.mock import patch

from scripts.influencer_kling_pipeline import (
    account_video_pack,
    parse_key_pool,
    select_one_key,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class InfluencerKlingPipelineTests(unittest.TestCase):
    def test_parse_newline_delimited_key_pool(self):
        self.assertEqual(parse_key_pool("key-a\nkey-b\nkey-a\n"), ["key-a", "key-b"])

    def test_parse_json_key_pool(self):
        self.assertEqual(parse_key_pool('["key-a", "key-b"]'), ["key-a", "key-b"])

    def test_uses_only_the_designated_primary_account(self):
        keys = ["primary-account", "secondary-account", "third-account"]
        self.assertEqual(select_one_key(keys, 1), "primary-account")
        with self.assertRaisesRegex(RuntimeError, "single designated primary"):
            select_one_key(keys, 2)

    def test_blocks_animation_without_explicit_paid_balance_authorization(self):
        from scripts.influencer_kling_pipeline import require_billing_authorization
        with self.assertRaisesRegex(RuntimeError, "explicitly authorized"):
            require_billing_authorization("animate", False)
        require_billing_authorization("check", False)
        require_billing_authorization("animate", True)

    @patch("scripts.influencer_kling_pipeline.requests.get")
    def test_requires_active_flagship_kling_4_package(self, get):
        get.return_value = _Response({
            "code": 0,
            "data": {
                "resource_pack_subscribe_infos": [
                    {
                        "resource_pack_tier": "flagship",
                        "resource_pack_applicable_models": "Available only for Kling 4.0 and Kling 4.0 Flash",
                        "remaining_quantity": 120,
                        "status": "online",
                    }
                ]
            }
        })
        package = account_video_pack("masked-test-key")
        self.assertEqual(package["resource_pack_tier"], "flagship")
        self.assertEqual(package["remaining_quantity"], 120)

    @patch("scripts.influencer_kling_pipeline.requests.get")
    def test_blocks_if_package_expired_or_ineligible(self, get):
        get.return_value = _Response({
            "code": 0,
            "data": {
                "resource_pack_subscribe_infos": [
                    {
                        "resource_pack_tier": "flagship",
                        "resource_pack_applicable_models": "Kling 4.0",
                        "remaining_quantity": 500,
                        "status": "expired",
                    }
                ]
            }
        })
        with self.assertRaises(RuntimeError):
            account_video_pack("masked-test-key")


if __name__ == "__main__":
    unittest.main()
