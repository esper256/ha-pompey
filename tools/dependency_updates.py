"""Create independently testable pinned-bundle PRs; never modify a live installation."""

import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode
from build_engine_manifest import build
from release_metadata import current, next_patch, set_version, release_notes

MANIFEST = "pompey/rootfs/usr/share/pompey/engines.json"
COMPONENTS = ["Radarr", "Sonarr", "Prowlarr", "seerr", "qbittorrent-nox", "recyclarr", "dotnet", "quality-resources"]


def command(*args):
    return subprocess.check_output(list(args), text=True).strip()


def api(path, method="GET", body=None):
    args = ["gh", "api", path, "-X", method]
    if body is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        # Keep the actionable HTTP/permission error, without logging token or body.
        raise RuntimeError("GitHub API " + method + " " + path + ": " + result.stderr.strip()[:500])
    return json.loads(result.stdout) if result.stdout.strip() else None


def release_number(run):
    import re
    match = re.fullmatch(r"Release PR #(\d+)", run.get("display_title", ""))
    return int(match[1]) if match else None


def queue_decision(runs, number=None):
    """One active release; unchanged failed candidates are never retried away."""
    active = [run for run in runs if release_number(run) and run["status"] != "completed"]
    if active:
        return "busy"
    previous = next((run for run in runs if release_number(run) == number), None) if number else None
    return "attempted" if previous else "dispatch"


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Publishing update PRs is restricted to the repository workflow")
    repo = os.environ["GITHUB_REPOSITORY"]
    if repo != "esper256/ha-pompey":
        raise RuntimeError("Dependency publication is not enabled for this repository")
    if command("git", "status", "--porcelain"):
        raise RuntimeError("Update discovery requires a clean checkout")
    version = current()
    if "-" in version:
        print("Discovery/promotion waits until the development version has a stable release.")
        return
    command("git", "fetch", "origin", "main")
    base = command("git", "rev-parse", "origin/main")
    summary = []
    failures = []
    runs = api("repos/" + repo + "/actions/workflows/test.yaml/runs?event=workflow_dispatch&per_page=100")["workflow_runs"]
    if queue_decision(runs) == "busy":
        print("A release is already in flight; dependency queue is unchanged.")
        return
    for component in COMPONENTS:
        branch = "codex/upstream-" + component.lower()
        try:
            command("git", "switch", "-C", branch, "origin/main")
            original = json.loads(Path(MANIFEST).read_text())
            resolved = build(component)
            candidate = json.loads(json.dumps(original))
            candidate["engines"].update(resolved["engines"])
            if "resources" in resolved:
                candidate["resources"] = resolved["resources"]
            if candidate == original:
                summary.append(component + ": current")
                continue
            query = urlencode({"head": repo.split("/")[0] + ":" + branch, "state": "open"})
            existing = api("repos/" + repo + "/pulls?" + query)
            if existing:
                pr = existing[0]
                if pr["user"]["login"] != "github-actions[bot]":
                    raise RuntimeError("Existing update branch is not owned by the Actions bot")
                remote = api("repos/" + repo + "/contents/" + MANIFEST + "?" + urlencode({"ref": branch}))
                import base64
                old_candidate = json.loads(base64.b64decode(remote["content"]))
                comparison = api("repos/" + repo + "/compare/main..." + pr["head"]["sha"])
                if candidate == old_candidate and comparison["behind_by"] == 0:
                    if queue_decision(runs, pr["number"]) == "dispatch":
                        api("repos/" + repo + "/actions/workflows/test.yaml/dispatches", "POST", {"ref": "main", "inputs": {"candidate_pr": str(pr["number"])}})
                        summary.append(component + ": dispatched existing candidate " + pr["html_url"])
                        break
                    summary.append(component + ": candidate unchanged (prior acceptance retained; no retry)")
                    continue
            lease = command("git", "ls-remote", "origin", "refs/heads/" + branch).split()
            expected = lease[0] if lease else ""
            Path(MANIFEST).write_text(json.dumps(candidate, indent=2) + "\n")
            version = next_patch(current())
            set_version(version)
            old = original["resources"] if component == "quality-resources" else original["engines"][component]
            new = candidate["resources"] if component == "quality-resources" else candidate["engines"][component]
            def describe(value):
                return value["version"] if "version" in value else ", ".join(name + " " + commit[:12] for name, commit in value.items())
            notes = f"## {version}\n\n- Update {component}: {describe(old)} → {describe(new)}.\n\n"
            changelog = Path("pompey/CHANGELOG.md")
            addition = f"- Update {component}: {describe(old)} → {describe(new)}."
            changelog.write_text(release_notes(changelog.read_text(), version, addition))
            command("git", "add", MANIFEST, "pompey/config.yaml", "pompey/Dockerfile", "pompey/CHANGELOG.md")
            command("git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-m", "Update pinned " + component + " for Pompey " + version)
            command("git", "push", "--force-with-lease=refs/heads/" + branch + ":" + expected, "origin", "HEAD:refs/heads/" + branch)
            body = f"Update the pinned {component} bundle and publish Pompey {version} after required release and forward-upgrade acceptance.\n\nBase: `{base}`. Existing installs change only through Home Assistant updates. Failed candidates retain their evidence and do not replace released pins.\n\n{notes}"
            if existing:
                pr = api("repos/" + repo + "/pulls/" + str(pr["number"]), "PATCH", {"title": "Update pinned " + component + " for Pompey " + version, "body": body})
            else:
                pr = api("repos/" + repo + "/pulls", "POST", {"base": "main", "head": branch, "title": "Update pinned " + component + " for Pompey " + version, "body": body, "draft": True})
            # Publish one candidate at a time. After publication the next
            # discovery starts from the new main/version instead of wasting
            # acceptance on several immediately stale parallel candidates.
            api("repos/" + repo + "/actions/workflows/test.yaml/dispatches", "POST", {"ref": "main", "inputs": {"candidate_pr": str(pr["number"])}})
            summary.append(component + ": " + pr["html_url"])
            break
        except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
            summary.append(component + ": discovery failed (" + type(exc).__name__ + "): " + str(exc)[:240])
            failures.append(component)
            # Continue resolving unrelated components; never retry a failed CI.
            command("git", "reset", "--hard", "origin/main")
    output = "\n".join(summary)
    print(output)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as target:
            target.write("## Dependency maintenance\n\n" + "\n".join("- " + line for line in summary) + "\n")
    if failures:
        raise RuntimeError("Dependency discovery failed for: " + ", ".join(failures))


if __name__ == "__main__":
    main()
