"""Serves the hub's own 3D city page from inside the app, so the City tab
works in demo and single-device mode too, with no hub.

It listens on 127.0.0.1 only and answers just the read-only calls the page
makes at start. Other apps on the phone can reach 127.0.0.1 as well, so
every request needs the cookie that only the app's own WebView gets: the
first URL carries a random key, which is swapped for that cookie.
"""
import http.server, json, os, secrets, threading, urllib.parse

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.json': 'application/json'}
THEMES = ('dark', 'light', 'sepia', 'cyberpunk', 'matrix', 'ocean')


class CityServer:
    def __init__(self, city, history=lambda: {}):
        """city() and history() return what the hub's /api/city and /api/history would."""
        self.city, self.history = city, history
        self.key = secrets.token_urlsafe(24)
        self.theme = 'dark'
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def head(self, code, headers=()):
                self.send_response(code)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                # lets it load inside Flet's web build too, which isolates its page (for trying the app on a computer)
                self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
                self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
                for h in headers:
                    self.send_header(*h)

            def send(self, code, body, ctype='application/json', headers=()):
                self.head(code, [('Content-Type', ctype), ('Content-Length', str(len(body)))] + list(headers))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(url.query)
                if url.path == '/' and secrets.compare_digest(query.get('key', [''])[0].encode(), server.key.encode()):
                    self.head(303, [('Location', '/?app'), ('Content-Length', '0'),
                                    ('Set-Cookie', 'dc_app=%s; Path=/; HttpOnly; SameSite=Strict' % server.key)])
                    return self.end_headers()
                cookies = dict(c.strip().split('=', 1) for c in (self.headers.get('Cookie') or '').split(';') if '=' in c)
                if not secrets.compare_digest(cookies.get('dc_app', '').encode(), server.key.encode()):
                    return self.send(403, b'{"error": "not for you"}')
                if url.path.startswith('/api/'):
                    return self.api(url.path)
                if url.path == '/theme-config.js':
                    # the page reads its theme from this cookie first, so the app's theme always wins
                    return self.send(200, b'window.DC_THEME = {cookie: "dc-app-theme", storage: "dc-app-theme"};',
                                     TYPES['.js'], [('Set-Cookie', 'dc-app-theme=%s; Path=/; SameSite=Strict' % server.theme)])
                path = os.path.normpath(os.path.join(WEB, url.path.lstrip('/') or 'index.html'))
                if not path.startswith(WEB + os.sep) or not os.path.isfile(path):
                    return self.send(404, b'{"error": "not found"}')
                with open(path, 'rb') as f:
                    self.send(200, f.read(), TYPES.get(os.path.splitext(path)[1], 'application/octet-stream'))

            def api(self, path):
                if path == '/api/me':
                    out = {'auth': 'off', 'admin': True, 'demo': True, 'setup': False}
                elif path == '/api/status':
                    out = {'running': False, 'demo': True}  # demo hides the page's Scan now; the app has its own
                elif path == '/api/city':
                    out = server.city()
                elif path == '/api/history':
                    out = server.history()
                else:
                    return self.send(404, b'{"error": "not in the app"}')
                self.send(200, json.dumps(out, default=lambda o: None).encode())  # skips the app's own node links

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        """The first URL for the WebView: it trades the key for the cookie."""
        return 'http://127.0.0.1:%d/?key=%s' % (self.httpd.server_address[1], self.key)

    def set_theme(self, name):
        self.theme = name if name in THEMES else 'dark'
