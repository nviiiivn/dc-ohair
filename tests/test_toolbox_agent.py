"""Tests for the toolbox's tools agent and the duplicate check job. Run: python3 -m unittest"""
import base64, json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DC_DATA', tempfile.mkdtemp(prefix='dc-ohair-test-'))

import collect  # noqa: E402
from toolbox import dupes, jobs  # noqa: E402

AGENT = os.path.join(ROOT, 'toolbox', 'tools_agent.py')


def run(**spec):
    arg = base64.b64encode(json.dumps(spec).encode()).decode()
    return json.loads(subprocess.run([sys.executable, AGENT, arg], stdout=subprocess.PIPE, check=True).stdout)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    return path


class ToolsAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        body = os.urandom(300_000)
        self.a = write(os.path.join(d, 'one', 'film.mkv'), body)
        self.b = write(os.path.join(d, 'two', 'film copy.mkv'), body)
        self.c = write(os.path.join(d, 'two', 'other.mkv'), body[:150_000] + b'x' + body[150_001:])   # same size, one byte off
        os.makedirs(os.path.join(d, 'empty', 'deeper'))
        os.symlink(os.path.join(d, 'gone'), os.path.join(d, 'two', 'dangling'))
        os.link(self.a, os.path.join(d, 'one', 'hardlink.mkv'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_matches_on_start_and_end_full_tells_them_apart(self):
        files = [[p, 300_000] for p in (self.a, self.b, self.c)]
        part = run(mode='hash', files=files)['hashes']
        self.assertEqual(part[self.a]['partial'], part[self.b]['partial'])
        self.assertEqual(part[self.a]['partial'], part[self.c]['partial'])     # the change is in the middle
        whole = run(mode='hash', files=files, full=True)['hashes']
        self.assertEqual(whole[self.a]['full'], whole[self.b]['full'])
        self.assertNotEqual(whole[self.a]['full'], whole[self.c]['full'])

    def test_a_file_that_changed_size_is_skipped_and_the_budget_holds(self):
        out = run(mode='hash', files=[[self.a, 123], [self.b, 300_000]], full=True, budget=1000)['hashes']
        self.assertEqual(out[self.a]['skip'], 'changed since the scan')
        self.assertTrue(out[self.b]['full_later'])

    def test_a_hard_link_reads_as_the_same_inode(self):
        link = os.path.join(self.tmp.name, 'one', 'hardlink.mkv')
        out = run(mode='hash', files=[[self.a, 300_000], [link, 300_000]])['hashes']
        self.assertEqual(out[self.a]['dev_ino'], out[link]['dev_ino'])

    def test_empty_folders_and_broken_links(self):
        d = self.tmp.name
        self.assertEqual(run(mode='empty', roots=[d])['empty'], [os.path.join(d, 'empty', 'deeper')])
        broken = run(mode='broken', roots=[d])['broken']
        self.assertEqual([b[0] for b in broken], [os.path.join(d, 'two', 'dangling')])

    def test_it_writes_nothing(self):
        before = sorted((p, os.stat(os.path.join(p, f)).st_mtime) for p, _, fs in os.walk(self.tmp.name) for f in fs
                        if not os.path.islink(os.path.join(p, f)))
        run(mode='hash', files=[[self.a, 300_000]], full=True)
        after = sorted((p, os.stat(os.path.join(p, f)).st_mtime) for p, _, fs in os.walk(self.tmp.name) for f in fs
                       if not os.path.islink(os.path.join(p, f)))
        self.assertEqual(before, after)


class CheckJobTest(unittest.TestCase):
    def test_the_check_reads_each_set_on_its_device_and_proves_or_splits_it(self):
        with tempfile.TemporaryDirectory() as d:
            body = os.urandom(200_000)
            write(os.path.join(d, 'a', 'x.bin'), body)
            write(os.path.join(d, 'b', 'x.bin'), body)
            write(os.path.join(d, 'b', 'y.bin'), os.urandom(200_000))
            node = lambda n: {'n': n, 't': 'f', 'c': [0, 0, 0, 0, 200_000], 'm': 0}
            tree = {'n': 'D', 'c': [0] * 5, 'k': [{'n': 'a', 'c': [0] * 5, 'k': [node('x.bin')]},
                                              {'n': 'b', 'c': [0] * 5, 'k': [node('x.bin'), node('y.bin')]}]}
            city = {'devices': [{'id': 'here', 'name': 'Here', 'drives': [{'name': 'D', 'path': d, 'tree': tree}]}]}

            class Hub:
                demo = None
                cfg = collect.validate({'devices': [{'id': 'here', 'name': 'Here', 'local': True,
                                                     'drives': [{'name': 'D', 'path': d}]}]})

                def city(self):
                    return city

            before = dupes.same_size(dupes.files(city))
            self.assertEqual([(g['sure'], g['count']) for g in before], [('size', 3)])
            jobs._check(Hub(), full=True)
            self.assertFalse(jobs.status()['running'])
            self.assertEqual(jobs.status()['devices']['here']['state'], 'done')
            after = dupes.same_size(dupes.files(city), 0, jobs.store_hashes(jobs.load_store()))
            self.assertEqual([(g['sure'], g['count']) for g in after], [('hash', 2)])
            self.assertTrue(all(f['path'].endswith('x.bin') for f in after[0]['files']))


if __name__ == '__main__':
    unittest.main()
