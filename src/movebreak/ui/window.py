# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The main window: a Today page and an Activities page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from movebreak import __version__, config
from movebreak.application import PAUSE_UNTIL_RESUMED, PAUSE_UNTIL_TOMORROW
from movebreak.i18n import _
from movebreak.ui.activities_page import ActivitiesPage
from movebreak.ui.preferences import PreferencesDialog
from movebreak.ui.today_page import TodayPage

if TYPE_CHECKING:
    from movebreak.application import MovebreakApplication


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: MovebreakApplication) -> None:
        super().__init__(
            application=application,
            title=config.APP_NAME,
            default_width=720,
            default_height=700,
        )
        self.set_size_request(360, 420)
        self._app = application

        self._stack = Adw.ViewStack()
        self.today_page = TodayPage(application, self)
        self.activities_page = ActivitiesPage(application, self)
        self._stack.add_titled_with_icon(
            self.today_page, "today", _("Today"), "emoji-recent-symbolic"
        )
        self._stack.add_titled_with_icon(
            self.activities_page, "activities", _("Activities"), "view-list-symbolic"
        )

        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        self._pause_button = Adw.SplitButton(
            label=_("Pause"),
            menu_model=self._pause_menu(),
            tooltip_text=_("Pause reminders"),
            dropdown_tooltip=_("Pause for a while"),
        )
        self._pause_button.connect("clicked", self._on_pause_clicked)
        header.pack_start(self._pause_button)

        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=self._primary_menu(),
            tooltip_text=_("Main Menu"),
            primary=True,
        )
        header.pack_end(menu_button)

        self._banner = Adw.Banner(button_label=_("Resume"))
        self._banner.connect("button-clicked", lambda *_args: self._app.resume())

        switcher_bar = Adw.ViewSwitcherBar(stack=self._stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self._banner)
        toolbar.set_content(self._stack)
        toolbar.add_bottom_bar(switcher_bar)

        self._toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self._toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 500sp"))
        narrow.add_setter(switcher_bar, "reveal", True)
        narrow.add_setter(switcher, "visible", False)
        self.add_breakpoint(narrow)

        self._remove_listener = application.add_listener(self.refresh)
        self.connect("close-request", self._on_close_request)
        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Update everything that changes over time (called on every tick)."""
        paused = self._app.scheduler.paused
        self._pause_button.set_label(_("Resume") if paused else _("Pause"))
        self._pause_button.set_tooltip_text(
            _("Resume reminders") if paused else _("Pause reminders")
        )
        self._banner.set_title(self._app.status_line() if paused else "")
        self._banner.set_revealed(paused)
        self.today_page.refresh()

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=3))

    def show_page(self, name: str) -> None:
        self._stack.set_visible_child_name(name)

    def show_preferences(self) -> None:
        PreferencesDialog(self._app).present(self)

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=config.APP_NAME,
            application_icon=config.APP_ID,
            developer_name=config.DEVELOPER_NAME,
            version=__version__,
            website=config.PROJECT_URL,
            issue_url=config.ISSUES_URL,
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 The Movebreak Authors",
            comments=_(
                "Evidence-based reminders to move, rest your eyes and stretch while you "
                "work at a computer. Not medical advice: if you have persistent pain, see "
                "a physiotherapist."
            ),
        )
        about.add_link(
            _("Evidence Behind the Defaults"), f"{config.PROJECT_URL}/blob/main/docs/EVIDENCE.md"
        )
        about.present(self)

    # ------------------------------------------------------------------

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        self._remove_listener()
        return False  # Let the window close; the app keeps running in the background.

    def _on_pause_clicked(self, _button: Adw.SplitButton) -> None:
        if self._app.scheduler.paused:
            self._app.resume()
            self.toast(_("Reminders resumed"))
        else:
            self._app.pause(PAUSE_UNTIL_RESUMED)

    @staticmethod
    def _pause_menu() -> Gio.Menu:
        menu = Gio.Menu()
        choices = (
            (_("For 30 Minutes"), 30),
            (_("For 1 Hour"), 60),
            (_("For 2 Hours"), 120),
            (_("Until Tomorrow"), PAUSE_UNTIL_TOMORROW),
            (_("Until I Resume"), PAUSE_UNTIL_RESUMED),
        )
        for label, minutes in choices:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("app.pause", GLib.Variant("i", minutes))
            menu.append_item(item)
        return menu

    @staticmethod
    def _primary_menu() -> Gio.Menu:
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("_Preferences"), "app.preferences")
        section.append(_("_About Movebreak"), "app.about")
        menu.append_section(None, section)
        quit_section = Gio.Menu()
        quit_section.append(_("_Quit (Stop Reminders)"), "app.quit")
        menu.append_section(None, quit_section)
        return menu
