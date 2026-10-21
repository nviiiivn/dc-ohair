"""The toolbox's long jobs, one at a time, in a thread of the hub.

A job goes device by device (so no two devices are read at once, and the hub
stays light) and keeps its progress where the page can poll it. Two jobs:

- Checking duplicates: the tools agent reads the start and end of every file
  in a same-size set ("partial"), or, if she asks, every byte ("full"); the
  answers are kept in data/toolbox/hashes.json for the next report.
- The secret finder: the tools agent searches each device's drives for keys
  and passwords (secret_rules.py) and sends back where they are, never what
  they are; kept in data/toolbox/secrets.json.
"""
import json, os, threading, time

import collect
from . import dupes, secret_rules

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_AGENT = os.path.join(HERE, 'tools_agent.py')
FULL_BUDGET = 200 * 1024 ** 3      # bytes one device reads in one full check; the rest waits for the next
lock = threading.Lock()
state = {'running': False}
DEMO_STORE = {}                     # demo mode's pretend hashes: never written to disk


def store_path(name='hashes.json'):
    from .api import TOOLS
    return os.path.join(TOOLS, name)


def load_store(name='hashes.json'):
    try:
        with open(store_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_store(store, name='hashes.json'):
    path = store_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(store, f)
    os.replace(tmp, path)


def current(store, f):
    """The stored hashes for a scanned file, if they are still about the same bytes."""
    rec = store.get('%s\t%s' % (f['device'], f['path']))
    if not rec or rec.get('size') != f['size'] or (f.get('mtime') or 0) > rec.get('mtime', 0) + 1:
        return None
    return rec


def todo(report, store, full):
    """device id -> [[path, size], ...] still to read for this check."""
    out = {}
    for g in report['bytes']:
        if g['sure'] == 'hash':
            continue
        for f in g['files']:
            rec = current(store, f)
            if rec and (rec.get('full') if full else rec.get('partial')):
                continue
            out.setdefault(f['device'], []).append([f['path'], f['size']])
    return out


def status():
    with lock:
        return json.loads(json.dumps(state))


def start_check(hub, full=False):
    """Start a duplicate check in the background. False if a job is already running."""
    with lock:
        if state.get('running'):
            return False
        state.clear()
        state.update(running=True, kind='full' if full else 'partial', started=time.time(), devices={})
    threading.Thread(target=_check, args=(hub, full), daemon=True).start()
    return True


def _check(hub, full):
    try:
        city = hub.city()
        store = DEMO_STORE if hub.demo else load_store()
        report = dupes.report(city, 0, store_hashes(store))
        work = todo(report, store, full)
        devs = {d['id']: d for d in hub.cfg['devices']}
        with lock:
            state['devices'] = {i: {'files': len(fs), 'state': 'waiting'} for i, fs in work.items()}
        for dev_id, files in work.items():
            with lock:
                state['devices'][dev_id]['state'] = 'reading'
            if hub.demo:
                out, err = _pretend(report, dev_id, files, full), None
                time.sleep(0.6)
            elif dev_id not in devs:
                out, err = None, 'This device is no longer in the config.'
            else:
                spec = {'mode': 'hash', 'files': files, 'full': full, 'budget': FULL_BUDGET}
                hours = hub.cfg['scan'].get('timeout_hours', 4)
                out, err = collect.run_agent(devs[dev_id], spec, int(hours * 3600), script=TOOLS_AGENT)
                if err and 'older agent' in err:
                    err = ("This device's key lets the hub run only the scanner, which never reads a file. To let the "
                           "toolbox check files here too, add the tools line (Tools → How to allow it) to the device.")
            done = 0
            if out and 'hashes' in out:
                for path, rec in out['hashes'].items():
                    if rec.get('partial'):
                        key = '%s\t%s' % (dev_id, path)
                        size = next((s for p, s in files if p == path), 0)
                        keep = store.get(key) if (store.get(key) or {}).get('size') == size else {}
                        store[key] = dict(keep or {}, size=size, **{k: v for k, v in rec.items() if k != 'skip'},
                                          when=int(time.time()))
                        done += 1
                if not hub.demo:
                    save_store(store)
            with lock:
                state['devices'][dev_id].update(state='done' if not err else 'failed', read=done,
                                                error=err or (out or {}).get('error'))
    except Exception as e:  # a job must never take the hub down
        with lock:
            state['error'] = str(e)
    finally:
        with lock:
            state.update(running=False, finished=time.time())


def hashes_for(hub):
    """(device, path) -> hashes, for this hub: the demo's in memory, else data/toolbox/hashes.json."""
    return store_hashes(DEMO_STORE if hub.demo else load_store())


def store_hashes(store):
    """The store in the shape dupes.same_size takes: (device, path) -> hashes."""
    return {tuple(k.split('\t', 1)): v for k, v in store.items() if '\t' in k}


def _pretend(report, dev_id, files, full):
    """Demo mode: a same-name set reads as identical, any other as different."""
    names = {}
    for g in report['bytes']:
        for f in g['files']:
            names[(f['device'], f['path'])] = (g['sure'], g['key'])
    out = {}
    for path, size in files:
        sure, key = names.get((dev_id, path), ('size', size))
        tag = 'same-%s' % key if sure == 'name' else 'own-%s-%s' % (dev_id, path)
        out[path] = {'partial': 'p' + tag, 'mtime': int(time.time())}
        if full:
            out[path]['full'] = 'f' + tag
    return {'hashes': out}


# ----- the secret finder -----

DEMO_SECRETS = {}


def secrets_for(hub):
    return DEMO_SECRETS if hub.demo else load_store('secrets.json')


def start_secrets(hub, only=None):
    """Search every device's drives (or [only]) for secrets in the background. False if a job is running."""
    with lock:
        if state.get('running'):
            return False
        state.clear()
        state.update(running=True, kind='secrets', started=time.time(), devices={})
    threading.Thread(target=_secrets, args=(hub, only), daemon=True).start()
    return True


def _secrets(hub, only):
    try:
        devs = [d for d in hub.cfg['devices'] if not only or d['id'] in only]
        store = secrets_for(hub)
        with lock:
            state['devices'] = {d['id']: {'state': 'waiting'} for d in devs}
        for dev in devs:
            with lock:
                state['devices'][dev['id']]['state'] = 'reading'
            roots = [dr['path'] for dr in dev.get('drives', []) if dr.get('path')]
            exclude = [x for dr in dev.get('drives', []) for x in dr.get('exclude') or []]
            if hub.demo:
                time.sleep(0.6)
                out, err = _pretend_secrets(dev['id'], roots), None
            else:
                hours = hub.cfg['scan'].get('timeout_hours', 4)
                out, err = collect.run_agent(dev, secret_rules.spec(roots, exclude), int(hours * 3600), script=TOOLS_AGENT)
                if err and 'older agent' in err:
                    err = ("This device's key lets the hub run only the scanner, which never reads a file. To let the "
                           "toolbox look here too, add the tools line (Tools → How to allow it) to the device.")
            rec = {'when': int(time.time()), 'roots': roots, 'error': err or (out or {}).get('error')}
            if out and 'findings' in out:
                rec.update(findings=out['findings'], files=out.get('files', 0), unread=out.get('unread', 0),
                           stopped=out.get('stopped'))
            elif dev['id'] in store and store[dev['id']].get('findings') is not None:
                rec['findings'] = store[dev['id']]['findings']      # keep the last good answer
                rec['stale'] = True
            store[dev['id']] = rec
            if not hub.demo:
                save_store(store, 'secrets.json')
            with lock:
                state['devices'][dev['id']].update(state='failed' if rec['error'] else 'done',
                                                   found=len(rec.get('findings') or []), error=rec['error'])
    except Exception as e:  # a job must never take the hub down
        with lock:
            state['error'] = str(e)
    finally:
        with lock:
            state.update(running=False, finished=time.time())


def _pretend_secrets(dev_id, roots):
    """Demo mode: the kinds of thing the finder turns up, at made-up paths."""
    base = (roots or ['/home'])[0].rstrip('/')
    made = [('%s/home/sam/projects/shop/.env' % base, 'env-file', 0, 'name'),
            ('%s/home/sam/projects/shop/.env' % base, 'stripe-access-token', 4, 'content'),
            ('%s/home/sam/projects/shop/deploy.sh' % base, 'aws-access-token', 12, 'content'),
            ('%s/home/sam/.ssh/id_ed25519' % base, 'ssh-private-key-file', 0, 'name'),
            ('%s/home/sam/.ssh/id_ed25519' % base, 'private-key', 1, 'content'),
            ('%s/home/sam/notes/router.txt' % base, 'url-password', 3, 'content'),
            ('%s/home/sam/bots/discord-bot/config.json' % base, 'discord-api-token', 7, 'content')]
    pick = made[:3 + (sum(map(ord, dev_id)) % 5)]
    import hashlib
    # The same made-up key on every device, as one reused key would be.
    fp = lambda r: hashlib.sha256(r.encode()).hexdigest()[:12]
    return {'findings': [dict({'path': p, 'rule': r, 'line': n, 'by': by}, **({'fp': fp(r)} if by == 'content' else {}))
                         for p, r, n, by in pick], 'files': 1200 + 37 * len(dev_id), 'unread': 0}


# ----- empty folders and broken links -----

DEMO_TIDY = {}


def tidy_for(hub):
    return DEMO_TIDY if hub.demo else load_store('tidy.json')


def start_tidy(hub, only=None):
    """List empty folders and broken symlinks on every device's drives. False if a job is running."""
    with lock:
        if state.get('running'):
            return False
        state.clear()
        state.update(running=True, kind='tidy', started=time.time(), devices={})
    threading.Thread(target=_tidy, args=(hub, only), daemon=True).start()
    return True


def _tidy(hub, only):
    try:
        devs = [d for d in hub.cfg['devices'] if not only or d['id'] in only]
        store = tidy_for(hub)
        with lock:
            state['devices'] = {d['id']: {'state': 'waiting'} for d in devs}
        for dev in devs:
            with lock:
                state['devices'][dev['id']]['state'] = 'reading'
            roots = [dr['path'] for dr in dev.get('drives', []) if dr.get('path')]
            exclude = [x for dr in dev.get('drives', []) for x in dr.get('exclude') or []]
            rec, err = {'when': int(time.time()), 'roots': roots}, None
            for mode in ('empty', 'broken'):
                if hub.demo:
                    out = {'empty': ['%s/Downloads/old' % roots[0].rstrip('/'), '%s/Movies/Unsorted' % roots[0].rstrip('/')],
                           'broken': [['%s/home/sam/current-game' % roots[0].rstrip('/'), '/mnt/games/Old Game']]}
                else:
                    hours = hub.cfg['scan'].get('timeout_hours', 4)
                    out, err = collect.run_agent(dev, {'mode': mode, 'roots': roots, 'exclude': exclude},
                                                 int(hours * 3600), script=TOOLS_AGENT)
                if err:
                    if 'older agent' in err:
                        err = ("This device's key lets the hub run only the scanner. To let the toolbox list "
                               "folders here too, add the tools line (Tools → How to allow it) to the device.")
                    break
                rec[mode] = out.get(mode, [])
                rec[mode + '_more'] = out.get('more', 0)
            rec['error'] = err
            if err and dev['id'] in store:
                rec = dict(store[dev['id']], error=err, stale=True)
            store[dev['id']] = rec
            if not hub.demo:
                save_store(store, 'tidy.json')
            with lock:
                state['devices'][dev['id']].update(state='failed' if err else 'done', error=err,
                                                   found=len(rec.get('empty') or []) + len(rec.get('broken') or []))
    except Exception as e:  # a job must never take the hub down
        with lock:
            state['error'] = str(e)
    finally:
        with lock:
            state.update(running=False, finished=time.time())
