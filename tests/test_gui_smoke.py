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


def _wait_for_name(name: str, *, owned: bool, timeout_s: float = 15.0) -> bool:
    """Wait until ``name`` is (or is no longer) owned on the session bus."""
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        (has_owner,) = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        ).unpack()
        if has_owner == owned:
            return True
        time.sleep(0.05)
    return False


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
    # That short-lived process owned the app's bus name; let the bus release it.
    assert _wait_for_name("io.github.isreb27.Movebreak", owned=False)

    service = subprocess.Popen(
        [sys.executable, "-m", "movebreak", "--background"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait on the bus instead of polling with `movebreak status`: each probe
        # would race the starting service for the application's bus name.
        if not _wait_for_name("io.github.isreb27.Movebreak", owned=True):
            service.terminate()
            _out, service_err = service.communicate(timeout=10)
            pytest.fail(f"the service never registered:\n{service_err}")
        status = movebreak("status")
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


FAKE_TRAY_HOST = """
from gi.repository import Gio, GLib
XML = '''<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
  <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
</interface></node>'''
info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]

def call(conn, sender, path, iface, method, params, invocation):
    print("REGISTERED", sender, params.unpack()[0], flush=True)
    invocation.return_value(None)

def get(conn, sender, path, iface, prop):
    return GLib.Variant("b", True)

def acquired(conn, name):
    conn.register_object("/StatusNotifierWatcher", info, call, get, None)
    print("READY", flush=True)

Gio.bus_own_name(Gio.BusType.SESSION, "org.kde.StatusNotifierWatcher",
                 Gio.BusNameOwnerFlags.NONE, None, acquired, None)
GLib.MainLoop().run()
"""


@skip_without_gui
def test_top_bar_icon_menu_and_process_name(isolated_home: Path) -> None:
    from gi.repository import Gio, GLib

    env = {**os.environ, "PYTHONPATH": str(SRC)}
    host = subprocess.Popen(
        [sys.executable, "-c", FAKE_TRAY_HOST], stdout=subprocess.PIPE, text=True
    )
    app = None
    try:
        assert host.stdout is not None
        assert host.stdout.readline().strip() == "READY"
        app = subprocess.Popen(
            [sys.executable, "-m", "movebreak", "--background"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        words = host.stdout.readline().split()
        assert words[0] == "REGISTERED", words
        sender, item_path = words[1], words[2]
        assert item_path == "/StatusNotifierItem"

        bus = Gio.bus_get_sync(Gio.BusType.SESSION)

        def call(path: str, interface: str, method: str, args: GLib.Variant | None) -> object:
            reply = bus.call_sync(
                sender, path, interface, method, args, None, Gio.DBusCallFlags.NONE, 5000, None
            )
            return reply.unpack() if reply is not None else None

        def prop(name: str) -> object:
            (value,) = call(  # type: ignore[misc]
                item_path,
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", name)),
            )
            return value

        assert prop("Status") == "Active"
        assert prop("IconName") == "movebreak-walk-symbolic"
        assert prop("IconPixmap"), "pixmap fallback is rendered"
        menu_path = prop("Menu")

        _revision, (_root, _props, children) = call(  # type: ignore[misc]
            menu_path, "com.canonical.dbusmenu", "GetLayout", GLib.Variant("(iias)", (0, -1, []))
        )
        labels = {child[1].get("label"): child[0] for child in children}
        assert any(label and label.startswith("Next:") for label in labels)
        call(
            menu_path,
            "com.canonical.dbusmenu",
            "Event",
            GLib.Variant(
                "(isvu)", (labels["Pause for 30 Minutes"], "clicked", GLib.Variant("s", ""), 0)
            ),
        )
        for _attempt in range(50):
            if prop("IconName") == "movebreak-paused-symbolic":
                break
            time.sleep(0.1)
        assert prop("IconName") == "movebreak-paused-symbolic"

        status = subprocess.run(
            [sys.executable, "-m", "movebreak", "status"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert "Paused until" in status.stdout

        comm = Path(f"/proc/{app.pid}/comm").read_text(encoding="utf-8").strip()
        assert comm == "Movebreak"
    finally:
        if app is not None:
            app.terminate()
            app.wait(timeout=10)
        host.terminate()
        host.wait(timeout=10)
