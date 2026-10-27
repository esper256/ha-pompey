#!/usr/bin/env python3
"""Build once and run logic, required isolated packets and real integration batches."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
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
    parser.add_argument("--upgrade-from", help="Also run a real cold forward upgrade from this released product image")
    parser.add_argument("--image", default="localhost/pompey:foundation")
    parser.add_argument(
        "--integration-only",
        "--journey-only",
        dest="journey_only",
        action="store_true",
        help="Focused integration timing; does not run logic or packet checks",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Independent batches for timing; any failure stops the run",
    )
    parser.add_argument(
        "--scenarios",
        type=int,
        default=15,
        help="Distinct integration scenarios per batch",
    )
    parser.add_argument(
        "--layout",
        choices=["shared", "cloned", "fresh", "ci"],
        default="shared",
        help="Reuse a running application stack, clone cold configured state, or start fresh per scenario",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--only", nargs="+", help="Focused shared/CI-layout scenario names"
    )
    selection.add_argument(
        "--exclude", nargs="+", help="Exclude named shared/CI-layout scenarios"
    )
    parser.add_argument("--suite", choices=["pr", "release"], default="release",
                        help="Fast representative PR checks or complete release acceptance")
    args = parser.parse_args()
    if args.suite == "pr" and not (args.only or args.exclude):
        args.only = ["movie-pg", "single-episode", "executable-payload"]
    if (args.only or args.exclude) and args.layout not in {"shared", "ci"}:
        parser.error("--only/--exclude requires --layout shared or ci")
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
                "BUILD_VERSION=" + subprocess.check_output([sys.executable, str(ROOT / "tools/release_metadata.py"), "current"], text=True).strip(),
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
    base_test_image = None
    if args.upgrade_from:
        base_tag = args.image + "-upgrade-base-tests"
        run(build + ["--build-arg", "POMPEY_IMAGE=" + args.upgrade_from,
                     "-f", "tests/journey/Dockerfile", "-t", base_tag, "."],
            600, label="upgrade-base-test-image-build")
        base_test_image = engine.image_id(base_tag)
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
        if args.only or args.exclude:
            from journey.catalogue import Case

            names = {Case(i, "", "").scenario: i for i in range(args.scenarios)}
            missing = set(args.only or args.exclude) - names.keys()
            if missing:
                raise ValueError("Unknown scenario names: " + ", ".join(sorted(missing)))
            sample["selectedCases"] = (
                sorted(names[name] for name in args.only)
                if args.only
                else [
                    i
                    for i in range(args.scenarios)
                    if Case(i, "", "").scenario not in args.exclude
                ]
            )
        summary["journeys"].append(sample)
        checkpoint()
        batch(engine, test_image, results, sample, args.scenarios)
        if base_test_image:
            upgrade = {"layout": "forward-upgrade", "runId": uuid.uuid4().hex[:10], "containers": [], "success": False}
            summary["journeys"].append(upgrade)
            base = journey(engine, base_test_image, results, upgrade, args.scenarios, [0, 4, 9], export_seed=True, handoff=True, baseline=True)
            journey(engine, test_image, results, upgrade, args.scenarios, [4, 9], seed=base["configuredSeed"], resume_evidence=Path(base["artifacts"]) / "result.json")
            upgrade["success"] = True
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
                sample.get("selectedCases", list(range(count))),
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
        sum(len(c.get("cases", [])) for c in sample["containers"]),
        "scenarios:",
        sample["wallSeconds"],
        "seconds",
        flush=True,
    )


def journey(
    engine, test_image, results, batch, count, selected, seed=None, export_seed=False,
    handoff=False, baseline=False, resume_evidence=None
):
    run = engine.run
    run_id = batch["runId"]
    name = "pompey-journey-" + uuid.uuid4().hex[:10]
    destination = results / name
    destination.mkdir()
    if resume_evidence:
        shutil.copy2(resume_evidence, destination / "boot1-result.json")
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
        "POMPEY_EVENT_LOG=/tmp/pompey/invariant-events.jsonl",
        "-e",
        "POMPEY_TEST_CASES=" + str(count),
        "-e",
        "POMPEY_TEST_SELECTED_CASES=" + json.dumps(selected),
        "-e",
        "POMPEY_JOB_INTERVALS="
        + json.dumps({"configuration": 2, "routing": 2, "requests": 2, "downloads": 2}),
    ]
    flags += engine.namespace_security
    if baseline:
        flags += ["-e", "POMPEY_TEST_BASELINE=1"]
    if 14 in selected and not seed:
        flags += ["-e", "POMPEY_TEST_COLD_OFFLINE=1"]
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
        if report.get("restartRequired") and not handoff:
            assert report.get("phaseComplete") and not report.get("error"), report
            engine.assert_clean_stop(destination)
            for filename in [
                "container.log",
                "external-requests.json",
                "unexpected-requests.json",
                "result.json",
            ]:
                (destination / filename).rename(destination / ("boot1-" + filename))
            sample["containerRestarts"] = 1
            run(
                ["start", "--attach", name],
                STARTUP_TIMEOUT
                + SCENARIO_TIMEOUT
                * (
                    sum(c["status"] in {"waiting", "paused"} for c in report["cases"])
                    + len(selected)
                    - len(report["cases"])
                )
                + 180,
                log=destination / "container.log",
            )
            report = json.loads((destination / "result.json").read_text())
            assert not report.get("restartRequired"), (
                "Unexpected second restart request"
            )
        assert report["success"] or (handoff and report.get("phaseComplete") and report.get("restartRequired")), report
        if handoff:
            sample["phase"] = "upgrade-seed"
        engine.assert_clean_stop(destination)
        assert "[qBittorrent]" in (destination / "container.log").read_text(), (
            "qBittorrent's file log was not forwarded to Home Assistant output"
        )
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
    startup = (report["currentBoot"]["startupSeconds"] if resume_evidence
               else report["timings"]["application-setup-complete"])
    print(
        "Startup:",
        startup,
        "seconds",
        flush=True,
    )
    for case in report["cases"]:
        print(
            "CASE",
            case["prefix"],
            "certification",
            repr(case["certification"]),
            "seconds",
            case.get("durationSeconds", "pending handoff"),
            "prior titles preserved",
            case.get("priorTitlesVerified", 0),
            flush=True,
        )
        previous = 0
        for stage, cumulative in case["timings"].items():
            print(f"  {stage:30} {cumulative - previous:7.3f}", flush=True)
            previous = cumulative
    # Cross-image evidence contains both boots' cumulative case durations. Only
    # this candidate boot belongs to this host container's lifecycle cost.
    work_seconds = (report["currentBoot"]["elapsedSeconds"] if resume_evidence or handoff
                    else report["timings"]["batch-complete"])
    overhead = round(
        elapsed - work_seconds - snapshot_seconds - restore_seconds,
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
        startupSeconds=startup,
        lifecycleOverheadSeconds=overhead,
    )
    save_json(destination / "timing.json", sample)
    engine.progress()
    return sample


if __name__ == "__main__":
    main()
