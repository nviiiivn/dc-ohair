"""Alert notifications: ntfy, Discord, email and plain webhooks.

Each channel in hub.notify is a small mapping:

    - {type: ntfy, url: https://ntfy.sh/my-disks}
    - {type: discord, url_cmd: "pass show discord/disk-webhook"}
    - {type: email, smtp_host: mail.example.com, smtp_port: 587, smtp_user: me,
       smtp_password_cmd: "pass show mail", from: hub@example.com, to: me@example.com}
    - {type: webhook, url: https://example.com/hook}

Anything secret (a webhook URL, a mail password) can come from a *_cmd that
prints it at send time, so it never has to sit in the config file.
"""
import json, smtplib, ssl, subprocess, urllib.request
from email.message import EmailMessage

TITLE = "Disk City"


def _secret(ch, key):
    if ch.get(key + '_cmd'):
        cmd = ch[key + '_cmd']
        out = subprocess.run(cmd, shell=isinstance(cmd, str), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=60, universal_newlines=True).stdout.strip()
        if not out:
            raise ValueError('%s_cmd printed nothing' % key)
        return out
    if not ch.get(key):
        raise ValueError('no %s set' % key)
    return ch[key]


def _post(url, body, headers):
    req = urllib.request.Request(url, data=body, headers=dict({'User-Agent': 'dc-ohair'}, **headers), method='POST')
    with urllib.request.urlopen(req, timeout=15) as res:
        res.read()


def send(ch, subject, text, level='warning', items=None):
    """Sends one message on one channel; raises on failure."""
    kind = ch.get('type')
    if kind == 'ntfy':
        _post(_secret(ch, 'url'), text.encode(), {'Title': subject, 'Priority': '4' if level == 'critical' else '3',
                                                  'Tags': 'rotating_light' if level == 'critical' else 'warning'})
    elif kind == 'discord':
        content = ('**%s**\n%s' % (subject, text))[:1990]
        _post(_secret(ch, 'url'), json.dumps({'content': content, 'username': TITLE}).encode(), {'Content-Type': 'application/json'})
    elif kind == 'webhook':
        _post(_secret(ch, 'url'), json.dumps({'title': subject, 'text': text, 'level': level, 'alerts': items or []}).encode(),
              {'Content-Type': 'application/json'})
    elif kind == 'email':
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = ch.get('from') or ch.get('smtp_user')
        msg['To'] = ch['to']
        msg.set_content(text)
        port = int(ch.get('smtp_port') or 587)
        ctx = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(ch['smtp_host'], port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(ch['smtp_host'], port, timeout=20)
            if ch.get('starttls', True):
                server.starttls(context=ctx)
        with server:
            if ch.get('smtp_user'):
                server.login(ch['smtp_user'], _secret(ch, 'smtp_password'))
            server.send_message(msg)
    else:
        raise ValueError('unknown channel type %r' % kind)


def send_all(channels, subject, text, level='warning', items=None):
    """Sends on every channel; returns one result per channel instead of raising."""
    results = []
    for ch in channels:
        try:
            send(ch, subject, text, level, items)
            results.append({'type': ch.get('type'), 'ok': True})
        except Exception as e:  # one broken channel mustn't stop the others
            results.append({'type': ch.get('type'), 'ok': False, 'error': str(e)[:200]})
    return results
