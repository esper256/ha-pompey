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


class StorageInterruptionEvidence(unittest.TestCase):
    def test_outage_and_recovery_are_logged_without_repeated_probe_spam(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from storage_guard import StorageGuard
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder)
            (ready / "storage.json").write_text(json.dumps({"ok": True, "phase": "ready"}))
            guard = StorageGuard(ready)
            failure = {"ok": False, "failureKind": "mount_changed", "path": "/media/share/downloads", "reason": "Storage could not be accessed.",
                       "detail": "Input/output error (errno=5)"}

            def run(args, **kwargs):
                return SimpleNamespace(stdout=json.dumps(failure if args[-1] == "report" else {}))

            with patch("storage_guard.subprocess.run", side_effect=run) as commands, \
                 patch("storage_guard.subprocess.check_output", return_value="true"), \
                 patch("storage_guard.stack_lock", side_effect=lambda: nullcontext()), \
                 patch("storage_guard.emit") as log:
                guard.check()
                guard.check()
                self.assertTrue(guard.blocked)
                self.assertEqual(log.call_count, 1)
                self.assertIn("/media/share/downloads", log.call_args.args[1])
                self.assertIn("errno=5", log.call_args.args[1])
                self.assertEqual(sum(c.args[0][0:2] == ["s6-svc", "-d"] for c in commands.call_args_list), 3)
                failure = {"ok": False, "failureKind": "io", "reason": "Storage is read-only."}
                guard.check()
                self.assertEqual(guard.status["phase"], "paused")
                self.assertEqual(guard.status["heldServices"], ["qbittorrent", "radarr", "sonarr"])
                self.assertFalse(any(c.args[0][0:2] == ["s6-svc", "-u"] for c in commands.call_args_list))
                failure = {"ok": True}
                guard.check()
                guard.check()
                self.assertFalse(guard.blocked)
                self.assertEqual(log.call_count, 3)
                self.assertIn("restarting held applications: qbittorrent, radarr, sonarr", log.call_args.args[1])
                self.assertEqual(sum(c.args[0][0:2] == ["s6-svc", "-u"] for c in commands.call_args_list), 3)

    def test_io_failure_blocks_maintenance_without_interrupting_imports(self):
        from types import SimpleNamespace
        from storage_guard import StorageGuard
        with tempfile.TemporaryDirectory() as folder:
            guard = StorageGuard(Path(folder))
            for reason in ["Storage is full.", "Storage is read-only.", "Input/output error"]:
                with self.subTest(reason=reason), patch("storage_guard.emit"), \
                     patch("storage_guard.subprocess.run", return_value=SimpleNamespace(
                         stdout=json.dumps({"ok": False, "failureKind": "io", "reason": reason}))) as commands:
                    guard.check()
                    self.assertTrue(guard.blocked)
                    self.assertEqual(guard.status["phase"], "degraded")
                    self.assertEqual(commands.call_count, 1)
            with patch("storage_guard.emit"), patch("storage_guard.stack_lock"), \
                 patch("storage_guard.subprocess.run", return_value=SimpleNamespace(stdout='{"ok":true}')) as commands:
                guard.check()
                self.assertFalse(guard.blocked)
                self.assertEqual(commands.call_count, 1)
                self.assertIsNone(guard.status["failureKind"])

    def test_timed_out_probe_is_distinguishable_from_a_permission_failure(self):
        from contextlib import nullcontext
        import subprocess
        from storage_guard import StorageGuard
        with tempfile.TemporaryDirectory() as folder:
            guard = StorageGuard(Path(folder))

            def run(args, **kwargs):
                if args[-1] == "report":
                    raise subprocess.TimeoutExpired(args, 8)

            with patch("storage_guard.subprocess.run", side_effect=run) as commands, \
                 patch("storage_guard.subprocess.check_output", return_value="false"), \
                 patch("storage_guard.stack_lock", side_effect=lambda: nullcontext()), \
                 patch("storage_guard.emit") as log:
                guard.check()
                self.assertTrue(guard.blocked)
                self.assertIn("exceeded 8 seconds", log.call_args.args[1])
                self.assertEqual(guard.status["phase"], "degraded")
                self.assertEqual(commands.call_count, 1)
                self.assertEqual(commands.call_args.args[0][-1], "report")


class SupportLogEvidence(unittest.TestCase):

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
            original = {**preferences.DEFAULTS, "simultaneous_downloads": 3}
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
            self.assertEqual(preferences.current()["after_download"], "stop_sharing")
            state.save("preferences", {"values": {**preferences.DEFAULTS, "prefer_dual_audio": False}})
            self.assertEqual(api.after_download(), "stop_sharing")
            self.assertIn("score: 0", render_config("r", "key", "s", "key"))
            self.assertNotIn("score: 15", render_config("r", "key", "s", "key"))


class WorkflowEvidence(unittest.TestCase):
    def test_same_series_and_pack_entries_keep_distinct_native_identities(self):
        from pompey_diagnostics import workflow
        source = {"available": True, "updated": 100, "data": {"titles": []}}
        sources = {"radarr": source, "sonarr": source, "qbittorrent": source}
        titles = [{"kind": "sonarr", "id": 4, "title": "Example", "monitored": True}]
        queue = [{"id": ident, "seriesId": 4, "downloadId": token, "sizeleft": 10,
                  "episode": {"seasonNumber": 2, "episodeNumber": episode}}
                 for ident, token, episode in [(1, "ABC", 4), (2, "ABC", 5), (3, "DEF", 6)]]
        torrents = {token: {"state": "downloading"} for token in ["abc", "def"]}
        rows = workflow(sources, titles, {"sonarr": queue}, torrents)
        self.assertEqual(len({row["key"] for row in rows}), 3)
        self.assertEqual([row["scope"] for row in rows], ["S02E04", "S02E05", "S02E06"])
        queue[0]["title"] = "Changed release display name"
        changed = workflow(sources, titles, {"sonarr": list(reversed(queue))}, torrents)
        self.assertEqual({row["key"] for row in rows}, {row["key"] for row in changed})

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
        self.assertEqual([r["title"] for r in workflow(sources, titles, {}, {}, {("sonarr", 3)})], ["Partial"])

    def test_completed_torrent_is_not_proof_of_import_or_cleanup_eligibility(self):
        from pompey_diagnostics import completed_download
        torrent = {"progress": 1, "amount_left": 0, "state": "stoppedUP", "category": "radarr", "ratio": .5}
        self.assertEqual(completed_download(torrent, None, "stop_sharing")[0], "Completed · import unconfirmed")
        self.assertEqual(completed_download(torrent, {"trackedDownloadState": "imported"}, "stop_sharing")[0], "Imported · cleanup pending")
        self.assertEqual(completed_download(torrent, None, "share_to_ratio")[0], "Seeding target pending")
        self.assertEqual(completed_download(dict(torrent, category="personal"), None, "stop_sharing")[0], "Completed · unmanaged")
        self.assertIsNone(completed_download(dict(torrent, progress=.99), None, "stop_sharing"))

    def test_library_check_requires_healthy_storage_and_targets_one_existing_title(self):
        from pompey_diagnostics import check_library
        from unittest.mock import Mock
        call = Mock(return_value={"id": 7})
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root,POMPEY_READY=root), patch("pompey_recovery.api_for",return_value=call):
            with self.assertRaisesRegex(ValueError,"healthy"):
                check_library({"kind":"radarr","id":7})
            call.assert_not_called()
            Path(root,"storage.json").write_text('{"ok":true}')
            check_library({"kind":"radarr","id":7})
            self.assertEqual(call.call_args_list[-1].args, ("POST","/command",{"name":"RescanMovie","movieId":7}))
            with self.assertRaises(ValueError):
                check_library({"kind":"radarr","id":True})


class DeliveryReceipts(unittest.TestCase):
    def test_upgrade_never_backfills_a_release_rejected_in_060(self):
        import pompey_delivery as delivery
        movie={"id":7,"tmdbId":42,"title":"Example","path":"/media/dlna/Movies/Kid Friendly/Example","hasFile":False,"monitored":True}
        mutations=[]
        def call(method,path,body=None):
            if method == "PUT": mutations.append(body); return body
            return [movie] if path == "/movie" else []
        events=[{"eventType":"downloadFolderImported","downloadId":"a"*40,"data":{"importedPath":movie["path"]+"/old.mkv","fileId":"12"}}]
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root), patch("pompey_recovery.api_for",return_value=call), patch.object(delivery,"recent_history",return_value=events):
            state.save("release-sequence-old",{"kind":"radarr","identity":{"tmdbId":42},"attempts":[{"downloadId":"a"*40,"target":{"libraryFiles":[{"id":12}]}}]})
            delivery.reconcile()
            self.assertFalse(delivery.for_title("radarr",movie))
            self.assertEqual(mutations,[])
            saved=state.load("deliveries")
            self.assertTrue(saved["rejectionsImported"])
            # A partial native-removal failure must not turn the rejected file
            # still on disk into new successful-delivery evidence either.
            receipts={}
            self.assertFalse(delivery.record_file(receipts,"movie",{"id":12},"old",excluded=saved["titles"]["radarr:42"]))
            self.assertTrue(delivery.record_file(receipts,"movie",{"id":13},"new",excluded=saved["titles"]["radarr:42"]))

    def test_delivered_episode_stays_unmonitored_without_stopping_future_episodes(self):
        import pompey_delivery as delivery
        calls=[]
        def call(method,path,body=None):
            calls.append((method,path,body))
            if method == "PUT": return body
            if path == "/series": return [{"id":7,"tvdbId":42,"title":"Example","path":"/media/dlna/TV/Example"}]
            if path.startswith("/episode?"): return [{"id":8,"seasonNumber":1,"episodeNumber":1,"monitored":True}, {"id":9,"seasonNumber":1,"episodeNumber":2,"monitored":True}]
            return []
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root), patch("pompey_recovery.api_for",return_value=call), patch.object(delivery,"recent_history",return_value=[]):
            state.save("deliveries",{"titles":{"sonarr:42":{"receipts":{"1:1":{"status":"delivered","fileId":10}}}}})
            delivery.reconcile()
            self.assertEqual([c for c in calls if c[0] == "PUT"], [("PUT","/episode/monitor",{"episodeIds":[8],"monitored":False})])

    def test_only_final_library_history_can_backfill_a_missing_movie(self):
        import pompey_delivery as delivery
        movie = {"id":7,"tmdbId":42,"title":"Example","path":"/media/dlna/Movies/Kid Friendly/Example","hasFile":False,"monitored":True}
        for imported, expected in [(movie["path"]+"/movie.mkv",True),("/media/dlna/downloads/By Rating/Movies/Example/movie.mkv",False)]:
            calls=[]
            def call(method,path,body=None):
                calls.append((method,path,body))
                if method == "PUT": return body
                return [movie] if path == "/movie" else []
            events=[{"eventType":"downloadFolderImported","downloadId":"a"*40,"data":{"importedPath":imported,"fileId":"12"}}]
            with self.subTest(path=imported), tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root), patch("pompey_recovery.api_for",return_value=call), patch("pompey_delivery.recent_history",return_value=events):
                delivery.reconcile()
                receipt=delivery.for_title("radarr",movie).get("movie")
                self.assertEqual(bool(receipt), expected)
                self.assertEqual(any(method=="PUT" for method,_,_ in calls),expected)
                if expected:
                    self.assertTrue(receipt["historical"])
                    self.assertEqual(receipt["fileId"],12)

    def test_delivery_survives_missing_file_but_rejection_requires_a_new_file(self):
        import pompey_delivery as delivery
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root):
            receipts = {}
            delivery.record_file(receipts,"movie",{"id":10},"/media/Movies/Original.mkv")
            state.save("deliveries",{"titles":{"radarr:42":{"receipts":receipts}}})
            self.assertEqual(delivery.for_title("radarr",{"tmdbId":42})["movie"]["status"],"delivered")
            self.assertEqual(delivery.for_title("radarr",{"tmdbId":43}),{})
            delivery.reopen({"kind":"radarr","identity":{"tmdbId":42},"libraryFiles":[],"downloadId":"a"*40})
            receipts = delivery.for_title("radarr",{"tmdbId":42})
            self.assertFalse(delivery.record_file(receipts,"movie",{"id":10},"old"))
            self.assertEqual(receipts["movie"]["status"],"retrying")
            self.assertTrue(delivery.record_file(receipts,"movie",{"id":11},"new"))
            self.assertEqual(receipts["movie"]["status"],"delivered")

    def test_episode_rejection_reopens_only_affected_coordinates(self):
        import pompey_delivery as delivery
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root):
            state.save("deliveries",{"titles":{"sonarr:42":{"receipts":{"1:1":{"status":"delivered","fileId":10},"1:2":{"status":"delivered","fileId":11}}}}})
            delivery.reopen({"kind":"sonarr","identity":{"tvdbId":42},"episodes":[{"season":1,"episode":1}],"libraryFiles":[{"id":10}],"downloadId":"a"*40})
            receipts=delivery.for_title("sonarr",{"tvdbId":42})
            self.assertEqual(receipts["1:1"]["status"],"retrying")
            self.assertEqual(receipts["1:2"]["status"],"delivered")
            from pompey_recovery import summarize
            sequence={"attempts":[{"target":{"episodes":[{"season":1,"episode":1},{"season":1,"episode":2}]}}]}
            self.assertEqual(summarize(sequence)["scope"],"S01E01, S01E02")

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
    def test_stored_seeding_policy_normalizes_without_losing_other_preferences(self):
        import pompey_preferences as preferences
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, POMPEY_DATA=tmp):
            old = {**preferences.DEFAULTS, "after_download": "share_one_day", "simultaneous_downloads": 3}
            state.save("preferences", {"values": old})
            state.save("preferences-applied", {"values": old})
            self.assertEqual(preferences.current(), {**old, "after_download": "stop_sharing"})
            self.assertTrue(preferences.status()["pending"])

    def test_share_limit_overrides_only_apply_to_managed_torrents(self):
        managed = {"hash": "owned", "category": "sonarr", "ratio_limit": 1}
        with patch.object(api, "http") as http:
            media.apply_managed_share_limits([managed, {**managed, "hash": "unknown", "category": "personal"}])
            self.assertEqual(http.call_count, 1)
            self.assertIn(b"hashes=owned&ratioLimit=0", http.call_args.args[2])
            self.assertNotIn(b"unknown", http.call_args.args[2])
        with patch.object(api, "http") as http:
            media.apply_managed_share_limits([{**managed, "ratio_limit": 0, "seeding_time_limit": -1,
                                               "inactive_seeding_time_limit": -1}])
            http.assert_not_called()

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


class ReleaseRecoverySafety(unittest.TestCase):
    def test_stale_confirmation_never_changes_files_or_writes_intent(self):
        import pompey_recovery as recovery
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root,POMPEY_READY=root):
            Path(root,'storage.json').write_text('{"ok":true}')
            with patch.object(recovery,'inspect_target',return_value=({'token':'new'},None,None,None,None)):
                with self.assertRaisesRegex(ValueError,'changed'):
                    recovery.reject({'confirm':True,'kind':'radarr','id':1,'token':'old','confirmationId':'a'*32})
            self.assertEqual(list(state.directory().glob('release-sequence-*')),[])

    def test_native_removal_failure_keeps_durable_pre_removal_evidence(self):
        import pompey_recovery as recovery
        target={'kind':'radarr','id':1,'title':'Real title','release':'Chosen.release',
                'identity':{'tmdbId':12},'episodeIds':[],'token':'fingerprint','downloadId':'a'*40,
                'libraryFiles':[],'queueIds':[5],'removeDownload':True,'grabId':4}
        rows=[{'id':4,'movieId':1,'date':'2026-10-27T10:00:00Z','eventType':'grabbed',
               'downloadId':'a'*40,'sourceTitle':'Chosen.release','data':{}}]
        def call(method,path,body=None):
            if path=='/config/downloadclient':return {'autoRedownloadFailed':False}
            if path.startswith('/qualityprofile'):return {'id':1,'name':'Default'}
            if path.startswith('/release?'):return []
            if method=='DELETE':
                stored=recovery.read_sequence(recovery.sequence_paths()[0])
                self.assertEqual(stored['attempts'][0]['stage'],'prepared')
                self.assertEqual(stored['nativeHistory'][0]['sourceTitle'],'Chosen.release')
                raise RuntimeError('downloader unavailable')
            self.fail((method,path))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,POMPEY_DATA=root,POMPEY_READY=root):
            Path(root,'storage.json').write_text('{"ok":true}')
            with patch.object(recovery,'inspect_target',return_value=(target,call,{'qualityProfileId':1},rows,None)):
                with self.assertRaisesRegex(ValueError,'interrupted'):
                    recovery.reject({'confirm':True,'token':'fingerprint','confirmationId':'a'*32,'reason':'stalled','note':'Stopped progressing'})
            saved=recovery.read_sequence(recovery.sequence_paths()[0])
            self.assertEqual(saved['status'],'Needs attention')
            self.assertIn('downloader unavailable',saved['attempts'][0]['error'])
            from pompey_report import collect_report
            with patch.object(recovery,'reconcile'):
                with patch('pompey_report.reconcile'):
                    report=json.loads(collect_report().split('\n\n',1)[1])
            self.assertEqual(report['schema'],'pompey-release-sequences-v1')
            self.assertEqual(report['sequences'][0]['attempts'][0]['note'],'Stopped progressing')
            self.assertNotIn('logs',report)
            self.assertNotIn('applications',report)

    def test_import_plan_only_includes_current_files_from_the_selected_grab(self):
        import pompey_recovery as recovery
        token='a'*40
        native=[{'id':1,'seriesId':7,'relativePath':'S01E01.mkv','size':10},
                {'id':2,'seriesId':7,'relativePath':'S01E02.mkv','size':10},
                {'id':3,'seriesId':7,'relativePath':'S02E01.mkv','size':10}]
        rows=[{'id':1,'seriesId':7,'episodeId':1,'eventType':'grabbed','downloadId':token,'sourceTitle':'Release.A'},
              {'id':2,'seriesId':7,'episodeId':2,'eventType':'grabbed','downloadId':token,'sourceTitle':'Release.A'},
              {'id':3,'seriesId':7,'eventType':'downloadFolderImported','downloadId':token,'data':{'fileId':'1'}},
              {'id':4,'seriesId':7,'eventType':'downloadFolderImported','downloadId':token,'data':{'fileId':'2'}},
              {'id':5,'seriesId':7,'eventType':'downloadFolderImported','downloadId':'b'*40,'data':{'fileId':'3'}}]
        def call(method,path,body=None):
            if path=='/series/7':return {'id':7,'title':'Example','path':'/media/dlna/TV/Example'}
            if path.startswith('/episodefile?'):return native
            if path.startswith('/queue?'):return {'records':[],'totalRecords':0}
            self.fail((method,path))
        with patch.object(recovery,'api_for',return_value=call), patch.object(recovery,'history',return_value=rows), \
             patch.object(api,'http',return_value=[]),patch.object(api,'media_root',return_value='/media/dlna'):
            plan=recovery.preview({'kind':'sonarr','id':7,'fileId':1})
            self.assertEqual([r['id'] for r in plan['libraryFiles']],[1,2])
            self.assertEqual(plan['episodeIds'],[1,2])
            native[0]['relativePath']='../../../../etc/passwd'
            with self.assertRaisesRegex(ValueError,'outside'):
                recovery.preview({'kind':'sonarr','id':7,'fileId':1})

    def test_imported_outcomes_never_count_a_rejected_release_as_success(self):
        import pompey_recovery as recovery
        seq={'kind':'sonarr','id':7,'episodeIds':[1,2],'started':0,'status':'Retrying','attempts':[{'at':0,'downloadId':'a'*40}], 'nativeHistory':[]}
        rows=[{'id':i,'date':'2026-10-27T10:00:00Z','eventType':'downloadFolderImported','episodeId':episode,'downloadId':token,'data':{}}
              for i,episode,token in [(1,1,'a'*40),(2,2,'a'*40),(3,1,'b'*40)]]
        recovery.merge_history(seq,rows)
        self.assertEqual(seq['status'],'Retrying')
        recovery.merge_history(seq,[{'id':4,'date':'2026-10-27T10:00:00Z','eventType':'downloadFolderImported','episodeId':2,'downloadId':'b'*40,'data':{}}])
        self.assertEqual(seq['status'],'Imported')

if __name__ == "__main__":
    unittest.main()
