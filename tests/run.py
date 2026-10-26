#!/usr/bin/env python3
"""Build once and run focused logic, isolated packets, and the single real journey."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    configured = os.environ.get("POMPEY_CONTAINER_COMMAND")
    if configured:
        return shlex.split(configured)
    for name in ["podman", "docker"]:
        if shutil.which(name):
            return [name]
    if shutil.which("distrobox-host-exec"):
        return ["distrobox-host-exec", "podman"]
    raise RuntimeError("Install Docker/Podman or set POMPEY_CONTAINER_COMMAND")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--image", default="localhost/pompey:foundation")
    args = parser.parse_args()
    command = runtime()
    podman = "podman" in command
    results = ROOT / ".test-results"
    results.mkdir(exist_ok=True)

    def run(arguments, timeout=600, log=None):
        print("+", shlex.join(command + arguments), flush=True)
        if log is None:
            subprocess.run(command + arguments, cwd=ROOT, check=True, timeout=timeout)
        else:
            with log.open("w") as out:
                try:
                    subprocess.run(
                        command + arguments,
                        cwd=ROOT,
                        check=True,
                        timeout=timeout,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                    )
                finally:
                    print(log.read_text()[-12000:], flush=True)

    started = time.monotonic()
    build = ["build"] + (["--layers", "--format", "docker"] if podman else [])
    if not args.skip_build:
        run(
            build
            + [
                "--build-arg",
                "BUILD_ARCH=amd64",
                "--build-arg",
                "BUILD_VERSION=0.4.0",
                "-t",
                args.image,
                "pompey/",
            ],
            900,
        )
    test_image = args.image + "-tests"
    run(
        build
        + [
            "--build-arg",
            "POMPEY_IMAGE=" + args.image,
            "-f",
            "tests/journey/Dockerfile",
            "-t",
            test_image,
            ".",
        ],
        600,
    )
    # No s6 or downloader launches in this check container.
    run(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--security-opt",
            "label=disable",
            "--entrypoint",
            "python3",
            test_image,
            "/tests/checks.py",
        ]
    )
    # Every firewall mutation occurs inside namespaces created by this worker.
    run(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--privileged",
            "--security-opt",
            "label=disable",
            "-e",
            "POMPEY_REQUIRE_NETWORK_TESTS=1",
            "--entrypoint",
            "python3",
            test_image,
            "/tests/test_firewall.py",
            "-v",
        ],
        120,
    )
    name = "pompey-journey-" + uuid.uuid4().hex[:10]
    destination = results / name
    destination.mkdir()
    try:
        run(
            [
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--cap-add",
                "NET_ADMIN",
                "--cap-add",
                "SYS_ADMIN",
                "--security-opt",
                "label=disable",
                "-v",
                str(destination) + ":/artifacts",
                test_image,
            ],
            240,
            log=destination / "container.log",
        )
    finally:
        subprocess.run(
            command + ["rm", "--force", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Acceptance artifacts:", destination, flush=True)
    report = json.loads((destination / "result.json").read_text())
    assert report["success"], report
    print(
        "Build and checks elapsed:",
        round(time.monotonic() - started, 2),
        "seconds",
        flush=True,
    )


if __name__ == "__main__":
    main()
