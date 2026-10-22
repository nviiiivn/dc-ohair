# Changelog

All notable changes to Disk City are listed here. Versions follow [semantic versioning](https://semver.org/).

## [Unreleased]

### Added
- **dc-agent**, the agent with nothing to install (`agent-go/`): one file for Windows, macOS (Intel and Apple Silicon), Linux (x86, 64-bit and 32-bit ARM), LibreELEC and NAS boxes, and FreeBSD. It scans exactly as `agent.py` does and does what `dc-push.py` does (install with the hub's token, a daily schedule, signed reports), so a Windows PC no longer needs Python first. Settings' install lines now download it from the hub (`/dl/`), with the Python line underneath as a fallback. It keeps itself the hub's version, and moves its folder aside on uninstall rather than deleting it.
- The agents are built in GitHub Actions on every change and attached to each release.

## [0.8.0] - the phone app

### Added
- **Phone and tablet app** (`app/`, built with Flet): Drives, Browse with a tappable treemap, the 3D City, Search and Alerts, on your hub's JSON API.
- **No hub needed**: the app can scan one computer over SSH (its own key, or a password typed for that scan only) or, on Android, the phone itself, with the same read-only scanner. The 3D City tab works there too: the app serves the page itself, to its own WebView only.

### Changed
- The everyday name is now **Disk City**. D.C. O'HAIR (Data City Over Here Am I Right?) stays as its long-form name.

### Fixed
- The Games rule `/emulat` no longer matches Android's `/storage/emulated/`, so a phone's files aren't all counted as Games. The scanner changed, so if you use a restricted `authorized_keys` line, replace it with the new one from Settings.

## [0.7.0] - sign in with OIDC, and a VoidAuth helper

### Added
- **Sign in with OIDC** (VoidAuth, Authelia, Authentik, Keycloak, Google…): the authorization-code flow with PKCE, an allowed-users list, and the client secret from an environment variable or a command, never the config.
- **Set up VoidAuth for me** in Settings → Security writes both setups ready to paste: VoidAuth's declared OIDC app plus the hub's settings, or a Caddy forward-auth block that still lets push agents through with their tokens.
- `python3 hub.py --auth-off` turns the login off again if a setting locks you out.

## [0.6.1] - phones, tablets and a fresh coat

### Changed
- On phones and portrait tablets the drive table is a **bottom sheet**: drag or tap its handle, and tapping a drive flies there and tucks the sheet away.
- Touch works like a map: one finger moves around, a pinch zooms, two fingers turn and tilt.
- A tighter toolbar on phones, the info card starts small there, and the camera stands back far enough to fit the city on a tall screen.
- The repository is now github.com/nviiiivn/diskcity; the product is still D.C. O'HAIR.
- New screenshots and GIFs throughout the README.

## [0.6.0] - alerts and notifications

### Added
- An **alert banner** under the toolbar for drives over the full line and failing drives (health warnings only while Health is on). Tap a line to fly to the drive; hide them for the session.
- **Notifications** to ntfy, Discord, email (SMTP, STARTTLS or SSL) and plain JSON webhooks, set up in Settings → Alerts with a test button each. Only changes are sent: new or worse alerts, and ones that cleared. Optional alerts for devices that couldn't be scanned.
- Secrets for channels can come from `url_cmd` and `smtp_password_cmd`, so they never have to sit in the config.

### Changed
- `hub.alert_webhook` still works, as an ntfy channel; Settings moves it into the new list.
- With login off, any command setting (`password_cmd`, `url_cmd`, `smtp_password_cmd`, `scrutiny_token_cmd`) can only be added from a browser on the hub itself, or in the config file.

## [0.5.0] - drive health, hidden by default

### Added
- **Drive health** from an existing Scrutiny (matched by WWN or serial) or built-in SMART (`smartctl -a -j`, with `-d sat` for USB enclosures). Hidden until you press **Health**: then the table shows OK / Warning / Failing with temperature, hours and sector counts, and drives get a green, amber or red rim in the 3D views. Each drive's info card always shows its health.
- A small red dot marks a drive that is actually failing, even with Health off; Settings → Health can turn it off.
- The scanner reports each drive's disk and its /dev/disk/by-id names.

## [0.4.0] - search and the list view

### Added
- **Search** across every drive on every device by name, from the toolbar or with **/**. Picking a result opens the folder it's in and flies the camera to it.
- **List view**: a plain table of what's in the current folder, or the biggest files anywhere below it, sortable by name, size or date, and following the category filter.

## [0.3.0] - find devices on my network

### Added
- **Find devices on my network** in the wizard and Settings → Devices: an opt-in search of the hub's own private network (or one you name, up to a /22) for machines answering on SSH, SMB, Mac file sharing or Synology ports. Each one shows its name (reverse DNS or Bonjour/mDNS), open ports, a guess at its system from the SSH greeting, and the network card's maker when an OUI list is installed. Tick yes for the ones to add; machines without SSH are set up to send their own reports. "+ Add your own" covers anything it missed.

## [0.2.0] - the push agent

### Added
- **Push agent** (`push.py`) for devices the hub can't reach, Windows included: one copy-paste command per device (Linux, macOS, LibreELEC / Kodi, NAS, PowerShell for Windows) with a per-device enrollment token, a daily schedule (systemd timer, cron, launchd or Task Scheduler), and a "Waiting for the device…" screen that turns green on the first report.
- Enrollment tokens are stored hashed and can be revoked in Settings → Security.
- A push device with no drives picked reports its own real drives first.
- The scanner runs on Windows (drive letters, `shutil.disk_usage`).
- `hub.public_url` for the address devices report to.

### Fixed
- Once the hub had its own SSH key, devices that only knew the user's usual key turned the hub away, because naming a key with `-i` makes ssh skip its default keys. The hub now offers both.

## [0.1.0] - first release

### Added
- **Three views**: City (nested treemap), Circle (rings of platforms), and EPCOT (the hub as a mainframe, devices on a ring, drives as villages).
- **Tap or click to go inside**, one level at a time, with a Back button, breadcrumbs, and Esc. Fly with WASD and Q/E; pinch and drag on touch.
- **Every file of 50 MB or more is its own building.** Big folders are grouped into blocks by letter, year or size band.
- **Sort** by type, size, newest, oldest or name.
- **Category filter**: the legend chips hide or show Music, Games, Series, Movies and Other everywhere. Double-tap a chip to show only that one.
- **Drive table** with Used, Free, Total and each category, composition bars, 90-day growth sparklines, and an "almost full" marker.
- **Free space** shown as empty lots, and **Not seen by the scan** for space the scan couldn't count.
- **Six themes** (dark, light, sepia, cyberpunk, matrix, ocean), each with its own color-blind-checked category palette. The theme can follow a cookie shared with your other sites.
- **Hideable side pane**, minimizable info card, and hideable legend.
- **Settings page**: devices (add, edit, test, find drives, scan now), scanning rules, alerts, look, security, and config import/export.
- **First-run setup wizard**: this machine, other devices, first scan.
- **SSH key handling**: the hub makes its own key, with an `ssh-copy-id` line or a restricted `authorized_keys` line that only runs the exact read-only scanner.
- **password_cmd** for devices without keys; the password is never stored.
- **Optional page login**: off (the default), an admin login (argon2, httpOnly cookie, rate-limited), or trust a reverse proxy's user header.
- **Scheduled scans**, used-space history, and an optional alert webhook.
- **JSON API** for drives, alerts, history, status and settings.
- **Demo mode** (`--demo`) with made-up devices.
- **Docker** and **bare-metal** installs (`install.sh` with a gum terminal wizard, and plain prompts as a fallback).
- **Scanner details**: read-only, hard links counted once, btrfs-aware filesystem boundaries, `.snapshots` skipped, exclude patterns, `mounted: true` for removable drives, and `command_prefix` (for example `caffeinate -i` on Macs).
