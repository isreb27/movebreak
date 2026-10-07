# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GTK application: owns the store, the scheduler and the desktop adapters.

The application is a single instance (``Gio.Application`` uniqueness via
D-Bus). Running ``movebreak <command>`` while it is running forwards the
command to the running instance, which prints the answer in your terminal.

Once started, it holds itself alive with no window, so closing the window
does not stop the reminders; "Quit" does.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from movebreak import __version__, config  # noqa: E402
from movebreak.core.clock import SystemClock  # noqa: E402
from movebreak.core.models import Outcome  # noqa: E402
from movebreak.core.scheduler import (  # noqa: E402
    ActivityCompleted,
    AwayReason,
    Event,
    PauseChanged,
    PromptClosed,
    PromptOpened,
    Scheduler,
    SchedulerConfig,
)
from movebreak.core.store import PauseState, Store, StoreError  # noqa: E402
from movebreak.core.text import TipRotation, format_remaining, reminder_text  # noqa: E402
from movebreak.desktop import portal  # noqa: E402
from movebreak.desktop.autostart import Autostart  # noqa: E402
from movebreak.desktop.dbus import session_bus  # noqa: E402
from movebreak.desktop.notifier import Notifier  # noqa: E402
from movebreak.desktop.presence import PresenceMonitor  # noqa: E402
from movebreak.desktop.tray import MenuEntry, TrayIcon  # noqa: E402
from movebreak.i18n import _  # noqa: E402

log = logging.getLogger(__name__)

ICONS_DIR = Path(__file__).resolve().parent / "icons"
TICK_SECONDS = 10
PAUSE_UNTIL_RESUMED = 0
PAUSE_UNTIL_TOMORROW = -1
DOCTOR_TIMEOUT_S = 60


def commands_help() -> str:
    return _(
        "Commands:\n"
        "  (none)            Open the window; starts the reminders if needed\n"
        "  pause [MINUTES]   Pause reminders for MINUTES, or until resumed\n"
        "  resume            Resume reminders\n"
        "  status            Show what is due next\n"
        "  profile [NAME]    List profiles, or switch to the profile NAME\n"
        "  doctor [quick]    Check how well this computer supports Movebreak;\n"
        "                    'quick' skips the notification test\n"
    )


def _out(command_line: Gio.ApplicationCommandLine, text: str) -> None:
    """Print to the terminal that ran the command (which may be another process)."""
    text = text if text.endswith("\n") else text + "\n"
    if hasattr(command_line, "print_literal"):  # GLib 2.80 and later
        command_line.print_literal(text)
    else:
        print(text, end="")


def _err(command_line: Gio.ApplicationCommandLine, text: str) -> None:
    text = text if text.endswith("\n") else text + "\n"
    if hasattr(command_line, "printerr_literal"):
        command_line.printerr_literal(text)
    else:
        print(text, end="", file=sys.stderr)


class MovebreakApplication(Adw.Application):
    def __init__(self, application_id: str = config.APP_ID) -> None:
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.set_option_context_parameter_string(_("[COMMAND]"))
        self.set_option_context_summary(
            _("Evidence-based reminders to move, rest your eyes and stretch.")
        )
        self.set_option_context_description(commands_help())
        self.add_main_option(
            "background",
            ord("b"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            _("Start without opening a window (used at login)"),
            None,
        )
        self.add_main_option(
            "version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, _("Print the version"), None
        )

        self._clock = SystemClock()
        self._tips = TipRotation()
        self._listeners: list[Callable[[], None]] = []
        self._service_running = False
        self._startup_error: str | None = None
        self._tick_source = 0
        self._last_status: str | None = None
        self._presence: PresenceMonitor | None = None
        self.tray: TrayIcon | None = None
        self._doctor_command_line: Gio.ApplicationCommandLine | None = None
        self._doctor_timeout = 0
        self._test_callback: Callable[[str], None] | None = None

        self.store: Store
        self.scheduler: Scheduler
        self.notifier: Notifier
        self.autostart: Autostart
        self.bus: Gio.DBusConnection | None = None

    # ------------------------------------------------------------------
    # GApplication virtual methods
    # ------------------------------------------------------------------

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"{config.APP_NAME} {__version__}")
            return 0
        return -1  # Continue with the default processing.

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))
        try:
            self.store = Store(config.database_path())
        except (StoreError, OSError) as error:
            self._startup_error = str(error)
            log.error("Cannot open the database: %s", error)
            return
        self.scheduler = Scheduler(self._clock, self.store.scheduler_config())
        self.bus = session_bus()
        self.notifier = Notifier(self)
        self.autostart = Autostart(self.store, self.bus)
        self._install_actions()

    def do_shutdown(self) -> None:
        if self._presence is not None:
            self._presence.stop()
        if self.tray is not None:
            self.tray.stop()
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0
        if self._startup_error is None:
            self.notifier.withdraw_reminder()
            self.store.close()
        Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        if self._startup_error is not None:
            return
        self._start_service()
        from movebreak.ui.window import MainWindow  # Imported lazily: the CLI never needs it.

        window = self.get_active_window() or MainWindow(application=self)
        window.present()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        if self._startup_error is not None:
            _err(
                command_line, _("Movebreak cannot start: {error}").format(error=self._startup_error)
            )
            return 1
        background = command_line.get_options_dict().contains("background")
        args = [a for a in command_line.get_arguments()[1:] if not a.startswith("-")]
        if not args:
            self._start_service()
            if not background:
                self.activate()
            return 0

        command, rest = args[0], args[1:]
        handlers = {
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "status": self._cmd_status,
            "profile": self._cmd_profile,
            "doctor": self._cmd_doctor,
        }
        handler = handlers.get(command)
        if handler is None:
            _err(command_line, _("Unknown command: {command}").format(command=command))
            _err(command_line, commands_help())
            return 2
        if command != "doctor" and not self._service_running:
            _err(command_line, _("Movebreak is not running. Start it with: movebreak --background"))
            return 1
        return handler(command_line, rest)

    # ------------------------------------------------------------------
    # Service
    # ------------------------------------------------------------------

    @property
    def service_running(self) -> bool:
        return self._service_running

    def _start_service(self) -> None:
        if self._service_running:
            return
        self._service_running = True
        self.hold()  # Keep running with no window open.
        self._handle(self.scheduler.set_plan(self.store.plan()))
        pause = self.store.pause_state()
        if pause.paused:
            self._handle(self.scheduler.pause(pause.until))
        if self.bus is not None:
            self._presence = PresenceMonitor(self.bus, self._on_away, self._on_back)
            self._presence.start()
            self.tray = TrayIcon(self.bus, icons_dir=ICONS_DIR, on_activate=self.activate)
            self.tray.start()
            self.refresh_tray()
        self._tick_source = GLib.timeout_add_seconds(TICK_SECONDS, self._on_tick)
        log.info("Reminders running with profile %s", self.store.active_profile().name)

    def _on_tick(self) -> bool:
        self._handle(self.scheduler.tick())
        return GLib.SOURCE_CONTINUE

    def _on_away(self, reason: AwayReason, idle_for: float) -> None:
        log.debug("Away: %s (idle for %.0f s)", reason, idle_for)
        self._handle(self.scheduler.user_away(reason, idle_for))

    def _on_back(self, reason: AwayReason) -> None:
        log.debug("Back: %s", reason)
        self._handle(self.scheduler.user_back(reason))

    def _handle(self, events: list[Event]) -> None:
        profile_id = self.store.active_profile().id
        now = self._clock.now()
        for event in events:
            if isinstance(event, PromptOpened):
                title, body = reminder_text(event.prompt, self._tips)
                snooze_minutes = round(self.scheduler.config.snooze_s / 60)
                self.notifier.show_reminder(event.prompt, title, body, snooze_minutes)
            elif isinstance(event, PromptClosed):
                self.notifier.withdraw_reminder()
                for activity in event.prompt.activities:
                    if activity.tracked:
                        self.store.log_outcome(
                            activity_id=activity.id,
                            profile_id=profile_id,
                            outcome=event.outcome,
                            at=now,
                        )
            elif isinstance(event, ActivityCompleted):
                self.store.log_outcome(
                    activity_id=event.activity_id,
                    profile_id=profile_id,
                    outcome=Outcome.DONE,
                    at=now,
                )
            elif isinstance(event, PauseChanged):
                self.store.set_pause_state(PauseState(event.paused, event.until))
        self._update_background_status()
        self.refresh_tray()
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------------
    # API used by the UI
    # ------------------------------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call ``callback`` after every tick and state change. Returns a remover."""
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def reload_plan(self) -> None:
        """Re-read the active profile after its settings or activities changed."""
        self._handle(self.scheduler.set_plan(self.store.plan()))

    def set_active_profile(self, profile_id: str) -> None:
        self.store.set_active_profile(profile_id)
        self.reload_plan()

    def apply_scheduler_config(self, scheduler_config: SchedulerConfig) -> None:
        self.store.set_scheduler_config(scheduler_config)
        self.scheduler.set_config(scheduler_config)
        self._handle([])

    def pause(self, minutes: int) -> datetime | None:
        """Pause for ``minutes``; :data:`PAUSE_UNTIL_RESUMED` or :data:`PAUSE_UNTIL_TOMORROW`."""
        now = self._clock.now()
        until: datetime | None
        if minutes == PAUSE_UNTIL_RESUMED:
            until = None
        elif minutes == PAUSE_UNTIL_TOMORROW:
            until = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif minutes > 0:
            until = now + timedelta(minutes=minutes)
        else:
            raise ValueError("minutes must be positive, 0 or -1")
        self._handle(self.scheduler.pause(until))
        return until

    def resume(self) -> None:
        self._handle(self.scheduler.resume())

    def complete(self, activity_id: str) -> None:
        self._handle(self.scheduler.complete(activity_id))

    def today_counts(self) -> dict[Outcome, int]:
        midnight = self._clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.store.outcome_counts(since=midnight)

    def status_line(self) -> str:
        """Short status, used in the window and in GNOME's Background Apps menu."""
        if self.scheduler.paused:
            until = self.scheduler.paused_until
            if until is None:
                return _("Paused until you resume")
            return _("Paused until {time}").format(time=_format_time(until, self._clock.now()))
        upcoming = self.scheduler.upcoming()
        if not upcoming:
            return _("No activities enabled")
        activity, remaining = upcoming[0]
        return _("Next: {name} {when}").format(name=activity.name, when=format_remaining(remaining))

    def send_test_notification(self, callback: Callable[[str], None] | None = None) -> None:
        self._test_callback = callback
        self.notifier.show_test()

    def refresh_tray(self) -> None:
        """Bring the top-bar icon and its menu up to date."""
        if self.tray is None:
            return
        paused = self.scheduler.paused
        entries = [MenuEntry(self.status_line()), MenuEntry(None)]
        if paused:
            entries.append(MenuEntry(_("Resume"), self.resume))
        else:
            for label, minutes in (
                (_("Pause for 30 Minutes"), 30),
                (_("Pause for 1 Hour"), 60),
                (_("Pause Until Tomorrow"), PAUSE_UNTIL_TOMORROW),
                (_("Pause Until I Resume"), PAUSE_UNTIL_RESUMED),
            ):
                entries.append(MenuEntry(label, partial(self._pause_from_tray, minutes)))
        entries += [
            MenuEntry(None),
            MenuEntry(_("Open Movebreak"), self.activate),
            MenuEntry(_("Quit Movebreak"), self.quit),
        ]
        self.tray.update(
            visible=self.store.tray_icon_enabled(),
            paused=paused,
            status_text=self.status_line(),
            entries=entries,
        )

    def _pause_from_tray(self, minutes: int) -> None:
        self.pause(minutes)

    def _update_background_status(self) -> None:
        if not config.is_flatpak() or self.bus is None:
            return
        status = self.status_line()
        if status != self._last_status:
            self._last_status = status
            portal.set_background_status(self.bus, status)

    # ------------------------------------------------------------------
    # Actions (menu items, notification buttons, `gapplication action`)
    # ------------------------------------------------------------------

    def _install_actions(self) -> None:
        def add(name: str, parameter: str | None, callback: Callable[..., object]) -> None:
            action = Gio.SimpleAction.new(
                name, GLib.VariantType.new(parameter) if parameter else None
            )
            action.connect("activate", callback)
            self.add_action(action)

        add("show-window", None, lambda *_args: self.activate())
        add("quit", None, lambda *_args: self.quit())
        add("preferences", None, self._on_preferences)
        add("about", None, self._on_about)
        add("pause", "i", lambda _a, value: self.pause(value.get_int32()))
        add("resume", None, lambda *_args: self.resume())
        add("complete", "s", lambda _a, value: self.complete(value.get_string()))
        add("set-profile", "s", lambda _a, value: self.set_active_profile(value.get_string()))
        add("prompt-response", "(us)", self._on_prompt_response)
        add("test-response", "s", self._on_test_response)

        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.preferences", ["<Primary>comma"])
        self.set_accels_for_action("window.close", ["<Primary>w"])

    def _on_prompt_response(self, _action: Gio.SimpleAction, value: GLib.Variant) -> None:
        prompt_id, answer = value.unpack()
        try:
            outcome = Outcome(answer)
            self._handle(self.scheduler.respond(prompt_id, outcome))
        except ValueError:
            log.warning("Ignoring unknown answer %r", answer)

    def _on_test_response(self, _action: Gio.SimpleAction, value: GLib.Variant) -> None:
        answer = value.get_string()
        self.notifier.withdraw_test()
        if self._doctor_command_line is not None:
            self._finish_doctor(answer)
        if self._test_callback is not None:
            self._test_callback(answer)
            self._test_callback = None

    def _on_preferences(self, *_args: object) -> None:
        self.activate()
        window = self.get_active_window()
        if window is not None:
            window.show_preferences()

    def _on_about(self, *_args: object) -> None:
        self.activate()
        window = self.get_active_window()
        if window is not None:
            window.show_about()

    # ------------------------------------------------------------------
    # Command-line commands
    # ------------------------------------------------------------------

    def _cmd_pause(self, command_line: Gio.ApplicationCommandLine, args: list[str]) -> int:
        minutes = PAUSE_UNTIL_RESUMED
        if args:
            try:
                minutes = int(args[0])
                if minutes <= 0:
                    raise ValueError
            except ValueError:
                _err(command_line, _("MINUTES must be a positive whole number."))
                return 2
        self.pause(minutes)
        _out(command_line, self.status_line())
        return 0

    def _cmd_resume(self, command_line: Gio.ApplicationCommandLine, _args: list[str]) -> int:
        self.resume()
        _out(command_line, self.status_line())
        return 0

    def _cmd_status(self, command_line: Gio.ApplicationCommandLine, _args: list[str]) -> int:
        self.scheduler.tick()  # Bring the clocks up to date before reporting.
        lines = [
            _("Profile: {name}").format(name=self.store.active_profile().name),
            self.status_line(),
        ]
        if not self.scheduler.present:
            lines.append(_("You are away from the computer."))
        upcoming = self.scheduler.upcoming()
        if upcoming and not self.scheduler.paused:
            width = max(len(activity.name) for activity, _r in upcoming)
            for activity, remaining in upcoming:
                lines.append(f"  {activity.name:<{width}}  {format_remaining(remaining)}")
        _out(command_line, "\n".join(lines))
        return 0

    def _cmd_profile(self, command_line: Gio.ApplicationCommandLine, args: list[str]) -> int:
        if not args:
            active = self.store.active_profile().id
            for item in self.store.profiles():
                marker = "*" if item.id == active else " "
                _out(command_line, f"{marker} {item.name}")
            return 0
        profile = self.store.find_profile(" ".join(args))
        if profile is None:
            _err(command_line, _("No profile named {name}.").format(name=" ".join(args)))
            return 1
        self.set_active_profile(profile.id)
        _out(command_line, _("Switched to {name}.").format(name=profile.name))
        return 0

    def _cmd_doctor(self, command_line: Gio.ApplicationCommandLine, args: list[str]) -> int:
        from movebreak import doctor

        for line in doctor.format_report(doctor.run_checks(self)):
            _out(command_line, line)
        if "quick" in args:
            return 0
        _out(
            command_line,
            _(
                "\nSending a test notification. Click one of its buttons within {seconds} s "
                "(or press Ctrl+C to stop)…"
            ).format(seconds=DOCTOR_TIMEOUT_S),
        )
        self._doctor_command_line = command_line  # Keeps the calling terminal waiting.
        self.hold()
        self._doctor_timeout = GLib.timeout_add_seconds(DOCTOR_TIMEOUT_S, self._on_doctor_timeout)
        self.notifier.show_test()
        return 0

    def _on_doctor_timeout(self) -> bool:
        self._doctor_timeout = 0
        self._finish_doctor(None)
        return GLib.SOURCE_REMOVE

    def _finish_doctor(self, answer: str | None) -> None:
        command_line = self._doctor_command_line
        if command_line is None:
            return
        self._doctor_command_line = None
        if self._doctor_timeout:
            GLib.source_remove(self._doctor_timeout)
            self._doctor_timeout = 0
        if answer is None:
            self.notifier.withdraw_test()
            _out(
                command_line,
                _(
                    "✗ No click arrived. If no banner appeared, check Settings › Notifications "
                    "› Movebreak and that the app's .desktop file is installed."
                ),
            )
            command_line.set_exit_status(1)
        elif answer == "banner":
            _out(command_line, _("✓ Notification clicked: the banner opens the app."))
        else:
            _out(
                command_line,
                _("✓ Button “{answer}” clicked: notification buttons work.").format(answer=answer),
            )
        if hasattr(command_line, "done"):  # GLib 2.80 and later
            command_line.done()
        self.release()


def _format_time(moment: datetime, now: datetime) -> str:
    local = moment.astimezone(now.tzinfo)
    if local.date() == now.date():
        return local.strftime("%H:%M")
    if local.date() == (now + timedelta(days=1)).date() and local.hour == 0 and local.minute == 0:
        return _("tomorrow")
    return local.strftime("%a %H:%M")
