# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``movebreak doctor``: checks how well this computer supports Movebreak.

It answers the open technical questions from the design proposal on the
machine where it runs (natively or inside the Flatpak sandbox):

* Is ``org.gnome.Mutter.IdleMonitor`` reachable?
* Is GNOME's Do Not Disturb setting readable?
* Do notification buttons work? (interactive test, done by the application)
* Is the Background portal's status line available?
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from movebreak import __version__, config
from movebreak.desktop import portal
from movebreak.desktop.autostart import autostart_file, describe_command
from movebreak.desktop.dbus import call_sync, session_bus
from movebreak.desktop.presence import (
    MUTTER_INTERFACE,
    MUTTER_NAME,
    MUTTER_PATH,
    SCREENSAVER_INTERFACE,
    SCREENSAVER_NAME,
    SCREENSAVER_PATH,
)
from movebreak.i18n import _

if TYPE_CHECKING:
    from movebreak.application import MovebreakApplication

NOTIFICATIONS_SCHEMA = "org.gnome.desktop.notifications"


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


_SYMBOL = {Status.OK: "✓", Status.WARN: "!", Status.FAIL: "✗", Status.INFO: "·"}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str


def run_checks(app: MovebreakApplication) -> list[Check]:
    bus = session_bus()
    checks = [
        _check_versions(),
        _check_session(),
        _check_desktop_file(),
    ]
    if bus is None:
        checks.append(
            Check(_("D-Bus session"), Status.FAIL, _("No session bus: nothing will work."))
        )
        return checks
    checks += [
        _check_idle_monitor(bus),
        _check_screen_lock(bus),
        _check_do_not_disturb(bus),
        _check_background_portal(bus),
        _check_notification_portal(bus),
        _check_autostart(app),
        Check(_("Data"), Status.INFO, str(config.database_path())),
    ]
    return checks


def format_report(checks: list[Check]) -> list[str]:
    width = max(len(check.name) for check in checks)
    lines = [_("Movebreak doctor"), ""]
    for check in checks:
        lines.append(f"{_SYMBOL[check.status]} {check.name:<{width}}  {check.detail}")
    problems = [c for c in checks if c.status in (Status.FAIL, Status.WARN)]
    lines.append("")
    if problems:
        lines.append(
            _("{count} item(s) need attention; see docs/TROUBLESHOOTING.md.").format(
                count=len(problems)
            )
        )
    else:
        lines.append(_("Everything Movebreak needs is available."))
    return lines


def _check_versions() -> Check:
    adw = (Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version())
    gtk = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
    detail = _("Movebreak {app}, Python {py}, GTK {gtk}, libadwaita {adw}, GLib {glib}").format(
        app=__version__,
        py=sys.version.split()[0],
        gtk=".".join(map(str, gtk)),
        adw=".".join(map(str, adw)),
        glib=f"{GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}",
    )
    status = Status.OK if adw[:2] >= config.MIN_LIBADWAITA else Status.FAIL
    return Check(_("Versions"), status, detail)


def _check_session() -> Check:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    session = os.environ.get("XDG_SESSION_TYPE", "?")
    packaging = _("Flatpak sandbox") if config.is_flatpak() else _("native install")
    status = Status.OK if "GNOME" in desktop.upper() else Status.WARN
    return Check(
        _("Session"),
        status,
        _("{desktop} on {session}, {packaging}").format(
            desktop=desktop, session=session, packaging=packaging
        ),
    )


def _check_desktop_file() -> Check:
    try:
        # PyGObject raises instead of returning None when the file is missing.
        found = Gio.DesktopAppInfo.new(f"{config.APP_ID}.desktop") is not None
    except TypeError:
        found = False
    if found:
        return Check(_("Desktop file"), Status.OK, _("installed: notifications can be shown"))
    return Check(
        _("Desktop file"),
        Status.FAIL,
        _(
            "{app_id}.desktop not found. GNOME ignores notifications from apps without one; "
            "run scripts/install.py."
        ).format(app_id=config.APP_ID),
    )


def _check_idle_monitor(bus: Gio.DBusConnection) -> Check:
    try:
        (idle_ms,) = call_sync(
            bus, MUTTER_NAME, MUTTER_PATH, MUTTER_INTERFACE, "GetIdletime", None, "(t)", 2000
        )
    except GLib.Error as error:
        return Check(
            _("Idle detection"),
            Status.WARN,
            _(
                "Mutter IdleMonitor not reachable ({error}). Time away will only be noticed "
                "when the screen locks."
            ).format(error=error.message),
        )
    return Check(
        _("Idle detection"),
        Status.OK,
        _("Mutter IdleMonitor reachable (idle for {seconds:.0f} s)").format(seconds=idle_ms / 1000),
    )


def _check_screen_lock(bus: Gio.DBusConnection) -> Check:
    try:
        (active,) = call_sync(
            bus,
            SCREENSAVER_NAME,
            SCREENSAVER_PATH,
            SCREENSAVER_INTERFACE,
            "GetActive",
            None,
            "(b)",
            2000,
        )
    except GLib.Error as error:
        return Check(
            _("Screen lock"),
            Status.WARN,
            _("org.gnome.ScreenSaver not reachable ({error})").format(error=error.message),
        )
    state = _("locked") if active else _("unlocked")
    return Check(_("Screen lock"), Status.OK, _("readable (currently {state})").format(state=state))


def _check_do_not_disturb(bus: Gio.DBusConnection) -> Check:
    name = _("Do Not Disturb")
    if not config.is_flatpak():
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup(NOTIFICATIONS_SCHEMA, True) is not None:
            banners = Gio.Settings.new(NOTIFICATIONS_SCHEMA).get_boolean("show-banners")
            return Check(name, Status.OK, _dnd_state(banners))
        return Check(name, Status.WARN, _("GNOME notification settings schema not found"))
    try:
        banners = portal.read_setting(bus, NOTIFICATIONS_SCHEMA, "show-banners")
    except GLib.Error as error:
        return Check(
            name,
            Status.WARN,
            _("not readable through the Settings portal ({error})").format(error=error.message),
        )
    return Check(name, Status.OK, _dnd_state(bool(banners)) + _(" (via Settings portal)"))


def _dnd_state(show_banners: bool) -> str:
    return _("readable (currently off)") if show_banners else _("readable (currently on)")


def _check_background_portal(bus: Gio.DBusConnection) -> Check:
    version = portal.interface_version(bus, portal.BACKGROUND_INTERFACE)
    name = _("Background portal")
    if version is None:
        return Check(name, Status.WARN, _("not available"))
    if not config.is_flatpak():
        return Check(
            name,
            Status.INFO,
            _(
                "version {version}; status line not used outside Flatpak (GNOME lists only "
                "sandboxed apps in Background Apps)"
            ).format(version=version),
        )
    if version < 2:
        return Check(
            name, Status.WARN, _("version {version}: no status line").format(version=version)
        )
    portal.set_background_status(bus, _("Movebreak doctor: status line works"))
    return Check(
        name,
        Status.OK,
        _(
            "version {version}; open Quick Settings › Background Apps and look for "
            "“Movebreak doctor: status line works”"
        ).format(version=version),
    )


def _check_notification_portal(bus: Gio.DBusConnection) -> Check:
    version = portal.interface_version(bus, "org.freedesktop.portal.Notification")
    if version is None:
        status = Status.WARN if config.is_flatpak() else Status.INFO
        return Check(_("Notification portal"), status, _("not available"))
    return Check(_("Notification portal"), Status.INFO, _("version {v}").format(v=version))


def _check_autostart(app: MovebreakApplication) -> Check:
    if app.autostart.is_enabled():
        where = _("via the Background portal") if config.is_flatpak() else str(autostart_file())
        return Check(_("Start at login"), Status.OK, _("on ({where})").format(where=where))
    return Check(
        _("Start at login"),
        Status.INFO,
        _("off; turn it on in Preferences. Command: {command}").format(command=describe_command()),
    )
