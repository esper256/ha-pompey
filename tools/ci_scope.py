"""Select development, PR or release checks and enforce their merge gate."""

import os
import json
from pathlib import PurePosixPath
import subprocess
import sys


def needs_code_checks(paths):
    def documentation(path):
        parts = PurePosixPath(path).parts
        if parts[0] == "docs" and path.endswith((".md", ".html", ".jpg", ".png", ".svg")):
            return True
        return path.endswith(".md") and (
            len(parts) == 1
            or parts[0] == "docs"
            or path in {"pompey/DOCS.md", "pompey/CHANGELOG.md"}
        )

    return any(not documentation(path) for path in paths)


def integration_suite(event, code, draft=False):
    if event not in {"workflow_dispatch", "schedule", "pull_request", "push"}:
        raise ValueError("Unknown CI event")
    if event in {"workflow_dispatch", "schedule"}:
        return "release"
    if event == "pull_request" and code and not draft:
        return "pr"
    return "none"


def selection(event, code, draft=False):
    draft = event == "pull_request" and draft
    return {"code": str(code).lower(), "suite": integration_suite(event, code, draft),
            "draft": str(draft).lower(), "context": "Draft checks" if draft else "CI required"}


def require_results(jobs):
    if jobs["changes"]["result"] != "success":
        raise ValueError("Check selection failed")
    outputs = jobs["changes"]["outputs"]
    code, suite, draft = outputs["code"], outputs["suite"], outputs["draft"]
    if code not in {"true", "false"} or suite not in {"none", "pr", "release"} or draft not in {"true", "false"}:
        raise ValueError("Invalid check selection")
    if suite != "none" and code != "true":
        raise ValueError("Integration selection requires code checks")
    expected_context = "Draft checks" if draft == "true" else "Release acceptance" if outputs["context"] == "Release acceptance" and suite == "release" else "CI required"
    if outputs["context"] != expected_context or (draft == "true" and suite != "none"):
        raise ValueError("Draft work cannot report successful submission acceptance")
    expected = {"fast": "success" if code == "true" else "skipped",
                "acceptance": "success" if suite != "none" else "skipped"}
    for name, result in expected.items():
        if jobs[name]["result"] != result:
            raise ValueError(name + " must be " + result)


def main():
    if sys.argv[1:] == ["--require"]:
        require_results(json.loads(os.environ["NEEDS"]))
        return
    base = os.environ.get("BASE", "")
    event = os.environ["EVENT"]
    draft = event == "pull_request" and os.environ.get("DRAFT") == "true"
    # Schedules/manual runs always exercise acceptance. Unknown bases do too.
    code = True
    if event in {"pull_request", "push"} and base and set(base) != {"0"}:
        revision = (
            f"{base}...{os.environ['HEAD']}"
            if event == "pull_request"
            else f"{base}..{os.environ['HEAD']}"
        )
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", revision],
            capture_output=True,
            check=True,
        )
        paths = [p for p in result.stdout.decode().split("\0") if p]
        code = needs_code_checks(paths)
    values = selection(event, code, draft)
    if os.environ.get("RELEASE_REVISION"):
        values.update(context="Release acceptance", revision=os.environ["RELEASE_REVISION"])
    else:
        values["revision"] = os.environ.get("HEAD", "")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
