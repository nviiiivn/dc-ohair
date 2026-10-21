# Setting up D.C. O'HAIR

This guide covers installing the hub, adding your devices, and putting the page behind a login or a reverse proxy.

- [What you need](#what-you-need)
- [Option A: Docker](#option-a-docker)
- [Option B: Bare metal with install.sh](#option-b-bare-metal-with-installsh)
- [Option C: By hand](#option-c-by-hand)
- [Adding devices over SSH](#adding-devices-over-ssh)
- [Device notes: Mac, LibreELEC, NAS, Windows](#device-notes)
- [Behind a reverse proxy](#behind-a-reverse-proxy)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

---

## What you need

**The hub** is the one machine that serves the page and does the scanning. It can be a Raspberry Pi, a NAS, a home server, or your desktop.

- Linux or macOS
- Python 3.9 or newer (or Docker)
- `ssh` and `ssh-keygen` (OpenSSH client)
- `sshpass`, only if you use `password_cmd` for a device

**Each device you scan** needs:

- an SSH server you can log in to
- `python3`, version 3.6 or newer

Nothing gets installed on the devices. The hub sends a small script each time it scans, and the script doesn't leave anything behind.

---

## Option A: Docker

```sh
git clone https://github.com/nviiiivn/dc-ohair.git
cd dc-ohair
mkdir -p config data   # your settings and your scans live here
docker compose up -d
```

Open **http://your-hub:7171**. The setup wizard starts by itself.

What the compose file sets up:

| Inside the container | From the host | Why |
|---|---|---|
| `/hostfs` | `/`, **read-only** | so the hub can scan the machine it runs on |
| `/data` | `./data` | scan results, history, and the hub's SSH key |
| `/config` | `./config` | your settings (`devices.yml`), written by the wizard and Settings |
| the hub user's `~/.ssh` | `~/.ssh`, read-only (optional) | to reuse keys you already have |

`DC_HOSTFS=/hostfs` tells the agent that the host's drives live under `/hostfs`, so **Find drives** on "This machine" lists the host's real drives rather than the container's. Those drives show up with paths like `/hostfs/mnt/media`, and that's expected.

To add the hub's key to a device from Docker, the wizard gives you a line like:

```sh
docker compose exec dc-ohair ssh-copy-id -i /data/ssh/id_ed25519.pub admin@nas.local
```

<p align="center"><img src="media/wizard-1.png" width="720" alt="The wizard's first step"></p>

---

## Option B: Bare metal with install.sh

Download the installer, **read it**, then run it:

```sh
git clone https://github.com/nviiiivn/dc-ohair.git
cd dc-ohair
less install.sh
./install.sh
```

The installer:

1. makes a Python virtual environment and installs `requirements.txt` (PyYAML, and argon2-cffi for the optional login)
2. asks a few questions: where to install, which port, whether to turn on a login, which of this machine's drives to scan, and which other devices to add
3. writes `devices.yml`
4. sets up a systemd service so the hub starts on boot

If [gum](https://github.com/charmbracelet/gum) is installed and you're in a real terminal, the questions come as a friendly terminal wizard. Otherwise you get plain prompts. Everything it asks can also be done later in the web page.

Please don't run any installer by piping it from the internet straight into a shell. Download it, read it, then run it.

---

## Option C: By hand

```sh
git clone https://github.com/nviiiivn/dc-ohair.git
cd dc-ohair
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python hub.py
```

That's all it takes to run. With no `devices.yml`, the page opens the setup wizard.

### Running it as a service

A systemd unit, for example at `/etc/systemd/system/dc-ohair.service`:

```ini
[Unit]
Description=D.C. O'HAIR hub
After=network-online.target
Wants=network-online.target

[Service]
User=dcohair
WorkingDirectory=/opt/dc-ohair
Environment=DC_CONFIG=/opt/dc-ohair/devices.yml
Environment=DC_DATA=/opt/dc-ohair/data
ExecStart=/opt/dc-ohair/.venv/bin/python hub.py
Restart=on-failure
# Let the hub read every file on this machine without being root.
# Remove this line if you'd rather only scan what the dcohair user can read.
AmbientCapabilities=CAP_DAC_READ_SEARCH

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now dc-ohair
```

To scan the hub machine itself, the hub's user needs read access to those folders. Anything it can't read shows up as **Not seen by the scan**. `CAP_DAC_READ_SEARCH` grants read-only access to everything, and nothing more.

You can also run it as a user service (`~/.config/systemd/user/`, with `WantedBy=default.target`) and `loginctl enable-linger` so it runs without you logged in.

---

## Adding devices over SSH

You can add devices in the setup wizard or in **Settings → Devices → + Add a device**.

<p align="center"><img src="media/settings-device.png" width="720" alt="Editing a device in Settings"></p>

1. **Name** it whatever you like: "Media NAS", "Desktop", "Laptop".
2. Pick **how the hub reaches it**:
   - **This machine**: the hub scans its own drives directly.
   - **SSH key** (recommended): the hub logs in with a key.
   - **password_cmd**: the hub runs a command that prints the password. See [security.md](security.md).
3. Fill in the **Host** (an IP like `192.0.2.10` or a name like `nas.local`), the **User**, and the **Port** if it isn't 22.
4. Let the hub in (next section).
5. Press **Find drives**, tick the ones you want, and press **Add ticked drives**. Or press **+ Add a path** to type one, like `/mnt/movies`.
6. Press **Test connection**. It checks the login, python3, and every drive path.
7. Press **Save**, then **Scan now** if you like.

### Letting the hub in with its own key

The first time it needs one, the hub makes its own SSH key at `~/.config/dc-ohair/ssh/id_ed25519` (in Docker: `/data/ssh/id_ed25519`). Settings shows two ways to add it to a device.

**Easiest: run ssh-copy-id on the hub.** It asks for the device's password once:

```sh
ssh-copy-id -i ~/.config/dc-ohair/ssh/id_ed25519.pub admin@nas.local
```

**Tighter: paste a restricted line on the device.** Settings shows a ready-made block to run *on the device*. It adds one line to `~/.ssh/authorized_keys` that looks like this (shortened):

```text
command="python3 -c \"…only runs the agent whose sha256 matches…\" $SSH_ORIGINAL_COMMAND",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA… dc-ohair@hub
```

With that line, the hub's key can **only** run D.C. O'HAIR's read-only scanner: no shell, no terminal, no port forwarding. The check is tied to the exact `agent.py`, so after you update D.C. O'HAIR, copy the new line from Settings and replace the old one.

**Keys you already have** work too. The hub's user also uses its normal `~/.ssh` keys and ssh-agent.

### Which user to scan as

The scan sees what that user can read. For media folders, a normal user is usually fine. For a whole system disk you'll see some **Not seen by the scan** space (other users' homes, system folders). That's fine, because the totals still come from the drive itself. If you want everything counted, use a user with read access (for example a group with read permission, or root on appliances like LibreELEC where root is the only user).

---

## Device notes

### macOS

- Turn on **System Settings → General → Sharing → Remote Login**.
- `python3` comes with Apple's Command Line Tools. If the test says so, run `xcode-select --install` on the Mac.
- Scan `/System/Volumes/Data`, which is where your files live. **Find drives** offers it.
- Laptops fall asleep in the middle of scans. Under **More**, set **Command prefix** to `caffeinate -i` to keep the Mac awake while it's being scanned.
- Folders protected by macOS privacy controls (Mail, Messages, Photos and so on) show up as **Not seen by the scan** unless you give `sshd` Full Disk Access.

### LibreELEC / Kodi boxes

- Turn on SSH in the LibreELEC settings. The user is `root`.
- Keys go in `/storage/.ssh/authorized_keys`, which `ssh-copy-id` handles.
- Media drives are under `/var/media/…`. Tick **mount** for them so an unplugged drive is skipped instead of scanned as empty.

### NAS boxes (Synology, TrueNAS, Unraid, plain Linux)

- Turn on SSH, and make sure `python3` is installed (on Synology it's in the Package Center).
- Scan the shares' real paths, like `/volume1/video`.

### Windows

Not supported yet. The scanner needs a POSIX system. A **push agent** for Windows (a PowerShell one-liner) is on the [roadmap](../README.md#roadmap). Until then, if a Windows disk is also mounted on a Linux machine (dual boot, or a disk in a dock), you can scan it there, read-only.

### Drives that come and go

Tick **mount** (`mounted: true`) for USB disks and anything not always plugged in. If the path isn't a mount point when the scan runs, that drive is skipped with "not mounted", and the city keeps its last good scan.

### Leaving things out

Each drive has a **leave out** box: comma-separated patterns matched against the full path or the folder or file name, like `/var/lib/docker, */node_modules, *.part`.

---

## Behind a reverse proxy

Running the hub at something like `https://disks.example.com`? Point your proxy at port 7171. With Caddy:

```caddyfile
disks.example.com {
    reverse_proxy 127.0.0.1:7171
}
```

To add a login there, either turn on the hub's own **Admin login** (Settings → Security), or let your proxy's auth handle it and set the hub to **My reverse proxy handles it**. [security.md](security.md) has examples for Authelia, oauth2-proxy and similar.

If only the proxy should reach the hub, set `hub.bind: 127.0.0.1`.

---

## Updating

```sh
cd dc-ohair
git pull
sudo systemctl restart dc-ohair        # or: docker compose up -d --build
```

Your `devices.yml` and `data/` aren't touched. If you used the **restricted** `authorized_keys` line on any device, copy the new line from Settings and replace the old one on those devices, because it's tied to the exact scanner version.

---

## Troubleshooting

**Test connection** explains most problems in plain words. The common ones:

| Message | What to do |
|---|---|
| The device turned the hub's key away | Add the hub's key (ssh-copy-id, or the restricted line), or use password_cmd. |
| Nothing is answering SSH on *host* | Turn on SSH on the device (Remote Login on a Mac). |
| Can't reach *host* | Is it on, awake, and on the same network? |
| The hub can't find a device called *host* | Use its IP address instead. |
| python3 isn't installed on *host* | Install it with the device's package manager. |
| This Mac needs Apple's Command Line Tools | Run `xcode-select --install` on the Mac. |
| This device's SSH identity changed | Expected after a reinstall, or if it booted into another OS. Otherwise, stop and find out why. When you're sure, run `ssh-keygen -R host` on the hub. |
| password_cmd printed nothing | Run the command yourself as the hub's user and check what it prints. |
| The device only accepts an older agent | Replace that device's restricted `authorized_keys` line with the new one from Settings. |
| *path*: can't read it (permission denied) | Scan as a user who can read it, or leave it out. |

**A scan seems stuck.** A sleeping laptop is the usual cause. Use `caffeinate -i` on Macs, and keep laptops awake for the scan. Scans give up after `scan.timeout_hours` (4 by default).

**The numbers don't add up to Used.** Some space can't be counted: other users' folders, system files, or filesystem overhead. It's shown as **Not seen by the scan**. On btrfs, compression and shared extents can make the files add up to *more* than Used. exFAT counts unfinished downloads at full size.
