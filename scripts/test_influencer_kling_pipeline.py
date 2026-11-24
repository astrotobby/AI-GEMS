import unittest
from unittest.mock import patch

import requests

from scripts.influencer_kling_pipeline import (
    account_video_pack,
    parse_key_pool,
    select_eligible_accounts,
    supported_model_endpoints,
    split_content_segments,
    build_content_prompt,
    create_task,
)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class InfluencerKlingPipelineTests(unittest.TestCase):
    def test_parse_newline_delimited_key_pool_and_deduplicate(self):
        self.assertEqual(parse_key_pool("key-a\nkey-b\nkey-a\n"), ["key-a", "key-b"])

    def test_parse_json_key_pool(self):
        self.assertEqual(parse_key_pool('["key-a", "key-b"]'), ["key-a", "key-b"])

    def test_content_segments_preserve_script_order(self):
        segments = split_content_segments(
            "First event happens. Second event follows. Third event ends. Fourth event concludes.",
            2,
        )
        self.assertIn("First event", segments[0])
        self.assertIn("Second event", segments[0])
        self.assertIn("Third event", segments[1])
        self.assertIn("Fourth event", segments[1])

    def test_content_prompt_requires_real_moving_footage_and_same_host(self):
        prompt = build_content_prompt("A scientist explains a new discovery.", 1, 2)
        self.assertIn("@host", prompt)
        self.assertIn("moving footage", prompt)
        self.assertIn("not a still image", prompt)
        self.assertIn("discovery", prompt)

    @patch("scripts.influencer_kling_pipeline.requests.post")
    def test_generation_uses_persistent_character_element_not_still_frames(self, post):
        post.return_value = _Response({"code": 0, "data": {"id": "task-123"}})
        task_id = create_task(
            "masked-test-key",
            "Create a documentary scene based on this narration.",
            "element-456",
            "test-task-001",
            "/omni-video/kling-3.0-omni",
            "kling-3.0-omni",
        )
        body = post.call_args.kwargs["json"]
        self.assertEqual(task_id, "task-123")
        self.assertEqual(body["contents"][1]["type"], "element")
        self.assertEqual(body["contents"][1]["element_id"], "element-456")
        self.assertFalse(any(x["type"] == "first_frame" for x in body["contents"]))
        self.assertTrue(body["settings"]["multi_shot"])
        self.assertEqual(body["settings"]["shot_type"], "intelligence")

    def test_models_include_kling_4_and_3_omni_without_4_only_restriction(self):
        endpoints = supported_model_endpoints({
            "resource_pack_applicable_models": "Kling 3.0 Omni, Kling 4.0",
        })
        self.assertEqual(
            endpoints,
            [
                ("kling-4.0", "/omni-video/kling-4.0"),
                ("kling-3.0-omni", "/omni-video/kling-3.0-omni"),
            ],
        )

    def test_kling_3_omni_only_pack_is_eligible(self):
        self.assertEqual(
            supported_model_endpoints({
                "resource_pack_applicable_models": "Kling 3.0 Omni",
            }),
            [("kling-3.0-omni", "/omni-video/kling-3.0-omni")],
        )

    def test_flash_endpoint_is_detected_when_pack_lists_it(self):
        self.assertEqual(
            supported_model_endpoints({
                "resource_pack_applicable_models": "Kling 4.0 and Kling 4.0 Flash",
            }),
            [
                ("kling-4.0", "/omni-video/kling-4.0"),
                ("kling-4.0-flash", "/omni-video/kling-4.0-flash"),
            ],
        )

    @patch("scripts.influencer_kling_pipeline.requests.get")
    def test_account_video_pack_accepts_online_3_omni_pack(self, get):
        get.return_value = _Response({
            "code": 0,
            "data": {
                "resource_pack_subscribe_infos": [{
                    "resource_pack_tier": "standard",
                    "resource_pack_applicable_models": "Kling 3.0 Omni",
                    "remaining_quantity": 120,
                    "status": "online",
                }]
            }
        })
        selected = account_video_pack("masked-test-key")
        self.assertEqual(selected[0]["model_name"], "kling-3.0-omni")
        self.assertEqual(selected[0]["pack"]["remaining_quantity"], 120)

    @patch("scripts.influencer_kling_pipeline.requests.get")
    def test_rejects_expired_or_exhausted_packs(self, get):
        get.return_value = _Response({
            "code": 0,
            "data": {
                "resource_pack_subscribe_infos": [{
                    "resource_pack_tier": "standard",
                    "resource_pack_applicable_models": "Kling 3.0 Omni",
                    "remaining_quantity": 0,
                    "status": "online",
                }]
            }
        })
        with self.assertRaisesRegex(RuntimeError, "no active video resource package"):
            account_video_pack("masked-test-key")

    @patch("scripts.influencer_kling_pipeline.account_video_pack")
    def test_checks_all_keys_in_order_and_selects_first_eligible(self, account_check):
        account_check.side_effect = [
            RuntimeError("no eligible package"),
            requests.HTTPError("unauthorized"),
            [{
                "pack": {"remaining_quantity": 50},
                "model_name": "kling-3.0-omni",
                "endpoint": "/omni-video/kling-3.0-omni",
                "remaining_quantity": 50,
            }],
            [{
                "pack": {"remaining_quantity": 200},
                "model_name": "kling-4.0",
                "endpoint": "/omni-video/kling-4.0",
                "remaining_quantity": 200,
            }],
        ]
        selected = select_eligible_accounts(["key-1", "key-2", "key-3", "key-4"])
        self.assertEqual([x["credential_index"] for x in selected], [3, 4])
        self.assertEqual(account_check.call_count, 4)
        self.assertEqual(selected[0]["key"], "key-3")

    @patch("scripts.influencer_kling_pipeline.account_video_pack")
    def test_stops_on_rate_limit_instead_of_rotating_around_it(self, account_check):
        response = requests.Response()
        response.status_code = 429
        account_check.side_effect = requests.HTTPError("rate limited", response=response)
        with self.assertRaisesRegex(RuntimeError, "stopping instead of rotating"):
            select_eligible_accounts(["key-1", "key-2", "key-3"])
        self.assertEqual(account_check.call_count, 1)


if __name__ == "__main__":
    unittest.main()
