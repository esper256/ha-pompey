"""Prepare only the isolated external world, then exec the image's actual /init."""

import json
import shutil
import configparser
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, "/tests/journey")
from fixture_network import PEER_IP, HOSTS
from catalogue import prepare_media


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def prepare():
    started = time.monotonic()
    links = json.loads(subprocess.check_output(["ip", "-j", "link"]))
    if {r["ifname"] for r in links} != {"lo"}:
        raise RuntimeError("Acceptance requires --network none (loopback only)")
    for family in ["-4", "-6"]:
        routes = json.loads(
            subprocess.check_output(
                ["ip", "-j", family, "route", "show", "table", "all"]
            )
        )
        if any(
            r.get("gateway") or r.get("dev") != "lo" or r.get("dst") == "default"
            for r in routes
        ):
            raise RuntimeError("Acceptance refuses external routes")
    Path("/etc/resolv.conf").write_text("nameserver 127.0.0.1\n")
    # Same-container restart gets a fresh external namespace and identical
    # synthetic payloads, while preserving the applications' real state.
    if Path("/tmp/world").exists():
        shutil.rmtree("/tmp/world")
    Path("/tmp/world").mkdir(exist_ok=True)
    Path("/tmp/world/started").write_text(str(started))
    timings = {}

    def stamp(name):
        timings[name] = round(time.monotonic() - started, 3)
        Path("/tmp/world/bootstrap-timings.json").write_text(json.dumps(timings))

    stamp("isolation-verified")
    Path("/artifacts").mkdir(exist_ok=True)
    Path("/artifacts/isolation.json").write_text(
        json.dumps(
            {"initialLinks": links, "namespace": os.readlink("/proc/self/ns/net")}
        )
    )
    for folder in ["/data", "/config/wireguard", "/tmp/world"]:
        Path(folder).mkdir(parents=True, exist_ok=True)
    options = json.loads(Path("/tests/fixtures/options.defaults.json").read_text())
    options.update(
        media_folder="/media/dlna", lan_networks="", after_download="stop_sharing"
    )
    Path("/data/options.json").write_text(json.dumps(options))
    os.environ.update(
        INGRESS_PORT="8099",
        NODE_EXTRA_CA_CERTS="/usr/local/share/ca-certificates/pompey-test.crt",
    )
    hosts = HOSTS
    run(
        "/tests/journey/tool",
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-keyout",
        "/tmp/world/key.pem",
        "-out",
        os.environ["NODE_EXTRA_CA_CERTS"],
        "-subj",
        "/CN=Pompey acceptance",
        "-addext",
        "subjectAltName=" + ",".join("DNS:" + h for h in hosts),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run("update-ca-certificates", stdout=subprocess.DEVNULL)
    stamp("certificate-ready")
    network_file = Path("/data/pompey-fixture/network.json")
    source = Path("/config/wireguard/wg0.conf")
    prior = json.loads(network_file.read_text()) if network_file.exists() else None
    if prior and source.exists():
        parsed = configparser.RawConfigParser()
        parsed.read(source)
        client = parsed["Interface"]["PrivateKey"].encode()
        server = prior["serverPrivateKey"].encode()
        port = int(parsed["Peer"]["Endpoint"].rsplit(":", 1)[1])
    else:
        client = subprocess.check_output(["wg", "genkey"]).strip()
        server = subprocess.check_output(["wg", "genkey"]).strip()
        port = 51821
    client_pub = (
        subprocess.check_output(["wg", "pubkey"], input=client).decode().strip()
    )
    server_pub = (
        subprocess.check_output(["wg", "pubkey"], input=server).decode().strip()
    )
    Path("/tmp/world/server.key").write_bytes(server)
    Path("/tmp/world/server.key").chmod(0o600)
    Path("/tmp/world/client.pub").write_text(client_pub)
    run(
        "ip",
        "link",
        "add",
        "fixture-link",
        "type",
        "veth",
        "peer",
        "name",
        "fixture-peer",
    )
    run("ip", "addr", "add", "198.18.0.1/30", "dev", "fixture-link")
    run("ip", "link", "set", "fixture-link", "up")
    config = f"""[Interface]
PrivateKey = {client.decode()}
Address = 10.77.0.1/32
ListenPort = 51820
DNS = {PEER_IP}

[Peer]
PublicKey = {server_pub}
Endpoint = 198.18.0.2:{port}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 1
"""
    if not prior:
        Path("/config/wireguard/wg0.conf").write_text(config)
    network_file.parent.mkdir(exist_ok=True)
    network_file.write_text(json.dumps({"serverPrivateKey": server.decode()}))
    network_file.chmod(0o600)
    Path("/tmp/world/server-port").write_text(str(port))
    Path("/config/wireguard/wg0.conf").chmod(0o600)
    # Disable public discovery before the real downloader starts; production
    # config generation preserves these unrelated native preferences.
    path = Path("/config/qBittorrent/config/qBittorrent.conf")
    config = configparser.RawConfigParser(delimiters=("=",), strict=False)
    config.optionxform = str
    if path.exists():
        config.read(path)
    if not config.has_section("BitTorrent"):
        config.add_section("BitTorrent")
    for key in ["Session\\DHTEnabled", "Session\\PeXEnabled", "Session\\LSDEnabled"]:
        config.set("BitTorrent", key, "false")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as out:
        config.write(out, space_around_delimiters=False)
    controls = Path("/data/pompey-fixture")
    controls.mkdir(exist_ok=True)
    os.chown(controls, 1000, 1000)
    events = Path("/tmp/world/events")
    events.mkdir()
    os.chown(events, 1000, 1000)
    prepare_media(Path("/tmp/world"))
    worker = subprocess.Popen(["python3", "-u", "/tests/journey/world.py"])
    deadline = time.monotonic() + 10
    while not Path("/tmp/world/namespace-ready").exists():
        if worker.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Fixture namespace creation failed")
        time.sleep(0.05)
    run("ip", "link", "set", "fixture-peer", "netns", str(worker.pid))
    stamp("fixtures-prepared")
    os.execv(
        "/init",
        [
            "/init",
            "/command/with-contenv",
            "python3",
            "-u",
            "/tests/journey/journey.py",
        ],
    )


if __name__ == "__main__":
    prepare()
