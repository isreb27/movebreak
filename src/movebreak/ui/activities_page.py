# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Activities page: the active profile, then enabled and disabled activities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from movebreak.core.text import describe_settings
from movebreak.i18n import _
from movebreak.ui.activity_editor import ActivityEditor
from movebreak.ui.dialogs import ask_text, confirm

if TYPE_CHECKING:
    from movebreak.application import MovebreakApplication
    from movebreak.ui.window import MainWindow


class ActivitiesPage(Adw.PreferencesPage):
    def __init__(self, application: MovebreakApplication, window: MainWindow) -> None:
        super().__init__()
        self._app = application
        self._window = window
        self._updating = False
        self._rebuild_pending = False
        self._activity_groups: list[Adw.PreferencesGroup] = []

        self._actions = Gio.SimpleActionGroup()
        for name, callback in (
            ("new", self._on_new_profile),
            ("rename", self._on_rename_profile),
            ("reset", self._on_reset_profile),
            ("delete", self._on_delete_profile),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self._actions.add_action(action)
        self.insert_action_group("profile", self._actions)

        profile_group = Adw.PreferencesGroup(
            title=_("Profile"),
            description=_(
                "Each profile keeps its own schedule for every activity. Switch to a lighter "
                "profile for a busy week and back again later."
            ),
        )
        self._profile_row = Adw.ComboRow(title=_("Active profile"))
        self._profile_row.connect("notify::selected", self._on_profile_selected)
        menu = Gio.Menu()
        menu.append(_("New Profile…"), "profile.new")
        menu.append(_("Rename…"), "profile.rename")
        menu.append(_("Reset to Defaults"), "profile.reset")
        menu.append(_("Delete"), "profile.delete")
        self._profile_row.add_suffix(
            Gtk.MenuButton(
                icon_name="view-more-symbolic",
                menu_model=menu,
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=_("Profile actions"),
            )
        )
        profile_group.add(self._profile_row)
        self.add(profile_group)

        self.rebuild()

    # ------------------------------------------------------------------

    def rebuild(self) -> None:
        """Re-create the lists from the store (after any change to profiles or activities)."""
        self._rebuild_pending = False
        store = self._app.store
        active = store.active_profile()
        profiles = store.profiles()

        self._updating = True
        self._profiles = profiles
        self._profile_row.set_model(Gtk.StringList.new([p.name for p in profiles]))
        self._profile_row.set_selected(next(i for i, p in enumerate(profiles) if p.id == active.id))
        self._updating = False
        self._enable_action("rename", not active.builtin)
        self._enable_action("delete", not active.builtin)
        self._enable_action("reset", active.builtin)

        for group in self._activity_groups:
            self.remove(group)
        self._activity_groups.clear()

        add_button = Gtk.Button(
            icon_name="list-add-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
            tooltip_text=_("Add Activity"),
        )
        add_button.connect("clicked", lambda _b: self.open_editor(None))
        enabled_group = Adw.PreferencesGroup(title=_("Enabled"), header_suffix=add_button)
        disabled_group = Adw.PreferencesGroup(
            title=_("Disabled"), description=_("Available, but not reminding you in this profile.")
        )

        settings = store.settings(active.id)
        enabled_count = disabled_count = 0
        for activity in store.activities():
            activity_settings = settings[activity.id]
            row = Adw.ActionRow(
                title=activity.name,
                subtitle=describe_settings(activity_settings),
                use_markup=False,
                activatable=True,
            )
            row.add_prefix(Gtk.Image(icon_name=activity.icon))
            switch = Gtk.Switch(
                active=activity_settings.enabled,
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Enabled in this profile"),
            )
            switch.connect("notify::active", self._on_switch_toggled, activity.id)
            row.add_suffix(switch)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", self._on_row_activated, activity.id)
            if activity_settings.enabled:
                enabled_group.add(row)
                enabled_count += 1
            else:
                disabled_group.add(row)
                disabled_count += 1

        if enabled_count == 0:
            enabled_group.add(
                Adw.ActionRow(
                    title=_("Nothing enabled"),
                    subtitle=_("Turn on an activity below, or add your own."),
                )
            )
        self.add(enabled_group)
        self._activity_groups.append(enabled_group)
        if disabled_count:
            self.add(disabled_group)
            self._activity_groups.append(disabled_group)

    def open_editor(self, activity_id: str | None) -> None:
        activity = self._app.store.activity(activity_id) if activity_id else None
        ActivityEditor(self._app, activity, on_saved=self._after_change).present(self._window)

    # ------------------------------------------------------------------

    def _after_change(self, message: str | None = None) -> None:
        self._app.reload_plan()
        self._schedule_rebuild()
        if message:
            self._window.toast(message)

    def _schedule_rebuild(self) -> None:
        # Rebuilding destroys the widget whose signal is running; wait until it returns.
        if not self._rebuild_pending:
            self._rebuild_pending = True
            GLib.idle_add(self._rebuild_when_idle)

    def _rebuild_when_idle(self) -> bool:
        self.rebuild()
        return GLib.SOURCE_REMOVE

    def _enable_action(self, name: str, enabled: bool) -> None:
        action = self._actions.lookup_action(name)
        if action is not None:
            action.set_enabled(enabled)

    def _on_row_activated(self, _row: Adw.ActionRow, activity_id: str) -> None:
        self.open_editor(activity_id)

    def _on_switch_toggled(self, switch: Gtk.Switch, _param: object, activity_id: str) -> None:
        store = self._app.store
        profile_id = store.active_profile().id
        current = store.settings(profile_id)[activity_id]
        if current.enabled == switch.get_active():
            return
        store.update_settings(
            profile_id, activity_id, replace(current, enabled=switch.get_active())
        )
        self._after_change()

    def _on_profile_selected(self, row: Adw.ComboRow, _param: object) -> None:
        if self._updating:
            return
        index = row.get_selected()
        if 0 <= index < len(self._profiles):
            profile = self._profiles[index]
            self._app.set_active_profile(profile.id)
            self._schedule_rebuild()
            self._window.toast(_("Switched to profile {name}").format(name=profile.name))

    # -- profile actions -------------------------------------------------

    def _guard(self, action: Callable[[], None]) -> None:
        """Run a store change and show validation errors as a toast."""
        try:
            action()
        except (ValueError, KeyError) as error:
            self._window.toast(str(error).strip("'\""))

    def _on_new_profile(self, *_args: object) -> None:
        current = self._app.store.active_profile()

        def create(name: str) -> None:
            def run() -> None:
                profile = self._app.store.create_profile(name, copy_from=current.id)
                self._app.set_active_profile(profile.id)
                self._after_change(_("Created profile {name}").format(name=profile.name))

            self._guard(run)

        ask_text(
            self._window,
            heading=_("New Profile"),
            body=_("It starts as a copy of “{name}”.").format(name=current.name),
            initial="",
            confirm_label=_("Create"),
            on_confirm=create,
        )

    def _on_rename_profile(self, *_args: object) -> None:
        current = self._app.store.active_profile()

        def rename(name: str) -> None:
            def run() -> None:
                self._app.store.rename_profile(current.id, name)
                self._after_change()

            self._guard(run)

        ask_text(
            self._window,
            heading=_("Rename Profile"),
            body="",
            initial=current.name,
            confirm_label=_("Rename"),
            on_confirm=rename,
        )

    def _on_reset_profile(self, *_args: object) -> None:
        current = self._app.store.active_profile()

        def reset() -> None:
            self._app.store.reset_profile(current.id)
            self._after_change(_("Profile reset to its defaults"))

        confirm(
            self._window,
            heading=_("Reset “{name}”?").format(name=current.name),
            body=_("Built-in activities go back to their default schedule in this profile."),
            confirm_label=_("Reset"),
            on_confirm=reset,
        )

    def _on_delete_profile(self, *_args: object) -> None:
        current = self._app.store.active_profile()

        def delete() -> None:
            self._app.store.delete_profile(current.id)
            self._after_change(_("Deleted profile {name}").format(name=current.name))

        confirm(
            self._window,
            heading=_("Delete “{name}”?").format(name=current.name),
            body=_("Its schedule is lost. Activities are kept for the other profiles."),
            confirm_label=_("Delete"),
            on_confirm=delete,
        )
