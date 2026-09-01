#!/usr/bin/env python3
"""Fake Torznab + qBittorrent WebUI: HTTP only, no torrent client."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "tests/lib/fake_source.py"
    loader = importlib.machinery.SourceFileLoader("fake_source", str(path))
    spec = importlib.util.spec_from_loader("fake_source", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


fs = _load()


class FakeSourceHTTP(unittest.TestCase):
    def setUp(self):
        os.environ["POMPEY_FAKE_VIDEO_BYTES"] = "65536"
        self.td = tempfile.TemporaryDirectory(prefix="pompey-fake-source-")
        work = Path(self.td.name)
        self.media = work / "media"
        self.state = fs.FakeState(work, media_root=self.media)
        self.torznab = fs.ThreadingHTTPServer(("127.0.0.1", 0), fs.torznab_handler(self.state))
        self.qbit = fs.ThreadingHTTPServer(("127.0.0.1", 0), fs.qbit_handler(self.state))
        threading.Thread(target=self.torznab.serve_forever, daemon=True).start()
        threading.Thread(target=self.qbit.serve_forever, daemon=True).start()
        time.sleep(0.05)
        self.tz = f"http://127.0.0.1:{self.torznab.server_address[1]}"
        self.qb = f"http://127.0.0.1:{self.qbit.server_address[1]}"

    def tearDown(self):
        self.torznab.shutdown()
        self.qbit.shutdown()
        self.torznab.server_close()
        self.qbit.server_close()
        self.td.cleanup()

    def _get(self, url: str) -> tuple[int, bytes]:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()

    def _qbit_form(self, path: str, fields: dict[str, str]) -> None:
        conn = HTTPConnection("127.0.0.1", self.qbit.server_address[1], timeout=5)
        conn.request(
            "POST",
            "/api/v2/auth/login",
            body=urllib.parse.urlencode({"username": "pompey", "password": "x"}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        login = conn.getresponse()
        login.read()
        self.assertEqual(login.status, 200)
        cookie = login.getheader("Set-Cookie")
        self.assertIn("SID=", cookie or "")
        conn.request(
            "POST",
            path,
            body=urllib.parse.urlencode(fields),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": (cookie or "").split(";", 1)[0],
            },
        )
        resp = conn.getresponse()
        body = resp.read()
        self.assertEqual(resp.status, 200, body)
        conn.close()

    def test_caps_is_torznab(self):
        _, body = self._get(self.tz + "/api?t=caps")
        text = body.decode()
        self.assertIn("<caps>", text)
        self.assertIn("movie-search", text)
        self.assertIn('id="2040"', text)

    def test_search_returns_wild_robot_magnet(self):
        q = urllib.parse.quote("The Wild Robot")
        _, body = self._get(self.tz + f"/api?t=movie&q={q}&apikey={fs.API_KEY}")
        text = body.decode()
        self.assertIn(fs.RELEASE_TITLE, text)
        self.assertIn(fs.INFOHASH, text)
        self.assertIn(str(fs.TMDB_ID), text)
        self.assertIn("magnet:?xt=urn:btih:", text)

    def test_tv_category_search_returns_tv_not_movie(self):
        _, body = self._get(self.tz + "/api?t=search&cat=5000,5040")
        text = body.decode()
        self.assertIn(fs.TV_TITLE, text)
        self.assertIn('name="category" value="5040"', text)
        self.assertNotIn(fs.RELEASE_TITLE, text)
        self.assertNotIn(fs.INFOHASH, text)

    def test_tvsearch_empty_returns_tv_item(self):
        _, body = self._get(self.tz + "/api?t=tvsearch")
        text = body.decode()
        self.assertIn(fs.TV_TITLE, text)
        self.assertNotIn(fs.RELEASE_TITLE, text)

    def test_movie_category_search_returns_item(self):
        _, body = self._get(self.tz + "/api?t=search&cat=2000,2040")
        text = body.decode()
        self.assertIn(fs.RELEASE_TITLE, text)
        self.assertNotIn(fs.TV_TITLE, text)

    def test_unrelated_query_is_empty(self):
        _, body = self._get(self.tz + "/api?t=search&q=no-such-title-zzzz")
        text = body.decode()
        self.assertNotIn(fs.RELEASE_TITLE, text)
        self.assertNotIn(fs.TV_TITLE, text)
        self.assertNotIn("<item>", text)

    def test_qbit_disables_dht_pex_lsd(self):
        _, body = self._get(self.qb + "/api/v2/app/preferences")
        prefs = json.loads(body.decode())
        self.assertFalse(prefs["dht"])
        self.assertFalse(prefs["pex"])
        self.assertFalse(prefs["lsd"])
        self.assertEqual(prefs["save_path"], str(self.media / "downloads" / "complete"))

    def test_qbit_records_magnet_add_and_materializes_complete(self):
        self._qbit_form(
            "/api/v2/torrents/add",
            {"urls": fs.MAGNET, "category": "radarr", "savepath": ""},
        )
        lines = [
            json.loads(line)
            for line in self.state.adds_path.read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["urls"], fs.MAGNET)
        self.assertEqual(lines[0]["category"], "radarr")
        self.assertIn(fs.INFOHASH, lines[0]["urls"])
        video = (
            self.media
            / "downloads"
            / "complete"
            / fs.RELEASE_DIR_NAME
            / f"{fs.RELEASE_DIR_NAME}.mkv"
        )
        self.assertTrue(video.is_file(), f"missing {video}")
        self.assertGreaterEqual(video.stat().st_size, 8)
        incomplete = list((self.media / "downloads" / "incomplete").rglob("*"))
        self.assertFalse(any(path.is_file() for path in incomplete))
        _, body = self._get(self.qb + "/api/v2/torrents/" + "info")
        rows = json.loads(body.decode())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hash"], fs.INFOHASH)
        self.assertEqual(rows[0]["state"], "uploading")
        self.assertEqual(rows[0]["progress"], 1)
        self.assertEqual(rows[0]["amount_left"], 0)
        self.assertEqual(rows[0]["content_path"], str(video.parent))
        self.assertTrue(str(rows[0]["save_path"]).endswith("downloads/complete"))

    def test_qbit_moves_incomplete_then_complete(self):
        delayed = fs.FakeState(
            Path(self.td.name) / "delayed",
            media_root=Path(self.td.name) / "delayed-media",
            finish_immediately=False,
        )
        delayed.record_add({"urls": fs.MAGNET, "category": "radarr", "savepath": ""})
        incomplete_video = (
            delayed.incomplete / fs.RELEASE_DIR_NAME / f"{fs.RELEASE_DIR_NAME}.mkv"
        )
        self.assertTrue(incomplete_video.is_file())
        self.assertFalse((delayed.complete / fs.RELEASE_DIR_NAME).exists())
        rows = delayed.list_torrents()
        self.assertEqual(rows[0]["state"], "downloading")
        self.assertLess(rows[0]["progress"], 1)
        delayed.finish_torrent(fs.INFOHASH)
        self.assertFalse(incomplete_video.exists())
        complete_video = delayed.complete / fs.RELEASE_DIR_NAME / f"{fs.RELEASE_DIR_NAME}.mkv"
        self.assertTrue(complete_video.is_file())
        rows = delayed.list_torrents()
        self.assertEqual(rows[0]["state"], "uploading")
        self.assertEqual(rows[0]["progress"], 1)
        self.assertEqual(rows[0]["content_path"], str(complete_video.parent))

    def test_qbit_stop_then_delete_keeps_files(self):
        self._qbit_form(
            "/api/v2/torrents/add",
            {"urls": fs.MAGNET, "category": "radarr"},
        )
        video = (
            self.media
            / "downloads"
            / "complete"
            / fs.RELEASE_DIR_NAME
            / f"{fs.RELEASE_DIR_NAME}.mkv"
        )
        self._qbit_form("/api/v2/torrents/" + "stop", {"hashes": fs.INFOHASH})
        _, body = self._get(self.qb + "/api/v2/torrents/" + "info")
        rows = json.loads(body.decode())
        self.assertEqual(rows[0]["state"], "stoppedUP")
        self._qbit_form(
            "/api/v2/torrents/delete",
            {"hashes": fs.INFOHASH, "deleteFiles": "false"},
        )
        _, body = self._get(self.qb + "/api/v2/torrents/" + "info")
        self.assertEqual(json.loads(body.decode()), [])
        self.assertTrue(video.is_file())
        deletes = [
            json.loads(line)
            for line in self.state.deletes_path.read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(deletes[0]["deleteFiles"], "false")

    def test_qbit_delete_files_true_removes_payload(self):
        self._qbit_form("/api/v2/torrents/add", {"urls": fs.MAGNET, "category": "radarr"})
        folder = self.media / "downloads" / "complete" / fs.RELEASE_DIR_NAME
        self.assertTrue(folder.is_dir())
        self._qbit_form(
            "/api/v2/torrents/delete",
            {"hashes": fs.INFOHASH, "deleteFiles": "true"},
        )
        self.assertFalse(folder.exists())

    def test_fixture_is_not_the_old_torznab_path(self):
        self.assertFalse((ROOT / "tests/dev/torznab.py").exists())
        self.assertTrue((ROOT / "tests/lib/fake_source.py").is_file())


if __name__ == "__main__":
    unittest.main()
