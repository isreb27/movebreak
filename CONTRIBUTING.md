# Contributing to Movebreak

Thanks for helping! This guide covers setting up, the checks every change must pass,
and the conventions the code follows.

## Set up

You need Python 3.11+, PyGObject, GTK 4 and libadwaita 1.5+ (see the README).

```sh
git clone https://github.com/isreb27/movebreak.git
cd movebreak
python3 scripts/install.py --dev   # launcher + desktop file pointing at this checkout
movebreak                          # runs your working copy
```

`--dev` matters: GNOME only shows notifications from apps with an installed desktop
file. Quit the running instance (main menu › Quit) before testing changes, or run
`gapplication action io.github.isreb27.Movebreak quit`.

Development tools (install them however you like; none need administrator rights):

```sh
python3 -m venv --system-site-packages .venv   # system site-packages give access to gi
.venv/bin/pip install -e ".[dev]"
```

On Ubuntu, `python3 -m venv` needs the `python3-venv` package. Without it, install the
tools with [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/).

## Checks

CI runs all of these on every pull request:

```sh
ruff check . && ruff format --check .   # lint and formatting
mypy                                    # strict typing for the core
pytest -m "not gui"                     # unit tests, no display needed
xvfb-run -a dbus-run-session -- python3 -m pytest -m gui   # headless GUI smoke tests
```

The Flatpak job builds `build-aux/flatpak/io.github.isreb27.Movebreak.json`.

## Conventions

- **Keep the core pure.** `movebreak.core` must not import `gi`. Scheduling rules belong
  there, with tests driven by the fake clock in `tests/conftest.py`.
- **Every user-visible string goes through `_()`**, with named placeholders:
  `_("Snooze {minutes} min").format(minutes=10)`.
- **Follow the GNOME HIG** for UI text: header capitalisation for buttons and titles
  ("Add Activity"), sentence case for descriptions.
- **New defaults need evidence.** Changes to built-in activities or timings should cite
  a study in `docs/EVIDENCE.md`.
- **Database changes are new migrations** appended to `_MIGRATIONS` in
  `core/store.py`; never edit a released one.
- **Each source file starts with SPDX headers** (`SPDX-License-Identifier:
  GPL-3.0-or-later`).
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat: add working hours`, `fix(scheduler): ...`, `docs: ...`.
- **Changelog:** add a line under "Unreleased" in `CHANGELOG.md`.

## Releasing

1. Update the version in `src/movebreak/__init__.py` and `pyproject.toml`.
2. Add a `<release>` entry to `data/app.metainfo.xml.in`.
3. Move "Unreleased" entries in `CHANGELOG.md` under the new version.
4. Tag `vX.Y.Z` and push the tag.

## Translations

Strings are marked for gettext but no translations exist yet. If you would like to add
one, open an issue and we will set up the `po/` directory together.
