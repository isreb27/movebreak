# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether GNOME's Do Not Disturb is on.

Persistent banners and the break screen break through Do Not Disturb (GNOME
shows urgent notifications even then), so Movebreak checks it first and falls
back to a quiet standard banner while it is on.
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from movebreak import config
from movebreak.desktop import portal

log = logging.getLogger(__name__)

SCHEMA = "org.gnome.desktop.notifications"
KEY = "show-banners"


def do_not_disturb_active(bus: Gio.DBusConnection | None) -> bool:
    """``True`` when banners are switched off. Unknown counts as off."""
    if config.is_flatpak():
        if bus is None:
            return False
        try:
            return not bool(portal.read_setting(bus, SCHEMA, KEY))
        except GLib.Error as error:
            log.debug("Do Not Disturb state unavailable: %s", error.message)
            return False
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA, True) is None:
        return False
    return not Gio.Settings.new(SCHEMA).get_boolean(KEY)
