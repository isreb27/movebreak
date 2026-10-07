# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-facing wording: durations, schedules and reminder text."""

from __future__ import annotations

import math

from movebreak.core.models import ActivitySettings, Importance, PlannedActivity
from movebreak.core.scheduler import Prompt
from movebreak.i18n import _


def format_span(minutes: int) -> str:
    """``90`` → ``"1 h 30 min"``."""
    hours, rest = divmod(max(0, minutes), 60)
    if hours and rest:
        return _("{hours} h {minutes} min").format(hours=hours, minutes=rest)
    if hours:
        return _("{hours} h").format(hours=hours)
    return _("{minutes} min").format(minutes=rest)


def format_duration(seconds: int) -> str:
    """A break length: ``20`` → ``"20 s"``, ``90`` → ``"1 min 30 s"``, ``180`` → ``"3 min"``."""
    if seconds < 60:
        return _("{seconds} s").format(seconds=seconds)
    minutes, rest = divmod(seconds, 60)
    if rest:
        return _("{minutes} min {seconds} s").format(minutes=minutes, seconds=rest)
    return format_span(minutes)


def format_interval(seconds: int) -> str:
    return _("Every {span}").format(span=format_span(round(seconds / 60)))


def format_remaining(seconds: float) -> str:
    """Time until a reminder, rounded up to whole minutes."""
    if seconds <= 30:
        return _("now")
    return _("in {span}").format(span=format_span(math.ceil(seconds / 60)))


def describe_settings(settings: ActivitySettings) -> str:
    """One-line summary for lists: ``"Every 30 min · 3 min"``."""
    parts = [format_interval(settings.interval_s)]
    if settings.duration_s:
        parts.append(format_duration(settings.duration_s))
    if settings.importance is Importance.GENTLE:
        parts.append(_("gentle"))
    elif settings.importance is Importance.IMPORTANT:
        parts.append(_("important"))
    return " · ".join(parts)


class TipRotation:
    """Cycles through each activity's tips so reminders do not repeat word for word."""

    def __init__(self) -> None:
        self._next: dict[str, int] = {}

    def next_tip(self, activity: PlannedActivity) -> str | None:
        if not activity.tips:
            return None
        index = self._next.get(activity.id, 0) % len(activity.tips)
        self._next[activity.id] = index + 1
        return activity.tips[index]


def reminder_text(prompt: Prompt, tips: TipRotation) -> tuple[str, str]:
    """Title and body of the notification for a reminder."""
    names = " + ".join(activity.name for activity in prompt.activities)
    title = names
    if prompt.duration_s:
        title = _("{names} · {duration}").format(
            names=names, duration=format_duration(prompt.duration_s)
        )
    lines = [tip for tip in (tips.next_tip(a) for a in prompt.activities) if tip]
    return title, "\n".join(lines)
