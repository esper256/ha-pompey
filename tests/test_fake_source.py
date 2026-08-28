#!/usr/bin/env python3
"""Fake Torznab + qBittorrent WebUI: HTTP only, no torrent client."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
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
        self.td = tempfile.TemporaryDirectory(prefix="pompey-fake-source-")
        work = Path(self.td.name)
        self.state = fs.FakeState(work)
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

    def test_tv_category_search_is_empty(self):
        _, body = self._get(self.tz + "/api?t=search&cat=5000,5040")
        text = body.decode()
        self.assertNotIn("<item>", text)

    def test_movie_category_search_returns_item(self):
        _, body = self._get(self.tz + "/api?t=search&cat=2000,2040")
        self.assertIn(fs.RELEASE_TITLE, body.decode())

    def test_unrelated_query_is_empty(self):
        _, body = self._get(self.tz + "/api?t=search&q=no-such-title-zzzz")
        text = body.decode()
        self.assertNotIn(fs.RELEASE_TITLE, text)
        self.assertNotIn("<item>", text)

    def test_qbit_records_magnet_add(self):
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
            "/api/v2/torrents/add",
            body=urllib.parse.urlencode(
                {"urls": fs.MAGNET, "category": "radarr", "savepath": "/media/downloads"}
            ),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": (cookie or "").split(";", 1)[0],
            },
        )
        added = conn.getresponse()
        self.assertEqual(added.status, 200)
        added.read()
        conn.close()
        lines = [
            json.loads(line)
            for line in self.state.adds_path.read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["urls"], fs.MAGNET)
        self.assertEqual(lines[0]["category"], "radarr")
        self.assertIn(fs.INFOHASH, lines[0]["urls"])

    def test_fixture_is_not_the_old_torznab_path(self):
        self.assertFalse((ROOT / "tests/dev/torznab.py").exists())
        self.assertTrue((ROOT / "tests/lib/fake_source.py").is_file())


if __name__ == "__main__":
    unittest.main()
