#!/usr/bin/env python3
"""Fast checks for the captured catalogue and Torznab response boundaries."""
import importlib.util
import json
from pathlib import Path
import unittest
import sys
import threading
import urllib.request
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests/lib'))
from anime_indexer import Catalogue, matching_rows

spec=importlib.util.spec_from_file_location('import_prowlarr_results',ROOT/'tools/import_prowlarr_results.py')
parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)


class CatalogueTests(unittest.TestCase):
    def test_blank_lines_and_optional_grabs_are_unambiguous(self):
        row='torrent\n51 days\n[Group] World Trigger S03 [Dual Audio]\nFixture\n19.4 GiB\n3493\n35 / 1\nTV/Anime\n'
        plain='torrent\n365 days\nWorld Trigger S3 (01 14) [Batch]\nOther\n18.5 GiB\n99 / 15\nTV/Anime\n'
        for text in [row+plain,row.replace('\n','\n\n')+'\n\n'+plain]:
            rows=parser.parse(text)
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['grabs'],3493)
            self.assertEqual((rows[0]['seeders'],rows[0]['leechers']),(35,1))
            self.assertNotIn('grabs',rows[1])
            self.assertEqual(rows[1]['seeders'],99)

    def test_incomplete_rows_fail_instead_of_silently_disappearing(self):
        with self.assertRaises(ValueError):parser.parse('torrent\n51 days\nTitle\nIndexer\n1 GiB\n')

    def test_episode_queries_do_not_return_packs_or_other_episodes(self):
        pack={'season':3,'episode':None}
        first={'season':3,'episode':1,'absolute':86}
        second={'season':3,'episode':2,'absolute':87}
        rows=[pack,first,second]
        for query in [{'q':['01']},{'q':['World Trigger 86']},{'season':['3'],'ep':['1']}]:
            self.assertEqual(matching_rows(rows,query),[first])
        self.assertEqual(matching_rows(rows,{'season':['3']}),rows)
        self.assertEqual(matching_rows(rows,{'season':['2']}),[])

    def test_torznab_pagination_preserves_every_captured_row(self):
        rows=json.loads((ROOT/'tests/fixtures/anime/world_trigger.json').read_text())
        catalogue=Catalogue();catalogue.reset(rows)
        server=ThreadingHTTPServer(('127.0.0.1',0),catalogue.handler())
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        titles=[]
        for offset,count in [(0,100),(100,100),(200,3),(300,0)]:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api?t=search&offset={offset}&apikey=fixture') as response:
                document=ET.fromstring(response.read())
            page=document.find('./channel/{http://www.newznab.com/DTD/2010/feeds/attributes/}response')
            self.assertEqual(page.attrib,{'offset':str(offset),'total':'203'})
            items=document.findall('./channel/item')
            self.assertEqual(len(items),count)
            titles.extend(item.findtext('title') for item in items)
            for item in items:
                self.assertTrue(item.findtext('link').startswith('magnet:?xt=urn:btih:'))
                self.assertIn('127.0.0.1',item.findtext('link'))
        self.assertEqual(titles,[row['title'] for row in rows])
        ledger=catalogue.snapshot()
        self.assertEqual([r['results'] for r in ledger],[100,100,3,0])
        self.assertFalse(any('apikey' in r['query'] for r in ledger))

    def test_catalogue_preserves_realistic_variation(self):
        rows=json.loads((ROOT/'tests/fixtures/anime/world_trigger.json').read_text())
        self.assertEqual(len(rows),203)
        self.assertEqual(len({r['source'] for r in rows}),7)
        self.assertTrue(any('Dual Audio' in r['title'] and r['seeders']>0 for r in rows))
        self.assertTrue(any('Dual-Subs' in r['title'] for r in rows))
        self.assertTrue(any('Digital' in r['title'] for r in rows))
        self.assertTrue(any('Remux' in r['title'] for r in rows))
        self.assertTrue(any('01 ~ 14' in r['title'] for r in rows))
        self.assertTrue(all(r['size']>0 and r['seeders']>=0 and r['leechers']>=0 for r in rows))


if __name__=='__main__':unittest.main()
