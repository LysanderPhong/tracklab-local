import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import device
import replay
import server
from version import VERSION
import web_assets


class ClearTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.marker = Path(self.temp.name) / 'needs-clear.json'
        self.patch = patch.object(server, 'MARKER', self.marker)
        self.patch.start()
        server.mark_pending(True, 'a' * 64, 'current-run')

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def run_clear(self, final, clear_result=None):
        runner = Mock()
        runner.status.side_effect = [{'active': True}, final]
        clear = Mock(return_value=clear_result or {'message': 'fallback acknowledged'})
        with patch.object(server, 'runner', runner), patch.object(device, 'clear_location', clear):
            result = server.clear_all()
        return runner, clear, result

    def test_successful_worker_clear_is_not_sent_twice(self):
        runner, clear, result = self.run_clear({'active': False, 'cleared': True, 'run_id': 'current-run'})
        runner.stop_and_wait.assert_called_once()
        clear.assert_not_called()
        self.assertFalse(self.marker.exists())
        self.assertIn('清除', result['message'])

    def test_failed_worker_clear_uses_independent_fallback(self):
        _, clear, result = self.run_clear({'active': False, 'cleared': False, 'run_id': 'current-run'})
        clear.assert_called_once_with('a' * 64)
        self.assertFalse(self.marker.exists())
        self.assertEqual(result['message'], 'fallback acknowledged')

    def test_other_run_or_unfinalized_worker_does_not_skip_fallback(self):
        for state in [
            {'active': False, 'cleared': True, 'run_id': 'old-run'},
            {'active': True, 'cleared': True, 'run_id': 'current-run'},
        ]:
            with self.subTest(state=state):
                server.mark_pending(True, 'a' * 64, 'current-run')
                _, clear, _ = self.run_clear(state)
                clear.assert_called_once_with('a' * 64)

    def test_failed_fallback_keeps_recovery_record(self):
        runner = Mock()
        runner.status.return_value = {'active': False, 'cleared': False, 'run_id': 'current-run'}
        with patch.object(server, 'runner', runner), patch.object(device, 'clear_location', side_effect=device.DeviceError('disconnected')):
            with self.assertRaises(device.DeviceError):
                server.clear_all()
        self.assertTrue(self.marker.exists())

    def test_wait_for_stop_includes_reader_final_state(self):
        runner = replay.Replay()
        runner.finished.clear()
        runner.info = {'active': True, 'state': 'running', 'cleared': False}
        runner.process = Mock()
        runner.process.poll.return_value = None

        def publish_final():
            time.sleep(.08)
            with runner.lock:
                runner.info.update(active=False, state='stopped', cleared=True)
                runner.finished.set()

        thread = threading.Thread(target=publish_final)
        thread.start()
        started = time.monotonic()
        state = runner.stop_and_wait()
        thread.join()
        self.assertGreaterEqual(time.monotonic() - started, .06)
        self.assertFalse(state['active'])
        self.assertTrue(state['cleared'])


class BackendHTTPTests(unittest.TestCase):
    def setUp(self):
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.port_patch = patch.object(server, 'PORT', self.http.server_port)
        self.port_patch.start()
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.port_patch.stop()

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=1)
        origin = f'http://127.0.0.1:{self.http.server_port}'
        request_headers = {'Origin': origin, 'X-TrackLab-Token': server.TOKEN, **(headers or {})}
        connection.request('GET' if data is None else 'POST', path,
                           None if data is None else json.dumps(data), request_headers)
        response = connection.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, headers, body

    def test_preview_and_status_respond_during_device_operation(self):
        server.LOCK.acquire()
        try:
            started = time.monotonic()
            status, _, body = self.request('/api/preview', {'distance_km': 1, 'pace': 8, 'variation': False})
            self.assertEqual(status, 200)
            self.assertIn('track', json.loads(body))
            status, _, body = self.request('/api/status')
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['operation_pending'])
            self.assertLess(time.monotonic() - started, 1)
        finally:
            server.LOCK.release()

    def test_static_assets_validate_cache_while_api_is_never_cached(self):
        status, headers, original = self.request('/portal/app.js')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-cache')
        self.assertIn('ETag', headers)
        status, cached, body = self.request('/portal/app.js', headers={'If-None-Match': headers['ETag']})
        self.assertEqual(status, 304)
        self.assertEqual(body, b'')
        self.assertEqual(cached['ETag'], headers['ETag'])
        status, api_headers, body = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(api_headers['Cache-Control'], 'no-store')
        data = json.loads(body)
        self.assertEqual(data['version'], VERSION)
        self.assertEqual(data['instance'], server.INSTANCE)
        self.assertEqual(len(data['instance']), 16)
        self.assertTrue(original)

    def test_removed_cloud_and_tile_endpoints_are_not_exposed(self):
        for path, data in [('/tiles/1/0/0.png', None), ('/api/keepalive', {}), ('/api/set', {}), ('/api/register', {})]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, data)[0], 404)


class AssetTests(unittest.TestCase):
    def test_asset_allowlist_rejects_private_or_unknown_files(self):
        for path in ['/portal/../server.py', '/portal/downloads/anything.zip', '/portal/unknown.png']:
            self.assertIsNone(web_assets.asset(path))
        for path in ['/fixed', '/connect', '/portal/vendor/leaflet.js']:
            self.assertIsNotNone(web_assets.asset(path))


if __name__ == '__main__':
    unittest.main()

class ShutdownTests(unittest.TestCase):
    setUp = BackendHTTPTests.setUp
    tearDown = BackendHTTPTests.tearDown
    request = BackendHTTPTests.request
    def test_shutdown_rejects_running_or_recovery_pending(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'MARKER', Path(temp)/'pending.json'), patch.object(self.http, 'shutdown') as stop:
            with patch.object(server.runner, 'status', return_value={'active': True, 'cleared': False}):
                self.assertEqual(self.request('/api/shutdown', {})[0], 409)
            server.MARKER.write_text('{}')
            with patch.object(server.runner, 'status', return_value={'active': False, 'cleared': False}):
                self.assertEqual(self.request('/api/shutdown', {})[0], 409)
            stop.assert_not_called()

    def test_shutdown_checks_authentication_then_stops_only_idle(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'MARKER', Path(temp)/'pending.json'), patch.object(server.runner, 'status', return_value={'active': False}), patch.object(self.http, 'shutdown') as stop:
            self.assertEqual(self.request('/api/shutdown', {}, {'X-TrackLab-Token': 'invalid'})[0], 403)
            self.assertEqual(self.request('/api/shutdown', {}, {'Origin': 'https://elsewhere.invalid'})[0], 403)
            stop.assert_not_called()
            status, _, body = self.request('/api/shutdown', {})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['stopped'])
            for _ in range(20):
                if stop.called: break
                time.sleep(.01)
            stop.assert_called_once()
