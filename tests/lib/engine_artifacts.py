#!/usr/bin/env python3
"""Servarr artifact helpers for tests.

Linux Prowlarr/Sonarr/Radarr are .NET: ELF launcher plus many .dll files
(those assemblies are PE/MZ even on Linux). Windows builds are .zip.
Tests must unpack a real linux-musl tar.gz, not only Range-GET the URL.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ELF = b"\x7fELF" + bytes(64)
MZ_DLL = b"MZ" + bytes(120)


def write_linux_tarball(path: Path, name: str = "Prowlarr") -> None:
    """Prowlarr-shaped gzip: ELF launcher + .dll sidecars with archive modes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:

        def add(arcname: str, data: bytes, mode: int) -> None:
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            tf.addfile(info, io.BytesIO(data))

        add(f"{name}/{name}", ELF, 0o755)
        add(f"{name}/System.Xml.ReaderWriter.dll", MZ_DLL, 0o644)
        add(
            f"{name}/ja/Microsoft.Data.SqlClient.resources.dll",
            MZ_DLL,
            0o644,
        )


def write_windows_zip(path: Path, name: str = "Prowlarr") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{name}/{name}.exe", b"MZ" + bytes(64))
        zf.writestr(f"{name}/System.Xml.ReaderWriter.dll", MZ_DLL)


def write_pe_tarball(path: Path, name: str = "Prowlarr") -> None:
    """Gzip tarball whose launcher is PE — must be rejected after unpack."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(f"{name}/{name}")
        data = b"MZ" + bytes(64)
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))


def magic(path: Path, n: int = 4) -> str:
    data = path.read_bytes()[:n]
    return data.hex()


def is_elf(path: Path) -> bool:
    return path.read_bytes()[:4] == b"\x7fELF"


def is_pe(path: Path) -> bool:
    return path.read_bytes()[:2] == b"MZ"


def is_gzip(path: Path) -> bool:
    return path.read_bytes()[:2] == b"\x1f\x8b"


def is_zip(path: Path) -> bool:
    return path.read_bytes()[:2] == b"PK"


def filename_from_headers(header_text: str) -> str:
    name = ""
    for raw in header_text.splitlines():
        if raw.lower().startswith("content-disposition:"):
            m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", raw, re.I)
            if not m:
                m = re.search(r'filename="([^"]+)"', raw, re.I)
            if not m:
                m = re.search(r"filename=([^;]+)", raw, re.I)
            if m:
                name = m.group(1).strip().strip('"')
    return name


def _curl(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["curl", "-sS", "-L", "--retry", "3", "--retry-delay", "2", "--retry-all-errors",
         "--max-time", str(timeout), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def inspect_url(url: str, require_musl: bool = False) -> dict[str, str]:
    """Range-GET the first bytes and Content-Disposition. No full download."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pompey-inspect-") as td:
        body = Path(td) / "body"
        hdr = Path(td) / "hdr"
        effective = _curl(
            ["-D", str(hdr), "-o", str(body), "-r", "0-255", "-w", "%{url_effective}", url],
            timeout=40,
        ).stdout.strip()
        raw = body.read_bytes()[:256]
        header_text = hdr.read_text(encoding="latin-1", errors="replace")

    filename = filename_from_headers(header_text)
    if not filename:
        filename = Path(effective.split("?", 1)[0]).name
    kind = raw[:2]
    info = {
        "url": url,
        "final": effective,
        "filename": filename,
        "magic": raw[:4].hex(),
    }
    blob = f"{filename} {effective}".lower()
    if url.rstrip("/").endswith("qbittorrent-nox"):
        if raw[:4] != b"\x7fELF":
            raise SystemExit(f"qBittorrent-nox is not ELF (magic {raw[:4].hex()})")
        return info
    if "windows" in blob or filename.lower().endswith(".zip") or kind == b"PK":
        raise SystemExit(
            f"Servarr URL looks like Windows ({filename or effective}); "
            f"HAOS needs a linux tarball. {url}"
        )
    if kind != b"\x1f\x8b":
        raise SystemExit(f"{filename or url} is not gzip (magic {raw[:4].hex()})")
    if require_musl and "musl" not in filename.lower() and "musl" not in effective.lower():
        raise SystemExit(f"{filename or effective} is not a musl build (url {url})")
    return info


def cache_url(url: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta = inspect_url(url, require_musl="linuxmusl" in url or "os=linuxmusl" in url)
    filename = meta["filename"] or "artifact.tar.gz"
    filename = Path(filename).name
    dest = cache_dir / filename
    marker = dest.with_suffix(dest.suffix + ".url")
    if (
        dest.is_file()
        and dest.stat().st_size > 1024 * 1024
        and marker.is_file()
        and marker.read_text().strip() == url
    ):
        return dest
    print(f"downloading {filename} from {url}", file=sys.stderr)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    subprocess.run(
        ["curl", "-fsSL", "--retry", "3", "--retry-delay", "2", "--max-time", "300", "-o", str(tmp), url],
        check=True,
    )
    tmp.replace(dest)
    marker.write_text(url + "\n")
    if dest.read_bytes()[:2] != b"\x1f\x8b":
        dest.unlink(missing_ok=True)
        raise SystemExit(f"cached {dest} is not gzip")
    return dest


class DispositionHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        name = Path(self.path.split("?", 1)[0]).name
        if name:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def serve(root: Path) -> None:
    os.chdir(root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DispositionHandler)
    port = httpd.server_address[1]
    print(f"PORT={port}", flush=True)
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make-linux")
    m.add_argument("path")
    m.add_argument("name", nargs="?", default="Prowlarr")

    z = sub.add_parser("make-windows-zip")
    z.add_argument("path")
    z.add_argument("name", nargs="?", default="Prowlarr")

    t = sub.add_parser("make-pe-tar")
    t.add_argument("path")
    t.add_argument("name", nargs="?", default="Prowlarr")

    g = sub.add_parser("magic")
    g.add_argument("path")

    i = sub.add_parser("inspect-url")
    i.add_argument("url")
    i.add_argument("--require-musl", action="store_true")

    c = sub.add_parser("cache-url")
    c.add_argument("url")
    c.add_argument("cache_dir")

    s = sub.add_parser("serve")
    s.add_argument("root")

    e = sub.add_parser("assert-elf")
    e.add_argument("path")

    args = p.parse_args(argv)
    if args.cmd == "make-linux":
        write_linux_tarball(Path(args.path), args.name)
    elif args.cmd == "make-windows-zip":
        write_windows_zip(Path(args.path), args.name)
    elif args.cmd == "make-pe-tar":
        write_pe_tarball(Path(args.path), args.name)
    elif args.cmd == "magic":
        print(magic(Path(args.path)))
    elif args.cmd == "inspect-url":
        info = inspect_url(args.url, require_musl=args.require_musl)
        for key, val in info.items():
            print(f"{key}={val}")
    elif args.cmd == "cache-url":
        print(cache_url(args.url, Path(args.cache_dir)))
    elif args.cmd == "serve":
        serve(Path(args.root))
    elif args.cmd == "assert-elf":
        path = Path(args.path)
        if not is_elf(path):
            kind = "PE/MZ" if is_pe(path) else magic(path)
            raise SystemExit(f"{path} is not ELF ({kind})")
        print(f"{path} is ELF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
