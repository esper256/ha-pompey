#!/usr/bin/env python3
"""Verify pinned upstream artifacts, including executable layout, without Supervisor."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests/lib'))
sys.path.insert(0,str(ROOT/'pompey/rootfs/usr/local/bin'))
from engine_runtime import artifact
from engine_manager import elf


@unittest.skipUnless(os.environ.get('POMPEY_REAL_ENGINES')=='1','set POMPEY_REAL_ENGINES=1 for upstream artifact checks')
class Artifacts(unittest.TestCase):
    def test_arr_artifact_layout_and_checksum(self):
        for name in ['Radarr','Sonarr','Prowlarr']:
            with self.subTest(engine=name):elf(artifact(name)/name)
    def test_recyclarr_layout_and_checksum(self):elf(artifact('recyclarr')/'recyclarr')
    def test_musl_artifact_layout_and_checksums(self):
        with patch.dict(os.environ,{'POMPEY_SERVARR_OS':'linuxmusl'}):
            for name in ['Radarr','Sonarr','Prowlarr','recyclarr','dotnet']:
                with self.subTest(engine=name):elf(artifact(name)/name)
    def test_download_client_artifact_checksum_without_execution(self):
        elf(artifact('qbittorrent-nox'))


if __name__=='__main__':unittest.main()
