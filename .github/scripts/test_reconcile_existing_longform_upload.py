"""Offline safety tests for existing-upload YouTube reconciliation."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "whop-clipper"))
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

import reconcile_existing_longform_upload as recovery


class ReconciliationHelperTests(unittest.TestCase):
    def test_same_time_accepts_equivalent_iso8601_offsets(self):
        self.assertTrue(recovery.same_time("2026-11-25T07:00:00Z", "2026-11-25T10:00:00+03:00"))
        self.assertTrue(recovery.same_time("2026-11-25T07:00:00.000Z", "2026-11-25T07:00:00+00:00"))

    def test_same_time_rejects_different_and_invalid_values(self):
        self.assertFalse(recovery.same_time("2026-11-25T07:00:01Z", "2026-11-25T07:00:00Z"))
        self.assertFalse(recovery.same_time(None, "2026-11-25T07:00:00Z"))
        self.assertFalse(recovery.same_time("not-a-time", "2026-11-25T07:00:00Z"))

    def test_private_unscheduled_status_omits_publish_at_and_preserves_mutable_fields(self):
        status = recovery.private_unscheduled_status({
            "privacyStatus": "private",
            "publishAt": "2026-11-25T07:00:00Z",
            "license": "youtube",
            "embeddable": True,
            "publicStatsViewable": False,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
            "uploadStatus": "processed",
        })
        self.assertEqual(status["privacyStatus"], "private")
        self.assertNotIn("publishAt", status)
        self.assertNotIn("uploadStatus", status)
        self.assertEqual(status["license"], "youtube")
        self.assertTrue(status["embeddable"])
        self.assertFalse(status["publicStatsViewable"])
        self.assertFalse(status["selfDeclaredMadeForKids"])
        self.assertTrue(status["containsSyntheticMedia"])

    def _fake_youtube(self, videos):
        class Request:
            def __init__(self, value):
                self.value = value

            def execute(self):
                return self.value

        class Channels:
            def list(self, **kwargs):
                return Request({"items": [{
                    "id": "CHANNEL123",
                    "contentDetails": {"relatedPlaylists": {"uploads": "UPLOADS123"}},
                }]})

        class PlaylistItems:
            def list(self, **kwargs):
                items = []
                for video_id, data in videos.items():
                    items.append({
                        "snippet": {"title": data["title"]},
                        "contentDetails": {"videoId": video_id},
                    })
                return Request({"items": items})

        class Videos:
            def list(self, **kwargs):
                video_id = kwargs["id"]
                data = videos[video_id]
                return Request({"items": [{
                    "id": video_id,
                    "snippet": {
                        "title": data["title"],
                        "channelId": data["channel_id"],
                        "publishedAt": data["published_at"],
                    },
                    "status": {
                        "privacyStatus": data["privacy"],
                        **({"publishAt": data["publish_at"]} if data.get("publish_at") else {}),
                    },
                }]})

        class FakeYouTube:
            def channels(self):
                return Channels()

            def playlistItems(self):
                return PlaylistItems()

            def videos(self):
                return Videos()

        return FakeYouTube()

    def test_discovers_one_exact_title_private_upload_in_target_window(self):
        yt = self._fake_youtube({
            "VIDEO123": {
                "title": "US to Blacklist Megaspeed After Nvidia Scandal: The Full Inside Story",
                "channel_id": "CHANNEL123",
                "published_at": "2026-11-24T15:00:00Z",
                "privacy": "private",
            }
        })
        result = recovery.find_private_upload_for_job(
            yt,
            "CHANNEL123",
            "US to Blacklist Megaspeed After Nvidia Scandal: The Full Inside Story",
            recovery.parse_time("2026-11-25T15:00:00Z"),
        )
        self.assertEqual(result, "VIDEO123")

    def test_refuses_multiple_exact_title_private_uploads(self):
        title = "Same story"
        yt = self._fake_youtube({
            "VIDEO123": {
                "title": title, "channel_id": "CHANNEL123",
                "published_at": "2026-11-24T15:00:00Z", "privacy": "private",
            },
            "VIDEO456": {
                "title": title, "channel_id": "CHANNEL123",
                "published_at": "2026-11-24T16:00:00Z", "privacy": "private",
            },
        })
        with self.assertRaisesRegex(SystemExit, "found 2 candidates"):
            recovery.find_private_upload_for_job(
                yt, "CHANNEL123", title, recovery.parse_time("2026-11-25T15:00:00Z")
            )

    def test_refuses_exact_title_non_private_candidate(self):
        title = "Same story"
        yt = self._fake_youtube({
            "VIDEO123": {
                "title": title, "channel_id": "CHANNEL123",
                "published_at": "2026-11-24T15:00:00Z", "privacy": "public",
            },
        })
        with self.assertRaisesRegex(SystemExit, "not private"):
            recovery.find_private_upload_for_job(
                yt, "CHANNEL123", title, recovery.parse_time("2026-11-25T15:00:00Z")
            )


if __name__ == "__main__":
    unittest.main()
