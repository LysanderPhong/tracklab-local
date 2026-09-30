import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from unittest.mock import patch
import build_release
import server

class LocalPackageTests(unittest.TestCase):
    def test_archive_is_source_complete_and_excludes_runtime_and_binaries(self):
        archive=build_release.build()
        with zipfile.ZipFile(archive) as z:
            names=z.namelist()
            self.assertTrue(all(n.startswith('TrackLab-Local/') for n in names))
            for n in names:
                self.assertFalse(any(p in {'.git','.venv','runtime','__pycache__','downloads','static'} for p in Path(n).parts),n)
                self.assertNotIn(Path(n).suffix,{'.apk','.pyc','.png'},n)
            release=json.loads(z.read('TrackLab-Local/RELEASE.json'))
            for name,digest in release['sha256'].items():
                self.assertEqual(hashlib.sha256(z.read('TrackLab-Local/'+name)).hexdigest(),digest)
            for name in ['LICENSE','server.py','desktop_launcher.py','requirements-desktop.txt','一键连接-Mac.command']:
                self.assertIn(name,release['sha256'])

    def test_local_root_and_guide_work_without_legacy_assets_or_cloud(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        with patch.object(server,'PORT',http.server_port):
            thread.start()
            try:
                for path,needle in [('/',b'TrackLab'),('/fixed',b'TrackLab'),('/connect','无需云端配对'.encode())]:
                    with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}'+path) as res:
                        self.assertEqual(res.status,200)
                        self.assertIn(needle,res.read())
            finally:http.shutdown();http.server_close();thread.join()

    def test_map_tiles_use_browser_cache_and_are_allowed_by_page_policy(self):
        script=(Path(__file__).resolve().parents[1]/'portal/app.js').read_text()
        self.assertIn("L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png'",script)
        self.assertNotIn("L.tileLayer('/tiles/",script)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        with patch.object(server,'PORT',http.server_port):
            thread.start()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}/fixed') as res:
                    self.assertIn('img-src \'self\' data: https://tile.openstreetmap.org',
                                  res.headers['Content-Security-Policy'])
                    self.assertEqual(res.headers['Referrer-Policy'],'strict-origin-when-cross-origin')
            finally:http.shutdown();http.server_close();thread.join()

    def test_status_stays_responsive_during_a_device_operation(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        with tempfile.TemporaryDirectory() as temp, patch.object(server,'PORT',http.server_port), \
             patch.object(server,'MARKER',Path(temp)/'needs-clear.json'):
            thread.start()
            server.LOCK.acquire()
            try:
                started=time.monotonic()
                with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}/api/status',timeout=1) as res:
                    state=json.load(res)
                self.assertLess(time.monotonic()-started,1)
                self.assertTrue(state['operation_pending'])
                self.assertFalse(state['needs_clear'])
            finally:
                server.LOCK.release()
                http.shutdown();http.server_close();thread.join()
