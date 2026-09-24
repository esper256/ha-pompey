#!/usr/bin/env python3
"""Progress, recovery, and malformed-snapshot contracts; no real engines."""
from concurrent.futures import Future
from pathlib import Path
import copy
from unittest.mock import Mock, patch
import unittest

from test_contracts import Sandbox, api, media, state, wire_stack, requests, engines
import pompey_controller as controller
import route_rating as routing


class Recovery(Sandbox):
    def test_expired_and_orphaned_receipts_do_not_block_new_imports(self):
        source = self.video('downloads/manual/New/New.mkv')
        receipts = {name: {'phase': 'submitted', 'kind': 'radarr', 'commandId': ident,
                          'files': [str(source)]} for name, ident in [('expired', 1), ('orphaned', 2)]}
        state.save('manual-imports', receipts)
        torrent = {'hash': 'new', 'category': 'prowlarr', 'state': 'stoppedUP',
                   'progress': 1, 'amount_left': 0, 'content_path': str(source.parent)}
        def http(method, url, body=None, **kw):
            if url.endswith('/command/1'): raise api.ApiError('Missing command', 404)
            if url.endswith('/command/2'): return {'status': 'orphaned'}
            if url.endswith('/command/3'): return {'status': 'completed'}
            if method == 'POST': return {'id': 3}
            return [{'path': str(source), 'movie': {'id': 7}, 'rejections': []}]
        with patch.object(api, 'arr_api_root', return_value='http://fake'), patch.object(api, 'http', side_effect=http) as calls:
            for _ in range(2): media.manual_imports([torrent], {'radarr_api_key': 'key', 'sonarr_api_key': 'key'})
        saved = state.load('manual-imports')
        self.assertEqual([saved[x]['phase'] for x in ['expired', 'orphaned', 'new']], ['review'] * 3)
        self.assertEqual(sum(c.args[0] == 'POST' for c in calls.call_args_list), 1)
        self.assertTrue(source.exists())
        self.assertTrue(media.attention())

    def test_receipt_outage_keeps_intent_and_other_receipts_progress(self):
        receipts = {str(n): {'phase': 'submitted', 'kind': 'radarr', 'commandId': n, 'files': []} for n in [1, 2]}
        def http(method, url, **kw):
            if url.endswith('/1'): raise api.ApiError('Unavailable', 503)
            return {'status': 'completed'}
        with patch.object(api, 'arr_api_root', return_value='http://fake'), patch.object(api, 'http', side_effect=http):
            media.check_receipts(receipts, {'radarr_api_key': 'key'})
        self.assertEqual(receipts['1']['phase'], 'submitted')
        self.assertEqual(receipts['2']['phase'], 'complete')
        self.assertIn('error', receipts['1'])

    def test_malformed_root_snapshots_never_authorize_cleanup(self):
        root = [{'id': 1, 'path': '/media/Movies'}]
        for endpoint in ['movie', 'importlist']:
            for invalid in [{'error': 'unavailable'}, {'records': []}, [None], [{'id': 5}], [{'id': 5, 'path': 123, 'rootFolderPath': 123}]]:
                def http(method, url, **kw):
                    if url.endswith('/rootfolder'): return root
                    return invalid if url.endswith('/' + endpoint) else []
                with self.subTest(endpoint=endpoint, invalid=invalid), patch.object(wire_stack, 'http', side_effect=http) as calls:
                    self.assertTrue(wire_stack.prune_root_folders('http://fake', 'key', 'radarr'))
                    self.assertTrue(all(c.args[0] == 'GET' for c in calls.call_args_list))

    def test_moves_wait_retry_and_verify_actual_files(self):
        source = self.video('downloads/By Rating/Movies/Example/Example.mkv')
        subtitle = source.with_suffix('.srt'); subtitle.write_text('subtitle')
        item = {'id': 1, 'tmdbId': 50, 'title': 'Example', 'path': str(source.parent), 'certification': 'R'}
        submitted = []
        commands = []
        def http(method, url, body=None, **kw):
            if url.endswith('/command'): return commands
            if method == 'GET': return [dict(item)]
            submitted.append(body)  # Accepted does not change files or paths.
        def run(now):
            with patch.object(routing.time, 'time', return_value=now):
                routing.route_library('key', 'http://fake/movie', api.movies_kid_dir(), api.movies_dir(),
                                      [api.movies_auto_dir()], routing.KID_MOVIE, 'movie')
        with patch.object(routing, 'http_json', side_effect=http):
            run(1000); run(1060)
            self.assertEqual(len(submitted), 1)
            commands.append({'id': 8, 'name': 'BulkMoveMovie', 'status': 'started', 'body': {'movies': [{'movieId': 1}]}})
            run(1400)
            self.assertEqual(len(submitted), 1)
            commands.clear()
            run(1401)  # Failed move can retry after backoff, including after restart.
            self.assertEqual(len(submitted), 2)
            run(1500)
            self.assertEqual(len(submitted), 2)
            target = source.parents[4] / 'Movies/General/Normalized Example'
            target.parent.mkdir(parents=True, exist_ok=True)
            source.parent.rename(target)
            item['path'] = str(target)
            run(1501)
            self.assertNotIn('pending', state.load('routing-movie')['1'])
            self.assertEqual((target / source.name).read_bytes(), b'fixture media')
            self.assertEqual((target / subtitle.name).read_text(), 'subtitle')
            run(1700)
            self.assertEqual(len(submitted), 2)

    def test_failed_move_is_visible_without_resubmitting_from_destination(self):
        source = self.video('downloads/By Rating/Movies/Example/Example.mkv')
        item = {'id': 1, 'tmdbId': 50, 'title': 'Example', 'path': str(source.parent)}
        calls = []
        def http(method, url, body=None, **kw):
            if url.endswith('/command'): return []
            if method == 'GET': return [item]
            calls.append(body)
        with patch.object(routing, 'http_json', side_effect=http):
            with patch.object(routing.time, 'time', return_value=1000):
                routing.route_library('key','http://fake/movie',api.movies_kid_dir(),api.movies_dir(),[api.movies_auto_dir()],routing.KID_MOVIE,'movie')
            item['path'] = api.movies_dir() + '/Example'
            with patch.object(routing.time, 'time', return_value=2000):
                routing.route_library('key','http://fake/movie',api.movies_kid_dir(),api.movies_dir(),[api.movies_auto_dir()],routing.KID_MOVIE,'movie')
        self.assertEqual(len(calls), 1)
        self.assertTrue(source.exists())
        self.assertTrue(any('move' in n for n in media.attention()))

    def test_missing_storage_is_not_a_successful_move(self):
        source = api.tv_auto_dir() + '/Stored Show'
        item = {'id': 1, 'tvdbId': 50, 'title': 'Stored Show', 'path': source,
                'statistics': {'episodeFileCount': 14}}
        def http(method, url, body=None, **kw):
            if url.endswith('/command'): return []
            if method == 'GET': return [item]
        with patch.object(routing, 'http_json', side_effect=http):
            with patch.object(routing.time, 'time', return_value=1000):
                routing.route_library('key','http://fake/series',api.tv_kid_dir(),api.tv_dir(),[api.tv_auto_dir()],routing.KID_TV,'series')
            item['path'] = api.tv_dir() + '/Stored Show'
            Path(item['path']).mkdir(parents=True)  # An empty destination is not proof of a move.
            with patch.object(routing.time, 'time', return_value=1400):
                routing.route_library('key','http://fake/series',api.tv_kid_dir(),api.tv_dir(),[api.tv_auto_dir()],routing.KID_TV,'series')
        self.assertIn('pending', state.load('routing-series')['1'])
        self.assertTrue(any('move' in notice for notice in media.attention()))

    def test_blocked_queue_notice_appears_and_clears(self):
        response = {'records': [{'id': 1, 'title': 'Example', 'trackedDownloadState': 'importPending',
                    'trackedDownloadStatus': 'warning', 'statusMessages': [{'messages': ['Permission denied']}]}]}
        with patch.object(api, 'arr_api_root', return_value='http://fake'), patch.object(api, 'http', return_value=response):
            with patch.object(media.time, 'time', return_value=1000):
                media.observe_blocked_imports({'radarr_api_key': 'key'})
                self.assertEqual(media.attention(), [])
            with patch.object(media.time, 'time', return_value=1301):
                self.assertIn('Permission denied', media.attention()[0])
            response['records'] = []
            media.observe_blocked_imports({'radarr_api_key': 'key'})
            self.assertEqual(media.attention(), [])


class Scheduling(Sandbox):
    def test_lock_contention_is_waiting_and_recovers(self):
        job = controller.Job('downloads', ['fixture'], 30, 120)
        with engines.stack_lock(), patch.object(controller, 'run_process') as run:
            self.assertFalse(controller.execute(job)); run.assert_not_called()
            self.assertEqual(controller.job_status(job, 100)['phase'], 'waiting')
            self.assertTrue(controller.job_status(job, 100)['overdue'])
        with patch.object(controller, 'run_process') as run:
            self.assertTrue(controller.execute(job)); run.assert_called_once()
        job.complete(100, True)
        self.assertIsNotNone(job.last_success)
        self.assertEqual(job.phase, 'idle')

    def test_newly_due_job_is_not_reported_overdue(self):
        job = controller.Job('downloads', [], 30, 120, due=100, phase='waiting')
        self.assertFalse(controller.job_status(job, 101)['overdue'])
        self.assertTrue(controller.job_status(job, 161)['overdue'])

    def test_configuration_gates_mutators_until_arr_wired(self):
        jobs = [controller.Job(name, [], 30, 120) for name in ['configuration', 'routing', 'downloads']]
        ready = self.root / 'ready'
        pool = Mock(); pool.submit.side_effect = lambda *args: Future()
        controller.dispatch(jobs, pool, 100, ready)
        self.assertEqual(pool.submit.call_count, 1)
        self.assertEqual(pool.submit.call_args.args[1].name, 'configuration')
        jobs[0].future = None; jobs[0].complete(100, True)
        ready.mkdir(parents=True, exist_ok=True)
        (ready / 'arr-wired').touch()
        controller.dispatch(jobs, pool, 101, ready)
        self.assertEqual(pool.submit.call_args.args[1].name, 'downloads')
        jobs[2].future = None; jobs[2].complete(101, True)
        controller.dispatch(jobs, pool, 102, ready)
        self.assertEqual(pool.submit.call_args.args[1].name, 'routing')


class SeasonRequests(Sandbox):
    def test_overlapping_requests_and_manual_seasons_are_preserved(self):
        rows = [{'id': 1, 'type': 'tv', 'status': 2, 'media': {'tvdbId': 50}, 'seasons': [{'seasonNumber': 1}, {'seasonNumber': 2}]},
                {'id': 2, 'type': 'tv', 'status': 2, 'media': {'tvdbId': 50}, 'seasons': [{'seasonNumber': 2}]}]
        title = {'id': 4, 'tvdbId': 50, 'monitored': True, 'monitorNewItems': 'all',
                 'seasons': [{'seasonNumber': n, 'monitored': True} for n in [1, 2, 3]]}
        commands = [{'id': n, 'name': 'SeasonSearch', 'status': 'queued', 'body': {'seriesId': 4, 'seasonNumber': n}} for n in [1, 2]]
        mutations = []
        def http(method, url, body=None, **kw):
            if method == 'PUT': title.update(body); mutations.append((method, url)); return body
            if method == 'DELETE': mutations.append((method, url)); return None
            if url.endswith('/series'): return [copy.deepcopy(title)]
            if url.endswith('/command'): return commands
            return []
        with patch.object(requests, 'requests_snapshot', side_effect=lambda: copy.deepcopy(rows)), patch.object(api, 'load_secrets', return_value={'radarr_api_key': 'r', 'sonarr_api_key': 's'}), patch.object(api, 'arr_api_root', return_value='http://fake'), patch.object(api, 'http', side_effect=http):
            requests.reconcile_requests()
            rows.pop(0)
            requests.reconcile_requests()
            self.assertEqual([s['monitored'] for s in title['seasons']], [False, True, True])
            self.assertEqual([url for method, url in mutations if method == 'DELETE'], ['http://fake/command/1'])
            rows.clear(); requests.reconcile_requests()
            self.assertEqual([s['monitored'] for s in title['seasons']], [False, False, True])
            self.assertTrue(title['monitored'])
            self.assertEqual(title['monitorNewItems'], 'all')
            count = len(mutations); requests.reconcile_requests()
            self.assertEqual(len(mutations), count)

    def test_unknown_seasons_do_not_authorize_cancellation(self):
        for value in [None, [], [None], [{'seasonNumber': '1'}], [{'seasonNumber': 1, 'status': 99}]]:
            rows = [{'type': 'tv', 'status': 2, 'media': {'tvdbId': 50}, 'seasons': value}]
            self.assertIsNone(requests.requested_seasons(rows, 50))


if __name__ == '__main__': unittest.main()
