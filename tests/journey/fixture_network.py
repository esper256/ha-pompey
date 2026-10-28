"""DNS and namespace controls for the isolated external world; no public forwarding."""

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import http.client

PEER_IP = "10.77.0.2"
HOSTS = (
    "api.themoviedb.org",
    "api.radarr.video",
    "skyhook.sonarr.tv",
    "thexem.info",
    "plex.tv",
    "image.tmdb.org",
    "api.github.com",
    "indexers.prowlarr.com",
    "radarr.servarr.com",
    "services.sonarr.tv",
    "prowlarr.servarr.com",
    "vpn-probe.fixture",
)


# Explicit negative replay: these telemetry destinations were already unavailable.
UNAVAILABLE_HOSTS = {"sentry.servarr.com", "sentry.sonarr.tv", "download.db-ip.com"}


class DnsServer:
    def __init__(self, record, unknown):
        self.record = record
        self.unknown = unknown

    def answer(self, data, source, transport):
        try:
            if len(data) < 12 or struct.unpack("!H", data[4:6])[0] != 1:
                raise ValueError("DNS requires one question")
            pos = 12
            labels = []
            while data[pos]:
                length = data[pos]
                if length > 63 or pos + length + 1 >= len(data):
                    raise ValueError("Invalid DNS question name")
                labels.append(data[pos + 1 : pos + 1 + length].decode("ascii"))
                pos += length + 1
            pos += 1
            kind, cls = struct.unpack("!HH", data[pos : pos + 4])
            end = pos + 4
            name = ".".join(labels).lower()
            known = name in HOSTS and cls == 1
            rcode = 0 if known else 3
            self.record(
                {
                    "kind": "dns",
                    "name": name,
                    "type": kind,
                    "transport": transport,
                    "source": source,
                    "rcode": rcode,
                }
            )
            if not known and name not in UNAVAILABLE_HOSTS:
                self.unknown("DNS " + name)
            answer = b""
            if known and kind == 1:
                answer = (
                    b"\xc0\x0c"
                    + struct.pack("!HHIH", 1, 1, 0, 4)
                    + socket.inet_aton(PEER_IP)
                )
            # AAAA and unsupported record types deliberately have no data.
            return (
                data[:2]
                + struct.pack("!HHHHH", 0x8180 | rcode, 1, bool(answer), 0, 0)
                + data[12:end]
                + answer
            )
        except (ValueError, IndexError, struct.error, UnicodeError) as exc:
            self.unknown("Malformed DNS question: " + str(exc))
            return data[:2] + struct.pack("!HHHHH", 0x8181, 0, 0, 0, 0)

    def start(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("0.0.0.0", 53))
        tcp = socket.socket()
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind(("0.0.0.0", 53))
        tcp.listen()

        def datagrams():
            while True:
                data, source = udp.recvfrom(65535)
                udp.sendto(self.answer(data, source[0], "udp"), source)

        def connection(sock, source):
            with sock:
                sock.settimeout(2)
                size = sock.recv(2)
                if len(size) != 2:
                    return
                length = struct.unpack("!H", size)[0]
                data = b""
                while len(data) < length:
                    chunk = sock.recv(length - len(data))
                    if not chunk:
                        return
                    data += chunk
                reply = self.answer(data, source[0], "tcp")
                sock.sendall(struct.pack("!H", len(reply)) + reply)

        def connections():
            while True:
                sock, source = tcp.accept()
                threading.Thread(
                    target=connection, args=(sock, source), daemon=True
                ).start()

        threading.Thread(target=datagrams, daemon=True).start()
        threading.Thread(target=connections, daemon=True).start()


def control(action):
    with open("/tmp/world/netns") as namespace:
        os.setns(namespace.fileno(), os.CLONE_NEWNET)
    if action in {"up", "down"}:
        subprocess.run(["ip", "link", "set", "wg-fixture", action], check=True)
        if action == "up":
            subprocess.run(
                ["ip", "route", "replace", "10.77.0.1/32", "dev", "wg-fixture"],
                check=True,
            )
    elif action == "rotate":
        data = json.loads(open("/tmp/world/rotation.json").read())
        subprocess.run(
            ["wg", "set", "wg-fixture", "peer", data["oldPublicKey"], "remove"],
            check=True,
        )
        subprocess.run(
            [
                "wg",
                "set",
                "wg-fixture",
                "listen-port",
                str(data["port"]),
                "peer",
                data["publicKey"],
                "allowed-ips",
                "10.77.0.1/32",
                "endpoint",
                "198.18.0.1:51820",
            ],
            check=True,
        )
    elif action == "underlay-listening":
        with socket.create_connection(("198.19.0.3", 18080), timeout=1):
            pass
    elif action == "ui-access":
        observed = []
        # The released baseline disables these routes with an explicit 404.
        # The candidate always enables them, retaining the Ingress access deny.
        internal_denied = 404 if os.environ.get("POMPEY_TEST_BASELINE") == "1" else 403
        for port, path, expected in [
            (5055, "/api/v1/settings/public", 200),
            (9696, "/ping", 200),
            (8099, "/", 403),
            (8099, "/debug/radarr/activity/queue", internal_denied),
            (8099, "/debug/sonarr/activity/queue", internal_denied),
            (8099, "/debug/qbittorrent/", internal_denied),
        ]:
            connection = http.client.HTTPConnection(
                "198.18.0.1", port, timeout=2,
                source_address=("198.19.0.2", 0),
            )
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                response.read()
                assert response.status == expected, (port, path, response.status, expected)
                observed.append({"port": port, "path": path, "status": response.status})
            finally:
                connection.close()
        print(json.dumps(observed), flush=True)
    else:
        raise ValueError("Unknown fixture control")


def probe(host, port, kind):
    if os.geteuid() != 0:
        raise RuntimeError("Egress probe must use the engine identity")
    connected = False
    try:
        if kind == "tcp":
            with socket.create_connection((host, int(port)), timeout=0.4):
                connected = True
        else:
            question = (
                b"".join(
                    bytes([len(part)]) + part.encode()
                    for part in "vpn-probe.fixture".split(".")
                )
                + b"\0"
            )
            packet = (
                struct.pack("!HHHHHH", 71, 0x100, 1, 0, 0, 0)
                + question
                + struct.pack("!HH", 1, 1)
            )
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.4)
                sock.sendto(packet, (host, int(port)))
                answer = sock.recv(4096)
                connected = answer[:2] == packet[:2] and answer[
                    -4:
                ] == socket.inet_aton(PEER_IP)
    except OSError:
        pass
    print(
        json.dumps(
            {
                "connected": connected,
                "uid": os.geteuid(),
                "host": host,
                "port": port,
                "kind": kind,
            }
        )
    )


if __name__ == "__main__":
    if sys.argv[1] == "probe":
        probe(*sys.argv[2:])
    else:
        control(sys.argv[1])
