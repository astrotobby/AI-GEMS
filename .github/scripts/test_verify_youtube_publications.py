from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from verify_youtube_publications import (
    CHANNELS,
    decide_video_action,
    has_due_jobs,
    run_verification,
)


NOW = datetime(2026, 11, 26, 10, 40, tzinfo=timezone.utc)
PUBLISH_AT = "2026-11-26T10:00:00Z"
CHANNEL_ID = CHANNELS["astrotobby"]["channel_id"]


def job(**overrides):
    value = {
        "job_id": "20261126_astrotobby_1300",
        "channel": "astrotobby",
        "channel_id": CHANNEL_ID,
        "publish_at": PUBLISH_AT,
        "video_id": "video123",
        "title": "A scheduled YouTube video",
        "status": "scheduled",
    }
    value.update(overrides)
    return value


def video(**overrides):
    value = {
        "id": "video123",
        "snippet": {"channelId": CHANNEL_ID, "title": "A scheduled YouTube video"},
        "status": {
            "privacyStatus": "private",
            "publishAt": PUBLISH_AT,
            "uploadStatus": "processed",
            "selfDeclaredMadeForKids": False,
        },
        "processingDetails": {"processingStatus": "succeeded"},
    }
    value.update(overrides)
    return value


class FakeRequest:
    def __init__(self, callback):
        self.callback = callback

    def execute(self, num_retries=0):
        return self.callback()


class FakeVideos:
    def __init__(self, initial):
        self.current = initial
        self.update_calls = []

    def list(self, **kwargs):
        return FakeRequest(lambda: {"items": [self.current]})

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        def update():
            body = kwargs["body"]
            self.current = {
                **self.current,
                "status": {**self.current.get("status", {}), **body["status"]},
            }
            return {"id": body["id"]}
        return FakeRequest(update)


class FakeYouTube:
    def __init__(self, initial):
        self._videos = FakeVideos(initial)

    def videos(self):
        return self._videos


class YouTubePublicationDecisionTests(unittest.TestCase):
    def test_already_public_video_is_only_verified(self):
        current = video(status={"privacyStatus": "public"})
        self.assertEqual(
            decide_video_action(job(), current, CHANNEL_ID, NOW),
            ("verify_public", "YouTube already reports the video as public"),
        )

    def test_correct_overdue_private_video_can_be_promoted(self):
        self.assertEqual(
            decide_video_action(job(), video(), CHANNEL_ID, NOW),
            ("promote_public", "channel, publishAt and completed-processing checks all passed"),
        )

    def test_wrong_channel_is_never_promoted(self):
        current = video(snippet={"channelId": "UC_WRONG", "title": "A scheduled YouTube video"})
        action, reason = decide_video_action(job(), current, CHANNEL_ID, NOW)
        self.assertEqual(action, "manual_review")
        self.assertIn("channel ID", reason)

    def test_changed_youtube_publish_time_is_never_overwritten(self):
        current = video(status={**video()["status"], "publishAt": "2026-11-26T11:00:00Z"})
        action, reason = decide_video_action(job(), current, CHANNEL_ID, NOW)
        self.assertEqual(action, "manual_review")
        self.assertIn("publishAt", reason)

    def test_unlisted_video_is_not_changed(self):
        current = video(status={**video()["status"], "privacyStatus": "unlisted"})
        self.assertEqual(decide_video_action(job(), current, CHANNEL_ID, NOW)[0], "manual_review")

    def test_pending_processing_waits_without_promotion(self):
        current = video(processingDetails={"processingStatus": "processing"})
        action, reason = decide_video_action(job(), current, CHANNEL_ID, NOW)
        self.assertEqual(action, "wait")
        self.assertIn("not complete", reason)

    def test_failed_processing_needs_manual_review(self):
        current = video(processingDetails={"processingStatus": "failed"})
        self.assertEqual(decide_video_action(job(), current, CHANNEL_ID, NOW)[0], "manual_review")

    def test_ten_minute_grace_period_is_respected(self):
        now = datetime(2026, 11, 26, 10, 9, tzinfo=timezone.utc)
        self.assertEqual(decide_video_action(job(), video(), CHANNEL_ID, now)[0], "wait")

    def test_videos_more_than_24_hours_late_are_not_auto_promoted(self):
        now = datetime(2026, 11, 27, 10, 1, tzinfo=timezone.utc)
        action, reason = decide_video_action(job(), video(), CHANNEL_ID, now)
        self.assertEqual(action, "manual_review")
        self.assertIn("24 hours", reason)


class YouTubePublicationExecutionTests(unittest.TestCase):
    def test_ledger_channel_id_mismatch_is_not_sent_to_youtube(self):
        youtube = FakeYouTube(video())
        ledger_job = job(channel_id="UC_WRONG")
        ledger = {"jobs": {"job123": ledger_job}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=True)

        self.assertEqual(youtube.videos()._videos.update_calls, [])
        self.assertEqual(ledger_job["status"], "scheduled")
        self.assertEqual(ledger_job["publication_check"]["state"], "manual_review")
        self.assertIn("ledger channel_id", ledger_job["publication_check"]["reason"])
        self.assertEqual(result["counts"]["manual_review"], 1)

    def test_due_preflight_skips_future_jobs_and_quarantined_manual_reviews(self):
        future = NOW + timedelta(hours=1)
        ledger = {"jobs": {
            "future": job(publish_at=future.isoformat()),
            "quarantined": job(publication_check={
                "state": "manual_review",
                "reason": "mismatched publishAt",
            }),
        }}
        self.assertFalse(has_due_jobs(ledger, NOW))

    def test_due_preflight_finds_overdue_scheduled_video(self):
        self.assertTrue(has_due_jobs({"jobs": {"job123": job()}}, NOW))

    def test_promote_only_after_all_checks_and_verify_public_result(self):
        youtube = FakeYouTube(video())
        ledger = {"jobs": {"job123": job()}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=True)

        saved = ledger["jobs"]["job123"]
        self.assertEqual(youtube.videos()._videos.update_calls[0]["body"]["status"]["privacyStatus"], "public")
        self.assertEqual(saved["status"], "published")
        self.assertEqual(saved["publication_check"]["state"], "public_verified")
        self.assertEqual(result["counts"]["promoted_public"], 1)

    def test_read_only_mode_does_not_make_any_youtube_changes(self):
        youtube = FakeYouTube(video())
        ledger = {"jobs": {"job123": job()}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=False)

        self.assertEqual(youtube.videos()._videos.update_calls, [])
        self.assertEqual(ledger["jobs"]["job123"]["status"], "scheduled")
        self.assertEqual(ledger["jobs"]["job123"]["publication_check"]["state"], "would_promote")
        self.assertEqual(result["counts"]["would_promote"], 1)

    def test_public_video_is_marked_published_without_update(self):
        youtube = FakeYouTube(video(status={"privacyStatus": "public"}))
        ledger = {"jobs": {"job123": job()}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=True)

        self.assertEqual(youtube.videos()._videos.update_calls, [])
        self.assertEqual(ledger["jobs"]["job123"]["status"], "published")
        self.assertEqual(result["counts"]["already_public"], 1)

    def test_wrong_channel_stays_scheduled_and_unchanged(self):
        youtube = FakeYouTube(video(snippet={"channelId": "UC_WRONG"}))
        ledger = {"jobs": {"job123": job()}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=True)

        self.assertEqual(youtube.videos()._videos.update_calls, [])
        self.assertEqual(ledger["jobs"]["job123"]["status"], "scheduled")
        self.assertEqual(ledger["jobs"]["job123"]["publication_check"]["state"], "manual_review")
        self.assertEqual(result["counts"]["manual_review"], 1)

    def test_future_release_is_not_touched(self):
        future = NOW + timedelta(hours=1)
        future_job = job(publish_at=(future + timedelta(hours=1)).isoformat())
        youtube = FakeYouTube(video())
        ledger = {"jobs": {"job123": future_job}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=True)

        self.assertEqual(youtube.videos()._videos.update_calls, [])
        self.assertNotIn("publication_check", ledger["jobs"]["job123"])
        self.assertEqual(result["counts"]["checked"], 0)

    def test_stale_overdue_video_is_left_private(self):
        old = NOW - timedelta(days=2)
        old_job = job(publish_at=old.isoformat())
        youtube = FakeYouTube(video())
        ledger = {"jobs": {"job123": old_job}}
        result = run_verification(ledger, {"astrotobby": youtube}, NOW, apply_publication=True)

        self.assertEqual(youtube.videos()._videos.update_calls, [])
        self.assertEqual(ledger["jobs"]["job123"]["status"], "scheduled")
        self.assertEqual(ledger["jobs"]["job123"]["publication_check"]["state"], "manual_review")
        self.assertEqual(result["counts"]["stale"], 1)


if __name__ == "__main__":
    unittest.main()
