# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain data types shared by the whole application.

An activity is split in two on purpose:

* :class:`Activity` describes *what* the activity is (name, icon, tips). It is
  shared by every profile.
* :class:`ActivitySettings` describes *how* it runs inside one profile
  (enabled, how often, how long, how insistent).

A :class:`Profile` is therefore just a named set of :class:`ActivitySettings`,
and switching profiles never loses a custom activity. :class:`PlannedActivity`
joins both halves for the scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MIN_INTERVAL_S = 60
MAX_INTERVAL_S = 24 * 60 * 60
MAX_DURATION_S = 60 * 60
MAX_NAME_LENGTH = 80


class Importance(StrEnum):
    """How insistent a reminder is."""

    GENTLE = "gentle"
    """A short banner without buttons that disappears by itself and is not tracked."""

    NORMAL = "normal"
    """A banner with Done, Snooze and Skip buttons."""

    IMPORTANT = "important"
    """Like normal, sent with high priority so it is harder to miss."""


class ReminderStyle(StrEnum):
    """How normal and important reminders appear. Gentle ones are always a short banner."""

    BANNER = "banner"
    """GNOME's standard banner: hidden after a few seconds, kept in the notification list."""

    PERSISTENT = "persistent"
    """A banner that stays on screen until it is answered."""

    BREAK_SCREEN = "break-screen"
    """A full-screen break screen with the tip, a countdown and the answer buttons."""


class Outcome(StrEnum):
    """How a reminder ended."""

    DONE = "done"
    SNOOZED = "snoozed"
    SKIPPED = "skipped"
    MISSED = "missed"
    """Nobody answered before the reminder expired."""
    DISMISSED = "dismissed"
    """A gentle reminder that disappeared by itself."""
    AWAY = "away"
    """The user was away from the computer long enough for it to count as done."""
    CANCELLED = "cancelled"
    """Withdrawn by the app (pause, profile change); never recorded in history."""

    @property
    def counts_as_done(self) -> bool:
        return self in (Outcome.DONE, Outcome.AWAY)


@dataclass(frozen=True, slots=True)
class Activity:
    """What an activity is. Shared by every profile."""

    id: str
    name: str
    icon: str
    tips: tuple[str, ...] = ()
    covers: frozenset[str] = frozenset()
    """Other activities that completing this one also completes (a walk rests the eyes)."""
    builtin: bool = False
    position: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("activity id must not be empty")
        if not self.name.strip():
            raise ValueError("activity name must not be empty")
        if len(self.name) > MAX_NAME_LENGTH:
            raise ValueError(f"activity name must be at most {MAX_NAME_LENGTH} characters")
        if self.id in self.covers:
            raise ValueError("an activity cannot cover itself")


@dataclass(frozen=True, slots=True)
class ActivitySettings:
    """How an activity runs inside one profile."""

    enabled: bool
    interval_s: int
    """Seconds of *active* computer use between reminders."""
    duration_s: int
    """Suggested length of the break, shown in the reminder."""
    importance: Importance = Importance.NORMAL
    done_if_away_s: int | None = None
    """Time away from the computer that counts as having done it; ``None`` means never."""

    def __post_init__(self) -> None:
        if not MIN_INTERVAL_S <= self.interval_s <= MAX_INTERVAL_S:
            raise ValueError(
                f"interval must be between {MIN_INTERVAL_S} and {MAX_INTERVAL_S} seconds"
            )
        if not 0 <= self.duration_s <= MAX_DURATION_S:
            raise ValueError(f"duration must be between 0 and {MAX_DURATION_S} seconds")
        if self.done_if_away_s is not None and self.done_if_away_s <= 0:
            raise ValueError("done_if_away_s must be positive or None")


@dataclass(frozen=True, slots=True)
class Profile:
    """A named set of activity settings, such as "Recommended" or "Light"."""

    id: str
    name: str
    builtin: bool = False
    position: int = 0


@dataclass(frozen=True, slots=True)
class PlannedActivity:
    """An enabled activity as the scheduler sees it: definition plus settings."""

    id: str
    name: str
    icon: str
    tips: tuple[str, ...]
    covers: frozenset[str]
    interval_s: int
    duration_s: int
    importance: Importance
    done_if_away_s: int | None
    position: int

    @classmethod
    def from_parts(cls, activity: Activity, settings: ActivitySettings) -> PlannedActivity:
        return cls(
            id=activity.id,
            name=activity.name,
            icon=activity.icon,
            tips=activity.tips,
            covers=activity.covers,
            interval_s=settings.interval_s,
            duration_s=settings.duration_s,
            importance=settings.importance,
            done_if_away_s=settings.done_if_away_s,
            position=activity.position,
        )

    @property
    def tracked(self) -> bool:
        """Whether outcomes of this activity are recorded in history."""
        return self.importance is not Importance.GENTLE
