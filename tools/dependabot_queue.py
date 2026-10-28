"""Enable protected Dependabot merges and let Dependabot refresh stale branches."""

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
    # Enable before requesting a rebase, which may advance the head immediately.
    subprocess.run(["gh", "pr", "merge", str(number), "--repo", REPO, "--auto", "--squash",
                    "--match-head-commit", head], check=True)
    if comparison["behind_by"]:
        marker = "<!-- pompey-dependabot-rebase:" + base + " -->"
        comments = api(ROOT + f"/issues/{number}/comments?per_page=100")
        if not any(comment.get("user", {}).get("login") == "github-actions[bot]" and marker in comment.get("body", "") for comment in comments):
            if len(comments) == 100:
                print(f"#{number}: comment history exceeds maintenance limit; review manually")
                return
            # A GITHUB_TOKEN branch update would suppress push-triggered CI.
            # Dependabot performs this rebase as its own app, triggering normal
            # PR acceptance. The marker coalesces requests for the same main.
            api(ROOT + f"/issues/{number}/comments", "POST", {"body": "@dependabot rebase\n\n" + marker})
    print(f"#{number}: protected auto-merge requested")


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPO or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Dependabot maintenance runs only in the trusted repository workflow")
    required_protection(REPO)
    prs = api(ROOT + "/pulls?state=open&base=main&sort=created&direction=asc&per_page=100")
    for pr in [pr for pr in prs if eligible(pr)][:10]:
        process(pr["number"])


if __name__ == "__main__":
    main()
