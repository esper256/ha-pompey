#!/usr/bin/env python3
"""Real Arr moves, season cancellation, and Prowlarr source synchronization."""
import json
import os
from pathlib import Path
import shutil
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from test_arr_integration import RealArrTestCase, http, ROOT
import anime_indexer
import fake_source
import pompey_common as api
import pompey_state as state
import request_policy
import route_rating
import wire_stack


class Orchestration(RealArrTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = cls.urls['Sonarr'] + '/api/v3'
        cls.media = cls.root / 'media'
        cls.media.mkdir()
        cls.env = patch.dict(os.environ, MEDIA_ROOT=str(cls.media), POMPEY_DATA=str(cls.root/'state'),
                             SONARR_URL=cls.urls['Sonarr'], RADARR_URL=cls.urls['Radarr'],
                             PROWLARR_URL=cls.urls['Prowlarr'])
        cls.env.start(); cls.addClassCleanup(cls.env.stop)
        root = Path(api.tv_auto_dir()); root.mkdir(parents=True)
        for folder in [root, Path(api.tv_dir()), Path(api.tv_kid_dir())]:
            folder.mkdir(parents=True, exist_ok=True)
            http('POST', cls.base+'/rootfolder', {'path': str(folder)})
        show = http('GET', cls.base+'/series/lookup?term=tvdb:283934')[0]
        show.update(qualityProfileId=http('GET', cls.base+'/qualityprofile')[0]['id'], rootFolderPath=str(root),
                    seriesType='anime', monitored=True, seasonFolder=True, addOptions={'searchForMissingEpisodes': False})
        cls.show = http('POST', cls.base+'/series', show)
        for _ in range(120):
            if len(http('GET', cls.base+'/episode?seriesId='+str(cls.show['id']))) >= 99: break
            time.sleep(.5)
        else: raise RuntimeError('World Trigger metadata unavailable')

    def setUp(self):
        started = time.monotonic()
        self.addCleanup(lambda: print(f"TIMING {self.id()}: {time.monotonic()-started:.2f}s", flush=True))

    def server(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f'http://127.0.0.1:{server.server_port}'

    def command(self, base, payload):
        command = http('POST', base+'/command', payload)
        for _ in range(1200):
            result = http('GET', base+'/command/'+str(command['id']))
            if result['status'] in {'completed', 'failed'}: break
            time.sleep(.5)
        self.assertEqual(result['status'], 'completed', result)

    def test_a_rating_move_preserves_payload_and_subtitles(self):
        title = http('GET', self.base+'/series/'+str(self.show['id']))
        source = Path(title['path']); source.mkdir(parents=True, exist_ok=True)
        (source/'fixture.mkv').write_bytes(b'synthetic move payload')
        (source/'fixture.en.srt').write_text('synthetic subtitle')
        route_rating.route_series('a'*32)
        for _ in range(120):
            route_rating.route_series('a'*32)
            record = state.load('routing-series').get(str(title['id']), {})
            if record and 'pending' not in record: break
            time.sleep(.25)
        self.assertNotIn('pending', record)
        moved = Path(http('GET', self.base+'/series/'+str(title['id']))['path'])
        self.assertNotEqual(moved, source)
        self.assertEqual((moved/'fixture.mkv').read_bytes(), b'synthetic move payload')
        self.assertEqual((moved/'fixture.en.srt').read_text(), 'synthetic subtitle')
        self.assertFalse(source.exists())
        shutil.rmtree(moved)  # Synthetic move files must not influence subsequent search.

    def test_b_seerr_season_snapshots_control_only_owned_seasons(self):
        # Use Seerr's paginated HTTP shape and real Sonarr monitoring updates.
        rows = [{'id': 1, 'type': 'tv', 'status': 2, 'media': {'tvdbId': 283934}, 'seasons': [{'seasonNumber': 1}, {'seasonNumber': 2}]},
                {'id': 2, 'type': 'tv', 'status': 2, 'media': {'tvdbId': 283934}, 'seasons': [{'seasonNumber': 2}]}]
        class SeerrFixture(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({'pageInfo': {'results': len(rows)}, 'results': rows}).encode())
        url = self.server(SeerrFixture)
        title = http('GET', self.base+'/series/'+str(self.show['id']))
        for season in title['seasons']: season['monitored'] = season['seasonNumber'] in {1, 2, 3}
        http('PUT', self.base+'/series/'+str(title['id']), title)
        with patch.dict(os.environ, SEERR_URL=url), patch.object(api, 'seerr_api_key_from_disk', return_value='fixture'), patch.object(api, 'load_secrets', return_value={'radarr_api_key': 'a'*32, 'sonarr_api_key': 'a'*32}):
            request_policy.reconcile_requests()
            rows.pop(0); request_policy.reconcile_requests()
            current = http('GET', self.base+'/series/'+str(title['id']))
            monitoring = {s['seasonNumber']: s['monitored'] for s in current['seasons']}
            self.assertFalse(monitoring[1]); self.assertTrue(monitoring[2]); self.assertTrue(monitoring[3])
            rows.clear(); request_policy.reconcile_requests()
            current = http('GET', self.base+'/series/'+str(title['id']))
            monitoring = {s['seasonNumber']: s['monitored'] for s in current['seasons']}
            self.assertFalse(monitoring[2]); self.assertTrue(monitoring[3]); self.assertTrue(current['monitored'])

    def test_c_search_through_real_prowlarr_is_bounded(self):
        catalogue = anime_indexer.Catalogue()
        row = next(r for r in json.loads((ROOT/'tests/fixtures/anime/world_trigger.json').read_text()) if r['title']=='World Trigger S03 (WEBRip 1080p x265 HEVC AAC + AC3) (Dual Audio) [S1PH3R]')
        full = os.environ.get('POMPEY_SEARCH_MODE') != 'pr'
        row = dict(row, season=3, episode=None, seeders=20)
        if not full:
            # Wiring needs a successful forwarded grab, not another anime fan-out test.
            title = http('GET', self.base+'/series/'+str(self.show['id']))
            self.addCleanup(http, 'PUT', self.base+'/series/'+str(title['id']), dict(title))
            http('PUT', self.base+'/series/'+str(title['id']), dict(title, seriesType='standard'))
            row.update(title='World Trigger S03E01 1080p WEBRip x265 [Fixture]', episode=1,
                       size=400*1024*1024)
        catalogue.reset([row])
        source = self.server(catalogue.handler())
        prowlarr = self.urls['Prowlarr']
        with patch.dict(os.environ, INDEXER_URL=source, INDEXER_API_KEY='fixture'):
            wire_stack.ensure_indexer(prowlarr, 'a'*32)
            wire_stack.ensure_prowlarr_app(prowlarr, 'a'*32, 'Sonarr', 'Sonarr', self.urls['Sonarr'], 'a'*32)
        self.command(prowlarr+'/api/v1', {'name': 'ApplicationIndexerSync'})
        indexers = http('GET', self.base+'/indexer')
        self.assertEqual(len(indexers), 1, indexers)
        fields = {f['name']: f.get('value') for f in indexers[0]['fields']}
        self.assertTrue(fields['baseUrl'].startswith(prowlarr), fields)
        fake = fake_source.FakeState(self.root/'downloader', self.media, False)
        qurl = self.server(fake_source.qbit_handler(fake))
        client = next(c for c in http('GET', self.base+'/downloadclient/schema') if c['implementation']=='QBittorrent')
        client = wire_stack.apply_download_client(client, {'host': '127.0.0.1', 'port': int(qurl.rsplit(':', 1)[1]), 'tvCategory': 'sonarr', 'username': 'test', 'password': 'test', 'useSsl': False}, True)
        client.update(name='Fixture', priority=1)
        http('POST', self.base+'/downloadclient', client)
        catalogue.reset([row])
        if full:
            self.command(self.base, {'name': 'SeasonSearch', 'seriesId': self.show['id'], 'seasonNumber': 3})
        else:
            episode = next(e for e in http('GET', self.base+'/episode?seriesId='+str(self.show['id']))
                           if e['seasonNumber'] == 3 and e['episodeNumber'] == 1)
            self.command(self.base, {'name': 'EpisodeSearch', 'episodeIds': [episode['id']]})
        queries = [r for r in catalogue.snapshot() if r['query'].get('t') != ['caps']]
        self.assertGreater(len(queries), 0)
        self.assertLessEqual(len(queries), 120 if full else 8, queries)
        self.assertEqual(len(fake.list_torrents()), 1)
        self.assertEqual(fake.list_torrents()[0]['hash'], anime_indexer.digest(row))
        history = http('GET', self.base+'/history?page=1&pageSize=100')['records']
        self.assertTrue(any(h.get('eventType') == 'grabbed' and str(h.get('downloadId', '')).lower() == anime_indexer.digest(row) for h in history), history)
        print(json.dumps({'path': 'Sonarr -> Prowlarr -> source', 'requests': len(queries), 'grabs': len(fake.list_torrents())}), flush=True)
        # Download housekeeping must not start another search.
        before = len(catalogue.snapshot())
        self.command(self.base, {'name': 'RefreshMonitoredDownloads'})
        self.assertEqual(len(catalogue.snapshot()), before)


if __name__ == '__main__': unittest.main()
