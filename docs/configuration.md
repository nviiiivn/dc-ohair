# Configuration reference

D.C. O'HAIR keeps its settings in one YAML file, `devices.yml`. Git ignores it, because it holds your hosts and users.

You rarely need to edit it by hand: the setup wizard and **Settings** write it for you. When the page saves it, the file is rewritten (so comments are lost), the previous version is kept as `devices.yml.bak`, and the file is made readable only by its owner.

A complete example is in [`devices.example.yml`](../devices.example.yml).

- [Where things live](#where-things-live)
- [hub](#hub)
- [scan](#scan)
- [devices](#devices)
- [Command-line options](#command-line-options)
- [Files in data/](#files-in-data)

---

## Where things live

| Environment variable | Default | What it is |
|---|---|---|
| `DC_CONFIG` | `./devices.yml` | the config file |
| `DC_DATA` | `./data` | scan results, history, sessions, and the hub's SSH key |
| `DC_HOSTFS` | (unset) | in Docker, where the host's filesystem is mounted (`/hostfs`), so **Find drives** lists the host's drives |

If the config file doesn't exist, the hub starts with no devices and the page opens the setup wizard.

---

## hub

```yaml
hub:
  port: 7171
  bind: 0.0.0.0
  auth: "off"
  schedule: ["04:30"]
  alert_percent: 90
  alert_webhook: ""
  history_days: 400
  ui:
    view: city
    units: binary
  theme:
    default: dark
    cookie: dc-theme
    domain: ""
    storage: dc-theme
```

| Option | Default | What it does |
|---|---|---|
| `port` | `7171` | The port the page and API listen on. |
| `bind` | `0.0.0.0` | The address to listen on. Use `127.0.0.1` when a reverse proxy on the same machine should be the only way in. |
| `auth` | `"off"` | Who can open the page: `"off"` (anyone who can reach it), `password` (the built-in admin login), or `proxy` (your reverse proxy does the login). Quote `"off"`, because YAML reads a bare `off` as "false". Change it in **Settings → Security**. See [security.md](security.md). |
| `admin_password` | (none) | The **hash** of the admin password (argon2). Never write a password here. Set it in Settings → Security, or with `python3 hub.py --set-password`. |
| `trusted_proxies` | `["127.0.0.1", "::1"]` | For `auth: proxy`: the proxy addresses whose user header is believed. |
| `proxy_user_header` | `Remote-User` | For `auth: proxy`: the header your proxy sets to the logged-in user's name. |
| `schedule` | `["04:30"]` | Daily scan times on a 24-hour clock, in the hub's timezone. A list, so `["04:30", "16:00"]` scans twice a day. **Quote the times**: YAML reads a bare `04:30` as the number 270. The hub copes with that, but quoting is clearer. An empty list turns scheduled scans off. |
| `alert_percent` | `90` | Drives this full or fuller get a **⚠ N% full** marker in the table and show up in `/api/alerts`. |
| `alert_webhook` | `""` | Optional. A URL that gets a short POST the first time a drive crosses `alert_percent`. An [ntfy](https://ntfy.sh) topic URL works as-is. |
| `history_days` | `400` | How long to keep used/free history, which feeds the sparklines in the table. |
| `ui.view` | `city` | The default view for someone who hasn't picked one: `city`, `circle` or `epcot`. |
| `ui.units` | `binary` | `binary` shows sizes in powers of 1024 (a "2 TB" drive shows as 1.82T). `decimal` uses powers of 1000 (2.00TB). |
| `theme.default` | `dark` | The theme for someone who hasn't picked one: `dark`, `light`, `sepia`, `cyberpunk`, `matrix` or `ocean`. |
| `theme.cookie` | `dc-theme` | The cookie that remembers each browser's theme pick. |
| `theme.domain` | `""` | Set it to something like `.example.com` to share that cookie with your other subdomains, so a theme picked on one of your sites carries over here. |
| `theme.storage` | `dc-theme` | The localStorage key used as a fallback. |

---

## scan

```yaml
scan:
  min_size: 50M
  depth: 12
  timeout_hours: 4
  categories: {}
```

| Option | Default | What it does |
|---|---|---|
| `min_size` | `50M` | Files and folders at least this big become their own buildings. Anything smaller is folded into one "…" block per folder, though it still counts in every total. Takes `K`, `M`, `G` or `T` (powers of 1024). |
| `depth` | `12` | How many folder levels below each drive are kept as separate blocks. Deeper folders still count in the totals. |
| `timeout_hours` | `4` | Give up on a device after this long. |
| `categories` | `{}` | Your own rules for what counts as what. Leave a key out (or empty) to keep the built-in rule. |

### categories

```yaml
scan:
  categories:
    music: [.flac, .mp3, .m4a, .opus]
    video: [.mkv, .mp4, .avi, .m4v]
    series: ['[Ss]\d{1,2}[Ee]\d{1,3}', 'season[ ._-]?\d', /series/, /tv/, /shows/]
    games: [/steamapps/, /steam/, /games/, /roms?/]
    big_video: 104857600
```

Each file lands in exactly one category, checked in this order:

1. **Games**: the folder path or the file name matches any `games` pattern (case-insensitive). Everything under a matching folder counts as Games.
2. **Music**: the file ends in one of the `music` extensions.
3. **Series** or **Movies**: the file ends in one of the `video` extensions *and* is at least `big_video` bytes (100 MiB by default). If its full path matches a `series` pattern it's Series; otherwise it's a Movie.
4. **Other**: everything else, including small videos like clips and samples.

`series` and `games` are regular expressions, joined with "or". Plain text like `/tv/` works as-is. Quote anything with backslashes, in single quotes, so YAML leaves the backslashes alone.

The same rules can be edited in **Settings → Scanning**, where the empty boxes show the built-in values.

---

## devices

```yaml
devices:
  - id: nas
    name: Media NAS
    note: Kodi box
    host: 192.0.2.10
    user: admin
    port: 22
    drives:
      - name: Movies
        path: /mnt/movies
        mounted: true
        exclude: ["*/.Trash*", "*.part"]
```

| Option | What it does |
|---|---|
| `id` | A short unique name made of letters, digits, `-`, `_` or `.`. It's used for file names in `data/`. The page makes one from the name. |
| `name` | What the city and table call it. |
| `note` | Optional. Shown next to the name in the table. |
| `local: true` | This is the machine the hub runs on, so it's scanned directly with no SSH. |
| `ssh` | `user@host`. Older shorthand for `user` + `host`; either form works. |
| `host`, `user`, `port` | How to reach it over SSH. `port` is optional (22). |
| `password_cmd` | Optional. A command the hub runs at scan time; whatever it **prints** is used as the SSH password (via `sshpass`, through an environment variable). Write it as a string to run it through the shell, or as a list (`["pass", "show", "nas/ssh"]`) to run it without one. The password is never saved. See [security.md](security.md). |
| `command_prefix` | Optional. Put in front of the scanner on the device, such as `caffeinate -i` (keeps a Mac awake) or `nice -n 19` (keeps the scan polite). |
| `drives` | The drives or folders to scan. |

Without `password_cmd`, the hub logs in with keys only and never waits at a password prompt: its own key (in `~/.config/dc-ohair/ssh/`, or `/data/ssh/` in Docker), plus the hub user's usual SSH keys and agent.

### drives

| Option | What it does |
|---|---|
| `name` | What the city and table call it. |
| `path` | An absolute path on that device, such as `/`, `/mnt/movies` or `/System/Volumes/Data`. |
| `mounted: true` | Only scan it if `path` is a mount point right now. Use it for USB drives and anything that comes and goes, so an unplugged drive is reported as "not mounted" instead of scanned as an empty folder. |
| `exclude` | Patterns to leave out. Each one is matched against the full path *and* against the file or folder name, with shell-style wildcards: `/var/lib/docker`, `*/node_modules`, `*.part`. (`skip`, a list of exact paths, still works too.) |

A scan never crosses into a different filesystem below `path`. On btrfs, subvolumes such as `/home` count as the same filesystem, and `.snapshots` folders are skipped so snapshots aren't counted twice.

---

## Command-line options

```text
python3 hub.py [--demo] [--config FILE] [--port N] [--bind ADDR] [--no-schedule] [--set-password]
```

| Option | What it does |
|---|---|
| `--demo` | Serve made-up devices instead of your config. Settings changes stay in memory, and nothing is scanned. |
| `--config FILE` | Use this config file instead of `$DC_CONFIG` or `./devices.yml`. |
| `--port N`, `--bind ADDR` | Override `hub.port` and `hub.bind`. |
| `--no-schedule` | Don't run scheduled scans. "Scan now" still works. |
| `--set-password` | Ask for an admin password in the terminal, save its hash, turn the login on, and exit. |

`python3 collect.py [device-id …]` runs a scan straight away from the terminal, without the web page.

---

## Files in data/

| File | What's in it |
|---|---|
| `<id>.json` | The latest scan of that device: the size tree for each drive. |
| `status.json` | When each device was last scanned, and whether it worked. |
| `history/<id>.jsonl` | One line per drive per scan (used, free, total, categories), for the sparklines. |
| `alerts.json` | Which drives were over the line last time, so each alert is only sent once. |
| `sessions.json` | Login sessions, stored as hashes only. |
| `ssh/id_ed25519` | The hub's own SSH key. Keep it private. |
| `snapshot/<id>.json` | Optional. Hand-imported results in the same format as `<id>.json`, shown (marked "snapshot") until a live scan of that drive works. |

All of `data/` is ignored by git.
