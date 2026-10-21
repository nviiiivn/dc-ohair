"""Tests for the secret finder. Run: python3 -m unittest

No real-looking secret is written in this file: each is put together when the
test runs, so the repo's own secret scrub (and gitleaks) has nothing to find.
"""
import base64, json, os, random, string, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DC_DATA', tempfile.mkdtemp(prefix='dc-ohair-test-'))

import collect  # noqa: E402
import demo  # noqa: E402
import hub  # noqa: E402
from toolbox import api, secret_rules  # noqa: E402

AGENT = os.path.join(ROOT, 'toolbox', 'tools_agent.py')
r = random.Random(5)


def rand(n, chars=string.ascii_letters + string.digits):
    return ''.join(r.choice(chars) for _ in range(n))


def search(roots, **extra):
    spec = dict(secret_rules.spec(roots), **extra)
    arg = base64.b64encode(json.dumps(spec).encode()).decode()
    out = subprocess.run([sys.executable, AGENT, arg], stdout=subprocess.PIPE, check=True).stdout.decode()
    return out, json.loads(out)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    return path


class SecretsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.gh = 'gh' + 'p_' + rand(36)
        self.aws = 'AK' + 'IA' + rand(16, string.ascii_uppercase + '234567')
        self.pw = rand(24)
        write(os.path.join(d, 'proj', 'deploy.sh'), 'echo hi\nexport GITHUB_TOKEN=%s\nAWS_KEY=%s\n' % (self.gh, self.aws))
        write(os.path.join(d, 'proj', 'copy.txt'), 'old token %s\n' % self.gh)
        write(os.path.join(d, 'proj', 'settings.py'), 'pass' + 'word = "change' + 'me"\napi_key = "%s"\n' % self.pw)
        write(os.path.join(d, 'proj', 'db.txt'), 'postgres://sam:%s@db.local/shop\n' % rand(14))
        write(os.path.join(d, 'proj', '.env'), 'NOTHING=here\n')
        write(os.path.join(d, 'proj', '.env.example'), 'TOKEN=\n')
        write(os.path.join(d, 'home', '.ssh', 'id_ed25519'), '-----BEGIN OPENSSH ' + 'PRIVATE KEY-----\n%s\n-----END OPENSSH ' % rand(200) + 'PRIVATE KEY-----\n')
        write(os.path.join(d, 'proj', 'node_modules', 'pkg', 'x.js'), 'token=%s\n' % ('gh' + 'p_' + rand(36)))
        with open(os.path.join(d, 'proj', 'blob.dat'), 'wb') as f:
            f.write(b'\0' * 10 + self.gh.encode())

    def tearDown(self):
        self.tmp.cleanup()

    def found(self):
        out, res = search([self.tmp.name])
        return out, {(os.path.relpath(f['path'], self.tmp.name), f['rule']): f for f in res['findings']}

    def test_it_finds_keys_by_content_and_by_name(self):
        _, f = self.found()
        self.assertIn(('proj/deploy.sh', 'github-pat'), f)
        self.assertEqual(f[('proj/deploy.sh', 'github-pat')]['line'], 2)
        self.assertIn(('proj/deploy.sh', 'aws-access-token'), f)
        self.assertIn(('proj/settings.py', 'generic-api-key'), f)
        self.assertIn(('proj/db.txt', 'url-password'), f)
        self.assertIn(('home/.ssh/id_ed25519', 'ssh-private-key-file'), f)
        self.assertIn(('home/.ssh/id_ed25519', 'private-key'), f)
        self.assertIn(('proj/.env', 'env-file'), f)
        self.assertNotIn(('proj/.env.example', 'env-file'), f)

    def test_the_secret_itself_never_leaves_the_device(self):
        out, _ = self.found()
        for value in (self.gh, self.aws, self.pw):
            self.assertNotIn(value, out)

    def test_one_key_in_two_files_has_one_fingerprint(self):
        _, f = self.found()
        self.assertEqual(f[('proj/deploy.sh', 'github-pat')]['fp'], f[('proj/copy.txt', 'github-pat')]['fp'])

    def test_examples_dependencies_and_binaries_are_not_findings(self):
        _, f = self.found()
        self.assertFalse([k for k in f if k[0] == 'proj/settings.py' and 'changeme' in str(f[k])])
        self.assertEqual(sum(1 for k in f if k[0] == 'proj/settings.py'), 1)     # the api_key, not "changeme"
        self.assertFalse([k for k in f if 'node_modules' in k[0] or k[0] == 'proj/blob.dat'])


class GuardTest(unittest.TestCase):
    def request(self, local, auth='off'):
        d = demo.build()
        cfg = collect.validate(d['config'])
        cfg['hub']['auth'] = auth

        class Request:
            hub = type('H', (), {'auth': auth, 'demo': None, 'cfg': cfg})()
            sent = {}

            def is_local(self):
                return local

            def send_json(self, obj, code=200):
                self.sent.update(obj=obj, code=code)
        return Request()

    def test_with_no_login_only_the_hubs_own_browser_sees_secrets(self):
        far = self.request(local=False)
        api.get(far, '/api/tools/secrets', '')
        self.assertEqual(far.sent['code'], 403)
        api.post(far, '/api/tools/secrets/scan', {})
        self.assertEqual(far.sent['code'], 403)
        near = self.request(local=True)
        api.get(near, '/api/tools/secrets', '')
        self.assertEqual(near.sent['code'], 200)

    def test_with_a_login_on_any_logged_in_browser_may(self):
        req = self.request(local=False, auth='password')
        api.get(req, '/api/tools/secrets', '')
        self.assertEqual(req.sent['code'], 200)


if __name__ == '__main__':
    unittest.main()
