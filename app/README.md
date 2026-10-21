# D.C. O'HAIR on your phone

A small app for phones and tablets that shows your [D.C. O'HAIR](../README.md) disk city as lists you can tap into.

- **Drives**: every drive on every device, with what fills it, Used / Free / Total, and the almost-full warning.
- **Browse**: tap into devices, drives and folders, biggest first; switch to **Biggest files** to see the largest files anywhere below.
- **Search**: find any file or folder by name.
- **Alerts**: what needs attention, and **Scan now**.
- The category chips (Music, Games, Series, Movies, Other) filter everything, like on the web page, and the app has the same six themes.

It connects to your hub over your network. If your hub has an admin login, the app asks for the password once. Hubs that sign in with a reverse proxy or OIDC aren't supported in the app yet. Not set up yet? Press **Look around a made-up home network first**.

## Try it on a computer

```sh
cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 sync.py                 # copies ../demo.py and ../agent.py into src/shared
cd src && ../.venv/bin/python main.py
```

## Build it

The release workflow builds the Android APK and the iOS app on GitHub's free runners and attaches them to each release. To build an APK yourself, install the Flet CLI (`pip install flet-cli==1.0.4`) and run `flet build apk` in `app/`.
