"""Disk City hub: serves the 3D city and the JSON API, runs scheduled scans,
and hosts the settings page and first-run wizard.

    python3 hub.py              # reads devices.yml, listens on hub.port (7171)
    python3 hub.py --demo       # made-up devices, for trying it out
    DC_CONFIG=/etc/dc-ohair/devices.yml DC_DATA=/var/lib/dc-ohair python3 hub.py

Scanning is read-only: nothing here deletes or moves anything on any device.
With no config file the hub starts empty and the page runs the setup wizard.
"""
import argparse, base64, datetime, glob, gzip, hashlib, hmac, ipaddress, json, os, re, secrets, threading, time, urllib.parse, urllib.request
from html import escape as html_escape
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import collect
import notify as notifier
import oidc
from collect import DATA, load

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, 'web')
# The no-Python agent (agent-go/), one file per system: served from here when the
# hub has them (a release tarball or the Docker image), else from the release on GitHub.
AGENTS = os.environ.get('DC_AGENTS', os.path.join(HERE, 'dist', 'agent'))
AGENT_RELEASES = 'https://github.com/nviiiivn/diskcity/releases/download/v%s/%s'
AGENT_NAME = re.compile(r'^dc-agent-(linux|darwin|windows|freebsd)-(amd64|arm64|arm|386)(\.exe)?$')
VERSION = '0.8.0'
THEME_DEFAULTS = {'cookie': 'dc-theme', 'storage': 'dc-theme', 'domain': '', 'default': 'dark'}
UI_DEFAULTS = {'view': 'city', 'units': 'binary'}
SESSION_DAYS = 30


def human(b):
    for unit, size in (('T', 1024 ** 4), ('G', 1024 ** 3), ('M', 1024 ** 2)):
        if b >= size:
            return ('%.2f%s' if unit == 'T' else '%.0f%s') % (b / size, unit)
    return '%dB' % b


def clock_times(schedule):
    """'04:30' strings -> (hour, minute); YAML 1.1 reads an unquoted 04:30 as 270."""
    out = []
    for t in schedule or []:
        if isinstance(t, int):
            out.append((t // 60 % 24, t % 60))
        else:
            h, m = str(t).split(':')
            out.append((int(h), int(m)))
    return out


def next_run(schedule, now=None):
    now = now or datetime.datetime.now()
    runs = []
    for h, m in clock_times(schedule):
        run = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if run <= now:
            run += datetime.timedelta(days=1)
        runs.append(run)
    return min(runs) if runs else None


# ---------- passwords ----------

def hash_password(pw):
    try:
        from argon2 import PasswordHasher
        return PasswordHasher().hash(pw)
    except ImportError:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024)
        return 'scrypt$32768$8$1$%s$%s' % (base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def check_password(pw, stored):
    if not stored or not pw:
        return False
    if stored.startswith('scrypt$'):
        _, n, r, p, salt, digest = stored.split('$')
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got, base64.b64decode(digest))
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerificationError, InvalidHashError
        try:
            return PasswordHasher().verify(stored, pw)
        except (VerificationError, InvalidHashError):
            return False
    except ImportError:
        return False


class Sessions:
    """Login sessions, kept as hashes in data/sessions.json so a restart doesn't log people out."""

    def __init__(self):
        self.path = os.path.join(DATA, 'sessions.json')
        self.lock = threading.Lock()

    def _load(self):
        now = time.time()
        return {k: v for k, v in load(self.path, {}).items() if v > now}

    def new(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            s = self._load()
            s[hashlib.sha256(token.encode()).hexdigest()] = time.time() + SESSION_DAYS * 86400
            os.makedirs(DATA, exist_ok=True)
            collect.save(self.path, s)
        return token

    def valid(self, token):
        if not token:
            return False
        return hashlib.sha256(token.encode()).hexdigest() in self._load()

    def drop(self, token=None):
        with self.lock:
            s = {} if token is None else self._load()
            if token:
                s.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            collect.save(self.path, s)


class RateLimit:
    def __init__(self, tries, window):
        self.tries, self.window, self.hits, self.lock = tries, window, {}, threading.Lock()

    def allow(self, key):
        now = time.time()
        with self.lock:
            hits = [t for t in self.hits.get(key, []) if t > now - self.window]
            if len(hits) >= self.tries:
                self.hits[key] = hits
                return False
            hits.append(now)
            self.hits[key] = hits
            return True


# ---------- the hub ----------

def commands_in(cfg):
    """Every command the hub would run from this config: password_cmd, *_cmd in health and notify."""
    out = set()
    if not isinstance(cfg, dict):
        return out
    for d in cfg.get('devices') or []:
        if isinstance(d, dict) and d.get('password_cmd'):
            out.add(json.dumps(d['password_cmd']))
    hubcfg = cfg.get('hub') or {}
    places = [hubcfg.get('health') or {}, hubcfg.get('oidc') or {}] + [c for c in hubcfg.get('notify') or [] if isinstance(c, dict)]
    for place in places:
        for k, v in place.items():
            if k.endswith('_cmd') and v:
                out.add(json.dumps(v))
    return out


def changed_commands(old, new):
    """True when new brings in a command old didn't have (commands run on the hub)."""
    return bool(commands_in(new) - commands_in(old))


class Hub:
    def __init__(self, cfg, path=None, demo=None):
        self.cfg = cfg
        self.path = path
        self.demo = demo
        self.running = False
        self.next = None
        self.lock = threading.Lock()
        self.sessions = Sessions()
        self._scrutiny = (0, {}, None)

    @property
    def auth(self):
        """off (default), password (built-in login), proxy (a reverse proxy vouches via a header) or oidc."""
        return 'off' if self.demo else self.cfg['hub'].get('auth', 'off')

    # ----- data -----

    def city(self):
        if self.demo:
            city = dict(self.demo['city'], ui=self.ui())
            city['alerts'] = self.current_alerts(city)
            return city
        status = load(os.path.join(DATA, 'status.json'), {})
        devices = []
        for dev in self.cfg['devices']:
            live = load(os.path.join(DATA, dev['id'] + '.json'), {'drives': []})
            snap = load(os.path.join(DATA, 'snapshot', dev['id'] + '.json'), {'drives': []})
            live_by = {d['name']: d for d in live['drives']}
            snap_by = {d['name']: d for d in snap['drives']}
            drives = []
            for want in dev['drives']:
                name = want['name']
                got = live_by.get(name, {})
                if 'tree' in got:
                    drives.append(dict(got, source='live', when=live['scanned']))
                elif name in snap_by:
                    drives.append(dict(snap_by[name], source='snapshot', when=snap['scanned'], why=got.get('error')))
                else:
                    drives.append({'name': name, 'path': want['path'], 'source': 'none', 'why': got.get('error')})
            devices.append({'id': dev['id'], 'name': dev['name'], 'note': dev.get('note', ''),
                            'hub': bool(dev.get('local')), 'status': status.get(dev['id']), 'drives': drives})
        self.attach_health(devices)
        city = {'generated': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'version': VERSION,
                'alert_percent': self.cfg['hub']['alert_percent'], 'ui': self.ui(), 'devices': devices}
        city['alerts'] = self.current_alerts(city)
        return city

    def ui(self):
        health = self.cfg['hub'].get('health') or {}
        return dict(UI_DEFAULTS, **(self.cfg['hub'].get('ui') or {}),
                    health=health.get('mode', 'off') != 'off' or bool(self.demo), failing_dot=health.get('failing_dot', True))

    # ----- drive health -----

    def scrutiny(self, fresh=False):
        """Scrutiny's /api/summary, keyed by WWN and by 'serial:<serial>'. Cached for five minutes."""
        hc = self.cfg['hub'].get('health') or {}
        url = (hc.get('scrutiny_url') or '').rstrip('/')
        when, cached, err = self._scrutiny
        if not url:
            return {}, 'no Scrutiny address set'
        if not fresh and time.time() - when < (300 if not err else 60):
            return cached, err
        headers = {'Accept': 'application/json'}
        if hc.get('scrutiny_token_cmd'):
            headers['Authorization'] = 'Bearer ' + collect.password({'password_cmd': hc['scrutiny_token_cmd']})
        out, err = {}, None
        try:
            req = urllib.request.Request(url + '/api/summary', headers=headers)
            with urllib.request.urlopen(req, timeout=8) as res:
                data = json.load(res)
            for wwn, item in ((data.get('data') or {}).get('summary') or {}).items():
                dev, sm = item.get('device') or {}, item.get('smart') or {}
                st = int(dev.get('device_status') or 0)
                notes = (['SMART says the drive is failing'] if st & 1 else []) + (['Scrutiny: its attributes match drives that tend to fail'] if st & 2 else [])
                h = {'state': 'failing' if st & 1 else 'warning' if st & 2 else 'ok', 'model': dev.get('model_name'),
                     'serial': dev.get('serial_number'), 'temp': sm.get('temp'), 'hours': sm.get('power_on_hours'),
                     'notes': notes, 'source': 'scrutiny'}
                h = {k: v for k, v in h.items() if v not in (None, '')}
                out[str(dev.get('wwn') or wwn).lower()] = h
                if dev.get('serial_number'):
                    out['serial:' + dev['serial_number'].lower()] = h
        except (OSError, ValueError, AttributeError) as e:
            err = "Couldn't read Scrutiny at %s: %s" % (url, e)
        self._scrutiny = (time.time(), out, err)
        return out, err

    def attach_health(self, devices):
        mode = (self.cfg['hub'].get('health') or {}).get('mode', 'off')
        if self.demo:
            return
        if mode == 'off':
            for dev in devices:
                for d in dev['drives']:
                    d.pop('health', None)
            return
        if mode != 'scrutiny':
            return
        table, _ = self.scrutiny()
        for dev in devices:
            for d in dev['drives']:
                d.pop('health', None)
                for i in (d.get('disk') or {}).get('ids', []):
                    key = i[4:].lower() if i.startswith('wwn-') else 'serial:' + i.rsplit('_', 1)[-1].lower()
                    if key in table:
                        d['health'] = table[key]
                        break

    def drives(self, city=None):
        city = city or self.city()
        limit = city['alert_percent']
        out = []
        for dev in city['devices']:
            for d in dev['drives']:
                fs = d.get('fs') or {}
                c = (d.get('tree') or {}).get('c')
                pct = round(100.0 * fs['used'] / fs['total'], 1) if fs.get('total') else None
                out.append({'device': dev['id'], 'device_name': dev['name'], 'drive': d['name'], 'path': d.get('path'),
                            'source': d['source'], 'when': d.get('when'), 'used': fs.get('used'), 'free': fs.get('free'),
                            'total': fs.get('total'), 'percent': pct, 'alert': pct is not None and pct >= limit,
                            'categories': dict(zip(['music', 'games', 'series', 'movies', 'other'], c)) if c else None})
        return out

    def history(self, days=90):
        if self.demo:
            return self.demo['history']
        cutoff = time.time() - days * 86400
        out = {}
        for path in glob.glob(os.path.join(DATA, 'history', '*.jsonl')):
            dev = os.path.basename(path)[:-len('.jsonl')]
            try:
                with open(path) as f:
                    for line in f:
                        row = json.loads(line)
                        if row['t'] >= cutoff:
                            out.setdefault(dev + '/' + row['d'], []).append([row['t'], row['used'], row['total']])
            except (OSError, ValueError, KeyError):
                continue
        return out

    def status(self):
        return {'running': self.running, 'demo': bool(self.demo), 'version': VERSION,
                'next': self.next.strftime('%Y-%m-%dT%H:%M:%S') if self.next else None,
                'progress': collect.progress,
                'devices': {} if self.demo else load(os.path.join(DATA, 'status.json'), {})}

    # ----- settings -----

    def public_config(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg['hub'].pop('admin_password', None)
        for d in cfg['devices']:
            if d.pop('push_token', None):
                d['paired'] = True
        return cfg

    SECURITY_KEYS = ('auth', 'trusted_proxies', 'proxy_user_header', 'admin_password', 'oidc')

    def save_config(self, new, security=False):
        """Saves a config from the settings page. Login settings only change through /api/security."""
        new = collect.validate(new)
        for key in self.SECURITY_KEYS if not security else ('admin_password',):
            if key in self.cfg['hub']:
                new['hub'][key] = self.cfg['hub'][key]
            else:
                new['hub'].pop(key, None)
        # enrollment tokens (stored hashed) only change through /api/push/token
        tokens = {d['id']: d.get('push_token') for d in self.cfg['devices']}
        for d in new['devices']:
            d.pop('paired', None)
            d.pop('push_token', None)
            if tokens.get(d['id']):
                d['push_token'] = tokens[d['id']]
        collect.save_config(new, self.path)
        self.cfg = new

    # ----- push devices -----

    def public_url(self, host_header=None):
        """The address devices use to reach the hub: hub.public_url, else this machine's LAN address."""
        if self.cfg['hub'].get('public_url'):
            return self.cfg['hub']['public_url'].rstrip('/')
        port = int(self.cfg['hub'].get('port', 7171))
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('192.0.2.1', 9))  # no packet is sent; this just picks the outgoing interface
            ip = s.getsockname()[0]
            s.close()
        except OSError:
            ip = (host_header or 'localhost').split(':')[0]
        return 'http://%s:%d' % (ip, port)

    def new_token(self, dev_id):
        token = secrets.token_urlsafe(24)
        cfg = json.loads(json.dumps(self.cfg))
        for d in cfg['devices']:
            if d['id'] == dev_id:
                d['push'] = True
                d['push_token'] = hashlib.sha256(token.encode()).hexdigest()
                break
        else:
            raise KeyError(dev_id)
        collect.save_config(collect.validate(cfg), self.path)
        self.cfg = cfg
        return token

    def revoke(self, dev_id):
        cfg = json.loads(json.dumps(self.cfg))
        for d in cfg['devices']:
            if d['id'] == dev_id:
                d.pop('push_token', None)
        collect.save_config(cfg, self.path)
        self.cfg = cfg

    def device_for(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for d in self.cfg['devices']:
            if d.get('push_token') and hmac.compare_digest(d['push_token'], digest):
                return d
        return None

    def push_spec(self, dev):
        spec = collect.scan_spec(self.cfg, dev.get('drives') or 'auto')
        times = clock_times(self.cfg['hub']['schedule'])
        spec['schedule'] = '%02d:%02d' % times[0] if times else '04:30'
        return spec

    def take_report(self, dev, out):
        if not isinstance(out, dict) or not isinstance(out.get('drives'), list):
            raise ValueError('a report needs a drives list')
        if not dev.get('drives'):
            # first report from a device set to "pick the drives for me"
            cfg = json.loads(json.dumps(self.cfg))
            for d in cfg['devices']:
                if d['id'] == dev['id']:
                    d['drives'] = [{'name': x['name'], 'path': x['path']} for x in out['drives'] if x.get('name') and x.get('path')]
            collect.save_config(collect.validate(cfg), self.path)
            self.cfg = cfg
        msg = collect.store(dev, out, self.cfg)
        threading.Thread(target=self.notify, daemon=True).start()
        return msg

    def set_password(self, pw):
        self.cfg['hub']['admin_password'] = hash_password(pw)
        collect.save_config(self.cfg, self.path)
        self.sessions.drop()

    # ----- scans -----

    def scan(self, only=None):
        if self.demo:
            return self.pretend_scan(only)
        with self.lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._scan, args=(only,), daemon=True).start()
        return True

    def pretend_scan(self, only):
        """Demo mode: walk the progress list through a scan without touching anything."""
        if self.running:
            return False
        self.running = True
        ids = [d['id'] for d in self.cfg['devices'] if not only or d['id'] in only]
        for i in ids:
            collect.progress[i] = {'state': 'waiting'}

        def run():
            for n, i in enumerate(ids):
                collect.progress[i] = {'state': 'scanning'}
                time.sleep(0.8 + 0.4 * n)
                collect.progress[i] = {'state': 'done', 'msg': 'scanned'}
            self.running = False
        threading.Thread(target=run, daemon=True).start()
        return True

    def _scan(self, only):
        try:
            collect.collect_all(self.cfg, only)
            self.notify()
        finally:
            self.running = False

    # ----- alerts -----

    def channels(self):
        hubcfg = self.cfg['hub']
        chans = [c for c in hubcfg.get('notify') or [] if isinstance(c, dict)]
        if hubcfg.get('alert_webhook'):  # the older single-URL setting
            chans.append({'type': 'ntfy', 'url': hubcfg['alert_webhook']})
        return chans

    def current_alerts(self, city=None):
        """Drives over the full line, drives whose health is off, and (if asked for) devices that couldn't be scanned."""
        city = city or self.city()
        opts = self.cfg['hub'].get('alerts') or {}
        limit = city['alert_percent']
        out = []
        for dev in city['devices']:
            st = dev.get('status')
            if opts.get('offline') and st and not st.get('ok'):
                out.append({'key': 'scan:' + dev['id'], 'kind': 'scan', 'level': 'warning', 'device': dev['name'],
                            'msg': "%s couldn't be scanned: %s" % (dev['name'], st.get('msg'))})
            for d in dev['drives']:
                where = '%s / %s' % (dev['name'], d['name'])
                fs = d.get('fs') or {}
                if fs.get('total'):
                    pct = 100.0 * fs['used'] / fs['total']
                    if pct >= limit:
                        out.append({'key': 'full:%s/%s' % (dev['id'], d['name']), 'kind': 'full', 'device': dev['name'], 'drive': d['name'],
                                    'level': 'critical' if pct >= 98 else 'warning', 'percent': round(pct, 1),
                                    'msg': '%s is %d%% full (%s free)' % (where, round(pct), human(fs['free']))})
                h = d.get('health')
                if h and h.get('state') in ('warning', 'failing') and opts.get('health', True):
                    out.append({'key': 'health:%s/%s' % (dev['id'], d['name']), 'kind': 'health', 'device': dev['name'], 'drive': d['name'],
                                'level': 'critical' if h['state'] == 'failing' else 'warning',
                                'msg': '%s health: %s%s' % (where, h['state'], ' (%s)' % '; '.join(h['notes']) if h.get('notes') else '')})
        return out

    def notify(self):
        """Sends what changed since last time: new or worse alerts, and ones that cleared."""
        path = os.path.join(DATA, 'alerts.json')
        before = load(path, {})
        if isinstance(before, list):  # the old format only remembered full drives
            before = {'full:' + k: 'warning' for k in before}
        alerts = self.current_alerts()
        now = {a['key']: a['level'] for a in alerts}
        collect.save(path, now)
        rank = {'warning': 1, 'critical': 2}
        new = [a for a in alerts if rank[a['level']] > rank.get(before.get(a['key']), 0)]
        gone = [k for k in before if k not in now]
        chans = self.channels()
        if not chans or not (new or gone):
            return
        lines = [a['msg'] for a in new] + ['Back to normal: %s' % k.split(':', 1)[1].replace('/', ' / ') for k in gone]
        level = 'critical' if any(a['level'] == 'critical' for a in new) else 'warning'
        subject = "Disk City: " + (lines[0] if len(lines) == 1 else '%d changes to your drives' % len(lines))
        results = notifier.send_all(chans, subject, '\n'.join(lines), level, new)
        collect.save(os.path.join(DATA, 'notify.json'), {'when': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'results': results})

    def scheduler(self):
        while True:
            sched = list(self.cfg['hub']['schedule'] or [])
            self.next = next_run(sched)
            while self.cfg['hub']['schedule'] == sched and (not self.next or datetime.datetime.now() < self.next):
                time.sleep(20)
            if self.cfg['hub']['schedule'] == sched and self.next:
                self.scan()


# ---------- HTTP ----------

class Handler(SimpleHTTPRequestHandler):
    hub = None
    logins = RateLimit(8, 600)
    probes = RateLimit(30, 60)
    reports = RateLimit(30, 60)
    searches = RateLimit(4, 60)

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Referrer-Policy', 'no-referrer')
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'max-age=86400' if self.path.startswith('/vendor/') else 'no-cache')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *a):
        pass

    # ----- auth helpers -----

    def client_ip(self):
        ip = self.client_address[0]
        fwd = self.headers.get('X-Forwarded-For')
        try:
            if fwd and ipaddress.ip_address(ip).is_loopback:
                return fwd.split(',')[0].strip()
        except ValueError:
            pass
        return ip

    def session_token(self):
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        return cookie['dc_session'].value if 'dc_session' in cookie else None

    def is_local(self):
        """A browser on the hub machine itself (not a reverse proxy passing someone through)."""
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback and not self.headers.get('X-Forwarded-For')
        except ValueError:
            return False

    def is_admin(self):
        hubcfg = self.hub.cfg['hub']
        auth = self.hub.auth
        if auth == 'off':
            return True
        if auth == 'proxy':
            proxies = hubcfg.get('trusted_proxies') or ['127.0.0.1', '::1']
            header = hubcfg.get('proxy_user_header') or 'Remote-User'
            return self.client_address[0] in proxies and bool(self.headers.get(header))
        return self.hub.sessions.valid(self.session_token())

    def need_admin(self):
        if self.is_admin():
            return True
        self.send_json({'error': 'login required'}, 401)
        return False

    def body(self):
        length = min(int(self.headers.get('Content-Length') or 0), 2 * 1024 * 1024)
        if not length:
            return {}
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('expected a JSON object')
        return data

    def set_session(self, token, max_age):
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
        self.extra_headers.append(('Set-Cookie', 'dc_session=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d%s' % (token, max_age, secure)))

    # ----- routes -----

    def do_GET(self):
        self.extra_headers = []
        path, _, query = self.path.partition('?')
        if path in ('/agent.py', '/push.py'):
            with open(os.path.join(HERE, path[1:]), 'rb') as f:
                return self.send_bytes(f.read(), 'text/x-python; charset=utf-8')
        if path == '/dl/version' or path.startswith('/dl/dc-agent-'):
            return self.agent_download(path[4:])
        if path == '/api/push/spec':
            dev = self.hub.device_for(self.bearer())
            if not dev:
                return self.send_json({'error': 'unknown or revoked enrollment token'}, 401)
            return self.send_json(self.hub.push_spec(dev))
        if path in ('/auth/login', '/auth/callback'):
            return self.oidc_route(path, query)
        if path == '/theme-config.js':
            theme = dict(THEME_DEFAULTS, **(self.hub.cfg['hub'].get('theme') or {}))
            return self.send_bytes(('window.DC_THEME = %s;\n' % json.dumps(theme)).encode(), 'text/javascript')
        if path == '/api/me':
            hub = self.hub
            bind = hub.cfg['hub'].get('bind', '0.0.0.0')
            return self.send_json({'admin': self.is_admin(), 'auth': hub.auth, 'demo': bool(hub.demo), 'version': VERSION,
                                   'login_name': (hub.cfg['hub'].get('oidc') or {}).get('name') or 'your login service',
                                   'password_set': bool(hub.cfg['hub'].get('admin_password')), 'local': self.is_local(),
                                   'exposed': hub.auth == 'off' and not hub.demo and not ipaddress.ip_address(bind).is_loopback,
                                   'setup': not hub.demo and not hub.cfg['devices']})
        if not path.startswith('/api/'):
            return super().do_GET()
        if not self.need_admin():
            return
        if path == '/api/city':
            return self.send_json(self.hub.city())
        if path == '/api/drives':
            return self.send_json(self.hub.drives())
        if path == '/api/alerts':
            return self.send_json(self.hub.current_alerts())
        if path == '/api/history':
            days = 90
            for part in query.split('&'):
                if part.startswith('days=') and part[5:].isdigit():
                    days = min(int(part[5:]), 3650)
            return self.send_json(self.hub.history(days))
        if path == '/api/status':
            return self.send_json(self.hub.status())
        if path == '/api/settings':
            return self.send_json(self.settings_payload())
        if path == '/api/settings/export':
            import yaml
            text = "# Disk City config export\n" + yaml.safe_dump(self.hub.public_config(), sort_keys=False, allow_unicode=True)
            self.extra_headers.append(('Content-Disposition', 'attachment; filename="dc-ohair-config.yml"'))
            return self.send_bytes(text.encode(), 'application/yaml')
        return self.send_error(404)

    def do_PUT(self):
        return self.do_POST()

    def bearer(self):
        auth = self.headers.get('Authorization', '')
        return auth[7:].strip() if auth.startswith('Bearer ') else None

    def report(self):
        """POST /api/report from a push agent: its scan result, signed with its enrollment token."""
        if not self.reports.allow(self.client_ip()):
            return self.send_json({'error': 'too many reports; try again in a minute'}, 429)
        dev = self.hub.device_for(self.bearer())
        if not dev:
            return self.send_json({'error': 'unknown or revoked enrollment token'}, 401)
        length = int(self.headers.get('Content-Length') or 0)
        if length > 256 * 1024 * 1024:
            return self.send_json({'error': 'report too big'}, 413)
        raw = self.rfile.read(length)
        try:
            if self.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            msg = self.hub.take_report(dev, json.loads(raw))
        except (OSError, ValueError, EOFError) as e:
            return self.send_json({'error': 'could not read that report: %s' % e}, 400)
        return self.send_json({'ok': True, 'msg': msg})

    def do_POST(self):
        self.extra_headers = []
        path = self.path.partition('?')[0]
        if path == '/api/report':
            return self.report()
        # every write needs a custom header, which a cross-site page can't send without CORS (never granted)
        if self.headers.get('X-DC') != '1':
            return self.send_json({'error': 'missing X-DC header'}, 403)
        try:
            data = self.body()
        except ValueError:
            return self.send_json({'error': 'bad JSON'}, 400)
        hub = self.hub
        if path == '/api/login':
            if not self.logins.allow(self.client_ip()):
                return self.send_json({'error': 'Too many tries. Wait ten minutes.'}, 429)
            if not check_password(data.get('password', ''), hub.cfg['hub'].get('admin_password')):
                return self.send_json({'error': 'That password is not right.'}, 401)
            self.set_session(hub.sessions.new(), SESSION_DAYS * 86400)
            return self.send_json({'admin': True})
        if path == '/api/logout':
            hub.sessions.drop(self.session_token())
            self.set_session('', 0)
            return self.send_json({'admin': False})
        if not self.need_admin():
            return
        if path == '/api/security' and not hub.demo:
            return self.security(data)
        if path == '/api/scan':
            only = set(data.get('devices') or []) or None
            started = hub.scan(only)
            return self.send_json({'started': started, 'running': hub.running}, 202 if started else 409)
        if hub.demo:
            return self.demo_post(path, data)
        if path in ('/api/settings', '/api/settings/import'):
            try:
                cfg = data.get('config')
                if path.endswith('/import'):
                    import yaml
                    cfg = yaml.safe_load(data.get('yaml', ''))
                # password_cmd runs on the hub, so with no login only a browser on the hub may change it
                if hub.auth == 'off' and not self.is_local() and changed_commands(hub.cfg, cfg):
                    return self.send_json({'error': 'With no login turned on, commands (password_cmd and other *_cmd settings) can only be '
                                                    'added from a browser on the hub itself, or in the config file. Turn on a login under Security.'}, 403)
                hub.save_config(cfg)
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                return self.send_json({'error': str(e)}, 400)
            except Exception as e:  # yaml errors
                return self.send_json({'error': 'Could not read that config: %s' % e}, 400)
            return self.send_json(self.settings_payload())
        if path == '/api/discover':
            if not self.searches.allow(self.client_ip()):
                return self.send_json({'error': 'One search at a time; try again in a minute.'}, 429)
            import discover
            try:
                found = discover.discover(data.get('network') or None)
            except ValueError as e:
                return self.send_json({'error': str(e)}, 400)
            known = {}
            for d in hub.cfg['devices']:
                host = d.get('host') or collect.ssh_target(d).split('@')[-1]
                if host:
                    known[host] = d['name']
            for h in found['hosts']:
                h['known'] = known.get(h['ip']) or known.get(h['name']) or ''
            return self.send_json(found)
        if path == '/api/health/test':
            if hub.auth == 'off' and not self.is_local() and changed_commands(hub.cfg, {'hub': {'health': data.get('health') or {}}}):
                return self.send_json({'ok': False, 'error': 'Save first: with no login on, commands can only be added from a browser on the hub.'})
            cfg = json.loads(json.dumps(hub.cfg))
            cfg['hub']['health'] = dict(cfg['hub'].get('health') or {}, **(data.get('health') or {}))
            probe = Hub(cfg, demo=None)
            table, err = probe.scrutiny(fresh=True)
            if err:
                return self.send_json({'ok': False, 'error': err})
            disks = {k for k in table if not k.startswith('serial:')}
            devices = hub.city()['devices']
            probe.attach_health(devices)
            matched = sum(1 for dv in devices for d in dv['drives'] if d.get('health'))
            return self.send_json({'ok': True, 'disks': len(disks), 'matched': matched})
        if path == '/api/notify/test':
            ch = data.get('channel') or {}
            if hub.auth == 'off' and not self.is_local() and changed_commands(hub.cfg, {'hub': {'notify': [ch]}}):
                return self.send_json({'type': ch.get('type'), 'ok': False, 'error': 'Save this channel first: with no login on, '
                                       'commands can only be added from a browser on the hub, or in the config file.'})
            results = notifier.send_all([ch], "Disk City test", 'This is a test from your disk city. If you can read it, alerts will reach you here.')
            return self.send_json(results[0])
        if path in ('/api/push/token', '/api/push/revoke'):
            dev_id = data.get('id', '')
            try:
                if path.endswith('/revoke'):
                    hub.revoke(dev_id)
                    return self.send_json({'ok': True})
                token = hub.new_token(dev_id)
            except KeyError:
                return self.send_json({'error': 'Save the device first, then make its command.'}, 404)
            return self.send_json({'token': token, 'hub': hub.public_url(self.headers.get('Host'))})
        if path in ('/api/test', '/api/detect'):
            if not self.probes.allow(self.client_ip()):
                return self.send_json({'error': 'Slow down a little.'}, 429)
            dev = data.get('device') or {}
            dev.setdefault('id', 'probe')
            if not dev.get('local') and not collect.ssh_target(dev):
                return self.send_json({'ok': False, 'error': 'Fill in the host first.'})
            if path == '/api/test':
                spec = {'mode': 'check', 'paths': [d.get('path') for d in dev.get('drives') or [] if d.get('path')]}
            else:
                spec = {'mode': 'mounts'}
            out, err = collect.run_agent(dev, spec, 60)
            if err:
                return self.send_json({'ok': False, 'error': err})
            return self.send_json(dict(out, ok=True))
        return self.send_json({'error': 'not found'}, 404)

    # ----- OpenID Connect -----

    def redirect(self, url):
        self.send_response(302)
        self.send_header('Location', url)
        for k, v in getattr(self, 'extra_headers', []):
            self.send_header(k, v)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def callback_url(self):
        cfg = self.hub.cfg['hub'].get('oidc') or {}
        if cfg.get('redirect_url'):
            return cfg['redirect_url']
        proto = self.headers.get('X-Forwarded-Proto') or 'http'
        host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or 'localhost'
        return '%s://%s/auth/callback' % (proto.split(',')[0].strip(), host.split(',')[0].strip())

    def oidc_route(self, path, query):
        hub = self.hub
        q = dict(urllib.parse.parse_qsl(query))
        cfg = hub.cfg['hub'].get('oidc') or {}
        if hub.auth != 'oidc':
            return self.redirect('/')
        try:
            if path == '/auth/login':
                nxt = q.get('next', '/')
                if not nxt.startswith('/') or nxt.startswith('//'):
                    nxt = '/'
                return self.redirect(oidc.start(cfg, self.callback_url(), nxt))
            if not self.logins.allow(self.client_ip()):
                raise ValueError('Too many tries. Wait ten minutes.')
            if q.get('error'):
                raise ValueError('The provider said: %s' % (q.get('error_description') or q['error']))
            who, nxt = oidc.finish(cfg, q.get('state'), q.get('code', ''))
        except (OSError, ValueError, KeyError) as e:
            page = ('<!doctype html><meta charset="utf-8"><title>Disk City</title><body style="font:16px system-ui;padding:2em">'
                    '<h2>Couldn\'t sign you in</h2><p>%s</p><p><a href="/auth/login">Try again</a></p>' % html_escape(str(e)))
            return self.send_bytes(page.encode(), 'text/html; charset=utf-8', 403)
        self.set_session(hub.sessions.new(), SESSION_DAYS * 86400)
        return self.redirect(nxt)

    def security(self, data):
        hub = self.hub
        mode = data.get('auth')
        if mode not in ('off', 'password', 'proxy', 'oidc'):
            return self.send_json({'error': 'auth must be off, password, proxy or oidc'}, 400)
        if mode == 'oidc':
            o = {k: v for k, v in (data.get('oidc') or {}).items() if v not in ('', None, [])}
            if not o.get('client_id'):
                return self.send_json({'error': 'Fill in the client ID.'}, 400)
            if not o.get('allowed_users') and not o.get('allow_any_user'):
                return self.send_json({'error': 'List at least one person who may sign in (their email or user name).'}, 400)
            if hub.auth == 'off' and not self.is_local() and changed_commands(hub.cfg, {'hub': {'oidc': o}}):
                return self.send_json({'error': 'With no login on, a client_secret_cmd can only be added from a browser on the hub.'}, 403)
            try:
                oidc.metadata(o, fresh=True)
            except (OSError, ValueError) as e:
                return self.send_json({'error': "Couldn't reach the provider's settings at that issuer: %s" % e}, 400)
        new = data.get('password') or ''
        if mode == 'password':
            if new and len(new) < 8:
                return self.send_json({'error': 'Use at least 8 characters.'}, 400)
            if not new and not hub.cfg['hub'].get('admin_password'):
                return self.send_json({'error': 'Choose a password first.'}, 400)
        if new and hub.auth == 'password' and hub.cfg['hub'].get('admin_password'):
            if not self.logins.allow(self.client_ip()):
                return self.send_json({'error': 'Too many tries. Wait ten minutes.'}, 429)
            if not check_password(data.get('old', ''), hub.cfg['hub']['admin_password']):
                return self.send_json({'error': 'The current password is not right.'}, 401)
        cfg = json.loads(json.dumps(hub.cfg))
        cfg['hub']['auth'] = mode
        if mode == 'proxy':
            cfg['hub']['trusted_proxies'] = [p for p in data.get('trusted_proxies') or ['127.0.0.1', '::1'] if p]
            cfg['hub']['proxy_user_header'] = data.get('proxy_user_header') or 'Remote-User'
        if mode == 'oidc':
            cfg['hub']['oidc'] = o
        hub.save_config(cfg, security=True)
        if new:
            hub.set_password(new)
        if mode in ('password', 'oidc'):
            self.set_session(hub.sessions.new(), SESSION_DAYS * 86400)
        return self.send_json({'auth': mode, 'admin': True})

    def demo_post(self, path, data):
        """Demo mode answers like a real hub but keeps changes in memory and makes up device replies."""
        hub = self.hub
        if path == '/api/settings':
            try:
                hub.cfg = collect.validate(data.get('config'))
            except (ValueError, TypeError, AttributeError) as e:
                return self.send_json({'error': str(e)}, 400)
            return self.send_json(self.settings_payload())
        if path == '/api/test':
            time.sleep(0.6)
            dev = data.get('device') or {}
            return self.send_json({'ok': True, 'python': '3.11.2', 'host': dev.get('name') or 'demo-box', 'platform': 'linux',
                                   'paths': [{'path': d.get('path'), 'exists': True, 'readable': True} for d in dev.get('drives') or []]})
        if path == '/api/detect':
            time.sleep(0.6)
            return self.send_json({'ok': True, 'host': 'demo-box', 'mounts': hub.demo['mounts']})
        if path == '/api/push/token':
            return self.send_json({'token': 'demo-token-not-real', 'hub': 'http://192.0.2.5:7171'})
        if path == '/api/health/test':
            return self.send_json({'ok': True, 'disks': 6, 'matched': 5})
        if path == '/api/notify/test':
            return self.send_json({'type': (data.get('channel') or {}).get('type'), 'ok': True})
        if path == '/api/discover':
            time.sleep(1.2)
            return self.send_json({'network': '192.0.2.0/24', 'hub': '192.0.2.5', 'hosts': hub.demo['found']})
        return self.send_json({'error': 'Not in demo mode.'}, 403)

    def settings_payload(self):
        out = {'config': self.hub.public_config(), 'config_path': self.hub.path, 'demo': bool(self.hub.demo),
               'category_defaults': collect.category_defaults(), 'in_docker': os.path.exists('/.dockerenv')}
        if self.hub.demo:
            fake = 'ssh-ed25519 AAAA-this-is-a-demo-key-not-a-real-one dc-ohair@demo'
            out.update(pubkey=fake, key_path='/data/ssh/id_ed25519.pub', agent_sha='demo',
                       restricted_line='command="python3 -c \\"…checks the agent, then runs it…\\" $SSH_ORIGINAL_COMMAND",no-port-forwarding,no-pty ' + fake)
        else:
            try:
                out.update(pubkey=collect.hub_pubkey(), key_path=collect.hub_key() + '.pub',
                           restricted_line=collect.restricted_key_line(), agent_sha=collect.agent_sha())
            except Exception as e:  # no ssh-keygen on this machine
                out['key_error'] = str(e)
        return out

    def agent_download(self, name):
        """/dl/version and /dl/dc-agent-<os>-<arch>: the no-Python agent, for the install
        lines in Settings and for installed agents keeping up with the hub. Public, like
        /push.py: the binary holds no secret, and the token travels in the command."""
        if name == 'version':
            have = sorted(n for n in os.listdir(AGENTS) if AGENT_NAME.match(n)) if os.path.isdir(AGENTS) else []
            return self.send_json({'version': VERSION, 'files': have, 'from': 'hub' if have else 'github'})
        if not AGENT_NAME.match(name):
            return self.send_json({'error': 'no such agent'}, 404)
        local = os.path.join(AGENTS, name)
        if os.path.isfile(local):
            with open(local, 'rb') as f:
                self.extra_headers.append(('Content-Disposition', 'attachment; filename="%s"' % name))
                return self.send_bytes(f.read(), 'application/octet-stream')
        return self.redirect(AGENT_RELEASES % (VERSION, name))

    def send_bytes(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        for k, v in getattr(self, 'extra_headers', []):
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        for k, v in getattr(self, 'extra_headers', []):
            self.send_header(k, v)
        if 'gzip' in self.headers.get('Accept-Encoding', '') and len(body) > 2048:
            body = gzip.compress(body, 5)
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description="Disk City hub")
    ap.add_argument('--demo', action='store_true', help='serve made-up devices instead of your config')
    ap.add_argument('--config', help='path to devices.yml (default: $DC_CONFIG or ./devices.yml)')
    ap.add_argument('--port', type=int, help='override hub.port')
    ap.add_argument('--bind', help='override hub.bind (default 0.0.0.0)')
    ap.add_argument('--no-schedule', action='store_true', help="don't run scheduled scans")
    ap.add_argument('--set-password', action='store_true', help='set the admin password from the terminal and exit')
    ap.add_argument('--auth-off', action='store_true', help='turn the page login off again (if you locked yourself out) and exit')
    args = ap.parse_args()
    path = args.config or collect.CONFIG
    if args.demo:
        import demo
        built = demo.build()
        hub = Hub(collect.validate(built['config']), demo=built)
    else:
        cfg = collect.load_config(path) if os.path.exists(path) else collect.validate({})
        hub = Hub(cfg, path)
        if args.auth_off:
            hub.cfg['hub']['auth'] = 'off'
            collect.save_config(hub.cfg, path)
            return print('Login is off again in', path)
        if args.set_password:
            import getpass
            pw = getpass.getpass('New admin password: ')
            if len(pw) < 8 or pw != getpass.getpass('Again: '):
                raise SystemExit('Passwords must match and be at least 8 characters.')
            hub.set_password(pw)
            hub.cfg['hub']['auth'] = 'password'
            collect.save_config(hub.cfg, path)
            return print('Saved to', path, '(login is now on)')
    port = args.port or int(hub.cfg['hub'].get('port', 7171))
    bind = args.bind or hub.cfg['hub'].get('bind', '0.0.0.0')
    if not args.demo and not args.no_schedule:
        threading.Thread(target=hub.scheduler, daemon=True).start()
    Handler.hub = hub
    print("Disk City %s on http://%s:%d%s" % (VERSION, bind, port, ' (demo)' if args.demo else ''), flush=True)
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
