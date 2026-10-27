"""Keep the Supervisor version, local build default and release notes coherent."""

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?")


def current(root=ROOT):
    text = (root / "pompey/config.yaml").read_text()
    value = re.search(r'^version: "([^"]+)"$', text, re.M)
    if not value or not VERSION.fullmatch(value[1]):
        raise ValueError("Invalid Pompey version")
    return value[1]


def set_version(value, root=ROOT):
    if not VERSION.fullmatch(value):
        raise ValueError("Invalid Pompey version")
    old = current(root)
    path = root / "pompey/config.yaml"
    path.write_text(path.read_text().replace('version: "' + old + '"', 'version: "' + value + '"', 1))
    path = root / "pompey/Dockerfile"
    text, count = re.subn(r'^ARG BUILD_VERSION="[^"]+"$', 'ARG BUILD_VERSION="' + value + '"', path.read_text(), flags=re.M)
    if count != 1:
        raise ValueError("Build version declaration missing")
    path.write_text(text)


def next_patch(value):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError("Automatic releases require a stable Pompey version")
    major, minor, patch = map(int, value.split("."))
    return f"{major}.{minor}.{patch + 1}"



def split_unreleased(changelog):
    header = "# Changelog\n\n"
    if not changelog.startswith(header):
        raise ValueError("Changelog header missing")
    body = changelog[len(header):]
    marker = "## Unreleased\n"
    if not body.startswith(marker):
        return "", body
    pending, separator, history = body[len(marker):].partition("\n## ")
    return pending.strip(), "## " + history if separator else ""


def release_notes(changelog, version, addition):
    pending, history = split_unreleased(changelog)
    entries = addition.strip() + ("\n\n" + pending if pending else "")
    return "# Changelog\n\n## " + version + "\n\n" + entries + "\n\n" + history

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["current", "set"])
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    if args.action == "set":
        set_version(args.version or "")
    print(current())

