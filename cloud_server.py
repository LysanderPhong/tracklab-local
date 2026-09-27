"""Public portal + authenticated relay. Place behind HTTPS; no device SDK here."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs

from relay import Relay, RelayError
import map_service
import routes

ROOT = Path(__file__).resolve().parent
VERSION = '0.4.0'
PORT = int(os.environ.get('TRACKLAB_CLOUD_PORT') or os.environ.get('PORT', '8770'))
ORIGIN = (os.environ.get('TRACKLAB_PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL')
          or f'http://127.0.0.1:{PORT}').rstrip('/')
relay = Relay()


def asset(path):
    if path in ('/', '/fixed'):
        path = '/portal/index.html'
    elif path == '/connect':
        path = '/portal/connect.html'
    if not path.startswith('/portal/'):
        return None
    file = (ROOT/path.lstrip('/')).resolve()
    if not file.is_relative_to(ROOT/'portal') or not file.is_file():
        return None
    if file.suffix not in {'.html', '.js', '.css', '.svg', '.png', '.apk', '.zip'}:
        return None
    return file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream'


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, address)
    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'TrackLab'
    def setup(self):
        super().setup()
        self.connection.settimeout(15)
    def log_message(self, *_):
        pass  # no addresses, search strings, coordinates or pairing tokens in logs
    def respond(self, status, body, kind='application/json; charset=utf-8', cache='no-store'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        for k, v in {'Content-Type': kind, 'Content-Length': str(len(body)), 'Cache-Control': cache,
                     'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin',
                     'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                     'Permissions-Policy': 'geolocation=()'}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    def guard(self):
        if self.headers.get('Host') != urlsplit(ORIGIN).netloc:
            raise RelayError('网址不匹配。', 403)
        origin = self.headers.get('Origin')
        if origin and origin != ORIGIN:
            raise RelayError('请求来源不匹配。', 403)
    def credentials(self):
        authorization = self.headers.get('Authorization', '')
        if not authorization.startswith('Bearer ') or '.' not in authorization:
            raise RelayError('请先配对连接程序。', 401)
        return authorization[7:].split('.', 1)
    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise RelayError('不支持这种请求格式。')
        size = int(self.headers.get('Content-Length', '0'))
        if size < 1 or size > 32768 or not self.headers.get('Content-Type', '').startswith('application/json'):
            raise RelayError('请求格式无效。')
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise RelayError('请求格式无效。')
        return data
    def do_GET(self):
        self.dispatch(False)
    def do_POST(self):
        self.dispatch(True)
    def dispatch(self, post):
        try:
            self.guard()
            parsed = urlsplit(self.path)
            path = parsed.path
            ip = self.client_address[0]  # deliberately do not trust spoofable X-Forwarded-For
            if not post:
                if path == '/api/config':
                    return self.respond(200, {'mode': 'cloud', 'version': VERSION,
                                             'desktop_download': (ROOT/'portal/downloads/TrackLab-desktop.zip').exists(),
                                             'android_download': (ROOT/'portal/downloads/TrackLab-Android-test.apk').exists(),
                                             'preview': urlsplit(ORIGIN).hostname in {'127.0.0.1', 'localhost'}})
                if path == '/healthz':
                    return self.respond(200, {'ok': True, 'version': VERSION})
                if path == '/api/route-preset':
                    return self.respond(200, {'name':'陵水滨海体育场 · 原近似路线',
                                             'points':[[p['latitude'],p['longitude']] for p in routes.TRACK[:-1]]})
                if path == '/api/status':
                    relay.limit((ip, 'status'), 600)
                    return self.respond(200, relay.status(*self.credentials()))
                if path == '/api/search':
                    relay.limit((ip, 'search'), 20)
                    params = parse_qs(parsed.query)
                    return self.respond(200, map_service.search(params.get('q', [''])[0], params.get('provider', ['photon'])[0]))
                if path == '/api/tracks':
                    relay.limit((ip, 'tracks'), 10)
                    params = parse_qs(parsed.query)
                    return self.respond(200, map_service.tracks(params.get('lat', [''])[0], params.get('lon', [''])[0]))
                if path.startswith('/tiles/'):
                    relay.limit((ip, 'tiles'), 240)
                    return self.respond(200, map_service.tile(path), 'image/png', 'public, max-age=604800')
                found = asset(path)
                if found:
                    return self.respond(200, *found)
                if path == '/favicon.ico':
                    return self.respond(204, b'')
                raise RelayError('页面不存在。', 404)
            data = self.body()
            if path == '/api/route-preview':
                relay.limit((ip, 'preview'), 30)
                try:
                    payload = routes.route_payload(data)
                except ValueError as error:
                    raise RelayError(str(error)) from None
                return self.respond(200, routes.preview(routes.build_plan(payload, payload['demo'])))
            if path == '/api/register':
                relay.limit((ip, 'register'), 6, 300)
                return self.respond(200, relay.create(data.get('platform'), data.get('capabilities')))
            if path == '/api/pair-link':
                relay.limit((ip, 'pair-link'), 10, 300)
                return self.respond(200, relay.pair_link(data.get('session'), data.get('token')))
            if path == '/api/pair':
                relay.limit((ip, 'pair'), 10, 300)
                return self.respond(200, relay.pair(str(data.get('code', ''))))
            if path == '/api/poll':
                relay.limit((ip, 'poll'), 600)
                return self.respond(200, relay.poll(*self.credentials(), data))
            if path == '/api/refresh':
                credentials = self.credentials()
                relay.status(*credentials)  # Authenticate before assigning a per-session limit.
                relay.limit((credentials[0], 'refresh'), 10)
                return self.respond(200, relay.refresh(*credentials, data.get('id')))
            if path == '/api/command':
                relay.limit((ip, 'command'), 30)
                return self.respond(200, relay.command(*self.credentials(), data))
            if path == '/api/unpair':
                relay.unpair(*self.credentials())
                return self.respond(200, {'ok': True})
            raise RelayError('操作不存在。', 404)
        except RelayError as error:
            self.respond(error.status, {'error': str(error)})
        except (ValueError, UnicodeDecodeError):
            self.respond(400, {'error': '请求参数无效。'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.respond(500, {'error': '服务暂时无法处理，请稍后重试。'})


if __name__ == '__main__':
    if urlsplit(ORIGIN).scheme != 'https' and urlsplit(ORIGIN).hostname not in {'127.0.0.1', 'localhost'}:
        raise SystemExit('公网运行必须设置 HTTPS 的 TRACKLAB_PUBLIC_ORIGIN。')
    host = os.environ.get('TRACKLAB_CLOUD_BIND', '0.0.0.0' if os.environ.get('RENDER') == 'true' else '127.0.0.1')
    server = BoundedServer((host, PORT), Handler)
    print(f'TrackLab {VERSION} portal listening on {host}:{PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
