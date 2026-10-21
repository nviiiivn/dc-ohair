"""The toolbox's small extras, from the scans: what grew this week, and the
biggest files nobody has touched in a long time.

What grew needs last week's sizes, which a scan does not keep, so after
every scan (and whenever the toolbox is opened) the size of every folder a
few levels down is kept in data/toolbox/sizes/<device>.jsonl, one line a day,
two months of them. Growth is today's sizes against the newest line that is
at least a week old, or the oldest there is while a week has not passed yet.
"""
import gzip, json, os, time

from . import dupes

DEPTH = 6              # folders this far below a drive are remembered
KEEP_DAYS = 62
DAY = 86400


def folder_sizes(city):
    """{device: {drive: {relative folder path: bytes}}} down to DEPTH."""
    out = {}
    for dev in city.get('devices', []):
        for drive in dev.get('drives', []):
            tree = drive.get('tree')
            if not tree:
                continue
            sizes = {}
            stack = [(tree, '', 0)]
            while stack:
                node, path, depth = stack.pop()
                sizes[path or '/'] = sum(node.get('c') or [0])
                if depth < DEPTH:
                    for kid in node.get('k', ()):
                        if kid.get('t') != 'f' and not kid.get('x'):
                            stack.append((kid, path + '/' + kid['n'], depth + 1))
            out.setdefault(dev['id'], {})[drive.get('name', '')] = sizes
    return out


def _folder(data_dir):
    return os.path.join(data_dir, 'toolbox', 'sizes')


def remember(city, data_dir, now=None):
    """Keep today's folder sizes (once a day per device), and drop lines older than KEEP_DAYS."""
    now = int(now or time.time())
    folder = _folder(data_dir)
    os.makedirs(folder, exist_ok=True)
    for dev_id, drives in folder_sizes(city).items():
        path = os.path.join(folder, dev_id + '.jsonl.gz')
        lines = _read(path)
        if lines and now - lines[-1]['t'] < DAY - 3600:
            continue
        lines = [l for l in lines if now - l['t'] <= KEEP_DAYS * DAY] + [{'t': now, 'drives': drives}]
        tmp = path + '.tmp'
        with gzip.open(tmp, 'wt') as f:
            for l in lines:
                f.write(json.dumps(l, separators=(',', ':')) + '\n')
        os.replace(tmp, path)


def _read(path):
    try:
        with gzip.open(path, 'rt') as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError, EOFError):
        return []


def growth(city, data_dir, now=None, min_bytes=1024 ** 3, limit=60):
    """The folders that grew most since about a week ago, and the files that landed this week."""
    now = int(now or time.time())
    names = {d['id']: d.get('name', d['id']) for d in city.get('devices', [])}
    roots = {(d['id'], dr.get('name', '')): dr.get('path', '/') for d in city.get('devices', []) for dr in d.get('drives', [])}
    since, grew = None, []
    for dev_id, drives in folder_sizes(city).items():
        lines = _read(os.path.join(_folder(data_dir), dev_id + '.jsonl.gz'))
        old = [l for l in lines if now - l['t'] >= 7 * DAY - 3600]
        base = old[-1] if old else (lines[0] if lines and now - lines[0]['t'] > 3600 else None)
        if not base:
            continue
        since = base['t'] if since is None else min(since, base['t'])
        for drive, sizes in drives.items():
            before = base['drives'].get(drive, {})
            delta = {p: s - before.get(p, 0) for p, s in sizes.items() if s - before.get(p, 0) >= min_bytes}
            for p, d in delta.items():
                # A folder whose growth is mostly one subfolder's is that subfolder's: show the deeper one.
                kids = [d2 for p2, d2 in delta.items() if p2 != p and p2.startswith(p.rstrip('/') + '/')
                        and p2.count('/') == p.count('/') + (p != '/')]
                if kids and max(kids) >= 0.8 * d:
                    continue
                root = roots.get((dev_id, drive), '/').rstrip('/')
                grew.append({'device': dev_id, 'device_name': names.get(dev_id, dev_id), 'drive': drive,
                             'folder': p, 'path': (root + p) if p != '/' else (root or '/'),
                             'grew': d, 'now': sizes[p], 'since': base['t']})
    grew.sort(key=lambda g: -g['grew'])
    landed = [f for f in dupes.files(city) if f['mtime'] and now - f['mtime'] <= 7 * DAY]
    landed.sort(key=lambda f: -f['size'])
    return {'since': since, 'grew': grew[:limit], 'landed': landed[:limit], 'landed_count': len(landed),
            'landed_bytes': sum(f['size'] for f in landed)}


def big_and_old(city, now=None, days=365, limit=200):
    """The biggest files not changed in [days]: often the safest space to win back."""
    now = int(now or time.time())
    old = [dict(f, age_days=int((now - f['mtime']) / DAY)) for f in dupes.files(city)
           if f['mtime'] and now - f['mtime'] >= days * DAY]
    old.sort(key=lambda f: -f['size'])
    return {'days': days, 'files': old[:limit], 'count': len(old), 'bytes': sum(f['size'] for f in old)}
