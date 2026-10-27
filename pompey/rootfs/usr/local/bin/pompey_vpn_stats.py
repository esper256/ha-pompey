"""Read tunnel counters for the controller's live status snapshot."""

import os
from pathlib import Path
import time


def sample():
    iface = os.environ.get("WG_IFACE", "wg0")
    result = {
        "iface": iface,
        "up": False,
        "rx_bytes": 0,
        "tx_bytes": 0,
        "sample_ms": int(time.time() * 1000),
    }
    try:
        for line in Path("/proc/net/dev").read_text().splitlines():
            name, sep, values = line.partition(":")
            if sep and name.strip() == iface:
                fields = values.split()
                result.update(
                    rx_bytes=int(fields[0]),
                    tx_bytes=int(fields[8]),
                    up=(
                        Path(os.environ.get("POMPEY_READY", "/tmp/pompey")) / "vpn-up"
                    ).exists(),
                )
                break
    except (OSError, ValueError, IndexError):
        pass
    return result
