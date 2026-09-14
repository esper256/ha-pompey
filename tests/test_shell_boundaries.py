#!/usr/bin/env python3
"""Run shell/Python boundaries with controlled paths and a failing firewall executable."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BIN=ROOT/'pompey/rootfs/usr/local/bin'


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='pompey-shell-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.env={**os.environ,'POMPEY_CONFIG':str(self.root/'config'),'POMPEY_DATA':str(self.root/'data'),
                  'POMPEY_READY':str(self.root/'ready'),'POMPEY_ENGINES':str(self.root/'engines'),
                  'MEDIA_ROOT':str(self.root/'media'),'POMPEY_SECRETS':str(self.root/'data/secrets.json'),
                  'POMPEY_WG_ETC':str(self.root/'wg'),'POMPEY_WG_CONF':str(self.root/'wg/wg0.conf'),
                  'POMPEY_LAN_FILE':str(self.root/'lan'),'POMPEY_RESOLV':str(self.root/'resolv'),
                  'PATH':str(BIN)+':'+os.environ['PATH']}
        self.stub=self.root/'commands';self.stub.mkdir()
        self.env['PATH']=str(self.stub)+':'+self.env['PATH']
        for name in ['vpn-killswitch','pompey-secrets']:
            file=self.stub/name
            file.write_text('#!/bin/sh\nexec bash "'+str(BIN/name)+'" "$@"\n');file.chmod(0o755)
        vpn=self.root/'config/wireguard';vpn.mkdir(parents=True)
        (vpn/'wg0.conf').write_text((ROOT/'tests/fixtures/wg0.conf').read_text())

    def test_firewall_failure_does_not_mark_applied(self):
        stub=self.stub/'nft';stub.write_text('#!/bin/sh\nexit 1\n');stub.chmod(0o755)
        proc=subprocess.run(['bash',str(BIN/'apply-vpn-config')],env=self.env,capture_output=True,text=True)
        self.assertEqual(proc.returncode,4,proc.stderr)
        self.assertFalse((self.root/'ready/vpn-applied').exists())
        self.assertFalse((self.root/'resolv').exists())

    def test_vpn_preflight_writes_through_resolver_symlink(self):
        ready=self.root/'ready';ready.mkdir()
        content='nameserver 172.30.32.3\n'
        (ready/'bootstrap-resolv.conf').write_text(content)
        resolver=self.root/'resolv';target=self.root/'managed-resolver'
        resolver.symlink_to(target)
        log=self.stub/'pompey-log';log.write_text('#!/bin/sh\nexit 0\n');log.chmod(0o755)
        service=(ROOT/'pompey/rootfs/etc/services.d/wireguard/run').read_text()
        # Exercise startup through DNS restoration, without touching networking.
        preflight=service.split('until apply-vpn-config; do',1)[0]
        for exists in [False,True]:
            if exists: target.write_text('nameserver 10.2.0.1\n')
            proc=subprocess.run(['bash','-c',preflight],env={**self.env,'POMPEY_FAKE_VPN':'0'},capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertTrue(resolver.is_symlink())
            self.assertEqual(target.read_text(),content)

    def test_python_config_writer_preserves_vpn_binding_and_policy(self):
        config=self.root/'config';config.mkdir(exist_ok=True)
        secret=self.root/'secrets.json'
        secret.write_text(json.dumps({'radarr_api_key':'r','sonarr_api_key':'s','prowlarr_api_key':'p','qbit_pbkdf2':'fake','qbit_user':'u'}))
        for policy in ['stop_sharing','share_to_ratio','share_one_day']:
            proc=subprocess.run([sys.executable,str(BIN/'write_engine_configs.py'),str(secret),str(self.root/'media')],
                                env={**self.env,'AFTER_DOWNLOAD':policy},capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            text=(config/'qBittorrent/qBittorrent.conf').read_text()
            self.assertIn('Session\\Interface=wg0',text)
            self.assertIn('Session\\MaxRatioAct=0',text)

    def test_every_shell_entrypoint_parses(self):
        paths=list(BIN.iterdir())+list((ROOT/'pompey/rootfs/etc').glob('services.d/*/*'))+list((ROOT/'pompey/rootfs/etc').glob('cont-init.d/*'))
        for path in paths:
            if path.is_file() and ('bash' in path.read_text().splitlines()[0]):
                with self.subTest(path=path.name):subprocess.run(['bash','-n',str(path)],check=True)

    def test_shellcheck(self):
        if not shutil.which('shellcheck'):
            self.skipTest('shellcheck is not installed')
        paths=[]
        for path in (ROOT/'pompey/rootfs').rglob('*'):
            if path.is_file():
                with path.open('rb') as source:first=source.readline()
                if first.startswith(b'#!') and b'bash' in first:paths.append(str(path))
        subprocess.run(['shellcheck',*paths],check=True)


if __name__=='__main__':unittest.main()
