"""Duplicates for `hub.py --demo`, so the toolbox has something to show there
and in the screenshots: a few of the demo's movies copied to other drives at
worse qualities, and two exact backups. Planted into the made-up city once,
at start, with every folder's totals kept right.
"""
import random

from . import dupes

GiB = 1024 ** 3
MOVIES = 3
WORSE = ['720p.WEBRip.x264', '1080p.WEB-DL.x264', '480p.DVDRip.XviD']


def _movie_nodes(tree, trail=()):
    for kid in tree.get('k', ()):
        if kid.get('t') == 'f' and kid['n'].endswith('.mkv') and kid['c'][MOVIES]:
            yield trail, kid
        elif kid.get('k'):
            yield from _movie_nodes(kid, trail + (kid,))


def _add(tree, folders, node):
    """Put [node] at tree/folders..., making the folders, adding its size all the way up."""
    chain, here = [tree], tree
    for name in folders:
        nxt = next((k for k in here.setdefault('k', []) if k['n'] == name and k.get('t') != 'f'), None)
        if nxt is None:
            nxt = {'n': name, 'c': [0] * 5, 'f': 0, 'm': node['m'], 'k': []}
            here['k'].append(nxt)
        chain.append(nxt)
        here = nxt
    here.setdefault('k', []).append(node)
    for n in chain:
        n['c'] = [a + b for a, b in zip(n['c'], node['c'])]
        n['f'] = n.get('f', 0) + 1
        n['m'] = max(n.get('m', 0), node['m'])


def plant(city, seed=11):
    r = random.Random(seed)
    drives = [(dev, dr) for dev in city['devices'] for dr in dev['drives'] if dr.get('tree')]
    found = [(dev, dr, trail, n) for dev, dr in drives for trail, n in _movie_nodes(dr['tree'])]
    if len(found) < 12 or len(drives) < 2:
        return
    found.sort(key=lambda x: -sum(x[3]['c']))
    for i, (dev, dr, trail, n) in enumerate(found[:9]):
        others = [x for x in drives if x[1] is not dr]
        odev, odr = others[i % len(others)]
        title = n['n'][:-4]
        size = int(sum(n['c']) * r.uniform(0.2, 0.55))
        c = [0] * 5
        c[MOVIES] = size
        name = '%s.%s.mkv' % (title.replace(' ', '.').replace('(', '').replace(')', ''), WORSE[i % len(WORSE)])
        _add(odr['tree'], ['Movies (old)', title], {'n': name, 't': 'f', 'c': c, 'f': 1, 'm': n['m'] - 86400 * 400})
    for dev, dr, trail, n in found[9:11]:
        odev, odr = next(x for x in drives if x[1] is not dr)
        _add(odr['tree'], ['Backup', 'Movies'], dict(n))
    return dupes.report(city)
