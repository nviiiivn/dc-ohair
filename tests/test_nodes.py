"""Hub to hub: one hub shows another's city as a village of its own, read-only,
with a token the other one made. Two real hubs, talking over HTTP. Run: python3 -m unittest"""
import json, os, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('DC_DATA', tempfile.mkdtemp(prefix='dc-ohair-test-'))

import collect  # noqa: E402
import hub  # noqa: E402

GiB = 1024 ** 3


def serve(h):
    handler = type('H', (hub.Handler,), {'hub': h})
    server = hub.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, 'http://127.0.0.1:%d' % server.server_address[1]


class NodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(collect.DATA, exist_ok=True)
        # B: the hub being shared, with one scanned device and one hub of its own it shows
        self.b_path = os.path.join(self.tmp.name, 'b.yml')
        collect.save_config(collect.validate({'devices': [
            {'id': 'b-nas', 'name': 'NAS', 'host': 'nas.lan', 'drives': [{'name': 'Media', 'path': '/srv/media'}]},
            {'id': 'b-loop', 'name': 'Its friend', 'node': 'http://192.0.2.9:7171'}]}), self.b_path)
        tree = {'n': 'Media', 'c': [0, 0, 0, 900 * GiB, 10 * GiB], 'f': 120, 'm': 1700000000,
                'k': [{'n': 'Heat (1995).mkv', 't': 'f', 'c': [0, 0, 0, 40 * GiB, 0], 'f': 1, 'm': 1700000000}]}
        collect.save(os.path.join(collect.DATA, 'b-nas.json'), {'scanned': '2026-10-21T04:30:00-0700', 'drives': [
            {'name': 'Media', 'path': '/srv/media', 'tree': tree, 'fs': {'total': 1000 * GiB, 'used': 960 * GiB, 'free': 40 * GiB},
             'disk': {'dev': '/dev/sda', 'ids': ['ata-SERIAL123']},
             'health': {'state': 'ok', 'notes': [], 'serial': 'SERIAL123', 'model': 'Disk'}}]})
        collect.save(os.path.join(collect.DATA, 'b-loop.json'), {'scanned': 'x', 'drives': [{'name': 'Far · Thing', 'tree': tree}]})
        self.b = hub.Hub(collect.load_config(self.b_path), self.b_path)
        self.b_server, self.b_url = serve(self.b)
        self.token = self.b.share("Sam's house")
        # A: the hub that shows B
        self.a_path = os.path.join(self.tmp.name, 'a.yml')
        collect.save_config(collect.validate({'devices': [
            {'id': 'a-sams', 'name': "Sam's house", 'node': self.b_url, 'node_token': self.token}]}), self.a_path)
        self.a = hub.Hub(collect.load_config(self.a_path), self.a_path)

    def tearDown(self):
        self.b_server.shutdown()
        self.b_server.server_close()
        self.tmp.cleanup()

    def fetch(self):
        collect.collect(self.a.cfg['devices'][0], self.a.cfg)
        return collect.load(os.path.join(collect.DATA, 'status.json'))['a-sams']

    def test_the_other_hub_is_a_village_here(self):
        status = self.fetch()
        self.assertTrue(status['ok'], status)
        dev = self.a.city()['devices'][0]
        self.assertTrue(dev['node'])
        self.assertEqual([d['name'] for d in dev['drives']], ['NAS · Media'])
        self.assertEqual(dev['drives'][0]['tree']['k'][0]['n'], 'Heat (1995).mkv')
        self.assertEqual(self.a.drives()[0]['drive'], 'NAS · Media')

    def test_only_its_own_devices_and_nothing_private_cross(self):
        self.fetch()
        sent = json.dumps(collect.load(os.path.join(collect.DATA, 'a-sams.json')))
        self.assertNotIn('SERIAL123', sent)
        self.assertNotIn('nas.lan', sent)
        self.assertNotIn('Far · Thing', sent)          # B's own linked hub is not passed on
        self.assertNotIn(self.token, sent)

    def test_a_wrong_or_revoked_token_is_turned_away_in_words(self):
        self.a.cfg['devices'][0]['node_token'] = 'not-it'
        self.assertIn("turned this one's token away", self.fetch()['msg'])
        self.a.cfg['devices'][0]['node_token'] = self.token
        self.b.unshare("Sam's house")
        self.assertFalse(self.fetch()['ok'])

    def test_tokens_never_leave_either_hub_and_survive_a_settings_save(self):
        b_pub = self.b.public_config()
        self.assertEqual(b_pub['hub']['shares'], [{'label': "Sam's house", 'created': b_pub['hub']['shares'][0]['created']}])
        with open(self.b_path) as f:
            self.assertNotIn(self.token, f.read())         # B keeps only a hash
        a_pub = self.a.public_config()
        self.assertNotIn('node_token', a_pub['devices'][0])
        self.assertTrue(a_pub['devices'][0]['node_paired'])
        self.a.save_config(a_pub)                          # the page sends the token back blank
        self.assertEqual(self.a.cfg['devices'][0]['node_token'], self.token)
        self.b.save_config(self.b.public_config())
        self.assertIsNotNone(self.b.share_for(self.token))

    def test_its_full_drives_alert_its_owner_not_this_hub_unless_asked(self):
        self.fetch()
        self.assertFalse([x for x in self.a.current_alerts() if x['kind'] == 'full'])
        self.a.cfg['devices'][0]['node_alerts'] = True
        self.assertTrue([x for x in self.a.current_alerts() if x['kind'] == 'full'])

    def test_an_address_that_is_not_a_hub_says_so(self):
        out, err = collect.node_report({'node': 'ftp://nope'})
        self.assertIn('http://', err)
        out, err = collect.node_report({'node': 'http://127.0.0.1:9', 'node_token': 'x'}, timeout=3)
        self.assertIn("Can't reach", err)


if __name__ == '__main__':
    unittest.main()
