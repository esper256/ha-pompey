#!/usr/bin/env python3
"""Prepare metadata online, then run all downloader checks without external networking."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_arr_integration import RealArrTestCase, http, ROOT
from engine_runtime import artifact
import wire_stack


@unittest.skipUnless(os.environ.get('POMPEY_REAL_QBIT') == '1', 'set POMPEY_REAL_QBIT=1 for isolated downloader contracts')
class RealDownloader(RealArrTestCase):
    # The parent suite's engine switch also applies: explicitly enable both.
    @classmethod
    def setUpClass(cls):
        cls.host_namespace = os.readlink('/proc/self/ns/net')
        cls.worker = ROOT/'tests/lib/isolated_qbit.py'
        cls.prefix = ['sudo', '-n', 'unshare', '--net', sys.executable, str(cls.worker),
                      '--host-namespace', cls.host_namespace, '--uid', str(os.getuid()), '--gid', str(os.getgid())]
        probe = subprocess.run(cls.prefix + ['--probe'], capture_output=True, text=True)
        if probe.returncode:
            # Opting into this test requires isolation; never silently fall back.
            raise RuntimeError('Downloader isolation unavailable: ' + probe.stderr)
        cls.qbit = artifact('qbittorrent-nox')  # Fetch before entering isolation.
        cls.sonarr = artifact('Sonarr')/'Sonarr'
        super().setUpClass()
        base = cls.urls['Sonarr']+'/api/v3'
        media = cls.root/'media'; media.mkdir()
        library = media/'library'; library.mkdir()
        http('POST', base+'/rootfolder', {'path': str(library)})
        show = http('GET', base+'/series/lookup?term=tvdb:79257')[0]
        show.update(qualityProfileId=http('GET', base+'/qualityprofile')[0]['id'], rootFolderPath=str(library),
                    monitored=True, seasonFolder=True, addOptions={'searchForMissingEpisodes': False})
        show = http('POST', base+'/series', show)
        import time
        for _ in range(120):
            episodes = http('GET', base+'/episode?seriesId='+str(show['id']))
            if any(e['seasonNumber']==1 and e['episodeNumber']==1 for e in episodes): break
            time.sleep(.5)
        else: raise RuntimeError('Synthetic import requires episode metadata')
        with patch.dict(os.environ, MEDIA_ROOT=str(media)):
            wire_stack.ensure_media_management(base, 'a'*32, 'sonarr')
        for proc in cls.procs: cls.stop(proc)
        (cls.root/'scenario.json').write_text(json.dumps({'qbit': str(cls.qbit), 'sonarr': str(cls.sonarr),
            'sonarrUrl': cls.urls['Sonarr'], 'seriesId': show['id'], 'media': str(media)}))

    def test_settings_restart_sharing_and_arr_import(self):
        proc = subprocess.Popen(self.prefix + ['--scenario', str(self.root/'scenario.json')], start_new_session=True)
        try:
            self.assertEqual(proc.wait(timeout=360), 0)
        finally:
            if proc.poll() is None:
                subprocess.run(['sudo', '-n', 'kill', '-TERM', '--', '-'+str(proc.pid)], check=False)
                try: proc.wait(10)
                except subprocess.TimeoutExpired:
                    subprocess.run(['sudo', '-n', 'kill', '-KILL', '--', '-'+str(proc.pid)], check=False)
                    proc.wait()


if __name__ == '__main__': unittest.main()
