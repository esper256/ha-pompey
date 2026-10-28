"""Create one weekly pinned-dependency bundle PR; never modify a live installation."""

import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode
from build_engine_manifest import build
from release_metadata import current, next_patch, set_version, release_notes

MANIFEST = "pompey/rootfs/usr/share/pompey/engines.json"
BRANCH = "codex/upstream-bundle"


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
    runs = api("repos/" + repo + "/actions/workflows/test.yaml/runs?event=workflow_dispatch&per_page=100")["workflow_runs"]
    if queue_decision(runs) == "busy":
        print("A release is already in flight; dependency queue is unchanged.")
        return
    command("git", "switch", "-C", BRANCH, "origin/main")
    original = json.loads(Path(MANIFEST).read_text())
    # Resolve all components before publishing anything. A discovery failure
    # leaves the existing bundle untouched rather than publishing a partial set.
    candidate = build("all")
    query = urlencode({"head": repo.split("/")[0] + ":" + BRANCH, "state": "open"})
    existing = api("repos/" + repo + "/pulls?" + query)
    pr = existing[0] if existing else None
    if pr and pr["user"]["login"] != "github-actions[bot]":
        raise RuntimeError("Existing update branch is not owned by the Actions bot")
    if candidate == original:
        if pr:
            api("repos/" + repo + "/pulls/" + str(pr["number"]), "PATCH", {"state": "closed"})
        print("All bundled dependencies are current; no build or release needed.")
        return
    if pr:
        remote = api("repos/" + repo + "/contents/" + MANIFEST + "?" + urlencode({"ref": BRANCH}))
        import base64
        old_candidate = json.loads(base64.b64decode(remote["content"]))
        comparison = api("repos/" + repo + "/compare/main..." + pr["head"]["sha"])
        if candidate == old_candidate and comparison["behind_by"] == 0:
            if queue_decision(runs, pr["number"]) == "dispatch":
                api("repos/" + repo + "/actions/workflows/test.yaml/dispatches", "POST", {"ref": "main", "inputs": {"candidate_pr": str(pr["number"])}})
            else:
                print("Bundle unchanged; prior acceptance evidence retained without automatic retry.")
            return
    lease = command("git", "ls-remote", "origin", "refs/heads/" + BRANCH).split()
    expected = lease[0] if lease else ""
    Path(MANIFEST).write_text(json.dumps(candidate, indent=2) + "\n")
    version = next_patch(current())
    set_version(version)
    notes = bundle_notes(original, candidate)
    changelog = Path("pompey/CHANGELOG.md")
    changelog.write_text(release_notes(changelog.read_text(), version, notes))
    command("git", "add", MANIFEST, "pompey/config.yaml", "pompey/Dockerfile", "pompey/CHANGELOG.md")
    title = "Update pinned dependencies for Pompey " + version
    command("git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-m", title)
    command("git", "push", "--force-with-lease=refs/heads/" + BRANCH + ":" + expected, "origin", "HEAD:refs/heads/" + BRANCH)
    body = (f"Update the pinned dependency bundle and publish Pompey {version} after full release and forward-upgrade acceptance.\n\n"
            f"Base: `{base}`. Existing installs change only through Home Assistant updates. Failed candidates retain their evidence.\n\n" + notes + "\n")
    if pr:
        pr = api("repos/" + repo + "/pulls/" + str(pr["number"]), "PATCH", {"title": title, "body": body})
    else:
        pr = api("repos/" + repo + "/pulls", "POST", {"base": "main", "head": BRANCH, "title": title, "body": body, "draft": True})
    api("repos/" + repo + "/actions/workflows/test.yaml/dispatches", "POST", {"ref": "main", "inputs": {"candidate_pr": str(pr["number"])}})
    print("Dependency bundle: " + pr["html_url"])
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as target:
            target.write("## Dependency maintenance\n\n" + pr["html_url"] + "\n\n" + notes + "\n")


def bundle_notes(before, after):
    def describe(value):
        return value["version"] if "version" in value else ", ".join(name + " " + commit[:12] for name, commit in value.items())
    changes = [(name, old, after["engines"][name]) for name, old in before["engines"].items()
               if old != after["engines"][name]]
    if before["resources"] != after["resources"]:
        changes.append(("quality-resources", before["resources"], after["resources"]))
    return "\n".join(f"- Update {name}: {describe(old)} → {describe(new)}." for name, old, new in changes)


if __name__ == "__main__":
    main()
