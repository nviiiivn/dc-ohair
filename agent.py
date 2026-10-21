"""D.C. O'HAIR agent: walks drive roots read-only and prints one JSON document.

Runs on any device with python3 (3.6+); nothing to install:
    ssh host python3 - <base64 spec> < agent.py
    python3 agent.py <base64 spec>

spec = {"drives": [{"name": "Media", "path": "/mnt/media", "exclude": ["*/.Trash*"]}],
        "min": bytes, "kids": n, "depth": n, "categories": {...}}
spec = {"mode": "mounts"}   lists the real filesystems instead of scanning
spec = {"mode": "check", "paths": [...]}   reports python version and path access

Each node is {"n": name, "c": [music, games, series, movies, other], "f": files,
"m": newest mtime, "k": [children]}; files get "t": "f", and everything below
the size cutoff is folded into one child per folder with "x": 1.
"""
import base64, fnmatch, json, os, re, shutil, socket, stat, subprocess, sys, time

DEFAULT_CATEGORIES = {
    'music': ['.flac', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.alac', '.aiff', '.aif', '.wma', '.ape', '.dsf'],
    'video': ['.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.wmv', '.webm', '.mpg', '.mpeg', '.m2ts', '.vob'],
    'series': [r'[Ss]\d{1,2}[Ee]\d{1,3}', r'season[ ._-]?\d', '/series/', '/tv/', '/shows/'],
    'games': ['/steamapps/', '/steam/', '/games/', '/heroic/', '/lutris/', 'gog games', 'epic games',
              '/steamlibrary/', '/proton', '/itch/', '/emulat', '/roms?/'],
    'big_video': 104857600,  # videos smaller than this (100 MiB) count as Other
}
MUSIC_I, GAMES_I, SERIES_I, MOVIES_I, OTHER_I = range(5)

spec = json.loads(base64.b64decode(sys.argv[1]).decode()) if len(sys.argv) > 1 else {}
rules = dict(DEFAULT_CATEGORIES, **{k: v for k, v in (spec.get('categories') or {}).items() if v})
MUSIC = set(e.lower() for e in rules['music'])
VIDEO = set(e.lower() for e in rules['video'])
SERIES = re.compile('|'.join(rules['series']), re.I)
GAMES = re.compile('|'.join(rules['games']), re.I)
BIG_VIDEO = int(rules['big_video'])
MIN = spec.get('min', 50 * 1024 * 1024)
KIDS = spec.get('kids', 5000)
DEPTH = spec.get('depth', 12)
seen = set()
errors = [0]


def add(a, b):
    for i in range(5):
        a[i] += b[i]


def classify(dirpath, dir_games, name, size):
    if dir_games or GAMES.search('/' + name.lower()):
        return GAMES_I
    ext = os.path.splitext(name)[1].lower()
    if ext in MUSIC:
        return MUSIC_I
    if ext in VIDEO and size >= BIG_VIDEO:
        return SERIES_I if SERIES.search(dirpath + '/' + name) else MOVIES_I
    return OTHER_I


def mount_sources():
    """mount point -> (source device, fs type), from /proc/self/mountinfo; None off Linux."""
    out = {}
    try:
        with open('/proc/self/mountinfo') as f:
            for line in f:
                left, right = line.split(' - ', 1)
                fields = left.split()
                mp = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields[4])
                fstype, source = right.split()[:2]
                out[mp] = (source, fstype, 'ro' in fields[5].split(','))
    except (OSError, ValueError, IndexError):
        return None
    return out


MOUNTS = mount_sources()


def root_source(root):
    best = ''
    for mp in MOUNTS:
        if (root == mp or root.startswith(mp.rstrip('/') + '/')) and len(mp) > len(best):
            best = mp
    return MOUNTS[best][0] if best in MOUNTS else None


def other_fs(path, st, dev, src):
    # btrfs subvolumes each get their own st_dev, so on Linux only a mount
    # point backed by a different source counts as another filesystem
    if os.name == 'nt':
        return False  # scandir reports st_dev as 0 on Windows; each drive letter is scanned on its own
    if MOUNTS is None:
        return st.st_dev != dev
    return path in MOUNTS and MOUNTS[path][0] != src


def excluded(path, name, patterns):
    return any(path == p or fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def scan(path, name, dev, src, exclude, depth):
    """Returns the pruned node for one folder, totals always complete."""
    cats = [0] * 5
    nfiles = 0
    newest = 0
    kids = []
    loose = [0] * 5
    loose_n = 0
    loose_m = 0
    slashed = path.replace('\\', '/')
    dir_games = bool(GAMES.search(slashed.lower() + '/'))
    try:
        it = os.scandir(path)
    except OSError:
        errors[0] += 1
        return {'n': name, 'c': cats, 'f': 0, 'm': 0}
    with it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                errors[0] += 1
                continue
            if stat.S_ISDIR(st.st_mode):
                if e.name == '.snapshots' or excluded(e.path, e.name, exclude) or other_fs(e.path, st, dev, src):
                    continue
                kid = scan(e.path, e.name, dev, src, exclude, depth + 1)
                add(cats, kid['c'])
                nfiles += kid['f']
                newest = max(newest, kid['m'])
                kids.append(kid)
            elif stat.S_ISREG(st.st_mode):
                if exclude and excluded(e.path, e.name, exclude):
                    continue
                if st.st_nlink > 1:
                    k = (st.st_dev, st.st_ino)
                    if k in seen:
                        continue
                    seen.add(k)
                size = getattr(st, 'st_blocks', 0) * 512 or st.st_size
                ci = classify(slashed, dir_games, e.name, st.st_size)
                cats[ci] += size
                nfiles += 1
                mtime = int(st.st_mtime)
                newest = max(newest, mtime)
                if size >= MIN:
                    c = [0] * 5
                    c[ci] = size
                    kids.append({'n': e.name, 'c': c, 'f': 1, 't': 'f', 'm': mtime})
                else:
                    loose[ci] += size
                    loose_n += 1
                    loose_m = max(loose_m, mtime)
    node = {'n': name, 'c': cats, 'f': nfiles, 'm': newest}
    if depth >= DEPTH:
        return node
    # every file and folder of MIN or more stays its own node; only the
    # small stuff is folded into one "…" node per folder
    kids.sort(key=lambda k: -sum(k['c']))
    keep = []
    for k in kids:
        if len(keep) < KIDS and sum(k['c']) >= MIN:
            keep.append(k)
        else:
            add(loose, k['c'])
            loose_n += k['f']
            loose_m = max(loose_m, k['m'])
    if keep:
        if sum(loose):
            keep.append({'n': '…', 'c': loose, 'f': loose_n, 'x': 1, 'm': loose_m})
        node['k'] = keep
    return node


def fs_usage(path):
    if not hasattr(os, 'statvfs'):  # Windows
        try:
            du = shutil.disk_usage(path)
        except OSError:
            return None
        return {'total': du.total, 'free': du.free, 'used': du.used}
    try:
        v = os.statvfs(path)
    except OSError:
        return None
    return {'total': v.f_blocks * v.f_frsize,
            'free': v.f_bavail * v.f_frsize,
            'used': (v.f_blocks - v.f_bfree) * v.f_frsize}


REAL_FS = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'ntfs', 'ntfs3', 'fuseblk', 'exfat', 'vfat', 'f2fs', 'jfs',
           'bcachefs', 'hfsplus', 'apfs', 'hfs', 'msdos', 'nfs', 'nfs4', 'cifs', 'smb3', 'smbfs'}
SYSTEM_PREFIXES = ('/boot', '/snap', '/var/lib/docker', '/var/lib/containers', '/run/user', '/proc', '/sys', '/dev',
                   '/System/Volumes/VM', '/System/Volumes/Preboot', '/System/Volumes/Update', '/System/Volumes/xarts',
                   '/System/Volumes/iSCPreboot', '/System/Volumes/Hardware', '/private/var/vm')


def list_mounts():
    """Real filesystems worth scanning, one per device (btrfs subvolumes collapse)."""
    found = []
    if os.name == 'nt':
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            if os.path.exists(letter + ':\\'):
                found.append((letter + ':\\', letter + ':', 'ntfs', False))
    elif MOUNTS is not None:
        for mp, (source, fstype, ro) in MOUNTS.items():
            found.append((mp, source, fstype, ro))
    else:
        try:
            text = subprocess.run(['mount'], stdout=subprocess.PIPE, universal_newlines=True).stdout
        except OSError:
            text = ''
        for line in text.splitlines():
            m = re.match(r'^(\S+) on (.+) \(([^,)]+)(.*)\)$', line)
            if m:
                found.append((m.group(2), m.group(1), m.group(3), 'read-only' in m.group(4)))
    # in Docker the host is mounted at DC_HOSTFS; only its mounts are the real drives
    hostfs = os.environ.get('DC_HOSTFS')
    out, by_source = [], {}
    for mp, source, fstype, ro in found:
        inner = mp
        if hostfs:
            if not (mp == hostfs or mp.startswith(hostfs + '/')):
                continue
            inner = mp[len(hostfs):] or '/'
        if fstype not in REAL_FS or inner.startswith(SYSTEM_PREFIXES) or not os.path.isdir(mp):
            continue
        if sys.platform == 'darwin' and not (mp == '/System/Volumes/Data' or mp.startswith('/Volumes/')):
            continue
        usage = fs_usage(mp)
        if not usage or usage['total'] < 1024 ** 3:
            continue
        item = dict(usage, path=mp, fs=fstype, source=source, network=fstype in ('nfs', 'nfs4', 'cifs', 'smb3', 'smbfs'),
                    readonly=ro, label=os.path.basename(mp.rstrip('/')) or mp)
        prev = by_source.get(source)
        if prev is None or len(mp) < len(prev['path']):
            if prev:
                out.remove(prev)
            by_source[source] = item
            out.append(item)
    return sorted(out, key=lambda d: d['path'])


def check(paths):
    out = {'python': sys.version.split()[0], 'host': socket.gethostname(), 'platform': sys.platform, 'paths': []}
    for p in paths:
        out['paths'].append({'path': p, 'exists': os.path.exists(p), 'readable': os.access(p, os.R_OK | os.X_OK)})
    return out


def main():
    sys.setrecursionlimit(20000)
    if spec.get('mode') == 'mounts':
        sys.stdout.write(json.dumps({'host': socket.gethostname(), 'mounts': list_mounts()}))
        return
    if spec.get('mode') == 'check':
        sys.stdout.write(json.dumps(check(spec.get('paths', []))))
        return
    out = {'agent': 2, 'host': socket.gethostname(), 'drives': []}
    t0 = time.time()
    for d in spec.get('drives', []):
        drive = {'name': d['name'], 'path': d['path']}
        root = os.path.realpath(d['path'])
        try:
            st = os.lstat(root)
        except OSError as e:
            drive['error'] = 'not found: %s' % e.strerror
            out['drives'].append(drive)
            continue
        if d.get('mounted') and not os.path.ismount(root):
            drive['error'] = 'not mounted'
            out['drives'].append(drive)
            continue
        errors[0] = 0
        t1 = time.time()
        src = root_source(root) if MOUNTS else None
        exclude = list(d.get('exclude') or []) + list(d.get('skip') or [])
        tree = scan(root, d['name'], st.st_dev, src, exclude, 0)
        drive.update(fs=fs_usage(root), tree=tree, errors=errors[0], secs=round(time.time() - t1, 1))
        out['drives'].append(drive)
    out['secs'] = round(time.time() - t0, 1)
    out['scanned'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    sys.stdout.write(json.dumps(out, separators=(',', ':')))


main()
