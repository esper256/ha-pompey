"""Cancel only previously observed Pompey requests after a complete stable snapshot."""
import json
import pompey_common as api
import pompey_state as state


def requests_snapshot():
    key = api.seerr_api_key_from_disk()
    if not key:
        raise RuntimeError('Seerr setup is not complete')
    rows, seen, total = [], set(), None
    for skip in range(0, 100000, 50):
        payload = api.http('GET', api.seerr_url() + f'/api/v1/request?take=50&skip={skip}&filter=all&sort=added',
                           headers={'X-API-Key': key})
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            raise RuntimeError('Incomplete Seerr request snapshot')
        count = (payload.get('pageInfo') or {}).get('results')
        if count is not None:
            if total is not None and count != total:
                raise RuntimeError('Seerr request list changed during pagination')
            total = count
        batch = payload['results']
        for row in batch:
            if not isinstance(row, dict) or row.get('id') is None or row['id'] in seen:
                raise RuntimeError('Invalid or unstable Seerr request pagination')
            seen.add(row['id'])
            rows.append(row)
        if len(batch) < 50:
            if total is not None and total != len(rows):
                raise RuntimeError('Truncated Seerr request list')
            return rows
    raise RuntimeError('Seerr pagination limit exceeded; no cancellation is safe')


def keys(rows):
    return {identity(row) for row in rows if row.get('status') in {1,2,4,5} and identity(row)[1]}


def identity(row):
    media = row.get('media')
    value = media.get('tvdbId' if row.get('type') == 'tv' else 'tmdbId') if isinstance(media, dict) else None
    return row.get('type'), str(value) if value else ''


def reconcile_requests():
    first = requests_snapshot()
    second = requests_snapshot()
    if sorted(json.dumps(r,sort_keys=True) for r in first) != sorted(json.dumps(r,sort_keys=True) for r in second):
        raise RuntimeError('Seerr requests changed; deferring cancellation')
    uncertain = set()
    for row in second:
        kind, external_id = identity(row)
        # Seerr statuses: pending, approved, declined, failed, completed.
        if kind not in {'movie','tv'}:
            uncertain.update({'movie','tv'})
        elif row.get('status') not in {1,2,3,4,5} or not external_id:
            uncertain.add(kind)
    active = keys(second)
    saved = state.load('requests')
    previous = saved.get('owned', {})
    owned = {}
    secrets = api.load_secrets()
    for kind, host in [('movie', api.radarr_url()), ('series', api.sonarr_url())]:
        key = secrets['radarr_api_key' if kind == 'movie' else 'sonarr_api_key']
        base = api.arr_api_root(host, key)
        rows = api.http('GET', base + '/' + kind, headers=api.arr_headers(key))
        if not isinstance(rows, list):
            raise RuntimeError('Invalid Arr title snapshot')
        for row in rows:
            ident = str(row.get('id'))
            token = kind + ':' + ident
            media_type = 'movie' if kind == 'movie' else 'tv'
            external_id = row.get('tmdbId' if kind == 'movie' else 'tvdbId')
            covered = (media_type, str(external_id)) in active
            if covered:
                owned[token] = {'externalId': row.get('tmdbId' if kind == 'movie' else 'tvdbId')}
                continue
            if token not in previous:
                continue  # Arr-only titles and a first observation are never cancellations.
            if previous[token].get('externalId') != external_id:
                continue  # Never apply old ownership to a reused Arr database ID.
            if media_type in uncertain:
                # Keep ownership until metadata resolves. Other media types can
                # still reconcile, but absence is unsafe for this type today.
                owned[token] = previous[token]
                continue
            updated = dict(row, monitored=False)
            if kind == 'series':
                updated['monitorNewItems'] = 'none'
                updated['seasons'] = [dict(season, monitored=False) for season in row.get('seasons', [])]
            if updated != row:
                api.http('PUT', base + '/' + kind + '/' + ident, updated, headers=api.arr_headers(key))
            commands = api.http('GET', base + '/command', headers=api.arr_headers(key))
            for command in api.as_list(commands):
                body = command.get('body') or {}
                ids = body.get('movieIds' if kind == 'movie' else 'seriesIds') or []
                matches = body.get('movieId' if kind == 'movie' else 'seriesId') == row['id'] or ids == [row['id']]
                if matches and command.get('status') in {'queued', 'started'} and 'Search' in command.get('name', ''):
                    api.http('DELETE', base + '/command/' + str(command['id']), headers=api.arr_headers(key))
            # Stop monitoring/searching. Keep already-downloaded or in-flight data;
            # request deletion must never become a removeFromClient file deletion.
    state.save('requests', {'version': 1, 'owned': owned})
