#!/usr/bin/env python3
"""Build-time only: verify and vendor the release manifest into an immutable image."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


def target():
    return "linuxmusl-amd64"


def download(artifact, dest):
    algorithm = "sha256" if "sha256" in artifact else "sha512"
    expected = artifact.get(algorithm)
    if not expected:
        raise ValueError("An engine artifact must have a checksum")
    digest = hashlib.new(algorithm)
    with (
        urllib.request.urlopen(artifact["url"], timeout=60) as incoming,
        dest.open("wb") as out,
    ):
        while chunk := incoming.read(1024 * 1024):
            out.write(chunk)
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        raise RuntimeError("Engine checksum mismatch")


def unpack(archive, dest, prefixes=None):
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # data_filter rejects escaping paths, special devices and unsafe links.
        members = (
            None
            if prefixes is None
            else [
                m
                for m in tar.getmembers()
                if any(
                    m.name.removeprefix("./") == p
                    or m.name.removeprefix("./").startswith(p + "/")
                    for p in prefixes
                )
            ]
        )
        tar.extractall(dest, members=members, filter="data")


def elf(path):
    with path.open("rb") as inp:
        if inp.read(4) != b"\x7fELF":
            raise RuntimeError(f"{path.name} is not a Linux executable")
    path.chmod(path.stat().st_mode | 0o111)


def stage(name, entry, folder):
    folder.mkdir(parents=True)
    if name == "seerr":
        image = entry["image"]
        if "@sha256:" not in image:
            raise ValueError("Seerr image must be pinned by digest")
        archive = folder / "image.tar"
        subprocess.run(
            ["crane", "export", "--platform", "linux/amd64", image, str(archive)],
            check=True,
            timeout=600,
        )
        unpack(archive, folder / "unpacked", prefixes=["app", "usr/local/bin/node"])
        archive.unlink()
        dest = folder / "unpacked"
        if not (dest / "app/dist/index.js").is_file():
            raise RuntimeError("Seerr image has no application")
        elf(dest / "usr/local/bin/node")
        config = dest / "app/config"
        if config.is_symlink() or config.is_file():
            config.unlink()
        elif config.exists():
            shutil.rmtree(config)
        config.symlink_to(Path(os.environ.get("POMPEY_CONFIG", "/config")) / "seerr")
        return dest
    artifact = entry["artifacts"][target()]
    archive = folder / "download"
    download(artifact, archive)
    if name == "qbittorrent-nox":
        elf(archive)
        return archive
    unpack(archive, folder / "unpacked")
    archive.unlink()
    launcher = {"recyclarr": "recyclarr", "dotnet": "dotnet"}.get(name, name)
    candidates = list((folder / "unpacked").rglob(launcher))
    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        raise RuntimeError(f"No {launcher} in artifact")
    elf(found)
    # Native libraries/apphosts must retain executable mapping permissions.
    for path in found.parent.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as inp:
                if inp.read(4) == b"\x7fELF":
                    path.chmod(path.stat().st_mode | 0o111)
    return found.parent


def main():
    manifest = json.loads(Path("/usr/share/pompey/engines.json").read_text())
    root = Path("/opt/pompey/engines")
    root.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as temp:
        for name, entry in manifest["engines"].items():
            print("Vendoring", name, entry["version"], flush=True)
            staged = stage(name, entry, Path(temp) / name)
            staged.rename(root / name)
    for name, url in [
        ("trash_guides", "https://github.com/TRaSH-Guides/Guides.git"),
        ("config_templates", "https://github.com/recyclarr/config-templates.git"),
    ]:
        commit = manifest["resources"][name]
        if not re.fullmatch("[a-f0-9]{40}", commit):
            raise ValueError("Resource commits must be pinned")
        dest = Path("/opt/pompey/resources") / name
        dest.mkdir(parents=True)
        subprocess.run(["git", "init", str(dest)], check=True)
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth=1", url, commit], check=True
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"], check=True
        )
        if (
            subprocess.check_output(
                ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True
            ).strip()
            != commit
        ):
            raise RuntimeError("Resource commit mismatch")
        shutil.rmtree(dest / ".git")


if __name__ == "__main__":
    main()
