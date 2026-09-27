import json
import http.client
import threading
import unittest
from unittest.mock import patch
import cloud_server
from relay import Relay, RelayError

class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.r=Relay(lambda:self.now)
        self.reg=self.r.create('iphone-mac')
        self.web=self.r.pair(self.reg['code'])
        self.sid=self.reg['session'];self.dt=self.reg['token'];self.wt=self.web['token']
        self.r.poll(self.sid,self.dt,{'status':{'ready':False}})
    def test_refresh_scan_then_fixed_can_start(self):
        result=self.r.refresh(self.sid,self.wt,'refresh-001')
        self.assertTrue(result['scan_requested'])
        self.assertFalse(result['device']['ready'])
        self.assertFalse(self.r.refresh(self.sid,self.wt,'refresh-002')['scan_requested'])
        command=self.r.poll(self.sid,self.dt,{'status':{'ready':False}})['command']
        self.assertEqual(command['action'],'scan')
        self.r.poll(self.sid,self.dt,{'status':{'ready':True},'ack':{'id':command['id'],'ok':True}})
        self.assertTrue(self.r.command(self.sid,self.wt,{'id':'fixed-001','action':'fixed','latitude':20,'longitude':110,'seconds':20})['accepted'])
    def test_offline_active_and_recovery_do_not_scan(self):
        for status in [{'active':True},{'needs_clear':True}]:
            self.r.poll(self.sid,self.dt,{'status':status})
            self.assertFalse(self.r.refresh(self.sid,self.wt,'refresh-001')['scan_requested'])
            self.assertIsNone(self.r.poll(self.sid,self.dt,{'status':status})['command'])
        self.now+=11
        result=self.r.refresh(self.sid,self.wt,'refresh-002')
        self.assertFalse(result['online']);self.assertFalse(result['scan_requested'])
    def test_unauthorized_and_not_ready_still_blocked(self):
        with self.assertRaises(RelayError):self.r.refresh(self.sid,self.dt,'refresh-001')
        with self.assertRaises(RelayError):self.r.command(self.sid,self.wt,{'id':'fixed-001','action':'fixed','latitude':20,'longitude':110})

class RefreshHTTPTests(unittest.TestCase):
    def test_refresh_endpoint_auth_origin_and_scan(self):
        server=cloud_server.BoundedServer(('127.0.0.1',0),cloud_server.Handler)
        origin=f'http://127.0.0.1:{server.server_port}'
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        def request(path,data,headers=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            c.request('POST',path,json.dumps(data),{'Content-Type':'application/json',**(headers or {})})
            res=c.getresponse();body=json.loads(res.read());c.close();return res.status,body
        with patch.object(cloud_server,'ORIGIN',origin),patch.object(cloud_server,'relay',Relay()):
            thread.start()
            try:
                _,reg=request('/api/register',{'platform':'iphone-mac'})
                _,web=request('/api/pair',{'code':reg['code']})
                auth={'Authorization':'Bearer '+web['session']+'.'+web['token']}
                self.assertEqual(request('/api/refresh',{'id':'refresh-001'})[0],401)
                self.assertEqual(request('/api/refresh',{'id':'refresh-001'},{**auth,'Origin':'https://other.invalid'})[0],403)
                status,result=request('/api/refresh',{'id':'refresh-001'},auth)
                self.assertEqual(status,200);self.assertTrue(result['scan_requested'])
                _,polled=request('/api/poll',{'status':{'ready':False}},{'Authorization':'Bearer '+reg['session']+'.'+reg['token']})
                self.assertEqual(polled['command']['action'],'scan')
            finally:server.shutdown();server.server_close();thread.join()
