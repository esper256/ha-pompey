"""One household journey through the actual shipped applications and controller."""

import hashlib
import json
from http.cookiejar import CookieJar
import subprocess
from pathlib import Path
import sys
import time
import traceback
import urllib.error
import urllib.request

sys.path[:0] = ["/tests/journey", "/usr/local/bin"]
from external import TITLE, MOVIE_ID, PEER_IP

started = float(Path("/tmp/world/started").read_text())
DEADLINE = started + 180
timings = {}
world = None
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def stamp(name):
    timings[name] = round(time.monotonic() - started, 3)
    print("TIMING", name, timings[name], flush=True)


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


def main():
    global world

    def peer():
        return call("GET", f"http://{PEER_IP}:18080/control/status")

    world = peer
    source = Path("/tmp/world") / TITLE / (TITLE + ".mkv")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    wait(
        lambda: Path("/data/pompey/secrets.json").is_file(),
        "Production startup did not configure engines",
    )
    secrets = json.loads(Path("/data/pompey/secrets.json").read_text())
    radarr = "http://127.0.0.1:7878/api/v3"
    sonarr = "http://127.0.0.1:8989/api/v3"
    prowlarr = "http://127.0.0.1:9696/api/v1"
    seerr = "http://127.0.0.1:5055/api/v1"

    def arr(method, path, body=None):
        return call(method, radarr + path, body, secrets["radarr_api_key"])

    def prow(method, path, body=None):
        return call(method, prowlarr + path, body, secrets["prowlarr_api_key"])

    def qbit(path, body=None):
        import urllib.parse

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

    for name, url in [
        ("Radarr", radarr + "/system/status"),
        ("Sonarr", sonarr + "/system/status"),
        ("Prowlarr", prowlarr + "/system/status"),
        ("Seerr", seerr + "/settings/public"),
    ]:
        key = secrets.get(name.lower() + "_api_key")
        wait(lambda: call("GET", url, key=key), name + " did not start")
    # Discovery cannot leave this namespace; also turn it off before any torrent exists.
    qbit(
        "app/setPreferences",
        {"json": json.dumps({"dht": False, "pex": False, "lsd": False, "upnp": False})},
    )
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
    # Adding an indexer is a household setup action through Prowlarr's real API.
    schemas = prow("GET", "/indexer/schema")
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
    prow("POST", "/indexer", indexer)
    # Complete first-admin creation through the real public Plex sign-in API.
    call("POST", seerr + "/auth/plex", {"authToken": "synthetic-token"})
    settings = call("GET", seerr + "/settings/main")
    skey = settings["apiKey"]

    def search(method, path, body=None):
        return call(method, seerr + path, body, skey)

    search("POST", "/settings/plex", {"ip": PEER_IP, "port": 80, "useSsl": False})
    # Production controller owns profiles and Seerr/Arr wiring. Never call its helpers here.
    wait(
        lambda: search("GET", "/settings/radarr") and search("GET", "/settings/sonarr"),
        "Controller did not connect Seerr to both Arr apps",
        120,
    )
    search("POST", "/settings/initialize", {})
    wait(lambda: arr("GET", "/indexer"), "Prowlarr did not synchronize into Radarr")
    stamp("household-setup-complete")
    results = search("GET", "/search?query=Pompey%20Foundation")
    assert any(row.get("id") == MOVIE_ID for row in results["results"]), results
    request = search("POST", "/request", {"mediaType": "movie", "mediaId": MOVIE_ID})
    assert request.get("id"), request
    stamp("requested")
    wait(
        lambda: peer()["blocks"] > 0,
        "Request did not produce an actual BitTorrent block request",
        90,
    )
    torrents = qbit("torrents/info")
    assert len(torrents) == 1 and torrents[0]["progress"] < 1, torrents
    # Force the real Arr check while a piece is deliberately withheld.
    command = arr("POST", "/command", {"name": "RefreshMonitoredDownloads"})
    wait(
        lambda: arr("GET", "/command/" + str(command["id"]))["status"] == "completed",
        "Incomplete-download check did not finish",
    )
    movies = arr("GET", "/movie")
    movie = next(m for m in movies if m["tmdbId"] == MOVIE_ID)
    assert not movie["hasFile"], movie
    stamp("incomplete-preserved")
    call("POST", f"http://{PEER_IP}:18080/control/release", {})
    wait(
        lambda: qbit("torrents/info") and qbit("torrents/info")[0]["progress"] == 1,
        "Real downloader did not verify the payload",
    )
    stamp("download-verified")
    # Ask the actual scheduler to run this supported maintenance command now.
    command = arr("POST", "/command", {"name": "RefreshMonitoredDownloads"})
    wait(
        lambda: arr("GET", "/command/" + str(command["id"]))["status"] == "completed",
        "Import check did not finish",
    )
    movie = wait(
        lambda: next(
            (
                m
                for m in arr("GET", "/movie")
                if m["tmdbId"] == MOVIE_ID and m["hasFile"]
            ),
            None,
        ),
        "Arr did not import the movie",
    )
    stamp("imported")

    # By Rating is a production policy job; wait for its actual filesystem outcome.
    def library_file():
        candidates = list(Path("/media/dlna/Movies/Kid Friendly").rglob("*.mkv"))
        return candidates[0] if candidates else None

    imported = wait(
        library_file, "Controller did not route PG media into the kid library", 90
    )
    assert hashlib.sha256(imported.read_bytes()).hexdigest() == expected, (
        "Imported bytes differ"
    )
    assert list(imported.parent.glob("*.srt")), "Subtitle missing"
    # Verify engines actually run under the shared unprivileged identity.
    identities = {}
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            body = path.read_text()
            cmd = (path.parent / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeError):
            continue
        if any(
            token in cmd
            for token in [
                "/Radarr/Radarr",
                "/Sonarr/Sonarr",
                "/Prowlarr/Prowlarr",
                "--confirm-legal-notice",
                "node dist/index.js",
            ]
        ):
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
            identities[cmd] = uid
    assert len(identities) == 5, identities
    evidence = peer()
    assert evidence["sent"] >= evidence["payloadBytes"], (
        "Payload was not transferred from the peer"
    )
    tunnel = subprocess.check_output(
        ["wg", "show", "wg0", "transfer"], text=True
    ).splitlines()
    assert sum(int(line.split()[1]) for line in tunnel) >= evidence["payloadBytes"], (
        "Payload bypassed the tunnel"
    )
    # Household diagnostics must explain the actual request and imported title.
    diagnosis = call("GET", "http://127.0.0.1:8097/diagnostics")
    assert any(
        r["id"] == request["id"] and r["arrLinked"] for r in diagnosis["requests"]
    ), diagnosis
    assert any(
        t["title"] == "Pompey Foundation" and t["hasFile"] for t in diagnosis["titles"]
    ), diagnosis
    explanation = call(
        "POST",
        "http://127.0.0.1:8097/diagnostics/search",
        {"kind": "radarr", "id": movie["id"]},
    )
    assert explanation["status"] == "Search complete", explanation
    assert any(
        r["title"] == TITLE and "rejections" in r for r in explanation["releases"]
    ), explanation
    evidence = peer()
    assert not evidence["unknown"], evidence["unknown"]
    stamp("correct-library-verified")
    Path("/artifacts/result.json").write_text(
        json.dumps(
            {
                "success": True,
                "timings": timings,
                "payloadBytes": evidence["payloadBytes"],
                "transferredBytes": evidence["sent"],
                "sha256": expected,
                "libraryPath": str(imported),
                "identities": identities,
            },
            indent=2,
        )
    )
    print(
        "PASS: Seerr request -> Prowlarr source -> real torrent transfer -> Arr import -> correct library",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        subprocess.run(["wg", "show"], check=False)
        subprocess.run(["nft", "list", "table", "inet", "pompey"], check=False)
        Path("/artifacts/result.json").write_text(
            json.dumps({"success": False, "timings": timings}, indent=2)
        )
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
        if world:
            try:
                evidence = world()
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
