"""Made-up devices for `hub.py --demo` and the README screenshots.

Everything is generated from a fixed seed, so the city looks the same on
every run. Shapes follow what real scans look like: a flat folder of movie
files, shows split into seasons, albums under artists, games, and system
folders full of small files.
"""
import random, time

GiB = 1024 ** 3
TiB = 1024 ** 4
WORDS_A = ['Quiet', 'Paper', 'Iron', 'Velvet', 'Hollow', 'Neon', 'Silver', 'Broken', 'Midnight', 'Northern', 'Glass',
           'Crimson', 'Last', 'Lucky', 'Silent', 'Golden', 'Wild', 'Distant', 'Electric', 'Secret', 'Fading', 'Lost']
WORDS_B = ['Harbor', 'Moons', 'Garden', 'Signal', 'Orchard', 'Station', 'Kingdom', 'River', 'Comet', 'Lantern', 'Summer',
           'Engine', 'Archive', 'Island', 'Machine', 'Highway', 'Forest', 'Echo', 'Parade', 'Circuit', 'Tide', 'Atlas']
MUSIC, GAMES, SERIES, MOVIES, OTHER = range(5)


class Gen:
    def __init__(self, seed=7):
        self.r = random.Random(seed)
        self.now = int(time.time())

    def title(self):
        return '%s %s' % (self.r.choice(WORDS_A), self.r.choice(WORDS_B))

    def age(self, days=900):
        return self.now - self.r.randint(0, days * 86400)

    def file(self, name, cat, size):
        c = [0] * 5
        c[cat] = int(size)
        return {'n': name, 'c': c, 'f': 1, 't': 'f', 'm': self.age()}

    def folder(self, name, kids, small=None):
        """small: (cat, bytes, count) of files below the size cutoff, folded into '…'."""
        kids = [k for k in kids if k]
        if small and small[1]:
            c = [0] * 5
            c[small[0]] = int(small[1])
            kids.append({'n': '…', 'c': c, 'f': small[2], 'x': 1, 'm': self.age()})
        c = [sum(k['c'][i] for k in kids) for i in range(5)]
        kids.sort(key=lambda k: -sum(k['c']))
        return {'n': name, 'c': c, 'f': sum(k['f'] for k in kids), 'm': max([k['m'] for k in kids] or [0]), 'k': kids}

    def movies(self, n_files, n_folders):
        r = self.r
        kids = [self.file('%s (%d).mkv' % (self.title(), r.randint(1968, 2025)), MOVIES, r.uniform(1.2, 28) * GiB)
                for _ in range(n_files)]
        for _ in range(n_folders):
            name = '%s (%d)' % (self.title(), r.randint(1968, 2025))
            kids.append(self.folder(name, [self.file(name + '.mkv', MOVIES, r.uniform(4, 60) * GiB)],
                                    (OTHER, r.uniform(1, 90) * 1024 ** 2, r.randint(2, 9))))
        return kids

    def shows(self, n):
        r = self.r
        out = []
        for _ in range(n):
            show = self.title()
            seasons = []
            for s in range(1, r.randint(1, 7) + 1):
                eps = [self.file('%s S%02dE%02d.mkv' % (show, s, e), SERIES, r.uniform(0.35, 4.5) * GiB)
                       for e in range(1, r.randint(6, 13) + 1)]
                seasons.append(self.folder('Season %d' % s, eps, (OTHER, r.uniform(1, 30) * 1024 ** 2, len(eps))))
            out.append(self.folder(show, seasons))
        return out

    def music(self, n_artists):
        r = self.r
        artists = []
        for _ in range(n_artists):
            albums = [self.folder(self.title(), [], (MUSIC, r.uniform(60, 1400) * 1024 ** 2, r.randint(8, 16)))
                      for _ in range(r.randint(1, 6))]
            albums = [a for a in albums if sum(a['c']) >= 50 * 1024 ** 2]
            artists.append(self.folder('%s %s' % (r.choice(WORDS_A), r.choice(WORDS_B) + 's'), albums))
        return artists

    def games(self, n):
        r = self.r
        return [self.folder(self.title(), [self.file('data%d.pak' % i, GAMES, r.uniform(0.3, 9) * GiB)
                                           for i in range(r.randint(1, 6))], (GAMES, r.uniform(0.2, 4) * GiB, r.randint(200, 9000)))
                for _ in range(n)]

    def system(self, name, size_gib, extra=()):
        r = self.r
        parts = [('usr', 0.12), ('var', 0.18), ('opt', 0.04)]
        kids = [self.folder(p, [self.folder(self.title().lower().replace(' ', '-'), [], (OTHER, size_gib * share / 4 * GiB, r.randint(500, 20000)))
                                for _ in range(4)]) for p, share in parts]
        home = self.folder('home', [self.folder('me', list(extra) + [
            self.folder('Downloads', [self.file('%s.iso' % self.title().lower().replace(' ', '-'), OTHER, r.uniform(1, 6) * GiB) for _ in range(5)]),
            self.folder('Documents', [], (OTHER, size_gib * 0.05 * GiB, 4000)),
            self.folder('.cache', [], (OTHER, size_gib * 0.08 * GiB, 30000))])])
        return self.folder(name, kids + [home], (OTHER, size_gib * 0.03 * GiB, 900))


def drive(name, path, tree, total):
    used = sum(tree['c'])
    total = max(total, used / 0.96)
    tree['n'] = name
    return {'name': name, 'path': path, 'source': 'live', 'when': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'fs': {'total': int(total), 'used': used, 'free': max(0, int(total) - used)}, 'tree': tree}


def build():
    g = Gen()
    r = g.r
    media1 = g.folder('Media 1', [g.folder('Series', g.shows(26)), g.folder('Music', g.music(40))])
    media2 = g.folder('Media 2', [g.folder('Movies', g.movies(420, 40))])
    archive = g.folder('Archive', [g.folder('Movies', g.movies(60, 10)), g.folder('Games', g.games(9))])
    models = g.folder('models', [g.file('%s-%dB.gguf' % (g.title().lower().replace(' ', '-'), r.choice([7, 8, 13, 32, 70])), OTHER, r.uniform(4, 40) * GiB) for _ in range(14)])
    desktop_sys = g.system('System', 260, [models, g.folder('Music', g.music(6))])
    windows = g.folder('Windows', [g.folder('SteamLibrary', [g.folder('steamapps', g.games(24))]), g.folder('Windows', [], (OTHER, 38 * GiB, 120000))])
    devices = [
        {'id': 'nas', 'name': 'Media NAS', 'note': 'Kodi box', 'hub': False, 'drives': [
            drive('Media 1', '/srv/media1', media1, 4 * TiB * 0.91), drive('Media 2', '/srv/media2', media2, 4 * TiB * 0.91),
            drive('Archive', '/srv/archive', archive, 1 * TiB * 0.91)]},
        {'id': 'desktop', 'name': 'Desktop', 'note': 'Linux + Windows', 'hub': False, 'drives': [
            drive('System', '/', desktop_sys, 2 * TiB * 0.91), drive('Windows', '/mnt/windows', windows, 2 * TiB * 0.91)]},
        {'id': 'pi', 'name': 'Pi', 'note': 'runs the hub', 'hub': True, 'drives': [
            drive('Root', '/', g.system('Root', 120), 512 * GiB * 0.93)]},
        {'id': 'laptop', 'name': 'Laptop', 'note': '', 'hub': False, 'drives': [
            drive('Root', '/', g.system('Root', 150, [g.folder('Videos', g.movies(8, 2))]), 256 * GiB * 0.93)]},
        {'id': 'mac', 'name': 'MacBook', 'note': '', 'hub': False, 'drives': [
            drive('Data', '/System/Volumes/Data', g.system('Data', 140, [g.folder('Music', g.music(12))]), 256 * GiB * 0.93)]},
    ]
    history = {}
    now = int(time.time())
    for dev in devices:
        for d in dev['drives']:
            used, total = d['fs']['used'], d['fs']['total']
            rate = r.uniform(0.0005, 0.004)
            history[dev['id'] + '/' + d['name']] = [[now - day * 86400, int(used * (1 - rate * day)), total] for day in range(60, -1, -3)]
    city = {'generated': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'version': 'demo', 'alert_percent': 90, 'devices': devices}
    # what Settings shows in demo mode: documentation-only addresses (192.0.2.0/24)
    config = {'hub': {'schedule': ['04:30'], 'alert_percent': 90}, 'devices': [
        {'id': 'nas', 'name': 'Media NAS', 'note': 'Kodi box', 'user': 'admin', 'host': '192.0.2.10',
         'drives': [{'name': d['name'], 'path': d['path'], 'mounted': True} for d in devices[0]['drives']]},
        {'id': 'desktop', 'name': 'Desktop', 'note': 'Linux + Windows', 'user': 'me', 'host': '192.0.2.30',
         'drives': [{'name': 'System', 'path': '/', 'exclude': ['/var/lib/docker']}, {'name': 'Windows', 'path': '/mnt/windows', 'mounted': True}]},
        {'id': 'pi', 'name': 'Pi', 'note': 'runs the hub', 'local': True, 'drives': [{'name': 'Root', 'path': '/'}]},
        {'id': 'laptop', 'name': 'Laptop', 'user': 'me', 'host': '192.0.2.40', 'drives': [{'name': 'Root', 'path': '/'}]},
        {'id': 'mac', 'name': 'MacBook', 'user': 'me', 'host': '192.0.2.20', 'command_prefix': 'caffeinate -i',
         'drives': [{'name': 'Data', 'path': '/System/Volumes/Data'}]},
    ]}
    mounts = [
        {'path': '/', 'label': '/', 'fs': 'ext4', 'source': '/dev/sda2', 'total': 512 * GiB, 'used': 141 * GiB, 'free': 371 * GiB, 'readonly': False, 'network': False},
        {'path': '/mnt/media', 'label': 'media', 'fs': 'xfs', 'source': '/dev/sdb1', 'total': 8 * TiB, 'used': 6 * TiB, 'free': 2 * TiB, 'readonly': False, 'network': False},
        {'path': '/mnt/windows', 'label': 'windows', 'fs': 'ntfs3', 'source': '/dev/sdc3', 'total': 2 * TiB, 'used': int(1.4 * TiB), 'free': int(0.6 * TiB), 'readonly': True, 'network': False},
        {'path': '/media/usb', 'label': 'usb', 'fs': 'exfat', 'source': '/dev/sdd1', 'total': 1 * TiB, 'used': 300 * GiB, 'free': 724 * GiB, 'readonly': False, 'network': False},
    ]
    found = [
        {'ip': '192.0.2.1', 'name': 'router.lan', 'ports': ['SSH'], 'ssh': True, 'banner': 'SSH-2.0-dropbear', 'os': 'small Linux box', 'mac': '', 'vendor': '', 'known': ''},
        {'ip': '192.0.2.10', 'name': 'nas.local', 'ports': ['SSH', 'SMB'], 'ssh': True, 'banner': 'SSH-2.0-OpenSSH_9.2p1 Debian', 'os': 'Debian', 'mac': '', 'vendor': '', 'known': 'Media NAS'},
        {'ip': '192.0.2.20', 'name': 'macbook.local', 'ports': ['SSH', 'Mac file sharing'], 'ssh': True, 'banner': 'SSH-2.0-OpenSSH_9.8', 'os': 'Mac', 'mac': '', 'vendor': '', 'known': 'MacBook'},
        {'ip': '192.0.2.44', 'name': 'livingroom-kodi.local', 'ports': ['SSH', 'SMB'], 'ssh': True, 'banner': 'SSH-2.0-OpenSSH_10.0', 'os': '', 'mac': '', 'vendor': '', 'known': ''},
        {'ip': '192.0.2.50', 'name': 'gaming-pc.local', 'ports': ['SMB'], 'ssh': False, 'banner': '', 'os': 'Windows?', 'mac': '', 'vendor': '', 'known': ''},
        {'ip': '192.0.2.61', 'name': 'backup-box.local', 'ports': ['SSH', 'Synology'], 'ssh': True, 'banner': 'SSH-2.0-OpenSSH_8.2', 'os': 'NAS', 'mac': '', 'vendor': '', 'known': ''},
    ]
    return {'city': city, 'history': history, 'config': config, 'mounts': mounts, 'found': found}
