"""Regressions for evidence loss, retry masking and binary payload boundaries."""

import json
import os
import socket
import struct
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent / "journey"))
from support import (
    Deadline,
    Evidence,
    FileChecks,
    HttpFailure,
    BackgroundRegression,
    digest,
)
from torrent_files import TorrentFiles
from external import World
from fixture_network import DnsServer, PEER_IP
from invariants import CommandHistory, Invariants, InvariantFailure


class FixtureIdentity(unittest.TestCase):
    def test_run_names_cannot_contain_embedded_bitrate_or_year_tokens(self):
        from catalogue import cases
        with patch.dict(os.environ, POMPEY_TEST_RUN_ID="f7ad38be50", POMPEY_TEST_CASES="16"):
            first = cases()
        with patch.dict(os.environ, POMPEY_TEST_RUN_ID="f7ad38be51", POMPEY_TEST_CASES="16"):
            second = cases()
        self.assertEqual(len({case.prefix for case in first}), 16)
        self.assertTrue(set(case.prefix for case in first).isdisjoint(case.prefix for case in second))
        self.assertTrue(all(case.prefix.removeprefix("Pompey").split("Case")[0].isalpha() for case in first))


class CumulativeInvariantEvents(unittest.TestCase):
    def test_recovered_failures_and_cleared_notices_still_reject_checkpoint(self):
        for event in [
            {"kind": "job-failed", "job": "routing", "error": "temporary"},
            {"kind": "attention", "notices": ["temporary review"]},
        ]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / "events"
                rows = [{"time": 0, "kind": "controller-started"}, {"time": 2, **event}]
                journal.write_text("".join(json.dumps(r) + "\n" for r in rows))
                client = SimpleNamespace(
                    ready_at=1, evidence=Evidence(Path(tmp) / "reports", 0)
                )
                with patch.dict(os.environ, POMPEY_EVENT_LOG=str(journal)):
                    invariants = Invariants(client, SimpleNamespace(ledger=[]))
                    with self.assertRaises(InvariantFailure):
                        invariants.assert_events()
                    # Recovery changes current state, not the cumulative journal.
                    with self.assertRaises(InvariantFailure):
                        invariants.assert_events()
                    self.assertEqual(
                        len(client.evidence.data["invariants"]["unexpected"]), 1
                    )


class CleanupDeadlines(unittest.TestCase):
    def test_shared_work_cannot_reset_an_earlier_owners_cleanup_budget(self):
        monitor = Invariants.__new__(Invariants)
        torrent = {"hash": "abc", "progress": 1}
        monitor.client = SimpleNamespace(
            qbit=lambda path: [torrent], arr=lambda *args: {"records": []}
        )
        monitor.background = SimpleNamespace(
            ledger=[
                {
                    "prefix": "earlier",
                    "infoHash": "abc",
                    "arrId": 7,
                    "cleanupDeadline": 5,
                }
            ]
        )
        with patch("invariants.time.time", return_value=4):
            self.assertEqual(monitor.pending_cleanup()[0]["owner"], "earlier")
        with patch("invariants.time.time", return_value=6):
            with self.assertRaises(BackgroundRegression) as caught:
                monitor.pending_cleanup()
            self.assertEqual(caught.exception.owner, "earlier")
        torrent["progress"] = 0.5
        with patch("invariants.time.time", return_value=4):
            with self.assertRaises(BackgroundRegression) as caught:
                monitor.pending_cleanup()
            self.assertEqual(caught.exception.field, "completed download regressed")


class LateNativeFailures(unittest.TestCase):
    def test_old_owner_is_attributed_and_evidence_survives_native_history_pruning(self):
        history = CommandHistory()
        ledger = [{"prefix": "earlier-case", "arrId": 7, "since": 1}]
        failure = {
            "id": 4,
            "name": "BulkMoveMovie",
            "status": "failed",
            "queued": "2026-10-26T00:00:00Z",
            "body": {"movies": [{"movieId": 7}]},
        }
        events = history.observe("radarr", [failure], ledger)
        self.assertEqual(events[0]["owner"], "earlier-case")
        self.assertEqual(history.observe("radarr", [], ledger), events)
        self.assertEqual(len(history.observe("radarr", [failure], ledger)), 1)

    def test_unrelated_and_preownership_failures_are_not_blamed_on_a_case(self):
        history = CommandHistory()
        rows = [
            {
                "id": 1,
                "name": "MoviesSearch",
                "status": "failed",
                "queued": "1970-01-01T00:00:01Z",
                "body": {"movieIds": [7]},
            },
            {
                "id": 2,
                "name": "MoviesSearch",
                "status": "failed",
                "queued": "2026-10-26T00:00:00Z",
                "body": {"movieIds": [8]},
            },
        ]
        self.assertEqual(
            history.observe(
                "radarr", rows, [{"prefix": "case", "arrId": 7, "since": 2}]
            ),
            [],
        )


class FixtureDns(unittest.TestCase):
    def test_known_names_resolve_without_forwarding_unknown_names(self):
        records = []
        unknown = []
        server = DnsServer(records.append, unknown.append)

        def query(name, kind):
            labels = (
                b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
            )
            return (
                struct.pack("!HHHHHH", 11, 256, 1, 0, 0, 0)
                + labels
                + struct.pack("!HH", kind, 1)
            )

        answer = server.answer(query("api.radarr.video", 1), "10.77.0.1", "udp")
        self.assertEqual(answer[-4:], socket.inet_aton(PEER_IP))
        self.assertEqual(struct.unpack("!H", answer[6:8])[0], 1)
        ipv6 = server.answer(query("api.radarr.video", 28), "10.77.0.1", "tcp")
        self.assertEqual(struct.unpack("!H", ipv6[6:8])[0], 0)
        missing = server.answer(query("unrecorded.invalid", 1), "10.77.0.1", "udp")
        self.assertEqual(missing[3] & 15, 3)
        self.assertEqual(unknown, ["DNS unrecorded.invalid"])
        self.assertEqual(records[1]["transport"], "tcp")


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
        world.unexpected = world.unknown.append
        _, _, status = world.respond("plex.tv", "/download/1", {})
        self.assertEqual(status, 404)
        self.assertEqual(world.unknown, ["plex.tv/download/1"])



if __name__ == "__main__":
    unittest.main()
