# D.C. O'HAIR on your phone

A small app for phones and tablets that shows your [D.C. O'HAIR](../README.md) disk city as lists you can tap into.

- **Drives**: every drive on every device, with what fills it, Used / Free / Total, and the almost-full warning.
- **Browse**: tap into devices, drives and folders, biggest first. **Map** shows the same thing as a treemap you tap into, and **Biggest** lists the largest files anywhere below.
- **City**: the 3D city, right in the app: your hub's own page, or with no hub, the same page served by the app itself.
- **Search**: find any file or folder by name.
- **Alerts**: what needs attention, and **Scan now**.
- The category chips (Music, Games, Series, Movies, Other) filter everything, like on the web page, and the app has the same six themes.

It connects to your hub over your network. If your hub has an admin login, the app asks for the password once. Hubs that sign in with a reverse proxy or OIDC aren't supported in the app yet. Not set up yet? Press **Look around a made-up home network first**.

## No hub? The app can scan by itself

- **Scan one computer over SSH**: fill in its address and user, then let the app in with the key it shows (add the line to `~/.ssh/authorized_keys` on that computer), or type the password when it asks; a typed password is used for that scan only and never saved. **Find its drives** lists the computer's real drives to pick from. The first time, the app shows the computer's fingerprint to check, and it warns you if that ever changes. The computer needs `python3`; Macs need **Remote Login** turned on.
- **Scan this phone** (Android): measures the phone's storage and any SD card. Android asks you to allow **All files access** first; the app only reads, and nothing leaves the phone.

Both use the hub's own read-only scanner and keep the last result on the phone. **Scan now** under Alerts scans again, and Settings → **Change what the app looks at** goes back to a hub.

## Try it on a computer

```sh
cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 sync.py                 # copies ../demo.py and ../agent.py into src/shared
cd src && ../.venv/bin/python main.py
```

## Install it

Each [release](https://github.com/nviiiivn/diskcity/releases) has both builds attached.

- **Android**: download the `.apk` on the phone and open it; Android asks once to allow installing apps from your browser or files app. A newer build may refuse to install over an older one (each build is signed by GitHub's runner); uninstall the old one first.
- **iPhone and iPad**: the `-unsigned.ipa` has to be signed with your own Apple ID before it installs. [AltStore](https://altstore.io) or [Sideloadly](https://sideloadly.io) do that for free; with a free Apple ID the app needs re-signing every 7 days, which AltStore does by itself.

## Build it

The [app workflow](../.github/workflows/app.yml) builds the APK and the IPA on GitHub's free runners and attaches them to each release; run it by hand from the Actions tab for a test build. To build an APK yourself, install the Flet CLI (`pip install flet-cli==1.0.4`), then run `python3 sync.py` and `flet build apk` in `app/`.
