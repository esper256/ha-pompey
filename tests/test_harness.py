"""Regressions for evidence loss, retry masking and binary payload boundaries."""

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent / "journey"))
from support import Deadline, Evidence, FileChecks, HttpFailure, digest
from torrent_files import TorrentFiles
from external import World


class Polling(unittest.TestCase):
    def test_permanent_api_failure_is_not_retried(self):
        calls = []

        def request():
            calls.append(True)
            raise HttpFailure(
                "GET", "http://fixture/resource?api_key=secret", 401, "unauthorized"
            )

        with self.assertRaises(HttpFailure) as caught:
            Deadline(10).wait(request, "ready")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", str(caught.exception))

    def test_temporary_unavailability_can_become_ready(self):
        calls = []

        def request():
            calls.append(True)
            if len(calls) == 1:
                raise HttpFailure("GET", "http://fixture", 503, "starting")
            return "ready"

        self.assertEqual(Deadline(10).wait(request, "ready"), "ready")
        self.assertEqual(len(calls), 2)

    def test_wait_limits_nested_http_budget_without_consuming_next_stage(self):
        deadline = Deadline(10)
        bounds = []

        def request():
            bounds.append(deadline.remaining())
            return False

        with self.assertRaises(AssertionError):
            deadline.wait(request, "not ready", seconds=0.02)
        self.assertLessEqual(max(bounds), 0.02)
        self.assertGreater(deadline.remaining(), 1)


class DurableEvidence(unittest.TestCase):
    def test_partial_case_and_original_error_survive_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Evidence(tmp, 0)
            case = SimpleNamespace(
                prefix="case-one",
                movie_id=1,
                certification="PG",
                library="Kid Friendly",
            )
            report.begin(case)
            report.stamp("requested")
            try:
                raise RuntimeError("native command failed")
            except RuntimeError as exc:
                report.fail(exc)
            saved = json.loads((Path(tmp) / "result.json").read_text())
            self.assertFalse(saved["success"])
            self.assertEqual(saved["cases"][0]["status"], "failed")
            self.assertIn("requested", saved["cases"][0]["timings"])
            self.assertEqual(saved["error"]["message"], "native command failed")
            self.assertEqual(
                json.loads((Path(tmp) / "case-one.json").read_text())["status"],
                "failed",
            )

    def test_final_verification_does_not_trust_cached_file_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "film"
            path.write_bytes(b"original")
            original = path.stat()
            expected = digest(path)
            checks = FileChecks()
            checks.verify(path, expected)
            path.write_bytes(b"changed!")
            with patch.object(Path, "stat", return_value=original):
                with self.assertRaises(AssertionError):
                    checks.verify(path, expected, full=True)


class PayloadBoundary(unittest.TestCase):
    def test_blocks_span_files_and_empty_files_without_loading_the_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, value in [("a", b"abcd"), ("b", b""), ("c", b"efghij")]:
                (Path(tmp) / name).write_bytes(value)
            data = TorrentFiles(tmp)
            try:
                self.assertEqual(data.size, 10)
                self.assertEqual(data.read(2, 7), b"cdefghi")
                self.assertEqual(data.read(10, 0), b"")
                with self.assertRaises(ValueError):
                    data.read(9, 2)
            finally:
                data.close()

    def test_truncated_fixture_cannot_silently_return_short_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a"
            path.write_bytes(b"12345")
            data = TorrentFiles(tmp)
            try:
                path.write_bytes(b"1")
                with self.assertRaises(OSError):
                    data.read(0, 5)
            finally:
                data.close()

    def test_wrong_host_cannot_receive_a_successful_fixture_response(self):
        world = World.__new__(World)
        world.unknown = []
        _, _, status = world.respond("plex.tv", "/download/1", {})
        self.assertEqual(status, 404)
        self.assertEqual(world.unknown, ["plex.tv/download/1"])


if __name__ == "__main__":
    unittest.main()
