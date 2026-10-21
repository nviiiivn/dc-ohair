"""Disk City for phones and tablets.

Connects to a Disk City hub (or shows a made-up home network) and lets you
look through every drive as lists you tap into: drives with what fills them,
folders sorted by size, the biggest files below anywhere, search, and alerts.
"""
import asyncio, datetime, functools, json, os

import flet as ft
import flet.canvas as cv

import model
import themes
import single
from hubclient import Demo, Hub, HubError

ICONS = {'device': ft.Icons.DEVICES, 'drive': ft.Icons.STORAGE, 'dir': ft.Icons.FOLDER, 'file': ft.Icons.INSERT_DRIVE_FILE,
         'rest': ft.Icons.MORE_HORIZ, 'unseen': ft.Icons.VISIBILITY_OFF, 'all': ft.Icons.LOCATION_CITY}
PAGE_ROWS = 300


def when(iso):
    if not iso:
        return ''
    try:
        d = datetime.datetime.strptime(iso[:19], '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return ''
    return d.strftime('%H:%M') if d.date() == datetime.date.today() else d.strftime('%d %b %H:%M')


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.prefs = ft.SharedPreferences()
        self.hub = None
        self.city = None
        self.top = self.root = None
        self.idx = []
        self.flt = model.Filter()
        self.order = 'size'
        self.mode = 'here'
        self.units = 'binary'
        self.theme = 'dark'
        self.t = themes.palette('dark')
        self.tab = 0
        self.shown = PAGE_ROWS
        self.highlight = None
        self.body = ft.Container(expand=True)
        self.citysrv = None  # serves the 3D page from inside the app when there's no hub

    # ---------- start, connect, log in ----------

    async def pref(self, key, default=None):
        """A saved setting; a slow or missing preferences service just means the default."""
        try:
            value = await self.prefs.get(key)
        except (RuntimeError, TimeoutError):
            return default
        return default if value is None else value

    async def save_pref(self, key, value):
        try:
            await self.prefs.set(key, value)
        except (RuntimeError, TimeoutError):
            pass

    async def start(self):
        self.page.update()  # lets the client register the preferences service before the first read
        self.theme = await self.pref('theme', 'dark')
        self.t = themes.apply(self.page, self.theme)
        cats = await self.pref('cats')
        self.flt = model.Filter([c == '1' for c in cats] if isinstance(cats, list) else None)
        self.page.title = "Disk City"
        try:
            folder = await ft.StoragePaths().get_application_support_directory()
        except Exception:  # not available in web mode, or the service is slow
            folder = None
        self.store = single.Store(os.path.join(folder or os.path.expanduser('~'), 'dc-ohair-app'))
        url = await self.pref('hub_url')
        if url == 'demo':
            self.hub = Demo()
        elif url == 'single':
            try:
                self.hub = single.Single(self.store, json.loads(await self.pref('single', '{}')))
            except ValueError:
                self.hub = None
        elif url:
            self.hub = Hub(url, await self.pref('session', ''))
        if self.hub:
            await self.load()
        else:
            self.show_connect()

    def screen(self, *controls, nav=False):
        self.page.appbar = None if not nav else self.page.appbar
        self.page.navigation_bar = None if not nav else self.page.navigation_bar
        self.page.controls.clear()
        self.page.controls.extend(controls)
        self.page.update()

    def show_connect(self, error=''):
        t = self.t
        url = ft.TextField(label='Hub address', hint_text='192.168.1.10:7171', keyboard_type=ft.KeyboardType.URL,
                           autofocus=True, value=self.hub.url if isinstance(self.hub, Hub) else '')
        msg = ft.Text(error, color=t['red'])

        async def connect(e):
            if not url.value.strip():
                msg.value = 'Type the address of your hub.'
                return self.page.update()
            self.hub = Hub(url.value)
            await self.save_pref('hub_url', self.hub.url)
            await self.load()

        async def demo(e):
            self.hub = Demo()
            await self.save_pref('hub_url', 'demo')
            await self.load()

        url.on_submit = connect
        self.page.appbar = None
        self.page.navigation_bar = None
        self.screen(ft.SafeArea(ft.Container(ft.Column([
            ft.Text("Disk City", size=30, weight=ft.FontWeight.BOLD, color=t['accent']),
            ft.Text("a.k.a. D.C. O'HAIR: Data City Over Here Am I Right?", italic=True, color=t['text2']),
            ft.Container(height=24),
            ft.Text('Connect to your hub: the machine that runs Disk City on your network.', color=t['text']),
            url, msg,
            ft.FilledButton('Connect', icon=ft.Icons.LINK, on_click=connect),
            ft.Container(height=8),
            ft.Text('No hub? The app can scan by itself:', color=t['text2']),
            ft.OutlinedButton('Scan one computer over SSH', icon=ft.Icons.TERMINAL, on_click=lambda e: self.show_ssh_setup()),
            *([ft.OutlinedButton('Scan this phone', icon=ft.Icons.PHONE_ANDROID, on_click=self.scan_phone)]
              if self.page.platform == ft.PagePlatform.ANDROID else []),
            ft.TextButton('Look around a made-up home network first', icon=ft.Icons.TRAVEL_EXPLORE, on_click=demo),
        ], spacing=12, tight=True, scroll=ft.ScrollMode.AUTO), padding=24, expand=True)))

    # ---------- single-device mode ----------

    def show_ssh_setup(self, cfg=None, error=''):
        t = self.t
        cfg = dict(cfg or {})
        field = lambda label, key, hint='', **kw: ft.TextField(label=label, value=str(cfg.get(key) or ''), hint_text=hint, **kw)
        name = field('Name', 'name', 'Living room PC')
        host = field('Host', 'host', '192.168.1.20 or pc.local', keyboard_type=ft.KeyboardType.URL)
        user = field('User', 'user', 'me')
        port = field('Port', 'port', '22', keyboard_type=ft.KeyboardType.NUMBER, width=110)
        paths = ft.TextField(label='Folders or drives to scan', value=', '.join(cfg.get('paths') or ['/']), hint_text='/, /mnt/media')
        msg = ft.Text(error, color=t['red'])
        try:
            pub = self.store.key()[1]
        except Exception as e:  # cryptography missing in an odd build
            pub = 'The app could not make its key: %s' % e

        def current():
            c = dict(cfg, kind='ssh', name=name.value.strip() or host.value.strip(), host=host.value.strip(), user=user.value.strip(),
                     port=int(port.value or 22) if (port.value or '22').isdigit() else 22,
                     paths=[p.strip() for p in paths.value.split(',') if p.strip()] or ['/'])
            return c

        async def copy_key(e):
            await ft.Clipboard().set(pub)
            e.control.content = 'Copied'
            self.page.update()

        async def find(e):
            probe = single.Single(self.store, current())
            try:
                found = await self.with_prompts(probe, probe.mounts)
            except single.ScanError as err:
                msg.value = str(err)
                return self.page.update()
            if found is None:
                return
            have = set(p.strip() for p in paths.value.split(','))
            paths.value = ', '.join([p for p in have if p] + [m['path'] for m in found if m['path'] not in have])
            msg.value = 'Found %d drive%s.' % (len(found), '' if len(found) == 1 else 's')
            self.page.update()

        async def go(e):
            c = current()
            if not c['host']:
                msg.value = 'Fill in the host.'
                return self.page.update()
            await self.save_pref('single', json.dumps(c))
            await self.save_pref('hub_url', 'single')
            self.hub = single.Single(self.store, c)
            await self.load()

        self.screen(ft.SafeArea(ft.Container(ft.Column([
            ft.Row([ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: self.show_connect()),
                    ft.Text('Scan one computer', size=20, weight=ft.FontWeight.BOLD, color=t['text'])]),
            ft.Text('The app logs in over SSH, runs the same read-only scanner the hub uses, and keeps the result on this phone.',
                    color=t['text2']),
            name, ft.Row([ft.Container(host, expand=True), port]), user, paths,
            ft.Text("Let the app in with its own key: add this line to ~/.ssh/authorized_keys on that computer. "
                    "Or skip it and type the password each time you scan; it's never saved.", color=t['text2'], size=12),
            ft.Container(ft.Text(pub, selectable=True, size=11, font_family='monospace'), bgcolor=t['surface'], padding=10, border_radius=8),
            ft.Row([ft.TextButton('Copy the key', icon=ft.Icons.COPY, on_click=copy_key),
                    ft.TextButton('Find its drives', icon=ft.Icons.TRAVEL_EXPLORE, on_click=find)], wrap=True),
            msg,
            ft.FilledButton('Save and scan', icon=ft.Icons.RADAR, on_click=go),
        ], spacing=10, scroll=ft.ScrollMode.AUTO), padding=20, expand=True)))

    async def with_prompts(self, src, fn):
        """Runs fn in a thread, asking for a password or a host-key OK when the scanner needs one."""
        for _ in range(3):
            try:
                return await asyncio.to_thread(fn)
            except single.UnknownHost as e:
                if not await self.ask_trust(e):
                    return None
                src.cfg['accept_host'] = e.raw
                if src is self.hub:
                    await self.save_pref('single', json.dumps(src.cfg))
            except single.NeedsPassword:
                pw = await self.ask_password(src.cfg)
                if pw is None:
                    return None
                src.password = pw
        raise single.ScanError("Couldn't log in.")

    async def ask(self, title, body, ok, field=None):
        done = asyncio.get_running_loop().create_future()

        def answer(value):
            if not done.done():
                done.set_result(value)
            self.page.pop_dialog()
        content = ft.Column([ft.Text(body)] + ([field] if field else []), tight=True)
        if field:
            field.on_submit = lambda e: answer(field.value)
        self.page.show_dialog(ft.AlertDialog(modal=True, title=ft.Text(title), content=content, actions=[
            ft.TextButton('Cancel', on_click=lambda e: answer(None)),
            ft.FilledButton(ok, on_click=lambda e: answer(field.value if field else True))]))
        return await done

    async def ask_trust(self, e):
        what = ('This computer\'s identity has CHANGED since last time. If you didn\'t reinstall it, don\'t trust it.\n\n' if e.changed
                else 'First time talking to %s. Check that this matches the computer (ssh-keygen -lf /etc/ssh/ssh_host_*.pub):\n\n' % e.host)
        return await self.ask('Trust this computer?', what + e.fingerprint, 'Trust it')

    async def ask_password(self, cfg):
        return await self.ask('Password for %s' % cfg.get('host'), "Used for this scan only; it isn't saved.", 'Scan',
                              ft.TextField(label='%s\'s password' % (cfg.get('user') or 'the'), password=True, can_reveal_password=True,
                                           autofocus=True))

    async def scan_phone(self, e=None):
        try:
            import flet_permission_handler as fph
            handler = fph.PermissionHandler()
            status = await handler.request(fph.Permission.MANAGE_EXTERNAL_STORAGE)
            if status != fph.PermissionStatus.GRANTED:
                ok = await self.ask('Allow All files access', 'To measure what fills this phone, Disk City needs to read all its files '
                                    '(it never changes or sends any of them). Turn on "All files access" for it in the next screen.', 'Open settings')
                if ok:
                    await handler.open_app_settings()
                return
        except ImportError:
            pass
        cfg = {'kind': 'phone', 'name': 'This phone'}
        await self.save_pref('single', json.dumps(cfg))
        await self.save_pref('hub_url', 'single')
        self.hub = single.Single(self.store, cfg)
        await self.load()

    def show_login(self, me):
        t = self.t
        pw = ft.TextField(label='Password', password=True, can_reveal_password=True, autofocus=True)
        msg = ft.Text('', color=t['red'])

        async def go(e):
            try:
                session = await asyncio.to_thread(self.hub.login, pw.value)
            except HubError as err:
                msg.value = str(err)
                return self.page.update()
            await self.save_pref('session', session)
            await self.load()

        pw.on_submit = go
        if me.get('auth') == 'password':
            body = [ft.Text('This hub has a login.', color=t['text']), pw, msg, ft.FilledButton('Log in', icon=ft.Icons.LOGIN, on_click=go)]
        else:
            body = [ft.Text('This hub signs people in through %s. The app can only use a password login so far; '
                            'turn on the admin login in Settings → Security, or use the web page.' % (
                                'your reverse proxy' if me.get('auth') == 'proxy' else me.get('login_name') or 'OIDC'), color=t['text'])]
        self.screen(ft.SafeArea(ft.Container(ft.Column([
            ft.Text("Disk City", size=28, weight=ft.FontWeight.BOLD, color=t['accent']), *body,
            ft.TextButton('Use a different hub', on_click=lambda e: self.show_connect()),
        ], spacing=12, tight=True), padding=24)))

    async def load(self, quiet=False):
        if not quiet:
            self.screen(ft.Container(ft.Column([ft.ProgressRing(), ft.Text('Hold on to your butts…', color=self.t['text2'])],
                                               horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
                                     alignment=ft.Alignment.CENTER, expand=True))
        try:
            me = await asyncio.to_thread(self.hub.me)
            if me.get('auth', 'off') != 'off' and not me.get('admin'):
                return self.show_login(me)
            if isinstance(self.hub, single.Single):
                city = await self.with_prompts(self.hub, self.hub.city)
                if city is None:
                    return self.show_ssh_setup(self.hub.cfg) if not self.hub.is_phone else self.show_connect()
                self.city = city
            else:
                self.city = await asyncio.to_thread(self.hub.city)
        except HubError as err:
            return self.show_connect(str(err))
        except single.ScanError as err:
            if isinstance(self.hub, single.Single) and not self.hub.is_phone:
                return self.show_ssh_setup(self.hub.cfg, str(err))
            return self.show_connect(str(err))
        self.units = (self.city.get('ui') or {}).get('units', 'binary')
        self.top = model.build(self.city)
        keep = self.path_names(self.root)
        self.root = self.find(keep) or self.top
        self.idx = model.index(self.top)
        self.build_main()

    def path_names(self, node):
        names = []
        while node is not None and node.p is not None:
            names.insert(0, node.n)
            node = node.p
        return names

    def find(self, names):
        node = self.top
        for name in names:
            node = next((k for k in node.k if k.n == name), None)
            if node is None:
                return None
        return node

    # ---------- the main screen ----------

    def build_main(self):
        t = self.t
        alerts = self.city.get('alerts') or []
        self.page.appbar = ft.AppBar(
            title=ft.Text("Disk City", weight=ft.FontWeight.BOLD, color=t['accent']), bgcolor=t['bg2'],
            actions=[ft.IconButton(ft.Icons.REFRESH, tooltip='Reload', on_click=self.reload),
                     ft.IconButton(ft.Icons.SETTINGS, tooltip='Settings', on_click=self.open_settings)])
        self.page.navigation_bar = ft.NavigationBar(
            selected_index=self.tab, on_change=self.switch, bgcolor=t['bg2'], indicator_color=t['surface'],
            destinations=[ft.NavigationBarDestination(icon=ft.Icons.STORAGE, label='Drives'),
                          ft.NavigationBarDestination(icon=ft.Icons.FOLDER_OPEN, label='Browse'),
                          ft.NavigationBarDestination(icon=ft.Icons.LOCATION_CITY, label='City'),
                          ft.NavigationBarDestination(icon=ft.Icons.SEARCH, label='Search'),
                          ft.NavigationBarDestination(icon=ft.Icons.NOTIFICATIONS, label='Alerts',
                                                      badge=str(len(alerts)) if alerts else None)])
        self.page.controls.clear()
        self.page.controls.append(self.body)
        self.render()

    def switch(self, e):
        self.tab = e.control.selected_index
        self.render()

    def go_tab(self, i):
        self.tab = i
        self.page.navigation_bar.selected_index = i
        self.render()

    def render(self):
        self.body.content = [self.drives_view, self.browse_view, self.city_view, self.search_view, self.alerts_view][self.tab]()
        self.page.update()

    async def reload(self, e=None):
        await self.load(quiet=True)

    # ---------- shared bits ----------

    def cat_color(self, i):
        return themes.UNSEEN if i >= 5 else self.t['cats'][i]

    def stacked_bar(self, parts, total, height=8):
        t = self.t
        cells = [ft.Container(height=height, bgcolor=color, expand=max(1, int(1000 * v / total))) for color, v in parts if v > 0]
        rest = total - sum(v for _, v in parts)
        if rest > 0:
            cells.append(ft.Container(height=height, bgcolor=t['surface'], expand=max(1, int(1000 * rest / total))))
        return ft.Container(ft.Row(cells, spacing=0), border_radius=height / 2, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                            bgcolor=t['surface'])

    def chips(self):
        t = self.t

        async def toggle(i, e):
            on = list(self.flt.on)
            on[i] = not on[i]
            self.flt = model.Filter(on if any(on) else None)
            await self.save_pref('cats', ['1' if x else '0' for x in self.flt.on])
            self.shown = PAGE_ROWS
            self.render()

        row = [ft.Chip(label=ft.Text(name, size=12), selected=self.flt.on[i], show_checkmark=False,
                       leading=ft.Container(width=10, height=10, border_radius=3, bgcolor=self.cat_color(i)),
                       selected_color=t['surface'], on_select=functools.partial(toggle, i))
               for i, name in enumerate(model.CATS)]
        return ft.Row(row, scroll=ft.ScrollMode.AUTO, spacing=6)

    def fmt(self, b):
        return model.fmt(b, self.units)

    def details(self, node):
        t = self.t
        lines = [ft.Text(node.path() or 'All devices', size=12, color=t['text2']),
                 ft.Text(self.fmt(self.flt.size(node)), size=24, weight=ft.FontWeight.BOLD, color=t['text'])]
        for i, v in enumerate(node.c):
            if v:
                lines.append(ft.Row([ft.Container(width=10, height=10, border_radius=3, bgcolor=self.cat_color(i)),
                                     ft.Text(model.CATS[i], expand=True), ft.Text(self.fmt(v))]))
        bits = []
        if node.f:
            bits.append('%s file%s' % (format(node.f, ','), '' if node.f == 1 else 's'))
        if node.m:
            bits.append(('changed ' if node.kind == 'file' else 'newest ') + datetime.date.fromtimestamp(node.m).strftime('%d %b %Y'))
        if bits:
            lines.append(ft.Text(' · '.join(bits), size=12, color=t['text2']))
        actions = [ft.TextButton('Close', on_click=lambda e: self.page.pop_dialog())]
        if node.drillable:
            def inside(e):
                self.page.pop_dialog()
                self.open_node(node)
            actions.insert(0, ft.FilledButton('Go inside', on_click=inside))
        self.page.show_dialog(ft.AlertDialog(title=ft.Text(node.n), content=ft.Column(lines, tight=True, spacing=6), actions=actions))

    def open_node(self, node):
        if node.drillable:
            self.root, self.highlight = node, None
        else:
            self.root, self.highlight = node.p, node
        if self.mode == 'files' or not node.drillable:
            self.mode = 'here'
        self.shown = PAGE_ROWS
        self.go_tab(1)

    # ---------- Drives ----------

    def drives_view(self):
        t = self.t
        limit = self.city.get('alert_percent', 90)
        items = [self.chips()]
        for dev in self.city['devices']:
            st = next((d for d in dev['drives'] if d.get('when')), {})
            live = all(d.get('source') == 'live' for d in dev['drives'])
            items.append(ft.Container(ft.Row([
                ft.Text(dev['name'], size=16, weight=ft.FontWeight.BOLD, color=t['text']),
                ft.Text(dev.get('note', ''), size=12, color=t['text2'], expand=True, no_wrap=True),
                ft.Text(('live · ' if live else 'snapshot · ') + when(st.get('when')), size=11, color=t['green'] if live else t['text2'])]),
                padding=ft.Padding.only(left=4, right=4, top=14)))
            for dr in dev['drives']:
                items.append(self.drive_card(dr, limit))
        return ft.ListView(items, expand=True, padding=12, spacing=8)

    def drive_card(self, dr, limit):
        t = self.t
        node = dr.get('_node')
        fs = dr.get('fs') or {}
        total = fs.get('total') or (node.s if node else 0) or 1
        parts = [(self.cat_color(i), v) for i, v in enumerate(node.c) if self.flt.on[i]] if node else []
        unseen = next((k.s for k in node.k if k.kind == 'unseen'), 0) if node else 0
        if unseen and self.flt.all:
            parts.append((themes.UNSEEN, unseen))
        head = [ft.Text(dr['name'], size=15, weight=ft.FontWeight.W_600, color=t['text'], expand=True)]
        h = dr.get('health') or {}
        if h.get('state') == 'failing' and (self.city.get('ui') or {}).get('failing_dot', True):
            head.insert(0, ft.Icon(ft.Icons.CIRCLE, size=10, color=t['red'], tooltip='This drive is failing'))
        pct = 100 * fs['used'] / fs['total'] if fs.get('total') else 0
        if pct >= limit:
            head.append(ft.Text('⚠ %d%% full' % round(pct), size=12, weight=ft.FontWeight.BOLD, color=t['red']))
        cats = [ft.Row([ft.Container(width=8, height=8, border_radius=2, bgcolor=self.cat_color(i)),
                        ft.Text('%s %s' % (model.CATS[i], self.fmt(v)), size=11, color=t['text2'])], spacing=4, tight=True)
                for i, v in enumerate(node.c if node else []) if v and self.flt.on[i]]
        return ft.Container(ft.Column([
            ft.Row(head, spacing=6),
            self.stacked_bar(parts, total),
            ft.Text('Used %s · Free %s · Total %s' % (self.fmt(fs.get('used')), self.fmt(fs.get('free')), self.fmt(fs.get('total'))),
                    size=12, color=t['text2']),
            ft.Row(cats, wrap=True, spacing=10, run_spacing=2),
        ], spacing=6, tight=True), bgcolor=t['surface'], border_radius=12, padding=12, ink=True,
            on_click=(lambda e, n=node: self.open_node(n)) if node else None)

    # ---------- Browse ----------

    def crumbs(self):
        t = self.t
        chain, n = [], self.root
        while n is not None:
            chain.insert(0, n)
            n = n.p
        out = []
        for i, n in enumerate(chain):
            if i:
                out.append(ft.Text('›', color=t['text2']))
            out.append(ft.TextButton(n.n, disabled=n is self.root,
                                     on_click=lambda e, n=n: self.open_node(n)))
        return ft.Row(out, scroll=ft.ScrollMode.AUTO, spacing=0)

    def browse_view(self):
        t = self.t
        node = self.root or self.top
        rows = model.biggest_files(node, self.flt) if self.mode == 'files' else model.kids(node, self.flt, self.order)
        biggest = max([self.flt.size(r) for r in rows] or [1]) or 1

        def set_mode(e):
            self.mode = next(iter(e.control.selected))
            self.shown = PAGE_ROWS
            self.render()

        def set_order(e):
            self.order = e.control.value
            self.render()

        tiles = []
        for r in rows[:self.shown]:
            size = self.flt.size(r)
            color = self.cat_color(r.dominant)
            sub = [ft.ProgressBar(value=size / biggest, color=color, bgcolor=t['surface'], bar_height=4)]
            info = r.path() if self.mode == 'files' else ' · '.join(x for x in (
                '%s file%s' % (format(r.f, ','), '' if r.f == 1 else 's') if r.f and r.kind != 'file' else '',
                datetime.date.fromtimestamp(r.m).strftime('%d %b %Y') if r.m else '') if x)
            if info:
                sub.insert(0, ft.Text(info, size=11, color=t['text2'], no_wrap=True))
            tiles.append(ft.ListTile(
                leading=ft.Icon(ICONS.get(r.kind, ft.Icons.FOLDER), color=color),
                title=ft.Text(r.n, no_wrap=True, weight=ft.FontWeight.W_600 if r.drillable else None),
                subtitle=ft.Column(sub, spacing=3, tight=True),
                trailing=ft.Text(self.fmt(size), weight=ft.FontWeight.BOLD, color=t['text']),
                bgcolor=t['surface'] if r is self.highlight else None,
                on_click=lambda e, r=r: self.open_node(r) if r.drillable else self.details(r),
                on_long_press=lambda e, r=r: self.details(r)))
        if len(rows) > self.shown:
            def more(e):
                self.shown += PAGE_ROWS
                self.render()
            tiles.append(ft.TextButton('Show %d more' % min(PAGE_ROWS, len(rows) - self.shown), on_click=more))
        if not tiles:
            tiles.append(ft.Container(ft.Text('Nothing here with these categories.', color=t['text2']), padding=20))
        up = node.p

        def go_up(e):
            if up is not None:
                self.open_node(up)
        head = ft.Row([
            ft.IconButton(ft.Icons.ARROW_BACK, tooltip='Up a level', on_click=go_up, disabled=up is None),
            ft.Column([ft.Text(node.n, size=18, weight=ft.FontWeight.BOLD, color=t['text'], no_wrap=True),
                       ft.Text('%s · %d items' % (self.fmt(self.flt.size(node)), len(node.k)), size=12, color=t['text2'])],
                      spacing=0, expand=True, tight=True),
            ft.IconButton(ft.Icons.INFO_OUTLINE, tooltip='Details', on_click=lambda e: self.details(node))])
        controls = ft.Row([
            ft.SegmentedButton(segments=[ft.Segment('here', label=ft.Text('List')), ft.Segment('map', label=ft.Text('Map')),
                                         ft.Segment('files', label=ft.Text('Biggest'))],
                               selected=[self.mode], on_change=set_mode),
            ft.Dropdown(value=self.order, width=130, dense=True, on_select=set_order,
                        options=[ft.DropdownOption('size', 'Size'), ft.DropdownOption('name', 'Name'),
                                 ft.DropdownOption('newest', 'Newest'), ft.DropdownOption('oldest', 'Oldest')]),
        ], wrap=True, spacing=8)
        top = ft.Container(ft.Column([self.crumbs(), head, self.chips(), controls], spacing=6, tight=True),
                           padding=ft.Padding.symmetric(horizontal=8))
        if self.mode == 'map':
            return ft.Column([top, self.map_view(node, rows)], expand=True)
        return ft.Column([top, ft.ListView(tiles, expand=True, padding=ft.Padding.symmetric(horizontal=4))], expand=True)

    # ---------- Map: a treemap you tap into ----------

    def map_view(self, node, rows):
        self.map_node, self.map_rows = node, rows
        w = (self.page.width or 400) - 16
        h = max(240, (self.page.height or 800) - 330)
        self.map_size = (w, h)
        self.canvas = cv.Canvas(shapes=self.map_shapes(w, h), expand=True, on_resize=self.map_resized, resize_interval=150)
        return ft.Container(ft.GestureDetector(content=self.canvas, on_tap_down=self.map_tap, expand=True),
                            expand=True, padding=ft.Padding.only(left=8, right=8, bottom=8))

    def map_resized(self, e):
        if (e.width, e.height) != self.map_size and e.width > 0 and e.height > 0:
            self.map_size = (e.width, e.height)
            self.canvas.shapes = self.map_shapes(e.width, e.height)
            self.canvas.update()

    def map_shapes(self, w, h):
        t = self.t
        rows = [r for r in self.map_rows if self.flt.size(r) > 0][:400]
        rects = model.squarify([self.flt.size(r) for r in rows], 0, 0, w, h)
        self.map_hits = []
        shapes = []
        fill = lambda c: ft.Paint(color=c, style=ft.PaintingStyle.FILL)
        for r, rect in zip(rows, rects):
            if not rect:
                continue
            x, y, rw, rh = rect
            self.map_hits.append((x, y, rw, rh, r))
            gap = 1.5 if min(rw, rh) > 6 else 0.5
            x, y, rw, rh = x + gap, y + gap, rw - 2 * gap, rh - 2 * gap
            if rw <= 0 or rh <= 0:
                continue
            kids = model.kids(r, self.flt) if r.k else []
            label = rw > 54 and rh > 30
            if not kids:
                shapes.append(cv.Rect(x, y, rw, rh, border_radius=3, paint=fill(self.cat_color(r.dominant))))
            else:
                shapes.append(cv.Rect(x, y, rw, rh, border_radius=3, paint=fill(t['surface'])))
                head = 16 if label else 0
                inner = model.squarify([self.flt.size(k) for k in kids[:150]], x + 2, y + 2 + head, rw - 4, rh - 4 - head)
                for k, kr in zip(kids[:150], inner):
                    if kr and kr[2] > 1 and kr[3] > 1:
                        shapes.append(cv.Rect(kr[0] + 0.5, kr[1] + 0.5, kr[2] - 1, kr[3] - 1, paint=fill(self.cat_color(k.dominant))))
            if label:
                shapes.append(cv.Text(x + 5, y + 2, '%s  %s' % (r.n, self.fmt(self.flt.size(r))), max_lines=1, max_width=rw - 10,
                                      ellipsis='…', style=ft.TextStyle(size=11, weight=ft.FontWeight.W_600, color=t['text'])))
        if not shapes:
            shapes.append(cv.Text(w / 2, h / 2, 'Nothing here with these categories.', alignment=ft.Alignment.CENTER,
                                  style=ft.TextStyle(color=t['text2'])))
        return shapes

    def map_tap(self, e):
        p = e.local_position
        for x, y, w, h, r in self.map_hits:
            if x <= p.x <= x + w and y <= p.y <= y + h:
                if r.drillable:
                    self.root, self.highlight, self.shown = r, None, PAGE_ROWS
                    self.render()
                else:
                    self.details(r)
                return

    # ---------- City: the hub's own 3D page ----------

    def city_view(self):
        t = self.t
        try:
            import flet_webview as fwv
        except ImportError:
            fwv = None
        if fwv and isinstance(self.hub, Hub):
            return ft.Column([fwv.WebView(url=self.hub.url, expand=True, bgcolor=t['bg'])], expand=True)
        if fwv:
            # no hub: the same page, served by the app itself from what's already loaded
            if self.citysrv is None:
                import cityserver
                self.citysrv = cityserver.CityServer(lambda: self.city, lambda: getattr(self.hub, 'data', {}).get('history', {}))
            self.citysrv.set_theme(self.theme)
            return ft.Column([fwv.WebView(url=self.citysrv.url, expand=True, bgcolor=t['bg'])], expand=True)

        async def open_web(e):
            await ft.UrlLauncher().launch_url(self.hub.url)
        body = [ft.Icon(ft.Icons.LOCATION_CITY, size=64, color=t['accent']),
                ft.Text('The 3D city needs the app\'s WebView, which this build doesn\'t have.', size=16, text_align=ft.TextAlign.CENTER),
                ft.TextButton('See the map instead', icon=ft.Icons.GRID_VIEW,
                              on_click=lambda e: (setattr(self, 'mode', 'map'), self.go_tab(1)))]
        if isinstance(self.hub, Hub):
            body.append(ft.FilledButton('Open it in the browser', icon=ft.Icons.OPEN_IN_BROWSER, on_click=open_web))
        return ft.Container(ft.Column(body, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True, spacing=12),
                            alignment=ft.Alignment.CENTER, expand=True, padding=24)

    # ---------- Search ----------

    def search_view(self):
        t = self.t
        results = ft.ListView(expand=True)
        box = ft.TextField(hint_text='Find a file or folder on any drive', prefix_icon=ft.Icons.SEARCH, autofocus=True,
                           value=getattr(self, 'query', ''))

        def fill(q):
            results.controls = [ft.ListTile(
                leading=ft.Icon(ICONS.get(n.kind, ft.Icons.FOLDER), color=self.cat_color(n.dominant)),
                title=ft.Text(n.n, no_wrap=True), subtitle=ft.Text(n.path(), size=11, color=t['text2'], no_wrap=True),
                trailing=ft.Text(self.fmt(n.s)), on_click=lambda e, n=n: self.open_node(n))
                for n in model.search(self.idx, q)]
            if q.strip() and not results.controls:
                results.controls = [ft.Container(ft.Text('Nothing by that name.', color=t['text2']), padding=20)]

        def changed(e):
            self.query = box.value
            fill(box.value)
            self.page.update()
        box.on_change = changed
        fill(box.value)
        return ft.Column([ft.Container(box, padding=12), results], expand=True)

    # ---------- Alerts ----------

    def alerts_view(self):
        t = self.t
        alerts = self.city.get('alerts') or []
        status = ft.Text('', color=t['text2'], size=12)

        async def scan(e):
            e.control.disabled = True
            status.value = 'Scanning… big drives take a few minutes.'
            self.page.update()
            try:
                if isinstance(self.hub, single.Single):
                    # scanning by itself: ask for a password or a host-key OK right here if needed
                    if not await self.with_prompts(self.hub, self.hub.scan_now):
                        raise single.ScanError('Scan cancelled.')
                else:
                    await asyncio.to_thread(self.hub.scan)
                    while (await asyncio.to_thread(self.hub.status)).get('running'):
                        await asyncio.sleep(3)
                await self.load(quiet=True)
            except (HubError, single.ScanError) as err:
                status.value = str(err)
                e.control.disabled = False
                self.page.update()

        tiles = [ft.ListTile(
            leading=ft.Icon(ft.Icons.WARNING_ROUNDED, color=t['red'] if a.get('level') == 'critical' else themes.AMBER),
            title=ft.Text(a['msg']),
            on_click=lambda e, a=a: self.open_alert(a)) for a in alerts]
        if not tiles:
            tiles = [ft.Container(ft.Text('All quiet: no drive is almost full.', color=t['text2']), padding=20)]
        return ft.Column([ft.Container(ft.Column([
            ft.Row([ft.FilledButton('Scan now', icon=ft.Icons.RADAR, on_click=scan), status], wrap=True),
            ft.Text('Updated %s' % when(self.city.get('generated')), size=12, color=t['text2'])], tight=True), padding=12),
            ft.ListView(tiles, expand=True)], expand=True)

    def open_alert(self, a):
        dev = next((d for d in self.city['devices'] if d['name'] == a.get('device')), None)
        dr = dev and next((d for d in dev['drives'] if d['name'] == a.get('drive')), None)
        if dr and dr.get('_node'):
            self.open_node(dr['_node'])

    # ---------- Settings ----------

    async def open_settings(self, e):
        t = self.t

        async def pick(name, ev):
            self.theme = name
            await self.save_pref('theme', name)
            self.t = themes.apply(self.page, name)
            self.page.pop_dialog()
            self.build_main()

        async def change_hub(ev):
            await self.save_pref('hub_url', '')
            await self.save_pref('session', '')
            await self.save_pref('single', '')
            self.page.pop_dialog()
            self.show_connect()

        swatches = [ft.Container(width=36, height=36, border_radius=18, bgcolor=p['bg'],
                                 border=ft.Border.all(3, p['accent'] if name == self.theme else p['border']),
                                 content=ft.Container(width=12, height=12, border_radius=6, bgcolor=p['accent']),
                                 alignment=ft.Alignment.CENTER, tooltip=name, on_click=functools.partial(pick, name))
                    for name, p in themes.THEMES.items()]
        where = ('made-up demo data' if isinstance(self.hub, Demo)
                 else 'this phone, scanned by the app' if isinstance(self.hub, single.Single) and self.hub.is_phone
                 else '%s, over SSH' % self.hub.cfg.get('host') if isinstance(self.hub, single.Single) else self.hub.url)
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('Settings'), content=ft.Column([
            ft.Text('Theme', weight=ft.FontWeight.W_600), ft.Row(swatches, wrap=True, spacing=8),
            ft.Divider(),
            ft.Text('Looking at', weight=ft.FontWeight.W_600), ft.Text(where, color=t['text2'], selectable=True),
            ft.TextButton('Change what the app looks at', icon=ft.Icons.SWAP_HORIZ, on_click=change_hub),
        ], tight=True, spacing=8), actions=[ft.TextButton('Close', on_click=lambda ev: self.page.pop_dialog())]))


async def main(page: ft.Page):
    page.padding = 0
    app = App(page)
    await app.start()


if __name__ == '__main__':
    if os.environ.get('DC_APP_WEB'):
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=int(os.environ.get('DC_APP_PORT', '8551')))
    else:
        ft.run(main)
