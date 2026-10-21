"""The toolbox's JSON API, /api/tools/*.

hub.py hands these over only after its own login check (need_admin), and
every POST has already passed the X-DC header check, so nothing here is
reachable by anyone the rest of the API would turn away.

    GET /api/tools/dupes?min=<bytes>   both kinds of duplicate, biggest waste first
"""
import json, os

from . import dupes

DATA = os.environ.get('DC_DATA', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
TOOLS = os.path.join(DATA, 'toolbox')
LIMIT = 400     # groups of each kind sent to the page; the totals count them all


def hashes():
    """(device, path) -> {'partial', 'full'} for files the tools agent has read, if any."""
    try:
        with open(os.path.join(TOOLS, 'hashes.json')) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    return {tuple(k.split('\t', 1)): v for k, v in raw.items() if '\t' in k}


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
        r = dupes.report(h.hub.city(), min_size, hashes())
        r['more_bytes'] = max(0, len(r['bytes']) - LIMIT)
        r['more_films'] = max(0, len(r['films']) - LIMIT)
        r['groups_bytes'], r['groups_films'] = len(r['bytes']), len(r['films'])
        r['bytes'], r['films'] = r['bytes'][:LIMIT], r['films'][:LIMIT]
        return h.send_json(r)
    return h.send_json({'error': 'not found'}, 404)


def post(h, path, data):
    return h.send_json({'error': 'not found'}, 404)
