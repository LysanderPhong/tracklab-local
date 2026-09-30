import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock,patch
import desktop_launcher as launcher

class BootstrapFixture:
    """Loopback-only protocol fixture; it never scans or changes a phone."""
    def __init__(self,data,port=0,shutdown_status=200):
        self.data=data;self.posts=[];fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                body=json.dumps(fixture.data).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length','0')))
                fixture.posts.append((self.path,dict(self.headers)))
                self.send_response(shutdown_status);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
                if shutdown_status==200:threading.Thread(target=fixture.close,daemon=True).start()
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        self.thread.start()
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

class LauncherTests(unittest.TestCase):
    def bootstrap_response(self,data):
        response=Mock();response.__enter__=Mock(return_value=io.BytesIO(json.dumps(data).encode()));response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        return patch.object(launcher.urllib.request,'build_opener',return_value=opener)
    def valid_bootstrap(self,root=launcher.ROOT):
        return {'version':launcher.VERSION,'instance':launcher.instance_id(root),'token':'test-token','replay':{'active':True}}
    def test_existing_service_is_reused_including_active_session(self):
        with self.bootstrap_response(self.valid_bootstrap()),patch.object(launcher.subprocess,'Popen') as spawn:
            self.assertFalse(launcher.ensure_service())
            spawn.assert_not_called()
    def test_unknown_service_is_not_replaced(self):
        with patch.object(launcher,'probe',side_effect=launcher.LauncherError('conflict')),patch.object(launcher.subprocess,'Popen') as spawn:
            with self.assertRaises(launcher.LauncherError):launcher.ensure_service()
            spawn.assert_not_called()
    def test_start_waits_for_service_and_binds_loopback_port(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(launcher,'probe',side_effect=[None,None,{'replay':{}}]),patch.object(launcher.time,'sleep'),patch.object(launcher.subprocess,'Popen') as spawn:
            spawn.return_value.poll.return_value=None
            self.assertTrue(launcher.ensure_service(Path(temp)))
            args=spawn.call_args
            self.assertEqual(args.kwargs['env']['TRACKLAB_PORT'],'8769')
            self.assertEqual(args.args[0][1],str(Path(temp).resolve()/'server.py'))
            spawn.return_value.terminate.assert_not_called()
            self.assertEqual(launcher.probe.call_args.kwargs['expected_root'],Path(temp).resolve())
    def test_failed_start_cleans_up_own_child(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(launcher,'probe',side_effect=[None,launcher.LauncherError('bad')]),patch.object(launcher.subprocess,'Popen') as spawn:
            spawn.return_value.poll.return_value=None
            with self.assertRaises(launcher.LauncherError):launcher.ensure_service(Path(temp))
            spawn.return_value.terminate.assert_called_once()
    def test_launcher_only_opens_local_ui(self):
        with patch.object(launcher,'ensure_service'),patch.object(launcher.webbrowser,'open',return_value=True) as browser,patch('sys.stdout',io.StringIO()):launcher.main()
        browser.assert_called_once_with('http://127.0.0.1:8769/fixed')
    def test_probe_rejects_non_tracklab_response(self):
        with self.bootstrap_response({'hello':'other service'}):
            with self.assertRaises(launcher.LauncherError):launcher.probe()
    def test_old_service_is_rejected_without_replacing_active_session(self):
        data=self.valid_bootstrap();data['version']='0.4.0';data.pop('instance')
        with self.bootstrap_response(data),patch.object(launcher.subprocess,'Popen') as spawn:
            with self.assertRaises(launcher.LauncherError) as error:launcher.ensure_service()
            self.assertIn('0.4.0',str(error.exception))
            self.assertIn(launcher.VERSION,str(error.exception))
            self.assertIn('恢复真实定位',str(error.exception))
            spawn.assert_not_called()
    def test_same_version_from_other_installation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            data=self.valid_bootstrap(Path(temp))
            with self.bootstrap_response(data),patch.object(launcher.subprocess,'Popen') as spawn:
                with self.assertRaises(launcher.LauncherError) as error:launcher.ensure_service()
                self.assertIn('另一份安装',str(error.exception))
                spawn.assert_not_called()
    def test_same_installation_is_reused_with_explicit_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.bootstrap_response(self.valid_bootstrap(root)),patch.object(launcher.subprocess,'Popen') as spawn:
                self.assertFalse(launcher.ensure_service(root))
                spawn.assert_not_called()
    def test_missing_instance_is_not_reused_even_at_same_version(self):
        data=self.valid_bootstrap();data.pop('instance')
        with self.bootstrap_response(data):
            with self.assertRaises(launcher.LauncherError):launcher.probe()
    def supported_idle_bootstrap(self,root):
        data=self.valid_bootstrap(root)
        data.update(version='0.5.2-beta',shutdown_supported=True,needs_clear=False,replay={'active':False})
        return data
    def test_supported_idle_service_safely_upgrades_before_starting(self):
        for previous_version in ('0.5.2-beta',launcher.VERSION):
            with self.subTest(previous_version=previous_version),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);data=self.supported_idle_bootstrap(root/'previous-installation');data['version']=previous_version
                old=BootstrapFixture(data);new=[]
                def start(*args,**kwargs):
                    self.assertFalse(old.thread.is_alive())
                    new.append(BootstrapFixture(self.valid_bootstrap(root),old.server.server_port))
                    return Mock(poll=Mock(return_value=None))
                try:
                    with patch.object(launcher.subprocess,'Popen',side_effect=start) as spawn:
                        self.assertTrue(launcher.ensure_service(root,old.base))
                        spawn.assert_called_once()
                    self.assertEqual(len(old.posts),1)
                    path,headers=old.posts[0]
                    self.assertEqual(path,'/api/shutdown')
                    self.assertEqual(headers['Origin'],old.base)
                    self.assertEqual(headers['X-Tracklab-Token'],'test-token')
                finally:
                    old.close()
                    for service in new:service.close()
    def test_active_or_uncleared_service_never_receives_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            for state in ({'replay':{'active':True}},{'needs_clear':True},{'needs_clear':None},{'replay':{}}):
                data=self.supported_idle_bootstrap(Path(temp));data.update(state);service=BootstrapFixture(data)
                try:
                    with patch.object(launcher.subprocess,'Popen') as spawn:
                        with self.assertRaises(launcher.LauncherError):launcher.ensure_service(Path(temp),service.base)
                        spawn.assert_not_called()
                    self.assertEqual(service.posts,[])
                finally:service.close()
    def test_legacy_service_without_shutdown_identity_is_never_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            data=self.supported_idle_bootstrap(Path(temp));data.update(version='0.4.0');data.pop('instance');data.pop('shutdown_supported')
            service=BootstrapFixture(data)
            try:
                with patch.object(launcher.subprocess,'Popen') as spawn:
                    with self.assertRaises(launcher.LauncherError):launcher.ensure_service(Path(temp),service.base)
                    spawn.assert_not_called()
                self.assertEqual(service.posts,[])
            finally:service.close()
    def test_shutdown_rejection_does_not_start_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            service=BootstrapFixture(self.supported_idle_bootstrap(Path(temp)),shutdown_status=409)
            try:
                with patch.object(launcher.subprocess,'Popen') as spawn:
                    with self.assertRaises(launcher.LauncherError):launcher.ensure_service(Path(temp),service.base)
                    spawn.assert_not_called()
                self.assertTrue(service.thread.is_alive())
                self.assertEqual(len(service.posts),1)
            finally:service.close()
