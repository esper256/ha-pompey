"""Local Torznab catalogue with an auditable request ledger. No peer traffic."""
import hashlib
import re
import threading
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.sax.saxutils import escape, quoteattr
from fake_source import caps_xml


def digest(row):
    return hashlib.sha1(row['title'].encode()).hexdigest()


def matching_rows(rows, query):
    season = query.get('season', [''])[0]
    episode = query.get('ep', [''])[0]
    q = query.get('q', [''])[0]
    # Empty queries model RSS/test calls. A specific episode
    # query does not magically expose an entire season pack.
    if season.isdigit(): rows = [r for r in rows if r.get('season') in {None,int(season)}]
    if episode.isdigit(): rows = [r for r in rows if r.get('episode')==int(episode)]
    absolute = re.search(r'(?:^|\s)(\d+)$', q)
    if absolute and not season:
        rows = [r for r in rows if int(absolute[1]) in {r.get('absolute'),r.get('episode')}]
    return rows


class Catalogue:
    def __init__(self):
        self.rows = []
        self.requests = []
        self.lock = threading.Lock()

    def reset(self, rows):
        with self.lock:
            self.rows = rows
            self.requests.clear()

    def snapshot(self):
        with self.lock:
            return list(self.requests)

    def handler(self):
        catalogue = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query)
                kind = query.get('t', ['search'])[0]
                with catalogue.lock:
                    entry={'path':urlsplit(self.path).path,'query':{k:v for k,v in query.items() if k!='apikey'}}
                    catalogue.requests.append(entry)
                    rows = list(catalogue.rows)
                if kind == 'caps':
                    body = caps_xml().replace('<subcat id="5040" name="TV/HD"/>', '<subcat id="5040" name="TV/HD"/><subcat id="5070" name="TV/Anime"/>')
                else:
                    rows = matching_rows(rows, query)
                    total=len(rows)
                    offset=int(query.get('offset',['0'])[0]);limit=min(100,int(query.get('limit',['100'])[0]))
                    rows=rows[offset:offset+limit]
                    entry.update(results=len(rows),total=total)
                    items=[]
                    for row in rows:
                        magnet='magnet:?'+urlencode({'xt':'urn:btih:'+digest(row),'dn':row['title'],'tr':'udp://127.0.0.1:9'},safe=':')
                        attrs={'category':5070,'size':row['size'],'seeders':row['seeders'],'peers':row['seeders']+row['leechers'],'infohash':digest(row),'magneturl':magnet}
                        tags=''.join(f'<torznab:attr name="{k}" value={quoteattr(str(v))}/>' for k,v in attrs.items())
                        items.append(f'<item><title>{escape(row["title"])}</title><guid>{digest(row)}</guid><link>{escape(magnet)}</link><pubDate>{format_datetime(datetime.now(timezone.utc)-timedelta(days=row.get('age_days',365)))}</pubDate><size>{row["size"]}</size><enclosure url={quoteattr(magnet)} length="{row["size"]}" type="application/x-bittorrent"/>{tags}</item>')
                    body='<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><title>Pompey anime fixture</title>'+f'<newznab:response offset="{offset}" total="{total}"/>'+''.join(items)+'</channel></rss>'
                data=body.encode()
                self.send_response(200);self.send_header('Content-Type','application/xml');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        return Handler
