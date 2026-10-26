"""Focused edge cases for safety logic; application contracts belong in the journey."""

import os
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
