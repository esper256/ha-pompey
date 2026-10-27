"""Submission/release selection and fail-closed required-check behavior."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ci_scope import integration_suite, needs_code_checks, require_results, selection


class CheckPolicy(unittest.TestCase):
    def result(self, suite="pr", code="true", draft="false"):
        return {"changes": {"result": "success", "outputs": {"code": code, "suite": suite, "draft": draft,
                "context": "Draft checks" if draft == "true" else "CI required"}},
                "fast": {"result": "success" if code == "true" else "skipped"},
                "acceptance": {"result": "success" if suite != "none" else "skipped"}}

    def test_draft_edits_and_main_pushes_never_select_release_acceptance(self):
        self.assertEqual(integration_suite("pull_request", True, draft=True), "none")
        self.assertEqual(integration_suite("pull_request", True), "pr")
        self.assertEqual(integration_suite("push", True), "none")
        self.assertEqual(integration_suite("workflow_dispatch", True), "release")
        self.assertEqual(integration_suite("schedule", True), "release")
        with self.assertRaises(ValueError):
            integration_suite("unknown-event", True)

    def test_documentation_does_not_hide_unknown_or_ui_code_changes(self):
        self.assertFalse(needs_code_checks(["README.md", "docs/roadmap-0.5.0/index.html"]))
        for path in ["pompey/rootfs/usr/share/pompey/index.html", "tests/fast.py", ".github/workflows/test.yaml", "unknown.file"]:
            self.assertTrue(needs_code_checks(["README.md", path]))
        self.assertEqual(integration_suite("pull_request", False), "none")

    def test_successful_selected_checks_authorize_the_gate(self):
        for suite, code in [("pr", "true"), ("release", "true"), ("none", "true"), ("none", "false")]:
            require_results(self.result(suite, code))

    def test_drafts_cannot_leave_a_green_gate_before_submission(self):
        require_results(self.result("none", draft="true"))
        self.assertEqual(selection("pull_request", True, draft=True)["context"], "Draft checks")
        self.assertEqual(selection("pull_request", True)["context"], "CI required")
        jobs = self.result("none", draft="true")
        jobs["changes"]["outputs"]["context"] = "CI required"
        with self.assertRaises(ValueError):
            require_results(jobs)
        with self.assertRaises(ValueError):
            require_results(self.result("pr", draft="true"))

    def test_selected_checks_cannot_fail_cancel_or_skip(self):
        for suite in ["pr", "release"]:
            for name in ["changes", "fast", "acceptance"]:
                for result in ["failure", "cancelled", "skipped"]:
                    jobs = self.result(suite)
                    jobs[name]["result"] = result
                    with self.subTest(suite=suite, job=name, result=result), self.assertRaises(ValueError):
                        require_results(jobs)
        jobs = copy.deepcopy(self.result())
        jobs["changes"]["outputs"]["suite"] = "unknown"
        with self.assertRaises(ValueError):
            require_results(jobs)
        with self.assertRaises(ValueError):
            require_results(self.result("release", code="false"))


if __name__ == "__main__":
    unittest.main()
