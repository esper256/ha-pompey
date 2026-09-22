"""Arr owns normal imports. Pompey stops at seeding goals, assists manual grabs,
and drops downloads that have no playable video.

Executables, scripts, archives, disc images, and obsolete video containers
are removed. Notes, split archive parts, and sample clips do not keep a
torrent. A current video that arrives beside one of those files stays, and
so do its subtitles.
Unmatched or ambiguous manual files remain available for operator review.
"""
import os
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
    changed = False
    for receipt in receipts.values():
        if receipt.get('phase') != 'submitted' or receipt.get('commandId') is None:
            continue
        kind = receipt['kind']
        host = api.radarr_url() if kind == 'radarr' else api.sonarr_url()
        key = secrets[kind + '_api_key']
        base = api.arr_api_root(host, key)
        command = api.http('GET', base + '/command/' + str(receipt['commandId']), headers=api.arr_headers(key))
        if not isinstance(command, dict):
            raise RuntimeError('Invalid Arr import command response')
        if command.get('status') in {'failed','aborted','cancelled'}:
            receipt['phase'] = 'review'
            changed = True
        elif command.get('status') == 'completed':
            # Arr commands can complete even when individual imports fail.
            receipt['phase'] = 'complete' if all(not Path(f).exists() for f in receipt['files']) else 'review'
            changed = True
    if changed:
        state.save('manual-imports', receipts)


def attention():
    notices = []
    extra = persist_notices()
    if extra:
        notices.extend(extra)
    receipts = state.load('manual-imports')
    pending = sum(r.get('phase') in {'submitting','review'} or (r.get('phase') == 'submitted' and r.get('commandId') is None) for r in receipts.values())
    if pending:
        notices.append(f'{pending} manual import(s) need review in Debug; files and import receipts were retained.')
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
            rows = api.as_list(api.http('GET', base + '/manualimport?' + query, headers=api.arr_headers(key)))
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
                receipts[digest].update(phase='submitted', commandId=(command or {}).get('id'))
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


def unsafe_name(name):
    rel = str(name).replace('\\', '/').strip()
    if not rel or rel.startswith('/'):
        return True
    return any(part == '..' for part in rel.split('/'))


def junk_roots():
    return (api.downloads_complete(), api.downloads_manual(), api.downloads_incomplete())


def path_in_junk_roots(raw):
    raw = str(raw or '').strip()
    if not raw:
        return False
    for root in junk_roots():
        try:
            path = Path(raw).resolve()
            base = Path(root).resolve()
        except OSError:
            continue
        if path == base or contained(str(path), str(base)):
            return True
    return False


def in_managed_downloads(torrent):
    """Use the current content path when qBittorrent has one.

    A category save path under downloads is not enough: after a move, the
    files may already be in a library. Those are left alone.
    """
    content = str(torrent.get('content_path') or '').strip()
    if content:
        return path_in_junk_roots(content)
    return any(path_in_junk_roots(torrent.get(key)) for key in ('save_path', 'download_path'))


def inside_download_file(path):
    for root in junk_roots():
        if contained(str(path), root):
            return True
    return False


def classify_files(files):
    """junk when no file is a playable video; mixed when junk sits beside one."""
    if not isinstance(files, list) or not files:
        return 'unknown'
    saw_video = False
    saw_junk = False
    for row in files:
        if not isinstance(row, dict):
            return 'unknown'
        name = row.get('name')
        if not isinstance(name, str) or not name.strip() or unsafe_name(name):
            return 'unknown'
        if api.is_video_name(name):
            saw_video = True
        elif api.is_junk_extension(api.extension_of(name)):
            saw_junk = True
    if saw_video and saw_junk:
        return 'mixed'
    if saw_video:
        return 'keep'
    return 'junk'


def candidate_paths(torrent, name):
    rel = str(name).replace('\\', '/').lstrip('/')
    base_name = rel.rsplit('/', 1)[-1]
    paths = []
    save = str(torrent.get('save_path') or '').strip()
    download = str(torrent.get('download_path') or '').strip()
    content = str(torrent.get('content_path') or '').strip()
    if save:
        paths.append(Path(save) / rel)
    if download:
        paths.append(Path(download) / rel)
    if content:
        content_path = Path(content)
        paths.append(content_path / rel)
        paths.append(content_path / base_name)
        if content_path.name == base_name:
            paths.append(content_path)
        paths.append(content_path.parent / rel)
    unique = []
    seen = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def remove_empty_parents(start):
    roots = []
    for root in junk_roots():
        try:
            roots.append(Path(root).resolve())
        except OSError:
            continue
    current = start
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            return
        if resolved in roots or not inside_download_file(resolved):
            return
        if current.is_symlink():
            return
        try:
            next(current.iterdir())
            return
        except StopIteration:
            pass
        except OSError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def unlink_contained_file(torrent, name, junk_only):
    if unsafe_name(name) or api.is_video_name(name):
        return False
    if junk_only and not api.is_junk_extension(api.extension_of(name)):
        return False
    for candidate in candidate_paths(torrent, name):
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        if api.is_video_name(resolved.name):
            continue
        if junk_only and not api.is_junk_extension(api.extension_of(resolved.name)):
            continue
        if not inside_download_file(resolved):
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            api.log(f'could not remove {candidate}: {exc}', 'WARNING')
            return False
        remove_empty_parents(candidate.parent)
        return True
    return False


def same_hash(value, digest):
    return str(value or '').strip().lower() == str(digest).strip().lower()


def iter_queue(base, key):
    page = 1
    while page <= 20:
        query = urllib.parse.urlencode({
            'page': page,
            'pageSize': 100,
            'includeUnknownMovieItems': 'true',
            'includeUnknownSeriesItems': 'true',
        })
        payload = api.http('GET', f'{base}/queue?{query}', headers=api.arr_headers(key))
        rows = api.as_list(payload)
        for row in rows:
            yield row
        total = payload.get('totalRecords') if isinstance(payload, dict) else None
        try:
            total_n = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_n = None
        if not rows or len(rows) < 100 or (total_n is not None and page * 100 >= total_n):
            return
        page += 1


def remove_from_queue(base, key, digest):
    removed = False
    for row in iter_queue(base, key):
        if not same_hash(row.get('downloadId') or row.get('downloadID'), digest):
            continue
        ident = row.get('id')
        if ident is None:
            continue
        query = urllib.parse.urlencode({
            'removeFromClient': 'true',
            'blocklist': 'true',
            'skipRedownload': 'true',
        })
        try:
            api.http('DELETE', f'{base}/queue/{ident}?{query}', headers=api.arr_headers(key))
        except RuntimeError as exc:
            if '-> 404' not in str(exc):
                raise
        removed = True
    return removed


def mark_history_failed(base, key, digest):
    query = urllib.parse.urlencode({'downloadId': digest, 'page': 1, 'pageSize': 50})
    payload = api.http('GET', f'{base}/history?{query}', headers=api.arr_headers(key))
    for row in api.as_list(payload):
        if not same_hash(row.get('downloadId'), digest) or row.get('id') is None:
            continue
        api.http('POST', f"{base}/history/failed/{row['id']}", {}, headers=api.arr_headers(key))


def blocklist_hash(digest, secrets):
    targets = (
        ('radarr', api.radarr_url(), secrets.get('radarr_api_key') or ''),
        ('sonarr', api.sonarr_url(), secrets.get('sonarr_api_key') or ''),
    )
    for kind, host, key in targets:
        if not key:
            continue
        try:
            base = api.arr_api_root(host, key)
            if not remove_from_queue(base, key, digest):
                mark_history_failed(base, key, digest)
        except RuntimeError as exc:
            api.log(f'{kind} could not blocklist {digest}: {exc}', 'WARNING')


def fetch_files(digest):
    query = urllib.parse.urlencode({'hash': digest})
    try:
        payload = api.http('GET', api.qbit_url() + '/api/v2/torrents/files?' + query)
    except RuntimeError as exc:
        api.log_if_new(f'junk-files:{digest}', f'could not list files for {digest}: {exc}', 'WARNING')
        return None
    if not isinstance(payload, list):
        return None
    return payload


def remove_junk_torrent(torrent, files, secrets):
    digest = str(torrent.get('hash') or '')
    blocklist_hash(digest, secrets)
    data = urllib.parse.urlencode({'hashes': digest, 'deleteFiles': 'true'}).encode()
    try:
        api.http('POST', api.qbit_url() + '/api/v2/torrents/delete', data)
    except RuntimeError as exc:
        if '-> 404' not in str(exc):
            api.log(f'could not remove junk download {digest}: {exc}', 'WARNING')
    for row in files:
        unlink_contained_file(torrent, row.get('name') or '', junk_only=False)
    api.log(f"removed junk download {torrent.get('name') or digest}")


def drop_junk_sidecars(torrent, files):
    digest = str(torrent.get('hash') or '')
    indexes = []
    names = []
    for index, row in enumerate(files):
        name = row.get('name') or ''
        if not api.is_junk_extension(api.extension_of(name)):
            continue
        indexes.append(str(index))
        names.append(name)
    if not indexes:
        return
    data = urllib.parse.urlencode({'hash': digest, 'id': '|'.join(indexes), 'priority': '0'}).encode()
    try:
        api.http('POST', api.qbit_url() + '/api/v2/torrents/filePrio', data)
    except RuntimeError as exc:
        api.log(f'could not skip junk files in {digest}: {exc}', 'WARNING')
    for name in names:
        unlink_contained_file(torrent, name, junk_only=True)
    api.log(f"removed junk files from {torrent.get('name') or digest}")


def discard_junk(items, secrets):
    """Drop downloads that a nontechnical household cannot watch.

    A playable video keeps the torrent. Executables, archives, disc images,
    and split archive parts beside that video are removed. With no playable
    video, the torrent goes, including its notes and sample clip. Skip
    metadata and allocation states, empty file lists, and unsafe names.
    Never delete a file outside the incomplete, complete, and manual folders.
    """
    for torrent in items:
        if not isinstance(torrent, dict):
            continue
        if torrent.get('category') not in {'radarr', 'sonarr', 'prowlarr'}:
            continue
        digest = str(torrent.get('hash') or '')
        if not digest or torrent.get('state') in {'metaDL', 'allocating'}:
            continue
        if not in_managed_downloads(torrent):
            continue
        files = fetch_files(digest)
        if files is None:
            continue
        decision = classify_files(files)
        if decision == 'junk':
            remove_junk_torrent(torrent, files, secrets)
        elif decision == 'mixed':
            drop_junk_sidecars(torrent, files)


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
    discard_junk(payload, secrets)
