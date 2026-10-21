# Contributing to Disk City

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Run it locally

```sh
git clone https://github.com/nviiiivn/diskcity.git
cd diskcity
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python hub.py --demo
```

Open http://localhost:7171. Demo mode uses made-up devices, so you can work on the page without scanning anything. Open http://localhost:7171/#setup to see the setup wizard.

To try it against real drives, run `.venv/bin/python hub.py` and use the wizard. It writes `devices.yml` and `data/`, which git ignores.

There's no build step. The page is plain HTML, CSS and JavaScript modules in `web/`. Edit, then reload.

## Run the tests

```sh
python3 -m unittest discover -s tests
```

CI runs them on every supported Python version and syntax-checks the JavaScript.

## How the code is laid out

| File | What it does |
|---|---|
| `agent.py` | The scanner that runs on each device. Read-only. |
| `collect.py` | Runs the agent (locally or over SSH), saves results, keeps history, and loads and saves the config. |
| `hub.py` | The web server: the page, the JSON API, logins, scheduled scans. |
| `demo.py` | The made-up devices for `--demo` and the screenshots. |
| `web/app.js` | The 3D city (three.js), the table, the controls. |
| `web/settings.js` | Settings, login, and the setup wizard. |
| `web/themes.css` | The six themes and their category colors. |

## Things to keep in mind

- **The agent stays tiny.** `agent.py` must run on Python 3.6+ with only the standard library, because it runs on whatever the devices have. No f-strings with `=`, no walrus operator, no new dependencies.
- **The agent only reads.** It must never write, move or delete anything on a device. Pull requests that change that won't be merged.
- **The hub stays small.** It uses the standard library plus PyYAML (and argon2-cffi for logins). Please talk to us in an issue before adding a dependency.
- **No passwords on disk.** Device logins use keys or `password_cmd`. Never add a way to store a device password.
- **Every write needs `X-DC: 1`.** Keep that rule for any new `POST`/`PUT` endpoint, and check whether the endpoint needs `need_admin()`.
- **Keep the API stable.** Tools and future phone apps will depend on `/api/*`. Add fields rather than renaming or removing them.
- **Colors come from the theme.** New UI should use the CSS variables in `web/themes.css`, and new category colors must be checked for color-blind separation against each theme's background.
- **Plain words in the UI.** Error messages should say what happened and what to do, in everyday language.
- **No personal data in the repo.** Use example values in docs, tests and screenshots: `192.0.2.x` addresses, `nas.local`, `me`, `admin`, and drives like Media or Movies. Take screenshots that show connection details in demo mode.

## Pull requests

1. Fork, make a branch, make your change.
2. Add or update tests when you change behavior.
3. Run the tests.
4. Describe what changed and why. Include a screenshot for UI changes, taken in demo mode.

## Security problems

Please don't file those as public issues. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contribution is released under the project's [PolyForm Noncommercial License 1.0.0](LICENSE).
