# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end smoke tests of the real application.

They need GTK 4, libadwaita, a display and a session bus, so they are
skipped unless those exist. Run them with:

    xvfb-run -a dbus-run-session -- python3 -m pytest -m gui
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.gui

SRC = Path(__file__).resolve().parents[1] / "src"


def _gui_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


skip_without_gui = pytest.mark.skipif(
    not _gui_available(), reason="needs GTK 4, libadwaita, a display and a D-Bus session"
)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MOVEBREAK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


@skip_without_gui
def test_window_editor_profiles_and_reminder_flow(isolated_home: Path) -> None:
    from gi.repository import GLib

    from movebreak.application import MovebreakApplication
    from movebreak.core.models import Outcome
    from movebreak.doctor import format_report, run_checks

    app = MovebreakApplication(application_id="io.github.isreb27.Movebreak.SmokeTest")
    errors: list[object] = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda *exc: errors.append(exc)  # PyGObject reports callback errors here.

    def steps() -> Iterator[None]:
        window = app.get_active_window()
        assert window is not None
        yield

        # Activities page, editor for a built-in and for a new activity.
        window.show_page("activities")
        page = window.activities_page
        page.open_editor("walk")
        yield
        dialog = window.get_visible_dialog()
        assert dialog is not None
        dialog.close()
        yield

        page.open_editor(None)
        yield
        editor = window.get_visible_dialog()
        editor._name.set_text("Push-ups")
        editor._interval.set_value(120)
        editor._duration.set_value(60)
        editor._on_save(editor._save)
        yield
        yield
        assert "Push-ups" in [a.name for a in app.store.activities()]
        assert "Push-ups" in [a.name for a in app.store.plan()]

        # Profiles.
        app.set_active_profile("light")
        page.rebuild()
        busy = app.store.create_profile("Busy week", copy_from="light")
        app.set_active_profile(busy.id)
        page.rebuild()
        yield
        assert app.store.active_profile().name == "Busy week"
        app.set_active_profile("recommended")
        page.rebuild()

        # Dialogs.
        window.show_preferences()
        yield
        window.get_visible_dialog().close()
        window.show_about()
        yield
        window.get_visible_dialog().close()
        yield

        # Pause and resume.
        app.pause(30)
        window.refresh()
        assert app.scheduler.paused
        app.resume()
        assert not app.scheduler.paused

        # Force a reminder and answer it through the notification action.
        app.scheduler._timers["walk"].elapsed = 10_000
        app._on_tick()
        prompt = app.scheduler.prompt
        assert prompt is not None and prompt.primary.id == "walk"
        app.activate_action("prompt-response", GLib.Variant("(us)", (prompt.id, "done")))
        yield
        assert app.scheduler.prompt is None
        assert app.today_counts()[Outcome.DONE] == 1

        window.show_page("today")
        window.today_page.refresh()
        assert format_report(run_checks(app))
        yield

    iterator = steps()

    def drive() -> bool:
        try:
            next(iterator)
        except StopIteration:
            app.quit()
            return GLib.SOURCE_REMOVE
        except BaseException as error:  # Surface assertion failures from inside the loop.
            errors.append(error)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(150, drive)
    try:
        status = app.run(["movebreak"])
    finally:
        sys.excepthook = previous_hook
    assert errors == [], errors
    assert status == 0


@skip_without_gui
def test_command_line_talks_to_the_running_instance(isolated_home: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC)}

    def movebreak(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "movebreak", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    not_running = movebreak("status")
    assert not_running.returncode == 1
    assert "not running" in not_running.stderr

    service = subprocess.Popen(
        [sys.executable, "-m", "movebreak", "--background"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _attempt in range(50):
            status = movebreak("status")
            if status.returncode == 0:
                break
            time.sleep(0.2)
        assert status.returncode == 0, status.stderr
        assert "Profile: Recommended" in status.stdout
        assert "Walk break" in status.stdout

        paused = movebreak("pause", "15")
        assert paused.returncode == 0 and "Paused until" in paused.stdout
        assert movebreak("resume").returncode == 0

        switched = movebreak("profile", "light")
        assert switched.returncode == 0 and "Light" in switched.stdout
        listed = movebreak("profile")
        assert "* Light" in listed.stdout

        doctor = movebreak("doctor", "quick")
        assert doctor.returncode == 0
        assert "Movebreak doctor" in doctor.stdout

        unknown = movebreak("dance")
        assert unknown.returncode == 2

        version = movebreak("--version")
        assert version.stdout.startswith("Movebreak ")
    finally:
        service.terminate()
        service.wait(timeout=10)
