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


if __name__ == "__main__":
    unittest.main()
