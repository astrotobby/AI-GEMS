#!/usr/bin/env python3
"""Offline tests for the YouTube OAuth encrypted-secret storage helper."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("youtube_oauth_secret_storage.py")
SPEC = importlib.util.spec_from_file_location("youtube_oauth_secret_storage", MODULE_PATH)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


def credentials(slug):
    spec = storage.CHANNELS[slug]
    return {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "refresh_token": "test-refresh-token",
        "channel": spec["label"],
        "youtube_title": spec["label"],
        "channel_id": spec["id"],
    }


class YoutubeOAuthSecretStorageTests(unittest.TestCase):
    def test_credentials_are_normalized_to_expected_channel(self):
        result = storage.clean_credentials(credentials("ASTRO_TOBBY"), "ASTRO_TOBBY")
        self.assertEqual(result["channel_id"], storage.CHANNELS["ASTRO_TOBBY"]["id"])
        self.assertEqual(result["channel"], "Astro Tobby")
        self.assertEqual(result["refresh_token"], "test-refresh-token")

    def test_wrong_channel_id_is_rejected(self):
        raw = credentials("DIJITOL_NOMAD")
        raw["channel_id"] = storage.CHANNELS["ASTRO_TOBBY"]["id"]
        with self.assertRaises(storage.StorageError):
            storage.clean_credentials(raw, "DIJITOL_NOMAD")

    def test_missing_client_secret_is_rejected(self):
        raw = credentials("ASTRO_TOBBY")
        raw.pop("client_secret")
        with self.assertRaises(storage.StorageError):
            storage.clean_credentials(raw, "ASTRO_TOBBY")

    def test_restore_writes_both_channel_files(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                storage.CHANNELS["ASTRO_TOBBY"]["env"]: json.dumps(credentials("ASTRO_TOBBY")),
                storage.CHANNELS["DIJITOL_NOMAD"]["env"]: json.dumps(credentials("DIJITOL_NOMAD")),
            }
            with patch.object(storage, "TOKEN_DIR", Path(directory)), patch.dict(os.environ, env, clear=True):
                self.assertEqual(storage.restore(), 0)
            astro = json.loads((Path(directory) / "ASTRO_TOBBY.json").read_text(encoding="utf-8"))
            nomad = json.loads((Path(directory) / "DIJITOL_NOMAD.json").read_text(encoding="utf-8"))
            self.assertEqual(astro["channel_id"], storage.CHANNELS["ASTRO_TOBBY"]["id"])
            self.assertEqual(nomad["channel_id"], storage.CHANNELS["DIJITOL_NOMAD"]["id"])

    def test_restore_rejects_partial_channel_secret_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                storage.CHANNELS["ASTRO_TOBBY"]["env"]: json.dumps(credentials("ASTRO_TOBBY")),
                storage.CHANNELS["DIJITOL_NOMAD"]["env"]: "",
            }
            with patch.object(storage, "TOKEN_DIR", Path(directory)), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(storage.StorageError):
                    storage.restore()


if __name__ == "__main__":
    unittest.main(verbosity=2)
