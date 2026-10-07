# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small reusable dialogs."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from movebreak.i18n import _


def ask_text(
    parent: Gtk.Widget,
    *,
    heading: str,
    body: str,
    initial: str,
    confirm_label: str,
    on_confirm: Callable[[str], None],
) -> None:
    """Ask for a short piece of text, such as a profile name."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial, activates_default=True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("ok", confirm_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")
    dialog.set_response_enabled("ok", bool(initial.strip()))
    entry.connect(
        "changed", lambda e: dialog.set_response_enabled("ok", bool(e.get_text().strip()))
    )

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "ok":
            on_confirm(entry.get_text().strip())

    dialog.connect("response", on_response)
    dialog.present(parent)
    entry.grab_focus()


def confirm(
    parent: Gtk.Widget,
    *,
    heading: str,
    body: str,
    confirm_label: str,
    on_confirm: Callable[[], None],
    destructive: bool = True,
) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("confirm", confirm_label)
    dialog.set_response_appearance(
        "confirm",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)
