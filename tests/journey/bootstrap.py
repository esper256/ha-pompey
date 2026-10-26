"""Prepare only the isolated external world, then exec the image's actual /init."""

import json
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, "/tests/journey")
from external import PEER_IP, TITLE


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def prepare():
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
    Path("/tmp/world").mkdir(exist_ok=True)
    Path("/tmp/world/started").write_text(str(time.monotonic()))
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
    hosts = [
        "api.themoviedb.org",
        "api.radarr.video",
        "skyhook.sonarr.tv",
        "plex.tv",
        "image.tmdb.org",
        "api.github.com",
        "indexers.prowlarr.com",
        "radarr.servarr.com",
        "services.sonarr.tv",
        "prowlarr.servarr.com",
    ]
    with open("/etc/hosts", "a") as out:
        out.write("\n" + PEER_IP + " " + " ".join(hosts) + "\n")
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
    client = subprocess.check_output(["wg", "genkey"]).strip()
    server = subprocess.check_output(["wg", "genkey"]).strip()
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
Endpoint = 198.18.0.2:51821
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 1
"""
    Path("/config/wireguard/wg0.conf").write_text(config)
    Path("/config/wireguard/wg0.conf").chmod(0o600)
    source = Path("/tmp/world") / TITLE
    source.mkdir()
    fixture = Path("/tests/journey/fixtures")
    media = gzip.decompress((fixture / "movie.mkv.gz").read_bytes())
    metadata = json.loads((fixture / "movie.json").read_text())
    if (
        len(media) != metadata["bytes"]
        or hashlib.sha256(media).hexdigest() != metadata["sha256"]
    ):
        raise RuntimeError("Synthetic media fixture is corrupted")
    (source / (TITLE + ".mkv")).write_bytes(media)
    (source / (TITLE + ".en.srt")).write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nSynthetic acceptance subtitle\n"
    )
    worker = subprocess.Popen(["python3", "-u", "/tests/journey/world.py"])
    deadline = time.monotonic() + 10
    while not Path("/tmp/world/namespace-ready").exists():
        if worker.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Fixture namespace creation failed")
        time.sleep(0.05)
    run("ip", "link", "set", "fixture-peer", "netns", str(worker.pid))
    os.execv("/init", ["/init", "python3", "-u", "/tests/journey/journey.py"])


if __name__ == "__main__":
    prepare()
