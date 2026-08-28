#!/usr/bin/env python3
"""Run wire-stack against a real Seerr image.

HTTP fakes of Seerr hid POST /auth/local 403 (login-only) and would hide the
next contract change too. qBittorrent and Torznab stay fake: tests must not
speak BitTorrent. Arr/Prowlarr stay fake here so this is Seerr's API, not a
full stack boot.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ["POMPEY_WAIT_TRIES"] = "40"
os.environ["POMPEY_WAIT_SLEEP"] = "0.25"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/lib"))
sys.path.insert(0, str(ROOT / "tests"))

import seerr_runtime as seerr  # noqa: E402
import test_python as tp  # noqa: E402

ws = tp.ws


class RealSeerrWire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seerr = seerr.SeerrProcess().start()
        cls.state = tp.FakeState()
        cls.servers = []
        cls.urls = {}
        for role in ("qbit", "sonarr", "radarr", "prowlarr"):
            httpd, url = tp.start_role(role, cls.state)
            cls.servers.append(httpd)
            cls.urls[role] = url

    @classmethod
    def tearDownClass(cls):
        for httpd in cls.servers:
            httpd.shutdown()
            httpd.server_close()
        cls.seerr.stop()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pompey-seerr-wire-"))
        secrets = {
            "sonarr_api_key": "sonarr-key",
            "radarr_api_key": "radarr-key",
            "prowlarr_api_key": "prowlarr-key",
            "qbit_user": "pompey",
            "qbit_password": "secret",
            "seerr_email": "pompey@local",
            "seerr_password": "seerr-secret",
        }
        (self.tmp / "secrets.json").write_text(json.dumps(secrets))
        ready = self.tmp / "ready"
        ready.mkdir()
        os.environ.update(
            {
                "POMPEY_SECRETS": str(self.tmp / "secrets.json"),
                "POMPEY_READY": str(ready),
                "MEDIA_ROOT": "/media",
                "PLEX_URL": "",
                "PLEX_TOKEN": "",
                "INDEXER_URL": "",
                "INDEXER_API_KEY": "",
                "QBIT_URL": self.urls["qbit"],
                "SONARR_URL": self.urls["sonarr"],
                "RADARR_URL": self.urls["radarr"],
                "PROWLARR_URL": self.urls["prowlarr"],
                "SEERR_URL": self.seerr.url,
                "SEERR_CONFIG": str(self.seerr.config),
                "NGINX_INGRESS_CONF": str(self.tmp / "ingress.conf"),
                "INGRESS_PORT": "8099",
            }
        )
        self.ready = ready
        self._old_path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self._old_path

    def test_auth_local_is_403_and_search_is_marked_ready(self):
        req = urllib.request.Request(
            self.seerr.url + "/api/v1/auth/local",
            data=json.dumps(
                {"email": "pompey@local", "password": "seerr-secret", "name": "Pompey"}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

        settings = self.seerr.settings()
        key = (settings.get("main") or {}).get("apiKey")
        self.assertTrue(key, "real Seerr writes main.apiKey on first start")

        arr_req = urllib.request.Request(
            self.seerr.url + "/api/v1/settings/radarr",
            headers={"X-API-Key": key},
        )
        with self.assertRaises(urllib.error.HTTPError) as arr_ctx:
            urllib.request.urlopen(arr_req, timeout=10)
        self.assertEqual(arr_ctx.exception.code, 403)

        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertFalse((self.ready / "seerr-arr").exists())
        self.assertFalse((self.tmp / "ingress.conf").exists())

        appdata = json.loads(
            urllib.request.urlopen(
                self.seerr.url + "/api/v1/status/appdata", timeout=10
            ).read()
        )
        self.assertTrue(
            appdata.get("appData"),
            f"Seerr must not warn that the config dir is ephemeral: {appdata}",
        )
        self.assertTrue(appdata.get("appDataPermissions"), appdata)
        self.assertFalse(
            (self.seerr.config / "DOCKER").exists(),
            "DOCKER sentinel makes Seerr think /config is not persisted",
        )

        public = json.loads(
            urllib.request.urlopen(
                self.seerr.url + "/api/v1/settings/public", timeout=10
            ).read()
        )
        self.assertFalse(public.get("initialized"))


if __name__ == "__main__":
    unittest.main()
