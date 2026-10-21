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
