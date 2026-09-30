"""Loopback-only workbench. Device mutations are serialized and explicit."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import secrets
import signal
import threading
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import device
import routes
from replay import runner
import map_service
from web_assets import asset
from version import VERSION

ROOT = Path(__file__).resolve().parent
INSTANCE = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
PORT = int(os.environ.get("TRACKLAB_PORT", "8769"))
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
MARKER = ROOT / "runtime" / "needs-clear.json"


def mark_pending(value: bool, target: str | None = None, run_id: str | None = None) -> None:
    if value:
        MARKER.parent.mkdir(exist_ok=True)
        temporary = MARKER.with_suffix(".tmp")
        temporary.write_text(json.dumps({"needs_clear": True, "target": target, "run_id": run_id}) + "\n")
        temporary.replace(MARKER)
    else:
        MARKER.unlink(missing_ok=True)


def reconcile():
    """An old worker must never remove a newer run's recovery marker."""
    state = runner.status()
    if state.get('cleared') and not state.get('active') and MARKER.exists():
        try:
            record = json.loads(MARKER.read_text())
            if state.get('run_id') and record.get('run_id') == state['run_id']:
                mark_pending(False)
        except (ValueError, AttributeError):
            pass
    return state


def clear_all():
    target = expected_target()
    try:
        pending_run = json.loads(MARKER.read_text()).get('run_id') if MARKER.exists() else None
    except (ValueError, AttributeError):
        pending_run = None
    if runner.status().get('active'):
        runner.stop_and_wait()
    state = reconcile()
    # The worker already cleared this exact run through its open connection.
    # A failed or unacknowledged stop still needs the independent fallback.
    if pending_run and state.get('run_id') == pending_run and state.get('cleared') and not state.get('active'):
        return {'message': '清除指令已发送。请打开手机地图确认恢复到实际位置。'}
    result = device.clear_location(target)
    mark_pending(False)
    return result


def expected_target() -> str | None:
    if not MARKER.exists():
        return None
    try:
        target = json.loads(MARKER.read_text()).get("target")
        if not isinstance(target, str) or len(target) != 64:
            raise ValueError()
        return target
    except (ValueError, AttributeError):
        raise device.DeviceError("恢复记录无法读取。请先在原手机上重启并确认实际定位，再联系我处理记录。") from None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, code: int, body, content_type="application/json; charset=utf-8", cache="no-store", etag=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        if code != 304:
            self.wfile.write(body)

    def host_allowed(self) -> bool:
        return self.headers.get("Host") in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def do_GET(self):
        if not self.host_allowed():
            return self.respond(403, {"error": "仅允许本机访问。"})
        if self.path == '/api/config':
            return self.respond(200, {'mode': 'local', 'version': VERSION, 'instance': INSTANCE})
        path = urlsplit(self.path)
        try:
            if path.path == '/api/route-preset':
                return self.respond(200, {'name':'陵水滨海体育场 · 原近似路线',
                                         'points':[[p['latitude'],p['longitude']] for p in routes.TRACK[:-1]]})
            if path.path == '/api/search':
                params = parse_qs(path.query)
                return self.respond(200, map_service.search(params.get('q', [''])[0], params.get('provider', ['photon'])[0]))
            if path.path == '/api/tracks':
                params = parse_qs(path.query)
                return self.respond(200, map_service.tracks(params.get('lat', [''])[0], params.get('lon', [''])[0]))
            if path.path == '/' or path.path == '/fixed' or path.path == '/connect' or path.path.startswith('/portal/'):
                found = asset(path.path)
                if found:
                    body, kind = found
                    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
                    if self.headers.get('If-None-Match') == etag:
                        return self.respond(304, body, kind, cache='no-cache', etag=etag)
                    return self.respond(200, body, kind, cache='no-cache', etag=etag)
        except map_service.MapError as error:
            return self.respond(error.status, {'error': str(error)})
        if self.path in {"/api/bootstrap", "/api/status"}:
            # A device check can take 15 seconds. Status is read-only and can
            # safely show the latest acknowledged state while that check runs.
            acquired = LOCK.acquire(blocking=False)
            try:
                state = reconcile() if acquired else runner.status()
                result = {"needs_clear": MARKER.exists(), "replay": state,
                          "operation_pending": not acquired, "version": VERSION, "instance": INSTANCE,
                          "shutdown_supported": True}
                if self.path == "/api/bootstrap":
                    result['token'] = TOKEN
            finally:
                if acquired:
                    LOCK.release()
            return self.respond(200, result)
        if self.path == "/favicon.ico":
            return self.respond(204, b"", "image/x-icon")
        return self.respond(404, {"error": "页面不存在。"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed_origins = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
        if (not self.host_allowed() or origin not in allowed_origins
                or not secrets.compare_digest(self.headers.get("X-TrackLab-Token", ""), TOKEN)):
            return self.respond(403, {"error": "请求来源无效，请从本地页面操作。"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 32768:
                raise ValueError("请求过大。")
            data = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("请求格式错误。")
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, {"error": "请求格式错误。"})
        if self.path == '/api/preview':
            # Route calculation does not read or mutate the connected phone.
            try:
                return self.respond(200, routes.preview(routes.build_plan(data)))
            except ValueError as error:
                return self.respond(400, {'error': str(error)})
        if self.path not in {"/api/shutdown", "/api/scan", "/api/clear", "/api/start", "/api/pause", "/api/resume", "/api/stop", "/api/fixed"}:
            return self.respond(404, {"error": "操作不存在。"})
        if not LOCK.acquire(blocking=False):
            return self.respond(409, {"error": "正在处理设备操作，请稍候。"})
        try:
            state = reconcile()
            if state.get('active') and self.path in {'/api/scan', '/api/start', '/api/fixed'}:
                raise device.DeviceError('已有路线正在回放，请先停止并恢复定位。')
            if self.path == '/api/shutdown':
                if state.get('active') or MARKER.exists():
                    return self.respond(409, {'error': '定位尚在运行或待恢复，请先恢复真实定位。'})
                self.respond(200, {'stopped': True, 'version': VERSION, 'instance': INSTANCE})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path == "/api/scan":
                result = device.scan()
            elif self.path in {'/api/start', '/api/fixed'}:
                demo = data.get('demo', False)
                if not isinstance(demo, bool):
                    raise ValueError('试播选项无效。')
                plan = routes.fixed_plan(data) if self.path == '/api/fixed' else routes.build_plan(data, demo=demo)
                if MARKER.exists():
                    raise device.DeviceError('上次模拟尚未确认清除，请先使用恢复按钮。')
                ready = device.require_ready()
                run_id = secrets.token_hex(12)
                mark_pending(True, device.fingerprint(ready['_serial']), run_id)
                result = {'replay': runner.start(ready, plan, run_id)}
            elif self.path in {'/api/pause', '/api/resume', '/api/stop'}:
                result = {'replay': runner.control(self.path.rsplit('/', 1)[1])}
            else:
                result = clear_all()
            result["needs_clear"] = MARKER.exists()
            return self.respond(200, result)
        except ValueError as error:
            return self.respond(400, {"error": str(error), "needs_clear": MARKER.exists()})
        except device.DeviceError as error:
            return self.respond(422, {"error": str(error), "needs_clear": MARKER.exists()})
        except Exception:
            return self.respond(500, {"error": "设备操作发生异常。未确认清除前，请保留连接并尝试恢复定位。", "needs_clear": MARKER.exists()})
        finally:
            LOCK.release()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"TrackLab 已启动：http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if MARKER.exists():
            try:
                with LOCK:
                    clear_all()
                print("已发送清除模拟位置指令，请在手机地图确认。", flush=True)
            except Exception:
                print("未能确认清除模拟位置。请重新连接手机，在页面点击恢复定位；必要时重启手机。", flush=True)


if __name__ == "__main__":
    main()
