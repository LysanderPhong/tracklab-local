"""Open the loopback workbench; no cloud session or pairing step."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from version import VERSION

ROOT = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:8769'

class LauncherError(Exception):
    pass

class ServiceUnavailable(LauncherError):
    pass

class IncompatibleService(LauncherError):
    def __init__(self, message, data):
        super().__init__(message)
        self.data=data

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise LauncherError('本机端口返回了跳转，无法确认服务身份。')

def instance_id(root=ROOT):
    return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]

def service_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

def inspect_service(base=BASE):
    try:
        with service_opener().open(base+'/api/bootstrap', timeout=2) as response:
            data=json.loads(response.read(65537))
    except urllib.error.HTTPError:
        raise LauncherError('8769 端口已被其他程序使用，请关闭冲突程序后重试。') from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return None
        raise ServiceUnavailable('本机服务暂时无响应，请稍后重试；不要在定位运行时强制结束服务。') from None
    except (TimeoutError,OSError):
        raise ServiceUnavailable('本机服务响应超时或连接中断，请稍后重试。') from None
    except ValueError:
        raise LauncherError('本机服务无法识别或响应超时，请检查已有程序。') from None
    if not isinstance(data,dict) or not isinstance(data.get('version'),str) or not isinstance(data.get('token'),str) or not isinstance(data.get('replay'),dict):
        raise LauncherError('8769 端口上的程序无法确认是 TrackLab，请关闭冲突程序后重试。')
    return data

def probe(base=BASE, expected_root=ROOT):
    data=inspect_service(base)
    if data is None:
        return None
    current_version=data['version']
    if current_version!=VERSION:
        raise IncompatibleService(f'已有 TrackLab 版本 {current_version} 正在运行，本次打开的是 {VERSION}。请先在旧网页恢复真实定位，再退出旧程序后重新打开本包；启动器不会强制结束旧服务。',data)
    if data.get('instance')!=instance_id(expected_root):
        raise IncompatibleService(f'已有 TrackLab {current_version} 来自另一份安装，本次打开的是 {VERSION}。请先在旧网页恢复真实定位并退出旧程序，再打开本包；启动器不会替换已有服务。',data)
    return data

def can_shutdown(data):
    return (data.get('shutdown_supported') is True
            and isinstance(data.get('instance'),str)
            and re.fullmatch(r'[0-9a-f]{16}',data['instance']) is not None
            and bool(data.get('token'))
            and data['replay'].get('active') is False
            and data.get('needs_clear') is False)

def shutdown_idle_service(base,data):
    request=urllib.request.Request(base+'/api/shutdown',data=b'{}',method='POST',headers={
        'Content-Type':'application/json','Origin':base.rstrip('/'),
        'X-TrackLab-Token':data['token']})
    try:
        with service_opener().open(request,timeout=2) as response:
            if response.status!=200:
                raise LauncherError('旧程序没有确认安全退出，请保留旧网页并稍后重试。')
    except (urllib.error.URLError,TimeoutError,OSError):
        raise LauncherError('旧程序没有确认安全退出。可能有设备操作正在进行，请先在旧网页恢复定位后重试。') from None
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:
            current=inspect_service(base)
        except ServiceUnavailable:
            # A confirmed shutdown can reset one in-flight connection. Wait
            # for connection refusal; a reset alone does not prove exit.
            time.sleep(.25)
            continue
        if current is None:
            return
        if any(current.get(key)!=data.get(key) for key in ('version','instance','token')):
            raise LauncherError('旧程序退出期间端口被另一服务占用，请重新打开本包。')
        time.sleep(.25)
    raise LauncherError('旧程序尚未退出，请稍后重试；启动器不会强制结束服务。')

def ensure_service(root=ROOT, base=BASE):
    root=Path(root).resolve()
    try:
        if probe(base, expected_root=root) is not None:
            return False
    except IncompatibleService as error:
        if not can_shutdown(error.data):
            raise
        shutdown_idle_service(base,error.data)
    runtime=root/'runtime';runtime.mkdir(exist_ok=True)
    options={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NO_WINDOW}
    env=dict(os.environ,TRACKLAB_PORT='8769')
    with (runtime/'desktop-launcher.log').open('a') as log:
        child=subprocess.Popen([sys.executable,str(root/'server.py')],cwd=root,env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
    try:
        for _ in range(60):
            # Another simultaneous click may have won the port; reuse it safely.
            if probe(base, expected_root=root) is not None:
                return True
            if child.poll() is not None:
                raise LauncherError('本机程序未能启动，请查看 runtime/desktop-launcher.log。')
            time.sleep(.25)
        raise LauncherError('本机程序启动超时，请稍后重试。')
    except Exception:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:pass
        raise

def main():
    ensure_service()
    url=BASE+'/fixed'
    if not webbrowser.open(url):
        print('请在浏览器打开：'+url)
    else:
        print('已打开本机直连页面。页面打开时自动检测手机；稍后插入可点“一键连接”，无需配对码。')
    print('启动窗口可以关闭，本机服务会继续运行。结束定位请使用网页中的恢复按钮。')

if __name__=='__main__':
    try:main()
    except LauncherError as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
