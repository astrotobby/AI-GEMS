"""Offline safety tests for the optional FreeLLMAPI GitHub Actions sidecar."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freellmapi_sidecar as sidecar


class FreeLLMApiSidecarTests(unittest.TestCase):
    def test_bootstrap_uses_only_anonymous_ovh_and_horde_and_disables_kilo(self):
        with tempfile.TemporaryDirectory() as temp:
            github_env = str(Path(temp) / "github_env")
            Path(github_env).write_text("", encoding="utf-8")
            os.environ["RUNNER_TEMP"] = temp
            os.environ["GITHUB_ENV"] = github_env
            os.environ["FREELLMAPI_ENABLED"] = "true"

            seen = {}

            def fake_request(path, method="GET", payload=None, bearer=None, timeout=15):
                config_path = Path(temp) / "freellmapi-longform" / "config.json"
                if path == "/api/ping":
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    seen["config"] = config
                    return {"status": "ok"}
                if path == "/api/auth/login":
                    return {"token": "session-test-only"}
                if path == "/api/client-profiles":
                    self.assertEqual(bearer, "session-test-only")
                    return {"key": "sk-cp-test-only"}
                if path == "/v1/models":
                    self.assertEqual(bearer, "sk-cp-test-only")
                    return {"data": [{"id": "test-free-model"}]}
                raise AssertionError(f"Unexpected FreeLLMAPI endpoint: {path}")

            with patch.object(sidecar, "docker", return_value=CompletedProcess([], 0, "", "")) as docker:
                with patch.object(sidecar, "request_json", side_effect=fake_request):
                    with patch.object(sidecar.time, "sleep"):
                        with patch("builtins.print") as output:
                            self.assertTrue(sidecar.bootstrap())

            keys = seen["config"]["keys"]
            self.assertEqual(
                {(row["platform"], row.get("enabled", True)) for row in keys},
                {("ovh", True), ("aihorde", True), ("kilo", False)},
            )
            self.assertNotIn("provider", seen["config"])
            self.assertEqual(seen["config"]["routing"]["strategy"], "reliable")

            written = Path(github_env).read_text(encoding="utf-8")
            self.assertIn("FREELLMAPI_API_KEY=sk-cp-test-only", written)
            self.assertIn("FREELLMAPI_BASE_URL=http://127.0.0.1:3001/v1", written)
            self.assertIn("FREELLMAPI_MODEL=auto:smart", written)
            self.assertNotIn("ENCRYPTION_KEY", written)
            self.assertNotIn("longform-", written)
            pull_call = next(call for call in docker.call_args_list if call.args[0][0] == "pull")
            self.assertEqual(pull_call.args[0][1], sidecar.IMAGE)
            run_call = next(call for call in docker.call_args_list if call.args[0][0] == "run")
            self.assertIn("127.0.0.1:3001:3001", run_call.args[0])

            for name in ("RUNNER_TEMP", "GITHUB_ENV", "FREELLMAPI_ENABLED"):
                os.environ.pop(name, None)

    def test_disabled_bootstrap_does_not_touch_docker(self):
        os.environ["FREELLMAPI_ENABLED"] = "false"
        try:
            with patch.object(sidecar, "docker") as docker:
                self.assertFalse(sidecar.bootstrap())
                docker.assert_not_called()
        finally:
            os.environ.pop("FREELLMAPI_ENABLED", None)

    def test_cleanup_is_best_effort(self):
        with tempfile.TemporaryDirectory() as temp:
            os.environ["RUNNER_TEMP"] = temp
            target = Path(temp) / "freellmapi-longform"
            target.mkdir()
            (target / "config.json").write_text("{}", encoding="utf-8")
            try:
                with patch.object(sidecar, "docker", side_effect=FileNotFoundError):
                    sidecar.cleanup()
                self.assertFalse(target.exists())
            finally:
                os.environ.pop("RUNNER_TEMP", None)


if __name__ == "__main__":
    unittest.main()
