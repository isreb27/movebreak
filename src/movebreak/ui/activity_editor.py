# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialog to create or edit an activity.

The dialog edits two things at once, and says so in the UI:

* the activity itself (name, icon, tips, what it also counts as), shared by
  all profiles;
* its schedule in the *active* profile.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from movebreak.core import presets
from movebreak.core.models import Activity, ActivitySettings, Importance
from movebreak.core.text import format_duration
from movebreak.i18n import _
from movebreak.ui.dialogs import confirm

if TYPE_CHECKING:
    from movebreak.application import MovebreakApplication

_IMPORTANCE_CHOICES = (Importance.GENTLE, Importance.NORMAL, Importance.IMPORTANT)


def _icon_labels() -> dict[str, str]:
    return {
        "movebreak-walk-symbolic": _("Walking"),
        "view-reveal-symbolic": _("Eye"),
        "movebreak-stretch-symbolic": _("Stretching"),
        "movebreak-strength-symbolic": _("Strength"),
        "movebreak-water-symbolic": _("Water"),
        "movebreak-breathing-symbolic": _("Breathing"),
        "movebreak-stand-symbolic": _("Standing"),
        "movebreak-timer-symbolic": _("Timer"),
        "movebreak-heart-symbolic": _("Heart"),
    }


def _icon_factory() -> Gtk.SignalListItemFactory:
    labels = _icon_labels()
    factory = Gtk.SignalListItemFactory()

    def setup(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0))
        item.set_child(box)

    def bind(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        icon_name = item.get_item().get_string()
        image = item.get_child().get_first_child()
        image.set_from_icon_name(icon_name)
        image.get_next_sibling().set_label(labels.get(icon_name, icon_name))

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory


def _importance_hint(importance: Importance) -> str:
    return {
        Importance.GENTLE: _("Silent banner that disappears after 30 seconds; not tracked"),
        Importance.NORMAL: _("Banner with Done, Snooze and Skip buttons"),
        Importance.IMPORTANT: _("Like normal, sent with high priority"),
    }[importance]


class ActivityEditor(Adw.Dialog):
    def __init__(
        self,
        application: MovebreakApplication,
        activity: Activity | None,
        *,
        on_saved: Callable[[str | None], None],
    ) -> None:
        super().__init__(
            title=_("Edit Activity") if activity else _("New Activity"),
            content_width=520,
            content_height=720,
        )
        self._app = application
        self._activity = activity
        self._on_saved = on_saved
        store = application.store
        self._profile = store.active_profile()
        settings = (
            store.settings(self._profile.id)[activity.id]
            if activity
            else presets.new_activity_settings()
        )

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("_Cancel"), use_underline=True)
        cancel.connect("clicked", lambda _b: self.close())
        self._save = Gtk.Button(
            label=_("_Save") if activity else _("_Add"),
            use_underline=True,
            css_classes=["suggested-action"],
        )
        self._save.connect("clicked", self._on_save)
        header.pack_start(cancel)
        header.pack_end(self._save)

        page = Adw.PreferencesPage()

        # -- what the activity is (shared by all profiles) -------------------
        details = Adw.PreferencesGroup(
            title=_("Activity"), description=_("Shared by every profile.")
        )
        self._name = Adw.EntryRow(title=_("Name"), text=activity.name if activity else "")
        self._name.connect("changed", self._on_name_changed)
        details.add(self._name)

        icons = list(presets.ICON_CHOICES)
        current_icon = activity.icon if activity else presets.DEFAULT_CUSTOM_ICON
        if current_icon not in icons:
            icons.append(current_icon)
        self._icons = icons
        self._icon = Adw.ComboRow(
            title=_("Icon"), model=Gtk.StringList.new(icons), factory=_icon_factory()
        )
        self._icon.set_selected(icons.index(current_icon))
        details.add(self._icon)
        page.add(details)

        tips_group = Adw.PreferencesGroup(
            title=_("Tips"),
            description=_("One per line. Each reminder shows the next tip in turn."),
        )
        self._tips = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            accepts_tab=False,
            top_margin=12,
            bottom_margin=12,
            left_margin=12,
            right_margin=12,
        )
        self._tips.get_buffer().set_text("\n".join(activity.tips) if activity else "")
        self._tips.update_property([Gtk.AccessibleProperty.LABEL], [_("Tips")])
        scroller = Gtk.ScrolledWindow(
            child=self._tips, min_content_height=110, css_classes=["card"]
        )
        tips_group.add(scroller)
        page.add(tips_group)

        # -- how it runs in the active profile --------------------------------
        schedule = Adw.PreferencesGroup(
            title=_("Schedule in “{name}”").format(name=self._profile.name),
            description=_("Other profiles keep their own schedule for this activity."),
        )
        self._enabled = Adw.SwitchRow(title=_("Enabled"), active=settings.enabled)
        schedule.add(self._enabled)

        self._interval = Adw.SpinRow.new_with_range(1, 720, 5)
        self._interval.set_title(_("Every (minutes)"))
        self._interval.set_subtitle(_("Minutes of computer use; idle time does not count"))
        self._interval.set_value(round(settings.interval_s / 60))
        schedule.add(self._interval)

        self._duration = Adw.SpinRow.new_with_range(0, 3600, 10)
        self._duration.set_title(_("Break length (seconds)"))
        self._duration.set_value(settings.duration_s)
        self._duration.connect("notify::value", self._on_duration_changed)
        schedule.add(self._duration)
        self._on_duration_changed(self._duration, None)

        self._importance = Adw.ComboRow(
            title=_("Style"),
            model=Gtk.StringList.new([_("Gentle"), _("Normal"), _("Important")]),
        )
        self._importance.set_selected(_IMPORTANCE_CHOICES.index(settings.importance))
        self._importance.connect("notify::selected", self._on_importance_changed)
        schedule.add(self._importance)
        self._on_importance_changed(self._importance, None)

        self._away = Adw.SpinRow.new_with_range(0, 240, 1)
        self._away.set_title(_("Counts as done after being away (minutes)"))
        self._away.set_subtitle(_("0 means never"))
        self._away.set_value(round((settings.done_if_away_s or 0) / 60))
        schedule.add(self._away)
        page.add(schedule)

        # -- what else it counts as -------------------------------------------
        others = [a for a in store.activities() if activity is None or a.id != activity.id]
        self._cover_rows: dict[str, Adw.SwitchRow] = {}
        if others:
            covers_group = Adw.PreferencesGroup()
            expander = Adw.ExpanderRow(
                title=_("Also Counts As"),
                subtitle=_("Finishing this activity also resets these"),
            )
            current_covers = activity.covers if activity else frozenset()
            for other in others:
                row = Adw.SwitchRow(
                    title=other.name, use_markup=False, active=other.id in current_covers
                )
                expander.add_row(row)
                self._cover_rows[other.id] = row
            expander.set_expanded(bool(current_covers))
            covers_group.add(expander)
            page.add(covers_group)

        # -- reset or delete ------------------------------------------------------
        if activity is not None:
            danger = Adw.PreferencesGroup()
            if activity.builtin:
                button = Gtk.Button(label=_("Reset to Defaults"), halign=Gtk.Align.CENTER)
                button.add_css_class("pill")
                button.connect("clicked", self._on_reset)
            else:
                button = Gtk.Button(label=_("Delete Activity"), halign=Gtk.Align.CENTER)
                button.add_css_class("pill")
                button.add_css_class("destructive-action")
                button.connect("clicked", self._on_delete)
            danger.add(button)
            page.add(danger)

        self._toasts = Adw.ToastOverlay(child=page)
        toolbar = Adw.ToolbarView(content=self._toasts)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)
        self.set_default_widget(self._save)
        self._on_name_changed(self._name)

    # ------------------------------------------------------------------

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        valid = bool(entry.get_text().strip())
        self._save.set_sensitive(valid)
        if valid:
            entry.remove_css_class("error")
        else:
            entry.add_css_class("error")

    def _on_duration_changed(self, row: Adw.SpinRow, _param: object) -> None:
        seconds = int(row.get_value())
        row.set_subtitle(format_duration(seconds) if seconds else _("No length shown"))

    def _on_importance_changed(self, row: Adw.ComboRow, _param: object) -> None:
        row.set_subtitle(_importance_hint(_IMPORTANCE_CHOICES[row.get_selected()]))

    def _collect_settings(self) -> ActivitySettings:
        away_minutes = int(self._away.get_value())
        return ActivitySettings(
            enabled=self._enabled.get_active(),
            interval_s=int(self._interval.get_value()) * 60,
            duration_s=int(self._duration.get_value()),
            importance=_IMPORTANCE_CHOICES[self._importance.get_selected()],
            done_if_away_s=away_minutes * 60 if away_minutes else None,
        )

    def _on_save(self, _button: Gtk.Button) -> None:
        store = self._app.store
        buffer = self._tips.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        tips = tuple(line.strip() for line in text.splitlines() if line.strip())
        icon = self._icons[self._icon.get_selected()]
        covers = frozenset(i for i, row in self._cover_rows.items() if row.get_active())
        try:
            settings = self._collect_settings()
            if self._activity is None:
                created = store.add_activity(
                    name=self._name.get_text(),
                    icon=icon,
                    tips=tips,
                    covers=covers,
                    settings=settings,
                    profile_id=self._profile.id,
                )
                message = _("Added {name}").format(name=created.name)
            else:
                saved = store.update_activity(
                    replace(
                        self._activity,
                        name=self._name.get_text().strip() or self._activity.name,
                        icon=icon,
                        tips=tips,
                        covers=covers,
                    )
                )
                store.update_settings(self._profile.id, saved.id, settings)
                message = None
        except (ValueError, KeyError) as error:
            self._toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(error))))
            return
        self._on_saved(message)
        self.close()

    def _on_reset(self, _button: Gtk.Button) -> None:
        activity = self._activity
        assert activity is not None

        def reset() -> None:
            self._app.store.reset_activity(activity.id, self._profile.id)
            self._on_saved(_("{name} reset to its defaults").format(name=activity.name))
            self.close()

        confirm(
            self,
            heading=_("Reset “{name}”?").format(name=activity.name),
            body=_("Its name, tips and schedule in this profile go back to the defaults."),
            confirm_label=_("Reset"),
            on_confirm=reset,
        )

    def _on_delete(self, _button: Gtk.Button) -> None:
        activity = self._activity
        assert activity is not None

        def delete() -> None:
            self._app.store.delete_activity(activity.id)
            self._on_saved(_("Deleted {name}").format(name=activity.name))
            self.close()

        confirm(
            self,
            heading=_("Delete “{name}”?").format(name=activity.name),
            body=_("It is removed from every profile. Its history is kept."),
            confirm_label=_("Delete"),
            on_confirm=delete,
        )
