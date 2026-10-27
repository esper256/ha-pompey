#!/usr/bin/env python3
"""Generate an atomic, dual-family, fail-closed firewall for this container only."""

import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
from vpn_config import render, bootstrap_servers, WIREGUARD_MARK, BOOTSTRAP_DNS_UID


def container_networks():
    """Discover private on-link container networks without assuming HAOS IPs."""
    networks = set()
    for family in (4, 6):
        routes = json.loads(
            subprocess.check_output(
                ["ip", f"-{family}", "-j", "route", "show", "proto", "kernel"],
                text=True,
            )
        )
        for route in routes:
            if route.get("dev") in {"lo", "wg0"} or route.get("gateway"):
                continue
            destination = route.get("dst")
            if not destination or destination == "default":
                continue
            net = ipaddress.ip_network(destination)
            if net.is_private and not net.is_loopback:
                networks.add(str(net))
    return sorted(networks)


def rules(text, lan, connected=()):
    config = render(text, resolve=False)[1] if text else {"endpoints": []}
    lines = [
        "add table inet pompey",
        "delete table inet pompey",
        "table inet pompey {",
        "chain output {",
        "type filter hook output priority 0; policy drop;",
        'oifname "lo" accept',
        'oifname "wg0" accept',
        # Replies to incoming UI/Plex connections may use the LAN. Do not
        # accept established outbound flows on an unprotected interface.
        "ct direction reply ct state established,related tcp sport { 5055, 9696, 8099 } accept",
        "icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit } ip6 hoplimit 255 accept",
    ]
    try:
        servers = bootstrap_servers()
    except FileNotFoundError:
        servers = []
    for server in servers:
        family = "ip6" if ipaddress.ip_address(server).version == 6 else "ip"
        lines.append(f"meta skuid {BOOTSTRAP_DNS_UID} {family} daddr {server} udp dport 53 accept")
        lines.append(f"meta skuid {BOOTSTRAP_DNS_UID} {family} daddr {server} tcp dport 53 accept")
    for peer in config["endpoints"]:
        ip = ipaddress.ip_address(peer["ip"])
        family = "ip6" if ip.version == 6 else "ip"
        lines.append(
            f"meta mark {WIREGUARD_MARK} {family} daddr {ip} udp dport {peer['port']} accept"
        )
    # Deny cleartext DNS for engines even when a LAN network is permitted.
    lines += ["udp dport 53 drop", "tcp dport 53 drop"]
    for raw in sorted(set([*connected, *lan.split(",")])):
        if raw.strip():
            net = ipaddress.ip_network(raw.strip())
            family = "ip6" if net.version == 6 else "ip"
            lines.append(f"{family} daddr {net} accept")
    return "\n".join([*lines, "}", "}", ""])


if __name__ == "__main__":
    print(
        rules(
            Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else "",
            os.environ.get("POMPEY_LAN_NETWORKS", ""),
            container_networks(),
        )
    )
