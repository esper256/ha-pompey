"""Real household journeys, sharing a noisy household or cloning configured state."""

import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

sys.path[:0] = ["/tests/journey", "/usr/local/bin"]
from catalogue import cases
from external import PEER_IP

started = float(Path("/tmp/world/started").read_text())
selected = json.loads(os.environ.get("POMPEY_TEST_SELECTED_CASES", "[0,1,2]"))
DEADLINE = started + 180 + 60 * len(selected)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
report = {
    "success": False,
    "timings": json.loads(Path("/tmp/world/bootstrap-timings.json").read_text()),
    "cases": [],
}
active = None
ledger_file = Path("/data/pompey-test/evidence.json")


def stamp(name):
    target = active["timings"] if active else report["timings"]
    origin = active["started"] if active else started
    target[name] = round(time.monotonic() - origin, 3)
    print(
        "TIMING",
        active["prefix"] if active else "startup",
        name,
        target[name],
        flush=True,
    )


def call(method, url, body=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Api-Key"] = key
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    try:
        with opener.open(
            request, timeout=max(0.1, min(30, DEADLINE - time.monotonic()))
        ) as response:
            raw = response.read()
            if not raw:
                return None
            try:
                return json.loads(raw)
            except ValueError:
                return raw.decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"{method} {url}: {exc.code}: {exc.read().decode()[:1500]}"
        ) from None


def wait(predicate, message, seconds=60):
    deadline = min(DEADLINE, time.monotonic() + seconds)
    last = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, RuntimeError, ValueError) as exc:
            last = str(exc)
        time.sleep(0.2)
    raise AssertionError(message + (": " + last if last else ""))


def peer(case):
    return call("GET", f"http://{PEER_IP}:18080/control/status/{case.movie_id}")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tunnel_bytes():
    rows = subprocess.check_output(
        ["wg", "show", "wg0", "transfer"], text=True
    ).splitlines()
    return sum(int(row.split()[1]) for row in rows)


class Clients:
    def __init__(self, secrets):
        self.secrets = secrets
        self.skey = ""

    def arr(self, method, path, body=None):
        return call(
            method,
            "http://127.0.0.1:7878/api/v3" + path,
            body,
            self.secrets["radarr_api_key"],
        )

    def prow(self, method, path, body=None):
        return call(
            method,
            "http://127.0.0.1:9696/api/v1" + path,
            body,
            self.secrets["prowlarr_api_key"],
        )

    def search(self, method, path, body=None):
        return call(method, "http://127.0.0.1:5055/api/v1" + path, body, self.skey)

    def qbit(self, path, body=None):
        data = None if body is None else urllib.parse.urlencode(body).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:8080/api/v2/" + path, data=data
        )
        with urllib.request.urlopen(
            request, timeout=max(0.1, min(30, DEADLINE - time.monotonic()))
        ) as response:
            raw = response.read()
            try:
                return json.loads(raw)
            except ValueError:
                return raw.decode()

    def refresh_downloads(self):
        command = self.arr("POST", "/command", {"name": "RefreshMonitoredDownloads"})
        wait(
            lambda: (
                self.arr("GET", "/command/" + str(command["id"]))["status"]
                == "completed"
            ),
            "Download maintenance did not complete",
        )


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


def verify_noise(client, ledger):
    for row in ledger:
        request = client.search("GET", "/request/" + str(row["requestId"]))
        assert request["media"]["tmdbId"] == row["tmdbId"], (
            "Earlier request changed",
            row,
            request,
        )
        assert digest(row["path"]) == row["sha256"], ("Earlier media changed", row)
        assert digest(row["subtitle"]) == row["subtitleSha256"], (
            "Earlier subtitle changed",
            row,
        )
        movie = client.arr("GET", "/movie/" + str(row["arrId"]))
        assert (
            movie["tmdbId"] == row["tmdbId"]
            and movie["title"] == row["prefix"]
            and movie["hasFile"]
        ), ("Earlier title changed", row, movie)


def configure(client, restored):
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


def scenario(client, case, ledger):
    global active
    active = {
        "prefix": case.prefix,
        "tmdbId": case.movie_id,
        "certification": case.certification,
        "library": case.library,
        "started": time.monotonic(),
        "timings": {},
    }
    report["cases"].append(active)
    print(
        "CASE",
        case.prefix,
        "tmdbId",
        case.movie_id,
        "certification",
        repr(case.certification),
        "priorTitles",
        len(ledger),
        flush=True,
    )
    verify_noise(client, ledger)
    before = tunnel_bytes()
    status = peer(case)
    assert status["blocks"] == 0, (
        "Unrequested case already downloaded",
        case.prefix,
        status,
    )
    infohash = status["infoHash"]

    def torrent():
        rows = client.qbit("torrents/info?hashes=" + infohash)
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def movie():
        return next(
            (m for m in client.arr("GET", "/movie") if m["tmdbId"] == case.movie_id),
            None,
        )

    results = client.search("GET", "/search?query=" + urllib.parse.quote(case.title))
    assert any(row.get("id") == case.movie_id for row in results["results"]), results
    stamp("catalogue-searched")
    request = client.search(
        "POST", "/request", {"mediaType": "movie", "mediaId": case.movie_id}
    )
    assert request.get("id"), request
    stamp("requested")
    wait(
        lambda: peer(case)["blocks"] > 0,
        "Request did not produce a real torrent block",
        90,
    )
    stamp("first-peer-block")
    current = torrent()
    assert current and current["progress"] < 1, (case.prefix, current)
    client.refresh_downloads()
    item = movie()
    assert item and not item["hasFile"], item
    stamp("incomplete-preserved")
    call("POST", f"http://{PEER_IP}:18080/control/release/{case.movie_id}", {})
    wait(
        lambda: (t := torrent()) and t["progress"] == 1,
        "Downloader did not verify the payload",
    )
    stamp("download-verified")
    client.refresh_downloads()
    item = wait(
        lambda: (m := movie()) and m["hasFile"] and m, "Arr did not import the movie"
    )
    stamp("imported")
    base = Path("/media/dlna/Movies")

    def library_file():
        matches = [
            p
            for p in (base / case.library).rglob("*.mkv")
            if p.name.startswith(case.prefix)
        ]
        assert len(matches) <= 1, (case.prefix, matches)
        return matches[0] if matches else None

    imported = wait(
        library_file,
        "Controller did not route " + case.prefix + " to " + case.library,
        90,
    )
    stamp("library-routed")
    source = Path("/tmp/world") / case.release / (case.release + ".mkv")
    assert digest(imported) == digest(source), "Imported bytes belong to another case"
    wrong = base / (
        "Not Kid Friendly" if case.library == "Kid Friendly" else "Kid Friendly"
    )
    assert not [p for p in wrong.rglob("*.mkv") if p.name.startswith(case.prefix)], (
        "Wrong library contains this case"
    )
    subtitles = list(imported.parent.glob("*.srt"))
    assert len(subtitles) == 1 and digest(subtitles[0]) == digest(
        source.with_suffix(".en.srt")
    ), "Wrong or missing subtitle"
    evidence = peer(case)
    assert evidence["sent"] >= evidence["payloadBytes"], (
        "Payload not transferred from real peer"
    )
    assert tunnel_bytes() - before >= evidence["payloadBytes"], (
        "Payload bypassed tunnel"
    )
    diagnosis = call("GET", "http://127.0.0.1:8097/diagnostics")
    assert any(
        r["id"] == request["id"] and r["arrLinked"] for r in diagnosis["requests"]
    ), diagnosis
    assert any(
        t["id"] == item["id"] and t["title"] == case.title and t["hasFile"]
        for t in diagnosis["titles"]
    ), diagnosis
    explanation = call(
        "POST",
        "http://127.0.0.1:8097/diagnostics/search",
        {"kind": "radarr", "id": item["id"]},
    )
    assert explanation["status"] == "Search complete" and any(
        r["title"] == case.release and "rejections" in r
        for r in explanation["releases"]
    ), explanation
    assert not peer(case)["unknown"], peer(case)["unknown"]
    verify_noise(client, ledger)
    ledger.append(
        {
            "prefix": case.prefix,
            "tmdbId": case.movie_id,
            "arrId": item["id"],
            "requestId": request["id"],
            "path": str(imported),
            "sha256": digest(imported),
            "subtitle": str(subtitles[0]),
            "subtitleSha256": digest(subtitles[0]),
        }
    )
    ledger_file.parent.mkdir(exist_ok=True)
    ledger_file.write_text(json.dumps(ledger, indent=2))
    active.update(
        payloadBytes=evidence["payloadBytes"],
        transferredBytes=evidence["sent"],
        externalTimings=evidence["timings"],
        libraryPath=str(imported),
        sha256=digest(imported),
        priorTitlesVerified=len(ledger) - 1,
    )
    stamp("correct-library-verified")
    (Path("/artifacts") / (case.prefix + ".json")).write_text(
        json.dumps(active, indent=2)
    )
    active = None


def main():
    restored = os.environ.get("POMPEY_TEST_CONFIGURED") == "1"
    wait(
        lambda: Path("/data/pompey/secrets.json").is_file(),
        "Startup did not configure engines",
    )
    client = Clients(json.loads(Path("/data/pompey/secrets.json").read_text()))
    configure(client, restored)
    ledger = json.loads(ledger_file.read_text()) if ledger_file.exists() else []
    assert bool(ledger) == restored, (
        "Expected configured-state clone" if restored else "Expected empty fresh state"
    )
    report.update(
        configuredClone=restored,
        identities=identities(),
        controllerIntervals=json.loads(os.environ.get("POMPEY_JOB_INTERVALS", "{}")),
    )
    all_cases = cases()
    for index in selected:
        scenario(client, all_cases[index], ledger)
    report["timings"]["batch-complete"] = round(time.monotonic() - started, 3)
    report["success"] = True
    print(
        "PASS: fresh install" if not restored else "PASS: configured clone",
        "and",
        len(selected),
        "real household journeys",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        subprocess.run(["wg", "show"], check=False)
        subprocess.run(["nft", "list", "table", "inet", "pompey"], check=False)
        for file in [
            Path("/tmp/pompey/health.json"),
            *Path("/config").rglob("*.debug.txt"),
        ]:
            try:
                print(str(file), file.read_text()[-12000:])
            except OSError:
                pass
        raise
    finally:
        Path("/artifacts/result.json").write_text(json.dumps(report, indent=2))
        try:
            evidence = peer(cases()[selected[-1]])
        except Exception as exc:
            evidence = {
                "requests": [],
                "unknown": ["Fixture evidence unavailable: " + str(exc)],
            }
        Path("/artifacts/external-requests.json").write_text(
            json.dumps(evidence["requests"], indent=2)
        )
        Path("/artifacts/unexpected-requests.json").write_text(
            json.dumps(evidence["unknown"], indent=2)
        )
