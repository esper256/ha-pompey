#!/usr/bin/env python3
"""Real Arr APIs and file imports with a fake download client; no torrent executable."""
import json
import os
from pathlib import Path
import socket
import threading
from http.server import ThreadingHTTPServer
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests/lib'),str(ROOT/'pompey/rootfs/usr/local/bin')]
from engine_runtime import artifact
import fake_source
import media_policy
import pompey_common as api
import wire_stack


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def http(method,url,body=None,timeout=30):
    req=urllib.request.Request(url,method=method,data=None if body is None else json.dumps(body).encode(),
                               headers={'X-Api-Key':'a'*32,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            raw=response.read();return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body=exc.read().decode()
        exc.close()
        raise RuntimeError(f'{method} {url}: {exc.code} {body}') from None


@unittest.skipUnless(os.environ.get('POMPEY_REAL_ENGINES')=='1','set POMPEY_REAL_ENGINES=1 for real Arr integration')
class RealArrTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='pompey-real-arr-');cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name);cls.urls={};cls.procs=[]
        for name in ['Radarr','Sonarr','Prowlarr']:
            config=cls.root/name;config.mkdir();number=port()
            (config/'config.xml').write_text(f'<Config><LogLevel>Debug</LogLevel><Port>{number}</Port><BindAddress>127.0.0.1</BindAddress><ApiKey>{"a"*32}</ApiKey><AuthenticationMethod>None</AuthenticationMethod><AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired><UpdateMechanism>Docker</UpdateMechanism></Config>')
            log=(config/'process.log').open('w');cls.addClassCleanup(log.close)
            proc=subprocess.Popen([str(artifact(name)/name),'-nobrowser','-data='+str(config)],stdout=log,stderr=subprocess.STDOUT)
            cls.procs.append(proc)
            cls.addClassCleanup(cls.stop,proc)
            url='http://127.0.0.1:'+str(number);cls.urls[name]=url
            for _ in range(120):
                if proc.poll() is not None:raise RuntimeError((config/'process.log').read_text()[-4000:])
                try:
                    http('GET',url+'/ping');break
                except OSError:time.sleep(.5)
            else:raise RuntimeError(name+' did not start')

    @staticmethod
    def stop(proc):
        proc.terminate()
        try:proc.wait(20)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()


class ArrIntegration(RealArrTestCase):
    def test_configuration_converges_on_real_arr(self):
        for kind in ['Radarr','Sonarr']:
            base=self.urls[kind]+'/api/v3'
            with patch.dict(os.environ,{'MEDIA_ROOT':str(self.root/'media')}):
                wire_stack.ensure_media_management(base,'a'*32,kind.lower())
                before=http('GET',base+'/config/mediamanagement')
                wire_stack.ensure_media_management(base,'a'*32,kind.lower())
                after=http('GET',base+'/config/mediamanagement')
            self.assertEqual(before,after)
            self.assertTrue(after['skipFreeSpaceCheckWhenImporting'])
            self.assertTrue(after['recycleBin'])
            if kind=='Sonarr':self.assertEqual(after['downloadPropersAndRepacks'],'doNotPrefer')

    def test_prowlarr_repairs_sync_mode_on_real_api(self):
        prowlarr = self.urls['Prowlarr']
        endpoint = prowlarr+'/api/v1/applications'
        with patch.dict(os.environ, PROWLARR_URL=prowlarr):
            for kind in ['Radarr', 'Sonarr']:
                wire_stack.ensure_prowlarr_app(prowlarr,'a'*32,kind,kind,self.urls[kind],'a'*32)
                app = next(a for a in http('GET',endpoint) if a['name']==kind)
                for field in ['syncLevel', 'apiKey']:
                    app = next(a for a in http('GET',endpoint) if a['name']==kind)
                    if field == 'syncLevel': app[field] = 'disabled'
                    else: wire_stack.set_app_fields(app, {'apiKey':'b'*32})
                    http('PUT',endpoint+'/'+str(app['id'])+'?forceSave=true',app)
                    wire_stack.ensure_prowlarr_app(prowlarr,'a'*32,kind,kind,self.urls[kind],'a'*32)
                    repaired = next(a for a in http('GET',endpoint) if a['id']==app['id'])
                    self.assertTrue(wire_stack.prowlarr_app_matches(
                        repaired, wire_stack.prowlarr_app_values(kind,self.urls[kind],'a'*32)))
                    http('POST',endpoint+'/test',repaired)
                    wire_stack.ensure_prowlarr_app(prowlarr,'a'*32,kind,kind,self.urls[kind],'a'*32)
                    self.assertEqual(repaired,next(a for a in http('GET',endpoint) if a['id']==app['id']))

    def test_real_multi_episode_import_with_subtitles(self):
        base=self.urls['Sonarr']+'/api/v3'
        root=self.root/'media/tv';root.mkdir(parents=True,exist_ok=True)
        http('POST',base+'/rootfolder',{'path':str(root)})
        show=http('GET',base+'/series/lookup?term=tvdb:79257')[0]
        show.update(qualityProfileId=http('GET',base+'/qualityprofile')[0]['id'],rootFolderPath=str(root),
                    monitored=True,seasonFolder=True,addOptions={'searchForMissingEpisodes':False})
        saved=http('POST',base+'/series',show)
        for _ in range(120):
            episodes=http('GET',base+'/episode?seriesId='+str(saved['id']))
            selected=[e for e in episodes if e['seasonNumber']==1 and e['episodeNumber'] in {1,2}]
            if len(selected)==2:break
            time.sleep(.5)
        self.assertEqual(len(selected),2)
        fake=fake_source.FakeState(self.root/'tv-client',self.root/'media',False)
        server=ThreadingHTTPServer(('127.0.0.1',0),fake_source.qbit_handler(fake))
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        qurl='http://127.0.0.1:'+str(server.server_port)
        client=next(c for c in http('GET',base+'/downloadclient/schema') if c['implementation']=='QBittorrent')
        client=wire_stack.apply_download_client(client,{'host':'127.0.0.1','port':server.server_port,
                    'tvCategory':'sonarr','username':'test','password':'test','useSsl':False},True)
        client.update(name='HTTP fixture',priority=1)
        http('POST',base+'/downloadclient',client)
        name='Planet.Earth.2006.S01E01E02.720p.WEB-DL';digest='3'*40
        item=fake.record_add({'urls':'magnet:?xt=urn:btih:'+digest+'&dn='+name,'category':'sonarr'})
        source=Path(item['content_path'])/(name+'.mkv')
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=black:s=1280x720:r=1/120',
                        '-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','6600','-c:v','ffv1','-c:a','flac','-y',str(source)],check=True)
        with source.open('r+b') as out:out.truncate(256*1024*1024)
        source.with_suffix('.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\nFixture subtitle\n')
        self.command(base,{'name':'RefreshMonitoredDownloads'})
        self.assertTrue(source.exists())
        self.assertFalse(any(e.get('hasFile') for e in http('GET',base+'/episode?seriesId='+str(saved['id']))))
        item=fake.finish_torrent(digest);source=Path(item['content_path'])/source.name
        with patch.dict(os.environ,{'MEDIA_ROOT':str(self.root/'media'),'QBIT_URL':qurl,'AFTER_DOWNLOAD':'stop_sharing'}),patch.object(api,'load_secrets',return_value={}):
            media_policy.maintain_downloads()
        for _ in range(12):
            self.command(base,{'name':'RefreshMonitoredDownloads'})
            selected=[http('GET',base+'/episode/'+str(e['id'])) for e in selected]
            if all(e.get('hasFile') for e in selected):break
            time.sleep(1)
        self.assertTrue(all(e.get('hasFile') for e in selected),(self.root/'Sonarr/logs/sonarr.debug.txt').read_text()[-7000:])
        self.assertEqual(selected[0]['episodeFileId'],selected[1]['episodeFileId'])
        self.assertFalse(source.exists())
        self.assertTrue(list(Path(saved['path']).rglob('*.srt')))

    def test_recyclarr_configures_real_profiles(self):
        import recyclarr_sync
        data=self.root/'policy';data.mkdir(exist_ok=True)
        secrets=data/'secrets.json';secrets.write_text(json.dumps({'radarr_api_key':'a'*32,'sonarr_api_key':'a'*32}))
        with patch.dict(os.environ,{'POMPEY_RECYCLARR':str(artifact('recyclarr')/'recyclarr'),
                        'POMPEY_RECYCLARR_DATA':str(data/'recyclarr'),'POMPEY_SECRETS':str(secrets),
                        'RADARR_URL':self.urls['Radarr'],'SONARR_URL':self.urls['Sonarr']}):
            code=recyclarr_sync.main()
            if code:
                for log in (data/'recyclarr').rglob('*.log'):
                    print(log.read_text()[-12000:])
            self.assertEqual(code,0)
        for kind in ['Radarr','Sonarr']:
            profiles=http('GET',self.urls[kind]+'/api/v3/qualityprofile')
            by_name={p['name']:p for p in profiles}
            self.assertTrue({'Default','Max'} <= by_name.keys())
            self.assertTrue(by_name['Default']['items'])
            self.assertTrue(http('GET',self.urls[kind]+'/api/v3/customformat'))
            if kind=='Sonarr':
                for name in ['Default','Max']:
                    profile=by_name[name]
                    scores={item['format']:item['score'] for item in profile['formatItems']}
                    formats=http('GET',self.urls[kind]+'/api/v3/customformat')
                    actual={item['name']:scores[item['id']] for item in formats}
                    self.assertEqual(actual['Season Pack'],recyclarr_sync.SEASON_PACK_SCORE)
                    self.assertEqual(actual['x265 (HD)'],0)
                    self.assertEqual(actual['x265 (no HDR/DV)'],0)
                    self.assertEqual(profile['cutoffFormatScore'],0)

    def command(self, base, payload):
        command = http('POST',base+'/command',payload)
        for _ in range(180):
            row = http('GET',base+'/command/'+str(command['id']))
            if row.get('status') in {'completed','failed'}: break
            time.sleep(.5)
        self.assertEqual(row['status'],'completed',row)

    def test_real_movie_import_and_upgrade(self):
        base=self.urls['Radarr']+'/api/v3'
        root=self.root/'media/movies';root.mkdir(parents=True,exist_ok=True)
        http('POST',base+'/rootfolder',{'path':str(root)})
        profiles=http('GET',base+'/qualityprofile')
        profile=profiles[0]
        profile.update(upgradeAllowed=True)
        last=profile['items'][-1]
        profile['cutoff']=last.get('id') or last['quality']['id']
        def allow(items):
            for item in items:
                item['allowed']=True
                allow(item.get('items',[]))
        allow(profile['items'])
        http('PUT',base+'/qualityprofile/'+str(profile['id']),profile)
        movie=http('GET',base+'/movie/lookup?term=tmdb:1184918')[0]
        movie.update(qualityProfileId=profile['id'],rootFolderPath=str(root),monitored=True,
                     addOptions={'searchForMovie':False})
        saved=http('POST',base+'/movie',movie);movie_id=saved['id']
        fake=fake_source.FakeState(self.root/'client',self.root/'media',False)
        server=ThreadingHTTPServer(('127.0.0.1',0),fake_source.qbit_handler(fake))
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        qurl='http://127.0.0.1:'+str(server.server_port)
        client=next(c for c in http('GET',base+'/downloadclient/schema') if c['implementation']=='QBittorrent')
        values={'host':'127.0.0.1','port':server.server_port,'movieCategory':'radarr','username':'test','password':'test','useSsl':False}
        client=wire_stack.apply_download_client(client,values,True)
        client.update(name='HTTP fixture',priority=1)
        http('POST',base+'/downloadclient',client)
        for number,quality in enumerate(['720p','1080p']):
            digest=str(number+1)*40
            name='The.Wild.Robot.2024.'+quality+'.WEB-DL'
            torrent=fake.record_add({'urls':'magnet:?xt=urn:btih:'+digest+'&dn='+name,'category':'radarr'})
            source=Path(torrent['content_path'])/(name+'.mkv')
            # Actual decodable synthetic media; never download copyrighted media.
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                            'color=black:s='+('1280x720' if quality=='720p' else '1920x1080')+':r=1/120',
                            '-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','6600','-c:v','ffv1','-c:a','flac','-y',str(source)],check=True)
            with source.open('r+b') as out:out.truncate(256*1024*1024)
            self.command(base,{'name':'RefreshMonitoredDownloads'})
            before=http('GET',base+'/movie/'+str(movie_id))
            self.assertEqual(before.get('hasFile'),bool(number))
            self.assertTrue(source.exists(),'incomplete download must stay outside the library')
            torrent=fake.finish_torrent(digest)
            source=Path(torrent['content_path'])/source.name
            with patch.dict(os.environ,{'MEDIA_ROOT':str(self.root/'media'),'QBIT_URL':qurl,'AFTER_DOWNLOAD':'stop_sharing'}),patch.object(api,'load_secrets',return_value={}):
                media_policy.maintain_downloads()
            self.assertTrue(source.exists(),'Pompey must leave the import to Arr')
            # Exercise native completed-download handling through the WebAPI
            # fixture. No ManualImport/DownloadedMoviesScan safety net.
            for _ in range(12):
                self.command(base,{'name':'RefreshMonitoredDownloads'})
                result=http('GET',base+'/movie/'+str(movie_id))
                if result.get('hasFile') and quality in result['movieFile']['quality']['quality']['name']:break
                time.sleep(1)
            if not result.get('hasFile'):
                self.fail(json.dumps(http('GET',base+'/queue?includeUnknownMovieItems=true')) + '\n' + (self.root/'Radarr/logs/radarr.debug.txt').read_text()[-10000:])
            path=Path(result['path'])/result['movieFile']['relativePath']
            self.assertTrue(path.exists())
            self.assertFalse(source.exists(),(self.root/'Radarr/logs/radarr.debug.txt').read_text()[-9000:])
            self.assertIn(quality,result['movieFile']['quality']['quality']['name'])


if __name__=='__main__':unittest.main()
