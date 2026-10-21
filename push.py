#!/usr/bin/env python3
"""D.C. O'HAIR push agent: for devices the hub can't reach over ssh.

The device runs this on a schedule. Each run it asks the hub what to scan,
runs agent.py (read-only), and sends the result back, signed with the
device's enrollment token. Python 3.6+, standard library only; works on
Linux, macOS, Windows, LibreELEC and most NAS boxes.

    python3 dc-push.py install --hub http://hub:7171 --token TOKEN   # once
    python3 dc-push.py run                                           # what the schedule runs
    python3 dc-push.py uninstall

install keeps its files in ~/.dc-ohair (the token in push.json, readable only
by you), runs once, and adds a daily schedule: a systemd timer, cron, launchd
on macOS, or Task Scheduler on Windows. --no-schedule skips that part.
"""
import argparse, base64, gzip, json, os, platform, shutil, subprocess, sys, urllib.error, urllib.request

HOME = os.path.join(os.path.expanduser('~'), '.dc-ohair')
CONF = os.path.join(HOME, 'push.json')
AGENT = os.path.join(HOME, 'agent.py')
SELF = os.path.join(HOME, 'dc-push.py')
LABEL = 'dc-ohair-push'
MARK = '# dc-ohair push'


def say(*a):
    print('[dc-ohair]', *a, flush=True)


def request(conf, path, body=None, gz=False):
    headers = {'Authorization': 'Bearer ' + conf['token'], 'User-Agent': 'dc-ohair-push'}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(',', ':')).encode()
        headers['Content-Type'] = 'application/json'
        if gz:
            data = gzip.compress(data, 6)
            headers['Content-Encoding'] = 'gzip'
    req = urllib.request.Request(conf['hub'].rstrip('/') + path, data=data, headers=headers, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=120) as res:
        raw = res.read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else raw


def run_agent(spec):
    arg = base64.b64encode(json.dumps(spec).encode()).decode()
    p = subprocess.run([sys.executable, AGENT, arg], stdout=subprocess.PIPE)
    return json.loads(p.stdout)


def run(conf):
    # always use the hub's current agent, so hub and device never drift apart
    with open(AGENT, 'wb') as f:
        f.write(request(conf, '/agent.py'))
    spec = request(conf, '/api/push/spec')
    if spec.get('drives') == 'auto':
        found = run_agent({'mode': 'mounts'})
        host = found.get('host') or platform.node()
        spec['drives'] = [{'name': host if m['path'] in ('/', 'C:\\') else m['label'], 'path': m['path'],
                           **({} if m['path'] in ('/', 'C:\\') else {'mounted': True})} for m in found['mounts']]
    say('scanning', ', '.join(d['path'] for d in spec['drives']) or 'nothing (no drives picked yet)')
    out = run_agent(spec)
    answer = request(conf, '/api/report', out, gz=True)
    say('sent to the hub:', answer.get('msg', 'ok'))


# ---------- schedules ----------

def schedule_install(at):
    h, m = (int(x) for x in at.split(':'))
    py = sys.executable
    if os.name == 'nt':
        cmd = '"%s" "%s" run' % (py, SELF)
        subprocess.run(['schtasks', '/Create', '/F', '/SC', 'DAILY', '/ST', '%02d:%02d' % (h, m), '/TN', LABEL, '/TR', cmd], check=True)
        return 'Task Scheduler (%s)' % LABEL
    if sys.platform == 'darwin':
        plist = os.path.expanduser('~/Library/LaunchAgents/%s.plist' % LABEL)
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        with open(plist, 'w') as f:
            f.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string><string>%s</string><string>%s</string><string>run</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>
</dict></plist>
''' % (LABEL, py, SELF, h, m))
        subprocess.run(['launchctl', 'unload', plist], stderr=subprocess.DEVNULL)
        subprocess.run(['launchctl', 'load', plist], check=True)
        return 'launchd (%s)' % plist
    if shutil.which('systemctl') and os.geteuid() != 0 and subprocess.run(
            ['systemctl', '--user', 'is-system-running'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip() in (b'running', b'degraded'):
        unit = os.path.expanduser('~/.config/systemd/user/')
        os.makedirs(unit, exist_ok=True)
        with open(unit + LABEL + '.service', 'w') as f:
            f.write('[Unit]\nDescription=D.C. O\'HAIR push agent\n\n[Service]\nType=oneshot\nExecStart=%s %s run\n' % (py, SELF))
        with open(unit + LABEL + '.timer', 'w') as f:
            f.write('[Unit]\nDescription=D.C. O\'HAIR daily push\n\n[Timer]\nOnCalendar=*-*-* %02d:%02d:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' % (h, m))
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', LABEL + '.timer'], check=True)
        return 'a systemd user timer (run "loginctl enable-linger" so it also runs while you are logged out)'
    if shutil.which('crontab'):
        old = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True).stdout
        lines = [l for l in old.splitlines() if MARK not in l]
        lines.append('%d %d * * * %s %s run >/dev/null 2>&1 %s' % (m, h, py, SELF, MARK))
        subprocess.run(['crontab', '-'], input='\n'.join(lines) + '\n', universal_newlines=True, check=True)
        return 'cron'
    return None


def schedule_remove():
    if os.name == 'nt':
        subprocess.run(['schtasks', '/Delete', '/F', '/TN', LABEL], stderr=subprocess.DEVNULL)
        return
    if sys.platform == 'darwin':
        plist = os.path.expanduser('~/Library/LaunchAgents/%s.plist' % LABEL)
        subprocess.run(['launchctl', 'unload', plist], stderr=subprocess.DEVNULL)
        if os.path.exists(plist):
            os.remove(plist)
        return
    unit = os.path.expanduser('~/.config/systemd/user/' + LABEL)
    if os.path.exists(unit + '.timer'):
        subprocess.run(['systemctl', '--user', 'disable', '--now', LABEL + '.timer'], stderr=subprocess.DEVNULL)
        os.remove(unit + '.timer')
        os.remove(unit + '.service')
    if shutil.which('crontab'):
        old = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True).stdout
        if MARK in old:
            subprocess.run(['crontab', '-'], input='\n'.join(l for l in old.splitlines() if MARK not in l) + '\n', universal_newlines=True)


# ---------- commands ----------

def install(args):
    os.makedirs(HOME, exist_ok=True)
    conf = {'hub': args.hub, 'token': args.token}
    fd = os.open(CONF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(conf, f)
    if os.path.abspath(__file__) != SELF:
        shutil.copyfile(os.path.abspath(__file__), SELF)
    say('first scan, the hub shows it when it arrives')
    run(conf)
    if args.no_schedule:
        return say('done; run "%s %s run" whenever you want a fresh scan' % (sys.executable, SELF))
    spec = request(conf, '/api/push/spec')
    how = schedule_install(spec.get('schedule') or '04:30')
    say('done; it will scan every day at %s via %s' % (spec.get('schedule') or '04:30', how) if how
        else 'done, but there is no scheduler here; run "%s run" yourself' % SELF)


def main():
    ap = argparse.ArgumentParser(description="D.C. O'HAIR push agent")
    sub = ap.add_subparsers(dest='cmd')
    i = sub.add_parser('install')
    i.add_argument('--hub', required=True)
    i.add_argument('--token', required=True)
    i.add_argument('--no-schedule', action='store_true')
    sub.add_parser('run')
    sub.add_parser('uninstall')
    args = ap.parse_args()
    try:
        if args.cmd == 'install':
            return install(args)
        if args.cmd == 'uninstall':
            schedule_remove()
            shutil.rmtree(HOME, ignore_errors=True)
            return say('removed the schedule and', HOME)
        with open(CONF) as f:
            run(json.load(f))
    except urllib.error.HTTPError as e:
        sys.exit('[dc-ohair] the hub said %s: %s' % (e.code, e.read().decode(errors='replace')[:200]))
    except urllib.error.URLError as e:
        sys.exit("[dc-ohair] can't reach the hub: %s" % e.reason)


if __name__ == '__main__':
    main()
