#!/usr/bin/env python3
"""The only allowed torrent-client launcher: peerless, loopback-only namespace."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'pompey/rootfs/usr/local/bin'), str(ROOT/'tests')]
import pompey_common as api
import wire_stack
from test_arr_integration import http


def verify_isolation(host_namespace):
    if os.readlink('/proc/self/ns/net') == host_namespace:
        raise RuntimeError('Refusing to launch in the host network namespace')
    links = json.loads(subprocess.check_output(['ip', '-j', 'link']))
    if {link['ifname'] for link in links} != {'lo'}:
        raise RuntimeError('Isolation must contain only loopback')
    for family in ['-4', '-6']:
        routes = json.loads(subprocess.check_output(['ip', '-j', family, 'route', 'show', 'table', 'all']))
        if any(route.get('dev') != 'lo' or route.get('gateway') or route.get('dst') == 'default' for route in routes):
            raise RuntimeError('Isolation contains an external route')


def bencode(value):
    if isinstance(value, int): return b'i'+str(value).encode()+b'e'
    if isinstance(value, str): value = value.encode()
    if isinstance(value, bytes): return str(len(value)).encode()+b':'+value
    if isinstance(value, list): return b'l'+b''.join(map(bencode, value))+b'e'
    return b'd'+b''.join(bencode(k)+bencode(v) for k,v in sorted(value.items()))+b'e'


def torrent_metadata(folder):
    # Private, no announce, no web seeds, no magnet metadata lookup.
    files = sorted(p for p in folder.iterdir() if p.is_file())
    chunks = bytearray(); pieces = []
    for file in files:
        with file.open('rb') as stream:
            while data := stream.read(262144):
                chunks.extend(data)
                while len(chunks) >= 262144:
                    pieces.append(hashlib.sha1(chunks[:262144]).digest()); del chunks[:262144]
    if chunks: pieces.append(hashlib.sha1(chunks).digest())
    info = {'name': folder.name, 'private': 1, 'piece length': 262144, 'pieces': b''.join(pieces),
            'files': [{'length': p.stat().st_size, 'path': [p.name]} for p in files]}
    return bencode({'info': info}), hashlib.sha1(bencode(info)).hexdigest()


class Scenario:
    def __init__(self, path, host_namespace):
        self.spec = json.loads(path.read_text()); self.root = path.parent
        self.host_namespace = host_namespace; self.qbit = None; self.sonarr = None
        self.url = 'http://127.0.0.1:8080'; self.base = self.spec['sonarrUrl']+'/api/v3'
        self.config = self.root/'config'; self.config.mkdir()
        shutil.copytree(self.root/'Sonarr', self.config/'sonarr')
        self.media = Path(self.spec['media'])
        os.environ.update(POMPEY_CONFIG=str(self.config), MEDIA_ROOT=str(self.media), POMPEY_DATA=str(self.root/'policy'),
                          QBIT_URL=self.url, SONARR_URL=self.spec['sonarrUrl'])
        self.secrets = self.root/'secrets.json'
        self.secrets.write_text(json.dumps({'sonarr_api_key': 'a'*32, 'radarr_api_key': 'a'*32,
            'prowlarr_api_key': 'a'*32, 'qbit_pbkdf2': base64.b64encode(b'fixture-salt').decode() + ':' + base64.b64encode(hashlib.pbkdf2_hmac('sha512', b'fixture', b'fixture-salt', 100000)).decode(),
            'qbit_user': 'fixture', 'qbit_password': 'fixture'}))

    def call(self, path, data=None):
        return api.http('GET' if data is None else 'POST', self.url+'/api/v2/'+path,
                        None if data is None else urllib.parse.urlencode(data).encode())

    def wait(self, predicate, message, seconds=45):
        until = time.monotonic()+seconds
        while time.monotonic() < until:
            if predicate(): return
            time.sleep(.25)
        raise AssertionError(message)

    def start_qbit(self, policy, write=True):
        verify_isolation(self.host_namespace)
        os.environ['AFTER_DOWNLOAD'] = policy
        if write:
            subprocess.run([sys.executable, str(ROOT/'pompey/rootfs/usr/local/bin/write_engine_configs.py'),
                            str(self.secrets), str(self.media)], check=True)
            for path in (self.config/'qBittorrent').rglob('qBittorrent.conf'):
                text = path.read_text().replace('=wg0', '=lo')
                text = text.replace('[BitTorrent]', '[BitTorrent]\nSession\\DHTEnabled=false\nSession\\PeXEnabled=false\nSession\\LSDEnabled=false')
                path.write_text(text)
        log = (self.root/'qbit.log').open('a')
        self.qbit = subprocess.Popen([self.spec['qbit'], '--profile='+str(self.config), '--webui-port=8080', '--confirm-legal-notice'], stdout=log, stderr=subprocess.STDOUT)
        log.close()
        def ready():
            if self.qbit.poll() is not None: raise AssertionError((self.root/'qbit.log').read_text())
            try: return bool(self.call('app/version'))
            except RuntimeError: return False
        self.wait(ready, 'qBittorrent did not start')
        print('Downloader contract:', self.call('app/version'), 'API', self.call('app/webapiVersion'), flush=True)
        prefs = self.call('app/preferences')
        assert not any(prefs[k] for k in ['dht', 'pex', 'lsd', 'upnp']), prefs
        return prefs

    def stop(self, name):
        proc = getattr(self, name)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try: proc.wait(15)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        setattr(self, name, None)

    def assert_policy(self, prefs, policy):
        ratio = {'stop_sharing': 0, 'share_to_ratio': 1, 'share_one_day': -1}[policy]
        minutes = 1440 if policy == 'share_one_day' else -1
        assert prefs['max_ratio'] == ratio, prefs
        assert prefs['max_seeding_time'] == minutes, prefs
        assert prefs['max_ratio_act'] == 0, prefs

    def command(self, body):
        command = http('POST', self.base+'/command', body)
        def completed():
            row = http('GET', self.base+'/command/'+str(command['id']))
            assert row['status'] != 'failed', row
            return row['status'] == 'completed'
        self.wait(completed, 'Sonarr command stalled')

    def run(self):
        # Configuration must be interpreted and survive restart for every policy.
        for policy in ['stop_sharing', 'share_to_ratio', 'share_one_day']:
            self.assert_policy(self.start_qbit(policy), policy)
            self.stop('qbit')
            self.assert_policy(self.start_qbit(policy, write=False), policy)
            # Exercise the same live preference API used by normal reconciliation.
            for live_policy in ['share_one_day', 'stop_sharing', 'share_to_ratio']:
                self.call('app/setPreferences', {'json': json.dumps(api.qbit_seed_preferences(live_policy))})
                self.assert_policy(self.call('app/preferences'), live_policy)
            self.stop('qbit')
        self.start_qbit('share_to_ratio')
        complete = self.media/'downloads/complete'; complete.mkdir(parents=True, exist_ok=True)
        folder = complete/'Planet.Earth.2006.S01E01.1080p.WEB-DL'; folder.mkdir()
        video = folder/(folder.name+'.mkv')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=black:s=1920x1080:r=1/120',
                        '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '3600', '-c:v', 'ffv1', '-c:a', 'flac', '-y', str(video)], check=True)
        with video.open('r+b') as out: out.truncate(64*1024*1024)
        subtitle = video.with_suffix('.en.srt'); subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\nSynthetic subtitle\n')
        metadata, digest = torrent_metadata(folder)
        self.call('torrents/createCategory', {'category': 'sonarr', 'savePath': str(complete)})
        assert self.call('torrents/categories')['sonarr']['savePath'].rstrip('/') == str(complete)
        boundary = 'pompeyFixture'
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="fixture.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode()+metadata+b'\r\n')
        for key, value in {'savepath': str(complete), 'category': 'sonarr', 'stopped': 'true', 'autoTMM': 'false'}.items():
            data += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        data += f'--{boundary}--\r\n'.encode()
        req = urllib.request.Request(self.url+'/api/v2/torrents/add', data=data, headers={'Content-Type': 'multipart/form-data; boundary='+boundary})
        with urllib.request.urlopen(req, timeout=10) as response:
            result = response.read()
            if result != b'Ok.':
                outcome = json.loads(result)
                assert outcome['failure_count'] == 0 and outcome['success_count'] == 1, outcome
                assert digest in outcome['added_torrent_ids'], outcome
        self.call('torrents/recheck', {'hashes': digest})
        self.wait(lambda: bool(self.call('torrents/info')) and self.call('torrents/info')[0]['progress'] == 1, 'Local payload failed recheck')
        self.call('torrents/start', {'hashes': digest})
        self.wait(lambda: self.call('torrents/info')[0]['state'] in {'uploading', 'stalledUP', 'queuedUP'}, 'Expected sharing state')
        properties = self.call('torrents/properties?hash='+digest)
        assert properties['seeding_time'] >= 0
        log = (self.root/'sonarr-isolated.log').open('w')
        self.sonarr = subprocess.Popen([self.spec['sonarr'], '-nobrowser', '-data='+str(self.config/'sonarr')], stdout=log, stderr=subprocess.STDOUT); log.close()
        def ready():
            try: return bool(http('GET', self.base+'/system/status'))
            except OSError: return False
        self.wait(ready, 'Sonarr did not start offline')
        client = next(c for c in http('GET', self.base+'/downloadclient/schema') if c['implementation']=='QBittorrent')
        client = wire_stack.apply_download_client(client, {'host': '127.0.0.1', 'port': 8080, 'tvCategory': 'sonarr', 'username': 'fixture', 'password': 'fixture', 'useSsl': False}, True)
        client.update(name='Isolated real downloader', priority=1)
        http('POST', self.base+'/downloadclient', client)
        self.command({'name': 'RefreshMonitoredDownloads'})
        self.wait(lambda: bool(http('GET', self.base+'/episodefile?seriesId='+str(self.spec['seriesId']))), 'Arr did not import real completed torrent')
        assert video.exists(), 'Below-goal payload must remain available for sharing'
        assert self.call('torrents/info'), 'Below-goal torrent must remain registered'
        files = http('GET', self.base+'/episodefile?seriesId='+str(self.spec['seriesId']))
        library = Path(http('GET', self.base+'/series/'+str(self.spec['seriesId']))['path'])
        imported = library/files[0]['relativePath']
        assert imported.read_bytes() == video.read_bytes()
        assert list(library.rglob('*.srt')), 'Subtitle not imported'
        self.call('app/setPreferences', {'json': json.dumps(api.qbit_seed_preferences('stop_sharing'))})
        self.wait(lambda: self.call('torrents/info')[0]['state'] == 'stoppedUP', 'Stop-sharing goal did not stop completed torrent')
        for _ in range(15):
            self.command({'name': 'RefreshMonitoredDownloads'})
            if not self.call('torrents/info'): break
            time.sleep(1)
        assert not self.call('torrents/info'), 'Arr did not remove imported torrent'
        assert not video.exists(), 'Arr did not remove completed download payload'
        assert imported.is_file(), 'Removing download damaged the library'
        print('Real qBittorrent: policies, restart, local completion, sharing retention, subtitles and Arr cleanup passed', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host-namespace', required=True); parser.add_argument('--uid', type=int, required=True)
    parser.add_argument('--gid', type=int, required=True); parser.add_argument('--probe', action='store_true')
    parser.add_argument('--scenario', type=Path)
    args = parser.parse_args()
    verify_isolation(args.host_namespace)
    if args.uid == 0:
        raise RuntimeError('Run the downloader suite as an unprivileged user with passwordless sudo')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    # The client cannot create interfaces or rejoin another namespace.
    os.setgroups([]); os.setgid(args.gid); os.setuid(args.uid)
    verify_isolation(args.host_namespace)
    if args.probe: return
    if not args.scenario: parser.error('--scenario is required')
    scenario = Scenario(args.scenario, args.host_namespace)
    try: scenario.run()
    except BaseException:
        for name in ['qbit.log', 'sonarr-isolated.log']:
            log = scenario.root/name
            if log.exists(): print(log.read_text()[-4000:], file=sys.stderr)
        raise
    finally: scenario.stop('sonarr'); scenario.stop('qbit')


if __name__ == '__main__': main()
