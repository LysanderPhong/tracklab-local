"""Open the loopback workbench; no cloud session or pairing step."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:8769'

class LauncherError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise LauncherError('本机端口返回了跳转，无法确认服务身份。')

def probe(base=BASE):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        with opener.open(base+'/api/bootstrap', timeout=2) as response:
            data=json.loads(response.read(65537))
    except urllib.error.HTTPError:
        raise LauncherError('8769 端口已被其他程序使用，请关闭冲突程序后重试。') from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return None
        raise LauncherError('本机服务暂时无响应，请稍后重试；不要在定位运行时强制结束服务。') from None
    except (ValueError, TimeoutError, OSError):
        raise LauncherError('本机服务无法识别或响应超时，请检查已有程序。') from None
    if not isinstance(data,dict) or data.get('version')!='0.4.0' or not isinstance(data.get('token'),str) or not isinstance(data.get('replay'),dict):
        raise LauncherError('本机服务版本不兼容，请先恢复定位并退出旧程序。')
    return data

def ensure_service(root=ROOT, base=BASE):
    if probe(base) is not None:
        return False
    runtime=root/'runtime';runtime.mkdir(exist_ok=True)
    options={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NO_WINDOW}
    env=dict(os.environ,TRACKLAB_PORT='8769')
    with (runtime/'desktop-launcher.log').open('a') as log:
        child=subprocess.Popen([sys.executable,str(root/'server.py')],cwd=root,env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
    try:
        for _ in range(60):
            # Another simultaneous click may have won the port; reuse it safely.
            if probe(base) is not None:
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
        print('已打开本机直连页面。页面打开时自动检测手机；稍后插入可点“刷新连接状态”，无需配对码。')
    print('启动窗口可以关闭，本机服务会继续运行。结束定位请使用网页中的恢复按钮。')

if __name__=='__main__':
    try:main()
    except LauncherError as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
