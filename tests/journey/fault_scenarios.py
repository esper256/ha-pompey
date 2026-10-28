"""Release-only faults with explicit expected failures and observed recovery."""

import json
from pathlib import Path
import subprocess
import time
from stack_checks import assert_firewall, assert_ui_access, fixture_control, probe, PEER_IP


def cold_start(client):
    for url in ["http://127.0.0.1:5055/api/v1/settings/public", "http://127.0.0.1:9696/ping",
                "http://127.0.0.1:7878/ping", "http://127.0.0.1:8989/ping"]:
        client.wait(lambda url=url: client.session.call("GET", url), "Local application did not start without VPN")
    client.wait(lambda: client.qbit("app/version"), "Downloader did not start without VPN")
    assert not Path("/tmp/pompey/vpn-up").exists()
    assert_ui_access(client)
    assert_firewall(client)
    assert not probe(PEER_IP, 53, "dns")["connected"], "DNS escaped before first VPN connection"
    client.evidence.data.setdefault("stackChecks", {})["coldStartup"] = {"localAccess": True, "internetBlocked": True}
    fixture_control("up")


def cold_result(client, case, background, ledger_file):
    client.evidence.begin(case)
    assert client.evidence.data["stackChecks"]["coldStartup"] == {"localAccess": True, "internetBlocked": True}
    client.arr("GET", "/movie/lookup?term=tmdb:" + str(case.movie_id))
    client.evidence.active.update(coldStartupVerified=True, priorTitlesVerified=len({r["prefix"] for r in background.ledger}))


def storage_recovery(client, case, background, ledger_file):
    client.evidence.begin(case)
    # This scenario covers storage loss with an established library. Native
    # post-import removal is a separate operation; finish it before hiding the
    # mount rather than attributing interrupted deletion to library recovery.
    client.invariants.checkpoint("before-storage-fault", full=True)
    prior = client.session.call("GET", "http://127.0.0.1:8099/diagnostics/overview")
    # This mount is inside the test container's mount namespace. No host mount,
    # firewall or routes are changed. The original media reappears at unmount.
    with client.invariants.expect_fault("read-only replacement media mount", {"configuration", "downloads", "routing"}):
        subprocess.run(["mount", "-t", "tmpfs", "-o", "size=1m,ro", "pompey-storage-fault", "/media/dlna"], check=True, timeout=3)
        try:
            def paused():
                result = json.loads(Path("/tmp/pompey/storage.json").read_text())
                return result.get("phase") == "paused" and result
            held = client.wait(paused, "Storage loss did not stop affected services", 45)
            assert set(held["heldServices"]) == {"radarr", "sonarr", "qbittorrent"}, held
            assert not list(Path("/media/dlna").rglob("*")), "Fallback mount contains writes"
            for service in held["heldServices"]:
                value = subprocess.check_output(["s6-svstat", "-o", "up", "/run/service/" + service], text=True).strip()
                assert value == "false", (service, value)
            assert_ui_access(client)
            # A liveness failure must not turn a real library count into zero.
            # The detailed read refreshes the common cache with a two-second
            # maximum age; preserve and label the last successful observation.
            def unavailable():
                client.session.call("GET", "http://127.0.0.1:8099/diagnostics")
                current = client.session.call("GET", "http://127.0.0.1:8099/diagnostics/overview")
                return not current["summary"]["movies"]["available"] and current
            observed = client.wait(unavailable, "Stopped application was presented as current", 10)
            for field in ["movies", "shows"]:
                assert observed["summary"][field]["value"] == prior["summary"][field]["value"], observed
                assert observed["summary"][field]["updated"], observed
            client.evidence.active["staleOverviewVerified"] = True
        finally:
            restored = time.time()
            subprocess.run(["umount", "/media/dlna"], check=True, timeout=3)
        client.wait(lambda: json.loads(Path("/tmp/pompey/storage.json").read_text()).get("ok") is True,
                    "Storage guard did not release services after restoration", 30)
        client.healthy(restored)
    background.verify(client, full=True)
    client.arr("GET", "/movie/lookup?term=tmdb:" + str(case.movie_id))
    client.evidence.active.update(storageProtectionVerified=True, priorTitlesVerified=len({r["prefix"] for r in background.ledger}))


def media_discovery(client):
    """A saved library list must not masquerade as a live Plex health check."""
    from pompey_observations import read_source
    from support import HttpFailure
    started = time.monotonic()
    control = lambda path: client.session.call("POST", "http://10.77.0.2:18080/control/" + path, {})
    original = client.search("GET", "/settings/plex")
    def observed():
        return read_source("seerr", client.secrets)["mediaServer"]
    assert observed()["state"] == "no-libraries"
    control("fault/plex/on")
    try:
        try:
            client.search("POST", "/settings/plex/library/sync", {})
        except HttpFailure as exc:
            assert exc.status in {500, 502, 503}, exc
        else:
            raise AssertionError("Seerr did not surface the configured Plex outage")
        cached = observed()
        assert cached["state"] == "no-libraries" and "reachable" not in cached, cached
    finally:
        control("fault/plex/off")
    control("plex-library")
    libraries = client.search("POST", "/settings/plex/library/sync", {})
    assert len(libraries) == 1 and libraries[0]["enabled"] is False, libraries
    client.search("PUT", "/settings/plex/library/1", {"enabled": True})
    try:
        recovered = observed()
        assert recovered["state"] == "saved-selections" and recovered["selectedLibraries"] == 1, recovered
        assert "reachable" not in recovered, recovered
    finally:
        client.search("POST", "/settings/plex", original)
    client.evidence.active["mediaDiscovery"] = {"failureAndRecovery": True, "cachedEvidenceHonest": True,
                                                "seconds": round(time.monotonic() - started, 3)}


def indexer_recovery(client, case, movie, request, background):
    """Exercise real Prowlarr backoff, then native test/RSS recovery of a waiting request."""
    from pompey_observations import read_source, indexer_availability
    from support import HttpFailure
    started = time.monotonic()
    control = lambda value: client.session.call("POST", "http://10.77.0.2:18080/control/fault/indexer/" + value, {})
    indexer = next(row for row in client.prow("GET", "/indexer") if row["name"] == "Synthetic source")
    control("on")
    try:
        try:
            client.prow("GET", "/search?query=" + case.title + "&indexerIds=" + str(indexer["id"]) + "&type=search")
        except HttpFailure as exc:
            assert exc.status in {400, 500, 502, 503}, exc
        def degraded():
            counts, issues = indexer_availability(read_source("prowlarr", client.secrets))
            return issues and counts["healthy"] == 0 and issues
        issues = client.wait(degraded, "Prowlarr did not expose the indexer outage", 15)
        assert issues[0]["name"] == indexer["name"] and issues[0]["retryAfter"], issues
        unchanged = client.arr("GET", "/movie/" + str(movie["id"]))
        assert unchanged["monitored"] and not unchanged["hasFile"], unchanged
        assert client.search("GET", "/request/" + str(request["id"]))["media"]["tmdbId"] == case.movie_id
        background.verify(client, full=True)
    finally:
        control("off")
    # Supported native validation clears backoff; the caller publishes the
    # release and runs RssSync. This proves recovery, not the production cadence.
    client.prow("POST", "/indexer/test", indexer)
    client.wait(lambda: not indexer_availability(read_source("prowlarr", client.secrets))[1],
                "Prowlarr did not recover after native validation", 15)
    client.evidence.active["indexerOutage"] = {"requestId": request["id"], "arrId": movie["id"],
        "nativeAcceleration": ["Prowlarr indexer test", "Radarr RssSync"], "seconds": round(time.monotonic() - started, 3)}
