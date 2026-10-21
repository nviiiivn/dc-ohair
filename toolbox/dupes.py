"""Duplicates across every device and drive the hub knows.

Two kinds, both read from the scans the hub already has, so finding them
touches no device:

- Same bytes: files of exactly the same size. A size match is a lead, not
  proof, so each group says how sure it is, weakest first: "size" (only the
  size agrees), "name" (the name too), then, once the tools agent has read
  them (a few KB from the start and the end, then the whole file, the
  fclones/jdupes order), "partial" or "hash". Sizes that are a whole number
  of MiB (swap files, sleep images, disk chunks) agree by design and are
  marked round; files named by their own hash (ollama, docker and git blobs)
  are different files by definition and never grouped.
- Same film: different files of one movie or episode ("Heat (1995)" as a
  1080p WEBRip on one drive and a 2160p Remux on another), matched by title
  and year (or show and episode) from the names, and ranked by quality so
  the best copy is the one to keep.

The scans list files of the hub's min_size (50 MB) and up, which is where
the space is; smaller duplicates are not in a scan and not found here.
"""
import os, re
from . import media

SURE = ['size', 'name', 'partial', 'hash']     # weakest first
BLOB = re.compile(r'(?:^|[-_.])(?:sha256|sha1|md5|blake\w*)?[-_:]?[0-9a-f]{32,}(?:$|[-_.])', re.I)

MOVIES_I, SERIES_I = 3, 2


def files(city):
    """Every file in the scans: dict(device, device_name, drive, path, size, mtime, cat)."""
    out = []
    for dev in city.get('devices', []):
        for drive in dev.get('drives', []):
            tree = drive.get('tree')
            if not tree:
                continue
            base = drive.get('path', '/')
            stack = [(tree, base)]
            while stack:
                node, path = stack.pop()
                for kid in node.get('k', ()):
                    if kid.get('x'):
                        continue    # everything under the size cutoff, folded into one
                    kpath = path.rstrip('/') + '/' + kid['n']
                    if kid.get('t') == 'f':
                        c = kid.get('c') or [0] * 5
                        out.append({'device': dev['id'], 'device_name': dev.get('name', dev['id']),
                                    'drive': drive.get('name', ''), 'path': kpath, 'size': sum(c),
                                    'mtime': kid.get('m', 0), 'cat': max(range(5), key=lambda i: c[i])})
                    else:
                        stack.append((kid, kpath))
    return out


def same_size(all_files, min_size=0, hashes=None):
    """Groups of two or more files of one size, biggest waste first.

    hashes maps (device, path) to {'partial': hex, 'full': hex} where the
    tools agent has read them; a group is then split by what was read, and
    its 'sure' is the strongest evidence every member has.
    """
    hashes = hashes or {}
    by_size = {}
    for f in all_files:
        if f['size'] >= max(min_size, 1):
            by_size.setdefault(f['size'], []).append(f)
    groups = []
    for size, members in by_size.items():
        if len(members) < 2:
            continue
        for split, sure in _split(members, hashes):
            if sure == 'size':
                # Content-addressed: a blob named by its hash only matches a blob of the same name.
                count = {}
                for f in split:
                    count[os.path.basename(f['path'])] = count.get(os.path.basename(f['path']), 0) + 1
                split = [f for f in split if not BLOB.search(os.path.basename(f['path']))
                         or count[os.path.basename(f['path'])] > 1]
            if len(split) < 2:
                continue
            names = {os.path.basename(f['path']) for f in split}
            if sure == 'size' and len(names) == 1:
                sure = 'name'
            g = _group('bytes', size, split, sure)
            g['round'] = size % (1 << 20) == 0
            groups.append(g)
    groups.sort(key=lambda g: (-SURE.index(g['sure']), g['round'], -g['wasted']))
    return groups


def _split(members, hashes):
    """One size's files, split by full hash, else partial hash, where known."""
    def key(f):
        h = hashes.get((f['device'], f['path']), {})
        if h.get('full'):
            return ('hash', h['full'])
        if h.get('partial'):
            return ('partial', h['partial'])
        return ('size', '')
    if not any(key(f)[0] != 'size' for f in members):
        return [(members, 'size')]
    # Once anything is hashed, a member with no hash cannot be put with any
    # group, so it stands alone until it is read too.
    parts = {}
    for f in members:
        parts.setdefault(key(f) if key(f)[0] != 'size' else ('size', f['device'] + f['path']), []).append(f)
    return [(v, k[0]) for k, v in parts.items()]


def _group(kind, key, members, sure):
    members = sorted(members, key=lambda f: (f['device'], f['path']))
    size = sum(f['size'] for f in members)
    keep = max(f['size'] for f in members)
    return {'kind': kind, 'key': key, 'sure': sure, 'count': len(members), 'size': size,
            'wasted': size - keep, 'files': members}


def same_film(all_files):
    """Copies of one movie or episode, best first in each group, biggest waste first."""
    by_key = {}
    for f in all_files:
        if f['cat'] not in (MOVIES_I, SERIES_I):
            continue
        info = media.parse(f['path'])
        if not info or info['sample'] or not info['key']:
            continue
        by_key.setdefault(info['key'], []).append(dict(f, media=info, quality=media.label(info)))
    groups = []
    for key, members in by_key.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda f: media.score(f['media'], f['size']), reverse=True)
        best = members[0]
        for i, f in enumerate(members):
            f['rank'] = i + 1
            f['keep'] = i == 0
            f['same_bytes'] = i > 0 and f['size'] == best['size']
        info = best['media']
        title = info['title'] + (' (%d)' % info['year'] if info.get('year') else '')
        if info['kind'] == 'episode':
            title = '%s S%02dE%02d' % (info['title'], info['season'], info['episode'])
        size = sum(f['size'] for f in members)
        groups.append({'kind': 'film', 'key': key, 'title': title, 'film': info['kind'], 'count': len(members),
                       'size': size, 'wasted': size - best['size'], 'keep': best['quality'],
                       'files': [{k: v for k, v in f.items() if k != 'media'} for f in members]})
    groups.sort(key=lambda g: -g['wasted'])
    return groups


def report(city, min_size=0, hashes=None):
    """Everything the Duplicates tool shows: both kinds, and the totals."""
    all_files = files(city)
    bytes_ = same_size(all_files, min_size, hashes)
    films = same_film(all_files)
    return {'files': len(all_files), 'bytes': bytes_, 'films': films,
            'wasted_bytes': sum(g['wasted'] for g in bytes_),
            'wasted_films': sum(g['wasted'] for g in films)}


def candidates(groups):
    """(device, path, size) of every member of size-only groups: what the tools agent should hash next."""
    return [(f['device'], f['path'], f['size']) for g in groups if g['sure'] != 'hash' for f in g['files']]


def where(f):
    """'tower: 2TB Windows /mnt/windows/Movies/Heat (1995).mkv' for a list row."""
    return '%s: %s %s' % (f['device_name'], f['drive'], f['path'])

