"""External HTTP replay and deterministic tracker/peer. Never a downloader fake."""

import hashlib
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import select
import socket
import ssl
import struct
import threading
import time
from urllib.parse import parse_qs, urlsplit
from catalogue import cases, offers
from tv_replay import skyhook, tv as tv_metadata, season as tv_season
from torrent_files import TorrentFiles
from fixture_network import PEER_IP, DnsServer
from support import save_json

PIECE_SIZE = 256 * 1024


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(map(bencode, value)) + b"e"
    return (
        b"d"
        + b"".join(bencode(k) + bencode(v) for k, v in sorted(value.items()))
        + b"e"
    )


def read_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        data = sock.recv(count - len(result))
        if not data:
            raise EOFError("Peer disconnected")
        result.extend(data)
    return bytes(result)


class Torrent:
    def __init__(self, case, folder):
        self.folder = folder
        self.data = TorrentFiles(folder)
        self.files = self.data.files
        self.size = self.data.size
        self.pieces = [
            hashlib.sha1(self.data.read(n, min(PIECE_SIZE, self.size - n))).digest()
            for n in range(0, self.size, PIECE_SIZE)
        ]
        info = {
            "name": case.release,
            "private": 1,
            "piece length": PIECE_SIZE,
            "pieces": b"".join(self.pieces),
            "files": [
                {"length": p.stat().st_size, "path": [p.name]} for p in self.files
            ],
        }
        self.digest = hashlib.sha1(bencode(info)).digest()
        self.torrent = bencode(
            {"announce": f"http://{PEER_IP}:18080/announce", "info": info}
        )
        self.release = threading.Event()
        self.blocks = 0
        self.sent = 0
        self.started = float(Path("/tmp/world/started").read_text())
        self.timings = {}

    def stamp(self, name):
        self.timings.setdefault(name, round(time.monotonic() - self.started, 3))

    def serve_peer(self, sock, hello):
        try:
            sock.settimeout(15)
            if hello[:20] != b"\x13BitTorrent protocol" or hello[28:48] != self.digest:
                raise ValueError("Wrong torrent handshake")
            sock.sendall(
                b"\x13BitTorrent protocol"
                + b"\0" * 8
                + self.digest
                + b"-PF0001-000000000000"
            )
            self.stamp("peer-connected")
            count = len(self.pieces)
            bits = bytearray((count + 7) // 8)
            for n in range(count):
                bits[n // 8] |= 1 << (7 - n % 8)

            def send(kind, data=b""):
                sock.sendall(struct.pack("!I", len(data) + 1) + bytes([kind]) + data)

            send(5, bits)
            send(1)
            pending = []
            while True:
                if self.release.is_set():
                    for index, begin, length in pending:
                        block = self.data.read(index * PIECE_SIZE + begin, length)
                        send(7, struct.pack("!II", index, begin) + block)
                        self.sent += len(block)
                        if self.sent >= self.size:
                            self.stamp("payload-sent")
                    pending = []
                if not select.select([sock], [], [], 0.05)[0]:
                    continue
                length = struct.unpack("!I", read_exact(sock, 4))[0]
                if not length:
                    continue
                if length > 1024 * 1024:
                    raise ValueError("Oversize peer message")
                message = read_exact(sock, length)
                if message[0] == 6:
                    index, begin, size = struct.unpack("!III", message[1:])
                    self.stamp("first-block-requested")
                    self.blocks += 1
                    if (
                        index >= count
                        or not 0 < size <= 16384
                        or begin + size
                        > min(PIECE_SIZE, self.size - index * PIECE_SIZE)
                    ):
                        raise ValueError("Invalid block request")
                    if index == count - 1 and not self.release.is_set():
                        pending.append((index, begin, size))
                    else:
                        block = self.data.read(index * PIECE_SIZE + begin, size)
                        send(7, struct.pack("!II", index, begin) + block)
                        self.sent += len(block)
                        if self.sent >= self.size:
                            self.stamp("payload-sent")
        except (EOFError, OSError):
            pass
        finally:
            sock.close()


class World:
    def __init__(self, folder):
        self.cases = cases()
        self.offers = {o.movie_id: o for c in self.cases for o in offers(c)}
        self.torrents = {
            token: Torrent(offer, folder / offer.release)
            for token, offer in self.offers.items()
        }
        self.by_hash = {t.digest: t for t in self.torrents.values()}
        self.unknown = []
        self.servers = []
        self.started = float(Path("/tmp/world/started").read_text())
        self.record_lock = threading.Lock()
        self.control_file = Path("/data/pompey-fixture/state.json")
        self.controls = (
            json.loads(self.control_file.read_text())
            if self.control_file.exists()
            else {}
        )

    def record(self, row):
        row = {"elapsedSeconds": round(time.monotonic() - self.started, 3), **row}
        with self.record_lock:
            with Path("/tmp/world/events/transcript.jsonl").open("a") as output:
                output.write(json.dumps(row) + "\n")

    def unexpected(self, message):
        with self.record_lock:
            self.unknown.append(message)
            save_json("/tmp/world/events/unknown.json", self.unknown)

    def certification(self, case):
        return self.controls.get(
            str(case.movie_id), "" if case.late_certification else case.certification
        )

    def start(self, cert, key):
        world = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                path = urlsplit(self.path).path
                host = self.headers.get("Host", "").split(":")[0]
                query = parse_qs(urlsplit(self.path).query, encoding="latin-1")
                if not path.startswith("/control/"):
                    world.record(
                        {
                            "kind": "http",
                            "source": self.client_address[0],
                            "elapsedSeconds": round(
                                time.monotonic() - world.started, 3
                            ),
                            "host": host,
                            "path": path,
                            "query": {
                                k: v
                                for k, v in query.items()
                                if k
                                not in {
                                    "api_key",
                                    "X-Plex-Token",
                                    "info_hash",
                                    "peer_id",
                                }
                            },
                        }
                    )
                try:
                    content_type, body, code = world.respond(host, path, query)
                except Exception as exc:
                    world.unexpected(f"{host}{path}: {exc}")
                    content_type, body, code = (
                        "application/json",
                        json.dumps({"error": str(exc)}).encode(),
                        500,
                    )
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        for port in [80, 443, 18080]:
            server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            server.daemon_threads = True
            if port == 443:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(cert, key)
                server.socket = context.wrap_socket(server.socket, server_side=True)
            self.servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        DnsServer(self.record, self.unexpected).start()
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 18181))
        listener.listen()

        def accept():
            while True:
                sock, _ = listener.accept()
                threading.Thread(
                    target=self.serve_peer, args=(sock,), daemon=True
                ).start()

        threading.Thread(target=accept, daemon=True).start()

    def respond(self, host, path, query):
        def js(value, code=200):
            return "application/json", json.dumps(value).encode(), code

        if host != PEER_IP and (
            path.startswith(("/control/", "/download/"))
            or path in {"/announce", "/api", "/api/"}
            or (path == "/library/sections" and host != "198.18.0.2")
        ):
            self.unexpected(f"{host}{path}")
            return js({"error": "Wrong fixture host"}, 404)
        if path.startswith("/control/publish/"):
            ident = path.rsplit("/", 1)[1]
            self.controls["visible:" + ident] = time.time()
            save_json(self.control_file, self.controls)
            return js({"ok": True})
        if path.startswith("/control/fault/"):
            name, value = path.split("/")[-2:]
            if name not in {"indexer", "plex"} or value not in {"off", "on"}:
                raise ValueError("Invalid fixture fault control")
            self.controls["fault:" + name] = value == "on"
            return js({"ok": True})
        if path == "/control/plex-library":
            self.controls["plex-library"] = True
            return js({"ok": True})
        if path.startswith("/control/certify/"):
            ident = path.rsplit("/", 1)[1]
            case = next(c for c in self.cases if str(c.movie_id) == ident)
            self.controls[ident] = case.certification
            save_json(self.control_file, self.controls)
            return js({"ok": True})
        if path.startswith("/control/status/"):
            torrent = self.torrents[int(path.rsplit("/", 1)[1])]
            return js(
                {
                    "timings": torrent.timings.copy(),
                    "blocks": torrent.blocks,
                    "released": torrent.release.is_set(),
                    "sent": torrent.sent,
                    "payloadBytes": torrent.size,
                    "infoHash": torrent.digest.hex(),
                    "unknown": self.unknown,
                }
            )
        if path.startswith("/control/release/"):
            torrent = self.torrents[int(path.rsplit("/", 1)[1])]
            torrent.stamp("final-piece-released")
            torrent.release.set()
            return js({"ok": True})
        if path == "/announce":
            torrent = self.by_hash[query["info_hash"][0].encode("latin-1")]
            torrent.stamp("tracker-announced")
            peers = socket.inet_aton(PEER_IP) + struct.pack("!H", 18181)
            return (
                "text/plain",
                bencode(
                    {"interval": 1, "complete": 1, "incomplete": 0, "peers": peers}
                ),
                200,
            )
        if path.startswith("/download/"):
            torrent = self.torrents[int(path.rsplit("/", 1)[1])]
            torrent.stamp("torrent-served")
            return "application/x-bittorrent", torrent.torrent, 200
        if path in {"/api", "/api/"}:
            if self.controls.get("fault:indexer"):
                return js({"error": "Intentional indexer outage"}, 503)
            if query.get("apikey") != ["fixture"]:
                return js({"error": "bad key"}, 403)
            if query.get("t") == ["caps"]:
                return (
                    "application/xml",
                    b"""<caps><server version="1.0" title="Fixture"/><limits max="100" default="100"/><searching><search available="yes" supportedParams="q"/><movie-search available="yes" supportedParams="q,imdbid,tmdbid"/><tv-search available="yes" supportedParams="q,season,ep,tvdbid"/></searching><categories><category id="2000" name="Movies"><subcat id="2040" name="Movies/HD"/></category><category id="5000" name="TV"><subcat id="5040" name="TV/HD"/><subcat id="5070" name="TV/Anime"/></category></categories></caps>""",
                    200,
                )
            tv = query.get("t") == ["tvsearch"] or any(
                int(n) >= 5000
                for n in ",".join(query.get("cat", [])).split(",")
                if n.isdigit()
            )
            selected = [
                c
                for c in self.cases
                if (c.kind == "tv") == tv
                and (
                    c.index != 4
                    or self.controls.get("visible:" + str(c.movie_id), False)
                )
            ]
            if "tmdbid" in query:
                selected = [c for c in selected if query["tmdbid"] == [str(c.movie_id)]]
            if "imdbid" in query:
                selected = [
                    c
                    for c in selected
                    if query["imdbid"][0].removeprefix("tt") == c.imdb_id
                ]
            q = re.sub(r"[^a-z0-9]", "", " ".join(query.get("q", [])).lower())
            if "tvdbid" in query:
                selected = [c for c in selected if query["tvdbid"] == [str(c.tvdb_id)]]
            if q:
                selected = [
                    c
                    for c in selected
                    if q in re.sub(r"[^a-z0-9]", "", c.title.lower())
                ]
            selected_offers = [o for c in selected for o in offers(c)]
            offset = int(query.get("offset", ["0"])[0])
            items = []
            for offer in selected_offers[offset : offset + 100]:
                case = offer.case
                torrent = self.torrents[offer.movie_id]
                category = (5070 if case.anime else 5040) if tv else 2040
                seeders = 4 - offer.variant if case.index in {1, 6} else 1
                url = f"http://{PEER_IP}:18080/download/{offer.movie_id}"
                published = formatdate(
                    self.controls.get("visible:" + str(case.movie_id), 1792972800),
                    usegmt=True,
                )
                items.append(
                    f'''<item><title>{offer.release}</title><guid isPermaLink="false">{torrent.digest.hex()}</guid><link>{url}</link><pubDate>{published}</pubDate><category>{category}</category><enclosure url="{url}" length="{torrent.size}" type="application/x-bittorrent"/><torznab:attr name="category" value="{category}"/><torznab:attr name="size" value="{torrent.size}"/><torznab:attr name="seeders" value="{seeders}"/><torznab:attr name="peers" value="1"/><torznab:attr name="tmdbid" value="{case.movie_id}"/><torznab:attr name="tvdbid" value="{case.tvdb_id}"/><torznab:attr name="imdbid" value="{case.imdb_id}"/><torznab:attr name="infohash" value="{torrent.digest.hex()}"/></item>'''
                )
            body = f'''<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><title>Fixture</title><newznab:response offset="{offset}" total="{len(selected_offers)}"/>{"".join(items)}</channel></rss>'''
            return "application/xml", body.encode(), 200
        for case in self.cases:
            if case.kind == "tv":
                if (
                    host == "skyhook.sonarr.tv"
                    and path == f"/v1/tvdb/shows/en/{case.tvdb_id}"
                ):
                    return js(skyhook(case))
                if host == "api.themoviedb.org":
                    if path == f"/3/tv/{case.movie_id}":
                        return js(tv_metadata(case))
                    if path == f"/3/tv/{case.movie_id}/content_ratings":
                        return js(tv_metadata(case)["content_ratings"])
                    for number in case.seasons:
                        if path == f"/3/tv/{case.movie_id}/season/{number}":
                            return js(tv_season(case, number))
                continue
            if host == "api.radarr.video" and path == f"/v1/movie/{case.movie_id}":
                movie = json.loads(
                    Path("/tests/journey/replay/radarr-movie.json").read_text()
                )
                movie.update(
                    TmdbId=case.movie_id,
                    ImdbId="tt" + case.imdb_id,
                    Title=case.title,
                    OriginalTitle=case.title,
                    TitleSlug=case.title.lower() + "-" + str(case.movie_id),
                    Certifications=[
                        {"Country": "US", "Certification": self.certification(case)}
                    ],
                )
                return js(movie)
            if host == "api.themoviedb.org" and path == f"/3/movie/{case.movie_id}":
                return js(self.movie(case))
            if (
                host == "api.themoviedb.org"
                and path == f"/3/movie/{case.movie_id}/release_dates"
            ):
                return js(self.movie(case)["release_dates"])
        if host == "api.themoviedb.org":
            if path in {"/3/search/multi", "/3/search/movie", "/3/search/tv"}:
                query_text = " ".join(query.get("query", [])).lower()
                matches = [
                    dict(
                        tv_metadata(c) if c.kind == "tv" else self.movie(c),
                        media_type=c.kind,
                    )
                    for c in self.cases
                    if query_text in c.title.lower()
                    and (path == "/3/search/multi" or path.endswith("/" + c.kind))
                ]
                return js(
                    {
                        "page": 1,
                        "results": matches,
                        "total_pages": 1,
                        "total_results": len(matches),
                    }
                )
        if host == "plex.tv" and path == "/users/account.json":
            return js(
                {
                    "user": {
                        "id": 1,
                        "email": "fixture@example.test",
                        "username": "Fixture",
                        "authToken": "synthetic-token",
                        "thumb": "",
                    }
                }
            )
        if host == "plex.tv" and path == "/api/resources":
            return "application/xml", b'<MediaContainer size="0"/>', 200
        if path == "/library/sections":
            if self.controls.get("fault:plex"):
                return js({"error": "Intentional media-server outage"}, 503)
            libraries = [{"key": "1", "title": "Fixture movies", "type": "movie", "agent": "tv.plex.agents.movie", "scanner": "Plex Movie"}] if self.controls.get("plex-library") else []
            return js({"MediaContainer": {"Directory": libraries}})
        if path == "/" and host in {PEER_IP, "198.18.0.2"}:
            return js(
                {
                    "MediaContainer": {
                        "machineIdentifier": "fixture-plex",
                        "friendlyName": "Fixture Plex",
                    }
                }
            )
        if host == "thexem.info" and path in {
            "/map/havemap",
            "/map/allNames",
            "/map/all",
        }:
            # There are no alternate scene names/numbers for our fictional series.
            return js(
                {
                    "result": "success",
                    "data": {} if path == "/map/allNames" else [],
                    "message": "",
                }
            )
        # These external checks are intentionally unavailable, not fake successes.
        if host in {
            "api.github.com",
            "indexers.prowlarr.com",
            "radarr.servarr.com",
            "services.sonarr.tv",
            "prowlarr.servarr.com",
        }:
            return js(
                {
                    "error": "External update/telemetry checks unavailable in isolated acceptance"
                },
                503,
            )
        self.unexpected(f"{host}{path}")
        return js({"error": "No replay for this request"}, 404)

    def movie(self, case):
        return {
            "id": case.movie_id,
            "imdb_id": "tt" + case.imdb_id,
            "title": case.title,
            "original_title": case.title,
            "original_language": "en",
            "runtime": 1,
            "overview": "Synthetic short film for acceptance.",
            "release_date": "2024-01-01",
            "status": "Released",
            "adult": False,
            "video": False,
            "genres": [],
            "production_companies": [],
            "production_countries": [],
            "spoken_languages": [],
            "poster_path": None,
            "backdrop_path": None,
            "budget": 0,
            "revenue": 0,
            "popularity": 1,
            "vote_average": 7,
            "vote_count": 1,
            "credits": {"cast": [], "crew": []},
            "videos": {"results": []},
            "keywords": {"keywords": []},
            "external_ids": {"imdb_id": "tt" + case.imdb_id},
            "watch/providers": {"results": {}},
            "release_dates": {
                "results": [
                    {
                        "iso_3166_1": "US",
                        "release_dates": [
                            {
                                "certification": self.certification(case),
                                "release_date": "2024-01-01T00:00:00Z",
                                "type": 4,
                            }
                        ],
                    }
                ]
            },
        }

    def serve_peer(self, sock):
        try:
            sock.settimeout(15)
            hello = read_exact(sock, 68)
            torrent = self.by_hash.get(hello[28:48])
            if torrent is None:
                raise ValueError("Unknown torrent in peer handshake")
            torrent.serve_peer(sock, hello)
        except (EOFError, OSError):
            sock.close()
        except ValueError as exc:
            self.unexpected(str(exc))
            sock.close()
