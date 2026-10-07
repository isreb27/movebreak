# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Program entry point: checks dependencies, then runs the application."""

from __future__ import annotations

import logging
import os
import sys

from movebreak import config, i18n

_MISSING_DEPENDENCIES = """\
Movebreak needs PyGObject with GTK 4 and libadwaita {minimum}+ ({error}).

  Fedora:         sudo dnf install python3-gobject gtk4 libadwaita
  Ubuntu/Debian:  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1

No administrator rights? Use the Flatpak build instead (see README.md).
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(
        level=os.environ.get("MOVEBREAK_LOG_LEVEL", "WARNING").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    i18n.setup()
    minimum = ".".join(map(str, config.MIN_LIBADWAITA))
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
    except (ImportError, ValueError) as error:
        sys.stderr.write(_MISSING_DEPENDENCIES.format(minimum=minimum, error=error))
        return 1
    if (Adw.get_major_version(), Adw.get_minor_version()) < config.MIN_LIBADWAITA:
        found = f"{Adw.get_major_version()}.{Adw.get_minor_version()}"
        sys.stderr.write(_MISSING_DEPENDENCIES.format(minimum=minimum, error=f"found {found}"))
        return 1

    from movebreak.application import MovebreakApplication

    return int(MovebreakApplication().run(argv))
