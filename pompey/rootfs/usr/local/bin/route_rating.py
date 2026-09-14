#!/usr/bin/env python3
"""Move titles into kid vs general folders from Radarr/Sonarr certification."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import pompey_state as state
from pompey_common import as_list, library_dir, media_root, movies_auto_dir, movies_dir, movies_kid_dir, radarr_url, secrets_path, sibling_auto_dir, sonarr_url, tv_auto_dir, tv_dir, tv_kid_dir


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
    auto_root: str,
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
    rows = as_list(payload)
    saved = state.load('routing-' + label)
    owned = {}
    known_roots = [kid_root, gen_root, auto_root]
    by_dest: dict[str, list] = {}
    labels: dict = {}
    for item in rows:
        cert = title_cert(item)
        path = item.get("path") or item.get("rootFolderPath") or ""
        if not path:
            continue
        token = str(item.get('id'))
        external = item.get('tmdbId' if label == 'movie' else 'tvdbId')
        previous = saved.get(token, {})
        # Remember auto mode after the first move. A manually changed path or
        # reused Arr ID relinquishes ownership; explicit library choices stay put.
        if not in_root(str(path), auto_root) and not (
            previous and previous.get('externalId') == external
            and str(path).rstrip('/') in previous.get('paths', [])
        ):
            continue
        owned[token] = {'externalId': external, 'paths': [str(path).rstrip('/')]}
        want = kid_root if kid_cert(cert, kid_set) else gen_root
        if in_root(path, want):
            continue
        ident = item.get("id")
        if ident is None:
            continue
        dest = retarget_path(str(path), want, known_roots)
        if dest.rstrip("/") == str(path).rstrip("/"):
            continue
        owned[token]["paths"].append(dest.rstrip("/"))
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
                log(f"{label} {title} -> {want} ({cert or 'unknown'})")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{label} routing failed for {len(ids)} title(s)") from exc


def arr_api_root(base: str, api_key: str) -> str:
    """Prefer /api/v3; use /api/v4 when v3 is gone."""
    host = base.rstrip("/")
    for ver in ("v3", "v4"):
        root = f"{host}/api/{ver}"
        try:
            http_json("GET", f"{root}/qualityprofile", headers=headers(api_key))
            return root
        except Exception:
            continue
    return f"{host}/api/v3"


def route_movies(key: str) -> None:
    route_library(
        key,
        f"{arr_api_root(radarr_url(), key)}/movie",
        movies_kid_dir(),
        movies_dir(),
        movies_auto_dir(),
        KID_MOVIE,
        "movie",
    )


def route_series(key: str) -> None:
    route_library(
        key,
        f"{arr_api_root(sonarr_url(), key)}/series",
        tv_kid_dir(),
        tv_dir(),
        tv_auto_dir(),
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
