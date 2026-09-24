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


def requested_seasons(rows, external_id):
    """None means insufficient evidence; never infer seasons from missing data."""
    result = set()
    for request in rows:
        if identity(request) != ('tv', str(external_id)) or request.get('status') not in {1, 2, 4, 5}:
            continue
        seasons = request.get('seasons')
        if not isinstance(seasons, list) or not seasons:
            return None
        for season in seasons:
            if (not isinstance(season, dict) or type(season.get('seasonNumber')) is not int
                    or season['seasonNumber'] < 0 or season.get('status', 2) not in {1, 2, 3, 4, 5}):
                return None
            if season.get('status') != 3:
                result.add(season['seasonNumber'])
    return result


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
        rows = api.object_list(rows, 'Arr titles', ('id', 'tmdbId' if kind == 'movie' else 'tvdbId'))
        for row in rows:
            ident = str(row.get('id'))
            token = kind + ':' + ident
            media_type = 'movie' if kind == 'movie' else 'tv'
            external_id = row.get('tmdbId' if kind == 'movie' else 'tvdbId')
            covered = (media_type, str(external_id)) in active
            prior = previous.get(token, {})
            if prior.get('externalId') != external_id:
                prior = {}
            seasons = requested_seasons(second, external_id) if kind == 'series' else None
            if covered:
                owned[token] = {'externalId': external_id}
                if seasons is not None:
                    owned[token]['seasons'] = sorted(seasons)
                elif 'seasons' in prior:
                    owned[token]['seasons'] = prior['seasons']
                if kind != 'series' or seasons is None or 'seasons' not in prior:
                    continue
            if not prior:
                continue  # No observation of ownership, or a reused Arr ID.
            if media_type in uncertain:
                owned[token] = prior
                continue
            removed = None
            if kind == 'series' and 'seasons' in prior:
                if seasons is None:
                    owned[token] = prior
                    continue
                removed = set(prior['seasons']) - seasons
                if not removed:
                    continue
                season_rows = api.object_list(row.get('seasons'), 'Sonarr seasons', ('seasonNumber', 'monitored'))
                updated = dict(row, seasons=[dict(s, monitored=False) if s['seasonNumber'] in removed else dict(s) for s in season_rows])
                # Preserve manual and future-season monitoring while any other
                # season remains monitored. Never turn monitoring back on.
                if not covered and not any(s['monitored'] for s in updated['seasons']):
                    updated.update(monitored=False, monitorNewItems='none')
            else:
                updated = dict(row, monitored=False)
                if kind == 'series':  # Legacy records predate season ownership.
                    updated['monitorNewItems'] = 'none'
                    updated['seasons'] = [dict(s, monitored=False) for s in row.get('seasons', [])]
            if updated != row:
                api.http('PUT', base + '/' + kind + '/' + ident, updated, headers=api.arr_headers(key))
            commands = api.http('GET', base + '/command', headers=api.arr_headers(key))
            for command in api.object_list(commands, 'Arr commands', ('id', 'name', 'status')):
                body = command.get('body') or {}
                ids = body.get('movieIds' if kind == 'movie' else 'seriesIds') or []
                matches = body.get('movieId' if kind == 'movie' else 'seriesId') == row['id'] or ids == [row['id']]
                if removed is not None and updated.get('monitored', True):
                    matches = matches and body.get('seasonNumber') in removed
                if matches and command.get('status') == 'queued' and 'Search' in command.get('name', ''):
                    api.http('DELETE', base + '/command/' + str(command['id']), headers=api.arr_headers(key))
            # Stop monitoring/searching. Keep already-downloaded or in-flight data;
            # request deletion must never become a removeFromClient file deletion.
    state.save('requests', {'version': 2, 'owned': owned})
