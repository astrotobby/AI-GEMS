from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from reconcile_longform_schedule import (  # noqa: E402
    EAT,
    job_id,
    live_publishing_enabled,
    read_ledger,
    select_next_slot,
)


class LongformScheduleRecoveryTests(unittest.TestCase):
    def test_live_master_switch_does_not_depend_on_legacy_visual_gate(self):
        self.assertTrue(live_publishing_enabled({"LONGFORM_LIVE_ENABLED": "true"}))

    def test_live_master_switch_requires_literal_true(self):
        for value in ("", "false", "1", "yes", "enabled"):
            with self.subTest(value=value):
                self.assertFalse(live_publishing_enabled({"LONGFORM_LIVE_ENABLED": value}))

    def setUp(self):
        self.target = date(2026, 11, 23)
        self.now = datetime(2026, 11, 22, 16, 0, tzinfo=EAT)

    def key(self, channel, slot):
        return job_id(self.target, channel, slot)

    def test_empty_ledger_dispatches_first_slot(self):
        result = select_next_slot({"jobs": {}}, self.target, self.now)
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual((result["channel"], result["slot"]), ("astrotobby", "10:00"))

    def test_skips_slots_already_scheduled_or_published(self):
        ledger = {
            "jobs": {
                self.key("astrotobby", "10:00"): {"status": "scheduled"},
                self.key("digital_nomad", "12:00"): {"status": "published"},
            }
        }
        result = select_next_slot(ledger, self.target, self.now)
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual((result["channel"], result["slot"]), ("astrotobby", "15:00"))

    def test_all_four_completed_returns_complete(self):
        ledger = {
            "jobs": {
                self.key(channel, slot): {"status": "scheduled"}
                for channel, slot in (
                    ("astrotobby", "10:00"),
                    ("digital_nomad", "12:00"),
                    ("astrotobby", "15:00"),
                    ("digital_nomad", "18:00"),
                )
            }
        }
        self.assertEqual(select_next_slot(ledger, self.target, self.now)["action"], "complete")

    def test_recent_render_state_waits_instead_of_dispatching_duplicate(self):
        ledger = {
            "jobs": {
                self.key("astrotobby", "10:00"): {
                    "status": "rendering",
                    "updated_at": (self.now - timedelta(minutes=20)).isoformat(),
                }
            }
        }
        result = select_next_slot(ledger, self.target, self.now)
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["job_id"], self.key("astrotobby", "10:00"))

    def test_stale_render_state_can_be_retried(self):
        ledger = {
            "jobs": {
                self.key("astrotobby", "10:00"): {
                    "status": "rendering",
                    "updated_at": (self.now - timedelta(hours=4)).isoformat(),
                }
            }
        }
        result = select_next_slot(ledger, self.target, self.now)
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual((result["channel"], result["slot"]), ("astrotobby", "10:00"))

    def test_ambiguous_upload_is_blocked(self):
        ledger = {
            "jobs": {
                self.key("astrotobby", "10:00"): {
                    "status": "uploading",
                    "updated_at": (self.now - timedelta(hours=4)).isoformat(),
                }
            }
        }
        result = select_next_slot(ledger, self.target, self.now)
        self.assertEqual(result["action"], "blocked")
        self.assertIn("ambiguous", result["reason"])

    def test_dry_run_record_is_not_mistaken_for_a_live_schedule(self):
        ledger = {
            "jobs": {
                self.key("astrotobby", "10:00"): {
                    "status": "dry_run_complete",
                    "updated_at": self.now.isoformat(),
                }
            }
        }
        result = select_next_slot(ledger, self.target, self.now)
        self.assertEqual(result["action"], "dispatch")

    def test_missing_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-ledger.json"
            with self.assertRaises(RuntimeError):
                read_ledger(path)

    def test_corrupt_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_ledger(path)

    def test_ledger_missing_jobs_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps({"schema": 1}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_ledger(path)


if __name__ == "__main__":
    unittest.main()
