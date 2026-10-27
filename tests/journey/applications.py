"""Common real application setup, clients, identity and background-state checks."""

import json
from pathlib import Path
import subprocess
import time
from support import FileChecks, NativeCommandFailed, BackgroundRegression
from external import PEER_IP


def tunnel_bytes():
    rows = subprocess.check_output(
        ["wg", "show", "wg0", "transfer"], text=True
    ).splitlines()
    return sum(int(row.split()[1]) for row in rows)


class ApplicationStack:
    def __init__(self, secrets, session, evidence):
        self.session = session
        self.evidence = evidence
        self.secrets = secrets
        self.skey = ""
        self.created_at = time.time()

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
                raise NativeCommandFailed(
                    "Native command failed: " + json.dumps(result)
                )
            return result["status"] == "completed" and result

        return self.wait(complete, service + " command did not complete")

    def refresh_downloads(self):
        command = self.arr("POST", "/command", {"name": "RefreshMonitoredDownloads"})
        return self.wait_command(command["id"])

    def service(self, name, up):
        if name not in {"wire"}:
            raise ValueError("Unknown controlled service")
        path = "/run/service/" + name
        subprocess.run(["s6-svc", "-u" if up else "-d", path], check=True, timeout=3)
        if not up:
            subprocess.run(
                ["s6-svwait", "-d", "-t", "10000", path], check=True, timeout=12
            )

    def health_snapshot(self, since=None):
        configuration = json.loads(Path("/tmp/pompey/configuration.json").read_text())
        health = self.session.call("GET", "http://127.0.0.1:8099/health.json")
        status = self.session.call("GET", "http://127.0.0.1:8099/status.json")
        self.evidence.data["lastHealth"] = {
            "configuration": configuration,
            "health": health,
            "status": status,
        }
        return (
            configuration.get("ok") is True
            and (since is None or configuration["updated"] >= since)
            and status["step"] == "ready"
            and status["label"] == "Ready"
            and status["need_vpn"] is False
            and all(health["services"].values())
            and all(
                j["failures"] == 0
                and j["lastSuccess"] is not None
                and (since is None or j["lastSuccess"] >= since)
                for j in health["jobs"].values()
            )
        )

    def healthy(self, since):
        self.wait(
            lambda: self.health_snapshot(since),
            "Controller/configuration/sidebar did not become healthy",
            45,
        )
        self.assert_health()

    def assert_health(self):
        assert self.health_snapshot(), self.evidence.data.get("lastHealth")
        health = self.evidence.data["lastHealth"]["health"]
        assert time.time() - health["updated"] < 15, ("Stale health", health)
        assert set(health["jobs"]) == {
            "configuration",
            "requests",
            "downloads",
            "routing",
        }, health
        assert set(health["services"]) == {
            "radarr",
            "sonarr",
            "prowlarr",
            "seerr",
            "qbittorrent",
            "vpn",
        }, health
        assert not health["attention"], ("Unexpected attention", health["attention"])
        assert self.arr("GET", "/indexer") and self.sonarr("GET", "/indexer"), (
            "Both Arr sources are required"
        )
        self.evidence.checkpoint()


def identities():
    tokens = {
        "Radarr": "radarr",
        "Sonarr": "sonarr",
        "Prowlarr": "prowlarr",
        "qbittorrent-nox": "qbittorrent",
        "node": "seerr",
    }
    processes = {}
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            values = dict(
                line.split(":", 1)
                for line in path.read_text().splitlines()
                if ":" in line
            )
            cmd = (path.parent / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            processes[int(path.parent.name)] = {
                "cmd": cmd,
                "parent": int(values["PPid"]),
                "uids": values["Uid"].split(),
                "caps": {k: int(values[k], 16) for k in ["CapEff", "CapPrm", "CapAmb"]},
            }
        except (OSError, UnicodeError):
            continue
    roots = {}
    for pid, p in processes.items():
        args = p["cmd"].split()
        if not args:
            continue
        name = Path(args[0]).name
        if name not in tokens or (name == "node" and "dist/index.js" not in args):
            continue
        roots[pid] = tokens[name]
    result = {kind: [] for kind in tokens.values()}
    for pid, p in processes.items():
        ancestor = pid
        seen = set()
        while ancestor not in roots and ancestor in processes and ancestor not in seen:
            seen.add(ancestor)
            ancestor = processes[ancestor]["parent"]
        if ancestor not in roots:
            continue
        assert set(p["uids"]) == {"1000"} and not any(p["caps"].values()), p
        result[roots[ancestor]].append(
            {"pid": pid, "role": "main" if pid in roots else "worker", **p}
        )
    assert all(result.values()), ("Missing engines", result)
    return result


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
    preferences = client.qbit("app/preferences")
    expected = {
        "bittorrent_protocol": 1,
        "dht": False,
        "pex": False,
        "lsd": False,
        "upnp": False,
    }
    assert all(preferences[k] == v for k, v in expected.items()), expected
    client.evidence.data["fixtureDownloaderPreferences"] = {
        k: preferences[k] for k in expected
    }
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
            "Fresh-install scenario has existing titles"
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
    client.healthy(client.created_at)
    client.ready_at = time.time()
    stamp("application-setup-complete")


class Background:
    """Check all earlier objects without re-reading every unchanged film per case."""

    def __init__(self, ledger):
        self.ledger = ledger
        self.files = FileChecks()

    def verify(self, client, full=False):
        if not self.ledger:
            return
        movies = {row["id"]: row for row in client.arr("GET", "/movie")}
        shows = (
            {row["id"]: row for row in client.sonarr("GET", "/series")}
            if any(r.get("kind") == "sonarr" for r in self.ledger)
            else {}
        )
        episodes = {}
        for series_id in {r["arrId"] for r in self.ledger if r.get("kind") == "sonarr"}:
            for episode in client.sonarr("GET", "/episode?seriesId=" + str(series_id)):
                episodes[
                    (series_id, episode["seasonNumber"], episode["episodeNumber"])
                ] = episode
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

            def require(ok, field, actual):
                if not ok:
                    raise BackgroundRegression(row["prefix"], field, actual)

            request = requests.get(row["requestId"])
            tv = row.get("kind") == "sonarr"
            external_key = "tvdbId" if tv else "tmdbId"
            external = row[external_key]
            if row.get("requestState") == "deleted":
                require(request is None, "request deletion", request)
            else:
                require(
                    request and request["media"][external_key] == external,
                    "request",
                    request,
                )
            movie = (shows if tv else movies).get(row["arrId"])
            require(
                movie
                and movie[external_key] == external
                and movie["title"] == row["prefix"]
                and (tv or movie["hasFile"]),
                "title",
                movie,
            )
            require(
                movie.get("monitored") == row["monitored"],
                "monitored",
                movie.get("monitored"),
            )
            if tv:
                require(
                    episodes.get(
                        (row["arrId"], row["seasonNumber"], row["episodeNumber"]), {}
                    ).get("hasFile"),
                    "episode link",
                    row["path"],
                )
                require(
                    {
                        str(s["seasonNumber"]): s["monitored"]
                        for s in movie["seasons"]
                        if s["seasonNumber"] > 0
                    }
                    == {str(k): v for k, v in row["seasons"].items()},
                    "seasons",
                    movie["seasons"],
                )
            if row.get("unwantedFiles"):
                library_files = {
                    str(p) for p in Path(row["path"]).parent.rglob("*") if p.is_file()
                }
                require(
                    library_files == {row["path"], row["subtitle"]},
                    "unexpected library files",
                    sorted(library_files),
                )
            for path, key in [
                (row["path"], "sha256"),
                (row["subtitle"], "subtitleSha256"),
            ]:
                try:
                    self.files.verify(path, row[key], full=full)
                except (OSError, AssertionError) as exc:
                    raise BackgroundRegression(row["prefix"], key, str(exc)) from exc
