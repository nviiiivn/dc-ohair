"""The toolbox's JSON API, /api/tools/*.

hub.py hands these over only after its own login check (need_admin), and
every POST has already passed the X-DC header check, so nothing here is
reachable by anyone the rest of the API would turn away.

    GET  /api/tools/dupes?min=<bytes>   both kinds of duplicate, biggest waste first
    POST /api/tools/dupes/check         {"full": false} read the same-size sets on their devices
    GET  /api/tools/jobs                what the running (or last) job is doing
    GET  /api/tools/key                 the authorized_keys line that lets the hub run only the tools agent
"""
import os

from . import dupes, jobs

DATA = os.environ.get('DC_DATA', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
TOOLS = os.path.join(DATA, 'toolbox')
LIMIT = 400     # groups of each kind sent to the page; the totals count them all


def _query(query):
    out = {}
    for part in query.split('&'):
        k, _, v = part.partition('=')
        if k:
            out[k] = v
    return out


def get(h, path, query):
    q = _query(query)
    if path == '/api/tools/dupes':
        min_size = int(q['min']) if q.get('min', '').isdigit() else 0
        r = dupes.report(h.hub.city(), min_size, jobs.hashes_for(h.hub))
        r['more_bytes'] = max(0, len(r['bytes']) - LIMIT)
        r['more_films'] = max(0, len(r['films']) - LIMIT)
        r['groups_bytes'], r['groups_films'] = len(r['bytes']), len(r['films'])
        r['bytes'], r['films'] = r['bytes'][:LIMIT], r['films'][:LIMIT]
        return h.send_json(r)
    if path == '/api/tools/jobs':
        return h.send_json(jobs.status())
    if path == '/api/tools/key':
        if h.hub.demo:
            return h.send_json({'line': 'command="python3 -c \\"…tools agent only…\\"",no-port-forwarding,no-pty ssh-ed25519 AAAA… dc-ohair@demo'})
        import collect
        try:
            line = collect.restricted_key_line(jobs.TOOLS_AGENT)
        except Exception as e:   # no ssh-keygen on this box
            line = 'unavailable here: %s' % e
        return h.send_json({'line': line})
    return h.send_json({'error': 'not found'}, 404)


def post(h, path, data):
    if path == '/api/tools/dupes/check':
        started = jobs.start_check(h.hub, full=bool(data.get('full')))
        return h.send_json(dict(jobs.status(), started=started), 202 if started else 409)
    return h.send_json({'error': 'not found'}, 404)
