"""Outbound-only desktop companion. The cloud never connects into localhost."""
import argparse
import json
import platform
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
LOCAL = 'http://127.0.0.1:8769'


class APIError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Pairing credentials and position commands must stay on the selected
        # origin, including when a reverse proxy is accidentally misconfigured.
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


def request(base, path, data=None, headers=None, timeout=8):
    payload = None if data is None else json.dumps(data).encode()
    headers = dict(headers or {})
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base+path, payload, headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get('error', '请求未完成。')
        except Exception:
            message = '请求未完成。'
        raise APIError(message) from None
    except (OSError, ValueError):
        raise APIError('连接服务暂时不可用，请检查网络和连接程序。') from None


def validate_url(value):
    value = value.strip().rstrip('/')
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise APIError('请输入网站首页地址，不要附带账号、参数或路径。')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        raise APIError('公网网站必须使用 https:// 地址。')
    if not parsed.hostname:
        raise APIError('网站地址无效。')
    return value


class Companion:
    def __init__(self, cloud, local=LOCAL):
        self.cloud, self.local = validate_url(cloud), local
        self.headers = {}
        self.connection_url = cloud
        self.lock = threading.RLock()
        self.ack = None
        self.task = None
        self.run_id = None
        self.ready = False
        self.message = '请在网站点击“检查设备”。'
        self.last_cloud = time.monotonic()
        self.shutdown = threading.Event()
        self.device_headers = {}

    def local_request(self, path, data=None, timeout=8):
        return request(self.local, path, data, self.device_headers, timeout)

    def connect(self):
        boot = self.local_request('/api/bootstrap')
        if boot.get('version') != '0.4.0':
            raise APIError('本机连接服务版本较旧。请结束模拟并关闭旧程序，再重新启动。')
        self.device_headers = {'Origin': self.local, 'X-TrackLab-Token': boot['token']}
        if boot.get('needs_clear') or boot.get('replay', {}).get('active'):
            raise APIError('上次模拟尚未结束。请先在本机页面恢复定位，再连接网站。')
        name = 'iphone-windows' if platform.system() == 'Windows' else 'iphone-mac'
        print('正在连接网站，请稍候（首次启动最多等待 60 秒）…', flush=True)
        pairing = request(self.cloud, '/api/register', {'platform': name, 'capabilities': ['route']}, timeout=60)
        self.headers = {'Authorization': 'Bearer '+pairing['session']+'.'+pairing['token']}
        if pairing.get('link_token'):
            self.connection_url = self.cloud+'/#connect='+pairing['session']+'.'+pairing['link_token']
            print('网页将自动连接，无需在手机上查找或输入配对码。', flush=True)
        else:
            print('网站仍是旧版，请先更新网站。临时配对码：'+pairing['code'], flush=True)
        print('请保留本窗口和手机连接。退出请按 Ctrl+C。', flush=True)
        return pairing['code']

    def execute(self, command):
        try:
            action = command['action']
            if action == 'scan':
                result = self.local_request('/api/scan', {}, 25)
                self.ready = bool(result.get('ready'))
                self.message = result.get('message', '')
            elif action in {'fixed', 'route'}:
                result = self.local_request('/api/'+('start' if action == 'route' else 'fixed'), command['payload'] | {'remote_lease': True}, 25)
                self.run_id = result['replay']['run_id']
                self.message = '动态路线已启动，请在手机地图核实。' if action == 'route' else '固定定位连接已启动，实际位置请在手机地图核实。'
            elif action in {'pause', 'resume'}:
                self.local_request('/api/'+action, {})
                self.message = '已发送暂停指令。' if action == 'pause' else '已发送继续指令。'
            elif action == 'clear':
                self.ready = False
                self.local_request('/api/clear', {}, 70)
                self.run_id = None
                self.message = '恢复指令已完成，请在手机地图确认真实位置。'
                try:
                    scan = self.local_request('/api/scan', {}, 25)
                    self.ready = bool(scan.get('ready'))
                    self.message += ' 设备已就绪，可以再次开始。' if self.ready else ' '+scan.get('message', '请重新检查设备。')
                except Exception:
                    self.message += ' 设备检查失败，请点击刷新连接状态后重试。'
            else:
                raise APIError('不支持的设备指令。')
            ack = {'id': command['id'], 'ok': True, 'message': self.message}
        except Exception as error:
            self.message = str(error) if isinstance(error, APIError) else '设备操作未完成，请检查连接。'
            ack = {'id': command['id'], 'ok': False, 'message': self.message}
        with self.lock:
            self.ack = ack

    def safe_stop_owned_run(self):
        # Never stop an unrelated locally-started route/session after reconnection.
        if not self.run_id:
            return
        try:
            s = self.local_request('/api/status')['replay']
            if s.get('active') and s.get('run_id') == self.run_id:
                self.local_request('/api/stop', {})
        except APIError:
            pass  # worker's 15-second lease remains the independent fallback

    def tick(self):
        status = self.local_request('/api/status')
        replay = status['replay']
        state = {k: replay.get(k) for k in ('state', 'active', 'elapsed', 'duration', 'cleared', 'mode', 'distance', 'pace')}
        state.update(ready=self.ready, needs_clear=status['needs_clear'], message=self.message)
        with self.lock:
            ack = self.ack
        response = request(self.cloud, '/api/poll', {'status': state, 'ack': ack}, self.headers)
        self.last_cloud = time.monotonic()
        with self.lock:
            if self.ack is ack:
                self.ack = None
        if response.get('paired') and self.run_id and replay.get('active') and replay.get('run_id') == self.run_id:
            self.local_request('/api/keepalive', {'run_id': self.run_id})
        command = response.get('command')
        if command:
            if self.task and self.task.is_alive():
                with self.lock:
                    self.ack = {'id': command['id'], 'ok': False, 'message': '设备正在处理上一项操作，请稍后。'}
            else:
                self.task = threading.Thread(target=self.execute, args=(command,), daemon=True)
                self.task.start()

    def run(self):
        try:
            while not self.shutdown.is_set():
                try:
                    self.tick()
                except APIError:
                    if time.monotonic()-self.last_cloud > 10:
                        self.safe_stop_owned_run()
                    if time.monotonic()-self.last_cloud > 60:
                        raise APIError('网站连接已中断。模拟会话停止续期，请检查手机位置后重新启动连接程序。')
                self.shutdown.wait(1)
        finally:
            self.safe_stop_owned_run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url')
    args = parser.parse_args()
    cloud = validate_url(args.url or input('请输入部署后的网站地址：https://…\n').strip())
    child = None
    try:
        try:
            request(LOCAL, '/api/bootstrap')
        except APIError:
            (ROOT/'runtime').mkdir(exist_ok=True)
            with (ROOT/'runtime'/'connector-local.log').open('a') as log:
                child = subprocess.Popen([sys.executable, str(ROOT/'server.py')], cwd=ROOT,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            for _ in range(30):
                time.sleep(.2)
                try:
                    request(LOCAL, '/api/bootstrap')
                    break
                except APIError:
                    if child.poll() is not None:
                        raise APIError('连接程序未能启动，请检查本机端口是否被占用。')
        companion = Companion(cloud)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: companion.shutdown.set())
        companion.connect()
        if not webbrowser.open(companion.connection_url):
            print('浏览器未能自动打开。请复制以下一次性链接到浏览器，5 分钟内有效，勿分享：', flush=True)
            print(companion.connection_url, flush=True)
        companion.run()
    finally:
        if child:
            child.terminate()
            try:
                child.wait(timeout=80)
            except subprocess.TimeoutExpired:
                # Retain recovery marker; never announce actual GPS recovery.
                print('本机服务仍在处理恢复，请检查手机和本机恢复页面。')


if __name__ == '__main__':
    try:
        main()
    except (APIError, KeyboardInterrupt) as error:
        print(str(error) or '连接程序已退出，请检查手机真实位置。', file=sys.stderr)
        sys.exit(1)
