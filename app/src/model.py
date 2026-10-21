"""The city's data, the same way the web page sees it: devices, drives and
folders as nodes with category sizes, plus the category filter and sizes."""

CATS = ['Music', 'Games', 'Series', 'Movies', 'Other']
GiB = 1024 ** 3


def fmt(b, units='binary'):
    if b is None:
        return '—'
    k = 1000 if units == 'decimal' else 1024
    x = 'B' if units == 'decimal' else ''
    if b >= k ** 4:
        return '%.2fT%s' % (b / k ** 4, x)
    if b >= 10 * k ** 3:
        return '%dG%s' % (round(b / k ** 3), x)
    if b >= k ** 3:
        return '%.1fG%s' % (b / k ** 3, x)
    if b >= k ** 2:
        return '%dM%s' % (round(b / k ** 2), x)
    return '<1M' + x if b > 0 else '0'


class Node:
    __slots__ = ('n', 'kind', 'c', 's', 'f', 'm', 'k', 'p', 'drive', 'dev', 'free')

    def __init__(self, n, kind, c=None, s=0, f=0, m=0, p=None, drive=None, dev=None, free=0):
        self.n, self.kind, self.c, self.s, self.f, self.m = n, kind, c or [0] * 5, s, f, m
        self.k, self.p, self.drive, self.dev, self.free = [], p, drive, dev, free

    def path(self):
        names, n = [], self.p
        while n is not None and n.kind != 'all':
            names.insert(0, n.n)
            n = n.p
        return ' › '.join(names)

    @property
    def drillable(self):
        return self.kind in ('all', 'device', 'drive', 'dir') and bool(self.k)

    @property
    def dominant(self):
        return 5 if self.kind == 'unseen' else self.c.index(max(self.c))


def _wrap(t, parent, drive):
    kind = 'file' if t.get('t') == 'f' else 'rest' if t.get('x') else 'dir'
    node = Node(t['n'], kind, list(t['c']), sum(t['c']), t.get('f', 0), t.get('m', 0), parent, drive)
    node.k = [_wrap(k, node, drive) for k in t.get('k', [])]
    return node


def build(city):
    """/api/city JSON -> the root node (All devices)."""
    top = Node('All devices', 'all')
    for dev in city['devices']:
        dn = Node(dev['name'], 'device', p=top, dev=dev)
        for dr in dev['drives']:
            t = dr.get('tree') or {'c': [0] * 5}
            node = Node(dr['name'], 'drive', list(t['c']), 0, t.get('f', 0), t.get('m', 0), dn, dr, dev,
                        (dr.get('fs') or {}).get('free', 0))
            node.k = [_wrap(k, node, dr) for k in t.get('k', [])]
            seen = sum(node.c)
            used = (dr.get('fs') or {}).get('used', seen)
            node.s = seen
            unseen = max(0, used - seen)
            if unseen > max(GiB, used * 0.01):
                node.k.append(Node('not seen by the scan' if seen else 'not broken down yet', 'unseen', s=unseen, p=node, drive=dr))
                node.s += unseen
            dr['_node'] = node
            dn.k.append(node)
            for i in range(5):
                dn.c[i] += node.c[i]
            dn.s += node.s
            dn.f += node.f
            dn.free += node.free
        top.k.append(dn)
        for i in range(5):
            top.c[i] += dn.c[i]
        top.s += dn.s
        top.f += dn.f
        top.free += dn.free
    return top


class Filter:
    """Which categories are shown; everything else sizes as if they weren't there."""

    def __init__(self, on=None):
        self.on = list(on) if on and len(on) == 5 else [True] * 5

    @property
    def all(self):
        return all(self.on)

    def size(self, n):
        if self.all:
            return n.s
        return sum(v for v, keep in zip(n.c, self.on) if keep)


def kids(node, flt, order='size'):
    ks = [k for k in node.k if flt.size(k) > 0]
    key = {'size': lambda k: -flt.size(k), 'name': lambda k: k.n.lower(),
           'newest': lambda k: -(k.m or 0), 'oldest': lambda k: (k.m or 9e15)}[order]
    tail = [k for k in ks if k.kind in ('rest', 'unseen')]
    return sorted([k for k in ks if k not in tail], key=key) + tail


def biggest_files(node, flt, limit=300):
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        for k in n.k:
            if k.kind == 'file':
                out.append(k)
            elif k.k:
                stack.append(k)
    out.sort(key=lambda k: -flt.size(k))
    return out[:limit]


def index(top):
    """Every searchable node with its lowercased name."""
    out, stack = [], [top]
    while stack:
        n = stack.pop()
        for k in n.k:
            if k.kind in ('rest', 'unseen'):
                continue
            out.append((k.n.lower(), k))
            if k.k:
                stack.append(k)
    return out


def search(idx, q, limit=50):
    q = q.strip().lower()
    if not q:
        return []
    words = q.split()
    hits = [(0 if name == q else 1 if name.startswith(words[0]) else 2, -n.s, n) for name, n in idx if all(w in name for w in words)]
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]
