"""Enable protected Dependabot merges; report stale branches requiring maintainer action."""

import os
import subprocess

from dependency_updates import api
from release_protection import required_protection

REPO = "esper256/ha-pompey"
ROOT = "repos/" + REPO


def eligible(pr):
    return (pr.get("state") == "open" and not pr.get("draft")
            and pr.get("user", {}).get("login") == "dependabot[bot]"
            and pr.get("base", {}).get("ref") == "main"
            and pr.get("head", {}).get("repo", {}).get("full_name") == REPO
            and pr.get("head", {}).get("ref", "").startswith(("dependabot/docker/", "dependabot/github_actions/")))


def failed_gate(checks):
    return any(check.get("name") == "CI required" and check.get("status") == "completed"
               and check.get("conclusion") != "success" for check in checks)


def process(number):
    pr = api(ROOT + "/pulls/" + str(number))
    if not eligible(pr):
        return
    head = pr["head"]["sha"]
    checks = api(ROOT + "/commits/" + head + "/check-runs?filter=latest&per_page=100")["check_runs"]
    if failed_gate(checks):
        print(f"#{number}: required CI failed; retained for review")
        return
    base = api(ROOT + "/git/ref/heads/main")["object"]["sha"]
    comparison = api(ROOT + "/compare/" + base + "..." + head)
    if comparison["behind_by"]:
        # GITHUB_TOKEN cannot issue Dependabot commands as a repository writer.
        # Updating the branch with that token would also suppress push-triggered
        # acceptance. Do not post a command we cannot execute or deduplicate a
        # rejected request forever. A maintainer's command lets Dependabot do
        # the update as its own app, preserving normal PR acceptance.
        raise RuntimeError(
            f"#{number}: branch is behind main; a maintainer with push access must "
            "comment '@dependabot rebase' on the PR, then rerun maintenance after "
            "Dependabot updates the branch. No automated rebase was requested.")
    subprocess.run(["gh", "pr", "merge", str(number), "--repo", REPO, "--auto", "--squash",
                    "--match-head-commit", head], check=True)
    print(f"#{number}: protected auto-merge requested")


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPO or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Dependabot maintenance runs only in the trusted repository workflow")
    required_protection(REPO)
    prs = api(ROOT + "/pulls?state=open&base=main&sort=created&direction=asc&per_page=100")
    failures = []
    for pr in [pr for pr in prs if eligible(pr)][:10]:
        try:
            process(pr["number"])
        except (RuntimeError, subprocess.CalledProcessError) as error:
            failures.append(str(error))
            print(f"::error::{error}")
    if failures:
        raise RuntimeError(f"Dependabot maintenance requires attention for {len(failures)} PR(s)")


if __name__ == "__main__":
    main()
