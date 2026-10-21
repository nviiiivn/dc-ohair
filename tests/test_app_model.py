"""Tests for the phone app's data model (app/src/model.py)."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app', 'src'))
sys.path.insert(0, ROOT)

import demo  # noqa: E402
import model  # noqa: E402


class AppModelTest(unittest.TestCase):
    def setUp(self):
        self.city = demo.build()['city']
        self.top = model.build(self.city)

    def test_totals_add_up(self):
        self.assertEqual(self.top.s, sum(d.s for d in self.top.k))
        self.assertEqual([d.n for d in self.top.k], [d['name'] for d in self.city['devices']])

    def test_filter_and_order(self):
        only_movies = model.Filter([False, False, False, True, False])
        kids = model.kids(self.top, only_movies)
        sizes = [only_movies.size(k) for k in kids]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertTrue(all(s > 0 for s in sizes))

    def test_biggest_files_and_search(self):
        files = model.biggest_files(self.top, model.Filter())
        self.assertTrue(files and all(f.kind == 'file' for f in files))
        self.assertGreaterEqual(files[0].s, files[-1].s)
        hits = model.search(model.index(self.top), 'harbor')
        self.assertTrue(hits and all('harbor' in h.n.lower() for h in hits))

    def test_squarify_fills_the_box(self):
        rects = model.squarify([6, 6, 4, 3, 2, 2, 1, 0], 0, 0, 600, 400)
        self.assertIsNone(rects[-1])
        area = sum(w * h for _, _, w, h in rects[:-1])
        self.assertAlmostEqual(area, 600 * 400, delta=1)
        for x, y, w, h in rects[:-1]:
            self.assertTrue(-0.01 <= x and x + w <= 600.01 and -0.01 <= y and y + h <= 400.01)

    def test_fmt(self):
        self.assertEqual(model.fmt(1024 ** 4 * 3.5), '3.50T')
        self.assertEqual(model.fmt(5 * 1000 ** 3, 'decimal'), '5.0GB')


if __name__ == '__main__':
    unittest.main()
