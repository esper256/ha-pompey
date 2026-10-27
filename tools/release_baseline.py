"""Select an immutable released baseline, including the HA-only 0.4.2 release."""

import json
import re
import subprocess

FALLBACK = "bc8e99d1dce6d47c7a3ad5205e80b7c68c36830d"


def choose(release):
    tag = release.get("tag_name", "")
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    if match and not release.get("prerelease") and tuple(map(int, match.groups())) > (0, 4, 2):
        return tag
    return FALLBACK


if __name__ == "__main__":
    # A failed API lookup is a release failure, not permission to silently choose
    # a stale baseline. The fallback applies only to the known HA-only release.
    raw = subprocess.check_output(["gh", "api", "repos/esper256/ha-pompey/releases/latest"], text=True)
    print(choose(json.loads(raw)))
