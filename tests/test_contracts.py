#!/usr/bin/env python3
"""Behavioral and failure contracts. All media and engine files are temporary fixtures."""
import hashlib
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'pompey/rootfs/usr/local/bin'
sys.path.insert(0, str(BIN))
import engine_manager as engines
import media_policy as media
import pompey_common as api
import pompey_state as state
import request_policy as requests
import vpn_config
import vpn_firewall
import wire_stack
from pompey_controller import Job


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pompey-contract-')
        self.root = Path(self.temp.name)
        self.environ = patch.dict(os.environ, {'MEDIA_ROOT':str(self.root/'media'), 'POMPEY_DATA':str(self.root/'data'),
                                 'POMPEY_CONFIG':str(self.root/'config'), 'POMPEY_READY':str(self.root/'ready'),
                                 'MEDIA_MOVIES':'Movies/General','MEDIA_MOVIES_KID':'Movies/Kids',
                                 'MEDIA_TV':'TV/General','MEDIA_TV_KID':'TV/Kids','AFTER_DOWNLOAD':'stop_sharing'})
        self.environ.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.environ.stop)

    def video(self, rel):
        path = Path(api.media_root()) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture media')
        return path


class MediaContracts(Sandbox):
    def torrent(self, **values):
        return dict({'hash':'fixture','category':'radarr','state':'uploading','progress':1,
                     'amount_left':0,'ratio':0.01,'seeding_time':5}, **values)

    def test_upgrade_is_preserved_for_arr(self):
        old = self.video('Movies/General/Example/Example.720p.mkv')
        new = self.video('downloads/complete/Example/Example.2160p.mkv')
        item = self.torrent(content_path=str(new))
        with patch.object(api,'http',return_value=[item]) as http, patch.object(api,'load_secrets',return_value={}):
            media.maintain_downloads()
        self.assertTrue(old.exists())
        self.assertTrue(new.exists())
        self.assertFalse(any('/command' in c.args[1] or c.args[0]=='DELETE' for c in http.call_args_list))

    def test_both_sharing_policies_keep_seeding_below_goal(self):
        for policy in ['share_to_ratio','share_one_day']:
            with self.subTest(policy=policy), patch.dict(os.environ,{'AFTER_DOWNLOAD':policy}), patch.object(api,'http') as http:
                media.stop_at_goal([self.torrent()])
                http.assert_not_called()

    def test_one_day_reads_seeding_time_from_properties_when_absent_in_list(self):
        item=self.torrent();item.pop('seeding_time')
        with patch.dict(os.environ,{'AFTER_DOWNLOAD':'share_one_day'}),patch.object(api,'http',return_value={'seeding_time':86400}) as http:
            media.seed_times([item])
        self.assertIn('/torrents/properties?',http.call_args.args[1])
        self.assertTrue(media.seed_goal_reached(item,'share_one_day'))

    def test_stop_at_ratio_and_time_goals(self):
        for policy,item in [('share_to_ratio',self.torrent(ratio=1)),('share_one_day',self.torrent(seeding_time=86400))]:
            with self.subTest(policy=policy), patch.dict(os.environ,{'AFTER_DOWNLOAD':policy}), patch.object(api,'http') as http:
                media.stop_at_goal([item])
                self.assertEqual(http.call_count,1)
                self.assertTrue(http.call_args.args[1].endswith('/stop'))

    def test_stopped_torrent_converges_without_writes(self):
        with patch.object(api,'http') as http:
            media.stop_at_goal([self.torrent(state='stoppedUP')])
            http.assert_not_called()

    def test_incomplete_never_stopped_or_imported(self):
        for item in [self.torrent(progress=.999, amount_left=10),self.torrent(state='moving')]:
            with patch.object(api,'http') as http:
                media.stop_at_goal([item])
                http.assert_not_called()

    def test_foreign_category_not_managed(self):
        with patch.object(api,'http') as http:
            media.stop_at_goal([self.torrent(category='unrelated')])
            http.assert_not_called()

    def test_share_preferences_match_qbit_52_api(self):
        self.assertEqual(api.qbit_seed_preferences('stop_sharing'), {
            'max_inactive_seeding_time_enabled': False, 'max_ratio_act': 0,
            'max_ratio': 0, 'max_seeding_time_enabled': False,
        })
        self.assertEqual(api.qbit_seed_preferences('share_to_ratio')['max_ratio'], 1)
        one_day = api.qbit_seed_preferences('share_one_day')
        self.assertEqual(one_day['max_seeding_time'], 1440)
        self.assertIs(one_day['max_ratio_enabled'], False)
        self.assertNotIn('max_ratio', one_day)
        self.assertNotIn('max_seeding_time_enabled', one_day)

    def test_malformed_download_list_is_an_error(self):
        with patch.object(api,'http',return_value={'error':'unavailable'}):
            with self.assertRaises(RuntimeError): media.maintain_downloads()

    def test_manual_grab_ambiguous_timeout_is_not_retried(self):
        source = self.video('downloads/manual/Example/Example.mkv')
        item = self.torrent(category='prowlarr',state='stoppedUP',content_path=str(source.parent))
        secrets = {'radarr_api_key':'fake','sonarr_api_key':'fake'}
        row = {'path':str(source),'movie':{'id':1},'quality':{},'rejections':[]}
        def http(method,url,*args,**kw):
            if method=='POST': raise RuntimeError('ambiguous timeout')
            return [row]
        with patch.object(api,'http',side_effect=http) as calls, patch.object(api,'arr_api_root',return_value='http://fake/api/v3'):
            with self.assertRaises(RuntimeError): media.manual_imports([item],secrets)
            calls.reset_mock()
            media.manual_imports([item],secrets)
            calls.assert_not_called()
        self.assertTrue(source.exists())
        self.assertEqual(state.load('manual-imports')['fixture']['phase'],'submitting')

    def test_rejected_manual_grab_and_sidecars_are_retained(self):
        source=self.video('downloads/manual/Example/Example.mkv')
        subtitle=source.with_suffix('.srt');subtitle.write_text('subtitle')
        item=self.torrent(category='prowlarr',state='stoppedUP',content_path=str(source.parent))
        with patch.object(api,'http',return_value=[{'path':str(source),'movie':{'id':1},'rejections':['not an upgrade']}]) as http, patch.object(api,'arr_api_root',return_value='http://fake'):
            media.manual_imports([item],{'radarr_api_key':'fake','sonarr_api_key':'fake'})
        self.assertTrue(source.exists());self.assertTrue(subtitle.exists())
        self.assertFalse(any(c.args[0]=='POST' for c in http.call_args_list))


class RequestContracts(Sandbox):
    def test_failed_and_completed_requests_keep_monitoring_until_removed(self):
        for status in [4,5]:
            for kind, collection, field in [('movie','movie','tmdbId'),('tv','series','tvdbId')]:
                with self.subTest(status=status,kind=kind):
                    state.save('requests',{})
                    request={'id':1,'type':kind,'status':status,'media':{field:50}}
                    title={'id':1,field:50,'monitored':True}
                    def http(method,url,body=None,**kw):
                        if method=='GET': return [title] if url.endswith('/'+collection) else []
                        return body
                    with patch.object(requests,'requests_snapshot',return_value=[request]) as snapshot,patch.object(api,'load_secrets',return_value={'radarr_api_key':'r','sonarr_api_key':'s'}),patch.object(api,'arr_api_root',return_value='http://fake'),patch.object(api,'http',side_effect=http) as calls:
                        requests.reconcile_requests()
                        self.assertTrue(all(c.args[0]=='GET' for c in calls.call_args_list))
                        self.assertIn(collection+':1',state.load('requests')['owned'])
                        snapshot.return_value=[]
                        calls.reset_mock()
                        requests.reconcile_requests()
                        updates=[c for c in calls.call_args_list if c.args[0]=='PUT']
                        self.assertEqual(len(updates),1)
                        self.assertFalse(updates[0].args[2]['monitored'])

    def test_missing_tv_identity_preserves_tv_but_allows_movie_cancellation(self):
        state.save('requests',{'owned':{'movie:1':{'externalId':50},'series:1':{'externalId':60}}})
        request={'id':1,'type':'tv','status':2,'media':{'tmdbId':70}}
        def http(method,url,body=None,**kw):
            if method!='GET': return body
            if url.endswith('/movie'):return [{'id':1,'tmdbId':50,'monitored':True}]
            if url.endswith('/series'):return [{'id':1,'tvdbId':60,'monitored':True}]
            return []
        with patch.object(requests,'requests_snapshot',return_value=[request]) as snapshot,patch.object(api,'load_secrets',return_value={'radarr_api_key':'r','sonarr_api_key':'s'}),patch.object(api,'arr_api_root',return_value='http://fake'),patch.object(api,'http',side_effect=http) as calls:
            requests.reconcile_requests()
            self.assertEqual([c.args[1] for c in calls.call_args_list if c.args[0]=='PUT'],['http://fake/movie/1'])
            self.assertIn('series:1',state.load('requests')['owned'])
            snapshot.return_value=[]
            calls.reset_mock()
            requests.reconcile_requests()
            self.assertEqual([c.args[1] for c in calls.call_args_list if c.args[0]=='PUT'],['http://fake/series/1'])

    def test_more_than_2050_requests_are_read(self):
        rows=[{'id':i} for i in range(2101)]
        def http(method,url,**kw):
            import urllib.parse
            skip=int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['skip'][0])
            return {'results':rows[skip:skip+50],'pageInfo':{'results':len(rows)}}
        with patch.object(api,'seerr_api_key_from_disk',return_value='fake'),patch.object(api,'http',side_effect=http):
            self.assertEqual(len(requests.requests_snapshot()),2101)

    def test_duplicate_page_is_not_a_complete_snapshot(self):
        with patch.object(api,'seerr_api_key_from_disk',return_value='fake'),patch.object(api,'http',return_value={'results':[{'id':1}]*50}):
            with self.assertRaises(RuntimeError): requests.requests_snapshot()

    def test_truncated_page_does_not_cancel(self):
        state.save('requests',{'owned':{'movie:1':{'externalId':5}}})
        with patch.object(api,'seerr_api_key_from_disk',return_value='fake'),patch.object(api,'http',return_value={'results':[],'pageInfo':{'results':2}}) as http:
            with self.assertRaises(RuntimeError): requests.reconcile_requests()
            self.assertTrue(all(c.args[0]=='GET' for c in http.call_args_list))
        self.assertIn('movie:1',state.load('requests')['owned'])

    def test_unavailable_seerr_does_not_cancel(self):
        with patch.object(requests,'requests_snapshot',side_effect=RuntimeError('403')),patch.object(api,'http') as http:
            with self.assertRaises(RuntimeError): requests.reconcile_requests()
            http.assert_not_called()

    def test_only_previously_owned_title_is_unmonitored(self):
        state.save('requests',{'owned':{'movie:1':{'externalId':5}}})
        movies=[{'id':1,'tmdbId':5,'monitored':True},{'id':2,'tmdbId':6,'monitored':True}]
        mutations=[]
        def http(method,url,body=None,**kw):
            if method!='GET': mutations.append((method,url,body));return body
            if url.endswith('/movie'):return movies
            return []
        with patch.object(requests,'requests_snapshot',return_value=[]),patch.object(api,'load_secrets',return_value={'radarr_api_key':'r','sonarr_api_key':'s'}),patch.object(api,'arr_api_root',return_value='http://fake'),patch.object(api,'http',side_effect=http):
            requests.reconcile_requests()
            requests.reconcile_requests()
        self.assertEqual(len(mutations),1)
        self.assertTrue(mutations[0][1].endswith('/movie/1'))
        self.assertFalse(mutations[0][2]['monitored'])

    def test_declined_request_is_not_recreated(self):
        row={'id':1,'type':'movie','status':3,'media':{'tmdbId':5}}
        with patch.object(requests,'requests_snapshot',return_value=[row]),patch.object(api,'load_secrets',return_value={'radarr_api_key':'r','sonarr_api_key':'s'}),patch.object(api,'arr_api_root',return_value='http://fake'),patch.object(api,'http',return_value=[]) as http:
            requests.reconcile_requests()
        self.assertTrue(all(c.args[0]=='GET' for c in http.call_args_list))


class FakeServices:
    def __init__(self): self.running=True;self.fail_health=False;self.fail_stop=False
    def stop(self,names):
        if self.fail_stop: self.fail_stop=False;raise RuntimeError('stop failed')
        self.running=False
    def start(self,names): self.running=True
    def health(self,names):
        if self.fail_health: self.fail_health=False;raise RuntimeError('unhealthy new engine')


class UpdateContracts(Sandbox):
    def setUp(self):
        super().setUp()
        process = patch.object(engines, 'run_process')
        self.validation = process.start()
        self.addCleanup(process.stop)
        self.engines=self.root/'engines';self.engines.mkdir()
        (self.engines/'Radarr').mkdir();(self.engines/'Radarr/version').write_text('old')
        self.config=self.root/'config';(self.config/'radarr').mkdir(parents=True)
        with closing(sqlite3.connect(self.config/'radarr/app.db')) as db: db.execute('PRAGMA user_version=1')
        engines.atomic_json(self.engines/'.installed.json',{'Radarr':'old'})
        self.services=FakeServices();self.transaction=engines.Transaction(self.engines,self.config,self.services)
        self.stage=self.root/'stage';self.stage.mkdir();(self.stage/'version').write_text('new')

    def assert_old(self):
        self.assertEqual((self.engines/'Radarr/version').read_text(),'old')
        self.assertTrue(self.services.running)
        with closing(sqlite3.connect(self.config/'radarr/app.db')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)

    def test_failed_swap_restores_files_and_restarts_service(self):
        with self.assertRaises(FileNotFoundError):
            self.transaction.install({'Radarr':self.root/'missing'},{'Radarr':'new'})
        self.assert_old()

    def test_failed_health_restores_database_and_binary(self):
        self.services.fail_health=True
        def migrate():
            with closing(sqlite3.connect(self.config/'radarr/app.db')) as db: db.execute('PRAGMA user_version=99')
        with self.assertRaises(RuntimeError):
            self.transaction.install({'Radarr':self.stage},{'Radarr':'new'},migrate)
        self.assert_old()

    def test_failed_stop_never_replaces_files(self):
        self.services.fail_stop=True
        with self.assertRaises(RuntimeError):self.transaction.install({'Radarr':self.stage},{'Radarr':'new'})
        self.assert_old()

    def test_failed_backup_never_replaces_live_files(self):
        with patch.object(engines.shutil,'copytree',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.transaction.install({'Radarr':self.stage},{'Radarr':'new'})
        self.assert_old()

    def test_success_keeps_recovery_snapshot(self):
        self.transaction.install({'Radarr':self.stage},{'Radarr':'new'})
        self.assertEqual((self.engines/'Radarr/version').read_text(),'new')
        self.assertTrue((self.engines/'.rollback/config/radarr/app.db').exists())
        self.assertFalse(self.transaction.journal.exists())

    def test_abrupt_exit_recovers_database_and_binary(self):
        code='''import sys,os
from pathlib import Path
import engine_manager as e
class Services:
 def stop(self,names):pass
 def start(self,names):pass
 def health(self,names):pass
r=Path(sys.argv[1])
t=e.Transaction(r/'engines',r/'config',Services())
t.install({'Radarr':r/'stage'},{'Radarr':'new'},lambda:os._exit(7))
'''
        proc=subprocess.run([sys.executable,'-c',code,str(self.root)],env={**os.environ,'PYTHONPATH':str(BIN)})
        self.assertEqual(proc.returncode,7)
        # Startup removes volatile readiness. Restored s6 services must be
        # released before recovery checks their HTTP health.
        marker = self.root/'ready/engines-ready'
        marker.unlink(missing_ok=True)
        def healthy(names):
            self.assertTrue(marker.exists(), 'restored engines are still waiting at their startup gate')
        with patch.object(self.services, 'health', side_effect=healthy):
            self.transaction.recover()
        self.assert_old()
        self.assertFalse(self.transaction.journal.exists())

    def test_failed_fresh_install_clears_readiness(self):
        import shutil
        shutil.rmtree(self.engines/'Radarr')
        (self.engines/'.installed.json').unlink()
        marker = self.root/'ready/engines-ready'
        def configure():
            marker.parent.mkdir(parents=True)
            marker.touch()
        self.services.fail_health = True
        with self.assertRaises(RuntimeError):
            self.transaction.install({'Radarr':self.stage},{'Radarr':'new'},configure)
        self.assertFalse(marker.exists())

    def test_checksum_mismatch_rejected(self):
        src=self.root/'artifact';src.write_bytes(b'wrong')
        with self.assertRaises(RuntimeError):engines.download({'url':src.as_uri(),'sha256':'0'*64},self.root/'download')

    def test_archive_path_escape_rejected(self):
        archive=self.root/'unsafe.tar'
        with tarfile.open(archive,'w') as tar:
            member=tarfile.TarInfo('../escape');member.size=1;tar.addfile(member,io.BytesIO(b'x'))
        with self.assertRaises(tarfile.FilterError):engines.unpack(archive,self.root/'unpacked')
        self.assertFalse((self.root/'escape').exists())

    def test_manifest_pins_every_artifact(self):
        manifest=json.loads(engines.manifest_path().read_text())
        for name,entry in manifest['engines'].items():
            if name=='seerr': self.assertIn('@sha256:',entry['image']);continue
            self.assertEqual(len(entry['artifacts']),4)
            for a in entry['artifacts'].values():
                self.assertNotIn('/latest/',a['url'])
                self.assertTrue(a.get('sha256') or a.get('sha512'))


    def environment(self):
        manifest=self.root/'manifest.json'
        manifest.write_text(json.dumps({'engines':{'Radarr':{'version':'test'}},'resources':{}}))
        return patch.dict(os.environ,{'POMPEY_ENGINE_MANIFEST':str(manifest),'POMPEY_ENGINES':str(self.engines),'POMPEY_FAKE_VPN':'1'})

    def test_cached_bundle_releases_readiness_after_restart(self):
        with self.environment(),patch.object(engines,'Services',return_value=self.services),patch.object(engines,'stage',return_value=self.stage),patch.object(engines.subprocess,'run'):
            engines.main()
        marker=self.root/'ready/engines-ready';self.assertTrue(marker.exists());marker.unlink()
        with self.environment(),patch.object(engines,'Services',return_value=self.services),patch.object(engines,'stage') as stage,patch.object(engines.subprocess,'run') as command:
            engines.main()
            stage.assert_not_called()
            self.assertEqual(command.call_args.args[0],['write-engine-configs'])
        self.assertTrue(marker.exists())

    def test_failed_download_can_boot_complete_previous_bundle(self):
        with self.environment(),patch.object(engines,'Services',return_value=self.services),patch.object(engines,'stage',side_effect=OSError('offline')),patch.object(engines.subprocess,'run'):
            engines.main()
        self.assertTrue((self.root/'ready/engines-ready').exists())
        self.assert_old()

    def test_failed_download_can_boot_legacy_bundle_without_claiming_new_version(self):
        (self.engines/'.installed.json').unlink()
        (self.engines/'.stamps').mkdir()
        (self.engines/'Radarr/Radarr').write_bytes(b'fixture launcher')
        with self.environment(),patch.object(engines,'Services',return_value=self.services),patch.object(engines,'stage',side_effect=OSError('offline')),patch.object(engines.subprocess,'run'):
            engines.main()
        self.assertTrue((self.root/'ready/engines-ready').exists())
        self.assertFalse((self.engines/'.installed.json').exists())

    def test_incomplete_legacy_bundle_does_not_release_services(self):
        (self.engines/'.installed.json').unlink()
        (self.engines/'.stamps').mkdir()
        with self.environment(),patch.object(engines,'Services',return_value=self.services),patch.object(engines,'stage',side_effect=OSError('offline')):
            with self.assertRaises(OSError): engines.main()
        self.assertFalse((self.root/'ready/engines-ready').exists())

    def test_known_legacy_downgrade_is_rejected_before_staging(self):
        stamps = self.engines/'.stamps'; stamps.mkdir()
        (stamps/'Radarr').write_text('etag|Radarr.master.9.0.0.100.linux-musl-core-x64.tar.gz|https://example.invalid')
        manifest = self.root/'versioned.json'
        manifest.write_text(json.dumps({'engines':{'Radarr':{'version':'v6.3.0.10514'}}}))
        with self.environment(),patch.dict(os.environ,POMPEY_ENGINE_MANIFEST=str(manifest)),patch.object(engines,'Services',return_value=self.services),patch.object(engines,'stage') as stage:
            with self.assertRaisesRegex(RuntimeError, 'newer than'): engines.main()
            stage.assert_not_called()
        self.assert_old()

    def test_version_guard_allows_equal_or_newer_bundle(self):
        engines.atomic_json(self.engines/'.active-manifest.json',{'engines':{'Radarr':{'version':'v6.3.0.100'}}})
        for version in ['v6.3.0.100','v6.3.0.101','v6.10.0.1']:
            engines.reject_downgrades(self.engines,{'Radarr':{'version':version}})
        with self.assertRaisesRegex(RuntimeError,'newer than'):
            engines.reject_downgrades(self.engines,{'Radarr':{'version':'v6.2.0.200'}})

    def test_failed_validation_restores_active_resource_manifest(self):
        old={'resources':{'trash_guides':'old'}}
        engines.atomic_json(self.engines/'.active-manifest.json',old)
        def fail(): raise RuntimeError('configuration rejected')
        with self.assertRaises(RuntimeError):
            self.transaction.install({'Radarr':self.stage},{'Radarr':'new'},validate=fail,manifest={'resources':{'trash_guides':'new'}})
        self.assertEqual(json.loads((self.engines/'.active-manifest.json').read_text()),old)
        self.assert_old()


class ControllerProcessContracts(Sandbox):
    def test_timeout_kills_descendants_before_releasing_lock(self):
        import time
        from pompey_controller import execute
        marker=self.root/'late-mutation'
        child="import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(1);Path("+repr(str(marker))+").write_text('bad')"
        parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(5)"
        with self.assertRaises(subprocess.TimeoutExpired):
            execute(Job('fixture',[sys.executable,'-c',parent],10,.3))
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_validation_timeout_stops_descendants_before_rollback(self):
        import time
        marker = self.root/'late-mutation'
        child = "import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(1);Path("+repr(str(marker))+").write_text('bad')"
        parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(5)"
        root = self.root/'engines'; (root/'Radarr').mkdir(parents=True)
        (root/'Radarr/version').write_text('old')
        stage = self.root/'stage'; stage.mkdir(); (stage/'version').write_text('new')
        config = self.root/'config'; config.mkdir()
        tx = engines.Transaction(root, config, FakeServices())
        with self.assertRaises(subprocess.TimeoutExpired):
            tx.install({'Radarr':stage},{'Radarr':'new'},validate=lambda:engines.run_process([sys.executable,'-c',parent],.3))
        self.assertEqual((root/'Radarr/version').read_text(),'old')
        self.assertFalse(tx.journal.exists())
        time.sleep(1.1)
        self.assertFalse(marker.exists())


class ProwlarrConnectionContracts(Sandbox):
    def test_masked_key_is_tested_and_repaired_only_when_broken(self):
        values = wire_stack.prowlarr_app_values('Radarr','http://127.0.0.1:7878','correct-key')
        for broken in [False, True]:
            item = {'name':'Radarr','id':1,'syncLevel':'fullSync','fields':[{'name':k,'value':v} for k,v in values.items()]}
            wire_stack.set_app_fields(item, {'apiKey':'********'})
            def http(method,url,body=None,**kwargs):
                if method == 'GET': return [item]
                if method == 'POST' and broken: raise RuntimeError('stored key rejected')
                if method == 'PUT': self.assertEqual(wire_stack.app_field(body,'apiKey'),'correct-key')
                return {}
            with self.subTest(broken=broken),patch.object(wire_stack,'http',side_effect=http) as calls:
                wire_stack.ensure_prowlarr_app('http://prowlarr','pkey','Radarr','Radarr',values['baseUrl'],values['apiKey'])
                self.assertEqual([c.args[0] for c in calls.call_args_list],['GET','POST','PUT'] if broken else ['GET','POST'])

    def test_repairs_each_managed_connection_field(self):
        values = wire_stack.prowlarr_app_values('Radarr','http://127.0.0.1:7878','correct-key')
        for field, wrong in [('baseUrl','http://127.0.0.1:9999'),('apiKey','wrong-key'),('syncLevel','disabled')]:
            with self.subTest(field=field):
                item = {'name':'Radarr','id':1,'syncLevel':'fullSync','fields':[{'name':k,'value':v} for k,v in values.items()]}
                if field == 'syncLevel': item[field] = wrong
                else: wire_stack.set_app_fields(item,{field:wrong})
                def http(method,url,body=None,**kwargs):
                    if method == 'GET': return [item]
                    item.update(body)
                    return item
                with patch.object(wire_stack,'http',side_effect=http) as calls:
                    wire_stack.ensure_prowlarr_app('http://prowlarr','pkey','Radarr','Radarr',values['baseUrl'],values['apiKey'])
                    self.assertEqual([c.args[0] for c in calls.call_args_list],['GET','PUT'])
                    calls.reset_mock()
                    wire_stack.ensure_prowlarr_app('http://prowlarr','pkey','Radarr','Radarr',values['baseUrl'],values['apiKey'])
                    self.assertEqual([c.args[0] for c in calls.call_args_list],['GET'])


class RoutingContracts(Sandbox):
    def test_auto_mode_survives_move_and_later_rating(self):
        import route_rating as routing
        auto=api.movies_auto_dir();general=api.movies_dir();kid=api.movies_kid_dir()
        item={'id':1,'tmdbId':50,'title':'Example','path':auto+'/Example','certification':''}
        moves=[]
        def http(method,url,body=None,**kw):
            if method=='GET':return [dict(item)]
            moves.append(body['rootFolderPath']);item['path']=body['rootFolderPath']+'/Example'
        with patch.object(routing,'http_json',side_effect=http):
            routing.route_library('key','http://fake/movie',kid,general,[auto],routing.KID_MOVIE,'movie')
            item['certification']='PG'
            routing.route_library('key','http://fake/movie',kid,general,[auto],routing.KID_MOVIE,'movie')
            # A subsequent explicit move out of the last automatic destination
            # relinquishes ownership instead of repeatedly undoing the choice.
            item['path']=general+'/Manually Chosen'
            routing.route_library('key','http://fake/movie',kid,general,[auto],routing.KID_MOVIE,'movie')
        self.assertEqual(moves,[general,kid])
        self.assertEqual(state.load('routing-movie'),{})


class LeftoverRootContracts(Sandbox):
    def test_known_legacy_names_are_narrow(self):
        wanted={api.movies_auto_dir(), api.movies_dir(), api.movies_kid_dir()}
        self.assertTrue(wire_stack.is_known_legacy_root('/media/Movies', wanted))
        self.assertTrue(wire_stack.is_known_legacy_root('/media/Kid Friendly Movies', wanted))
        self.assertFalse(wire_stack.is_known_legacy_root(str(Path(api.media_root())/'Archive'), wanted))
        self.assertFalse(wire_stack.is_known_legacy_root(api.movies_dir(), wanted))

    def test_titles_on_household_roots_do_not_occupy_a_parent_legacy_root(self):
        leftover='/media/Movies'
        registered=[leftover, '/media/Movies/By Rating', '/media/Movies/Not Kid Friendly']
        self.assertFalse(wire_stack.root_is_occupied(leftover, [{'path':'/media/Movies/By Rating/Title'}], [], registered))
        self.assertTrue(wire_stack.root_is_occupied(leftover, [{'path':'/media/Movies/Old Title'}], [], registered))
        self.assertTrue(wire_stack.root_is_occupied(leftover, [], [{'rootFolderPath':leftover}], registered))

    def test_occupied_legacy_root_is_not_deleted_even_if_arr_would_allow_it(self):
        folders=[{'id':9,'path':'/media/Movies'}]
        deletes=[]
        def http(method,url,body=None,**kw):
            if method=='DELETE':
                deletes.append(url); folders.clear(); return None
            if url.endswith('/rootfolder'): return folders
            if url.endswith('/movie'): return [{'id':1,'path':'/media/Movies/Old Title'}]
            if url.endswith('/importlist'): return []
            return []
        with patch.object(wire_stack,'http',side_effect=http):
            notices=wire_stack.prune_root_folders('http://fake','k','radarr')
        self.assertEqual(deletes,[])
        self.assertEqual(folders,[{'id':9,'path':'/media/Movies'}])
        self.assertTrue(any('still in use' in note for note in notices))
        self.assertFalse(any('editor' in url for url in deletes))

    def test_unused_legacy_root_is_unregistered(self):
        folders=[{'id':9,'path':'/media/Movies'}]
        def http(method,url,body=None,**kw):
            if method=='DELETE':
                folders.clear(); return None
            if url.endswith('/rootfolder'): return folders
            if url.endswith('/movie'): return []
            if url.endswith('/importlist'): return []
            return []
        with patch.object(wire_stack,'http',side_effect=http):
            notices=wire_stack.prune_root_folders('http://fake','k','radarr')
        self.assertEqual(folders,[])
        self.assertEqual(notices,[])

    def test_empty_retired_staging_root_is_removed(self):
        legacy = api.legacy_movies_auto_dir()
        Path(legacy).mkdir(parents=True)
        folders=[{'id':4,'path':legacy}]
        def http(method,url,body=None,**kw):
            if method=='DELETE':
                folders.clear(); return None
            if url.endswith('/rootfolder'): return folders
            if url.endswith('/movie'): return []
            if url.endswith('/importlist'): return []
            return []
        with patch.object(wire_stack,'http',side_effect=http):
            notices=wire_stack.prune_root_folders('http://fake','k','radarr')
        self.assertEqual(folders,[])
        self.assertFalse(Path(legacy).exists())
        self.assertEqual(notices,[])

    def test_retired_staging_with_files_stays_registered(self):
        legacy = api.legacy_movies_auto_dir()
        title = Path(legacy)/'Title'
        title.mkdir(parents=True)
        (title/'movie.mkv').write_bytes(b'keep')
        folders=[{'id':4,'path':legacy}]
        def http(method,url,body=None,**kw):
            if method=='DELETE':
                raise AssertionError('occupied staging root was unregistered')
            if url.endswith('/rootfolder'): return folders
            if url.endswith('/movie'): return [{'id':1,'path':str(title)}]
            if url.endswith('/importlist'): return []
            return []
        with patch.object(wire_stack,'http',side_effect=http):
            notices=wire_stack.prune_root_folders('http://fake','k','radarr')
        self.assertEqual(folders,[{'id':4,'path':legacy}])
        self.assertTrue((title/'movie.mkv').is_file())
        self.assertTrue(any('still has files' in note for note in notices))

    def test_retired_staging_keeps_an_empty_title_dir_while_arr_points_at_it(self):
        legacy = api.legacy_movies_auto_dir()
        title = Path(legacy)/'Title'
        title.mkdir(parents=True)
        folders=[{'id':4,'path':legacy}]
        def http(method,url,body=None,**kw):
            if method=='DELETE':
                raise AssertionError('referenced staging root was unregistered')
            if url.endswith('/rootfolder'): return folders
            if url.endswith('/movie'): return [{'id':1,'path':str(title)}]
            if url.endswith('/importlist'): return []
            return []
        with patch.object(wire_stack,'http',side_effect=http):
            notices=wire_stack.prune_root_folders('http://fake','k','radarr')
        self.assertEqual(folders,[{'id':4,'path':legacy}])
        self.assertTrue(title.is_dir())
        self.assertTrue(any('still in use' in note for note in notices))

    def test_retired_staging_empty_directories_are_removed(self):
        legacy = api.legacy_movies_auto_dir()
        (Path(legacy)/'Gone Title').mkdir(parents=True)
        folders=[{'id':4,'path':legacy}]
        def http(method,url,body=None,**kw):
            if method=='DELETE':
                folders.clear(); return None
            if url.endswith('/rootfolder'): return folders
            if url.endswith('/movie'): return []
            if url.endswith('/importlist'): return []
            return []
        with patch.object(wire_stack,'http',side_effect=http):
            notices=wire_stack.prune_root_folders('http://fake','k','radarr')
        self.assertEqual(folders,[])
        self.assertFalse(Path(legacy).exists())
        self.assertEqual(notices,[])


class ConfigurationContracts(Sandbox):
    def test_library_traversal_and_overlap_rejected(self):
        import pompey_config
        for value in ['../outside','/absolute','downloads/complete','Movies','Movies/By Rating']:
            with self.subTest(path=value),patch.dict(os.environ,{'MEDIA_MOVIES':value}):
                with self.assertRaises(ValueError):pompey_config.validate()

    def test_library_symlink_escape_rejected(self):
        import pompey_config
        media_root=Path(api.media_root());media_root.mkdir()
        (media_root/'Movies').symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):pompey_config.validate()

    def test_malformed_media_identity_cannot_cancel_known_request(self):
        state.save('requests',{'owned':{'series:1':{'externalId':60}}})
        def http(method,url,**kw):
            return [{'id':1,'tvdbId':60,'monitored':True}] if url.endswith('/series') else []
        for media in [{}, None, 'invalid']:
            with self.subTest(media=media),patch.object(requests,'requests_snapshot',return_value=[{'id':1,'type':'tv','status':2,'media':media}]),patch.object(api,'load_secrets',return_value={'radarr_api_key':'r','sonarr_api_key':'s'}),patch.object(api,'arr_api_root',return_value='http://fake'),patch.object(api,'http',side_effect=http) as calls:
                requests.reconcile_requests()
                self.assertTrue(all(c.args[0]=='GET' for c in calls.call_args_list))
                self.assertIn('series:1',state.load('requests')['owned'])

    def test_completed_command_with_retained_files_needs_review(self):
        file=self.video('downloads/manual/Example/test.mkv')
        receipts={'hash':{'phase':'submitted','commandId':1,'kind':'radarr','files':[str(file)]}}
        with patch.object(api,'arr_api_root',return_value='http://fake'),patch.object(api,'http',return_value={'status':'completed'}):
            media.check_receipts(receipts,{'radarr_api_key':'key'})
        self.assertEqual(receipts['hash']['phase'],'review')
        self.assertTrue(media.attention())
        self.assertTrue(file.exists())


class VpnContracts(unittest.TestCase):
    def setUp(self):self.config=(ROOT/'tests/fixtures/wg0.conf').read_text()
    def test_dns_is_taken_from_any_provider_file(self):
        _,meta=vpn_config.render(self.config.replace('10.2.0.1','172.29.8.9'))
        self.assertEqual(meta['dns'],['172.29.8.9'])
    def test_no_dns_does_not_invent_provider_resolver(self):
        _,meta=vpn_config.render(self.config.replace('DNS = 10.2.0.1',''))
        self.assertEqual(meta['dns'],[])
    def test_ipv6_endpoint_and_dual_stack(self):
        text=self.config.replace('185.159.157.1:51820','[2001:db8::1]:51820').replace('0.0.0.0/0','0.0.0.0/0, ::/0')
        _,meta=vpn_config.render(text)
        self.assertTrue(meta['ipv6'])
        self.assertEqual(meta['endpoints'][0]['ip'],'2001:db8::1')
    def test_partial_tunnel_rejected(self):
        with self.assertRaises(ValueError):vpn_config.parse(self.config.replace('0.0.0.0/0','10.0.0.0/8'))
    def test_generated_config_drops_shell_hooks(self):
        text,_=vpn_config.render(self.config+'\nPostUp = echo unsafe\n')
        self.assertNotIn('PostUp',text)
        self.assertLess(text.index('Table = off'),text.index('[Peer]'))
    def test_firewall_replacement_is_one_dual_family_transaction(self):
        rules=vpn_firewall.rules(self.config,'192.168.1.0/24')
        self.assertIn('policy drop',rules)
        self.assertIn('table inet pompey',rules)
        self.assertIn('ct direction reply',rules)
        self.assertNotIn('ct state established,related accept\n',rules.replace('ct direction reply ct state established,related accept',''))
    def test_ipv6_without_tunnel_has_no_public_exception(self):
        self.assertNotIn('ip6 daddr',vpn_firewall.rules(self.config,''))


class ControllerContracts(unittest.TestCase):
    def test_failed_job_recovers_after_dependency_returns(self):
        job=Job('fixture',[],60,30)
        for n in range(40):job.complete(n,False)
        self.assertLessEqual(job.due,39+300)
        job.complete(1000,True)
        self.assertEqual(job.failures,0);self.assertEqual(job.due,1060)
    def test_python_entrypoints_have_suffixes(self):
        for path in BIN.iterdir():
            if path.is_file() and path.read_bytes().startswith(b'#!/usr/bin/env python3'):
                self.assertEqual(path.suffix,'.py')
    def test_no_global_source_capability_proxy(self):
        self.assertFalse((BIN/'prowlarr_arr_proxy.py').exists())
    def test_quality_profiles_never_fabricate_default_or_max(self):
        with patch.object(wire_stack,'http',return_value=[{'id':1,'name':'Any'}]) as http:
            with self.assertRaises(RuntimeError):wire_stack.household_quality_profile('http://fake','fake','radarr')
            self.assertEqual(http.call_count,1)


if __name__=='__main__':unittest.main()
