"""Tests for the toolbox's extras: what grew, and big old files. Run: python3 -m unittest"""
import copy, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from toolbox import extras  # noqa: E402

GB = 1024 ** 3
DAY = 86400
NOW = 1_800_000_000


def f(name, size, m):
    return {'n': name, 't': 'f', 'c': [0, 0, 0, size, 0], 'f': 1, 'm': m}


def d(name, *kids):
    return {'n': name, 'c': [sum(k['c'][i] for k in kids) for i in range(5)], 'k': list(kids)}


def city(tree):
    return {'devices': [{'id': 'nas', 'name': 'NAS', 'drives': [{'name': 'Media', 'path': '/mnt/media', 'tree': tree}]}]}


class ExtrasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.before = city(d('Media', d('Movies', d('New', f('a.mkv', 2 * GB, NOW - 400 * DAY))),
                             d('Backups', f('old.tar', 30 * GB, NOW - 900 * DAY))))
        self.after = city(d('Media', d('Movies', d('New', f('a.mkv', 2 * GB, NOW - 400 * DAY),
                                                   f('b.mkv', 9 * GB, NOW - 2 * DAY))),
                            d('Backups', f('old.tar', 30 * GB, NOW - 900 * DAY))))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sizes_are_kept_once_a_day_and_pruned_after_two_months(self):
        extras.remember(self.before, self.tmp.name, NOW - 70 * DAY)
        extras.remember(self.before, self.tmp.name, NOW - 8 * DAY)
        extras.remember(self.before, self.tmp.name, NOW - 8 * DAY + 600)     # the same day: not again
        extras.remember(self.after, self.tmp.name, NOW)
        lines = extras._read(os.path.join(self.tmp.name, 'toolbox', 'sizes', 'nas.jsonl.gz'))
        self.assertEqual([l['t'] for l in lines], [NOW - 8 * DAY, NOW])

    def test_growth_names_the_deepest_folder_that_grew(self):
        extras.remember(self.before, self.tmp.name, NOW - 8 * DAY)
        g = extras.growth(self.after, self.tmp.name, NOW)
        self.assertEqual([(x['folder'], x['grew']) for x in g['grew']], [('/Movies/New', 9 * GB)])
        self.assertEqual(g['since'], NOW - 8 * DAY)
        self.assertEqual([x['path'] for x in g['landed']], ['/mnt/media/Movies/New/b.mkv'])

    def test_no_growth_before_there_is_anything_to_compare(self):
        self.assertEqual(extras.growth(self.after, self.tmp.name, NOW)['grew'], [])

    def test_big_and_old_is_biggest_first_and_only_old(self):
        b = extras.big_and_old(self.after, NOW, days=365)
        self.assertEqual([x['path'].split('/')[-1] for x in b['files']], ['old.tar', 'a.mkv'])
        self.assertEqual(b['files'][0]['age_days'], 900)


if __name__ == '__main__':
    unittest.main()
