"""Observe the shipped controller, ingress, firewall and real outage/reconnect path."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from fixture_network import PEER_IP
from support import Deadline


def fixture_control(action):
    subprocess.run(
        [sys.executable, "/tests/journey/fixture_network.py", action],
        check=True,
        timeout=3,
    )


def probe(host, port, kind):
    return json.loads(
        subprocess.check_output(
            [
                sys.executable,
                "/tests/journey/fixture_network.py",
                "probe",
                host,
                str(port),
                kind,
            ],
            text=True,
            timeout=3,
        )
    )


def assert_firewall(client):
    table = json.loads(
        subprocess.check_output(
            ["nft", "-j", "list", "table", "inet", "pompey"], text=True
        )
    )
    assert any(
        row.get("chain", {}).get("name") == "output"
        and row["chain"].get("hook") == "output"
        and row["chain"].get("policy") == "drop"
        for row in table["nftables"]
    ), table
    fixture_control("underlay-listening")
    # A non-connected address deliberately forced onto the unprotected route:
    # container-neighbor services are allowed, arbitrary underlay traffic is not.
    subprocess.run(["ip", "route", "replace", "198.19.0.3/32", "via", "198.18.0.2", "dev", "fixture-link"], check=True)
    try:
        for port, kind in [(18080, "tcp"), (53, "dns")]:
            result = probe("198.19.0.3", port, kind)
            client.evidence.data.setdefault("stackChecks", {}).setdefault(
                "underlayProbes", []
            ).append(result)
            assert not result["connected"], ("Engine escaped over the underlay", result)
    finally:
        subprocess.run(["ip", "route", "del", "198.19.0.3/32"], check=True)
    client.evidence.data.setdefault("stackChecks", {})["firewall"] = table
    client.evidence.checkpoint()


def assert_ui_access(client):
    result = json.loads(subprocess.check_output(
        [sys.executable, "/tests/journey/fixture_network.py", "ui-access"],
        text=True, timeout=8,
    ))
    client.evidence.data.setdefault("stackChecks", {}).setdefault("publishedInterfaces", []).append(result)
    # The released upgrade baseline predates always-on internal interfaces.
    # Candidate startup must serve the actual native pages without a debug flag.
    checks = client.evidence.data.setdefault("stackChecks", {})
    if os.environ.get("POMPEY_TEST_BASELINE") != "1" and not checks.get("internalInterfaces"):
        pages = {}
        for name, suffix in [("radarr", "activity/queue"), ("sonarr", "activity/queue"), ("qbittorrent", "")]:
            path = "/debug/" + name + "/" + suffix
            html = client.session.call("GET", "http://127.0.0.1:8099" + path)
            assert isinstance(html, str) and "<html" in html.lower() and "/debug/shim.js" in html, (name, html)
            assert 'src="dashboard.js"' not in html, ("Internal route fell back to Pompey", name)
            pages[name] = path
        checks["internalInterfaces"] = pages
    client.evidence.checkpoint()


def outage(client):
    previous = client.session.deadline
    try:
        client.session.deadline = Deadline(previous.remaining(90))
        _outage(client)
    finally:
        client.session.deadline = previous


def _outage(client):
    started = time.monotonic()
    client.evidence.stamp("vpn-outage-begin")
    assert_firewall(client)
    assert probe(PEER_IP, 53, "dns")["connected"], (
        "VPN DNS did not answer the engine identity"
    )
    fixture_control("down")
    try:
        # Losing packets leaves an old handshake timestamp valid for 180s.
        # Remove the real peer as a deterministic fault so the native monitor
        # detects lost handshake state without faking its marker or the clock.
        peer = subprocess.check_output(
            ["wg", "show", "wg0", "peers"], text=True
        ).strip()
        subprocess.run(["wg", "set", "wg0", "peer", peer, "remove"], check=True)
        client.wait(
            lambda: (
                not client.session.call("GET", "http://127.0.0.1:8099/health.json")[
                    "services"
                ]["vpn"]
            ),
            "VPN loss was not reported",
            30,
        )
        client.evidence.stamp("vpn-unavailable")
        status = client.session.call("GET", "http://127.0.0.1:8099/status.json")
        assert "VPN unavailable" in status["label"], status
        for url in [
            "http://127.0.0.1:5055/api/v1/settings/public",
            "http://127.0.0.1:9696/ping",
            "http://127.0.0.1:7878/ping",
            "http://127.0.0.1:8989/ping",
        ]:
            client.session.call("GET", url)
        client.qbit("app/version")
        assert_ui_access(client)
        assert not probe(PEER_IP, 53, "dns")["connected"], (
            "DNS remained reachable during outage"
        )
        assert_firewall(client)
    finally:
        fixture_control("up")
    restored = time.time()
    client.wait(
        lambda: client.session.call("GET", "http://127.0.0.1:8099/health.json")[
            "services"
        ]["vpn"],
        "Native WireGuard service did not reconnect",
        45,
    )
    client.healthy(restored)
    assert probe(PEER_IP, 53, "dns")["connected"], "DNS did not recover"
    # A real refresh must work over recovered DNS/TLS, rather than merely pinging apps.
    movie = client.arr("GET", "/movie")[0]
    command = client.arr(
        "POST", "/command", {"name": "RefreshMovie", "movieId": movie["id"]}
    )
    client.wait_command(command["id"])
    client.evidence.data["stackChecks"]["vpnOutage"] = {
        "success": True,
        "seconds": round(time.monotonic() - started, 3),
        "detection": "remote link loss plus native peer reset",
    }
    client.evidence.checkpoint()


def rotate(client):
    started = time.monotonic()
    old_public = subprocess.check_output(
        ["wg", "show", "wg0", "public-key"], text=True
    ).strip()
    private = subprocess.check_output(["wg", "genkey"], text=True).strip()
    public = subprocess.check_output(["wg", "pubkey"], input=private, text=True).strip()
    path = Path("/config/wireguard/wg0.conf")
    config = path.read_text()
    config = re.sub(r"(?m)^PrivateKey\s*=.*", "PrivateKey = " + private, config)
    config = re.sub(r"(?m)^Endpoint\s*=.*", "Endpoint = 198.18.0.2:51822", config)
    Path("/tmp/world/rotation.json").write_text(
        json.dumps({"oldPublicKey": old_public, "publicKey": public, "port": 51822})
    )
    assert_firewall(client)
    fixture_control("rotate")
    response = client.session.call(
        "POST", "http://127.0.0.1:8099/setup/vpn", {"config": config}
    )
    assert response.get("ok"), response

    def changed():
        return (
            subprocess.run(
                ["wg", "show", "wg0", "public-key"], capture_output=True, text=True
            ).stdout.strip()
            == public
        )

    client.wait(changed, "Saved WireGuard rotation was not applied", 45)
    client.healthy(time.time())
    assert probe(PEER_IP, 53, "dns")["connected"], "Rotated VPN DNS failed"
    assert_firewall(client)
    client.evidence.active["vpnRotation"] = {
        "seconds": round(time.monotonic() - started, 3),
        "publicKeyChanged": True,
        "endpointPort": 51822,
    }


def rotation_scenario(client, case, background, _ledger_file):
    """Rotation proves the saved configuration and engine network access.

    In-flight transfer recovery is independently asserted by vpn-download-loss.
    """
    client.evidence.begin(case)
    rotate(client)
    rows = client.arr("GET", "/movie/lookup?term=tmdb:" + str(case.movie_id))
    assert any(
        row["tmdbId"] == case.movie_id and row["title"] == case.title for row in rows
    ), rows
    from urllib.parse import quote

    results = client.search("GET", "/search?query=" + quote(case.title))
    assert any(row.get("id") == case.movie_id for row in results["results"]), results
    assert client.peer(case)["blocks"] == 0, (
        "Metadata lookup unexpectedly grabbed a torrent"
    )
    client.evidence.active.update(
        kind="network",
        priorTitlesVerified=len({r["prefix"] for r in background.ledger}),
    )
