"""Tests for the phone app's single-device mode (app/src/single.py)."""
import os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app', 'src'))
subprocess.run([sys.executable, os.path.join(ROOT, 'app', 'sync.py')], check=True, stdout=subprocess.DEVNULL)

import model  # noqa: E402
import single  # noqa: E402

MiB = 1024 * 1024

try:
    import paramiko  # noqa: F401
    HAVE_SSH = True
except ImportError:
    HAVE_SSH = False


class SingleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = single.Store(os.path.join(self.tmp, 'app'))

    def touch(self, path, size):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.truncate(size)

    def test_scan_this_phone(self):
        storage = os.path.join(self.tmp, 'storage')
        self.touch(os.path.join(storage, 'emulated', '0', 'Movies', 'film.mkv'), 200 * MiB)
        self.touch(os.path.join(storage, 'ABCD-1234', 'Music', 'album.flac'), 60 * MiB)
        os.makedirs(os.path.join(storage, 'self'))
        out = single.Single(self.store, {'kind': 'phone'}).run_phone(storage)
        city = single.city_from('My phone', '', out)
        top = model.build(city)
        phone = top.k[0]
        self.assertEqual(phone.n, 'My phone')
        self.assertEqual([d.n for d in phone.k], ['Phone storage', 'Card ABCD-1234'])
        self.assertGreaterEqual(phone.k[0].c[model.CATS.index('Movies')], 200 * MiB)
        self.assertGreaterEqual(phone.k[1].c[model.CATS.index('Music')], 60 * MiB)

    def test_unreadable_phone_says_what_to_do(self):
        with self.assertRaises(single.ScanError) as e:
            single.Single(self.store, {'kind': 'phone'}).run_phone(os.path.join(self.tmp, 'nothing'))
        self.assertIn('All files access', str(e.exception))

    def test_full_drive_alert(self):
        out = {'scanned': '2026-01-01T00:00:00Z', 'drives': [
            {'name': 'data', 'fs': {'used': 95, 'free': 5, 'total': 100}, 'tree': {'n': 'data', 'c': [0, 0, 0, 0, 95]}},
            {'name': 'gone', 'error': 'not mounted'}]}
        city = single.city_from('Box', '', out)
        self.assertEqual([d['name'] for d in city['devices'][0]['drives']], ['data'])
        self.assertEqual(city['alerts'][0]['level'], 'warning')

    def test_paths_become_drives(self):
        s = single.Single(self.store, {'name': 'Box', 'paths': ['/', '/mnt/media/']})
        self.assertEqual([d['name'] for d in s.spec()['drives']], ['Box', 'media'])

    def test_plain_errors(self):
        self.assertIn('Remote Login', single.human_error(OSError('[Errno 111] Connection refused'), 'mac'))
        self.assertIn("Can't reach mac", single.human_error(OSError('timed out'), 'mac'))

    def test_store_round_trip(self):
        self.assertEqual(self.store.read_json('missing.json', {}), {})
        self.store.write_json('a.json', {'x': 1})
        self.assertEqual(self.store.read_json('a.json'), {'x': 1})

    @unittest.skipUnless(HAVE_SSH, 'paramiko not installed')
    def test_app_key_is_made_once_and_kept_private(self):
        key, line = self.store.key()
        again, line2 = self.store.key()
        self.assertEqual(line, line2)
        self.assertTrue(line.startswith('ssh-ed25519 ') and line.endswith(' dc-ohair-app'))
        self.assertEqual(os.stat(self.store.path('id_ed25519')).st_mode & 0o777, 0o600)

    @unittest.skipUnless(HAVE_SSH, 'paramiko not installed')
    def test_nobody_answering(self):
        s = single.Single(self.store, {'host': '127.0.0.1', 'port': 9, 'paths': ['/']})
        s.password = 'typed'
        with self.assertRaises(single.ScanError):
            s.scan_now()
        self.assertIsNone(s.password)
        self.assertFalse(s.running)


if __name__ == '__main__':
    unittest.main()
