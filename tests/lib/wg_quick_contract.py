#!/usr/bin/env python3
"""Fail if a runtime WireGuard conf would make `wg addconf` reject the file.

wg-quick only strips its extra keys from [Interface]. A `Table = off` (or DNS=)
line after [Peer] is passed to wg, which prints `Line unrecognized` and deletes
wg0. This check does not need Proton, a tunnel, or even the `wg` binary.
"""
from __future__ import annotations

import sys
from pathlib import Path

WGQUICK_IFACE = {
    "address",
    "dns",
    "mtu",
    "table",
    "preup",
    "postup",
    "predown",
    "postdown",
    "saveconfig",
}
WG_IFACE = {"privatekey", "listenport", "fwmark"}
WG_PEER = {
    "publickey",
    "presharedkey",
    "allowedips",
    "endpoint",
    "persistentkeepalive",
}


def check_text(text: str) -> list[str]:
    errors: list[str] = []
    section: str | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            errors.append(f"line {lineno}: not key = value")
            continue
        key = line.split("=", 1)[0].strip().lower()
        if section == "interface":
            if key not in WGQUICK_IFACE and key not in WG_IFACE:
                errors.append(
                    f"line {lineno}: {key} in [Interface] is not a wg-quick or wg key "
                    "(wg addconf would say Line unrecognized)"
                )
        elif section == "peer":
            if key in WGQUICK_IFACE:
                errors.append(
                    f"line {lineno}: {key} is a wg-quick [Interface] option but sits in "
                    "[Peer] (wg addconf: Line unrecognized)"
                )
            elif key not in WG_PEER:
                errors.append(
                    f"line {lineno}: {key} in [Peer] is not a wg key "
                    "(wg addconf would say Line unrecognized)"
                )
        else:
            errors.append(f"line {lineno}: {key} is outside [Interface]/[Peer]")
    return errors


def check_path(path: str | Path) -> list[str]:
    return check_text(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: wg_quick_contract.py WG0.CONF", file=sys.stderr)
        return 2
    errors = check_path(argv[1])
    if not errors:
        return 0
    print(f"{argv[1]} would fail wg addconf:", file=sys.stderr)
    for item in errors:
        print(f"  {item}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
