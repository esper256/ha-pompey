"""Observe the shipped controller, ingress, firewall and real outage/reconnect path."""

import json
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
                "s6-setuidgid",
                "pompey",
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
    for port, kind in [(18080, "tcp"), (53, "dns")]:
        result = probe("198.18.0.2", port, kind)
        client.evidence.data.setdefault("stackChecks", {}).setdefault(
            "underlayProbes", []
        ).append(result)
        assert not result["connected"], ("Engine escaped over the underlay", result)
    client.evidence.data.setdefault("stackChecks", {})["firewall"] = table
    client.evidence.checkpoint()


def outage(client):
    started = time.monotonic()
    client.evidence.stamp("vpn-outage-begin")
    client.session.deadline = Deadline(90)
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
