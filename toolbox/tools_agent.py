"""Disk City tools agent: the toolbox's read-only work on a device.

Like agent.py it is piped over ssh and leaves nothing behind, needs only
python3 (3.6+), and prints one JSON document:
    ssh host python3 - <base64 spec> < tools_agent.py

Unlike agent.py it reads file CONTENTS, so it has its own restricted key
line (Settings shows it): a device that only trusts the scanner's key turns
this away, and stays a device nothing reads.

spec = {"mode": "hash", "files": [[path, size], ...], "full": false, "budget": bytes}
    For each file: a hash of its first 4 KiB and last 16 KiB ("partial"),
    and with full, of every byte ("full"), the fclones order. A file whose
    size is no longer the one the scan saw, or that is not a plain file, is
    skipped. Full hashing stops once budget bytes have been read; what is
    left is reported as not done, for the next run.
spec = {"mode": "empty", "roots": [...], "exclude": [...]}    empty folders
spec = {"mode": "broken", "roots": [...], "exclude": [...]}   symlinks to nothing

Nothing is ever written, moved or deleted.
"""
import base64, fnmatch, hashlib, json, os, stat, sys, time

HEAD, TAIL, CHUNK = 4096, 16384, 1 << 20
spec = json.loads(base64.b64decode(sys.argv[1]).decode()) if len(sys.argv) > 1 else {}


def blake(data=b''):
    return hashlib.blake2b(data, digest_size=16)


def partial(f, size):
    h = blake(b'%d:' % size)
    h.update(f.read(HEAD))
    if size > HEAD + TAIL:
        f.seek(size - TAIL)
    h.update(f.read(TAIL))
    return h.hexdigest()


def full(f):
    h = blake()
    f.seek(0)
    n = 0
    while True:
        b = f.read(CHUNK)
        if not b:
            return h.hexdigest(), n
        h.update(b)
        n += len(b)


def do_hash():
    budget = spec.get('budget', 50 * 1024 ** 3)
    want_full = bool(spec.get('full'))
    out, read, t0 = {}, 0, time.time()
    for path, size in spec.get('files', []):
        rec = {}
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                rec['skip'] = 'not a plain file'
            elif st.st_size != size:
                rec['skip'] = 'changed since the scan'
            else:
                rec['mtime'] = int(st.st_mtime)
                rec['dev_ino'] = '%d:%d' % (st.st_dev, st.st_ino)
                with open(path, 'rb') as f:
                    rec['partial'] = partial(f, size)
                    if want_full:
                        if read + size > budget:
                            rec['full_later'] = True
                        else:
                            rec['full'], n = full(f)
                            read += n
        except OSError as e:
            rec['skip'] = e.strerror or str(e)
        out[path] = rec
    return {'hashes': out, 'read': read, 'secs': round(time.time() - t0, 1)}


def walk(roots, exclude):
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(os.path.join(dirpath, d), x) for x in exclude)
                       and not os.path.ismount(os.path.join(dirpath, d))]
            yield dirpath, dirs, files


def do_empty():
    found = []
    for dirpath, dirs, files in walk(spec.get('roots', []), spec.get('exclude', [])):
        if not dirs and not files and dirpath not in spec.get('roots', []):
            found.append(dirpath)
    return {'empty': found[:20000], 'more': max(0, len(found) - 20000)}


def do_broken():
    found = []
    for dirpath, dirs, files in walk(spec.get('roots', []), spec.get('exclude', [])):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) and not os.path.exists(p):
                found.append([p, os.readlink(p)])
    return {'broken': found[:20000], 'more': max(0, len(found) - 20000)}


def main():
    mode = spec.get('mode')
    fn = {'hash': do_hash, 'empty': do_empty, 'broken': do_broken}.get(mode)
    out = fn() if fn else {'error': 'unknown mode %r' % mode}
    out['tools_agent'] = 1
    json.dump(out, sys.stdout)


if __name__ == '__main__':
    main()
