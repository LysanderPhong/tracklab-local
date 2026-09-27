import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import desktop_launcher as launcher

class LauncherTests(unittest.TestCase):
    def test_existing_service_is_reused_including_active_session(self):
        with patch.object(launcher,'probe',return_value={'replay':{'active':True}}),patch.object(launcher.subprocess,'Popen') as spawn:
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
            self.assertEqual(args.args[0][1],str(Path(temp)/'server.py'))
            spawn.return_value.terminate.assert_not_called()
    def test_failed_start_cleans_up_own_child(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(launcher,'probe',side_effect=[None,launcher.LauncherError('bad')]),patch.object(launcher.subprocess,'Popen') as spawn:
            spawn.return_value.poll.return_value=None
            with self.assertRaises(launcher.LauncherError):launcher.ensure_service(Path(temp))
            spawn.return_value.terminate.assert_called_once()
    def test_launcher_only_opens_local_ui(self):
        with patch.object(launcher,'ensure_service'),patch.object(launcher.webbrowser,'open',return_value=True) as browser,patch('sys.stdout',io.StringIO()):launcher.main()
        browser.assert_called_once_with('http://127.0.0.1:8769/fixed')
    def test_probe_rejects_non_tracklab_response(self):
        response=Mock();response.__enter__=Mock(return_value=io.BytesIO(json.dumps({'hello':'other service'}).encode()));response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        with patch.object(launcher.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(launcher.LauncherError):launcher.probe()
