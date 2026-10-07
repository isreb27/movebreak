# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Start at login.

* Native installs write ``~/.config/autostart/<APP_ID>.desktop`` (the XDG
  Autostart spec; GNOME's "Startup Applications" does the same thing).
* Flatpak cannot write there, so it asks the Background portal, which shows
  a consent dialog and writes the file for us.

Either way the app starts with ``--background``: no window, just reminders.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio

from movebreak import config
from movebreak.core.store import Store
from movebreak.desktop import portal
from movebreak.i18n import _

log = logging.getLogger(__name__)

BACKGROUND_ARG = "--background"


def autostart_file() -> Path:
    return config.config_dir() / "autostart" / f"{config.APP_ID}.desktop"


def launch_command() -> list[str]:
    """The command that starts this installation of Movebreak."""
    launcher = os.environ.get("MOVEBREAK_LAUNCHER")  # Exported by the installed launcher script.
    if launcher and Path(launcher).is_file():
        return [str(Path(launcher).resolve())]
    found = shutil.which("movebreak")
    if found:
        return [found]
    # Running from a source checkout: python3 -m movebreak with the right path.
    package_parent = str(Path(__file__).resolve().parents[2])
    return ["env", f"PYTHONPATH={package_parent}", sys.executable, "-m", "movebreak"]


def _exec_line(arguments: list[str]) -> str:
    """Quote arguments for a desktop file ``Exec`` key."""
    quoted = []
    for argument in arguments:
        if any(c in argument for c in " \t\n\"'\\><~|&;$*?#()`"):
            escaped = argument.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
            escaped = escaped.replace("$", "\\$")
            quoted.append(f'"{escaped}"')
        else:
            quoted.append(argument)
    return " ".join(quoted).replace("%", "%%")


class Autostart:
    def __init__(self, store: Store, bus: Gio.DBusConnection | None) -> None:
        self._store = store
        self._bus = bus

    @property
    def sandboxed(self) -> bool:
        return config.is_flatpak()

    def is_enabled(self) -> bool:
        if self.sandboxed:
            return self._store.autostart_requested()
        path = autostart_file()
        if not path.is_file():
            return False
        content = path.read_text(encoding="utf-8", errors="replace")
        return "Hidden=true" not in content and "X-GNOME-Autostart-enabled=false" not in content

    def set_enabled(self, enabled: bool, on_done: Callable[[bool, str | None], None]) -> None:
        """Turn autostart on or off. ``on_done(enabled_now, error_message)``."""
        if self.sandboxed:
            self._set_with_portal(enabled, on_done)
            return
        try:
            self._write_file(enabled)
        except OSError as error:
            on_done(self.is_enabled(), str(error))
            return
        on_done(enabled, None)

    def _write_file(self, enabled: bool) -> None:
        path = autostart_file()
        if not enabled:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        command = _exec_line([*launch_command(), BACKGROUND_ARG])
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={config.APP_NAME}\n"
            f"Comment={_('Start break reminders at login')}\n"
            f"Exec={command}\n"
            f"Icon={config.APP_ID}\n"
            "Terminal=false\n"
            "NoDisplay=true\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        log.info("Wrote %s", path)

    def _set_with_portal(self, enabled: bool, on_done: Callable[[bool, str | None], None]) -> None:
        if self._bus is None:
            on_done(False, _("No D-Bus session bus."))
            return

        def finished(_background: bool, autostart: bool, error: str | None) -> None:
            if error is None:
                self._store.set_autostart_requested(autostart)
            on_done(autostart if error is None else self.is_enabled(), error)

        portal.request_background(
            self._bus,
            reason=_("Movebreak reminds you to take breaks while its window is closed."),
            autostart=enabled,
            commandline=["movebreak", BACKGROUND_ARG],
            on_done=finished,
        )


def describe_command() -> str:
    """Human-readable launch command, for diagnostics."""
    return shlex.join([*launch_command(), BACKGROUND_ARG])
