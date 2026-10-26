#!/usr/bin/env python3
"""Build once and run logic, required isolated packets and real household batches."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import time
import uuid
from container_runtime import Runtime
from journey.support import save_json, STARTUP_TIMEOUT, SCENARIO_TIMEOUT

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
        "--scenarios", type=int, default=4, help="Distinct household journeys per batch"
    )
    parser.add_argument(
        "--layout",
        choices=["shared", "cloned", "fresh", "ci"],
        default="shared",
        help="Reuse a running household, clone cold configured state, or start fresh per scenario",
    )
    args = parser.parse_args()
    if args.runs < 1 or not 1 <= args.scenarios <= (99 if args.layout == "ci" else 100):
        parser.error(
            "--runs must be positive and --scenarios must be within catalogue capacity (99 for ci, 100 otherwise)"
        )
    command = runtime()
    results = ROOT / ".test-results"
    results.mkdir(exist_ok=True)
    started = time.monotonic()
    summary = {"success": False, "journeys": []}

    def checkpoint():
        summary["totalSeconds"] = round(time.monotonic() - started, 3)
        save_json(results / "timing-summary.json", summary)

    engine = Runtime(command, ROOT, checkpoint)
    summary["sharedSeconds"] = engine.measurements
    checkpoint()
    try:
        execute(args, engine, results, summary, checkpoint)
        summary["success"] = True
    except BaseException as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        checkpoint()
        print("Timing summary:", results / "timing-summary.json", flush=True)
    print("Build and checks elapsed:", summary["totalSeconds"], "seconds", flush=True)


def execute(args, engine, results, summary, checkpoint):
    run = engine.run
    podman = "podman" in engine.command
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
    test_image = engine.image_id(test_image)
    if not args.journey_only:
        check_suite(engine, test_image)
    summary["testImageId"] = test_image
    for _ in range(args.runs):
        sample = {
            "layout": args.layout,
            "runId": uuid.uuid4().hex[:10],
            "containers": [],
            "success": False,
        }
        summary["journeys"].append(sample)
        checkpoint()
        batch(engine, test_image, results, sample, args.scenarios)
        checkpoint()


def check_suite(engine, test_image):
    # No s6/downloader in logic checks; packet workers modify only their own namespaces.
    engine.worker(test_image, "/tests/checks.py", label="logic-syntax-and-lint")
    engine.worker(
        test_image,
        "/tests/test_firewall.py",
        network_checks=True,
        label="isolated-packets",
    )


def batch(engine, test_image, results, sample, count):
    started = time.monotonic()
    try:
        if sample["layout"] in {"shared", "ci"}:
            first = journey(
                engine,
                test_image,
                results,
                sample,
                count + (sample["layout"] == "ci"),
                list(range(count)),
                export_seed=sample["layout"] == "ci",
            )
            if sample["layout"] == "ci":
                journey(
                    engine,
                    test_image,
                    results,
                    sample,
                    count + 1,
                    [count],
                    seed=first["configuredSeed"],
                )
        else:
            first = journey(
                engine,
                test_image,
                results,
                sample,
                count,
                [0],
                export_seed=sample["layout"] == "cloned" and count > 1,
            )
            for index in range(1, count):
                journey(
                    engine,
                    test_image,
                    results,
                    sample,
                    count,
                    [index],
                    seed=first.get("configuredSeed"),
                )
        sample["success"] = True
    finally:
        sample["wallSeconds"] = round(time.monotonic() - started, 3)
        engine.progress()
    print(
        "BATCH",
        sample["layout"],
        count + (sample["layout"] == "ci"),
        "scenarios:",
        sample["wallSeconds"],
        "seconds",
        flush=True,
    )


def journey(
    engine, test_image, results, batch, count, selected, seed=None, export_seed=False
):
    run = engine.run
    run_id = batch["runId"]
    name = "pompey-journey-" + uuid.uuid4().hex[:10]
    destination = results / name
    destination.mkdir()
    started = time.monotonic()
    snapshot_seconds = restore_seconds = 0
    sample = {
        "artifacts": str(destination),
        "success": False,
        "selectedCases": selected,
        "testImageId": test_image,
    }
    batch["containers"].append(sample)
    engine.progress()
    report = None
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
        "POMPEY_TEST_CASES=" + str(count),
        "-e",
        "POMPEY_TEST_SELECTED_CASES=" + json.dumps(selected),
        "-e",
        "POMPEY_JOB_INTERVALS="
        + json.dumps({"configuration": 2, "routing": 2, "requests": 2, "downloads": 2}),
    ]
    flags += engine.namespace_security
    try:
        if seed:
            run(["create", *flags, "-e", "POMPEY_TEST_CONFIGURED=1", test_image], 60)
            restore_seconds = engine.restore(name, seed)
            run(
                ["start", "--attach", name],
                STARTUP_TIMEOUT + SCENARIO_TIMEOUT * len(selected) + 180,
                log=destination / "container.log",
            )
        else:
            run(
                ["run", *flags, test_image],
                STARTUP_TIMEOUT + SCENARIO_TIMEOUT * len(selected) + 180,
                log=destination / "container.log",
            )
        report = json.loads((destination / "result.json").read_text())
        assert report["success"], report
        engine.assert_clean_stop(destination)
        if export_seed:
            seed, snapshot_seconds = engine.snapshot(name, destination)
            sample["configuredSeed"] = seed
        sample["success"] = True
    except BaseException as exc:
        if (destination / "container.log").exists():
            try:
                engine.assert_clean_stop(destination)
            except Exception as stop:
                sample["shutdownError"] = str(stop)
        sample["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        try:
            engine.remove(name)
        except Exception as cleanup:
            sample["cleanupError"] = str(cleanup)
            if sample["success"]:
                sample["success"] = False
                raise
        finally:
            sample["wallSeconds"] = round(time.monotonic() - started, 3)
            sample["snapshotExportSeconds"] = snapshot_seconds
            sample["snapshotRestoreSeconds"] = restore_seconds
            partial = destination / "result.json"
            if partial.exists():
                try:
                    report = json.loads(partial.read_text())
                    sample["cases"] = report.get("cases", [])
                except ValueError:
                    sample["resultError"] = "Container result is not valid JSON"
            save_json(destination / "timing.json", sample)
            engine.progress()
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
    sample.update(
        startupSeconds=report["timings"]["household-setup-complete"],
        lifecycleOverheadSeconds=overhead,
    )
    save_json(destination / "timing.json", sample)
    engine.progress()
    return sample


if __name__ == "__main__":
    main()
