"""Promote only an exact, current, protected and fully tested bot candidate."""

import os
from pathlib import Path
import re
import subprocess
import base64
import copy
import json
import time
from dependency_updates import api
from release_metadata import current, next_patch, split_unreleased
from release_protection import required_protection

ALLOWED = {"pompey/rootfs/usr/share/pompey/engines.json", "pompey/config.yaml", "pompey/Dockerfile", "pompey/CHANGELOG.md"}
MANIFEST = "pompey/rootfs/usr/share/pompey/engines.json"


def validate_acceptance(run, jobs, sha, attempt, context="CI required"):
    """Only successful release acceptance for this exact workflow run may publish."""
    if (run.get("event") != "workflow_dispatch" or run.get("head_sha") != sha
            or run.get("path") != ".github/workflows/test.yaml"
            or run.get("run_attempt") != attempt):
        raise ValueError("Release acceptance must match the exact candidate workflow attempt")
    for name in ["Application integration and network isolation", context]:
        matches = [job for job in jobs if job.get("name") == name]
        if (len(matches) != 1 or matches[0].get("status") != "completed"
                or matches[0].get("conclusion") != "success"):
            raise ValueError("Required automated acceptance did not pass: " + name)


def validate_contents(component, before, after, version):
    """An allowed filename is not permission to change unrelated HA settings."""
    previous = re.search(r'^version: "([^"]+)"$', before["pompey/config.yaml"], re.M)
    if not previous or version != next_patch(previous[1]):
        raise ValueError("Candidate must increment the previous stable patch version")
    expected_config = before["pompey/config.yaml"].replace('version: "' + previous[1] + '"', 'version: "' + version + '"', 1)
    if after["pompey/config.yaml"] != expected_config:
        raise ValueError("Candidate changed Home Assistant settings beyond the version")
    expected_docker = re.sub(r'^ARG BUILD_VERSION="[^"]+"$', 'ARG BUILD_VERSION="' + version + '"', before["pompey/Dockerfile"], flags=re.M)
    if after["pompey/Dockerfile"] != expected_docker:
        raise ValueError("Candidate changed the Dockerfile beyond the version")
    old, new = json.loads(before[MANIFEST]), json.loads(after[MANIFEST])
    if component == "bundle":
        if set(new["engines"]) != set(old["engines"]):
            raise ValueError("Bundle changed the supported application set")
        expected = copy.deepcopy(old)
        expected.update(engines=new["engines"], resources=new["resources"])
        if new != expected or new == old:
            raise ValueError("Bundle changed unrelated metadata or contains no updates")
        # Reuse each component's exact source, platform and digest restrictions.
        # A bundle does not widen the release bot's permission to modify files.
        for name in [*old["engines"], "quality-resources"]:
            single = copy.deepcopy(old)
            if name == "quality-resources":
                single["resources"] = new["resources"]
            else:
                single["engines"][name] = new["engines"][name]
            if single != old:
                validate_contents(name.lower(), before, {**after, MANIFEST: json.dumps(single)}, version)
        return
    expected = copy.deepcopy(old)
    if component == "quality-resources":
        if set(new["resources"]) != set(old["resources"]) or not all(re.fullmatch(r"[0-9a-f]{40}", commit) for commit in new["resources"].values()):
            raise ValueError("Invalid resource pins")
        expected["resources"] = new["resources"]
    else:
        names = {name.lower(): name for name in old["engines"]}
        if component not in names:
            raise ValueError("Unknown dependency component")
        name = names[component]
        entry = new["engines"][name]
        if not isinstance(entry.get("version"), str) or not entry["version"]:
            raise ValueError("Missing dependency version")
        if name == "seerr":
            if set(entry) != {"version", "image"} or not re.fullmatch(r"ghcr.io/seerr-team/seerr@sha256:[0-9a-f]{64}", entry["image"]):
                raise ValueError("Invalid Seerr image pin")
        else:
            hosts = {"Radarr": "https://github.com/Radarr/Radarr/releases/download/", "Sonarr": "https://github.com/Sonarr/Sonarr/releases/download/", "Prowlarr": "https://github.com/Prowlarr/Prowlarr/releases/download/", "recyclarr": "https://github.com/recyclarr/recyclarr/releases/download/", "qbittorrent-nox": "https://github.com/userdocs/qbittorrent-nox-static/releases/download/", "dotnet": "https://builds.dotnet.microsoft.com/dotnet/Runtime/"}
            if set(entry) != {"version", "artifacts"} or set(entry["artifacts"]) != {"linuxmusl-amd64"}:
                raise ValueError("Invalid runtime artifact platforms")
            artifact = entry["artifacts"]["linuxmusl-amd64"]
            algorithm, size = ("sha512", 128) if name == "dotnet" else ("sha256", 64)
            if set(artifact) != {"url", algorithm} or not artifact["url"].startswith(hosts[name]) or not re.fullmatch(r"[0-9a-f]{" + str(size) + "}", artifact[algorithm]):
                raise ValueError("Dependency artifact source or digest is invalid")
        expected["engines"][name] = entry
    if new == old or new != expected:
        raise ValueError("Candidate changed dependencies outside its component")
    header = "# Changelog\n\n"
    pending, history = split_unreleased(before["pompey/CHANGELOG.md"])
    after_notes = after["pompey/CHANGELOG.md"]
    prefix = header + "## " + version + "\n"
    if (not after_notes.startswith(prefix) or not history or not after_notes.endswith(history)
            or (pending and pending not in after_notes[len(prefix):-len(history)])):
        raise ValueError("Candidate rewrote or omitted existing release notes")


def validate(pr, files, sha, comparison, protection):
    if (pr["user"]["login"] != "github-actions[bot]" or pr["head"]["repo"]["full_name"] != "esper256/ha-pompey"
            or not pr["head"]["ref"].startswith("codex/upstream-") or pr["base"]["ref"] != "main"
            or pr["head"]["sha"] != sha or (pr["state"] != "open" and not pr.get("merged")) or pr["draft"]):
        raise ValueError("Only the exact open Actions-bot update PR may be promoted")
    names = {row["filename"] for row in files}
    if not names or not names <= ALLOWED or any(row["status"] != "modified" for row in files):
        raise ValueError("Candidate changes exceed pinned dependencies and release metadata")
    if comparison["behind_by"] and not pr.get("merged"):
        raise ValueError("Candidate must be regenerated against current main")
    checks = protection.get("checks", [])
    if not protection.get("strict") or not any(row["context"] == "CI required" and row.get("app_id") == 15368 for row in checks):
        raise ValueError("Strict required GitHub Actions CI protection must be enabled")


def validate_plan(plan, pr, main_sha, merge):
    if (plan["kind"] not in {"dependency", "release"} or pr["head"]["sha"] != plan["head"]
            or pr["head"]["repo"]["full_name"] != "esper256/ha-pompey" or pr["base"]["ref"] != "main"):
        raise ValueError("Release PR identity changed after acceptance")
    if not pr.get("merged"):
        if main_sha != plan["base"] or pr["state"] != "open":
            raise ValueError("Main or PR changed after acceptance; regenerate the candidate")
        if [p["sha"] for p in merge["parents"]] != [plan["base"], plan["head"]]:
            raise ValueError("Tested merge candidate no longer matches the PR")
    if merge["tree"]["sha"] != plan["tree"]:
        raise ValueError("Tested merge content changed")


def validate_check_target(plan, head):
    if head["sha"] != plan["head"] or head["tree"]["sha"] != plan["tree"]:
        raise ValueError("Required-check target must contain exactly the accepted release tree")


def await_merge_gate(base, number, plan):
    for attempt in range(8):
        pr = api(base + "/pulls/" + number)
        if pr["head"]["sha"] != plan["head"] or pr["base"]["sha"] != plan["base"]:
            raise ValueError("Release changed while GitHub was recognizing its required check")
        # "unstable" is mergeable: optional status/suite results need not pass.
        # The protected merge endpoint remains authoritative for required rules.
        if pr.get("mergeable_state") in {"clean", "unstable"}:
            return
        if attempt < 7:
            time.sleep(2)
    raise ValueError("GitHub has not recognized the accepted required check; publication withheld")


def main():
    repo = "esper256/ha-pompey"
    if os.environ.get("GITHUB_REPOSITORY") != repo or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publication must execute trusted main")
    plan = json.loads(os.environ["RELEASE_PLAN"])
    number = plan["pr"]
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Invalid release PR")
    base = "repos/" + repo
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    if int(plan["attempt"]) != attempt:
        raise ValueError("Rerun the complete release workflow to obtain current acceptance evidence")
    run = api(base + "/actions/runs/" + run_id)
    jobs = api(base + "/actions/runs/" + run_id + "/attempts/" + str(attempt) + "/jobs?per_page=100")["jobs"]
    # The workflow runs on trusted main; its read-only test jobs check out the
    # exact synthetic merge SHA resolved by the trusted planning job.
    validate_acceptance(run, jobs, plan["base"], attempt, context="Release acceptance")
    pr = api(base + "/pulls/" + number)
    main_sha = api(base + "/git/ref/heads/main")["object"]["sha"]
    merge = api(base + "/git/commits/" + plan["revision"])
    validate_plan(plan, pr, main_sha, merge)
    protection = required_protection(repo)
    if plan["kind"] == "dependency":
        files = api(base + "/pulls/" + number + "/files?per_page=100")
        comparison = api(base + "/compare/" + plan["base"] + "..." + plan["head"])
        validate({**pr, "draft": False}, files, plan["head"], comparison, protection)
        def contents(ref):
            return {path: base64.b64decode(api(base + "/contents/" + path + "?ref=" + ref)["content"]).decode() for path in ALLOWED}
        validate_contents(pr["head"]["ref"].removeprefix("codex/upstream-"), contents(plan["base"]), contents(plan["head"]), plan["version"])
    if not pr.get("merged"):
        # GitHub can replace its temporary merge SHA after marking a PR ready.
        # Report against the stable head only after proving identical content.
        validate_check_target(plan, api(base + "/git/commits/" + plan["head"]))
        if pr["draft"]:
            api("graphql", "POST", {"query": "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{id}}}", "variables": {"id": pr["node_id"]}})
        current_merge_sha = api(base + "/git/ref/pull/" + number + "/merge")["object"]["sha"]
        current_merge = api(base + "/git/commits/" + current_merge_sha)
        validate_plan(plan, pr, api(base + "/git/ref/heads/main")["object"]["sha"], current_merge)
        api(base + "/check-runs", "POST", {"name": "CI required", "head_sha": plan["head"],
            "status": "completed", "conclusion": "success", "details_url": run["html_url"],
            "output": {"title": "Full release and upgrade acceptance passed",
                       "summary": "Accepted merge tree: `" + plan["tree"] + "`. " + run["html_url"]}})
        await_merge_gate(base, number, plan)
        result = api(base + "/pulls/" + number + "/merge", "PUT", {"sha": plan["head"], "merge_method": "squash"})
        if result.get("merged") is not True:
            raise RuntimeError("GitHub did not merge the protected release PR")
        merged = result["sha"]
    else:
        merged = pr["merge_commit_sha"]
    if api(base + "/git/commits/" + merged)["tree"]["sha"] != plan["tree"]:
        raise ValueError("Merged content differs from accepted content; publication withheld")
    version = plan["version"]
    tag = "v" + version
    releases = api(base + "/releases?per_page=100")
    existing = next((r for r in releases if r["tag_name"] == tag), None)
    if existing:
        ref = api(base + "/git/ref/tags/" + tag)["object"]
        if ref["type"] == "tag":
            ref = api(base + "/git/tags/" + ref["sha"])["object"]
        if ref["sha"] != merged or existing["draft"] or existing["prerelease"]:
            raise ValueError("Existing release does not match the accepted stable revision")
    else:
        # Create the tag atomically; never publish against an unrelated existing tag.
        refs = api(base + "/git/matching-refs/tags/" + tag)
        existing_ref = next((r for r in refs if r["ref"] == "refs/tags/" + tag), None)
        if existing_ref:
            obj = existing_ref["object"]
            if obj["type"] == "tag":
                obj = api(base + "/git/tags/" + obj["sha"])["object"]
            if obj["sha"] != merged:
                raise ValueError("Existing tag points to another revision")
        else:
            api(base + "/git/refs", "POST", {"ref": "refs/tags/" + tag, "sha": merged})
        notes = Path("/tmp/pompey-release-notes.md")
        changelog = base64.b64decode(api(base + "/contents/pompey/CHANGELOG.md?ref=" + merged)["content"]).decode()
        section = changelog.split("## " + version + "\n", 1)[1].split("\n## ", 1)[0].strip()
        notes.write_text(section + "\n\nValidation: full integration and previous-release upgrade passed for tree `" + plan["tree"] + "`. " + run["html_url"] + "\n")
        subprocess.run(["gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--title", "Pompey " + version, "--notes-file", str(notes), "--latest"], check=True)
    print("Published " + tag + " from accepted merge " + merged)


if __name__ == "__main__":
    main()
