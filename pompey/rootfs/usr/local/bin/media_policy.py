"""Arr owns normal imports. Pompey only stops at seeding goals and assists manual grabs.

Never infer duplicate files from names, delete a download directory, or synthesize
Plex episodes. Unmatched/ambiguous manual files remain available for operator review.
"""
import os
import time
from pathlib import Path
import urllib.parse
import pompey_common as api
import pompey_state as state

STOPPED = {'pausedUP', 'stoppedUP', 'pausedDL', 'stoppedDL'}
TRANSIENT = {'checkingUP', 'checkingDL', 'checkingResumeData', 'moving', 'allocating', 'metaDL'}


def finished(item):
    try:
        return float(item.get('progress', 0)) >= 1 and int(item.get('amount_left', 1)) == 0
    except (ValueError, TypeError):
        return False


def seed_goal_reached(item, policy):
    if not finished(item):
        return False
    try:
        if policy == 'share_to_ratio':
            return float(item.get('ratio', 0)) >= 1
        if policy == 'share_one_day':
            return int(item.get('seeding_time', 0)) >= 86400
        return policy == 'stop_sharing'
    except (ValueError, TypeError):
        return False


def contained(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return Path(path).resolve() != Path(root).resolve()
    except (ValueError, TypeError):
        return False


def stop_at_goal(items):
    hashes = [r['hash'] for r in items if r.get('category') in {'radarr', 'sonarr', 'prowlarr'}
              and r.get('hash') and r.get('state') not in STOPPED | TRANSIENT
              and seed_goal_reached(r, api.after_download())]
    if hashes:
        data = urllib.parse.urlencode({'hashes': '|'.join(hashes)}).encode()
        api.http('POST', api.qbit_url() + '/api/v2/torrents/stop', data)


def check_receipts(receipts, secrets):
    for receipt in receipts.values():
        if receipt.get('phase') != 'submitted' or receipt.get('commandId') is None:
            continue
        before = dict(receipt)
        kind = receipt['kind']
        host = api.radarr_url() if kind == 'radarr' else api.sonarr_url()
        key = secrets[kind + '_api_key']
        try:
            base = api.arr_api_root(host, key)
            command = api.http('GET', base + '/command/' + str(receipt['commandId']), headers=api.arr_headers(key))
            if not isinstance(command, dict) or not isinstance(command.get('status'), str):
                raise RuntimeError('Invalid Arr import command response')
            status = command['status']
            receipt.pop('error', None)
            if status in {'failed', 'aborted', 'cancelled', 'orphaned'}:
                receipt.update(phase='review', error='Import ' + status)
            elif status == 'completed':
                receipt['phase'] = 'complete' if all(not Path(f).exists() for f in receipt['files']) else 'review'
            elif time.time() - receipt.setdefault('submittedAt', time.time()) > 86400:
                receipt.update(phase='review', error='Import has not finished after one day')
        except RuntimeError as exc:
            # An expired command is not evidence that files were imported. Keep
            # its payload and require review, without blocking unrelated grabs.
            receipt['error'] = str(exc)
            if isinstance(exc, api.ApiError) and exc.status == 404:
                receipt['phase'] = 'review'
        if receipt != before:
            state.save('manual-imports', receipts)


def attention():
    notices = []
    extra = persist_notices()
    if extra:
        notices.extend(extra)
    receipts = state.load('manual-imports')
    pending = sum(bool(r.get('error')) or r.get('phase') in {'submitting','review'} or (r.get('phase') == 'submitted' and r.get('commandId') is None) for r in receipts.values())
    if pending:
        notices.append(f'{pending} manual import(s) need review in Debug; files and import receipts were retained.')
    for label in ('movie', 'series'):
        for record in state.load('routing-' + label).values():
            pending_move = record.get('pending', {})
            if pending_move.get('error'):
                notices.append(f"Library move needs attention: {record.get('title', label)}. {pending_move['error']}")
    for record in state.load('blocked-imports').values():
        if time.time() - record['firstSeen'] >= 300:
            notices.append(f"Import blocked: {record['title']}. {record['reason']}")
    return notices


def persist_notices():
    extra = state.load('arr-roots').get('notices')
    if not isinstance(extra, list):
        return []
    return [str(item) for item in extra if str(item).strip()]


def manual_imports(items, secrets):
    """Submit only completed, stopped, explicitly manual downloads once.

    Persist intent before submitting. An ambiguous timeout/crash keeps the files
    and receipt for review instead of retrying a potentially destructive command.
    Normal radarr/sonarr category downloads never enter this path.
    """
    receipts = state.load('manual-imports')
    check_receipts(receipts, secrets)
    for torrent in items:
        path = torrent.get('content_path', '')
        digest = torrent.get('hash')
        if (torrent.get('category') != 'prowlarr' or not digest or digest in receipts
                or torrent.get('state') not in STOPPED or not finished(torrent)
                or not seed_goal_reached(torrent, api.after_download())
                or not contained(path, api.downloads_manual()) or not Path(path).exists()):
            continue
        for kind, host, key in [('radarr', api.radarr_url(), secrets['radarr_api_key']),
                                ('sonarr', api.sonarr_url(), secrets['sonarr_api_key'])]:
            base = api.arr_api_root(host, key)
            query = urllib.parse.urlencode({'folder': path, 'filterExistingFiles': 'true'})
            rows = api.object_list(api.http('GET', base + '/manualimport?' + query, headers=api.arr_headers(key)), 'manual imports', ('path',))
            files = []
            for row in rows:
                source = row.get('path', '')
                if (row.get('rejections') or not contained(source, api.downloads_manual())
                        or not (Path(source).resolve() == Path(path).resolve() or contained(source, path))):
                    continue
                file = {k: row[k] for k in ('path', 'quality', 'languages', 'releaseGroup', 'indexerFlags') if k in row}
                if kind == 'radarr' and (row.get('movie') or {}).get('id'):
                    file['movieId'] = row['movie']['id']
                elif kind == 'sonarr' and (row.get('series') or {}).get('id') and row.get('episodes'):
                    file['seriesId'] = row['series']['id']
                    file['episodeIds'] = [ep['id'] for ep in row['episodes']]
                else:
                    continue
                file['downloadId'] = digest
                files.append(file)
            if files:
                receipts[digest] = {'phase': 'submitting', 'files': [f['path'] for f in files], 'kind': kind}
                state.save('manual-imports', receipts)
                command = api.http('POST', base + '/command',
                                   {'name': 'ManualImport', 'files': files, 'importMode': 'Move'},
                                   headers=api.arr_headers(key))
                receipts[digest].update(phase='submitted', submittedAt=time.time(), commandId=(command or {}).get('id'))
                state.save('manual-imports', receipts)
                break


def seed_times(items):
    if api.after_download() != 'share_one_day':
        return
    for item in items:
        if (item.get('category') in {'radarr','sonarr','prowlarr'} and finished(item)
                and item.get('hash') and 'seeding_time' not in item):
            query = urllib.parse.urlencode({'hash': item['hash']})
            properties = api.http('GET', api.qbit_url() + '/api/v2/torrents/properties?' + query)
            if not isinstance(properties, dict) or 'seeding_time' not in properties:
                raise RuntimeError('Download client did not report seeding time')
            item['seeding_time'] = properties['seeding_time']


def maintain_downloads():
    payload = api.http('GET', api.qbit_url() + '/api/v2/torrents/info')
    if not isinstance(payload, list):
        raise RuntimeError('Download client returned an invalid torrent list')
    seed_times(payload)
    stop_at_goal(payload)
    # Fetch again: stop acknowledgement does not mean file handles are closed.
    payload = api.http('GET', api.qbit_url() + '/api/v2/torrents/info')
    if not isinstance(payload, list):
        raise RuntimeError('Download client returned an invalid torrent list')
    seed_times(payload)
    secrets = api.load_secrets()
    manual_imports(payload, secrets)
    observe_blocked_imports(secrets)


def observe_blocked_imports(secrets):
    """Surface Arr's explanations; never retry or delete files on its behalf."""
    previous = state.load('blocked-imports')
    observed = {}
    for kind, host in [('radarr', api.radarr_url()), ('sonarr', api.sonarr_url())]:
        key = secrets.get(kind + '_api_key')
        if not key:
            continue
        try:
            base = api.arr_api_root(host, key)
            payload = api.http('GET', base + '/queue?page=1&pageSize=100&includeUnknownSeriesItems=false&includeUnknownMovieItems=false', headers=api.arr_headers(key))
            rows = api.object_list(payload.get('records') if isinstance(payload, dict) else None, 'Arr queue', ('id',))
        except RuntimeError:
            observed.update({k: v for k, v in previous.items() if k.startswith(kind + ':')})
            continue
        for row in rows:
            if row.get('trackedDownloadState') != 'importPending' or row.get('trackedDownloadStatus') not in {'warning', 'error'}:
                continue
            token = kind + ':' + str(row['id'])
            messages = [str(text) for message in row.get('statusMessages', []) for text in message.get('messages', [])]
            observed[token] = {'firstSeen': previous.get(token, {}).get('firstSeen', time.time()),
                               'title': row.get('title', 'Download'), 'reason': '; '.join(messages)[:300] or 'Check the Arr queue in Debug'}
    if observed != previous:
        state.save('blocked-imports', observed)
