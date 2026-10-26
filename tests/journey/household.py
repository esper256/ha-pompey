"""Common real application setup, clients, identity and background-state checks."""

import json
from pathlib import Path
import subprocess
from support import FileChecks
from external import PEER_IP


def tunnel_bytes():
    rows = subprocess.check_output(
        ["wg", "show", "wg0", "transfer"], text=True
    ).splitlines()
    return sum(int(row.split()[1]) for row in rows)


class Household:
    def __init__(self, secrets, session, evidence):
        self.session = session
        self.evidence = evidence
        self.secrets = secrets
        self.skey = ""

    def arr(self, method, path, body=None):
        return self.session.call(
            method,
            "http://127.0.0.1:7878/api/v3" + path,
            body,
            self.secrets["radarr_api_key"],
        )

    def prow(self, method, path, body=None):
        return self.session.call(
            method,
            "http://127.0.0.1:9696/api/v1" + path,
            body,
            self.secrets["prowlarr_api_key"],
        )

    def search(self, method, path, body=None):
        return self.session.call(
            method, "http://127.0.0.1:5055/api/v1" + path, body, self.skey
        )

    def qbit(self, path, body=None):
        return self.session.call(
            "GET" if body is None else "POST",
            "http://127.0.0.1:8080/api/v2/" + path,
            body,
            form=True,
        )

    def sonarr(self, method, path, body=None):
        return self.session.call(
            method,
            "http://127.0.0.1:8989/api/v3" + path,
            body,
            self.secrets["sonarr_api_key"],
        )

    def peer(self, case):
        return self.session.call(
            "GET", f"http://{PEER_IP}:18080/control/status/{case.movie_id}"
        )

    def release(self, case):
        return self.session.call(
            "POST", f"http://{PEER_IP}:18080/control/release/{case.movie_id}", {}
        )

    def wait(self, predicate, message, seconds=60):
        return self.session.deadline.wait(predicate, message, seconds)

    def wait_command(self, ident, service="radarr"):
        api = self.arr if service == "radarr" else self.sonarr

        def complete():
            result = api("GET", "/command/" + str(ident))
            if result["status"] in {"failed", "aborted", "cancelled"}:
                raise AssertionError("Native command failed: " + json.dumps(result))
            return result["status"] == "completed" and result

        return self.wait(complete, service + " command did not complete")

    def refresh_downloads(self):
        command = self.arr("POST", "/command", {"name": "RefreshMonitoredDownloads"})
        return self.wait_command(command["id"])


def identities():
    found = {}
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            body = path.read_text()
            cmd = (path.parent / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeError):
            continue
        if not any(
            token in cmd
            for token in [
                "/Radarr/Radarr",
                "/Sonarr/Sonarr",
                "/Prowlarr/Prowlarr",
                "--confirm-legal-notice",
                "node dist/index.js",
            ]
        ):
            continue
        uid = next(
            line for line in body.splitlines() if line.startswith("Uid:")
        ).split()[1:]
        assert set(uid) == {"1000"}, (cmd, uid)
        caps = {
            line.split(":")[0]: int(line.split()[1], 16)
            for line in body.splitlines()
            if line.startswith(("CapEff:", "CapPrm:", "CapAmb:"))
        }
        assert not any(caps.values()), (cmd, caps)
        found[cmd] = uid
    assert len(found) == 5, found
    return found


def configure(client, restored):
    wait = client.wait
    stamp = client.evidence.stamp
    call = client.session.call
    for name, url in [
        ("Radarr", "http://127.0.0.1:7878/api/v3/system/status"),
        ("Sonarr", "http://127.0.0.1:8989/api/v3/system/status"),
        ("Prowlarr", "http://127.0.0.1:9696/api/v1/system/status"),
        ("Seerr", "http://127.0.0.1:5055/api/v1/settings/public"),
    ]:
        wait(
            lambda: call("GET", url, key=client.secrets.get(name.lower() + "_api_key")),
            name + " did not start",
        )
    client.qbit(
        "app/setPreferences",
        {
            "json": json.dumps(
                {
                    "dht": False,
                    "pex": False,
                    "lsd": False,
                    "upnp": False,
                    "bittorrent_protocol": 1,
                }
            )
        },
    )
    assert client.qbit("app/preferences")["bittorrent_protocol"] == 1
    wait(
        lambda: any(
            int(line.split()[1]) > 0
            for line in subprocess.check_output(
                ["wg", "show", "wg0", "latest-handshakes"], text=True
            ).splitlines()
        ),
        "Local WireGuard handshake failed",
    )
    stamp("applications-running")
    if not restored:
        assert client.search("GET", "/settings/public")["initialized"] is False
        assert client.arr("GET", "/movie") == [], (
            "Fresh-install journey has existing titles"
        )
    # Create the first administrator before indexer setup can delay it past reconcile.
    client.search("POST", "/auth/plex", {"authToken": "synthetic-token"})
    client.skey = client.search("GET", "/settings/main")["apiKey"]
    stamp("seerr-account-ready")
    if not restored:
        client.search(
            "POST", "/settings/plex", {"ip": PEER_IP, "port": 80, "useSsl": False}
        )
        schemas = client.prow("GET", "/indexer/schema")
        indexer = next(row for row in schemas if row["implementation"] == "Torznab")
        values = {
            "baseUrl": f"http://{PEER_IP}:18080",
            "apiPath": "/api",
            "apiKey": "fixture",
        }
        for field in indexer["fields"]:
            if field["name"] in values:
                field["value"] = values[field["name"]]
        indexer.update(
            name="Synthetic source", enable=True, appProfileId=1, priority=25, tags=[]
        )
        client.prow("POST", "/indexer", indexer)
        stamp("source-added")
    wait(
        lambda: (
            client.search("GET", "/settings/radarr")
            and client.search("GET", "/settings/sonarr")
        ),
        "Controller did not connect both Arr apps",
        120,
    )
    if not restored:
        client.search("POST", "/settings/initialize", {})
    assert client.search("GET", "/settings/public")["initialized"] is True
    wait(
        lambda: client.arr("GET", "/indexer"),
        "Prowlarr did not synchronize into Radarr",
    )
    stamp("household-setup-complete")


class Background:
    """Check all earlier objects without re-reading every unchanged film per case."""

    def __init__(self, ledger):
        self.ledger = ledger
        self.files = FileChecks()

    def verify(self, client, full=False):
        if not self.ledger:
            return
        movies = {row["id"]: row for row in client.arr("GET", "/movie")}
        requests = {}
        skip = 0
        while True:
            rows = client.search("GET", f"/request?take=100&skip={skip}")["results"]
            for row in rows:
                if row["id"] in requests:
                    raise AssertionError("Request pagination repeated an identity")
                requests[row["id"]] = row
            if len(rows) < 100:
                break
            skip += len(rows)
            if skip > 10000:
                raise AssertionError("Request pagination did not terminate")
        for row in self.ledger:
            request = requests.get(row["requestId"])
            assert request and request["media"]["tmdbId"] == row["tmdbId"], (
                "Earlier request changed",
                row,
                request,
            )
            movie = movies.get(row["arrId"])
            assert (
                movie
                and movie["tmdbId"] == row["tmdbId"]
                and movie["title"] == row["prefix"]
                and movie["hasFile"]
            ), ("Earlier title changed", row, movie)
            for path, key in [
                (row["path"], "sha256"),
                (row["subtitle"], "subtitleSha256"),
            ]:
                self.files.verify(path, row[key], full=full)
