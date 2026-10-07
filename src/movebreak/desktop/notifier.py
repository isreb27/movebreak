# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shows reminders as GNOME notifications.

``Gio.Notification`` goes through GNOME Shell natively and through the
notification portal inside Flatpak. Buttons trigger application actions, so
they work even when no window is open.

GNOME only displays notifications from apps that have an installed
``.desktop`` file named after the application ID; ``scripts/install.py``
installs it.

Every reminder reuses one notification ID, so a new reminder replaces the
previous one instead of stacking up in the message tray.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

from movebreak.core.models import Importance, Outcome
from movebreak.core.scheduler import Prompt
from movebreak.i18n import _

REMINDER_ID = "reminder"
TEST_ID = "test"

_PRIORITY = {
    # Low-priority notifications get no banner in GNOME, which would make
    # gentle reminders invisible; they stay normal and are simply buttonless.
    Importance.GENTLE: Gio.NotificationPriority.NORMAL,
    Importance.NORMAL: Gio.NotificationPriority.NORMAL,
    Importance.IMPORTANT: Gio.NotificationPriority.HIGH,
}


class Notifier:
    def __init__(self, application: Gio.Application) -> None:
        self._app = application

    def show_reminder(self, prompt: Prompt, title: str, body: str, snooze_minutes: int) -> None:
        notification = Gio.Notification.new(title)
        if body:
            notification.set_body(body)
        priority = max(
            (_PRIORITY[a.importance] for a in prompt.activities),
            key=lambda p: int(p),
        )
        notification.set_priority(priority)
        notification.set_default_action("app.show-window")
        if not prompt.gentle:
            self._add_answer(notification, _("Done"), prompt.id, Outcome.DONE)
            if prompt.can_snooze:
                label = _("Snooze {minutes} min").format(minutes=snooze_minutes)
                self._add_answer(notification, label, prompt.id, Outcome.SNOOZED)
            self._add_answer(notification, _("Skip"), prompt.id, Outcome.SKIPPED)
        self._app.send_notification(REMINDER_ID, notification)

    def withdraw_reminder(self) -> None:
        self._app.withdraw_notification(REMINDER_ID)

    def show_test(self) -> None:
        """A notification with three buttons, used by Preferences and ``movebreak doctor``."""
        notification = Gio.Notification.new(_("Movebreak test notification"))
        notification.set_body(_("Click one of the buttons to check that they work."))
        notification.set_priority(Gio.NotificationPriority.NORMAL)
        notification.set_default_action_and_target("app.test-response", GLib.Variant("s", "banner"))
        for label, target in ((_("Done"), "done"), (_("Snooze"), "snooze"), (_("Skip"), "skip")):
            notification.add_button_with_target(
                label, "app.test-response", GLib.Variant("s", target)
            )
        self._app.send_notification(TEST_ID, notification)

    def withdraw_test(self) -> None:
        self._app.withdraw_notification(TEST_ID)

    @staticmethod
    def _add_answer(
        notification: Gio.Notification, label: str, prompt_id: int, outcome: Outcome
    ) -> None:
        notification.add_button_with_target(
            label, "app.prompt-response", GLib.Variant("(us)", (prompt_id, outcome.value))
        )
