"""Opt-in network discovery for the setup wizard.

Looks for machines on the hub's own local network that answer on SSH or
file-sharing ports, so you can tick the ones to add. It only tries to open
a TCP connection to a handful of ports on private addresses; it doesn't log
in, and it never runs unless someone presses "Find devices".
"""
import concurrent.futures, ipaddress, os, re, socket, struct, subprocess, time

PORTS = {22: 'SSH', 445: 'SMB', 548: 'Mac file sharing', 5000: 'Synology', 5001: 'Synology'}
MAX_HOSTS = 1024
OUI_FILES = ['/usr/share/ieee-data/oui.txt', '/usr/share/nmap/nmap-mac-prefixes', '/usr/share/arp-scan/ieee-oui.txt',
             '/usr/share/wireshark/manuf']
_oui = None


def hub_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))  # picks the outgoing interface; nothing is sent
        return s.getsockname()[0]
    finally:
        s.close()


def network(cidr=None):
    """The network to look through: the given one, or the hub's own /24. Private ranges only."""
    net = ipaddress.ip_network(cidr or '%s/24' % hub_address(), strict=False)
    if net.version != 4 or not net.is_private or net.is_loopback:
        raise ValueError('Only private IPv4 networks (like 192.168.1.0/24) can be searched.')
    if net.num_addresses > MAX_HOSTS:
        raise ValueError('That network is too big; use a /22 or smaller.')
    return net


def _open_ports(ip):
    found = []
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.35)
            if s.connect_ex((ip, port)) == 0:
                found.append(port)
    return found


def _ssh_banner(ip):
    try:
        with socket.create_connection((ip, 22), timeout=0.8) as s:
            s.settimeout(0.8)
            return s.recv(200).decode(errors='replace').strip().splitlines()[0][:120]
    except (OSError, IndexError):
        return ''


def _name(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ''


def _dns_name(buf, off):
    labels, jumped, end = [], False, off
    for _ in range(64):
        n = buf[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            if not jumped:
                end = off + 2
            off, jumped = ((n & 0x3F) << 8) | buf[off + 1], True
            continue
        labels.append(buf[off + 1:off + 1 + n].decode(errors='replace'))
        off += 1 + n
    return '.'.join(labels), (end if jumped else off)


def mdns_names(ips, wait=1.5):
    """Asks the local network over multicast DNS (Bonjour/Avahi) for each address's name."""
    names = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        for ip in ips:
            q = '.'.join(reversed(ip.split('.'))) + '.in-addr.arpa'
            qname = b''.join(bytes([len(p)]) + p.encode() for p in q.split('.')) + b'\0'
            s.sendto(struct.pack('>6H', 0, 0, 1, 0, 0, 0) + qname + struct.pack('>HH', 12, 0x8001), ('224.0.0.251', 5353))
        until = time.time() + wait
        while time.time() < until:
            try:
                buf, _ = s.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                qd, an = struct.unpack('>HH', buf[4:8])
                off = 12
                for _ in range(qd):
                    off = _dns_name(buf, off)[1] + 4
                for _ in range(an):
                    owner, off = _dns_name(buf, off)
                    rtype, _, _, rdlen = struct.unpack('>HHIH', buf[off:off + 10])
                    off += 10
                    if rtype == 12 and owner.endswith('.in-addr.arpa'):
                        ip = '.'.join(reversed(owner[:-len('.in-addr.arpa')].split('.')))
                        names[ip] = _dns_name(buf, off)[0].rstrip('.')
                    off += rdlen
            except (struct.error, IndexError):
                continue
        s.close()
    except OSError:
        pass
    return names


def own_addresses():
    try:
        out = subprocess.run(['hostname', '-I'], stdout=subprocess.PIPE, universal_newlines=True, timeout=3).stdout
        return set(out.split()) | {hub_address()}
    except (OSError, subprocess.SubprocessError):
        return {hub_address()}


def _arp():
    """ip -> MAC from the kernel's neighbour table (Linux); empty elsewhere."""
    out = {}
    try:
        with open('/proc/net/arp') as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[3] != '00:00:00:00:00:00':
                    out[parts[0]] = parts[3].upper()
    except OSError:
        pass
    return out


def vendor(mac):
    """Maker of a network card from its MAC, if an OUI list is installed (e.g. Debian's ieee-data)."""
    global _oui
    if not mac:
        return ''
    if int(mac[1], 16) & 2:
        return 'private (random) address'
    if _oui is None:
        _oui = {}
        for path in OUI_FILES:
            if not os.path.exists(path):
                continue
            with open(path, errors='replace') as f:
                for line in f:
                    m = re.match(r'^([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})\s+(?:\(hex\)\s+)?(.+)$', line.strip())
                    if m:
                        _oui.setdefault((m.group(1) + m.group(2) + m.group(3)).upper(), m.group(4).split('\t')[-1].strip())
            break
    return _oui.get(mac.replace(':', '')[:6], '')


def os_hint(banner, ports):
    b = banner.lower()
    for word, name in (('windows', 'Windows'), ('raspbian', 'Raspberry Pi OS'), ('ubuntu', 'Ubuntu'), ('debian', 'Debian'),
                       ('freebsd', 'FreeBSD'), ('dropbear', 'small Linux box')):
        if word in b:
            return name
    if 548 in ports:
        return 'Mac'
    if 5000 in ports or 5001 in ports:
        return 'NAS'
    if 445 in ports and 22 not in ports:
        return 'Windows?'
    return ''


def discover(cidr=None):
    net = network(cidr)
    me = hub_address()
    mine = own_addresses()
    hosts = [str(h) for h in net.hosts() if str(h) not in mine]
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        ports = dict(zip(hosts, pool.map(_open_ports, hosts)))
        live = [h for h in hosts if ports[h]]
        names = dict(zip(live, pool.map(_name, live)))
        banners = dict(zip(live, pool.map(lambda h: _ssh_banner(h) if 22 in ports[h] else '', live)))
    bonjour = mdns_names([h for h in live if not names[h]])
    arp = _arp()
    out = []
    for h in live:
        mac = arp.get(h, '')
        out.append({'ip': h, 'name': names[h] or bonjour.get(h, ''), 'ports': [PORTS[p] for p in ports[h]], 'ssh': 22 in ports[h],
                    'banner': banners[h], 'os': os_hint(banners[h], ports[h]), 'mac': mac, 'vendor': vendor(mac)})
    return {'network': str(net), 'hub': me, 'hosts': sorted(out, key=lambda d: ipaddress.ip_address(d['ip']))}


if __name__ == '__main__':
    import json, sys
    print(json.dumps(discover(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
