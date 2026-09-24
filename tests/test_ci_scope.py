"""Path selection must never skip executable changes or unknown file types."""
import sys
import json
import os
import subprocess
import yaml
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from ci_scope import needs_code_checks


class Scope(unittest.TestCase):
    def test_household_and_agent_docs_skip_runtime(self):
        self.assertFalse(needs_code_checks(['README.md', 'AGENTS.md', 'docs/testing.md', 'pompey/CHANGELOG.md']))

    def test_code_and_unknown_paths_require_checks(self):
        for path in ['docs/helper.py', 'tests/fixtures/example.md', 'pompey/rootfs/page.md',
                     'pompey/config.yaml', '.github/workflows/test.yaml', 'tools/ci_scope.py', 'new-file']:
            with self.subTest(path=path):
                self.assertTrue(needs_code_checks(['README.md', path]))


class RequiredGate(unittest.TestCase):
    def test_gate_requires_exact_selected_results(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load((root/'.github/workflows/test.yaml').read_text())
        gate = workflow['jobs']['required']
        script = gate['steps'][0]['run']
        for code, outcome, selection, passes in [
            ('true', 'success', 'success', True),
            ('false', 'skipped', 'success', True),
            ('true', 'skipped', 'success', False),
            ('true', 'failure', 'success', False),
            ('true', 'cancelled', 'success', False),
            ('false', 'skipped', 'failure', False),
        ]:
            with self.subTest(code=code, outcome=outcome, selection=selection):
                jobs = {name: {'result': 'success' if code == 'true' else 'skipped'}
                        for name in gate['needs'] if name != 'changes'}
                jobs['anime']['result'] = outcome
                jobs['changes'] = {'result': selection, 'outputs': {'code': code}}
                result = subprocess.run(['bash', '-c', script],
                                        env=dict(os.environ, NEEDS=json.dumps(jobs)), capture_output=True)
                self.assertEqual(result.returncode == 0, passes, result.stderr.decode())


if __name__ == '__main__':
    unittest.main()
