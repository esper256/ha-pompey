"""External services in a separate namespace, reachable only over local WireGuard."""

import os
from pathlib import Path
import subprocess
import time
from external import World, PEER_IP


def run(*args):
    subprocess.run(args, check=True)


def main():
    os.unshare(os.CLONE_NEWNET)
    # Pin the namespace before dropping UID: /proc/PID/ns becomes ptrace-gated
    # after setuid. A private nsfs handle avoids adding SYS_PTRACE to the runner.
    Path("/tmp/world/netns").touch()
    run("mount", "--bind", "/proc/self/ns/net", "/tmp/world/netns")
    Path("/tmp/world/namespace-ready").touch()
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if (
            subprocess.run(
                ["ip", "link", "show", "fixture-peer"], capture_output=True
            ).returncode
            == 0
        ):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Local fixture underlay missing")
    run("ip", "link", "set", "lo", "up")
    run("ip", "addr", "add", "198.18.0.2/30", "dev", "fixture-peer")
    run("ip", "link", "set", "fixture-peer", "up")
    run("ip", "link", "add", "wg-fixture", "type", "wireguard")
    run("ip", "addr", "add", PEER_IP + "/32", "dev", "wg-fixture")
    client_pub = Path("/tmp/world/client.pub").read_text().strip()
    run(
        "wg",
        "set",
        "wg-fixture",
        "listen-port",
        Path("/tmp/world/server-port").read_text().strip(),
        "private-key",
        "/tmp/world/server.key",
        "peer",
        client_pub,
        "allowed-ips",
        "10.77.0.1/32",
        "endpoint",
        "198.18.0.1:51820",
    )
    run("ip", "link", "set", "wg-fixture", "up")
    run("ip", "route", "add", "10.77.0.1/32", "dev", "wg-fixture")
    world = World(Path("/tmp/world"))
    world.start(
        "/usr/local/share/ca-certificates/pompey-test.crt", "/tmp/world/key.pem"
    )
    # Sockets are bound; the synthetic peer itself has no root capabilities.
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
