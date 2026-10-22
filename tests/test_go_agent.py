"""The Go agent (agent-go/) against agent.py: the same folders and the same spec
must give the same report, so the hub cannot tell which one scanned.
Skipped where Go is not installed. Run: python3 -m unittest"""
import base64, json, os, shutil, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('DC_DATA', tempfile.mkdtemp(prefix='dc-ohair-test-'))

import collect  # noqa: E402
import hub  # noqa: E402
MiB = 1024 ** 2
GO = shutil.which('go')


def sparse(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.truncate(size)


def solid(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'x' * size)


def canon(node):
    """Order kids by size then name: ties come out of readdir in either order."""
    node = dict(node)
    if 'k' in node:
        node['k'] = sorted((canon(k) for k in node['k']), key=lambda k: (-sum(k['c']), k['n']))
    return node


def report(out):
    out = json.loads(out)
    for k in ('secs', 'scanned', 'via'):
        out.pop(k, None)
    for d in out.get('drives', []):
        d.pop('secs', None)
        if 'tree' in d:
            d['tree'] = canon(d['tree'])
    return out


@unittest.skipUnless(GO, 'Go is not installed')
class GoAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin_dir = tempfile.TemporaryDirectory()
        cls.binary = os.path.join(cls.bin_dir.name, 'dc-agent')
        subprocess.run([GO, 'build', '-o', cls.binary, '.'], cwd=os.path.join(ROOT, 'agent-go'), check=True,
                       env=dict(os.environ, CGO_ENABLED='0'))

    @classmethod
    def tearDownClass(cls):
        cls.bin_dir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        sparse(os.path.join(d, 'Movies', 'Some Film (2001).mkv'), 120 * MiB)
        sparse(os.path.join(d, 'Movies', 'Other Film (1999).mp4'), 130 * MiB)
        sparse(os.path.join(d, 'Shows', 'Show', 'Season 1', 'Show.S01E02.mkv'), 110 * MiB)
        sparse(os.path.join(d, 'Shows', 'Show', 'Season 1', 'Show.S01E03.mkv'), 105 * MiB)
        solid(os.path.join(d, 'Music', 'Album', '01.flac'), 3 * MiB)
        solid(os.path.join(d, 'Music', 'Album', '02.FLAC'), 2 * MiB)
        sparse(os.path.join(d, 'steamapps', 'common', 'Game', 'game.pak'), 70 * MiB)
        sparse(os.path.join(d, 'storage', 'emulated', '0', 'clip.mp4'), 101 * MiB)   # not a game: /emulated/
        sparse(os.path.join(d, 'emulators', 'roms', 'x.bin'), 9 * MiB)
        solid(os.path.join(d, 'notes.txt'), 1000)
        solid(os.path.join(d, '.bashrc'), 300)
        solid(os.path.join(d, 'Café', 'menu ünïcode.txt'), 5000)
        os.link(os.path.join(d, 'Music', 'Album', '01.flac'), os.path.join(d, 'Music', 'same.flac'))
        os.symlink(os.path.join(d, 'Movies'), os.path.join(d, 'link-to-movies'))
        sparse(os.path.join(d, '.snapshots', 'old', 'big.img'), 200 * MiB)
        sparse(os.path.join(d, 'skipme', 'big.img'), 200 * MiB)
        sparse(os.path.join(d, 'Caches', 'tmp.dat'), 2 * MiB)
        deep = os.path.join(d, *['level%d' % i for i in range(8)])
        sparse(os.path.join(deep, 'deep.mkv'), 150 * MiB)
        for i in range(30):
            solid(os.path.join(d, 'many', 'f%02d.txt' % i), 1000 * (i + 1))

    def tearDown(self):
        self.tmp.cleanup()

    def both(self, spec):
        arg = base64.b64encode(json.dumps(spec).encode()).decode()
        py = subprocess.run([sys.executable, os.path.join(ROOT, 'agent.py'), arg], stdout=subprocess.PIPE, check=True).stdout
        go = subprocess.run([self.binary, arg], stdout=subprocess.PIPE, check=True).stdout
        return report(py), report(go)

    def test_the_same_report_as_agent_py(self):
        spec = {'min': 4 * MiB, 'depth': 5, 'kids': 3,
                'drives': [{'name': 'Disk', 'path': self.tmp.name, 'exclude': ['*/skipme', 'Caches']},
                           {'name': 'Gone', 'path': os.path.join(self.tmp.name, 'nope')},
                           {'name': 'NotMounted', 'path': os.path.join(self.tmp.name, 'Movies'), 'mounted': True}]}
        py, go = self.both(spec)
        self.assertEqual(go, py)
        tree = go['drives'][0]['tree']
        self.assertEqual(tree['c'], py['drives'][0]['tree']['c'])
        self.assertGreater(tree['c'][1], 0)       # games
        self.assertEqual(go['drives'][1]['error'], py['drives'][1]['error'])

    def test_the_same_with_the_hubs_own_categories(self):
        spec = {'min': 1 * MiB, 'categories': {'games': ['/emulators/'], 'big_video': 50 * MiB, 'music': []},
                'drives': [{'name': 'Disk', 'path': self.tmp.name}]}
        py, go = self.both(spec)
        self.assertEqual(go, py)

    def test_mounts_and_check_answer_in_the_same_shape(self):
        py, go = self.both({'mode': 'mounts'})
        self.assertEqual([m['path'] for m in go['mounts']], [m['path'] for m in py['mounts']])
        self.assertEqual(set(go['mounts'][0]) if go['mounts'] else set(), set(py['mounts'][0]) if py['mounts'] else set())
        py, go = self.both({'mode': 'check', 'paths': [self.tmp.name, '/nope']})
        self.assertEqual([p for p in go['paths']], [p for p in py['paths']])


@unittest.skipUnless(GO, 'Go is not installed')
class GoPushTest(unittest.TestCase):
    """The Go agent enrolling with a real hub, as Settings' install line has it do."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.binary = os.path.join(t, 'dc-agent')
        subprocess.run([GO, 'build', '-ldflags', '-X main.Version=' + hub.VERSION, '-o', self.binary, '.'],
                       cwd=os.path.join(ROOT, 'agent-go'), check=True, env=dict(os.environ, CGO_ENABLED='0'))
        self.drive = os.path.join(t, 'Media')
        sparse(os.path.join(self.drive, 'Movies', 'Film (2001).mkv'), 120 * MiB)
        self.cfg_path = os.path.join(t, 'devices.yml')
        cfg = collect.validate({'devices': [{'id': 'box', 'name': 'Box', 'push': True,
                                             'drives': [{'name': 'Media', 'path': self.drive}]}]})
        collect.save_config(cfg, self.cfg_path)
        self.hub = hub.Hub(collect.load_config(self.cfg_path), self.cfg_path)
        hub.Handler.hub = self.hub
        self.server = hub.ThreadingHTTPServer(('127.0.0.1', 0), hub.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.home = os.path.join(t, 'home')
        os.makedirs(self.home)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def agent(self, *args):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        return subprocess.run([self.binary] + list(args), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=120)

    def test_install_scans_and_the_hub_takes_the_report(self):
        token = self.hub.new_token('box')
        p = self.agent('install', '--hub', self.url, '--token', token, '--no-schedule')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('sent to the hub', p.stdout)
        saved = collect.load(os.path.join(collect.DATA, 'box.json'))
        self.assertEqual(saved['drives'][0]['tree']['c'][3], 120 * MiB)
        self.assertTrue(saved['via'].startswith('dc-agent'))
        conf = os.path.join(self.home, '.dc-ohair', 'push.json')
        with open(conf) as f:
            self.assertEqual(json.load(f), {'hub': self.url, 'token': token})
        self.assertEqual(os.stat(conf).st_mode & 0o777, 0o600)
        self.assertTrue(os.access(os.path.join(self.home, '.dc-ohair', 'dc-agent'), os.X_OK))
        self.assertEqual(self.agent('run').returncode, 0)        # what the daily schedule runs

    def test_a_wrong_token_is_turned_away_in_words(self):
        p = self.agent('install', '--hub', self.url, '--token', 'not-it', '--no-schedule')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('the hub said 401', p.stderr)

    def test_uninstall_puts_the_folder_aside_rather_than_deleting_it(self):
        token = self.hub.new_token('box')
        self.agent('install', '--hub', self.url, '--token', token, '--no-schedule')
        p = self.agent('uninstall')
        self.assertEqual(p.returncode, 0, p.stderr)
        aside = [n for n in os.listdir(self.home) if n.startswith('.dc-ohair-uninstalled-')]
        self.assertEqual(len(aside), 1)
        self.assertTrue(os.path.exists(os.path.join(self.home, aside[0], 'push.json')))

    def test_the_hub_serves_its_agents_or_points_at_the_release(self):
        v = json.load(urllib.request.urlopen(self.url + '/dl/version'))
        self.assertEqual(v['version'], hub.VERSION)
        os.makedirs(os.path.join(self.tmp.name, 'dist'))
        with open(os.path.join(self.tmp.name, 'dist', 'dc-agent-linux-arm64'), 'wb') as f:
            f.write(b'BIN')
        old = hub.AGENTS
        hub.AGENTS = os.path.join(self.tmp.name, 'dist')
        try:
            self.assertEqual(urllib.request.urlopen(self.url + '/dl/dc-agent-linux-arm64').read(), b'BIN')
            self.assertEqual(json.load(urllib.request.urlopen(self.url + '/dl/version'))['files'], ['dc-agent-linux-arm64'])

            class NoFollow(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **kw):
                    return None
            opener = urllib.request.build_opener(NoFollow)
            with self.assertRaises(urllib.error.HTTPError) as e:
                opener.open(self.url + '/dl/dc-agent-windows-amd64.exe')
            self.assertEqual(e.exception.code, 302)
            self.assertIn('/releases/download/v%s/dc-agent-windows-amd64.exe' % hub.VERSION, e.exception.headers['Location'])
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(self.url + '/dl/dc-agent-../../etc/passwd')
            self.assertEqual(e.exception.code, 404)
        finally:
            hub.AGENTS = old


if __name__ == '__main__':
    unittest.main()
