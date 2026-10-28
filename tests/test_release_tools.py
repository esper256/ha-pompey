"""Promotion trust boundaries and release-baseline selection, without GitHub writes."""

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from promote_candidate import validate, validate_contents, validate_acceptance, validate_plan, validate_check_target, MANIFEST
from release_baseline import choose, FALLBACK
from release_metadata import next_patch


class DependabotQueue(unittest.TestCase):
    def test_privileged_queue_rejects_forks_people_drafts_and_unrelated_branches(self):
        from dependabot_queue import eligible
        pr={"state":"open","draft":False,"user":{"login":"dependabot[bot]"},
            "base":{"ref":"main"},"head":{"repo":{"full_name":"esper256/ha-pompey"},"ref":"dependabot/docker/pompey/base"}}
        self.assertTrue(eligible(pr))
        for change in [{"draft":True},{"state":"closed"},{"user":{"login":"person"}},
                       {"head":{"repo":{"full_name":"someone/fork"},"ref":"dependabot/docker/base"}},
                       {"head":{"repo":{"full_name":"esper256/ha-pompey"},"ref":"codex/upstream-sonarr"}}]:
            self.assertFalse(eligible({**pr,**change}))

    def test_failed_acceptance_never_requests_rebase_or_merge(self):
        import dependabot_queue as queue
        pr={"state":"open","draft":False,"user":{"login":"dependabot[bot]"},
            "base":{"ref":"main"},"head":{"sha":"head","repo":{"full_name":queue.REPO},"ref":"dependabot/docker/base"}}
        with patch.object(queue,"api",side_effect=[pr,{"check_runs":[{"name":"CI required","status":"completed","conclusion":"failure"}]}]) as api, patch.object(queue.subprocess,"run") as merge:
            queue.process(104)
            self.assertEqual(api.call_count,2)
            merge.assert_not_called()

    def test_stale_branch_uses_bot_rebase_once_and_keeps_head_protection(self):
        import dependabot_queue as queue
        pr={"state":"open","draft":False,"user":{"login":"dependabot[bot]"},
            "base":{"ref":"main"},"head":{"sha":"head","repo":{"full_name":queue.REPO},"ref":"dependabot/docker/base"}}
        marker="<!-- pompey-dependabot-rebase:base -->"
        for comments, writes in [([],1),([{"user":{"login":"github-actions[bot]"},"body":marker}],0)]:
            responses=[pr,{"check_runs":[]},{"object":{"sha":"base"}},{"behind_by":1},comments]+([{}] if writes else [])
            with patch.object(queue,"api",side_effect=responses) as api, patch.object(queue.subprocess,"run") as merge:
                queue.process(104)
                posts=[c for c in api.call_args_list if len(c.args)>1 and c.args[1]=="POST"]
                self.assertEqual(len(posts),writes)
                if posts:
                    self.assertEqual(posts[0].args[2]["body"],"@dependabot rebase\n\n"+marker)
                args=merge.call_args.args[0]
                self.assertIn("--auto",args)
                self.assertEqual(args[-2:],["--match-head-commit","head"])
                self.assertNotIn("--admin",args)


class PromotionPolicy(unittest.TestCase):
    def setUp(self):
        self.pr = {"user": {"login": "github-actions[bot]"},
                   "head": {"repo": {"full_name": "esper256/ha-pompey"}, "ref": "codex/upstream-sonarr", "sha": "accepted"},
                   "base": {"ref": "main"}, "state": "open", "draft": False}
        self.files = [{"filename": "pompey/rootfs/usr/share/pompey/engines.json", "status": "modified"}]
        self.protection = {"strict": True, "checks": [{"context": "CI required", "app_id": 15368}]}
        self.run = {"event": "workflow_dispatch", "head_sha": "accepted",
                    "path": ".github/workflows/test.yaml", "run_attempt": 2}
        self.jobs = [{"name": name, "status": "completed", "conclusion": "success"}
                     for name in ["Application integration and network isolation", "CI required"]]

    def test_acceptance_requires_the_exact_candidate_and_workflow_attempt(self):
        validate_acceptance(self.run, self.jobs, "accepted", 2)
        for key, value in [("event", "pull_request"), ("head_sha", "another-candidate"),
                           ("path", ".github/workflows/another.yaml"), ("run_attempt", 1)]:
            with self.subTest(field=key), self.assertRaises(ValueError):
                validate_acceptance({**self.run, key: value}, self.jobs, "accepted", 2)

    def test_failed_incomplete_or_missing_checks_cannot_authorize_publication(self):
        for index in range(len(self.jobs)):
            for state in ["failure", "cancelled", "skipped", None]:
                jobs = copy.deepcopy(self.jobs)
                jobs[index]["conclusion"] = state
                with self.subTest(job=index, conclusion=state), self.assertRaises(ValueError):
                    validate_acceptance(self.run, jobs, "accepted", 2)
            jobs = copy.deepcopy(self.jobs)
            jobs[index]["status"] = "in_progress"
            with self.assertRaises(ValueError):
                validate_acceptance(self.run, jobs, "accepted", 2)
        for jobs in [self.jobs[:1], self.jobs[1:], self.jobs + [self.jobs[0]]]:
            with self.assertRaises(ValueError):
                validate_acceptance(self.run, jobs, "accepted", 2)

    def test_only_current_protected_bot_candidate_is_accepted(self):
        validate(self.pr, self.files, "accepted", {"behind_by": 0}, self.protection)
        for strict, app_id, behind in [(False, 15368, 0), (True, 1, 0), (True, 15368, 1)]:
            with self.subTest(strict=strict, app_id=app_id, behind=behind), self.assertRaises(ValueError):
                validate(self.pr, self.files, "accepted", {"behind_by": behind}, {"strict": strict, "checks": [{"context": "CI required", "app_id": app_id}]})

    def test_foreign_identity_changed_head_and_extra_code_are_rejected(self):
        variants = []
        for key, value in [("sha", "new-head"), ("ref", "feature/unrelated"), ("repo", {"full_name": "someone/fork"})]:
            candidate = copy.deepcopy(self.pr)
            candidate["head"][key] = value
            variants.append(candidate)
        candidate = copy.deepcopy(self.pr)
        candidate["user"]["login"] = "another-bot"
        variants.append(candidate)
        for candidate in variants:
            with self.assertRaises(ValueError):
                validate(candidate, self.files, "accepted", {"behind_by": 0}, self.protection)
        with self.assertRaises(ValueError):
            validate(self.pr, self.files + [{"filename": "tools/promote_candidate.py", "status": "modified"}], "accepted", {"behind_by": 0}, self.protection)

    def test_prereleases_cannot_implicitly_become_stable_updates(self):
        self.assertEqual(next_patch("0.5.9"), "0.5.10")
        for version in ["0.5.0-beta.1", "0.5", "0.5.0;echo bad"]:
            with self.assertRaises(ValueError):
                next_patch(version)

    def test_allowed_filenames_do_not_authorize_config_or_artifact_source_changes(self):
        manifest = {"schema": 1, "engines": {"Sonarr": {"version": "v1", "artifacts": {"linuxmusl-amd64": {"url": "https://github.com/Sonarr/Sonarr/releases/download/v1/app.tar.gz", "sha256": "a" * 64}}}}, "resources": {"guides": "a" * 40}}
        before = {"pompey/config.yaml": 'version: "0.5.0"\nprivileged: false\n',
                  "pompey/Dockerfile": 'ARG BUILD_VERSION="0.5.0"\n',
                  "pompey/CHANGELOG.md": '# Changelog\n\n## 0.5.0\n\nExisting release.\n',
                  MANIFEST: json.dumps(manifest)}
        after = {path: value.replace('0.5.0', '0.5.1') for path, value in before.items()}
        after["pompey/CHANGELOG.md"] = '# Changelog\n\n## 0.5.1\n\nNew pins.\n\n' + before["pompey/CHANGELOG.md"].split('\n\n', 1)[1]
        manifest["engines"]["Sonarr"]["version"] = "v2"
        after[MANIFEST] = json.dumps(manifest)
        validate_contents("sonarr", before, after, "0.5.1")
        for path, value in [("pompey/config.yaml", after["pompey/config.yaml"].replace("false", "true")),
                            ("pompey/Dockerfile", after["pompey/Dockerfile"] + "RUN unexpected-command\n"),
                            (MANIFEST, after[MANIFEST].replace("https://github.com/Sonarr/Sonarr/", "https://example.invalid/"))]:
            changed = {**after, path: value}
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_contents("sonarr", before, changed, "0.5.1")

    def test_bundle_preserves_each_component_source_and_scope_restriction(self):
        pins = {"schema": 1, "engines": {
            name: {"version": "v1", "artifacts": {"linuxmusl-amd64": {
                "url": url + "v1/app.tar.gz", "sha256": "a" * 64}}}
            for name, url in {"Sonarr": "https://github.com/Sonarr/Sonarr/releases/download/",
                              "recyclarr": "https://github.com/recyclarr/recyclarr/releases/download/"}.items()
        }, "resources": {"trash_guides": "b" * 40}}
        before = {"pompey/config.yaml": 'version: "0.5.0"\nprivileged: false\n',
                  "pompey/Dockerfile": 'ARG BUILD_VERSION="0.5.0"\n',
                  "pompey/CHANGELOG.md": '# Changelog\n\n## 0.5.0\n\nExisting release.\n',
                  MANIFEST: json.dumps(pins)}
        from release_metadata import next_patch, release_notes
        import re
        version = next_patch(re.search(r'^version: "([^\"]+)"$', before["pompey/config.yaml"], re.M)[1])
        after = dict(before)
        after["pompey/config.yaml"] = re.sub(r'^version: "[^\"]+"$', 'version: "' + version + '"', before["pompey/config.yaml"], flags=re.M)
        after["pompey/Dockerfile"] = re.sub(r'^ARG BUILD_VERSION="[^\"]+"$', 'ARG BUILD_VERSION="' + version + '"', before["pompey/Dockerfile"], flags=re.M)
        after["pompey/CHANGELOG.md"] = release_notes(before["pompey/CHANGELOG.md"], version, "- Update dependencies.")
        pins = json.loads(before[MANIFEST])
        pins["engines"]["Sonarr"]["version"] = "test-new-version"
        pins["engines"]["recyclarr"]["version"] = "test-new-version"
        pins["resources"]["trash_guides"] = "a" * 40
        after[MANIFEST] = json.dumps(pins)
        validate_contents("bundle", before, after, version)
        for changed in [
            {**pins, "schema": 99},
            {**pins, "engines": {**pins["engines"], "unknown": {"version": "1"}}},
            {**pins, "resources": {**pins["resources"], "trash_guides": "unpinned"}},
        ]:
            with self.assertRaises(ValueError):
                validate_contents("bundle", before, {**after, MANIFEST: json.dumps(changed)}, version)
        pins["engines"]["Sonarr"]["artifacts"]["linuxmusl-amd64"]["url"] = "https://example.invalid/unsafe.tar.gz"
        with self.assertRaises(ValueError):
            validate_contents("bundle", before, {**after, MANIFEST: json.dumps(pins)}, version)

    def test_pending_notes_move_into_the_next_release_without_rewriting_history(self):
        from release_metadata import release_notes, split_unreleased
        before = "# Changelog\n\n## Unreleased\n\n- Fix concurrent requests.\n\n## 0.5.0\n\nExisting release.\n"
        after = release_notes(before, "0.5.1", "- Update quality resources.")
        self.assertNotIn("## Unreleased", after)
        self.assertTrue(after.startswith("# Changelog\n\n## 0.5.1\n"))
        self.assertIn("- Fix concurrent requests.", after)
        self.assertTrue(after.endswith(split_unreleased(before)[1]))

    def test_baseline_tracks_the_last_stable_release_not_an_old_public_tag(self):
        self.assertEqual(choose({"tag_name": "v0.4.0"}), FALLBACK)
        self.assertEqual(choose({"tag_name": "v0.5.0"}), "v0.5.0")
        self.assertEqual(choose({"tag_name": "v0.6.0", "prerelease": True}), FALLBACK)
        self.assertEqual(choose({"tag_name": "--unsafe-ref"}), FALLBACK)


class ReleasePlanning(unittest.TestCase):
    def test_stable_head_check_requires_identical_accepted_content(self):
        plan = {"head": "head", "tree": "accepted-tree"}
        validate_check_target(plan, {"sha": "head", "tree": {"sha": "accepted-tree"}})
        for head in [{"sha": "changed", "tree": {"sha": "accepted-tree"}},
                     {"sha": "head", "tree": {"sha": "untested-tree"}}]:
            with self.assertRaises(ValueError):
                validate_check_target(plan, head)

    def test_merge_wait_is_bounded_and_rejects_a_changed_release(self):
        from unittest.mock import patch
        from promote_candidate import await_merge_gate
        plan = {"head": "head", "base": "base"}
        pr = {"head": {"sha": "head"}, "base": {"sha": "base"}, "mergeable_state": "blocked"}
        with patch("promote_candidate.api", side_effect=[pr, {**pr, "mergeable_state": "clean"}]), patch("promote_candidate.time.sleep") as pause:
            await_merge_gate("repos/test", "1", plan)
            pause.assert_called_once_with(2)
        with patch("promote_candidate.api", return_value={**pr, "mergeable_state": "unstable"}), patch("promote_candidate.time.sleep") as pause:
            await_merge_gate("repos/test", "1", plan)
            pause.assert_not_called()
        with patch("promote_candidate.api", return_value=pr), patch("promote_candidate.time.sleep") as pause, self.assertRaises(ValueError):
            await_merge_gate("repos/test", "1", plan)
        self.assertEqual(pause.call_count, 7)
        with patch("promote_candidate.api", return_value={**pr, "head": {"sha": "changed"}}), patch("promote_candidate.time.sleep") as pause, self.assertRaises(ValueError):
            await_merge_gate("repos/test", "1", plan)
        pause.assert_not_called()

    def test_effective_rules_require_the_actions_check_in_the_same_strict_policy(self):
        from release_protection import protection_from_rules
        rule = {"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": True,
            "required_status_checks": [{"context": "CI required", "integration_id": 15368}]}}
        self.assertTrue(protection_from_rules([rule])["strict"])
        loose = copy.deepcopy(rule)
        loose["parameters"]["strict_required_status_checks_policy"] = False
        foreign = copy.deepcopy(rule)
        foreign["parameters"]["required_status_checks"][0]["integration_id"] = 1
        missing_app = copy.deepcopy(rule)
        missing_app["parameters"]["required_status_checks"][0].pop("integration_id")
        for rules in [[], [loose], [foreign], [missing_app], [loose, foreign], [{"type": "pull_request"}]]:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                protection_from_rules(rules)

    def test_merge_gate_rejects_changed_head_base_tree_and_foreign_repo(self):
        plan = dict(kind="release", head="head", base="base", revision="merge", tree="tree")
        pr = {"head": {"sha": "head", "repo": {"full_name": "esper256/ha-pompey"}},
              "base": {"ref": "main"}, "state": "open"}
        merge = {"parents": [{"sha": "base"}, {"sha": "head"}], "tree": {"sha": "tree"}}
        validate_plan(plan, pr, "base", merge)
        for changed in [{**plan, "head": "another"}, {**plan, "base": "stale"}, {**plan, "tree": "other"}]:
            with self.assertRaises(ValueError):
                validate_plan(changed, pr, "base", merge)
        with self.assertRaises(ValueError):
            validate_plan(plan, pr, "advanced-main", merge)
        changed = copy.deepcopy(pr)
        changed["head"]["repo"]["full_name"] = "someone/fork"
        with self.assertRaises(ValueError):
            validate_plan(plan, changed, "base", merge)

    def test_planning_requires_new_stable_version_and_exact_merge_parents(self):
        from release_plan import validate_candidate, stable_version
        pr = {"head": {"sha": "head", "repo": {"full_name": "esper256/ha-pompey"}},
              "base": {"ref": "main", "sha": "base"}, "state": "open"}
        merge = {"parents": [{"sha": "base"}, {"sha": "head"}]}
        validate_candidate(pr, "base", merge, "0.5.1", "0.5.0", {"behind_by": 0})
        with self.assertRaises(ValueError):
            validate_candidate(pr, "base", merge, "0.5.1", "0.5.0", {"behind_by": 1})
        for version in ["0.5.0", "0.4.9"]:
            with self.assertRaises(ValueError):
                validate_candidate(pr, "base", merge, version, "0.5.0", {"behind_by": 0})
        with self.assertRaises(ValueError):
            stable_version('version: "0.5.1-beta.1"')
        with self.assertRaises(ValueError):
            validate_candidate(pr, "base", {"parents": [{"sha": "head"}]}, "0.5.1", "0.5.0", {"behind_by": 0})

    def test_queue_serializes_active_releases_and_retains_failed_evidence(self):
        from dependency_updates import queue_decision
        active = {"display_title": "Release PR #105", "status": "in_progress"}
        self.assertEqual(queue_decision([active]), "busy")
        failed = {**active, "status": "completed", "conclusion": "failure"}
        self.assertEqual(queue_decision([failed], 105), "attempted")
        self.assertEqual(queue_decision([failed], 106), "dispatch")
        self.assertEqual(queue_decision([{**active, "display_title": "Unrelated workflow"}]), "dispatch")

    def test_timing_summary_preserves_failure_upgrade_and_safe_case_labels(self):
        from ci_summary import markdown
        result = markdown({"success": False, "journeys": [{"layout": "forward-upgrade", "success": False,
            "containers": [{"success": False, "cases": [{"scenario": "<script>|unsafe", "status": "failed"}]}]}]})
        self.assertIn("Previous-release upgrade", result)
        self.assertIn("failed / incomplete", result)
        self.assertNotIn("<script>", result)
        self.assertIn("&#124;", result)


if __name__ == "__main__":
    unittest.main()
