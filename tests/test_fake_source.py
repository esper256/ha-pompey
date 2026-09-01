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

    def test_qbit_records_magnet_add(self):
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
        self.assertIn(fs.INFOHASH, lines[0]["urls"])

    def test_held_download_stays_in_incomplete_not_library(self):
        delayed = fs.FakeState(
            Path(self.td.name) / "held",
            media_root=Path(self.td.name) / "held-media",
            finish_immediately=False,
        )
        library = delayed.media_root / "Movies" / "Not Kid Friendly"
        library.mkdir(parents=True)
        delayed.record_add({"urls": fs.MAGNET, "category": "radarr", "savepath": ""})
        incomplete_video = (
            delayed.incomplete / fs.RELEASE_DIR_NAME / f"{fs.RELEASE_DIR_NAME}.mkv"
        )
        self.assertTrue(incomplete_video.is_file())
        self.assertFalse(any(delayed.complete.rglob("*.mkv")))
        self.assertFalse(any(library.rglob("*.mkv")))

    def test_finish_moves_out_of_incomplete(self):
        delayed = fs.FakeState(
            Path(self.td.name) / "finish",
            media_root=Path(self.td.name) / "finish-media",
            finish_immediately=False,
        )
        delayed.record_add({"urls": fs.MAGNET, "category": "radarr", "savepath": ""})
        delayed.finish_held_torrents()
        self.assertFalse(any(p.is_file() for p in delayed.incomplete.rglob("*")))
        self.assertTrue(any(delayed.complete.rglob("*.mkv")))

    def test_pompey_finish_endpoint(self):
        held = fs.FakeState(
            Path(self.td.name) / "http-hold",
            media_root=Path(self.td.name) / "http-hold-media",
            finish_immediately=False,
        )
        qbit = fs.ThreadingHTTPServer(("127.0.0.1", 0), fs.qbit_handler(held))
        threading.Thread(target=qbit.serve_forever, daemon=True).start()
        time.sleep(0.05)
        held.record_add({"urls": fs.MAGNET, "category": "radarr"})
        conn = HTTPConnection("127.0.0.1", qbit.server_address[1], timeout=5)
        conn.request("POST", "/pompey/finish")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200, resp.read())
        conn.close()
        qbit.shutdown()
        qbit.server_close()
        self.assertTrue(any(held.complete.rglob("*.mkv")))
        self.assertFalse(any(p.is_file() for p in held.incomplete.rglob("*")))

    def test_fixture_is_not_the_old_torznab_path(self):
        self.assertFalse((ROOT / "tests/dev/torznab.py").exists())
        self.assertTrue((ROOT / "tests/lib/fake_source.py").is_file())


if __name__ == "__main__":
    unittest.main()
