# Changelog

All notable changes to D.C. O'HAIR are listed here. Versions follow [semantic versioning](https://semver.org/).

## [Unreleased]

### Planned
- Search with fly-to.
- Drive health (Scrutiny pairing or built-in smartctl), hidden by default.
- Notifications: ntfy, Discord and email.
- OIDC login, and a VoidAuth helper.
- Phone apps on the same JSON API.

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
