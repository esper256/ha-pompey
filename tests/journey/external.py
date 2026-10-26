"""External HTTP replay and deterministic tracker/peer. Never a downloader fake."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import ssl
import struct
import threading
from urllib.parse import parse_qs, urlsplit

TITLE = "Pompey.Foundation.2024.1080p.WEB-DL"
MOVIE_ID = 1184918
PEER_IP = "10.77.0.2"
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


class World:
    def __init__(self, folder):
        self.folder = folder
        self.files = sorted(folder.iterdir())
        self.payload = b"".join(p.read_bytes() for p in self.files)
        self.pieces = [
            hashlib.sha1(self.payload[n : n + PIECE_SIZE]).digest()
            for n in range(0, len(self.payload), PIECE_SIZE)
        ]
        info = {
            "name": TITLE,
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
        self.requests = []
        self.unknown = []
        self.blocks = 0
        self.sent = 0
        self.servers = []

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
                query = parse_qs(urlsplit(self.path).query)
                if not path.startswith("/control/"):
                    world.requests.append(
                        {
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
                    world.unknown.append(f"{host}{path}: {exc}")
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

        if path == "/control/status":
            return js(
                {
                    "blocks": self.blocks,
                    "sent": self.sent,
                    "payloadBytes": len(self.payload),
                    "requests": self.requests,
                    "unknown": self.unknown,
                }
            )
        if path == "/control/release":
            self.release.set()
            return js({"ok": True})
        if path == "/announce":
            peers = socket.inet_aton(PEER_IP) + struct.pack("!H", 18181)
            return (
                "text/plain",
                bencode(
                    {"interval": 1, "complete": 1, "incomplete": 0, "peers": peers}
                ),
                200,
            )
        if path == "/download":
            return "application/x-bittorrent", self.torrent, 200
        if path in {"/api", "/api/"}:
            if query.get("apikey") != ["fixture"]:
                return js({"error": "bad key"}, 403)
            if query.get("t") == ["caps"]:
                return (
                    "application/xml",
                    b"""<caps><server version="1.0" title="Fixture"/><limits max="100" default="100"/><searching><search available="yes" supportedParams="q"/><movie-search available="yes" supportedParams="q,imdbid,tmdbid"/><tv-search available="yes" supportedParams="q,season,ep,tvdbid"/></searching><categories><category id="2000" name="Movies"><subcat id="2040" name="Movies/HD"/></category><category id="5000" name="TV"><subcat id="5040" name="TV/HD"/></category></categories></caps>""",
                    200,
                )
            tv = query.get("t") == ["tvsearch"] or any(
                int(n) >= 5000
                for n in ",".join(query.get("cat", [])).split(",")
                if n.isdigit()
            )
            title = "Pompey Fixture S01E01 1080p WEB-DL" if tv else TITLE
            category = 5040 if tv else 2040
            matches = all(
                value == [str(MOVIE_ID)]
                for key, value in query.items()
                if key == "tmdbid"
            )
            matches = matches and all(
                value[0].removeprefix("tt") == "29623480"
                for key, value in query.items()
                if key == "imdbid"
            )
            q = " ".join(query.get("q", [])).lower()
            if q and not tv:
                matches = matches and ("pompey" in q or "foundation" in q)
            offset = int(query.get("offset", ["0"])[0])
            item = f'''<item><title>{title}</title><guid isPermaLink="false">{self.digest.hex()}</guid><link>http://{PEER_IP}:18080/download</link><pubDate>Mon, 26 Oct 2026 00:00:00 GMT</pubDate><category>{category}</category><enclosure url="http://{PEER_IP}:18080/download" length="{len(self.payload)}" type="application/x-bittorrent"/><torznab:attr name="category" value="{category}"/><torznab:attr name="size" value="{len(self.payload)}"/><torznab:attr name="seeders" value="1"/><torznab:attr name="peers" value="1"/><torznab:attr name="tmdbid" value="{MOVIE_ID}"/><torznab:attr name="imdbid" value="29623480"/><torznab:attr name="infohash" value="{self.digest.hex()}"/></item>'''
            total = 1 if matches else 0
            if offset or not matches:
                item = ""
            body = f'''<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><title>Fixture</title><newznab:response offset="{offset}" total="{total}"/>{item}</channel></rss>'''
            return "application/xml", body.encode(), 200
        if host == "api.radarr.video" and path == f"/v1/movie/{MOVIE_ID}":
            return js(
                json.loads(Path("/tests/journey/replay/radarr-movie.json").read_text())
            )
        if host == "api.themoviedb.org":
            if path == f"/3/movie/{MOVIE_ID}":
                return js(self.movie())
            if path in {"/3/search/multi", "/3/search/movie"}:
                return js(
                    {
                        "page": 1,
                        "results": [dict(self.movie(), media_type="movie")],
                        "total_pages": 1,
                        "total_results": 1,
                    }
                )
            if path == f"/3/movie/{MOVIE_ID}/release_dates":
                return js(self.movie()["release_dates"])
            if path in {
                "/3/configuration",
                "/3/configuration/languages",
                "/3/configuration/countries",
                "/3/genre/movie/list",
            }:
                return js(
                    {
                        "images": {
                            "secure_base_url": "https://image.tmdb.org/t/p/",
                            "poster_sizes": ["w500"],
                        },
                        "genres": [],
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
            return js({"MediaContainer": {"Directory": []}})
        if path == "/" and host == PEER_IP:
            return js(
                {
                    "MediaContainer": {
                        "machineIdentifier": "fixture-plex",
                        "friendlyName": "Fixture Plex",
                    }
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
        self.unknown.append(f"{host}{path}")
        return js({"error": "No replay for this request"}, 404)

    def movie(self):
        return {
            "id": MOVIE_ID,
            "imdb_id": "tt29623480",
            "title": "Pompey Foundation",
            "original_title": "Pompey Foundation",
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
            "external_ids": {"imdb_id": "tt29623480"},
            "watch/providers": {"results": {}},
            "release_dates": {
                "results": [
                    {
                        "iso_3166_1": "US",
                        "release_dates": [
                            {
                                "certification": "PG",
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
            if hello[:20] != b"\x13BitTorrent protocol" or hello[28:48] != self.digest:
                raise ValueError("Wrong torrent handshake")
            sock.sendall(
                b"\x13BitTorrent protocol"
                + b"\0" * 8
                + self.digest
                + b"-PF0001-000000000000"
            )
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
                        block = self.payload[
                            index * PIECE_SIZE + begin : index * PIECE_SIZE
                            + begin
                            + length
                        ]
                        send(7, struct.pack("!II", index, begin) + block)
                        self.sent += len(block)
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
                    self.blocks += 1
                    if (
                        index >= count
                        or size > 16384
                        or begin + size
                        > min(PIECE_SIZE, len(self.payload) - index * PIECE_SIZE)
                    ):
                        raise ValueError("Invalid block request")
                    if index == count - 1 and not self.release.is_set():
                        pending.append((index, begin, size))
                    else:
                        block = self.payload[
                            index * PIECE_SIZE + begin : index * PIECE_SIZE
                            + begin
                            + size
                        ]
                        send(7, struct.pack("!II", index, begin) + block)
                        self.sent += len(block)
        except (EOFError, OSError):
            pass
        finally:
            sock.close()
