# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The break screen: a full-screen, dimmed overlay with the break in the middle.

Wayland does not let apps place windows, so going full screen is the only
reliable way to put something in the middle of the screen. The window is
translucent, so the desktop shows through, dimmed.

GNOME may refuse to bring a window from a background app to the front (focus
stealing prevention). The application checks :meth:`BreakScreen.is_active`
shortly after presenting and, if the screen did not come up, falls back to a
notification that opens it.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from movebreak.core.models import Outcome  # noqa: E402
from movebreak.i18n import _  # noqa: E402

_CSS = """
window.movebreak-break-screen {
  background-color: rgba(10, 12, 16, 0.82);
  color: #ffffff;
}
.movebreak-break-screen .break-title {
  font-size: 32pt;
  font-weight: 800;
}
.movebreak-break-screen .break-tip {
  font-size: 15pt;
  color: rgba(255, 255, 255, 0.86);
}
.movebreak-break-screen .break-clock {
  font-size: 34pt;
  font-weight: 700;
  font-feature-settings: "tnum";
}
.movebreak-break-screen .break-icon {
  color: #ffffff;
  opacity: 0.85;
}
.movebreak-break-screen .break-hint {
  color: rgba(255, 255, 255, 0.55);
}
.movebreak-break-screen button.pill {
  background-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
  min-width: 128px;
}
.movebreak-break-screen button.pill:hover {
  background-color: rgba(255, 255, 255, 0.22);
}
.movebreak-break-screen button.pill.suggested-action {
  background-color: #2ec27e;
  color: #ffffff;
}
.movebreak-break-screen button.pill.suggested-action:hover {
  background-color: #33d17a;
}
"""

_RING_SIZE = 200
_RING_WIDTH = 12.0

_css_installed = False


def _install_css() -> None:
    global _css_installed
    display = Gdk.Display.get_default()
    if _css_installed or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)  # GTK 4.12+, older than any supported libadwaita
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _css_installed = True


def _clock(seconds: float) -> str:
    whole = max(0, math.ceil(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


class CountdownRing(Gtk.Widget):
    """A ring that empties as the break runs out. Drawn with GSK paths (no cairo needed)."""

    _TRACK = Gdk.RGBA()
    _TRACK.parse("rgba(255, 255, 255, 0.16)")
    _FILL = Gdk.RGBA()
    _FILL.parse("#2ec27e")  # The green of the app icon.

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(_RING_SIZE, _RING_SIZE)
        self.fraction = 1.0

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - _RING_WIDTH
        stroke = Gsk.Stroke.new(_RING_WIDTH)
        stroke.set_line_cap(Gsk.LineCap.ROUND)

        track = Gsk.PathBuilder.new()
        track.add_circle(Graphene.Point().init(cx, cy), radius)
        snapshot.append_stroke(track.to_path(), stroke, self._TRACK)

        fraction = min(1.0, max(0.0, self.fraction))
        if fraction <= 0:
            return
        arc = Gsk.PathBuilder.new()
        if fraction >= 0.999:
            arc.add_circle(Graphene.Point().init(cx, cy), radius)
        else:
            # Clockwise from 12 o'clock.
            angle = -math.pi / 2 + 2 * math.pi * fraction
            arc.move_to(cx, cy - radius)
            arc.svg_arc_to(
                radius,
                radius,
                0,
                fraction > 0.5,
                True,
                cx + radius * math.cos(angle),
                cy + radius * math.sin(angle),
            )
        snapshot.append_stroke(arc.to_path(), stroke, self._FILL)


@dataclass(frozen=True, slots=True)
class BreakContent:
    title: str
    tips: tuple[str, ...]
    icon: str
    duration_s: int
    can_snooze: bool
    snooze_minutes: int


class BreakScreen(Gtk.Window):
    """Shows one break. ``on_answer`` receives Done, Snoozed or Skipped exactly once."""

    def __init__(
        self,
        application: Adw.Application,
        content: BreakContent,
        on_answer: Callable[[Outcome], None],
    ) -> None:
        super().__init__(application=application, title=content.title, decorated=False)
        _install_css()
        self.add_css_class("movebreak-break-screen")
        self._content = content
        self._on_answer = on_answer
        self._answered = False
        self._started = time.monotonic()
        self._tick_source = 0

        column = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            margin_start=24,
            margin_end=24,
        )
        column.append(Gtk.Image(icon_name=content.icon, pixel_size=64, css_classes=["break-icon"]))
        column.append(
            Gtk.Label(
                label=content.title,
                wrap=True,
                justify=Gtk.Justification.CENTER,
                css_classes=["break-title"],
            )
        )
        for tip in content.tips:
            column.append(
                Gtk.Label(
                    label=tip,
                    wrap=True,
                    max_width_chars=48,
                    justify=Gtk.Justification.CENTER,
                    css_classes=["break-tip"],
                )
            )

        self._ring = CountdownRing()
        self._clock = Gtk.Label(label=_clock(content.duration_s), css_classes=["break-clock"])
        self._clock.update_property([Gtk.AccessibleProperty.LABEL], [_("Time left in this break")])
        ring = Gtk.Overlay(
            child=self._ring, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12
        )
        ring.add_overlay(self._clock)
        ring.set_visible(content.duration_s > 0)
        column.append(ring)

        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6)
        self._done = self._button(_("Done"), Outcome.DONE, suggested=True)
        buttons.append(self._done)
        if content.can_snooze:
            label = _("Snooze {minutes} min").format(minutes=content.snooze_minutes)
            buttons.append(self._button(label, Outcome.SNOOZED))
        buttons.append(self._button(_("Skip"), Outcome.SKIPPED))
        column.append(buttons)

        hint = _("Esc snoozes this break") if content.can_snooze else _("Choose Done or Skip")
        column.append(Gtk.Label(label=hint, css_classes=["break-hint", "caption"]))

        self.set_child(column)
        self._column = column

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("close-request", self._on_close_request)
        self.connect("unrealize", lambda *_args: self._stop_ticking())

    # ------------------------------------------------------------------

    def show_break(self) -> None:
        """Go full screen, fade in and start the countdown."""
        self.fullscreen()
        self._column.set_opacity(0.0)
        self.present()
        self._done.grab_focus()
        target = Adw.PropertyAnimationTarget.new(self._column, "opacity")
        Adw.TimedAnimation.new(self._column, 0.0, 1.0, 450, target).play()
        if self._content.duration_s > 0:
            self._tick_source = GLib.timeout_add(250, self._on_tick)

    def finish(self) -> None:
        """Close without reporting an answer (the reminder ended elsewhere)."""
        self._answered = True
        self.close()

    @property
    def remaining_s(self) -> float:
        return max(0.0, self._content.duration_s - (time.monotonic() - self._started))

    # ------------------------------------------------------------------

    def _button(self, label: str, outcome: Outcome, *, suggested: bool = False) -> Gtk.Button:
        button = Gtk.Button(label=label, css_classes=["pill"])
        if suggested:
            button.add_css_class("suggested-action")
        button.connect("clicked", lambda _b: self._answer(outcome))
        return button

    def _answer(self, outcome: Outcome) -> None:
        if self._answered:
            return
        self._answered = True
        self._stop_ticking()
        self._on_answer(outcome)
        self.close()

    def _on_key_pressed(
        self, _controller: Gtk.EventControllerKey, keyval: int, _code: int, _state: object
    ) -> bool:
        if keyval == Gdk.KEY_Escape and self._content.can_snooze:
            self._answer(Outcome.SNOOZED)
            return True
        return False

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        # Closed with Alt+F4 or similar: treat it as "later", not as done.
        if not self._answered:
            self._answer(Outcome.SNOOZED if self._content.can_snooze else Outcome.SKIPPED)
        self._stop_ticking()
        return False

    def _on_tick(self) -> bool:
        remaining = self.remaining_s
        self._ring.fraction = remaining / self._content.duration_s
        self._ring.queue_draw()
        self._clock.set_label(_clock(remaining) if remaining > 0 else "✓")
        if remaining <= 0:
            self._tick_source = 0
            self._done.set_label(_("Done, Nice Work"))
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _stop_ticking(self) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0
