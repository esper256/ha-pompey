"""Host-side container commands, immutable image identity and cold state transfer."""

from contextlib import ExitStack
from pathlib import Path
import shlex
import subprocess
import time
import uuid
from journey.support import tail


class Runtime:
    def __init__(self, command, root, progress=lambda: None):
        self.command = command
        self.root = root
        self.progress = progress
        self.measurements = {}

    def run(
        self,
        arguments,
        timeout=600,
        log=None,
        label=None,
        capture=False,
        input_file=None,
        output_file=None,
    ):
        started = time.monotonic()
        print("+", shlex.join(self.command + arguments), flush=True)
        try:
            with ExitStack() as files:
                options = {"cwd": self.root, "check": True, "timeout": timeout}
                if input_file:
                    options["stdin"] = files.enter_context(Path(input_file).open("rb"))
                if output_file:
                    options["stdout"] = files.enter_context(
                        Path(output_file).open("wb")
                    )
                elif log:
                    options["stdout"] = files.enter_context(Path(log).open("wb"))
                    options["stderr"] = subprocess.STDOUT
                elif capture:
                    options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                result = subprocess.run(self.command + arguments, **options)
            return result.stdout.decode().strip() if capture else result
        except BaseException:
            if log and Path(log).exists():
                print(tail(log), flush=True)
            raise
        finally:
            if label:
                self.measurements[label] = round(time.monotonic() - started, 3)
                print(
                    "DURATION", label, self.measurements[label], "seconds", flush=True
                )
            self.progress()

    def worker(self, image, script, timeout=120, privileged=False, label=None):
        name = "pompey-check-" + uuid.uuid4().hex[:10]
        flags = [
            "--name",
            name,
            "--rm",
            "--network",
            "none",
            "--security-opt",
            "label=disable",
        ]
        if privileged:
            flags += ["--privileged", "-e", "POMPEY_REQUIRE_NETWORK_TESTS=1"]
        failed = False
        try:
            return self.run(
                ["run", *flags, "--entrypoint", "python3", image, script, "-v"],
                timeout,
                label=label,
            )
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self.remove(name)
            except Exception as exc:
                if not failed:
                    raise
                print("Worker cleanup also failed:", exc, flush=True)

    def image_id(self, image):
        return self.run(
            ["image", "inspect", "--format", "{{.Id}}", image], capture=True
        )

    def remove(self, name):
        result = subprocess.run(
            self.command + ["rm", "--force", name], capture_output=True, timeout=30
        )
        detail = result.stderr.decode(errors="replace")
        if result.returncode and not any(
            x in detail.lower() for x in ["no such container", "does not exist"]
        ):
            raise RuntimeError("Container cleanup failed: " + detail)

    def restore(self, name, seed):
        before = time.monotonic()
        for folder in ["data", "config", "media"]:
            self.run(
                ["cp", "-", name + ":/"], 60, input_file=Path(seed) / (folder + ".tar")
            )
        return round(time.monotonic() - before, 3)

    def snapshot(self, name, destination):
        # Called only after PID 1 exits. Reject nonzero exits, fatal signals and
        # service shutdown timeouts; none is a trustworthy configured seed.
        log = (destination / "container.log").read_text()
        if "s6-svwait: fatal: timed out" in log or any(
            app + suffix in log
            for app in ["Radarr", "Sonarr", "Prowlarr", "Seerr", "qBittorrent"]
            for suffix in [" exited (", " killed ("]
        ):
            raise RuntimeError(
                "Refusing configured state after an unclean engine shutdown"
            )
        seed = destination / "configured-state"
        seed.mkdir()
        before = time.monotonic()
        for folder in ["data", "config", "media"]:
            self.run(
                ["cp", name + ":/" + folder, "-"],
                60,
                output_file=seed / (folder + ".tar"),
            )
        return str(seed), round(time.monotonic() - before, 3)
