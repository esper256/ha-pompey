"""Promotion trust boundaries and release-baseline selection, without GitHub writes."""

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from promote_candidate import validate, validate_contents, validate_release_gate, MANIFEST
from release_baseline import choose, FALLBACK
from release_metadata import next_patch


class PromotionPolicy(unittest.TestCase):
    def test_release_requires_protected_manual_haos_gate(self):
        validate_release_gate({"protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}]})
        for environment in [{}, {"protection_rules": []}, {"protection_rules": [{"type": "required_reviewers", "reviewers": []}]}]:
            with self.assertRaises(ValueError):
                validate_release_gate(environment)

    def setUp(self):
        self.pr = {"user": {"login": "github-actions[bot]"},
                   "head": {"repo": {"full_name": "esper256/ha-pompey"}, "ref": "codex/upstream-sonarr", "sha": "accepted"},
                   "base": {"ref": "main"}, "state": "open", "draft": False}
        self.files = [{"filename": "pompey/rootfs/usr/share/pompey/engines.json", "status": "modified"}]
        self.protection = {"strict": True, "checks": [{"context": "CI required", "app_id": 15368}]}

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

    def test_baseline_tracks_the_last_stable_release_not_an_old_public_tag(self):
        self.assertEqual(choose({"tag_name": "v0.4.0"}), FALLBACK)
        self.assertEqual(choose({"tag_name": "v0.5.0"}), "v0.5.0")
        self.assertEqual(choose({"tag_name": "v0.6.0", "prerelease": True}), FALLBACK)
        self.assertEqual(choose({"tag_name": "--unsafe-ref"}), FALLBACK)


if __name__ == "__main__":
    unittest.main()
