"""Focused edge cases for safety logic; application contracts are verified by integration scenarios."""

import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "pompey/rootfs/usr/local/bin"
if not BIN.exists():
    BIN = Path("/usr/local/bin")
sys.path.insert(0, str(BIN))
import media_policy as media
import pompey_common as api
import pompey_state as state
import pompey_config as config
from request_policy import closeout_guard, requested_seasons
from media_policy import seed_goal_reached, contained
from vpn_config import render, parse
from pompey_controller import Job, dispatch
from concurrent.futures import Future


class SupportLogEvidence(unittest.TestCase):
    def test_report_survives_unavailable_applications_and_missing_files(self):
        from pompey_report import Collector, collect_report
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
                "POMPEY_CONFIG": root, "POMPEY_DATA": root, "POMPEY_READY": root,
                "POMPEY_SECRETS": root + "/missing-secrets", "POMPEY_OPTIONS": root + "/missing-options"}), \
                patch.object(Collector, "read", side_effect=OSError("application unavailable")):
            report = json.loads(collect_report().split("\n\n", 1)[1])
        self.assertFalse(report["requestCoverage"]["complete"])
        self.assertEqual(report["requests"], [])
        self.assertIn("application unavailable", report["applications"]["seerr"]["requests"]["collectionError"])
        self.assertTrue(report["collectionNotices"])

    def test_report_endpoint_preserves_raw_text_and_disables_http_caching(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        from pompey_setup import Handler
        raw = 'Real title /media/private/path Authorization: Bearer original-token\n'
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("pompey_report.collect_report", return_value=raw):
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                connection.request("POST", "/diagnostics/report", "{}", {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertEqual(response.read().decode(), raw)
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_development_capture_preserves_rejected_alternatives_before_console_filtering(self):
        import contextlib
        import io
        import pompey_log_emit as logs
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"POMPEY_READY": directory}), patch.dict(logs._captures, {}, clear=True):
            output = io.StringIO()
            line = "2026-10-27|Warn|Release alternative rejected: Not enough seeders: 0 apikey=original-secret"
            with contextlib.redirect_stderr(output):
                logs.emit("Sonarr", line)
                logs.emit("Sonarr", line)
            captured = (Path(directory) / "captured-logs/Sonarr.log").read_text()
            self.assertEqual(captured.count("original-secret"), 2)
            self.assertNotIn("original-secret", output.getvalue())
            for handler in logs._captures.values():
                handler.close()

    def test_native_diagnostic_messages_redact_headers_and_embedded_credentials(self):
        from pompey_log_emit import redact_value
        data = {"id": 8, "messages": ['Authorization: Bearer bearer-secret',
                                     'Authorization: Basic basic-secret',
                                     '{"apiKey":"json-secret"}', 'X-Plex-Token: plex-secret']}
        result = redact_value(data)
        self.assertEqual(result["id"], 8)
        for secret in ["bearer-secret", "basic-secret", "json-secret", "plex-secret"]:
            self.assertNotIn(secret, json.dumps(result))

    def test_first_errors_and_repeat_counts_survive_without_credentials(self):
        import contextlib
        import io
        import pompey_log_emit as logs
        logs._repeated.clear()
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream), contextlib.redirect_stdout(stream):
            logs.emit("Prowlarr", "2026-10-27 01:00:00|Warn|Unable to connect to indexer apikey=secret-value")
            logs.emit("Prowlarr", "2026-10-27 01:00:01|Warn|Unable to connect to indexer apikey=secret-value")
            logs.emit("Prowlarr", "2026-10-27 01:00:02|Info|Indexer connection restored")
            logs.flush_repeats()
        output = stream.getvalue()
        self.assertNotIn("secret-value", output)
        self.assertIn("Unable to connect to indexer", output)
        self.assertIn("repeated 1 time(s)", output)
        self.assertIn("connection restored", output)


class ObservationSemantics(unittest.TestCase):
    def test_access_hosts_reject_wildcards_urls_and_bind_addresses_and_preserve_explicit_names(self):
        from pompey_hosts import hostname, merged_hosts
        self.assertEqual(hostname("HA.Example.org"), "ha.example.org")
        self.assertEqual(hostname("[2001:db8::1]"), "[2001:db8::1]")
        for value in ["*", "*.example.org", "http://ha.example.org", "ha.example.org:8123", "ha.org,evil.org", "0.0.0.0", "[::]", "bad host", "1.2.3.999"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                hostname(value)
        result = merged_hosts("*,0.0.0.0,manual.example.org;*.trusted.example.org", ["ha.example.org", "127.0.0.1"])
        self.assertEqual(result, "manual.example.org,*.trusted.example.org,ha.example.org,127.0.0.1")

    def test_report_pagination_keeps_requests_beyond_the_dashboard_limit_and_partial_evidence(self):
        from pompey_report import Collector
        collector = Collector()
        first = {"results": [{"id": i} for i in range(100)], "pageInfo": {"results": 101}}
        second = {"results": [{"id": 100}], "pageInfo": {"results": 101}}
        with patch.object(collector, "read", side_effect=[first, second]):
            result = collector.pages("http://local/request", seerr=True)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["records"]), 101)
        for final in [first, OSError("application unavailable")]:
            with patch.object(collector, "read", side_effect=[first, final]):
                result = collector.pages("http://local/request", seerr=True)
            self.assertFalse(result["complete"])
            self.assertEqual(len(result["records"]), 100)
            self.assertTrue(result["collectionError"])

    def test_report_correlates_grab_history_without_inventing_historical_seed_counts(self):
        from pompey_report import request_evidence
        request = {"id": 4, "type": "movie", "status": 2, "media": {"tmdbId": 12}}
        services = {"seerr": {"requests": {"records": [request]}},
            "radarr": {"titles": [{"id": 7, "tmdbId": 12, "title": "Real title", "monitored": True}],
                "queue": {"complete": True, "records": [{"id": 21, "movieId": 7, "downloadId": "ABC"}]},
                "history": {"complete": True, "records": [{"id": 31, "movieId": 7, "downloadId": "ABC", "sourceTitle": "Chosen release"}]}},
            "qbittorrent": {"torrents": [{"hash": "abc", "state": "stalledDL", "num_seeds": 0}]}}
        result = request_evidence(services, {})[0]
        self.assertEqual(result["observedPhase"], "Waiting for peers")
        self.assertEqual(result["historyIds"], [31])
        self.assertEqual(result["downloadIds"], ["abc"])
        self.assertEqual(result["request"], request)
        self.assertIsNone(result["lastExplicitSearch"])
        services["radarr"]["queue"]["complete"] = False
        self.assertIn("incomplete", request_evidence(services, {})[0]["observedPhase"])

    def test_torrent_buckets_are_exclusive_and_stale_counts_stay_labelled(self):
        from pompey_observations import torrent_counts, summarize
        states = ["downloading", "stalledDL", "stoppedDL", "stalledUP", "checkingDL", "queuedDL"]
        counts = torrent_counts([{"state": value} for value in states])
        self.assertEqual(sum(counts.values()), len(states))
        self.assertEqual(counts, dict(downloading=1, stalled=1, paused=1, seeding=1, checking=1, other=1))
        result = summarize({"radarr": {"available": False, "updated": 12, "data": {"titles": [{"monitored": True}]}}})
        self.assertEqual(result["movies"], {"available": False, "updated": 12, "value": 1})
        self.assertIsNone(result["shows"]["value"])

    def test_indexer_denominator_excludes_disabled_sources_and_duplicate_failures(self):
        from datetime import datetime, timezone
        from pompey_observations import indexer_availability
        future = datetime.fromtimestamp(4102444800, timezone.utc).isoformat()
        past = datetime.fromtimestamp(1, timezone.utc).isoformat()
        data = {"indexers": [{"id": 1, "enable": True, "name": "A"}, {"id": 2, "enable": True, "name": "B"}, {"id": 3, "enable": False, "name": "C"}],
                "status": [{"indexerId": 1, "disabledTill": future}, {"indexerId": 1, "disabledTill": future}, {"indexerId": 3, "disabledTill": future}, {"indexerId": 2, "disabledTill": past}]}
        counts, failures = indexer_availability(data)
        self.assertEqual(counts, {"healthy": 1, "enabled": 2})
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["name"], "A")

    def test_partial_tv_files_do_not_imply_request_completion(self):
        from pompey_diagnostics import request_progress
        request = {"status": 2, "media": {"status": 4}}
        phase, _, _ = request_progress(request, {"kind": "sonarr", "hasFile": 2}, [], {})
        self.assertEqual(phase, "Partially available")
        phase, _, _ = request_progress(request, {"kind": "radarr", "hasFile": True}, [], {})
        self.assertEqual(phase, "Imported to library")
        phase, _, _ = request_progress({"status": 1}, None, [], {})
        self.assertEqual(phase, "Awaiting approval")


class IngressPreferences(unittest.TestCase):
    def test_invalid_write_preserves_prior_policy_and_pending_means_not_applied(self):
        import pompey_preferences as preferences
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, POMPEY_DATA=tmp):
            original = {**preferences.DEFAULTS, "after_download": "share_one_day"}
            state.save("preferences", {"values": original})
            preferences.applied()
            self.assertFalse(preferences.status()["pending"])
            for field, value in [("after_download", []), ("simultaneous_downloads", True),
                                 ("simultaneous_downloads", 21), ("prefer_dual_audio", "false")]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    preferences.save({**original, field: value})
                self.assertEqual(preferences.current(), original)
            with patch("pompey_runtime.stack_lock", side_effect=BlockingIOError):
                with self.assertRaises(BlockingIOError):
                    preferences.save(preferences.DEFAULTS)
                self.assertEqual(preferences.current(), original)
            preferences.save(preferences.DEFAULTS)
            self.assertTrue(preferences.status()["pending"])
            self.assertEqual(api.qbit_share_limits()["ratio"], 0)
            self.assertEqual(api.simultaneous_downloads(), 8)

    def test_legacy_values_are_used_until_first_save_and_audio_changes_affect_profiles(self):
        import pompey_preferences as preferences
        from recyclarr_sync import render_config
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, POMPEY_DATA=tmp,
                AFTER_DOWNLOAD="share_to_ratio", SIMULTANEOUS_DOWNLOADS="3"):
            self.assertEqual(preferences.current()["after_download"], "share_to_ratio")
            state.save("preferences", {"values": {**preferences.DEFAULTS, "prefer_dual_audio": False}})
            self.assertEqual(api.after_download(), "stop_sharing")
            self.assertIn("score: 0", render_config("r", "key", "s", "key"))
            self.assertNotIn("score: 15", render_config("r", "key", "s", "key"))


class WorkflowEvidence(unittest.TestCase):
    def test_queue_evidence_includes_manual_work_and_marks_stale_peer_data(self):
        from pompey_diagnostics import workflow
        source = {"available": True, "updated": 100, "data": {"titles": []}}
        sources = {"radarr": source, "sonarr": source,
                   "qbittorrent": {"available": False, "updated": 90}}
        titles = [{"kind": "radarr", "id": 4, "title": "Example", "monitored": True}]
        queues = {"radarr": [{"movieId": 4, "downloadId": "ABC", "sizeleft": 10}]}
        torrents = {"abc": {"state": "stalledDL", "num_seeds": 0, "num_leechs": 2, "dlspeed": 0},
                    "manual": {"name": "Manual grab", "state": "stoppedUP"}}
        result = workflow(sources, titles, queues, torrents)
        linked = next(row for row in result if row["id"] == 4)
        self.assertEqual(linked["phase"], "Current progress unavailable")
        self.assertEqual(linked["lastKnownPhase"], "Waiting for peers")
        self.assertEqual(linked["updated"], 90)
        self.assertEqual(linked["download"]["connectedPeers"], 2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["title"], "Manual grab")

    def test_completed_titles_are_excluded_without_hiding_missing_episodes(self):
        from pompey_diagnostics import workflow
        sources = {"radarr": {"available": True, "updated": 100},
                   "sonarr": {"available": True, "updated": 100, "data": {"titles": [
                       {"id": 2, "statistics": {"episodeCount": 5, "episodeFileCount": 3}},
                       {"id": 3, "statistics": {"episodeCount": 5, "episodeFileCount": 5}}]}}}
        titles = [{"kind": "radarr", "id": 1, "title": "Complete", "monitored": True, "hasFile": True},
                  {"kind": "sonarr", "id": 2, "title": "Partial", "monitored": True, "hasFile": 3},
                  {"kind": "sonarr", "id": 3, "title": "Caught up", "monitored": True, "hasFile": 5}]
        rows = workflow(sources, titles, {}, {})
        self.assertEqual([row["title"] for row in rows], ["Partial"])
        self.assertEqual(rows[0]["phase"], "Missing episodes")

    def test_requested_monitoring_off_is_not_hidden_as_an_unrequested_title(self):
        from pompey_diagnostics import workflow
        title = {"kind": "radarr", "id": 1, "title": "Paused request", "monitored": False}
        sources = {"radarr": {"available": True, "updated": 100}}
        self.assertEqual(workflow(sources, [title], {}, {}), [])
        rows = workflow(sources, [title], {}, {}, {("radarr", 1)})
        self.assertEqual(rows[0]["phase"], "Monitoring disabled")


class ChangingRequestHistory(unittest.TestCase):
    def test_changed_snapshots_defer_without_loading_or_mutating_monitoring(self):
        from request_policy import reconcile_requests
        with patch("request_policy.requests_snapshot", side_effect=[[{"id": 1}], [{"id": 1}, {"id": 2}]]), \
             patch("request_policy.api.log_if_new") as log, patch("request_policy.state.load") as load, \
             patch("request_policy.api.http") as http:
            reconcile_requests()
            log.assert_called_once()
            load.assert_not_called()
            http.assert_not_called()

    def test_snapshot_api_failure_is_still_a_real_failure(self):
        from request_policy import reconcile_requests
        with patch("request_policy.requests_snapshot", side_effect=RuntimeError("Seerr unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Seerr unavailable"):
                reconcile_requests()


class CumulativeFailureEvidence(unittest.TestCase):
    def test_recovery_and_controller_restart_do_not_erase_failure(self):
        from pompey_events import record

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, POMPEY_EVENT_LOG=tmp + "/events"),
        ):
            job = Job("downloads", [], 2, 30)
            job.error = "temporary failure"
            job.complete(1, False)
            job.complete(2, True)
            record("controller-started")
            self.assertEqual(job.failures, 0)
            events = [
                json.loads(line)
                for line in Path(tmp + "/events").read_text().splitlines()
            ]
            self.assertEqual(events[0]["kind"], "job-failed")
            self.assertEqual(events[0]["error"], "temporary failure")
            self.assertEqual(events[1]["kind"], "controller-started")

    def test_attention_created_and_cleared_between_health_samples_is_retained(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, POMPEY_DATA=tmp, POMPEY_EVENT_LOG=tmp + "/events"),
        ):
            state.save("requests", {"reviewRequired": "History changed"})
            state.save("requests", {})
            self.assertEqual(media.attention(), [])
            events = [
                json.loads(line)
                for line in Path(tmp + "/events").read_text().splitlines()
            ]
            self.assertTrue(
                any("History changed" in e.get("notices", []) for e in events)
            )


class ControllerScheduling(unittest.TestCase):
    def test_due_jobs_cannot_starve_behind_faster_recurring_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp)
            for marker in ["downloads-wired", "seerr-arr", "roots-wired"]:
                (ready / marker).touch()
            jobs = [
                Job(name, [], 2, 30)
                for name in ["configuration", "requests", "downloads", "routing"]
            ]
            calls = []

            class Pool:
                def submit(self, func, job):
                    calls.append(job.name)
                    result = Future()
                    result.set_result(True)
                    return result

            for now in range(24):
                for job in jobs:
                    if job.future:
                        job.complete(now, job.future.result())
                dispatch(jobs, Pool(), now, ready)
                self.assertLessEqual(sum(j.future is not None for j in jobs), 1)
            self.assertTrue(all(calls.count(j.name) >= 4 for j in jobs), calls)


class PathSafety(unittest.TestCase):
    def test_traversal_overlap_and_symlink_escapes(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, MEDIA_ROOT=temp),
        ):
            for name in config.DEFAULTS:
                os.environ.pop(name, None)
            config.validate()
            for value in ["../escape", "/absolute", "downloads/complete", "Movies"]:
                with (
                    self.subTest(value=value),
                    patch.dict(os.environ, MEDIA_MOVIES=value),
                ):
                    with self.assertRaises(ValueError):
                        config.validate()
            outside = Path(temp).parent / "outside-pompey"
            (Path(temp) / "linked").symlink_to(outside)
            with patch.dict(os.environ, MEDIA_MOVIES="linked/movie"):
                with self.assertRaises(ValueError):
                    config.validate()
            (Path(temp) / "elsewhere").symlink_to(
                Path(temp) / "Movies/Not Kid Friendly"
            )
            with patch.dict(os.environ, MEDIA_TV="elsewhere"):
                with self.assertRaises(ValueError):
                    config.validate()

    def test_containment_excludes_root_prefix_lookalikes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "manual"
            root.mkdir()
            (root / "escape").symlink_to(Path(temp))
            self.assertTrue(contained(str(root / "release/file.mkv"), str(root)))
            for path in [
                root,
                root.parent / "manual2/file",
                root / "escape/file",
                root / "../outside",
            ]:
                self.assertFalse(contained(str(path), str(root)), path)


class RequestSafety(unittest.TestCase):
    def test_history_identity_and_bulk_disappearance(self):
        owned = {"movie:" + str(i): {"externalId": i} for i in range(10)}
        self.assertTrue(closeout_guard(owned, set(), "old", "new"))
        self.assertTrue(closeout_guard(owned, set(), "same", "same"))
        self.assertFalse(
            closeout_guard(
                owned, {("movie", str(i)) for i in range(1, 10)}, "same", "same"
            )
        )
        self.assertFalse(
            closeout_guard({"movie:1": {"externalId": 1}}, set(), "same", "same")
        )

    def test_overlapping_declined_and_unknown_seasons(self):
        def request(seasons, status=2):
            return {
                "type": "tv",
                "media": {"tvdbId": 42},
                "status": status,
                "seasons": seasons,
            }

        rows = [
            request([{"seasonNumber": 1}, {"seasonNumber": 2}]),
            request([{"seasonNumber": 2}, {"seasonNumber": 3, "status": 3}]),
        ]
        self.assertEqual(requested_seasons(rows, 42), {1, 2})
        self.assertEqual(
            requested_seasons([request([{"seasonNumber": 1}], 3)], 42), set()
        )
        for value in [
            None,
            [],
            [{}],
            [{"seasonNumber": True}],
            [{"seasonNumber": -1}],
            [{"seasonNumber": 1, "status": 99}],
        ]:
            self.assertIsNone(requested_seasons([request(value)], 42), value)


class SharingBoundaries(unittest.TestCase):
    def test_only_complete_downloads_can_reach_a_goal(self):
        complete = {"progress": 1, "amount_left": 0, "ratio": 1, "seeding_time": 86400}
        for policy in ["stop_sharing", "share_to_ratio", "share_one_day"]:
            self.assertTrue(seed_goal_reached(complete, policy))
            for altered in [
                {"progress": 0.999},
                {"amount_left": 1},
                {"progress": "unknown"},
            ]:
                self.assertFalse(seed_goal_reached(dict(complete, **altered), policy))
        self.assertFalse(
            seed_goal_reached(dict(complete, ratio=0.999), "share_to_ratio")
        )
        self.assertFalse(
            seed_goal_reached(dict(complete, seeding_time=86399), "share_one_day")
        )
        self.assertFalse(
            seed_goal_reached(dict(complete, seeding_time=None), "share_one_day")
        )


class ImportReceiptSafety(unittest.TestCase):
    def test_ambiguous_submission_retains_file_and_cannot_submit_twice(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                os.environ,
                MEDIA_ROOT=folder,
                POMPEY_DATA=folder,
                AFTER_DOWNLOAD="stop_sharing",
            ),
        ):
            source = Path(api.downloads_manual()) / "release/file.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"intent test; not integration media")
            item = {
                "hash": "a" * 40,
                "content_path": str(source.parent),
                "category": "prowlarr",
                "state": "stoppedUP",
                "progress": 1,
                "amount_left": 0,
            }

            def exchange(method, url, body=None, **kwargs):
                if method == "GET" and "/manualimport?" in url:
                    return [
                        {
                            "path": str(source),
                            "movie": {"id": 1},
                            "quality": {},
                            "languages": [],
                        }
                    ]
                if method == "POST" and url.endswith("/command"):
                    raise RuntimeError(
                        "Response lost after the import may have been accepted"
                    )
                raise AssertionError((method, url))

            keys = {"radarr_api_key": "fixture", "sonarr_api_key": "fixture"}
            with patch.object(api, "http", side_effect=exchange):
                with self.assertRaises(RuntimeError):
                    media.manual_imports([item], keys)
            self.assertTrue(source.exists())
            self.assertEqual(
                state.load("manual-imports")[item["hash"]]["phase"], "submitting"
            )
            with patch.object(
                api,
                "http",
                side_effect=AssertionError("Ambiguous import submitted again"),
            ):
                media.manual_imports([item], keys)
            self.assertTrue(source.exists())

    def test_one_failed_receipt_does_not_prevent_another_from_completing(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, POMPEY_DATA=folder),
        ):
            source = Path(folder) / "retained.mkv"
            source.write_bytes(b"preserve")
            receipts = {
                "first": {
                    "kind": "radarr",
                    "phase": "submitted",
                    "commandId": 1,
                    "files": [str(source)],
                },
                "second": {
                    "kind": "radarr",
                    "phase": "submitted",
                    "commandId": 2,
                    "files": [str(Path(folder) / "moved.mkv")],
                },
            }

            def exchange(method, url, **kwargs):
                if url.endswith("/1"):
                    raise RuntimeError("Temporary outage")
                return {"status": "completed"}

            with patch.object(api, "http", side_effect=exchange):
                media.check_receipts(receipts, {"radarr_api_key": "fixture"})
            self.assertIn("error", receipts["first"])
            self.assertEqual(receipts["second"]["phase"], "complete")
            self.assertTrue(source.exists())


class WireGuardSafety(unittest.TestCase):
    def test_hooks_are_removed_and_ipv6_endpoint_is_preserved(self):
        text = """[Interface]
PrivateKey = synthetic
Address = 10.77.0.1/32
DNS = 10.77.0.2, local.domain
PostUp = unwanted-command
Table = 123
FwMark = 123
[Peer]
PublicKey = synthetic
AllowedIPs = 0.0.0.0/1,128.0.0.0/1,::/0
Endpoint = [2001:db8::1]:51820
"""
        result, metadata = render(text)
        self.assertNotIn("unwanted-command", result)
        self.assertIn("Table = off", result)
        self.assertNotIn("FwMark = 123", result)
        self.assertEqual(
            metadata,
            {
                "dns": ["10.77.0.2"],
                "endpoints": [{"ip": "2001:db8::1", "port": 51820}],
                "ipv6": True,
            },
        )
        for invalid in [
            text.replace("0.0.0.0/1,128.0.0.0/1", "10.0.0.0/8"),
            text.replace(":51820", ":99999"),
            text.replace("[Interface]", "[Unknown]"),
        ]:
            with self.assertRaises(ValueError):
                parse(invalid)


if __name__ == "__main__":
    unittest.main()
