"""Tests for the agent, collector config and hub helpers. Run: python3 -m unittest"""
import base64, datetime, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('DC_DATA', tempfile.mkdtemp(prefix='dc-ohair-test-'))

import collect  # noqa: E402
import demo  # noqa: E402
import hub  # noqa: E402

MiB = 1024 ** 2


def sparse(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.truncate(size)


def run_agent(drives, **spec):
    spec = dict(spec, drives=drives)
    arg = base64.b64encode(json.dumps(spec).encode()).decode()
    out = subprocess.run([sys.executable, os.path.join(ROOT, 'agent.py'), arg], stdout=subprocess.PIPE, check=True)
    return json.loads(out.stdout)


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        sparse(os.path.join(d, 'Movies', 'Some Film (2001).mkv'), 120 * MiB)
        sparse(os.path.join(d, 'Shows', 'Show', 'Show.S01E02.mkv'), 110 * MiB)
        sparse(os.path.join(d, 'Music', 'Album', '01.flac'), 30 * MiB)
        sparse(os.path.join(d, 'steamapps', 'common', 'game.pak'), 70 * MiB)
        sparse(os.path.join(d, 'notes.txt'), 1000)
        os.link(os.path.join(d, 'Music', 'Album', '01.flac'), os.path.join(d, 'Music', 'same.flac'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_categories_and_hardlinks(self):
        out = run_agent([{'name': 'test', 'path': self.tmp.name}], min=50 * MiB)
        tree = out['drives'][0]['tree']
        music, games, series, movies, other = tree['c']
        self.assertEqual(music, 30 * MiB)  # the hard link is counted once
        self.assertEqual(games, 70 * MiB)
        self.assertEqual(series, 110 * MiB)
        self.assertEqual(movies, 120 * MiB)
        self.assertEqual(other, 1000)
        self.assertEqual(tree['f'], 5)

    def test_big_files_are_their_own_nodes(self):
        out = run_agent([{'name': 'test', 'path': self.tmp.name}], min=50 * MiB)
        movies = next(k for k in out['drives'][0]['tree']['k'] if k['n'] == 'Movies')
        self.assertEqual(movies['k'][0]['t'], 'f')
        self.assertEqual(movies['k'][0]['n'], 'Some Film (2001).mkv')

    def test_small_stuff_is_folded(self):
        out = run_agent([{'name': 'test', 'path': self.tmp.name}], min=50 * MiB)
        names = [k['n'] for k in out['drives'][0]['tree']['k']]
        self.assertIn('…', names)
        self.assertNotIn('notes.txt', names)

    def test_missing_and_unmounted_drives(self):
        out = run_agent([{'name': 'gone', 'path': os.path.join(self.tmp.name, 'nope')},
                         {'name': 'not mounted', 'path': self.tmp.name, 'mounted': True}])
        self.assertIn('not found', out['drives'][0]['error'])
        self.assertEqual(out['drives'][1]['error'], 'not mounted')

    def test_skip(self):
        skip = os.path.join(self.tmp.name, 'Movies')
        out = run_agent([{'name': 'test', 'path': self.tmp.name, 'skip': [skip]}])
        self.assertEqual(out['drives'][0]['tree']['c'][3], 0)


class ConfigTest(unittest.TestCase):
    def write(self, text):
        f = tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_parse_size(self):
        self.assertEqual(collect.parse_size('50M'), 50 * MiB)
        self.assertEqual(collect.parse_size('1.5G'), int(1.5 * 1024 ** 3))
        self.assertEqual(collect.parse_size('2TiB'), 2 * 1024 ** 4)
        self.assertEqual(collect.parse_size(1234), 1234)
        with self.assertRaises(ValueError):
            collect.parse_size('lots')

    def test_example_config_loads(self):
        cfg = collect.load_config(os.path.join(ROOT, 'devices.example.yml'))
        self.assertTrue(cfg['devices'])
        self.assertEqual(cfg['hub']['port'], 7171)

    def test_defaults_fill_in(self):
        cfg = collect.load_config(self.write('devices:\n  - {id: a, local: true, drives: [{name: R, path: /}]}\n'))
        self.assertEqual(cfg['hub']['alert_percent'], 90)
        self.assertEqual(cfg['scan']['min_size'], '50M')

    def test_bad_ids_are_refused(self):
        for bad in ['../x', '', 'a/b']:
            path = self.write('devices:\n  - {id: "%s", local: true, drives: [{name: R, path: /}]}\n' % bad)
            with self.assertRaises(ValueError):
                collect.load_config(path)

    def test_device_needs_a_way_in(self):
        with self.assertRaises(ValueError):
            collect.load_config(self.write('devices:\n  - {id: a, drives: [{name: R, path: /}]}\n'))


class KeyTest(unittest.TestCase):
    def test_default_keys_are_kept_next_to_the_hub_key(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, '.ssh'))
        open(os.path.join(home, '.ssh', 'id_ed25519'), 'w').close()
        old_home, old_dir = os.environ.get('HOME'), collect.SSH_DIR
        os.environ['HOME'] = home
        collect.SSH_DIR = os.path.join(home, 'hub')
        os.makedirs(collect.SSH_DIR)
        open(os.path.join(collect.SSH_DIR, 'id_ed25519'), 'w').close()
        try:
            args = collect.identity_args()
        finally:
            os.environ['HOME'] = old_home
            collect.SSH_DIR = old_dir
        self.assertEqual(args, ['-i', os.path.join(home, '.ssh', 'id_ed25519'), '-i', os.path.join(home, 'hub', 'id_ed25519')])


class PushTest(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix='.yml')
        cfg = collect.validate({'devices': [{'id': 'pc', 'name': 'PC', 'push': True, 'drives': []}]})
        collect.save_config(cfg, self.path)
        self.hub = hub.Hub(collect.load_config(self.path), self.path)

    def tearDown(self):
        for p in (self.path, self.path + '.bak'):
            if os.path.exists(p):
                os.unlink(p)

    def test_token_is_stored_hashed_and_finds_its_device(self):
        token = self.hub.new_token('pc')
        with open(self.path) as f:
            self.assertNotIn(token, f.read())
        self.assertEqual(self.hub.device_for(token)['id'], 'pc')
        self.assertIsNone(self.hub.device_for('wrong'))
        self.assertNotIn('push_token', self.hub.public_config()['devices'][0])
        self.assertTrue(self.hub.public_config()['devices'][0]['paired'])

    def test_settings_save_keeps_the_token_and_revoke_drops_it(self):
        token = self.hub.new_token('pc')
        self.hub.save_config(self.hub.public_config())
        self.assertIsNotNone(self.hub.device_for(token))
        self.hub.revoke('pc')
        self.assertIsNone(self.hub.device_for(token))

    def test_first_report_picks_the_drives(self):
        self.hub.new_token('pc')
        dev = self.hub.cfg['devices'][0]
        self.assertEqual(self.hub.push_spec(dev)['drives'], 'auto')
        out = {'drives': [{'name': 'C', 'path': 'C:\\', 'fs': {'used': 1, 'free': 1, 'total': 2}, 'tree': {'n': 'C', 'c': [0, 0, 0, 0, 1]}}],
               'secs': 1, 'scanned': '2026-01-01T00:00:00+0000'}
        self.hub.take_report(dev, out)
        self.assertEqual(self.hub.cfg['devices'][0]['drives'], [{'name': 'C', 'path': 'C:\\'}])
        with self.assertRaises(ValueError):
            self.hub.take_report(dev, {'nope': 1})


def load_agent():
    import importlib.util
    old = sys.argv
    sys.argv = ['agent', base64.b64encode(b'{}').decode()]
    try:
        spec = importlib.util.spec_from_file_location('agent_mod', os.path.join(ROOT, 'agent.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old
    return mod


class HealthTest(unittest.TestCase):
    def test_smart_summaries(self):
        agent = load_agent()
        ok = agent.summarize({'smart_status': {'passed': True}, 'temperature': {'current': 35}, 'power_on_time': {'hours': 1200},
                              'ata_smart_attributes': {'table': [{'id': 5, 'raw': {'value': 0}}, {'id': 197, 'raw': {'value': 0}}]}}, 0)
        self.assertEqual((ok['state'], ok['temp'], ok['hours']), ('ok', 35, 1200))
        warn = agent.summarize({'smart_status': {'passed': True}, 'ata_smart_attributes': {'table': [{'id': 5, 'raw': {'value': 8}}]}}, 0)
        self.assertEqual(warn['state'], 'warning')
        self.assertIn('8 reallocated sectors', warn['notes'])
        nvme = agent.summarize({'smart_status': {'passed': True}, 'nvme_smart_health_information_log': {'media_errors': 0, 'percentage_used': 95}}, 0)
        self.assertEqual(nvme['state'], 'warning')
        bad = agent.summarize({'smart_status': {'passed': False}}, 8)
        self.assertEqual(bad['state'], 'failing')


class ScrutinyTest(unittest.TestCase):
    def test_summary_is_matched_by_wwn_and_serial(self):
        import http.server, threading
        summary = {'success': True, 'data': {'summary': {
            '0x5000cca264eb01d7': {'device': {'wwn': '0x5000cca264eb01d7', 'serial_number': 'AAA111', 'model_name': 'HDD', 'device_status': 0},
                                   'smart': {'temp': 34, 'power_on_hours': 100}},
            '0x5000cca264ec3183': {'device': {'wwn': '0x5000cca264ec3183', 'serial_number': 'BBB222', 'model_name': 'HDD2', 'device_status': 1},
                                   'smart': {'temp': 50, 'power_on_hours': 200}}}}}

        class Fake(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(summary).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cfg = collect.validate({'hub': {'health': {'mode': 'scrutiny', 'scrutiny_url': 'http://127.0.0.1:%d' % srv.server_port}}})
        h = hub.Hub(cfg)
        devices = [{'drives': [{'name': 'A', 'disk': {'ids': ['wwn-0x5000cca264eb01d7']}},
                               {'name': 'B', 'disk': {'ids': ['ata-HDD2_BBB222']}},
                               {'name': 'C', 'disk': {'ids': ['ata-OTHER_ZZZ']}}]}]
        h.attach_health(devices)
        srv.shutdown()
        a, b, c = devices[0]['drives']
        self.assertEqual((a['health']['state'], a['health']['temp']), ('ok', 34))
        self.assertEqual(b['health']['state'], 'failing')
        self.assertNotIn('health', c)


class DiscoverTest(unittest.TestCase):
    def test_only_small_private_networks(self):
        import discover
        self.assertEqual(str(discover.network('192.168.1.7/24')), '192.168.1.0/24')
        for bad in ('8.8.8.0/24', '10.0.0.0/8', '127.0.0.0/24'):
            with self.assertRaises(ValueError):
                discover.network(bad)

    def test_os_hint(self):
        import discover
        self.assertEqual(discover.os_hint('SSH-2.0-OpenSSH_for_Windows_9.5', [22]), 'Windows')
        self.assertEqual(discover.os_hint('', [445]), 'Windows?')
        self.assertEqual(discover.os_hint('SSH-2.0-OpenSSH_9.8', [22, 548]), 'Mac')

    def test_random_macs_are_named_as_such(self):
        import discover
        self.assertEqual(discover.vendor('DA:A1:19:00:00:01'), 'private (random) address')


class HubTest(unittest.TestCase):
    def test_clock_times_reads_yaml_sexagesimal(self):
        self.assertEqual(hub.clock_times(['04:30', 270, '23:05']), [(4, 30), (4, 30), (23, 5)])

    def test_next_run(self):
        now = datetime.datetime(2026, 1, 1, 12, 0)
        self.assertEqual(hub.next_run(['04:30'], now), datetime.datetime(2026, 1, 2, 4, 30))
        self.assertEqual(hub.next_run(['04:30', '13:00'], now), datetime.datetime(2026, 1, 1, 13, 0))
        self.assertIsNone(hub.next_run([], now))

    def test_demo_city_and_drives(self):
        d = demo.build()
        h = hub.Hub(collect.validate(d['config']), demo=d)
        drives = h.drives()
        self.assertEqual(len(drives), sum(len(dev['drives']) for dev in d['city']['devices']))
        self.assertTrue(all(0 < x['percent'] <= 100 for x in drives))
        self.assertTrue(h.scan())  # demo mode only pretends to scan
        self.assertFalse(os.path.exists(os.path.join(collect.DATA, 'nas.json')))

    def test_city_falls_back_to_snapshot(self):
        cfg = {'hub': dict(collect.DEFAULTS['hub']), 'devices': [
            {'id': 'box', 'name': 'Box', 'ssh': 'u@h', 'drives': [{'name': 'D', 'path': '/d'}]}]}
        os.makedirs(os.path.join(collect.DATA, 'snapshot'), exist_ok=True)
        snap = {'scanned': '2026-01-01T00:00:00+0000', 'drives': [{'name': 'D', 'path': '/d', 'tree': {'n': 'D', 'c': [1, 0, 0, 0, 0]}}]}
        collect.save(os.path.join(collect.DATA, 'snapshot', 'box.json'), snap)
        city = hub.Hub(cfg).city()
        self.assertEqual(city['devices'][0]['drives'][0]['source'], 'snapshot')


if __name__ == '__main__':
    unittest.main()
