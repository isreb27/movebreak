# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Translation helpers.

Every user-visible string goes through :func:`_` so the project can be
translated later without touching the code. Use ``str.format`` placeholders
with names, never string concatenation, so translators can reorder words::

    _("Snooze {minutes} min").format(minutes=10)
"""

from __future__ import annotations

import contextlib
import gettext
import locale

DOMAIN = "movebreak"


def setup(localedir: str | None = None) -> None:
    """Bind the gettext domain. Call once at start-up."""
    with contextlib.suppress(locale.Error):  # Unsupported locale: untranslated strings.
        locale.setlocale(locale.LC_ALL, "")
    gettext.bindtextdomain(DOMAIN, localedir)
    gettext.textdomain(DOMAIN)


def _(message: str) -> str:
    return gettext.dgettext(DOMAIN, message)


def ngettext(singular: str, plural: str, count: int) -> str:
    return gettext.dngettext(DOMAIN, singular, plural, count)
