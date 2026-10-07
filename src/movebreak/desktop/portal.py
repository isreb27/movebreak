# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""XDG desktop portal calls used by the app and by ``movebreak doctor``.

Portals are the sanctioned way for a sandboxed (Flatpak) app to ask the
desktop for things. Requests that need the user's consent answer
asynchronously through a ``Response`` signal on a request object; see
https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from typing import Any

from gi.repository import Gio, GLib

from movebreak.desktop.dbus import call_async, call_sync

log = logging.getLogger(__name__)

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND_INTERFACE = "org.freedesktop.portal.Background"
SETTINGS_INTERFACE = "org.freedesktop.portal.Settings"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"

MAX_STATUS_LENGTH = 96
"""The Background portal truncates status messages longer than this."""


def interface_version(bus: Gio.DBusConnection, interface: str) -> int | None:
    """The ``version`` property of a portal interface, or ``None`` if it is missing."""
    try:
        (value,) = call_sync(
            bus,
            PORTAL_NAME,
            PORTAL_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (interface, "version")),
            "(v)",
            timeout_ms=2000,
        )
    except GLib.Error:
        return None
    return int(value)


def request_background(
    bus: Gio.DBusConnection,
    *,
    reason: str,
    autostart: bool,
    commandline: list[str],
    on_done: Callable[[bool, bool, str | None], None],
) -> None:
    """Ask to run in the background and, optionally, to start at login.

    ``on_done(background_allowed, autostart_enabled, error_message)`` runs once
    GNOME has answered (it may show a dialog the first time).
    """
    unique = (bus.get_unique_name() or ":0").lstrip(":").replace(".", "_")
    token = f"movebreak_{secrets.token_hex(6)}"
    request_path = f"{PORTAL_PATH}/request/{unique}/{token}"
    subscription = 0

    def on_response(
        _bus: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
    ) -> None:
        bus.signal_unsubscribe(subscription)
        response, results = parameters.unpack()
        if response != 0:
            on_done(False, False, "The request was cancelled or denied.")
            return
        on_done(bool(results.get("background")), bool(results.get("autostart")), None)

    # Subscribe before calling, so a fast answer cannot be missed.
    subscription = bus.signal_subscribe(
        PORTAL_NAME,
        REQUEST_INTERFACE,
        "Response",
        request_path,
        None,
        Gio.DBusSignalFlags.NONE,
        on_response,
    )

    options = {
        "handle_token": GLib.Variant("s", token),
        "reason": GLib.Variant("s", reason),
        "autostart": GLib.Variant("b", autostart),
        "commandline": GLib.Variant("as", commandline),
        "dbus-activatable": GLib.Variant("b", False),
    }

    def on_called(_result: Any, error: GLib.Error | None) -> None:
        if error is not None:
            bus.signal_unsubscribe(subscription)
            on_done(False, False, error.message)

    call_async(
        bus,
        PORTAL_NAME,
        PORTAL_PATH,
        BACKGROUND_INTERFACE,
        "RequestBackground",
        GLib.Variant("(sa{sv})", ("", options)),
        "(o)",
        on_called,
    )


def set_background_status(bus: Gio.DBusConnection, message: str) -> None:
    """Show ``message`` under the app in Quick Settings › Background Apps (Flatpak only)."""
    text = message[:MAX_STATUS_LENGTH]

    def on_done(_result: Any, error: GLib.Error | None) -> None:
        if error is not None:
            log.debug("Could not set background status: %s", error.message)

    call_async(
        bus,
        PORTAL_NAME,
        PORTAL_PATH,
        BACKGROUND_INTERFACE,
        "SetStatus",
        GLib.Variant("(a{sv})", ({"message": GLib.Variant("s", text)},)),
        None,
        on_done,
    )


def read_setting(bus: Gio.DBusConnection, namespace: str, key: str) -> Any:
    """Read a desktop setting through the Settings portal. Raises ``GLib.Error``."""
    try:
        (value,) = call_sync(
            bus,
            PORTAL_NAME,
            PORTAL_PATH,
            SETTINGS_INTERFACE,
            "ReadOne",
            GLib.Variant("(ss)", (namespace, key)),
            "(v)",
            timeout_ms=2000,
        )
        return value
    except GLib.Error as error:
        if "UnknownMethod" not in str(error.message) and "No such method" not in str(error.message):
            raise
    # Version 1 of the portal only has Read, which wraps the value twice.
    (value,) = call_sync(
        bus,
        PORTAL_NAME,
        PORTAL_PATH,
        SETTINGS_INTERFACE,
        "Read",
        GLib.Variant("(ss)", (namespace, key)),
        "(v)",
        timeout_ms=2000,
    )
    return value.unpack() if isinstance(value, GLib.Variant) else value
