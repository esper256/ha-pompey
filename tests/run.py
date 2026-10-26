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
    parser.add_argument(
        "--journey-only",
        action="store_true",
        help="Focused journey timing; does not run logic or packet checks",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Independent batches for timing; any failure stops the run",
    )
    parser.add_argument(
        "--scenarios", type=int, default=3, help="Distinct household journeys per batch"
    )
    parser.add_argument(
        "--layout",
        choices=["shared", "cloned", "fresh"],
        default="shared",
        help="Reuse a running household, clone cold configured state, or start fresh per scenario",
    )
    args = parser.parse_args()
    if args.runs < 1 or not 1 <= args.scenarios <= 100:
        parser.error(
            "--runs must be positive and --scenarios must be between 1 and 100"
        )
    command = runtime()
    podman = "podman" in command
    results = ROOT / ".test-results"
    results.mkdir(exist_ok=True)
    measurements = {}

    def run(arguments, timeout=600, log=None, label=None):
        before = time.monotonic()
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
        if label:
            measurements[label] = round(time.monotonic() - before, 3)
            print("DURATION", label, measurements[label], "seconds", flush=True)

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
            label="product-image-build",
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
        label="test-image-build",
    )
    if not args.journey_only:
        check_suite(run, test_image)
    samples = [
        batch(run, command, test_image, results, args.layout, args.scenarios)
        for _ in range(args.runs)
    ]
    summary = {
        "sharedSeconds": measurements,
        "journeys": samples,
        "totalSeconds": round(time.monotonic() - started, 3),
    }
    (results / "timing-summary.json").write_text(json.dumps(summary, indent=2))
    print("Build and checks elapsed:", summary["totalSeconds"], "seconds", flush=True)
    print("Timing summary:", results / "timing-summary.json", flush=True)


def check_suite(run, test_image):
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
        ],
        label="logic-syntax-and-lint",
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
        label="isolated-packets",
    )


def batch(run, command, test_image, results, layout, count):
    run_id = uuid.uuid4().hex[:10]
    started = time.monotonic()
    if layout == "shared":
        containers = [
            journey(
                run, command, test_image, results, run_id, count, list(range(count))
            )
        ]
    else:
        first = journey(
            run,
            command,
            test_image,
            results,
            run_id,
            count,
            [0],
            export_seed=layout == "cloned" and count > 1,
        )
        containers = [first]
        for index in range(1, count):
            containers.append(
                journey(
                    run,
                    command,
                    test_image,
                    results,
                    run_id,
                    count,
                    [index],
                    seed=first.get("configuredSeed"),
                )
            )
    sample = {
        "layout": layout,
        "runId": run_id,
        "containers": containers,
        "wallSeconds": round(time.monotonic() - started, 3),
    }
    print(
        "BATCH",
        layout,
        count,
        "scenarios:",
        sample["wallSeconds"],
        "seconds",
        flush=True,
    )
    return sample


def journey(
    run,
    command,
    test_image,
    results,
    run_id,
    count,
    selected,
    seed=None,
    export_seed=False,
):
    name = "pompey-journey-" + uuid.uuid4().hex[:10]
    destination = results / name
    destination.mkdir()
    started = time.monotonic()
    snapshot_seconds = restore_seconds = 0
    flags = [
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
        "-e",
        "POMPEY_TEST_RUN_ID=" + run_id,
        "-e",
        "S6_SERVICES_GRACETIME=30000",
        "-e",
        "POMPEY_TEST_CASES=" + str(count),
        "-e",
        "POMPEY_TEST_SELECTED_CASES=" + json.dumps(selected),
        "-e",
        "POMPEY_JOB_INTERVALS=" + json.dumps({"configuration": 2, "routing": 1}),
    ]
    try:
        if seed:
            run(["create", *flags, "-e", "POMPEY_TEST_CONFIGURED=1", test_image], 60)
            before = time.monotonic()
            for folder in ["data", "config", "media"]:
                with (Path(seed) / (folder + ".tar")).open("rb") as archive:
                    subprocess.run(
                        command + ["cp", "-", name + ":/"],
                        stdin=archive,
                        check=True,
                        timeout=60,
                    )
            restore_seconds = round(time.monotonic() - before, 3)
            run(
                ["start", "--attach", name],
                240 + 60 * len(selected),
                log=destination / "container.log",
            )
        else:
            run(
                ["run", *flags, test_image],
                240 + 60 * len(selected),
                log=destination / "container.log",
            )
        report = json.loads((destination / "result.json").read_text())
        assert report["success"], report
        if export_seed:
            # PID 1 has exited: no app or controller can still write these databases.
            text = (destination / "container.log").read_text()
            if any(
                name + " exited (" in text
                for name in ["Radarr", "Sonarr", "Prowlarr", "Seerr", "qBittorrent"]
            ):
                raise RuntimeError(
                    "Refusing configured state after an unclean engine shutdown"
                )
            seed = destination / "configured-state"
            seed.mkdir()
            before = time.monotonic()
            for folder in ["data", "config", "media"]:
                with (seed / (folder + ".tar")).open("wb") as archive:
                    subprocess.run(
                        command + ["cp", name + ":/" + folder, "-"],
                        stdout=archive,
                        check=True,
                        timeout=60,
                    )
            snapshot_seconds = round(time.monotonic() - before, 3)
    finally:
        subprocess.run(
            command + ["rm", "--force", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Acceptance artifacts:", destination, flush=True)
    elapsed = round(time.monotonic() - started, 3)
    print(
        "Startup:", report["timings"]["household-setup-complete"], "seconds", flush=True
    )
    for case in report["cases"]:
        print(
            "CASE",
            case["prefix"],
            "certification",
            repr(case["certification"]),
            "seconds",
            case["timings"]["correct-library-verified"],
            "prior titles preserved",
            case["priorTitlesVerified"],
            flush=True,
        )
        previous = 0
        for stage, cumulative in case["timings"].items():
            print(f"  {stage:30} {cumulative - previous:7.3f}", flush=True)
            previous = cumulative
    overhead = round(
        elapsed
        - report["timings"]["batch-complete"]
        - snapshot_seconds
        - restore_seconds,
        3,
    )
    print(
        "Container lifecycle overhead:",
        overhead,
        "seconds; wall time:",
        elapsed,
        flush=True,
    )
    sample = {
        "artifacts": str(destination),
        "startupSeconds": report["timings"]["household-setup-complete"],
        "cases": report["cases"],
        "lifecycleOverheadSeconds": overhead,
        "wallSeconds": elapsed,
        "snapshotExportSeconds": snapshot_seconds,
        "snapshotRestoreSeconds": restore_seconds,
    }
    if export_seed:
        sample["configuredSeed"] = str(seed)
    (destination / "timing.json").write_text(json.dumps(sample, indent=2))
    return sample


if __name__ == "__main__":
    main()
