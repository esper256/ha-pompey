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
