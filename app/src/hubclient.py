"""Talks to a D.C. O'HAIR hub's JSON API, or stands in for one with demo data."""
import gzip, json, time, urllib.error, urllib.parse, urllib.request


class HubError(Exception):
    pass


class Hub:
    def __init__(self, url, session=''):
        url = url.strip().rstrip('/')
        if url and '://' not in url:
            url = 'http://' + url
        if url and urllib.parse.urlparse(url).port is None and url.startswith('http://'):
            url += ':7171'
        self.url, self.session = url, session

    def _call(self, path, body=None, timeout=30):
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'gzip', 'User-Agent': 'dc-ohair-app'}
        if self.session:
            headers['Cookie'] = 'dc_session=' + self.session
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({'Content-Type': 'application/json', 'X-DC': '1'})
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method='POST' if data is not None else 'GET')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
                if res.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                for cookie in res.headers.get_all('Set-Cookie') or []:
                    if cookie.startswith('dc_session='):
                        self.session = cookie.split(';')[0].split('=', 1)[1]
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get('error')
            except ValueError:
                msg = None
            if e.code == 401:
                raise HubError(msg or 'Log in first.')
            raise HubError(msg or 'The hub answered %s.' % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise HubError("Can't reach %s (%s). Is it on, and is this phone on the same network?" % (self.url, getattr(e, 'reason', e)))

    def me(self):
        return self._call('/api/me', timeout=8)

    def login(self, password):
        self._call('/api/login', {'password': password})
        return self.session

    def city(self):
        return self._call('/api/city', timeout=90)

    def alerts(self):
        return self._call('/api/alerts')

    def status(self):
        return self._call('/api/status')

    def scan(self, devices=None):
        try:
            return self._call('/api/scan', {'devices': devices} if devices else {})
        except HubError as e:
            if '409' in str(e):
                return {'started': False, 'running': True}
            raise


class Demo:
    """A made-up home network, so the app can be tried without a hub."""
    url = 'demo'
    session = ''

    def __init__(self):
        from shared import demo
        self.data = demo.build()
        self.running_until = 0

    def me(self):
        return {'auth': 'off', 'admin': True, 'demo': True, 'version': 'demo'}

    def city(self):
        city = dict(self.data['city'])
        city['ui'] = {'units': 'binary', 'health': True, 'failing_dot': True}
        city['alerts'] = []
        for dev in city['devices']:
            for d in dev['drives']:
                fs = d.get('fs') or {}
                if fs.get('total') and fs['used'] / fs['total'] >= 0.9:
                    city['alerts'].append({'kind': 'full', 'level': 'warning', 'device': dev['name'], 'drive': d['name'],
                                           'msg': '%s / %s is %d%% full' % (dev['name'], d['name'], round(100 * fs['used'] / fs['total']))})
                h = d.get('health') or {}
                if h.get('state') in ('warning', 'failing'):
                    city['alerts'].append({'kind': 'health', 'level': 'critical' if h['state'] == 'failing' else 'warning',
                                           'device': dev['name'], 'drive': d['name'], 'msg': '%s / %s health: %s' % (dev['name'], d['name'], h['state'])})
        return city

    def alerts(self):
        return self.city()['alerts']

    def status(self):
        return {'running': time.time() < self.running_until, 'demo': True}

    def scan(self, devices=None):
        self.running_until = time.time() + 4
        return {'started': True, 'running': True}
