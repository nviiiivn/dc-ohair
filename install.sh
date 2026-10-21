#!/usr/bin/env bash
# D.C. O'HAIR bare-metal installer.
#
# Read this before you run it. It:
#   1. checks for python3 and ssh,
#   2. asks a few questions (with gum's nicer prompts when gum is installed),
#   3. copies the hub into an install folder and makes a Python venv there,
#   4. writes devices.yml from your answers (only if there isn't one yet),
#   5. sets up a systemd service so the hub starts on boot,
#   6. tells you the address to open.
# It never changes anything on your other devices; you add the hub's key to
# them yourself (it prints the commands).
#
#   bash install.sh            # ask questions
#   bash install.sh --yes      # take every default, no questions
set -euo pipefail

REPO_URL="${DC_REPO:-https://github.com/nviiiivn/dc-ohair.git}"
YES=0
[[ "${1:-}" == "--yes" ]] && YES=1

if [[ $EUID -eq 0 ]]; then
  APP_DEFAULT=/opt/dc-ohair
  DATA_DEFAULT=/var/lib/dc-ohair
else
  APP_DEFAULT="$HOME/.local/share/dc-ohair"
  DATA_DEFAULT="$HOME/.local/share/dc-ohair/data"
fi

# ---------- prompts: gum when we can, plain read when we can't ----------

USE_GUM=0
if [[ $YES -eq 0 && -t 0 && -t 1 ]] && command -v gum >/dev/null 2>&1; then USE_GUM=1; fi

say() { if [[ $USE_GUM -eq 1 ]]; then gum style --foreground 212 "$*"; else printf '%s\n' "$*"; fi; }
title() {
  if [[ $USE_GUM -eq 1 ]]; then gum style --border rounded --padding "0 2" --border-foreground 214 "$@"
  else printf '\n== %s ==\n' "$1"; shift; printf '%s\n' "$@"; fi
}
ask() {  # ask "question" default
  local q=$1 def=${2:-} ans
  if [[ $YES -eq 1 ]]; then printf '%s' "$def"; return; fi
  if [[ $USE_GUM -eq 1 ]]; then gum input --header "$q" --value "$def"; return; fi
  read -r -p "$q [$def]: " ans </dev/tty || true
  printf '%s' "${ans:-$def}"
}
yesno() {  # yesno "question" default(yes|no)
  local q=$1 def=${2:-yes} ans
  if [[ $YES -eq 1 ]]; then [[ $def == yes ]]; return; fi
  if [[ $USE_GUM -eq 1 ]]; then
    if [[ $def == yes ]]; then gum confirm --default=true "$q"; else gum confirm --default=false "$q"; fi
    return
  fi
  read -r -p "$q [$([[ $def == yes ]] && echo Y/n || echo y/N)]: " ans </dev/tty || true
  ans=${ans:-$def}
  [[ $ans =~ ^[Yy] ]]
}
pick() {  # pick "question" option... -> one option
  local q=$1; shift
  if [[ $YES -eq 1 ]]; then printf '%s' "$1"; return; fi
  if [[ $USE_GUM -eq 1 ]]; then gum choose --header "$q" "$@"; return; fi
  local i=1 o
  printf '%s\n' "$q" >/dev/tty
  for o in "$@"; do printf '  %d) %s\n' "$i" "$o" >/dev/tty; i=$((i + 1)); done
  read -r -p "number [1]: " i </dev/tty || true
  i=${i:-1}
  printf '%s' "${@:$i:1}"
}
pick_many() {  # pick_many "question" option... -> chosen options, one per line (all by default)
  local q=$1; shift
  if [[ $YES -eq 1 ]]; then printf '%s\n' "$@"; return; fi
  if [[ $USE_GUM -eq 1 ]]; then gum choose --no-limit --selected="*" --header "$q" "$@"; return; fi
  local o
  printf '%s\n' "$q" >/dev/tty
  for o in "$@"; do yesno "  include $o?" yes && printf '%s\n' "$o"; done
}

# ---------- 1. what we need ----------

title "D.C. O'HAIR" "Data City Over Here Am I Right?" "This sets up the hub on this machine."
command -v python3 >/dev/null || { echo "python3 is missing. Install it first (apt install python3 python3-venv)."; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "The hub needs python3 3.8 or newer."; exit 1; }
python3 -c 'import venv' 2>/dev/null || { echo "python3's venv module is missing (apt install python3-venv)."; exit 1; }
command -v ssh >/dev/null || say "Note: ssh isn't installed, so the hub can only scan this machine until you add it."

# ---------- 2. questions ----------

APP=$(ask "Install the hub in" "$APP_DEFAULT")
DATA=$(ask "Keep scans and history in" "$DATA_DEFAULT")
PORT=$(ask "Port for the web page" "7171")
AUTH=$(pick "Who can open the page?" "anyone on my network (no login)" "only me, with a password" "my reverse proxy handles logins")
case $AUTH in
  only*) AUTH=password ;;
  my*) AUTH=proxy ;;
  *) AUTH=off ;;
esac

# ---------- 3. files and venv ----------

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$APP" "$DATA"
if [[ -f "$HERE/hub.py" && -f "$HERE/agent.py" ]]; then
  [[ "$HERE" != "$APP" ]] && cp -R "$HERE/agent.py" "$HERE/collect.py" "$HERE/hub.py" "$HERE/demo.py" "$HERE/push.py" "$HERE/discover.py" "$HERE/notify.py" \
    "$HERE/requirements.txt" "$HERE/web" "$HERE/systemd" "$HERE/devices.example.yml" "$APP/"
else
  command -v git >/dev/null || { echo "Run this from a copy of the repo, or install git so it can fetch one."; exit 1; }
  say "Fetching D.C. O'HAIR from $REPO_URL"
  git clone --depth 1 "$REPO_URL" "$APP/src"
  cp -R "$APP/src/." "$APP/"
fi
say "Making a Python venv in $APP/.venv"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --upgrade pip
"$APP/.venv/bin/pip" install --quiet -r "$APP/requirements.txt"

export DC_DATA="$DATA" DC_CONFIG="$APP/devices.yml"

# ---------- 4. this machine's drives, other devices, devices.yml ----------

if [[ -f "$DC_CONFIG" ]]; then
  say "Keeping your existing $DC_CONFIG"
else
  # the agent lists real filesystems (system and pseudo ones filtered out)
  SPEC=$(printf '{"mode":"mounts"}' | base64 | tr -d '\n')
  mapfile -t MOUNTS < <("$APP/.venv/bin/python" "$APP/agent.py" "$SPEC" | "$APP/.venv/bin/python" -c '
import json, sys
for m in json.load(sys.stdin)["mounts"]:
    print("%s  (%s, %.0fG used of %.0fG)" % (m["path"], m["fs"], m["used"] / 2**30, m["total"] / 2**30))')
  CHOSEN=()
  if [[ ${#MOUNTS[@]} -gt 0 ]]; then
    mapfile -t CHOSEN < <(pick_many "Scan which drives on this machine?" "${MOUNTS[@]}")
  fi
  HUBNAME=$(ask "Name for this machine" "$(hostname)")

  OTHERS=()
  while yesno "Add another device the hub should reach over SSH?" no; do
    NAME=$(ask "Its name" "NAS")
    TARGET=$(ask "user@host to ssh to" "me@nas.local")
    DRV=$(ask "A path to scan on it" "/")
    OTHERS+=("$NAME|$TARGET|$DRV")
  done

  printf '%s\n' "${CHOSEN[@]+"${CHOSEN[@]}"}" > "$DATA/.chosen"
  printf '%s\n' "${OTHERS[@]+"${OTHERS[@]}"}" > "$DATA/.others"
  APP_DIR="$APP" "$APP/.venv/bin/python" - "$HUBNAME" "$PORT" "$AUTH" "$DATA/.chosen" "$DATA/.others" <<'PY'
import os, re, sys
sys.path.insert(0, os.environ['APP_DIR'])
import collect
hubname, port, auth, chosen, others = sys.argv[1:]
slug = lambda s: re.sub(r'[^a-z0-9_.-]+', '-', s.lower()).strip('-.') or 'device'
paths = [l.split('  (')[0] for l in open(chosen).read().splitlines() if l.strip()]
drives = [{'name': hubname if p in ('/', os.environ.get('DC_HOSTFS', '')) else os.path.basename(p.rstrip('/')) or p,
           'path': p, **({} if p == '/' else {'mounted': True})} for p in paths]
devices = [{'id': slug(hubname), 'name': hubname, 'local': True, 'drives': drives}] if drives else []
for line in open(others).read().splitlines():
    if line.strip():
        name, target, path = line.split('|')
        devices.append({'id': slug(name), 'name': name, 'ssh': target, 'drives': [{'name': name, 'path': path}]})
cfg = {'hub': {'port': int(port), 'auth': auth}, 'devices': devices}
collect.save_config(cfg)
print('Wrote', collect.CONFIG)
PY
  rm -f "$DATA/.chosen" "$DATA/.others"
fi

# the hub's own ssh key, for your other devices
PUB=$(cd "$APP" && "$APP/.venv/bin/python" -c 'import collect; print(collect.hub_key() + ".pub")' 2>/dev/null || true)

if [[ $AUTH == password ]]; then
  say "Choose the password for the web page."
  (cd "$APP" && "$APP/.venv/bin/python" hub.py --set-password)
fi

# ---------- 5. service ----------

UNIT_SRC="$APP/systemd/dc-ohair.service"
fill() { sed -e "s#@APP@#$APP#g" -e "s#@DATA@#$DATA#g" -e "s#@CONFIG@#$DC_CONFIG#g" -e "s#@USER@#$(id -un)#g" "$UNIT_SRC"; }
if command -v systemctl >/dev/null && yesno "Start the hub now and on every boot (systemd)?" yes; then
  if [[ $EUID -eq 0 ]]; then
    fill > /etc/systemd/system/dc-ohair.service
    systemctl daemon-reload
    systemctl enable --now dc-ohair.service
  else
    mkdir -p "$HOME/.config/systemd/user"
    fill | grep -v '^User=' | sed 's/^WantedBy=multi-user.target/WantedBy=default.target/' > "$HOME/.config/systemd/user/dc-ohair.service"
    systemctl --user daemon-reload
    systemctl --user enable --now dc-ohair.service
    say "Tip: run 'loginctl enable-linger $(id -un)' so the hub keeps running after you log out."
  fi
else
  say "Start it yourself with: cd $APP && DC_DATA=$DATA DC_CONFIG=$DC_CONFIG .venv/bin/python hub.py"
fi

# ---------- 6. done ----------

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
title "Done" "Open http://${IP:-localhost}:$PORT" "The setup wizard there finishes the job (Settings → Devices any time later)."
if [[ -n "$PUB" ]]; then
  say "To let the hub into another device, run this here once per device:"
  printf '  ssh-copy-id -i %s user@device\n' "$PUB"
fi
