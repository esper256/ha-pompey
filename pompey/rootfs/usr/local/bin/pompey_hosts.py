"""Learn explicit access names from authenticated Ingress without a wildcard."""

import ipaddress
import json
import re
import subprocess
from urllib.parse import urlsplit

import pompey_common as api
import pompey_state as state
from pompey_runtime import stack_lock


def hostname(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Expected an explicit access hostname or IP address")
    candidate = value.removeprefix("[").removesuffix("]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        address = None
    if address is not None:
        if address.is_unspecified or address.is_multicast or getattr(address, "scope_id", None):
            raise ValueError("A bind address is not an access hostname")
        return "[" + str(address) + "]" if address.version == 6 else str(address)
    if any(c in value for c in ":/*,;@[]") or re.fullmatch(r"[\d.]+", value):
        raise ValueError("Use an explicit hostname without a URL, port or wildcard")
    result = value.rstrip(".").encode("idna").decode("ascii").lower()
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in result.split(".")):
        raise ValueError("Invalid access hostname")
    return result


def managed_hosts():
    remembered = state.load("access-hosts").get("hosts", [])
    if not remembered:
        return []  # Do not lock out an unknown HA address before first Ingress use.
    names = {"homeassistant", "homeassistant.local", "localhost", "127.0.0.1", "[::1]"}
    names.update(hostname(value) for value in remembered)
    names.add(hostname(urlsplit(api.prowlarr_url()).hostname))
    # Local interface addresses, not hardcoded Supervisor subnet assumptions.
    interfaces = json.loads(subprocess.check_output(["ip", "-j", "address", "show"], timeout=2))
    for interface in interfaces:
        for item in interface.get("addr_info", []):
            if item.get("family") in {"inet", "inet6"}:
                try:
                    names.add(hostname(item["local"]))
                except ValueError:
                    pass
    return sorted(names)


def merged_hosts(existing, managed):
    # Preserve explicit user names/patterns, replacing only unrestricted/bind entries.
    previous = [value.strip() for value in re.split("[,;]", existing or "")
                if value.strip() not in {"", "*", "0.0.0.0", "::", "[::]"}]
    return ",".join(dict.fromkeys([*previous, *managed]))


def apply_hosts(key):
    names = managed_hosts()
    if not names:
        return
    url = api.prowlarr_url() + "/api/v1/config/host"
    headers = api.arr_headers(key)
    current = api.http("GET", url, headers=headers, timeout=2)
    wanted = merged_hosts(current.get("allowedHosts"), names)
    if wanted != current.get("allowedHosts"):
        api.http("PUT", url, {**current, "allowedHosts": wanted}, headers=headers, timeout=2)


def remember(value):
    name = hostname(value)
    with stack_lock(blocking=False):
        hosts = state.load("access-hosts").get("hosts", [])
        if name in hosts:
            return {"ok": True}
        if len(hosts) >= 20:
            raise ValueError("Access hostname limit reached; review the saved access names")
        state.save("access-hosts", {"hosts": [*hosts, name]})
        try:
            apply_hosts(api.load_secrets()["prowlarr_api_key"])
        except (OSError, RuntimeError, KeyError, ValueError):
            # Normal configuration reconciliation retries after startup/outages.
            return {"ok": True, "pending": True}
    return {"ok": True, "pending": False}
