"""YouTube OAuth scope guard tests for the long-form publisher."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from validate_youtube_channels import has_video_management_scope  # noqa: E402


class YouTubeManagementScopeTests(unittest.TestCase):
    def test_full_youtube_scope_is_accepted(self):
        self.assertTrue(
            has_video_management_scope("https://www.googleapis.com/auth/youtube")
        )

    def test_force_ssl_scope_is_accepted(self):
        self.assertTrue(
            has_video_management_scope("https://www.googleapis.com/auth/youtube.force-ssl")
        )

    def test_upload_only_scope_is_rejected(self):
        self.assertFalse(
            has_video_management_scope("https://www.googleapis.com/auth/youtube.upload")
        )

    def test_upload_plus_readonly_without_management_scope_is_rejected(self):
        self.assertFalse(
            has_video_management_scope(
                "https://www.googleapis.com/auth/youtube.upload "
                "https://www.googleapis.com/auth/youtube.readonly"
            )
        )


if __name__ == "__main__":
    unittest.main()
