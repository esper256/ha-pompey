#!/usr/bin/env python3
"""Real Sonarr decisions and search fan-out against local realistic catalogues."""
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urlencode
from pathlib import Path
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from test_arr_integration import RealArrTestCase, http, ROOT
from engine_runtime import artifact
import anime_indexer
import fake_source
import recyclarr_sync
import wire_stack
import media_policy
import pompey_common as api


def known_policy_gap(test):
    return test if os.environ.get('POMPEY_ANIME_STRICT')=='1' else unittest.expectedFailure(test)


class AnimeIntegration(RealArrTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base=cls.urls['Sonarr']+'/api/v3'
        cls.version=http('GET',cls.base+'/system/status')['version']
        cls.catalogues=[]
        cls.fixture=json.loads((ROOT/'tests/fixtures/anime/world_trigger.json').read_text())
        cls.reports=[]
        report=Path(os.environ.get('POMPEY_ANIME_REPORT','/tmp/pompey-anime-report.json'))
        cls.report_path=report
        report.parent.mkdir(parents=True,exist_ok=True)
        def save_report():
            report.write_text(json.dumps(cls.reports,indent=2)+'\n')
            shutil.copytree(cls.root/'Sonarr/logs',report.with_suffix('.logs'),dirs_exist_ok=True)
        cls.addClassCleanup(save_report)
        media=cls.root/'media';media.mkdir()
        http('POST',cls.base+'/rootfolder',{'path':str(media)})
        with patch.dict(os.environ,MEDIA_ROOT=str(media)):
            wire_stack.ensure_media_management(cls.base,'a'*32,'sonarr')
        matches=http('GET',cls.base+'/series/lookup?term=tvdb:283934')
        show=next(s for s in matches if s['title'].lower()=='world trigger')
        show.update(qualityProfileId=http('GET',cls.base+'/qualityprofile')[0]['id'],rootFolderPath=str(media),
                    seriesType='anime',monitored=True,seasonFolder=True,addOptions={'searchForMissingEpisodes':False})
        for season in show['seasons']:season['monitored']=season['seasonNumber']==3
        cls.show=http('POST',cls.base+'/series',show)
        for _ in range(120):
            cls.episodes=[e for e in http('GET',cls.base+'/episode?seriesId='+str(cls.show['id'])) if e['seasonNumber']==3]
            if len(cls.episodes)==14:break
            time.sleep(.5)
        if len(cls.episodes)!=14:raise RuntimeError('World Trigger metadata did not provide 14 season-three episodes')
        # Query-routing hints only: Sonarr still parses every original title.
        for row in cls.fixture:
            title=row['title']
            season=re.search(r'(?:S0?([123])(?=E|\b)|(?:Season\s*0?([123]))|([123])(?:st|nd|rd) Season)',title,re.I)
            row['season']=int(next(g for g in season.groups() if g)) if season and not re.search(r'S0?[123]-S0?[123]',title,re.I) else None
            episode=re.search(r'S0?3E(\d+)|(?:S3|3rd Season)\s*-\s*(\d+)(?!\d|\s*[~\-]\s*\d)',title,re.I)
            row['episode']=int(next(g for g in episode.groups() if g)) if episode and not any(x in title.lower() for x in ['batch','01 ~ 14']) else None
            match=next((e for e in cls.episodes if e['episodeNumber']==row['episode']),None)
            if match:row['absolute']=match.get('absoluteEpisodeNumber')
        cls.dual=next(r for r in cls.fixture if r['title']=='World Trigger S03 (WEBRip 1080p x265 HEVC AAC + AC3) (Dual Audio) [S1PH3R]')
        cls.captured_dual=dict(cls.dual)
        cls.single=dict(cls.dual,title='World Trigger S03 (WEBRip 1080p x265 HEVC AAC) [Fixture]',synthetic=True)
        # Matched quality/size isolates audio preference from Sonarr's quality ranking.
        # The untouched SubsPlease batch remains in the full-catalogue probe.
        data=cls.root/'recyclarr';data.mkdir()
        secret=data/'secrets.json';secret.write_text(json.dumps({'radarr_api_key':'a'*32,'sonarr_api_key':'a'*32}))
        with patch.dict(os.environ,POMPEY_RECYCLARR=str(artifact('recyclarr')/'recyclarr'),POMPEY_RECYCLARR_DATA=str(data),POMPEY_SECRETS=str(secret),RADARR_URL=cls.urls['Radarr'],SONARR_URL=cls.urls['Sonarr']):
            if recyclarr_sync.main():raise RuntimeError('Production quality profiles failed')
        cls.profile=next(p for p in http('GET',cls.base+'/qualityprofile') if p['name']=='Default')
        cls.show=http('GET',cls.base+'/series/'+str(cls.show['id']))
        cls.show['qualityProfileId']=cls.profile['id'];http('PUT',cls.base+'/series/'+str(cls.show['id']),cls.show)
        for number in range(2):
            catalogue=anime_indexer.Catalogue();catalogue.reset(cls.fixture)
            server=ThreadingHTTPServer(('127.0.0.1',0),catalogue.handler())
            cls.addClassCleanup(server.server_close);cls.addClassCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever,daemon=True).start();cls.catalogues.append(catalogue)
            indexer=next(i for i in http('GET',cls.base+'/indexer/schema') if i['implementation']=='Torznab')
            indexer.update(name=f'Anime fixture {number}',enableRss=False,enableAutomaticSearch=True,enableInteractiveSearch=True)
            wire_stack.set_app_fields(indexer,{'baseUrl':f'http://127.0.0.1:{server.server_port}', 'apiPath':'/api','apiKey':'fixture','categories':[5070,5040],'animeCategories':[5070], 'animeStandardFormatSearch':True,'minimumSeeders':1})
            http('POST',cls.base+'/indexer',indexer)
        cls.client=fake_source.FakeState(cls.root/'client',media,False)
        server=ThreadingHTTPServer(('127.0.0.1',0),fake_source.qbit_handler(cls.client))
        cls.addClassCleanup(server.server_close);cls.addClassCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        client=next(c for c in http('GET',cls.base+'/downloadclient/schema') if c['implementation']=='QBittorrent')
        client=wire_stack.apply_download_client(client,{'host':'127.0.0.1','port':server.server_port,'tvCategory':'sonarr','username':'test','password':'test','useSsl':False},True)
        cls.qurl=f'http://127.0.0.1:{server.server_port}'
        client.update(name='HTTP fixture',priority=1);http('POST',cls.base+'/downloadclient',client)

    def setUp(self):
        self.client.delete_torrents('|'.join(self.client.torrents),'true')
        self.add_offset=len(self.client.adds_path.read_text().splitlines())
        self.command({'name':'RefreshMonitoredDownloads'})
        for catalogue in self.catalogues:catalogue.reset([])
        if self._testMethodName in {'test_captured_healthy_dual_pack_is_usable','test_completed_season_search_has_bounded_indexer_requests'}:
            cls=type(self)
            if not hasattr(cls,'baseline'):
                cls.baseline=self.search(self.fixture)
            self.baseline_decisions,self.baseline_requests=cls.baseline
            self.assertTrue(self.baseline_decisions,'Catalogue search returned no decisions')
            self.assertTrue(any(d['title']==self.captured_dual['title'] for d in self.baseline_decisions))

    def clear_library(self):
        # Keep scenarios independent even when the runner changes test order.
        files=http('GET',self.base+'/episodefile?seriesId='+str(self.show['id']))
        for item in files:http('DELETE',self.base+'/episodefile/'+str(item['id']))
        self.assertFalse(any(e.get('hasFile') for e in http('GET',self.base+'/episode?seriesId='+str(self.show['id']))))

    def command(self, payload):
        command=http('POST',self.base+'/command',payload)
        for _ in range(180):
            state=http('GET',self.base+'/command/'+str(command['id']))
            if state['status'] in {'completed','failed'}:break
            time.sleep(.5)
        self.assertEqual(state['status'],'completed',state)

    def rows(self, *packs, live_episodes=False):
        result=[dict(r) for r in self.fixture if r.get('episode') is not None]
        if not live_episodes:
            for row in result:row['seeders']=0
        for name in packs:
            row=dict(self.single if name=='single-pack' else self.dual)
            row['scenario_id']=name
            if name=='phantom-pack':row.update(seeders=0,leechers=2)
            result.append(row)
        return result

    def search(self, rows, automatic=False):
        for catalogue in self.catalogues:catalogue.reset(rows)
        decisions=[]
        started=time.monotonic()
        try:
            if automatic:
                command=http('POST',self.base+'/command',{'name':'SeasonSearch','seriesId':self.show['id'],'seasonNumber':3})
                for _ in range(2400):
                    state=http('GET',self.base+'/command/'+str(command['id']))
                    if state['status'] in {'completed','failed'}:break
                    time.sleep(.25)
                self.assertEqual(state['status'],'completed',state)
                decisions=[]
            else:
                decisions=http('GET',self.base+f'/release?seriesId={self.show["id"]}&seasonNumber=3',timeout=600)
        finally:
            ledger=[{'indexer':i,**r} for i,c in enumerate(self.catalogues) for r in c.snapshot()]
            requests=[r for r in ledger if r['query'].get('t')!=['caps']]
            report={'test':self._testMethodName,'requests':requests,'request_count':len(requests),'capability_requests':len(ledger)-len(requests),
                    'grabs':[r['name'] for r in self.client.list_torrents()],
                    'add_requests':len(self.client.adds_path.read_text().splitlines())-self.add_offset,
                    'decisions':[{'title':d['title'],'approved':d.get('approved'),'rejections':d.get('rejections'),'score':d.get('customFormatScore'),'fullSeason':d.get('fullSeason'),'seeders':d.get('seeders')} for d in decisions]}
            report['episode_requests']=sum(bool(r['query'].get('ep')) or bool(re.search(r'(?:^|\s)\d+$',r['query'].get('q',[''])[0])) for r in requests)
            report['sonarr_version']=self.version
            self.reports.append(report)
            print(json.dumps({k:v for k,v in report.items() if k not in {'requests','decisions'}}),flush=True)
            report['elapsed_seconds']=round(time.monotonic()-started,2)
            self.report_path.write_text(json.dumps(self.reports,indent=2)+'\n')
        return decisions, requests

    def test_zero_seed_dual_pack_is_rejected(self):
        decisions,_=self.search(self.rows('phantom-pack','single-pack'))
        phantom=[d for d in decisions if d['title']==self.dual['title']]
        self.assertTrue(phantom,decisions)
        self.assertTrue(all(not d['approved'] for d in phantom),phantom)
        self.assertTrue(all(any('seed' in reason.lower() for reason in d['rejections']) for d in phantom),phantom)
        self.assertTrue(any(d['approved'] and d['title']==self.single['title'] for d in decisions),decisions)

    def test_live_dual_pack_beats_single_pack_and_individual_episodes(self):
        self.search(self.rows('dual-pack','single-pack',live_episodes=True),automatic=True)
        grabs=self.client.list_torrents()
        self.assertEqual(len(grabs),1,grabs)
        self.assertEqual(len(self.client.adds_path.read_text().splitlines())-self.add_offset,1,'Duplicate downloader add requests')
        self.assertEqual(anime_indexer.digest(self.dual),grabs[0]['hash'])

    def test_healthy_dual_pack_beats_single_audio_pack(self):
        self.search(self.rows('dual-pack','single-pack'),automatic=True)
        grabs=self.client.list_torrents()
        self.assertEqual(len(grabs),1,grabs)
        self.assertEqual(len(self.client.adds_path.read_text().splitlines())-self.add_offset,1,'Duplicate downloader add requests')
        self.assertEqual(anime_indexer.digest(self.dual),grabs[0]['hash'])

    def test_captured_healthy_dual_pack_is_usable(self):
        choices=[d for d in self.baseline_decisions if d['title']==self.captured_dual['title']]
        self.assertTrue(any(d['approved'] for d in choices),choices)

    @known_policy_gap
    def test_completed_season_search_has_bounded_indexer_requests(self):
        requests=self.baseline_requests
        # Four naming/ID variants with two pages each are sufficient for packs.
        # Episode-by-episode fan-out is outside this completed-season budget.
        self.assertLessEqual(len(requests),8*len(self.catalogues),requests)

    def test_standard_tv_keeps_quality_size_and_language_gates(self):
        show=http('GET',self.base+'/series/lookup?term=tvdb:79257')[0]
        show.update(qualityProfileId=self.profile['id'],rootFolderPath=str(self.root/'media'),seriesType='standard',
                    monitored=True,seasonFolder=True,addOptions={'searchForMissingEpisodes':False})
        saved=http('POST',self.base+'/series',show)
        self.addCleanup(http,'DELETE',self.base+'/series/'+str(saved['id'])+'?deleteFiles=true')
        for _ in range(120):
            episodes=http('GET',self.base+'/episode?seriesId='+str(saved['id']))
            if any(e['seasonNumber']==1 for e in episodes):break
            time.sleep(.5)
        cases=[('Planet Earth S01 1080p WEB-DL x265 [Fixture]',1024**3*11,True),
               ('Planet Earth S01 1080p WEB-DL x265 [Tiny]',1024**2,False),
               ('Planet Earth S01 480p HDTV H264 [Fixture]',1024**3,False),
               ('Planet Earth S01 1080p WEB-DL FRENCH H264 [Fixture]',1024**3*11,False),
               ('Planet Earth S01 1080p BluRay REMUX H264 [Fixture]',1024**3*200,False)]
        rows=[dict(self.dual,title=title,size=size,season=1,episode=None,synthetic=True) for title,size,_ in cases]
        for catalogue in self.catalogues:catalogue.reset(rows)
        decisions=http('GET',self.base+f'/release?seriesId={saved["id"]}&seasonNumber=1',timeout=120)
        for title,_,approved in cases:
            with self.subTest(title=title):
                matches=[d for d in decisions if d['title']==title]
                self.assertTrue(matches,decisions)
                self.assertTrue(all(d['approved']==approved for d in matches),matches)

    def test_complete_season_imports_subtitles_and_cleans_downloads(self):
        """Real Sonarr CDH imports fourteen generated episodes; no manual import API."""
        self.addCleanup(self.clear_library)
        magnet='magnet:?'+urlencode({'xt':'urn:btih:'+anime_indexer.digest(self.dual),'dn':self.dual['title'],'tr':'udp://127.0.0.1:9'},safe=':')
        item=self.client.record_add({'urls':magnet,'category':'sonarr'})
        folder=Path(item['content_path'])
        for path in folder.glob('*.mkv'):path.unlink()
        template=self.root/'episode.mkv'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=black:s=1920x1080:r=1/120',
                        '-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','1440','-c:v','ffv1','-c:a','flac','-y',str(template)],check=True)
        for number in range(1,15):
            target=folder/f'World Trigger S03E{number:02d} 1080p WEBRip [S1PH3R].mkv'
            shutil.copyfile(template,target)
            with target.open('r+b') as stream:stream.truncate(128*1024*1024)
            target.with_suffix('.en.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\nFixture subtitles\n')
        (folder/'release.txt').write_text('Synthetic season pack; no downloaded media.\n')
        self.command({'name':'RefreshMonitoredDownloads'})
        self.assertFalse(any(e.get('hasFile') for e in http('GET',self.base+'/episode?seriesId='+str(self.show['id']))))
        self.assertEqual(len(list(folder.glob('*.mkv'))),14)
        self.client.finish_torrent(item['hash'])
        with patch.dict(os.environ,MEDIA_ROOT=str(self.root/'media'),QBIT_URL=self.qurl,AFTER_DOWNLOAD='stop_sharing'),patch.object(api,'load_secrets',return_value={}):
            media_policy.maintain_downloads()
        for _ in range(20):
            self.command({'name':'RefreshMonitoredDownloads'})
            episodes=[e for e in http('GET',self.base+'/episode?seriesId='+str(self.show['id'])) if e['seasonNumber']==3]
            if all(e.get('hasFile') for e in episodes) and not self.client.list_torrents():break
            time.sleep(1)
        self.assertEqual(sum(bool(e.get('hasFile')) for e in episodes),14)
        library=Path(self.show['path'])
        self.assertEqual(len(list(library.rglob('*.mkv'))),14)
        self.assertEqual(len(list(library.rglob('*.srt'))),14)
        self.assertFalse(self.client.list_torrents())
        self.assertFalse(any(p.is_file() for p in (self.root/'media/downloads').rglob('*')))
        # Repeat ordinary maintenance after import: no new search or duplicate grab.
        for _ in range(2):self.command({'name':'RefreshMonitoredDownloads'})
        self.assertFalse(any(c.snapshot() for c in self.catalogues))
        # A completed library must not be replaced just to regain the pack score.
        before={e['id']:e['episodeFileId'] for e in episodes}
        for catalogue in self.catalogues:catalogue.reset(self.rows('dual-pack',live_episodes=True))
        indexers=http('GET',self.base+'/indexer')
        for indexer in indexers:
            self.addCleanup(http,'PUT',self.base+'/indexer/'+str(indexer['id']),dict(indexer))
            http('PUT',self.base+'/indexer/'+str(indexer['id']),dict(indexer,enableRss=True))
        add_count=len(self.client.adds_path.read_text().splitlines())
        for _ in range(2):self.command({'name':'RssSync'})
        self.assertTrue(any(c.snapshot() for c in self.catalogues),'RSS replay must reach the fixture')
        self.assertEqual(len(self.client.adds_path.read_text().splitlines()),add_count,'Pack bonus triggered another download')
        after={e['id']:e['episodeFileId'] for e in http('GET',self.base+'/episode?seriesId='+str(self.show['id'])) if e['seasonNumber']==3}
        self.assertEqual(after,before)
        # Max must keep a satisfactory 1080p fallback without score churn,
        # but still accept an actual resolution upgrade when 2160p appears.
        original=http('GET',self.base+'/series/'+str(self.show['id']))
        self.addCleanup(http,'PUT',self.base+'/series/'+str(self.show['id']),original)
        maximum=next(p for p in http('GET',self.base+'/qualityprofile') if p['name']=='Max')
        http('PUT',self.base+'/series/'+str(self.show['id']),dict(original,qualityProfileId=maximum['id']))
        self.command({'name':'RssSync'})
        self.assertEqual(len(self.client.adds_path.read_text().splitlines()),add_count,'Max redownloaded its 1080p fallback')
        upgrade=dict(self.dual,title=self.dual['title'].replace('1080p','2160p'),size=28*1024**3,synthetic=True)
        for catalogue in self.catalogues:catalogue.reset([upgrade])
        self.addCleanup(self.client.delete_torrents,anime_indexer.digest(upgrade),'true')
        self.command({'name':'RssSync'})
        grabs=self.client.list_torrents()
        self.assertEqual([g['hash'] for g in grabs],[anime_indexer.digest(upgrade)],'Max failed to accept a resolution upgrade')
        self.reports.append({'test':self._testMethodName,'imported_episodes':14,'imported_subtitles':14,
                             'remaining_downloads_after_import':0,'maintenance_indexer_requests':0,'resolution_upgrade_grabs':1})


if __name__=='__main__':
    try:
        unittest.main()
    finally:
        AnimeIntegration.doClassCleanups()
