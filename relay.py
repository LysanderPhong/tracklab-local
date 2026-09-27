"""Ephemeral pairing/command mailbox. Never opens a device port to the internet."""
from __future__ import annotations
from collections import OrderedDict, deque
import math
import secrets
import threading
import time
import routes


class RelayError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Relay:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.limits = OrderedDict()

    def limit(self, key, count, seconds=60):
        with self.lock:
            now = self.clock()
            history = self.limits.setdefault(key, deque())
            while history and history[0] <= now-seconds:
                history.popleft()
            if len(history) >= count:
                raise RelayError('操作太频繁，请稍后再试。', 429)
            history.append(now)
            self.limits.move_to_end(key)
            while len(self.limits) > 2048:
                self.limits.popitem(last=False)

    def prune(self):
        now = self.clock()
        for sid in list(self.sessions):
            s = self.sessions[sid]
            if now > s['expires'] or (not s['web_token'] and now > s['code_expires']):
                del self.sessions[sid]

    def create(self, platform, capabilities=None):
        with self.lock:
            self.prune()
            if platform not in {'iphone-mac', 'iphone-windows', 'android'}:
                raise RelayError('暂不支持这种连接程序。')
            if len(self.sessions) >= 32:
                raise RelayError('连接名额已满，请稍后再试。', 503)
            sid, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
            codes = {s['code'] for s in self.sessions.values()}
            code = str(secrets.randbelow(90_000_000)+10_000_000)
            while code in codes:
                code = str(secrets.randbelow(90_000_000)+10_000_000)
            now = self.clock()
            self.sessions[sid] = dict(device_token=token, web_token=None, platform=platform,
                capabilities=['route'] if isinstance(capabilities, list) and 'route' in capabilities else [],
                code=code, link_token=secrets.token_urlsafe(32), code_expires=now+300, expires=now+7200,
                seen=now, status={'state': 'idle', 'ready': False, 'needs_clear': False},
                pending=None, last_command=None, seen_ids=set())
            return dict(session=sid, token=token, code=code, code_seconds=300, link_token=self.sessions[sid]['link_token'])

    def pair(self, code):
        with self.lock:
            self.prune()
            for sid, s in self.sessions.items():
                if s['code'] == code and self.clock() <= s['code_expires'] and not s['web_token']:
                    s['web_token'] = secrets.token_urlsafe(32)
                    s['code'] = None
                    s['link_token'] = None
                    return dict(session=sid, token=s['web_token'], platform=s['platform'])
            raise RelayError('配对码不正确或已过期，请查看连接程序。', 404)

    def pair_link(self, sid, token):
        with self.lock:
            self.prune()
            s = self.sessions.get(sid) if isinstance(sid, str) else None
            expected = s.get('link_token') if s else None
            if (not expected or not isinstance(token, str) or not token.isascii()
                    or not secrets.compare_digest(expected, token) or s['web_token']):
                raise RelayError('连接链接已失效或已使用，请重新启动连接程序。', 404)
            s['web_token'] = secrets.token_urlsafe(32)
            s['code'] = s['link_token'] = None
            return dict(session=sid, token=s['web_token'], platform=s['platform'])

    def auth(self, sid, token, role):
        self.prune()
        s = self.sessions.get(sid)
        expected = s.get(role+'_token') if s else None
        if not expected or not isinstance(token, str) or not token.isascii() or not secrets.compare_digest(expected, token):
            raise RelayError('连接已失效，请重新配对。', 401)
        return s

    def status(self, sid, token):
        with self.lock:
            s = self.auth(sid, token, 'web')
            self.expire_command(s)
            return dict(platform=s['platform'], online=self.clock()-s['seen'] < 10,
                        device=s['status'], command=s['last_command'], capabilities=s['capabilities'])

    def refresh(self, sid, token, command_id):
        # Refresh is a device scan only when idle; never interrupts a running task.
        with self.lock:
            current = self.status(sid, token)
            last = current['command']
            queued = False
            if not current['online']:
                message = '连接程序已离线，请保持程序运行；若已退出，请重新打开并连接。'
            elif last and last['state'] in {'queued', 'delivered'}:
                message = '已刷新连接状态，正在等待上一项设备操作完成。'
            elif current['device'].get('active'):
                message = '已刷新连接状态；定位正在运行，本次不重新扫描设备。'
            elif current['device'].get('needs_clear'):
                message = '已刷新连接状态，请先恢复上次定位，再检查设备。'
            else:
                self.command(sid, token, {'id': command_id, 'action': 'scan'})
                queued = True
                message = '已请求重新检查手机，正在等待连接程序返回结果。'
            return self.status(sid, token) | {'scan_requested': queued, 'message': message}

    def expire_command(self, s):
        p = s['pending']
        if p and self.clock() > p['deadline']:
            s['last_command'] = {'id': p['id'], 'state': 'expired', 'message': '指令已过期，未发送到设备。'}
            s['pending'] = None
        last = s['last_command']
        if last and last['state'] == 'delivered' and self.clock()-last['sent_at'] > 60:
            s['last_command'] = dict(last, state='unknown', message='未收到执行结果，请检查连接程序和手机。')

    def command(self, sid, token, data):
        with self.lock:
            s = self.auth(sid, token, 'web')
            self.expire_command(s)
            if self.clock()-s['seen'] >= 10:
                raise RelayError('连接程序已离线，请先恢复连接。', 409)
            action, cid = data.get('action'), data.get('id')
            if not isinstance(cid, str) or not 8 <= len(cid) <= 80:
                raise RelayError('指令编号无效。')
            if cid in s['seen_ids']:
                return {'accepted': True, 'duplicate': True}
            if len(s['seen_ids']) >= 4096:
                raise RelayError('本次会话操作次数已用尽，请恢复定位后重新配对。', 409)
            if action not in {'scan', 'fixed', 'route', 'pause', 'resume', 'clear'}:
                raise RelayError('不支持这种操作。')
            if s['pending'] or (s['last_command'] and s['last_command']['state'] == 'delivered'):
                raise RelayError('上一条指令尚未完成，请稍候。', 409)
            payload = {}
            if action in {'fixed', 'route'}:
                if action == 'route':
                    if 'route' not in s['capabilities']:
                        raise RelayError('请更新并重新启动 0.4 版连接程序，旧版只支持固定定位。', 409)
                    payload = routes.route_payload(data)
                else:
                    plan = routes.fixed_plan(data)
                    payload = {k: plan[k] for k in ('latitude', 'longitude')}
                    payload['seconds'] = plan['duration']
                if not s['status'].get('ready') or s['status'].get('needs_clear') or s['status'].get('active'):
                    raise RelayError('请先检查设备，并恢复上次的定位。', 409)
            if action in {'pause', 'resume'} and (not s['status'].get('active') or s['status'].get('mode') != 'route'):
                raise RelayError('当前没有动态路线在运行。', 409)
            s['pending'] = dict(id=cid, action=action, payload=payload, deadline=self.clock()+10)
            s['last_command'] = dict(id=cid, state='queued', message='等待连接程序接收。')
            s['seen_ids'].add(cid)
            return {'accepted': True}

    def poll(self, sid, token, data):
        with self.lock:
            s = self.auth(sid, token, 'device')
            s['seen'] = self.clock()
            incoming = data.get('status', {})
            if isinstance(incoming, dict):
                # Explicit public schema. No serial numbers, errors from SDKs or paths.
                status = {k: bool(incoming.get(k, False)) for k in ('ready', 'active', 'needs_clear', 'cleared')}
                status['state'] = str(incoming.get('state', 'idle'))[:24]
                status['message'] = str(incoming.get('message', ''))[:240]
                status['mode'] = 'route' if incoming.get('mode') == 'route' else 'fixed'
                for k in ('elapsed', 'duration', 'distance', 'pace'):
                    v = incoming.get(k, 0)
                    status[k] = v if type(v) in (float, int) and math.isfinite(v) else 0
                s['status'] = status
            ack = data.get('ack')
            if isinstance(ack, dict) and s['last_command'] and s['last_command']['id'] == ack.get('id'):
                s['last_command'] = {'id': ack['id'], 'state': 'done' if ack.get('ok') is True else 'failed',
                                     'message': str(ack.get('message', ''))[:240]}
            self.expire_command(s)
            command = s['pending']
            if command:
                s['pending'] = None  # at most once; reconnect never replays an old start
                s['last_command'] = dict(id=command['id'], state='delivered', sent_at=self.clock(), message='连接程序正在执行。')
                command = {k: command[k] for k in ('id', 'action', 'payload')}
            return dict(paired=bool(s['web_token']), command=command)

    def unpair(self, sid, token):
        with self.lock:
            self.auth(sid, token, 'web')
            del self.sessions[sid]
