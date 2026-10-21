"""Tests for the toolbox's name parser and duplicate finder. Run: python3 -m unittest"""
import os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DC_DATA', tempfile.mkdtemp(prefix='dc-ohair-test-'))

import collect  # noqa: E402
import demo  # noqa: E402
import hub  # noqa: E402
from toolbox import api, dupes, media  # noqa: E402
from toolbox import demo as toolbox_demo  # noqa: E402

GB = 10 ** 9
MUSIC, GAMES, SERIES, MOVIES, OTHER = range(5)


def f(name, size, cat=MOVIES, m=1700000000):
    c = [0] * 5
    c[cat] = size
    return {'n': name, 't': 'f', 'c': c, 'f': 1, 'm': m}


def d(name, *kids):
    return {'n': name, 'c': [0] * 5, 'k': list(kids)}


def city(**devices):
    """city(nas={'Media': ('/mnt/media', tree)}) in the hub's /api/city shape."""
    return {'devices': [{'id': dev, 'name': dev.upper(),
                         'drives': [{'name': n, 'path': p, 'tree': t} for n, (p, t) in drives.items()]}
                        for dev, drives in devices.items()]}


class ParseTest(unittest.TestCase):
    def test_a_release_name_gives_title_year_and_quality(self):
        i = media.parse('/m/Heat.1995.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-GRP.mkv')
        self.assertEqual((i['kind'], i['title'], i['year']), ('movie', 'Heat', 1995))
        self.assertEqual(media.label(i), '2160p Remux HDR HEVC Atmos')

    def test_the_last_year_is_the_release_year(self):
        self.assertEqual(media.parse('/m/2001 A Space Odyssey (1968).mkv')['title'], '2001 A Space Odyssey')
        self.assertEqual(media.parse('/m/Blade Runner 2049 (2017) [2160p]/x.mkv')['key'], 'movie:blade runner 2049:2017')

    def test_a_bare_file_takes_its_folders_name(self):
        i = media.parse('/m/Heat (1995)/movie.mkv')
        self.assertEqual(i['key'], 'movie:heat:1995')

    def test_episodes_are_keyed_by_show_season_and_episode(self):
        i = media.parse('/tv/The Office/Season 2/The.Office.S02E03.720p.HDTV.x264.mkv')
        self.assertEqual((i['kind'], i['season'], i['episode']), ('episode', 2, 3))
        self.assertEqual(i['key'], 'episode:office:s02e03')
        self.assertEqual(media.parse('/tv/Show/Season 1/S01E02.mkv')['title'], 'Show')

    def test_a_screen_size_is_not_an_episode(self):
        self.assertEqual(media.parse('/m/Heat 1995 1920x1080.mkv')['kind'], 'movie')

    def test_samples_are_marked_and_non_video_is_none(self):
        self.assertTrue(media.parse('/m/Heat (1995)/Sample/heat.sample.mkv')['sample'])
        self.assertIsNone(media.parse('/m/Heat (1995)/heat.nfo'))

    def test_titles_match_across_punctuation_and_the(self):
        self.assertEqual(media.normal_title("The Lord of the Rings: The Return of the King"),
                         media.normal_title('Lord.of.the.Rings.The.Return.of.the.King'.replace('.', ' ')))
        self.assertEqual(media.normal_title("Carlito's Way"), media.normal_title('Carlitos Way'))

    def test_quality_ranks_resolution_then_source_then_size(self):
        uhd = media.parse('/m/Heat.1995.2160p.WEBRip.mkv')
        remux = media.parse('/m/Heat.1995.1080p.BluRay.REMUX.mkv')
        web = media.parse('/m/Heat.1995.1080p.WEB-DL.mkv')
        self.assertGreater(media.score(uhd), media.score(remux))
        self.assertGreater(media.score(remux), media.score(web))
        self.assertGreater(media.score(web, 5 * GB), media.score(web, 3 * GB))

    def test_an_untagged_big_copy_beats_a_tagged_small_one(self):
        bare = media.parse('/m/Heat (1995).mkv')
        small = media.parse('/m/Heat.1995.720p.WEBRip.x264.mkv')
        self.assertGreater(media.score(bare, 20 * GB), media.score(small, 2 * GB))
        self.assertEqual(media.label(bare), 'unknown quality')


class DupesTest(unittest.TestCase):
    def setUp(self):
        self.city = city(
            nas={'Media': ('/mnt/media', d('Media', d('Movies',
                                         d('Heat (1995)', f('Heat.1995.1080p.WEBRip.x264.mkv', 3 * GB)),
                                         f('Gladiator (2000).mkv', 5 * GB),
                                         f('swap.img', 1 << 30, OTHER),
                                         f('backup-2026-06-18.tar.gz', 4 * GB + 17, OTHER))))},
            pc={'Big': ('/', d('Big', d('home',
                               d('Movies', f('Heat.1995.2160p.BluRay.REMUX.HDR.mkv', 40 * GB),
                                 f('Gladiator.2000.EXTENDED.2160p.BluRay.x265.mkv', 9 * GB),
                                 d('Heat (1995)', d('Sample', f('heat.sample.mkv', 3 * GB)))),
                               f('backup-2026-06-18.tar.gz', 4 * GB + 17, OTHER),
                               f('sleepimage', 1 << 30, OTHER),
                               f('sha256-6150cb3aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 4 * GB + 17, OTHER),
                               f('sha256-91caa17bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 7 * GB, OTHER),
                               f('sha256-4c27e0fccccccccccccccccccccccccccccccccccccccccccccccccccccc', 7 * GB, OTHER))))})

    def test_every_file_in_the_scans_with_its_full_path(self):
        paths = {x['path'] for x in dupes.files(self.city)}
        self.assertIn('/mnt/media/Movies/Heat (1995)/Heat.1995.1080p.WEBRip.x264.mkv', paths)
        self.assertIn('/home/Movies/Heat.1995.2160p.BluRay.REMUX.HDR.mkv', paths)

    def test_same_film_keeps_the_best_copy_first(self):
        films = {g['title']: g for g in dupes.same_film(dupes.files(self.city))}
        heat = films['Heat (1995)']
        self.assertEqual(heat['count'], 2)                      # the sample is not a copy
        self.assertTrue(heat['files'][0]['keep'])
        self.assertIn('2160p Remux', heat['files'][0]['quality'])
        self.assertEqual(heat['wasted'], 3 * GB)
        self.assertEqual(films['Gladiator (2000)']['files'][0]['device'], 'pc')

    def test_same_size_with_the_same_name_is_surer_than_size_alone(self):
        groups = dupes.same_size(dupes.files(self.city), min_size=GB)
        backup = next(g for g in groups if g['files'][0]['path'].endswith('.tar.gz'))
        self.assertEqual(backup['sure'], 'name')
        self.assertEqual(groups[0], backup)                      # the surest group comes first

    def test_round_sizes_are_marked_and_hash_named_blobs_never_grouped(self):
        groups = dupes.same_size(dupes.files(self.city), min_size=GB)
        swap = next(g for g in groups if g['key'] == 1 << 30)
        self.assertTrue(swap['round'])
        self.assertFalse(any(all('sha256-' in x['path'] for x in g['files']) for g in groups))

    def test_hashes_split_a_size_group_and_raise_its_certainty(self):
        files = dupes.files(self.city)
        a, b = '/mnt/media/Movies/backup-2026-06-18.tar.gz', '/home/backup-2026-06-18.tar.gz'
        same = {('nas', a): {'partial': 'p1', 'full': 'h1'}, ('pc', b): {'partial': 'p1', 'full': 'h1'}}
        g = next(g for g in dupes.same_size(files, GB, same) if g['files'][0]['path'].endswith('.tar.gz'))
        self.assertEqual(g['sure'], 'hash')
        differ = {('nas', a): {'partial': 'p1'}, ('pc', b): {'partial': 'p2'}}
        self.assertFalse([g for g in dupes.same_size(files, GB, differ) if g['files'][0]['path'].endswith('.tar.gz')])

    def test_the_report_totals_both_kinds(self):
        r = dupes.report(self.city, min_size=GB)
        self.assertEqual(r['wasted_films'], sum(g['wasted'] for g in r['films']))
        self.assertEqual(r['files'], 12)


def off_totals(tree):
    """How many folders' totals are not the sum of what is in them."""
    bad, stack = 0, [tree]
    while stack:
        n = stack.pop()
        kids = n.get('k') or []
        if kids:
            bad += [sum(k['c'][i] for k in kids) for i in range(5)] != n['c']
            stack.extend(kids)
    return bad


class ApiTest(unittest.TestCase):
    def test_the_demo_gets_duplicates_and_keeps_its_totals_right(self):
        d = demo.build()
        before = [off_totals(dr['tree']) for dev in d['city']['devices'] for dr in dev['drives']]
        r = toolbox_demo.plant(d['city'])
        after = [off_totals(dr['tree']) for dev in d['city']['devices'] for dr in dev['drives']]
        self.assertEqual(before, after)
        self.assertGreaterEqual(len(r['films']), 9)
        self.assertTrue(any(g['sure'] == 'name' for g in r['bytes']))

    def test_the_api_sends_the_report_through_the_hubs_handler(self):
        d = demo.build()
        toolbox_demo.plant(d['city'])

        class Request:
            hub = hub.Hub(collect.validate(d['config']), demo=d)
            sent = {}

            def send_json(self, obj, code=200):
                self.sent.update(obj=obj, code=code)

        req = Request()
        api.get(req, '/api/tools/dupes', 'min=0')
        self.assertEqual(req.sent['code'], 200)
        self.assertEqual(req.sent['obj']['groups_films'], len(req.sent['obj']['films']))
        api.get(req, '/api/tools/nothing', '')
        self.assertEqual(req.sent['code'], 404)


if __name__ == '__main__':
    unittest.main()
