"""Promote only an exact, current, protected and fully tested bot candidate."""

import os
from pathlib import Path
import re
import subprocess
import base64
import copy
import json
from dependency_updates import api
from release_metadata import current, next_patch

ALLOWED = {"pompey/rootfs/usr/share/pompey/engines.json", "pompey/config.yaml", "pompey/Dockerfile", "pompey/CHANGELOG.md"}
MANIFEST = "pompey/rootfs/usr/share/pompey/engines.json"


def validate_release_gate(environment):
    if not any(rule.get("type") == "required_reviewers" and rule.get("reviewers")
               for rule in environment.get("protection_rules", [])):
        raise ValueError("The haos-release environment requires a manual HAOS reviewer gate")


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
    if not before["pompey/CHANGELOG.md"].startswith(header) or not after["pompey/CHANGELOG.md"].startswith(header + "## " + version + "\n") or not after["pompey/CHANGELOG.md"].endswith(before["pompey/CHANGELOG.md"][len(header):]):
        raise ValueError("Candidate rewrote existing release notes")


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


def main():
    number = os.environ.get("CANDIDATE_PR", "")
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Invalid candidate PR")
    repo = "esper256/ha-pompey"
    if os.environ.get("GITHUB_REPOSITORY") != repo:
        raise ValueError("Promotion is disabled for this repository")
    base = "repos/" + repo
    validate_release_gate(api(base + "/environments/haos-release"))
    sha = os.environ["GITHUB_SHA"]
    pr = api(base + "/pulls/" + number)
    files = api(base + "/pulls/" + number + "/files?per_page=100")
    comparison = api(base + "/compare/main..." + sha)
    # REST protection details require Administration:read, which GITHUB_TOKEN
    # cannot request. Read the public rule through GraphQL instead of adding a
    # privileged PAT to the release workflow.
    query = 'query { repository(owner:"esper256",name:"ha-pompey") { ref(qualifiedName:"refs/heads/main") { branchProtectionRule { requiresStrictStatusChecks requiredStatusChecks { context app { databaseId } } } } } }'
    rule = api("graphql", "POST", {"query": query})["data"]["repository"]["ref"]["branchProtectionRule"]
    protection = {"strict": bool(rule and rule["requiresStrictStatusChecks"]),
                  "checks": [{"context": check["context"], "app_id": (check.get("app") or {}).get("databaseId")}
                             for check in (rule or {}).get("requiredStatusChecks", [])]}
    validate(pr, files, sha, comparison, protection)
    raw = api(base + "/contents/pompey/config.yaml?ref=" + sha)
    config = base64.b64decode(raw["content"]).decode()
    match = re.search(r'^version: "([0-9]+\.[0-9]+\.[0-9]+)"$', config, re.M)
    if not match:
        raise ValueError("Only stable versions may be automatically released")
    version = match[1]
    ancestor = comparison["merge_base_commit"]["sha"]
    def contents(ref):
        return {path: base64.b64decode(api(base + "/contents/" + path + "?ref=" + ref)["content"]).decode() for path in ALLOWED}
    validate_contents(pr["head"]["ref"].removeprefix("codex/upstream-"), contents(ancestor), contents(sha), version)
    if not pr.get("merged") and version != next_patch(current()):
        raise ValueError("Candidate release number must follow current main")
    if pr.get("merged"):
        merged = pr["merge_commit_sha"]
    else:
        result = api(base + "/pulls/" + number + "/merge", "PUT", {"sha": sha, "merge_method": "squash"})
        if result.get("merged") is not True:
            raise RuntimeError("GitHub did not merge the protected candidate")
        merged = result["sha"]
    head_tree = api(base + "/git/commits/" + sha)["tree"]["sha"]
    merged_tree = api(base + "/git/commits/" + merged)["tree"]["sha"]
    if head_tree != merged_tree:
        raise RuntimeError("Merged content differs from the accepted candidate; release withheld")
    existing = subprocess.run(["gh", "release", "view", "v" + version, "--repo", repo], capture_output=True)
    if existing.returncode == 0:
        ref = api(base + "/git/ref/tags/v" + version)["object"]
        if ref["type"] == "tag":
            ref = api(base + "/git/tags/" + ref["sha"])["object"]
        if ref["sha"] != merged:
            raise RuntimeError("Release tag already points to another revision")
        print("Accepted release already exists")
        return
    notes = Path("/tmp/pompey-release-notes.md")
    notes.write_text(pr["body"] + "\n\nValidation: required fresh-install, release and forward-upgrade acceptance passed for `" + sha + "`.\n")
    subprocess.run(["gh", "release", "create", "v" + version, "--repo", repo, "--target", merged,
                    "--title", "Pompey " + version, "--notes-file", str(notes)], check=True)


if __name__ == "__main__":
    main()
