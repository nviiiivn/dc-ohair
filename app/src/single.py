"""Single-device mode: no hub. The app scans one computer over SSH, or this
phone itself, with the same read-only scanner the hub uses (shared/agent.py),
and keeps the last result on the phone.

SSH logs in with the app's own key (made on first use; the public half is
shown to copy) or with a password typed for that scan only, never saved.
A computer's host key is remembered the first time and checked after that.
"""
import base64, contextlib, hashlib, io, json, os, socket, threading, time

AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shared', 'agent.py')


class NeedsPassword(Exception):
    pass


class UnknownHost(Exception):
    """First contact (or a changed key): the person checks the fingerprint, then the app retries with accept_host=raw."""

    def __init__(self, host, fingerprint, raw, changed):
        super().__init__('First time connecting to %s' % host)
        self.host, self.fingerprint, self.raw, self.changed = host, fingerprint, raw, changed


class ScanError(Exception):
    pass


def human_error(e, host):
    text = str(e)
    for needle, fix in (('Authentication failed', "The computer turned the login away. Add the app's key to it, or check the password."),
                        ('No route to host', "Can't reach %s. Is it on, and on this network?" % host),
                        ('timed out', "Can't reach %s. Is it on, and on this network?" % host),
                        ('Connection refused', 'Nothing is answering SSH on %s. Turn SSH on (on a Mac: Remote Login).' % host),
                        ('Name or service not known', "The phone can't find a computer called %s. Try its IP address." % host),
                        ('python3: not found', 'python3 isn\'t installed on %s.' % host),
                        ('command not found', 'python3 isn\'t installed on %s.' % host)):
        if needle.lower() in text.lower():
            return fix
    return text or e.__class__.__name__


class Store:
    """The app's own files: its SSH key, known host keys, and the last scan."""

    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def path(self, name):
        return os.path.join(self.folder, name)

    def read_json(self, name, default=None):
        try:
            with open(self.path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def write_json(self, name, obj):
        tmp = self.path(name) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(obj, f)
        os.replace(tmp, self.path(name))

    def key(self):
        """The app's private key (made on first use) and its public line."""
        import paramiko
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        p = self.path('id_ed25519')
        if not os.path.exists(p):
            pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(pem)
        key = paramiko.Ed25519Key.from_private_key_file(p)
        return key, '%s %s dc-ohair-app' % (key.get_name(), key.get_base64())


def city_from(name, note, out):
    """One device's scan result, shaped like a hub's /api/city."""
    drives = [dict(d, source='live', when=out.get('scanned')) for d in out.get('drives', []) if 'tree' in d]
    city = {'generated': out.get('scanned'), 'alert_percent': 90, 'ui': {'units': 'binary'},
            'devices': [{'id': 'this', 'name': name, 'note': note, 'drives': drives}]}
    city['alerts'] = []
    for d in drives:
        fs = d.get('fs') or {}
        if fs.get('total') and fs['used'] / fs['total'] >= 0.9:
            city['alerts'].append({'kind': 'full', 'level': 'critical' if fs['used'] / fs['total'] >= 0.98 else 'warning',
                                   'device': name, 'drive': d['name'],
                                   'msg': '%s / %s is %d%% full' % (name, d['name'], round(100 * fs['used'] / fs['total']))})
    return city


class Single:
    """Looks like a hub to the app (me, city, scan, status) but scans by itself."""

    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        self.url = 'single'
        self.session = ''
        self.password = None
        self.running = False
        self.error = None

    @property
    def is_phone(self):
        return self.cfg.get('kind') == 'phone'

    def me(self):
        return {'auth': 'off', 'admin': True, 'single': True}

    def city(self):
        city = self.store.read_json('last-scan.json')
        if city is None:
            self.scan_now()
            city = self.store.read_json('last-scan.json')
        return city

    def status(self):
        return {'running': self.running, 'error': self.error}

    def scan(self, devices=None):
        def run():
            try:
                self.scan_now()
            except (ScanError, NeedsPassword, UnknownHost):
                pass  # kept in self.error for the app to show
        self.running = True
        threading.Thread(target=run, daemon=True).start()
        return {'started': True, 'running': True}

    def scan_now(self):
        self.running, self.error = True, None
        try:
            out = self.run_phone() if self.is_phone else self.run_ssh(self.spec())
            self.store.write_json('last-scan.json', city_from(self.cfg.get('name') or 'This device', self.cfg.get('note', ''), out))
            return True
        except (ScanError, NeedsPassword, UnknownHost) as e:
            self.error = e
            raise
        finally:
            self.running = False

    def spec(self, mode=None):
        if mode:
            return {'mode': mode}
        drives = [{'name': p.rstrip('/').split('/')[-1] or self.cfg.get('name') or 'Root', 'path': p} for p in self.cfg.get('paths') or ['/']]
        return {'min': 50 * 1024 * 1024, 'depth': 12, 'drives': drives}

    # ----- over SSH -----

    def run_ssh(self, spec):
        import paramiko
        host, port, user = self.cfg['host'], int(self.cfg.get('port') or 22), self.cfg.get('user') or None
        known = self.store.read_json('known_hosts.json', {})
        transport = None
        try:
            sock = socket.create_connection((host, port), timeout=10)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=15)
            hk = transport.get_remote_server_key()
            fp = '%s %s' % (hk.get_name(), hk.get_base64())
            name = '%s:%d' % (host, port)
            if known.get(name) != fp:
                if self.cfg.get('accept_host') == fp:
                    known[name] = fp
                    self.store.write_json('known_hosts.json', known)
                else:
                    raise UnknownHost(host, '%s SHA256:%s' % (hk.get_name(), base64.b64encode(
                        hashlib.sha256(hk.asbytes()).digest()).decode().rstrip('=')), fp, name in known)
            key, _ = self.store.key()
            try:
                transport.auth_publickey(user, key)
            except paramiko.AuthenticationException:
                if not self.password:
                    raise NeedsPassword()
                transport.auth_password(user, self.password)
            arg = base64.b64encode(json.dumps(spec).encode()).decode()
            chan = transport.open_session(timeout=15)
            chan.exec_command('python3 - ' + arg)
            with open(AGENT, 'rb') as f:
                chan.sendall(f.read())
            chan.shutdown_write()
            out, err = io.BytesIO(), io.BytesIO()
            while True:
                if chan.recv_ready():
                    out.write(chan.recv(65536))
                if chan.recv_stderr_ready():
                    err.write(chan.recv_stderr(65536))
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                time.sleep(0.05)
            try:
                return json.loads(out.getvalue())
            except ValueError:
                raise ScanError(human_error(Exception(err.getvalue().decode(errors='replace')), host))
        except (UnknownHost, NeedsPassword, ScanError):
            raise
        except paramiko.AuthenticationException:
            raise ScanError(human_error(Exception('Authentication failed'), host))
        except (OSError, paramiko.SSHException) as e:
            raise ScanError(human_error(e, host))
        finally:
            self.password = None  # a typed password is used for one scan only
            if transport is not None:
                transport.close()

    def mounts(self):
        """The remote computer's real drives, for picking paths."""
        return self.run_ssh({'mode': 'mounts'}).get('mounts', [])

    # ----- this phone -----

    def run_phone(self, storage='/storage'):
        import sys
        argv, sys.argv = sys.argv, ['agent']  # the scanner reads its spec from argv on import
        try:
            from shared import agent
        finally:
            sys.argv = argv
        main = storage + '/emulated/0'
        roots = [main]
        try:
            roots += [storage + '/' + d for d in sorted(os.listdir(storage)) if d not in ('emulated', 'self') and os.path.isdir(storage + '/' + d)]
        except OSError:
            pass
        agent.spec = {'drives': [{'name': 'Phone storage' if r == main else 'Card ' + r.rsplit('/', 1)[-1], 'path': r}
                                 for r in roots if os.access(r, os.R_OK)]}
        if not agent.spec['drives']:
            raise ScanError("The app can't read the phone's storage yet. Allow All files access for D.C. O'HAIR in Android's settings.")
        agent.seen.clear()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                agent.main()
            return json.loads(buf.getvalue())
        except (OSError, ValueError) as e:
            raise ScanError('The scan stopped: %s' % e)
