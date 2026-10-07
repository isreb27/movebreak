# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small helpers around GDBus calls."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 5000


def session_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as error:
        log.warning("No D-Bus session bus: %s", error.message)
        return None


def call_sync(
    bus: Gio.DBusConnection,
    name: str,
    path: str,
    interface: str,
    method: str,
    parameters: GLib.Variant | None = None,
    reply_type: str | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> Any:
    """Call a method and return the unpacked reply. Raises ``GLib.Error``."""
    reply = bus.call_sync(
        name,
        path,
        interface,
        method,
        parameters,
        GLib.VariantType.new(reply_type) if reply_type else None,
        Gio.DBusCallFlags.NONE,
        timeout_ms,
        None,
    )
    return reply.unpack() if reply is not None else None


def call_async(
    bus: Gio.DBusConnection,
    name: str,
    path: str,
    interface: str,
    method: str,
    parameters: GLib.Variant | None,
    reply_type: str | None,
    on_done: Callable[[Any | None, GLib.Error | None], None],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> None:
    """Call a method without blocking; ``on_done(result, error)`` receives the unpacked reply."""

    def finish(connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            reply = connection.call_finish(result)
        except GLib.Error as error:
            on_done(None, error)
            return
        on_done(reply.unpack() if reply is not None else None, None)

    bus.call(
        name,
        path,
        interface,
        method,
        parameters,
        GLib.VariantType.new(reply_type) if reply_type else None,
        Gio.DBusCallFlags.NONE,
        timeout_ms,
        None,
        finish,
    )
