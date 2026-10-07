# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preferences: start at login, reminder timing and a notification test."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from movebreak.core.scheduler import SchedulerConfig
from movebreak.i18n import _

if TYPE_CHECKING:
    from movebreak.application import MovebreakApplication


def _minutes_row(title: str, subtitle: str, low: int, high: int, value: float) -> Adw.SpinRow:
    row = Adw.SpinRow.new_with_range(low, high, 1)
    row.set_title(title)
    row.set_subtitle(subtitle)
    row.set_value(round(value / 60))
    return row


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, application: MovebreakApplication) -> None:
        super().__init__()
        self._app = application
        self._applying_autostart = False

        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")

        startup = Adw.PreferencesGroup(title=_("Running in the Background"))
        self._autostart = Adw.SwitchRow(
            title=_("Start at Login"),
            subtitle=_("Reminders run in the background from the moment you log in"),
            active=application.autostart.is_enabled(),
        )
        self._autostart.connect("notify::active", self._on_autostart_toggled)
        startup.add(self._autostart)

        tray = application.tray
        self._tray_icon = Adw.SwitchRow(
            title=_("Show Icon in Top Bar"),
            subtitle=(
                _("Shows the next break and a pause menu")
                if tray is not None and tray.host_available
                else _(
                    "Needs the “AppIndicator and KStatusNotifierItem Support” GNOME "
                    "extension (included in Ubuntu)"
                )
            ),
            active=application.store.tray_icon_enabled(),
        )
        self._tray_icon.connect("notify::active", self._on_tray_icon_toggled)
        startup.add(self._tray_icon)
        page.add(startup)

        config = application.scheduler.config
        timing = Adw.PreferencesGroup(
            title=_("Reminders"),
            description=_("These apply to every profile."),
        )
        self._snooze = _minutes_row(_("Snooze length"), _("Minutes"), 1, 60, config.snooze_s)
        self._gap = _minutes_row(
            _("Minimum time between reminders"), _("Minutes"), 0, 60, config.min_gap_s
        )
        self._merge = _minutes_row(
            _("Combine reminders due within"), _("Minutes"), 0, 15, config.merge_window_s
        )
        self._timeout = _minutes_row(
            _("Unanswered reminders expire after"), _("Minutes"), 1, 30, config.prompt_timeout_s
        )
        for row in (self._snooze, self._gap, self._merge, self._timeout):
            row.connect("notify::value", self._on_timing_changed)
            timing.add(row)
        page.add(timing)

        notifications = Adw.PreferencesGroup(
            title=_("Notifications"),
            description=_(
                "Sound, banners and Do Not Disturb are controlled by GNOME: "
                "Settings › Notifications › Movebreak."
            ),
        )
        test = Adw.ActionRow(
            title=_("Send a Test Notification"),
            subtitle=_("Check that banners and buttons work"),
            activatable=True,
        )
        test.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        test.connect("activated", self._on_test)
        notifications.add(test)
        page.add(notifications)

        self.add(page)

    def _on_tray_icon_toggled(self, row: Adw.SwitchRow, _param: object) -> None:
        self._app.store.set_tray_icon_enabled(row.get_active())
        self._app.refresh_tray()

    def _on_autostart_toggled(self, row: Adw.SwitchRow, _param: object) -> None:
        if self._applying_autostart:
            return
        wanted = row.get_active()

        def done(enabled: bool, error: str | None) -> None:
            self._applying_autostart = True
            row.set_active(enabled)
            self._applying_autostart = False
            if error:
                self.add_toast(Adw.Toast(title=GLib.markup_escape_text(error)))
            elif enabled:
                self.add_toast(Adw.Toast(title=_("Movebreak will start when you log in")))

        self._app.autostart.set_enabled(wanted, done)

    def _on_timing_changed(self, _row: Adw.SpinRow, _param: object) -> None:
        self._app.apply_scheduler_config(
            SchedulerConfig(
                snooze_s=self._snooze.get_value() * 60,
                min_gap_s=self._gap.get_value() * 60,
                merge_window_s=self._merge.get_value() * 60,
                prompt_timeout_s=self._timeout.get_value() * 60,
            )
        )

    def _on_test(self, _row: Adw.ActionRow) -> None:
        def answered(answer: str) -> None:
            message = (
                _("The banner works")
                if answer == "banner"
                else _("Button “{answer}” works").format(answer=answer)
            )
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(message)))

        self._app.send_test_notification(answered)
        self.add_toast(Adw.Toast(title=_("Test notification sent; click one of its buttons")))
