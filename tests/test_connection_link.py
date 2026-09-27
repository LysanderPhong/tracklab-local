import unittest
from concurrent.futures import ThreadPoolExecutor
from relay import Relay, RelayError


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.now=10
        self.relay=Relay(lambda:self.now)
        self.reg=self.relay.create('iphone-mac')

    def claim(self):
        return self.relay.pair_link(self.reg['session'],self.reg['link_token'])

    def test_link_is_single_use_and_revokes_legacy_code(self):
        web=self.claim()
        self.assertNotEqual(web['token'],self.reg['token'])
        self.assertEqual(self.relay.status(web['session'],web['token'])['platform'],'iphone-mac')
        with self.assertRaises(RelayError): self.claim()
        with self.assertRaises(RelayError): self.relay.pair(self.reg['code'])
        with self.assertRaises(RelayError): self.relay.poll(web['session'],web['token'],{})
        with self.assertRaises(RelayError): self.relay.status(web['session'],self.reg['link_token'])

    def test_legacy_claim_revokes_link(self):
        self.relay.pair(self.reg['code'])
        with self.assertRaises(RelayError): self.claim()

    def test_bad_or_expired_links_cannot_claim(self):
        for token in [None, {}, 123, '', '错误', self.reg['token']]:
            with self.assertRaises(RelayError):self.relay.pair_link(self.reg['session'],token)
        with self.assertRaises(RelayError):self.relay.pair_link({},self.reg['link_token'])
        self.now+=301
        with self.assertRaises(RelayError):self.claim()

    def test_concurrent_claim_has_one_winner(self):
        def claim(_):
            try:self.claim();return 1
            except RelayError:return 0
        with ThreadPoolExecutor(4) as pool:self.assertEqual(sum(pool.map(claim,range(4))),1)

