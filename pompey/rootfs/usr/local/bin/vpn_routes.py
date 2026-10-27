#!/usr/bin/env python3
"""Keep endpoint/LAN routes outside WireGuard; install more-specific tunnel defaults."""

import ipaddress
import json
import os
from pathlib import Path
import subprocess

UI_PORTS = (5055, 9696, 8099)
UI_REPLY_TABLE = 20560
UI_REPLY_PRIORITY = 11000


def ip(*args):
    return subprocess.check_output(["ip", *args], text=True)


def preserve_ui_replies(family, original):
    """Route published TCP service replies through the original interface.

    The firewall separately permits established reply traffic only. Selecting a
    source port here cannot authorize a new cleartext outbound connection.
    """
    if not original:
        return
    flag = f"-{family}"
    dev = original["dev"]
    # Keep same-bridge Supervisor/Ingress clients directly reachable too.
    for route in json.loads(ip(flag, "-j", "route", "show", "dev", dev)):
        destination = route.get("dst")
        if (
            destination
            and destination != "default"
            and not route.get("gateway")
            and route.get("type", "unicast") == "unicast"
        ):
            ip(
                flag, "route", "replace", "table", str(UI_REPLY_TABLE),
                destination, "dev", dev,
            )
    via = ["via", original["gateway"]] if original.get("gateway") else []
    ip(
        flag, "route", "replace", "table", str(UI_REPLY_TABLE),
        "default", *via, "dev", dev,
    )
    installed = json.loads(ip(flag, "-j", "rule", "show"))
    for offset, port in enumerate(UI_PORTS):
        priority = UI_REPLY_PRIORITY + offset
        existing = next(
            (rule for rule in installed if rule.get("priority") == priority), None,
        )
        if existing:
            if str(existing.get("table")) != str(UI_REPLY_TABLE):
                raise RuntimeError("Published-interface routing priority is already in use")
            continue
        ip(
            flag, "rule", "add", "priority", str(priority), "ipproto", "tcp",
            "sport", str(port), "lookup", str(UI_REPLY_TABLE),
        )


def main():
    ready = Path(os.environ.get("POMPEY_READY", "/tmp/pompey"))
    config = json.loads((ready / "vpn-config.json").read_text())
    for family in (4, 6):
        routes = json.loads(ip(f"-{family}", "-j", "route", "show", "default"))
        orig = next((r for r in routes if r.get("dev") != "wg0"), None)
        preserve_ui_replies(family, orig)
        destinations = [
            p["ip"]
            for p in config["endpoints"]
            if ipaddress.ip_address(p["ip"]).version == family
            and not ipaddress.ip_address(p["ip"]).is_loopback
        ]
        for destination in destinations:
            candidates = json.loads(ip(f"-{family}", "-j", "route", "get", destination))
            route = next((r for r in candidates if r.get("dev") != "wg0"), orig)
            if not route:
                raise RuntimeError(f"No IPv{family} route to the VPN endpoint")
            via = ["via", route["gateway"]] if route.get("gateway") else []
            ip(f"-{family}", "route", "replace", destination, *via, "dev", route["dev"])
        lans = os.environ.get("POMPEY_LAN_NETWORKS", "").split(",")
        if orig:
            via = ["via", orig["gateway"]] if orig.get("gateway") else []
            for destination in lans:
                if (
                    destination.strip()
                    and ipaddress.ip_network(destination.strip()).version == family
                ):
                    ip(
                        f"-{family}",
                        "route",
                        "replace",
                        destination.strip(),
                        *via,
                        "dev",
                        orig["dev"],
                    )
        if family == 4 or config["ipv6"]:
            for net in (
                ["0.0.0.0/1", "128.0.0.0/1"] if family == 4 else ["::/1", "8000::/1"]
            ):
                ip(f"-{family}", "route", "replace", net, "dev", "wg0")
        targets = config["dns"] + (
            [os.environ["NAT_PMP_GATEWAY"]] if os.environ.get("NAT_PMP_GATEWAY") else []
        )
        for dns in targets:
            if ipaddress.ip_address(dns).version == family:
                if family == 6 and not config["ipv6"]:
                    raise RuntimeError("IPv6 DNS requires IPv6 AllowedIPs")
                ip(f"-{family}", "route", "replace", dns, "dev", "wg0")
    if config["dns"]:
        Path(os.environ.get("POMPEY_RESOLV", "/etc/resolv.conf")).write_text(
            "".join(f"nameserver {addr}\n" for addr in config["dns"])
        )


if __name__ == "__main__":
    main()
