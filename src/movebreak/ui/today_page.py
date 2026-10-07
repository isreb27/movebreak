# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Today page: what is due next and what you did today."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from movebreak.core.models import Outcome
from movebreak.core.text import format_interval, format_remaining
from movebreak.i18n import _

if TYPE_CHECKING:
    from movebreak.application import MovebreakApplication
    from movebreak.ui.window import MainWindow


class TodayPage(Adw.PreferencesPage):
    def __init__(self, application: MovebreakApplication, window: MainWindow) -> None:
        super().__init__()
        self._app = application
        self._window = window

        header = Adw.PreferencesGroup()
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=6
        )
        self._headline = Gtk.Label(
            css_classes=["title-1"], wrap=True, justify=Gtk.Justification.CENTER
        )
        self._subline = Gtk.Label(
            css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER
        )
        box.append(self._headline)
        box.append(self._subline)
        header.add(box)
        self.add(header)

        self._upcoming = Adw.PreferencesGroup(
            title=_("Up Next"),
            description=_("Timers count only the time you actually spend at the computer."),
        )
        self.add(self._upcoming)
        self._rows: dict[str, Adw.ActionRow] = {}
        self._empty_row = Adw.ActionRow(
            title=_("No activities enabled"),
            subtitle=_("Turn some on in the Activities page."),
        )

        self._today = Adw.PreferencesGroup(title=_("Today"))
        self._counts: dict[str, Gtk.Label] = {}
        for key, title in (
            ("done", _("Done")),
            ("snoozed", _("Snoozed")),
            ("skipped", _("Skipped")),
            ("missed", _("Missed")),
        ):
            row = Adw.ActionRow(title=title)
            label = Gtk.Label(css_classes=["numeric"])
            row.add_suffix(label)
            self._today.add(row)
            self._counts[key] = label
        self.add(self._today)

    def refresh(self) -> None:
        scheduler = self._app.scheduler
        upcoming = scheduler.upcoming()
        profile = self._app.store.active_profile()

        if scheduler.paused:
            self._headline.set_label(_("Paused"))
            self._subline.set_label(_("Timers are frozen and continue where they left off."))
        elif not upcoming:
            self._headline.set_label(_("No reminders"))
            self._subline.set_label(_("Profile: {name}").format(name=profile.name))
        else:
            activity, remaining = upcoming[0]
            self._headline.set_label(activity.name)
            when = format_remaining(remaining)
            if not scheduler.present:
                detail = _("{when} · timers wait while you are away").format(when=when)
            else:
                detail = _("{when} · profile {name}").format(when=when, name=profile.name)
            self._subline.set_label(detail)

        self._sync_rows([activity.id for activity, _r in upcoming])
        for activity, remaining in upcoming:
            row = self._rows[activity.id]
            row.set_title(activity.name)
            row.set_subtitle(
                f"{format_remaining(remaining)} · {format_interval(activity.interval_s).lower()}"
            )

        counts = self._app.today_counts()
        self._counts["done"].set_label(str(counts[Outcome.DONE] + counts[Outcome.AWAY]))
        self._counts["snoozed"].set_label(str(counts[Outcome.SNOOZED]))
        self._counts["skipped"].set_label(str(counts[Outcome.SKIPPED]))
        self._counts["missed"].set_label(str(counts[Outcome.MISSED]))

    def _sync_rows(self, activity_ids: list[str]) -> None:
        """Rebuild the rows only when the set or order of activities changed."""
        if list(self._rows) == activity_ids:
            return
        for row in self._rows.values():
            self._upcoming.remove(row)
        if self._empty_row.get_parent() is not None:
            self._upcoming.remove(self._empty_row)
        self._rows.clear()
        if not activity_ids:
            self._upcoming.add(self._empty_row)
            return
        icons = {a.id: a.icon for a in self._app.store.activities()}
        for activity_id in activity_ids:
            row = Adw.ActionRow(use_markup=False)
            row.add_prefix(Gtk.Image(icon_name=icons.get(activity_id, "movebreak-timer-symbolic")))
            button = Gtk.Button(
                label=_("Done"),
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=_("Mark as done now"),
            )
            button.connect("clicked", self._on_done_clicked, activity_id)
            row.add_suffix(button)
            self._upcoming.add(row)
            self._rows[activity_id] = row

    def _on_done_clicked(self, _button: Gtk.Button, activity_id: str) -> None:
        name = self._app.store.activity(activity_id).name
        self._app.complete(activity_id)
        self._window.toast(_("{name}: marked as done").format(name=name))
