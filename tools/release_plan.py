"""Resolve an immutable, current PR merge candidate from trusted main."""

import base64
import json
import os
import re
import time
from dependency_updates import api
from release_protection import required_protection

REPO = "esper256/ha-pompey"
BASE = "repos/" + REPO


def stable_version(text):
    match = re.search(r'^version: "(\d+\.\d+\.\d+)"$', text, re.M)
    if not match:
        raise ValueError("Release candidates must declare a stable Pompey version")
    return match[1]


def contents(path, revision):
    return base64.b64decode(api(BASE + "/contents/" + path + "?ref=" + revision)["content"]).decode()


def validate_candidate(pr, main, merge, version, previous):
    if (pr["state"] != "open" or pr["head"]["repo"]["full_name"] != REPO
            or pr["base"]["ref"] != "main" or pr["base"]["sha"] != main):
        raise ValueError("Release requires an open, current, same-repository PR into main")
    parents = [parent["sha"] for parent in merge["parents"]]
    if parents != [main, pr["head"]["sha"]]:
        raise ValueError("Merge candidate does not match the current base and PR head")
    if tuple(map(int, version.split("."))) <= tuple(map(int, previous.split("."))):
        raise ValueError("Release version must advance current main")


def hold(pr):
    if not pr["draft"]:
        api("graphql", "POST", {"query": "mutation($id:ID!){convertPullRequestToDraft(input:{pullRequestId:$id}){pullRequest{id}}}",
                               "variables": {"id": pr["node_id"]}})


def main():
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("Release orchestration must run from trusted main")
    bot, manual = os.environ.get("CANDIDATE_PR", ""), os.environ.get("RELEASE_PR", "")
    if bool(bot) == bool(manual) or not re.fullmatch(r"[1-9][0-9]*", bot or manual):
        raise ValueError("Choose exactly one dependency candidate or release PR")
    # Catch an unreadable/missing gate before spending time building two images.
    required_protection(REPO)
    number = int(bot or manual)
    pr = api(BASE + "/pulls/" + str(number))
    main_sha = api(BASE + "/git/ref/heads/main")["object"]["sha"]
    if main_sha != os.environ["GITHUB_SHA"]:
        raise ValueError("Main advanced before release planning; dispatch against current main")
    if bot and (pr["user"]["login"] != "github-actions[bot]" or not pr["head"]["ref"].startswith("codex/upstream-")):
        raise ValueError("Dependency release requires an Actions-owned update PR")
    # GitHub can briefly be calculating the synthetic merge commit after a push.
    for attempt in range(3):
        if pr.get("mergeable") is not None:
            break
        time.sleep(2)
        pr = api(BASE + "/pulls/" + str(number))
    if not pr.get("mergeable"):
        raise ValueError("Release PR cannot be merged cleanly")
    merge_sha = api(BASE + "/git/ref/pull/" + str(number) + "/merge")["object"]["sha"]
    merge = api(BASE + "/git/commits/" + merge_sha)
    version = stable_version(contents("pompey/config.yaml", merge_sha))
    previous = stable_version(contents("pompey/config.yaml", main_sha))
    validate_candidate(pr, main_sha, merge, version, previous)
    hold(pr)
    plan = {"pr": str(number), "kind": "dependency" if bot else "release", "base": main_sha,
            "head": pr["head"]["sha"], "revision": merge_sha, "tree": merge["tree"]["sha"],
            "version": version, "attempt": str(os.environ["GITHUB_RUN_ATTEMPT"])}
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in plan.items():
            output.write(key + "=" + value + "\n")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
