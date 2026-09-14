#!/usr/bin/env python3
"""Check the executable boundary, while allowing downloader WebAPI fixtures."""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class NoTorrentProcess(unittest.TestCase):
    def test_python_tests_never_launch_torrent_executables(self):
        for path in (ROOT/'tests').rglob('*.py'):
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'Popen','run','call','check_call','check_output'}:
                    rendered=ast.unparse(node)
                    for forbidden in ['qbittorrent-nox','transmission-daemon','libtorrent','aria2c']:
                        self.assertNotIn(forbidden,rendered,str(path))
    def test_shell_tests_do_not_boot_torrent_client(self):
        for path in (ROOT/'tests').rglob('*.sh'):
            self.assertNotIn('--confirm-legal-notice',path.read_text(),str(path))


if __name__=='__main__':unittest.main()
