# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tells the scheduler when the user goes idle, locks the screen and comes back.

* Idle: ``org.gnome.Mutter.IdleMonitor``. An *idle watch* fires once the user
  has not touched the keyboard or mouse for :data:`IDLE_THRESHOLD_S`; a
  one-shot *user-active watch* then fires on the next input. Both are
  event-driven, so nothing is polled. Wayland-only GNOME (50 and later) has
  no X11 idle API, so this is the supported way.
* Lock: the ``ActiveChanged`` signal of ``org.gnome.ScreenSaver``.

Suspend is not handled here: the scheduler detects it from the clocks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib

from movebreak.core.scheduler import AwayReason
from movebreak.desktop.dbus import call_async

log = logging.getLogger(__name__)

IDLE_THRESHOLD_S = 60
"""Seconds without input after which the user counts as away."""

MUTTER_NAME = "org.gnome.Mutter.IdleMonitor"
MUTTER_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
MUTTER_INTERFACE = "org.gnome.Mutter.IdleMonitor"

SCREENSAVER_NAME = "org.gnome.ScreenSaver"
SCREENSAVER_PATH = "/org/gnome/ScreenSaver"
SCREENSAVER_INTERFACE = "org.gnome.ScreenSaver"


class PresenceMonitor:
    """Reports presence changes through two callbacks."""

    def __init__(
        self,
        bus: Gio.DBusConnection,
        on_away: Callable[[AwayReason, float], None],
        on_back: Callable[[AwayReason], None],
    ) -> None:
        self._bus = bus
        self._on_away = on_away
        self._on_back = on_back
        self._idle_watch: int | None = None
        self._active_watch: int | None = None
        self._idle_away = False
        self._subscriptions: list[int] = []
        self._name_watch: int | None = None
        self.idle_supported = False

    def start(self) -> None:
        self._subscriptions.append(
            self._bus.signal_subscribe(
                MUTTER_NAME,
                MUTTER_INTERFACE,
                "WatchFired",
                MUTTER_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_watch_fired,
            )
        )
        self._subscriptions.append(
            self._bus.signal_subscribe(
                SCREENSAVER_NAME,
                SCREENSAVER_INTERFACE,
                "ActiveChanged",
                SCREENSAVER_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_lock_changed,
            )
        )
        self._name_watch = Gio.bus_watch_name_on_connection(
            self._bus,
            MUTTER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_mutter_appeared,
            self._on_mutter_vanished,
        )
        call_async(
            self._bus,
            SCREENSAVER_NAME,
            SCREENSAVER_PATH,
            SCREENSAVER_INTERFACE,
            "GetActive",
            None,
            "(b)",
            self._on_initial_lock_state,
        )

    def stop(self) -> None:
        for subscription in self._subscriptions:
            self._bus.signal_unsubscribe(subscription)
        self._subscriptions.clear()
        if self._name_watch is not None:
            Gio.bus_unwatch_name(self._name_watch)
            self._name_watch = None

    # -- idle ---------------------------------------------------------------

    def _on_mutter_appeared(self, _bus: Gio.DBusConnection, _name: str, _owner: str) -> None:
        call_async(
            self._bus,
            MUTTER_NAME,
            MUTTER_PATH,
            MUTTER_INTERFACE,
            "AddIdleWatch",
            GLib.Variant("(t)", (IDLE_THRESHOLD_S * 1000,)),
            "(u)",
            self._on_idle_watch_added,
        )

    def _on_idle_watch_added(self, result: tuple[int] | None, error: GLib.Error | None) -> None:
        if error is not None or result is None:
            log.warning("Idle detection unavailable: %s", error.message if error else "no reply")
            self.idle_supported = False
            return
        self._idle_watch = result[0]
        self.idle_supported = True
        log.debug("Idle watch %d registered", self._idle_watch)

    def _on_mutter_vanished(self, _bus: Gio.DBusConnection, _name: str) -> None:
        self._idle_watch = None
        self._active_watch = None
        self.idle_supported = False
        if self._idle_away:
            # Don't stay "away" forever if the monitor disappears mid-idle.
            self._idle_away = False
            self._on_back(AwayReason.IDLE)

    def _on_watch_fired(
        self,
        _bus: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
    ) -> None:
        (watch_id,) = parameters.unpack()
        if watch_id == self._idle_watch and not self._idle_away:
            self._idle_away = True
            self._on_away(AwayReason.IDLE, float(IDLE_THRESHOLD_S))
            call_async(
                self._bus,
                MUTTER_NAME,
                MUTTER_PATH,
                MUTTER_INTERFACE,
                "AddUserActiveWatch",
                None,
                "(u)",
                self._on_active_watch_added,
            )
        elif watch_id == self._active_watch:
            self._active_watch = None  # User-active watches fire only once.
            if self._idle_away:
                self._idle_away = False
                self._on_back(AwayReason.IDLE)

    def _on_active_watch_added(self, result: tuple[int] | None, error: GLib.Error | None) -> None:
        if error is not None or result is None:
            log.warning("Could not watch for user activity: %s", error.message if error else "")
            if self._idle_away:
                self._idle_away = False
                self._on_back(AwayReason.IDLE)
            return
        self._active_watch = result[0]

    # -- screen lock ----------------------------------------------------------

    def _on_initial_lock_state(self, result: tuple[bool] | None, error: GLib.Error | None) -> None:
        if error is not None:
            log.info("Screen lock state unavailable: %s", error.message)
            return
        if result and result[0]:
            self._on_away(AwayReason.LOCKED, 0.0)

    def _on_lock_changed(
        self,
        _bus: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
    ) -> None:
        (active,) = parameters.unpack()
        if active:
            self._on_away(AwayReason.LOCKED, 0.0)
        else:
            self._on_back(AwayReason.LOCKED)
