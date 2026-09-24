#!/usr/bin/env python3
"""Move titles into kid vs general folders from Radarr/Sonarr certification."""
from __future__ import annotations

import json
from pathlib import Path
import os
import sys
import time
import urllib.request
import pompey_state as state
from pompey_common import arr_api_root, object_list, legacy_movies_auto_dir, legacy_tv_auto_dir, movies_auto_dir, movies_dir, movies_kid_dir, radarr_url, secrets_path, sonarr_url, tv_auto_dir, tv_dir, tv_kid_dir


AUTO_FOLDER = "By Rating"


KID_MOVIE = {"G", "PG", "PG-13"}
KID_TV = {"TV-Y", "TV-Y7", "TV-G", "TV-PG"}


def log(msg: str, level: str = "INFO") -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {level}: [route_rating.py] {msg}", flush=True)


def http_json(method: str, url: str, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else None


def headers(key: str) -> dict:
    return {"X-Api-Key": key}


def kid_cert(cert: str, kid_set: set[str]) -> bool:
    raw = (cert or "").strip()
    if not raw:
        return False
    return raw in kid_set or raw.upper() in kid_set


def title_cert(item: dict) -> str:
    """Radarr/Sonarr certification is often empty until metadata fills in."""
    for key in ("certification", "contentRating", "originalCertification"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    ratings = item.get("ratings")
    if isinstance(ratings, dict):
        for nested in ratings.values():
            if isinstance(nested, dict):
                for key in ("certification", "value"):
                    val = nested.get(key)
                    if isinstance(val, str) and val.strip() and not val.replace(".", "").isdigit():
                        return val.strip()
            elif isinstance(nested, str) and nested.strip():
                return nested.strip()
    return ""


def in_root(path: str, root: str) -> bool:
    path = (path or "").rstrip("/")
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def retarget_path(path: str, dest_root: str, known_roots: list[str]) -> str:
    """Replace a known library root prefix. Keep the title folder name."""
    path = (path or "").rstrip("/")
    dest_root = dest_root.rstrip("/")
    for root in known_roots:
        root = (root or "").rstrip("/")
        if not root:
            continue
        if path == root:
            return dest_root
        if path.startswith(root + "/"):
            return dest_root + path[len(root) :]
    name = path.rsplit("/", 1)[-1] if path else ""
    return f"{dest_root}/{name}" if name else dest_root


def route_library(
    key: str,
    list_url: str,
    kid_root: str,
    gen_root: str,
    auto_roots: list[str],
    kid_set: set[str],
    label: str,
) -> None:
    """Move titles with Arr's editor. A full PUT keeps the old path and no-ops.

    Production logged MoveMovieService from Not Kid Friendly to the same folder
    every minute because the movie body still had path set to the current
    location. Editor takes rootFolderPath + moveFiles and computes the dest.
    """
    payload = http_json("GET", list_url, headers=headers(key))
    if not isinstance(payload, list):
        raise RuntimeError('Invalid Arr routing snapshot')
    rows = object_list(payload, 'routing titles', ('id', 'path'))
    saved = state.load('routing-' + label)
    owned = {}
    autos = [root.rstrip("/") for root in auto_roots if root]
    known_roots = [kid_root, gen_root, *autos]
    by_dest: dict[str, list] = {}
    labels: dict = {}
    now = time.time()
    commands = None
    for item in rows:
        cert = title_cert(item)
        path = item.get("path") or item.get("rootFolderPath") or ""
        if not path:
            continue
        token = str(item.get('id'))
        external = item.get('tmdbId' if label == 'movie' else 'tvdbId')
        previous = saved.get(token, {})
        if previous.get('externalId') != external:
            previous = {}
        pending = previous.get('pending')
        # The editor may normalize the title's folder name. Observe its actual
        # destination rather than guessing the final name from the source.
        if pending:
            source = Path(pending['source'])
            at_destination = in_root(path, pending['destination'])
            source_files = source.exists() and any(p.is_file() for p in source.rglob('*'))
            if at_destination and not source_files and (not pending['hadFiles'] or (Path(path).is_dir() and any(p.is_file() for p in Path(path).rglob('*')))):
                previous = {'externalId': external, 'paths': [str(path).rstrip('/')]}
                pending = None
            elif str(path).rstrip('/') not in previous.get('paths', []) and not at_destination:
                continue  # A manual move relinquishes automatic ownership.
            else:
                owned[token] = previous
                if now - pending['submittedAt'] < 120:
                    continue
                if commands is None:
                    commands = object_list(http_json('GET', list_url.rsplit('/', 1)[0] + '/command', headers=headers(key)), 'move commands', ('id', 'name', 'status'))
                active = any(c['status'] in {'queued', 'started'} and c['name'] == ('BulkMoveMovie' if label == 'movie' else 'BulkMoveSeries')
                             and any(str(x.get('movieId' if label == 'movie' else 'seriesId')) == token
                                     for x in (c.get('body') or {}).get('movies' if label == 'movie' else 'series', []))
                             for c in commands)
                if active:
                    if now - pending['submittedAt'] > 900:
                        pending['error'] = 'Arr is still moving files; waiting for completion.'
                    continue
                pending['error'] = 'Files have not reached the intended library. Check storage and the Arr queue in Debug.'
                # Never submit a move from a new path while old payload remains.
                if at_destination or now < pending['retryAt']:
                    continue
        if not any(in_root(str(path), root) for root in autos) and not (
            previous and str(path).rstrip('/') in previous.get('paths', [])
        ):
            continue
        want = kid_root if kid_cert(cert, kid_set) else gen_root
        owned[token] = {'externalId': external, 'title': item.get('title', label), 'paths': [str(path).rstrip('/')]}
        if in_root(path, want):
            continue
        ident = item['id']
        dest = retarget_path(str(path), want, known_roots)
        if dest.rstrip('/') == str(path).rstrip('/'):
            continue
        attempts = pending.get('attempts', 0) + 1 if pending else 1
        owned[token]['paths'].append(dest.rstrip('/'))
        owned[token]['pending'] = {'source': str(path), 'destination': want, 'submittedAt': now,
                                  'hadFiles': bool(item.get('hasFile') or (item.get('statistics') or {}).get('episodeFileCount')
                                                   or (Path(path).exists() and any(p.is_file() for p in Path(path).rglob('*')))),
                                  'attempts': attempts, 'retryAt': now + min(3600, 300 * 2 ** min(attempts - 1, 4))}
        if pending and pending.get('error'):
            owned[token]['pending']['error'] = pending['error']
        by_dest.setdefault(want, []).append(ident)
        labels[ident] = (item.get("title"), cert)
    # Save intent before moving; a crash between editor acknowledgement and
    # observation must not forget that the title was automatically routed.
    state.save("routing-" + label, owned)
    if not by_dest:
        return
    id_key = "movieIds" if label == "movie" else "seriesIds"
    for want, ids in by_dest.items():
        try:
            http_json(
                "PUT",
                f"{list_url}/editor",
                {id_key: ids, "rootFolderPath": want, "moveFiles": True},
                headers=headers(key),
            )
            for ident in ids:
                title, cert = labels[ident]
                log(f"Requested move: {label} {title} -> {want} ({cert or 'unknown'})")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{label} routing failed for {len(ids)} title(s)") from exc


def route_movies(key: str) -> None:
    route_library(
        key,
        f"{arr_api_root(radarr_url(), key)}/movie",
        movies_kid_dir(),
        movies_dir(),
        [movies_auto_dir(), legacy_movies_auto_dir()],
        KID_MOVIE,
        "movie",
    )


def route_series(key: str) -> None:
    route_library(
        key,
        f"{arr_api_root(sonarr_url(), key)}/series",
        tv_kid_dir(),
        tv_dir(),
        [tv_auto_dir(), legacy_tv_auto_dir()],
        KID_TV,
        "series",
    )


def run_once() -> None:
    secrets = json.load(open(secrets_path(), encoding="utf-8"))
    route_movies(secrets["radarr_api_key"])
    route_series(secrets["sonarr_api_key"])


def main(argv: list[str] | None = None) -> None:
    once = "--once" in (argv if argv is not None else sys.argv)
    if once:
        run_once()
        return
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            log(str(exc))
        time.sleep(60)


if __name__ == "__main__":
    main()
