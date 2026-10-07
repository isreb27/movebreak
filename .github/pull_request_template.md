## What and why

<!-- What does this change, and which issue does it address? -->

## Checklist

- [ ] `ruff check . && ruff format --check .` passes
- [ ] `mypy` passes
- [ ] `pytest` passes (and `xvfb-run -a dbus-run-session -- pytest -m gui` for UI changes)
- [ ] New user-visible strings use `_()`
- [ ] `CHANGELOG.md` updated under "Unreleased"
- [ ] Screenshots attached for UI changes
