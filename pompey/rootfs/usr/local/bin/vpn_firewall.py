#!/usr/bin/env python3
"""Generate an atomic, dual-family, fail-closed firewall for this container only."""
import ipaddress
import os
from pathlib import Path
import sys
from vpn_config import render


def rules(text, lan):
    _, config = render(text, resolve=False)
    lines = ["add table inet pompey", "delete table inet pompey", "table inet pompey {", "chain output {",
             "type filter hook output priority 0; policy drop;",
             'oifname "lo" accept', 'oifname "wg0" accept',
             # Replies to incoming UI/Plex connections may use the LAN. Do not
             # accept established outbound flows on an unprotected interface.
             'ct direction reply ct state established,related tcp sport { 5055, 9696, 8099 } accept',
             'icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit } ip6 hoplimit 255 accept']
    for raw in ["172.30.32.0/23", *lan.split(",")]:
        if raw.strip():
            net = ipaddress.ip_network(raw.strip())
            family = "ip6" if net.version == 6 else "ip"
            lines.append(f"{family} daddr {net} accept")
    for peer in config["endpoints"]:
        ip = ipaddress.ip_address(peer["ip"])
        family = "ip6" if ip.version == 6 else "ip"
        lines.append(f"{family} daddr {ip} udp dport {peer['port']} accept")
    return "\n".join([*lines, "}", "}", ""])


if __name__ == "__main__":
    print(rules(Path(sys.argv[1]).read_text(), os.environ.get("POMPEY_LAN_NETWORKS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")))
