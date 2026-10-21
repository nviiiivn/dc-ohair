"""D.C. O'HAIR collector: runs agent.py on each device and saves the results.

Read-only on every device: the agent only lists and stats files. Remote
devices are reached over ssh with keys (the hub's own key in ~/.config/dc-ohair/ssh, plus
whatever keys the hub's user already has). A device can instead set
password_cmd, a command that prints the ssh password at scan time (a
password-manager lookup, say); the password goes to sshpass through the
environment and is never written anywhere.

    python3 collect.py              # scan every device in devices.yml
    python3 collect.py nas laptop   # just these
"""
import base64, hashlib, json, os, re, socket, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, 'agent.py')
DATA = os.environ.get('DC_DATA', os.path.join(HERE, 'data'))
CONFIG = os.environ.get('DC_CONFIG', os.path.join(HERE, 'devices.yml'))
# the hub's private key lives outside the code folder so it can't end up in a commit
SSH_DIR = os.environ.get('DC_SSH_DIR', os.path.join(os.path.expanduser('~'), '.config', 'dc-ohair', 'ssh'))
DEFAULTS = {
    'hub': {'port': 7171, 'bind': '0.0.0.0', 'schedule': ['04:30'], 'alert_percent': 90, 'alert_webhook': '',
            'history_days': 400, 'theme': {}, 'ui': {}},
    'scan': {'min_size': '50M', 'depth': 12, 'timeout_hours': 4, 'categories': {}},
}
SAFE_ID = re.compile(r'^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$')
lock = threading.Lock()


def parse_size(v):
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r'\s*([\d.]+)\s*([KMGT]?)i?B?\s*', str(v), re.I)
    if not m:
        raise ValueError('bad size: %r' % v)
    return int(float(m.group(1)) * 1024 ** ' KMGT'.index(m.group(2).upper() or ' '))


def ssh_target(dev):
    if dev.get('ssh'):
        return dev['ssh']
    return '%s@%s' % (dev['user'], dev['host']) if dev.get('user') else dev.get('host', '')


def validate(cfg):
    """Fills in defaults and checks the shape; raises ValueError with a readable message."""
    if isinstance(cfg, list):
        cfg = {'devices': cfg}
    if not isinstance(cfg, dict):
        raise ValueError('the config must be a mapping with hub, scan and devices')
    for part, defaults in DEFAULTS.items():
        cfg[part] = dict(defaults, **(cfg.get(part) or {}))
    # YAML 1.1 reads a bare off/on as false/true
    auth = cfg['hub'].get('auth', 'off')
    cfg['hub']['auth'] = {False: 'off', None: 'off', True: 'password'}.get(auth, auth) if not isinstance(auth, str) else auth
    if cfg['hub']['auth'] not in ('off', 'password', 'proxy'):
        raise ValueError('hub.auth must be off, password or proxy')
    if isinstance(cfg['hub']['schedule'], (str, int)):
        cfg['hub']['schedule'] = [cfg['hub']['schedule']]
    parse_size(cfg['scan']['min_size'])
    seen = set()
    for dev in cfg.setdefault('devices', []) or []:
        dev_id = str(dev.get('id', ''))
        if not SAFE_ID.match(dev_id) or dev_id in seen:
            raise ValueError('every device needs a unique id made of letters, digits, - _ or . (got %r)' % dev_id)
        seen.add(dev_id)
        dev.setdefault('name', dev_id)
        if not dev.get('local') and not ssh_target(dev):
            raise ValueError('device %s needs either local: true or a host to ssh to' % dev_id)
        for d in dev.setdefault('drives', []) or []:
            if not d.get('name') or not str(d.get('path', '')).startswith('/'):
                raise ValueError('every drive on %s needs a name and an absolute path' % dev_id)
    return cfg


def load_config(path=None):
    path = path or CONFIG
    with open(path) as f:
        text = f.read()
    if path.endswith('.json'):
        cfg = json.loads(text)
    else:
        import yaml
        cfg = yaml.safe_load(text) or {}
    return validate(cfg)


def save_config(cfg, path=None):
    """Writes the config readable only by its owner; the old file is kept as .bak."""
    import yaml
    path = path or CONFIG
    validate(cfg)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write("# D.C. O'HAIR config. Edited by the settings page; keep it out of git.\n")
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    if os.path.exists(path):
        os.replace(path, path + '.bak')
    os.replace(tmp, path)


def save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, separators=(',', ':'))
    os.replace(tmp, path)


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------- the hub's own ssh key ----------

def hub_key():
    """Path of the hub's private key, made on first use."""
    folder = SSH_DIR
    key = os.path.join(folder, 'id_ed25519')
    if not os.path.exists(key):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'dc-ohair@' + socket.gethostname(), '-f', key],
                       check=True, stdout=subprocess.DEVNULL)
    return key


def hub_pubkey():
    with open(hub_key() + '.pub') as f:
        return f.read().strip()


def agent_sha():
    with open(AGENT, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def restricted_key_line():
    """An authorized_keys line that lets the hub's key run this exact agent.py and nothing else."""
    code = ("import sys,hashlib;c=sys.stdin.buffer.read();h=hashlib.sha256(c).hexdigest();"
            "sys.exit('dc-ohair: agent '+h[:12]+' is not the one this key allows') if h!='%s' else None;"
            "sys.argv=['agent',sys.argv[-1].split()[-1]];exec(compile(c,'agent.py','exec'))") % agent_sha()
    opts = 'command="python3 -c \\"%s\\" $SSH_ORIGINAL_COMMAND",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty' % code
    return opts + ' ' + hub_pubkey()


# ---------- running the agent ----------

FIXES = [
    (r'REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed',
     "This device's SSH identity changed since the hub last saw it (reinstalled, or booted into another OS). "
     "If that's expected, forget the old one on the hub with: ssh-keygen -R {host}"),
    (r'Permission denied \(publickey\)|Permission denied \(publickey,',
     "The device turned the hub's key away. Add the hub's key to the device (the ssh-copy-id or authorized_keys "
     "line in Settings → Devices), or choose password_cmd."),
    (r'Permission denied, please try again|Permission denied \(password',
     "The password from password_cmd was refused. Check what the command prints."),
    (r'Connection refused', "Nothing is answering SSH on {host}. Turn SSH on (on a Mac: System Settings → General → Sharing → Remote Login)."),
    (r'Could not resolve hostname', "The hub can't find a device called {host}. Try its IP address."),
    (r'timed out|No route to host|Network is unreachable|Host is down',
     "Can't reach {host}. Is it switched on, awake, and on this network?"),
    (r'xcrun: error|xcode-select', "This Mac needs Apple's Command Line Tools for python3. On the Mac run: xcode-select --install"),
    (r'python3: (command )?not found|python3: No such file|No such file or directory.*python3|exec: python3',
     "python3 isn't installed on {host}. Install it with the device's package manager, then test again."),
    (r'sshpass: (command )?not found|No such file or directory: .sshpass.',
     "password_cmd needs sshpass on the hub. Install it (apt install sshpass), or use a key instead."),
    (r'is not the one this key allows', "The device only accepts an older agent. Replace its authorized_keys line with the new one from Settings."),
]


def explain(stderr, dev):
    text = stderr.strip()
    host = dev.get('host') or ssh_target(dev).split('@')[-1]
    for pattern, fix in FIXES:
        if re.search(pattern, text, re.I):
            return fix.format(host=host)
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith('Warning: Permanently added')]
    return lines[-1] if lines else 'No answer from the device.'


def password(dev):
    cmd = dev['password_cmd']
    p = subprocess.run(cmd, shell=isinstance(cmd, str), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=60, universal_newlines=True)
    return p.stdout.strip()


def run_agent(dev, spec, timeout):
    """Runs agent.py with spec on dev. Returns (parsed JSON, None) or (None, plain-language error)."""
    arg = base64.b64encode(json.dumps(spec).encode()).decode()
    env = None
    if dev.get('local'):
        cmd, stdin = [sys.executable, AGENT, arg], subprocess.DEVNULL
    else:
        cmd = ['ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=30']
        if os.path.exists(os.path.join(SSH_DIR, 'id_ed25519')):
            cmd += ['-i', os.path.join(SSH_DIR, 'id_ed25519')]
        if dev.get('port'):
            cmd += ['-p', str(dev['port'])]
        # base64 keeps the argument safe in fish as well as sh; command_prefix wraps
        # the agent, e.g. "caffeinate -i" keeps a Mac awake, "nice -n 19" keeps it polite
        cmd += [ssh_target(dev), ' '.join(filter(None, [dev.get('command_prefix'), 'python3 -', arg]))]
        if dev.get('password_cmd'):
            try:
                secret = password(dev)
            except (OSError, subprocess.SubprocessError):
                secret = ''
            if not secret:
                return None, 'password_cmd printed nothing.'
            env = dict(os.environ, SSHPASS=secret)
            cmd = ['sshpass', '-e'] + cmd
        else:
            cmd.insert(1, '-oBatchMode=yes')
        stdin = open(AGENT, 'rb')
    try:
        p = subprocess.run(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, 'Gave up after %d minutes.' % (timeout // 60)
    except OSError as e:
        return None, explain(str(e), dev)
    finally:
        if stdin is not subprocess.DEVNULL:
            stdin.close()
    try:
        return json.loads(p.stdout), None
    except ValueError:
        return None, explain(p.stderr.decode(errors='replace'), dev)


def category_defaults():
    """The agent's built-in category rules, read from agent.py without running it."""
    import ast
    with open(AGENT) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'DEFAULT_CATEGORIES':
            return ast.literal_eval(node.value)
    return {}


def scan_spec(cfg, drives):
    scan = cfg['scan']
    return {'min': parse_size(scan['min_size']), 'depth': int(scan['depth']), 'drives': drives,
            'categories': scan.get('categories') or {}}


# ---------- scans ----------

progress = {}


def set_status(dev_id, ok, msg):
    path = os.path.join(DATA, 'status.json')
    with lock:
        status = load(path, {})
        status[dev_id] = {'ok': ok, 'when': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'msg': msg}
        save(path, status)
        progress[dev_id] = {'state': 'done' if ok else 'failed', 'msg': msg}


def record_history(dev_id, out, keep_days):
    """One line per drive per scan in data/history/<id>.jsonl, oldest pruned."""
    folder = os.path.join(DATA, 'history')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, dev_id + '.jsonl')
    now = int(time.time())
    cutoff = now - keep_days * 86400
    lines = []
    try:
        with open(path) as f:
            lines = [l for l in f if l.strip() and json.loads(l)['t'] >= cutoff]
    except (OSError, ValueError, KeyError):
        pass
    for d in out['drives']:
        fs = d.get('fs')
        if fs and 'tree' in d:
            lines.append(json.dumps({'t': now, 'd': d['name'], 'used': fs['used'], 'free': fs['free'],
                                     'total': fs['total'], 'c': d['tree']['c']}, separators=(',', ':')) + '\n')
    with lock:
        with open(path + '.tmp', 'w') as f:
            f.writelines(lines)
        os.replace(path + '.tmp', path)


def collect(dev, cfg):
    progress[dev['id']] = {'state': 'scanning', 'since': time.time()}
    out, err = run_agent(dev, scan_spec(cfg, dev['drives']), float(cfg['scan']['timeout_hours']) * 3600)
    if err:
        return set_status(dev['id'], False, err)
    save(os.path.join(DATA, dev['id'] + '.json'), out)
    record_history(dev['id'], out, int(cfg['hub']['history_days']))
    bad = [d['name'] + ': ' + d['error'] for d in out['drives'] if 'error' in d]
    set_status(dev['id'], not bad, '; '.join(bad) or 'scanned in %ss' % out['secs'])


def collect_all(cfg, only=None):
    os.makedirs(DATA, exist_ok=True)
    devices = [d for d in cfg['devices'] if (not only or d['id'] in only) and d.get('drives')]
    for d in devices:
        progress[d['id']] = {'state': 'waiting'}
    threads = [threading.Thread(target=collect, args=(d, cfg)) for d in devices]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == '__main__':
    collect_all(load_config(), set(sys.argv[1:]))
