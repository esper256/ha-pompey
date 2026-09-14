#!/usr/bin/env python3
"""Validate and normalize a full-tunnel WireGuard configuration. No provider defaults."""
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys


def parse(text):
    sections = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].lower()
            if name not in {"interface", "peer"}:
                raise ValueError("Only [Interface] and [Peer] sections are supported")
            sections.append((name, []))
        elif "=" in line and sections:
            key, value = line.split("=", 1)
            sections[-1][1].append((key.strip(), value.strip()))
        else:
            raise ValueError("Invalid WireGuard configuration line")
    interfaces = [fields for name, fields in sections if name == "interface"]
    peers = [fields for name, fields in sections if name == "peer"]
    if len(interfaces) != 1 or not peers or sections[0][0] != "interface":
        raise ValueError("Use one [Interface] followed by at least one [Peer]")
    iface = {k.lower(): v for k, v in interfaces[0]}
    if not iface.get("privatekey") or not iface.get("address"):
        raise ValueError("The VPN file needs PrivateKey and Address")
    for addr in iface["address"].split(","):
        ipaddress.ip_interface(addr.strip())
    addresses = [ipaddress.ip_interface(addr.strip()) for addr in iface["address"].split(",")]
    if not any(addr.version == 4 for addr in addresses):
        raise ValueError("The VPN file needs an IPv4 tunnel address")
    networks = []
    for fields in peers:
        peer = {k.lower(): v for k, v in fields}
        if not peer.get("publickey") or not peer.get("endpoint"):
            raise ValueError("Each VPN peer needs PublicKey and Endpoint")
        endpoint(peer["endpoint"])
        networks.extend(ipaddress.ip_network(n.strip()) for n in peer.get("allowedips", "").split(",") if n.strip())
    v4 = list(ipaddress.collapse_addresses(n for n in networks if n.version == 4))
    if ipaddress.ip_network("0.0.0.0/0") not in v4:
        raise ValueError("The VPN must route all IPv4 traffic (AllowedIPs = 0.0.0.0/0)")
    return sections


def endpoint(value):
    host, sep, port = value.rpartition(":")
    host = host.strip("[]")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("Endpoint must be a hostname or IP followed by a valid port")
    return host, int(port)


def render(text, resolve=True):
    sections = parse(text)
    out, endpoints, dns, ipv6 = [], [], [], False
    for section, fields in sections:
        out.append("[" + section.title() + "]")
        if section == "interface":
            out.append("Table = off")
        keepalive = False
        for key, value in fields:
            low = key.lower()
            if low in {"preup", "postup", "predown", "postdown", "table", "saveconfig"}:
                continue
            if low == "dns":
                for entry in value.split(","):
                    try:
                        dns.append(str(ipaddress.ip_address(entry.strip())))
                    except ValueError:
                        pass  # wg-quick permits search domains; they are not resolvers.
                continue
            if low == "endpoint":
                host, port = endpoint(value)
                try:
                    ip = str(ipaddress.ip_address(host))
                except ValueError:
                    if not resolve:
                        ip = host
                    else:
                        ip = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0][4][0]
                endpoints.append({"ip": ip, "port": port})
                value = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            if low == "allowedips":
                nets = [ipaddress.ip_network(n.strip()) for n in value.split(",")]
                ipv6 |= ipaddress.ip_network("::/0") in list(ipaddress.collapse_addresses(n for n in nets if n.version == 6))
            keepalive |= low == "persistentkeepalive"
            out.append(f"{key} = {value}")
        if section == "peer" and not keepalive:
            out.append("PersistentKeepalive = 25")
        out.append("")
    return "\n".join(out), {"endpoints": endpoints, "dns": dns, "ipv6": ipv6}


def main():
    text = Path(sys.argv[1]).read_text()
    config, metadata = render(text)
    dest = Path(sys.argv[2])
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(config)
    tmp.chmod(0o600)
    tmp.replace(dest)
    meta = Path(sys.argv[3])
    meta.parent.mkdir(parents=True, exist_ok=True)
    temp = meta.with_suffix(".tmp")
    temp.write_text(json.dumps(metadata))
    temp.replace(meta)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
