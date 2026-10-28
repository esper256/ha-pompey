"""Small local checks. Never build or start the product application stack."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("POMPEY_FAST_IMAGE", "localhost/pompey:dev-tools")


def container(prepare=False):
    from run import runtime
    command = runtime()
    if prepare:
        flags = ["--format", "docker"] if any(Path(part).name == "podman" for part in command) else []
        subprocess.run(command + ["build", *flags, "-f", "tests/Dockerfile.fast", "-t", IMAGE, "."],
                       cwd=ROOT, check=True)
        return
    found = subprocess.run(command + ["image", "inspect", IMAGE], capture_output=True)
    if found.returncode:
        raise RuntimeError("Install Node.js and ShellCheck, or run 'bash tests/run.sh --prepare' once. Fast checks never download or build tools automatically.")
    subprocess.run(command + ["run", "--rm", "--pull=never", "--network", "none",
                              "--security-opt", "label=disable", "-v", str(ROOT) + ":/workspace:ro",
                              "-w", "/workspace", "-e", "PYTHONDONTWRITEBYTECODE=1",
                              "--entrypoint", "python3", IMAGE, "/workspace/tests/fast.py", "--host"], check=True)


def run(label, command):
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
    if result.returncode:
        print(result.stdout + result.stderr, end="", file=sys.stderr)
        raise RuntimeError(label + " failed")
    print(f"PASS {label}: {time.monotonic() - started:.3f}s", flush=True)


def checks():
    started = time.monotonic()
    for folder in ["pompey", "tests", "tools"]:
        for path in (ROOT / folder).rglob("*.py"):
            compile(path.read_bytes(), str(path), "exec")
    print(f"PASS Python syntax: {time.monotonic() - started:.3f}s", flush=True)
    for script in ["test_logic.py", "test_harness.py", "test_release_tools.py", "test_ci_policy.py"]:
        run(script, [sys.executable, str(ROOT / "tests" / script)])
    run("UI JavaScript syntax", ["node", "tests/ui_syntax.js"])
    run("Ingress routing", ["node", "tests/test_ingress.js"])
    shells = {"bash": [], "sh": []}
    for folder in ["pompey/rootfs/etc", "pompey/rootfs/usr/local/bin", "tests", "tools"]:
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file() or path.suffix == ".py":
                continue
            with path.open("rb") as source:
                first = source.readline(256)
            if first.startswith(b"#!"):
                if b"bash" in first:
                    shells["bash"].append(str(path))
                elif first.strip() in {b"#!/bin/sh", b"#!/usr/bin/env sh"}:
                    shells["sh"].append(str(path))
    for shell, paths in shells.items():
        if paths:
            run(shell + " entrypoint lint", ["shellcheck", "-s", shell, *paths])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--host", action="store_true", help="Require local Node.js and ShellCheck; never use a container")
    args = parser.parse_args()
    if args.prepare:
        container(prepare=True)
        return
    started = time.monotonic()
    local = all(shutil.which(name) for name in ["node", "shellcheck"])
    if local:
        checks()
    elif args.host:
        raise RuntimeError("Fast checks require Node.js and ShellCheck on PATH")
    else:
        container()
    label = "Fast checks complete" if local else "Fast checks including tool startup"
    print(f"{label}: {time.monotonic() - started:.3f}s", flush=True)


if __name__ == "__main__":
    main()
