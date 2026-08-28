"""Unpack and run the official Seerr image the way the addon does.

The published image is Alpine/musl. Host glibc node cannot load its sqlite3
native addon, so we chroot into the crane export and use the image's node.
That is the same artifact fetch-engines unpacks on HAOS.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

IMAGE = os.environ.get("POMPEY_SEERR_IMAGE", "ghcr.io/seerr-team/seerr:latest")
CACHE = Path(os.environ.get("POMPEY_SEERR_CACHE", Path.home() / ".cache/pompey/seerr"))
CRANE_DIR = Path(os.environ.get("POMPEY_CRANE_DIR", Path.home() / ".cache/pompey/bin"))
CONFIG_NAME = "pompey-test-config"


def crane_archive_url() -> str:
    machine = os.uname().machine
    name = "Linux_arm64" if machine in {"aarch64", "arm64"} else "Linux_x86_64"
    return (
        "https://github.com/google/go-containerregistry/releases/download/"
        f"v0.22.0/go-containerregistry_{name}.tar.gz"
    )


def crane_platform() -> str:
    machine = os.uname().machine
    if machine in {"aarch64", "arm64"}:
        return "linux/arm64"
    return "linux/amd64"


def ensure_crane() -> Path:
    found = shutil.which("crane")
    if found:
        return Path(found)
    CRANE_DIR.mkdir(parents=True, exist_ok=True)
    crane = CRANE_DIR / "crane"
    if crane.is_file() and os.access(crane, os.X_OK):
        return crane
    # Same curl|tar as pompey/Dockerfile. tar -xz without -f reads stdin.
    archive = CRANE_DIR / "crane.tgz"
    archive.unlink(missing_ok=True)
    subprocess.run(
        [
            "curl",
            "-fsSL",
            "--retry",
            "3",
            "--retry-delay",
            "2",
            "-o",
            str(archive),
            crane_archive_url(),
        ],
        check=True,
    )
    subprocess.run(
        ["tar", "-xzf", str(archive), "-C", str(CRANE_DIR), "crane"],
        check=True,
    )
    archive.unlink(missing_ok=True)
    crane.chmod(0o755)
    if not crane.is_file():
        raise RuntimeError("crane archive did not contain crane")
    return crane


def ensure_unpacked() -> Path:
    marker = CACHE / "app" / "dist" / "index.js"
    if marker.is_file() and (CACHE / "usr/local/bin/node").is_file():
        return CACHE
    crane = ensure_crane()
    staging = CACHE.parent / "seerr.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    export = subprocess.Popen(
        [str(crane), "export", "--platform", crane_platform(), IMAGE],
        stdout=subprocess.PIPE,
    )
    try:
        unpack = subprocess.run(
            ["tar", "--no-same-owner", "--no-same-permissions", "-x", "-C", str(staging)],
            stdin=export.stdout,
            check=False,
        )
    finally:
        if export.stdout:
            export.stdout.close()
        export.wait()
    if export.returncode:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"crane export {IMAGE} failed ({export.returncode})")
    if unpack.returncode:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"tar unpack of {IMAGE} failed ({unpack.returncode})")
    if not (staging / "app/dist/index.js").is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"{IMAGE} did not contain /app/dist/index.js")
    shutil.rmtree(CACHE, ignore_errors=True)
    staging.rename(CACHE)
    return CACHE


def config_dir() -> Path:
    return CACHE / CONFIG_NAME


def reset_config() -> Path:
    dest = config_dir()
    # Seerr in the chroot writes as root, so a leftover config is root-owned.
    subprocess.run(["sudo", "-n", "rm", "-rf", str(dest)], check=True)
    dest.mkdir(parents=True)
    (dest / "DOCKER").touch()
    return dest


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_public(url: str, tries: int = 60, delay: float = 0.5) -> dict:
    last = None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url + "/api/v1/settings/public", timeout=3) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"Seerr never became ready at {url}: {last}")


class SeerrProcess:
    def __init__(self, port: int | None = None):
        self.root = ensure_unpacked()
        self.config = reset_config()
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.log_path = self.config / "seerr.log"

    def start(self) -> "SeerrProcess":
        inner = (
            "cd /app && "
            "export NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production "
            f"PORT={self.port} HOST=127.0.0.1 BIND_IP=127.0.0.1 "
            f"CONFIG_DIRECTORY=/{CONFIG_NAME} && "
            "exec /usr/local/bin/node dist/index.js"
        )
        logf = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["sudo", "-n", "chroot", str(self.root), "/bin/sh", "-c", inner],
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        logf.close()
        try:
            wait_public(self.url)
        except Exception:
            self.stop()
            tail = ""
            if self.log_path.is_file():
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Seerr failed to start on {self.url}\n{tail}") from None
        return self

    def stop(self) -> None:
        subprocess.run(
            ["sudo", "-n", "pkill", "-f", f"PORT={self.port} "],
            check=False,
            capture_output=True,
        )
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def settings(self) -> dict:
        path = self.config / "settings.json"
        return json.loads(path.read_text(encoding="utf-8"))


def node_check(root: Path, text: str, name: str = "pompey-rewrite-check.js") -> subprocess.CompletedProcess:
    """Parse JS with the image's musl node (host glibc node is the wrong ABI)."""
    check = root / name
    check.write_text(text, encoding="utf-8")
    try:
        return subprocess.run(
            [
                "sudo",
                "-n",
                "chroot",
                str(root),
                "/usr/local/bin/node",
                "--check",
                f"/{name}",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        check.unlink(missing_ok=True)
