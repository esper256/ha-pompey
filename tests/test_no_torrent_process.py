#!/usr/bin/env python3
"""Check the executable boundary, while allowing downloader WebAPI fixtures."""
import ast
import os
import subprocess
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class NoTorrentProcess(unittest.TestCase):
    def test_python_tests_never_launch_torrent_executables(self):
        for path in (ROOT/'tests').rglob('*.py'):
            if path == ROOT/'tests/lib/isolated_qbit.py':
                continue  # Sole launcher; runtime boundary tested below.
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'Popen','run','call','check_call','check_output'}:
                    rendered=ast.unparse(node)
                    for forbidden in ['qbittorrent-nox','transmission-daemon','libtorrent','aria2c']:
                        self.assertNotIn(forbidden,rendered,str(path))
    def test_real_downloader_worker_refuses_host_namespace(self):
        result = subprocess.run([sys.executable, str(ROOT/'tests/lib/isolated_qbit.py'),
            '--host-namespace', os.readlink('/proc/self/ns/net'), '--uid', str(os.getuid()),
            '--gid', str(os.getgid()), '--probe'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to launch in the host network namespace', result.stderr)

    def test_shell_tests_do_not_boot_torrent_client(self):
        for path in (ROOT/'tests').rglob('*.sh'):
            self.assertNotIn('--confirm-legal-notice',path.read_text(),str(path))


if __name__=='__main__':unittest.main()
