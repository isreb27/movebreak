# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decides when to remind, counting *active* time instead of wall-clock time.

The scheduler knows nothing about GTK or D-Bus. The application:

1. calls :meth:`Scheduler.tick` every few seconds,
2. reports presence changes with :meth:`Scheduler.user_away` and
   :meth:`Scheduler.user_back` (idle, screen lock),
3. forwards the user's answers with :meth:`Scheduler.respond`,

and turns the returned :data:`Event` objects into notifications and history
entries. Suspends are detected here, by comparing boot time with monotonic
time (see :mod:`movebreak.core.clock`).

Rules, in the order they are applied on every tick:

* Time only counts while the user is present and reminders are not paused.
* Time away that reaches an activity's ``done_if_away_s`` counts as having
  done it; nothing piles up while you are away.
* Only one reminder is open at a time; reminders are at least
  ``min_gap_s`` apart.
* Activities due within ``merge_window_s`` of each other are merged into one
  reminder. An activity covered by another one in the same reminder is not
  shown separately (a walk already rests the eyes).
* Unanswered reminders expire after ``prompt_timeout_s`` of active time;
  gentle ones disappear after ``gentle_visible_s``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from movebreak.core.clock import Clock
from movebreak.core.models import Importance, Outcome, PlannedActivity

SUSPEND_THRESHOLD_S = 5.0
"""A boot-time jump larger than this between two ticks counts as a suspend."""

_IMPORTANCE_ORDER = {Importance.IMPORTANT: 0, Importance.NORMAL: 1, Importance.GENTLE: 2}
_ANSWERS = frozenset({Outcome.DONE, Outcome.SNOOZED, Outcome.SKIPPED})


class AwayReason(StrEnum):
    IDLE = "idle"
    LOCKED = "locked"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    snooze_s: float = 10 * 60
    min_gap_s: float = 5 * 60
    merge_window_s: float = 3 * 60
    prompt_timeout_s: float = 5 * 60
    gentle_visible_s: float = 30
    max_snoozes: int = 2

    def __post_init__(self) -> None:
        for name in ("snooze_s", "min_gap_s", "merge_window_s", "prompt_timeout_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.prompt_timeout_s <= 0 or self.gentle_visible_s <= 0 or self.snooze_s <= 0:
            raise ValueError("timeouts and snooze length must be positive")
        if self.max_snoozes < 0:
            raise ValueError("max_snoozes must not be negative")


@dataclass(frozen=True, slots=True)
class Prompt:
    """One reminder on screen. It may cover several activities."""

    id: int
    activities: tuple[PlannedActivity, ...]
    """Activities shown to the user, most important first."""
    members: frozenset[str]
    """Every activity this reminder resolves, including covered ones not shown."""
    opened_at: float
    can_snooze: bool

    @property
    def primary(self) -> PlannedActivity:
        return self.activities[0]

    @property
    def gentle(self) -> bool:
        return all(a.importance is Importance.GENTLE for a in self.activities)

    @property
    def duration_s(self) -> int:
        return max(a.duration_s for a in self.activities)


@dataclass(frozen=True, slots=True)
class PromptOpened:
    prompt: Prompt


@dataclass(frozen=True, slots=True)
class PromptClosed:
    prompt: Prompt
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class ActivityCompleted:
    """The user marked an activity as done without a reminder ("Do it now")."""

    activity_id: str


@dataclass(frozen=True, slots=True)
class PauseChanged:
    paused: bool
    until: datetime | None


Event = PromptOpened | PromptClosed | ActivityCompleted | PauseChanged


@dataclass(slots=True)
class _Timer:
    activity: PlannedActivity
    elapsed: float = 0.0
    snooze_target: float | None = None
    snoozes: int = 0

    @property
    def remaining(self) -> float:
        target = self.snooze_target if self.snooze_target is not None else self.activity.interval_s
        return target - self.elapsed

    def reset(self) -> None:
        self.elapsed = 0.0
        self.snooze_target = None
        self.snoozes = 0

    def snooze(self, seconds: float) -> None:
        self.snooze_target = self.elapsed + seconds
        self.snoozes += 1


class Scheduler:
    """Per-activity active-time clocks plus the rules that turn them into reminders."""

    def __init__(self, clock: Clock, config: SchedulerConfig | None = None) -> None:
        self._clock = clock
        self._config = config or SchedulerConfig()
        self._timers: dict[str, _Timer] = {}
        self._away_reasons: set[AwayReason] = set()
        self._away_since: float | None = None
        self._paused = False
        self._paused_until: datetime | None = None
        self._prompt: Prompt | None = None
        self._prompt_active_s = 0.0
        self._last_prompt_at: float | None = None
        self._next_prompt_id = 1
        self._last_boot = clock.boottime()
        self._last_mono = clock.monotonic()
        self._accounted_until = self._last_boot

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def config(self) -> SchedulerConfig:
        return self._config

    @property
    def present(self) -> bool:
        return not self._away_reasons

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def paused_until(self) -> datetime | None:
        return self._paused_until

    @property
    def prompt(self) -> Prompt | None:
        return self._prompt

    def remaining(self, activity_id: str) -> float | None:
        """Active seconds until the activity is due, or ``None`` if it is not planned."""
        timer = self._timers.get(activity_id)
        return None if timer is None else timer.remaining

    def upcoming(self) -> list[tuple[PlannedActivity, float]]:
        """Planned activities with their remaining active seconds, soonest first."""
        return sorted(
            ((t.activity, t.remaining) for t in self._timers.values()),
            key=lambda item: (item[1], item[0].position),
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_config(self, config: SchedulerConfig) -> None:
        self._config = config

    def set_plan(self, activities: Iterable[PlannedActivity]) -> list[Event]:
        """Replace the enabled activities, keeping the progress of those that remain."""
        events = self._sync()
        timers: dict[str, _Timer] = {}
        for activity in activities:
            timer = self._timers.get(activity.id) or _Timer(activity)
            timer.activity = activity
            timers[activity.id] = timer
        self._timers = timers
        if self._prompt is not None and not self._prompt.members <= timers.keys():
            events.extend(self._close_prompt(Outcome.CANCELLED))
        return events

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def tick(self) -> list[Event]:
        """Advance the clocks and open or expire reminders. Call every few seconds."""
        events = self._sync()
        events.extend(self._expire_pause())
        events.extend(self._expire_prompt())
        events.extend(self._maybe_open_prompt())
        return events

    def user_away(self, reason: AwayReason, idle_for: float = 0.0) -> list[Event]:
        """The user left. ``idle_for`` back-dates the start (idle is noticed after a delay)."""
        events = self._sync()
        events.extend(self._go_away(reason, self._last_boot - max(0.0, idle_for)))
        return events

    def user_back(self, reason: AwayReason) -> list[Event]:
        events = self._sync()
        events.extend(self._come_back(reason, self._last_boot))
        return events

    def respond(self, prompt_id: int, outcome: Outcome) -> list[Event]:
        """Apply the user's answer to the open reminder. Stale answers are ignored."""
        if outcome not in _ANSWERS:
            raise ValueError(f"{outcome!r} is not an answer a user can give")
        events = self._sync()
        prompt = self._prompt
        if prompt is None or prompt.id != prompt_id:
            return events
        if outcome is Outcome.SNOOZED and not prompt.can_snooze:
            return events
        events.extend(self._close_prompt(outcome))
        return events

    def complete(self, activity_id: str) -> list[Event]:
        """Mark an activity as done right now, with or without an open reminder."""
        events = self._sync()
        if self._prompt is not None and activity_id in self._prompt.members:
            events.extend(self._close_prompt(Outcome.DONE))
            return events
        timer = self._timers.get(activity_id)
        if timer is not None:
            self._reset_with_covers(timer)
            events.append(ActivityCompleted(activity_id))
        return events

    def pause(self, until: datetime | None = None) -> list[Event]:
        """Freeze every clock. ``until=None`` pauses until :meth:`resume`."""
        events = self._sync()
        if self._prompt is not None:
            events.extend(self._close_prompt(Outcome.CANCELLED))
        self._paused = True
        self._paused_until = until
        events.append(PauseChanged(True, until))
        return events

    def resume(self) -> list[Event]:
        events = self._sync()
        if self._paused:
            events.extend(self._resume())
        return events

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sync(self) -> list[Event]:
        """Count active time up to now, treating a suspend as time away."""
        boot, mono = self._clock.boottime(), self._clock.monotonic()
        slept = (boot - self._last_boot) - (mono - self._last_mono)
        events: list[Event] = []
        if slept > SUSPEND_THRESHOLD_S:
            asleep_at = boot - slept
            self._account(asleep_at)
            events.extend(self._go_away(AwayReason.SUSPENDED, asleep_at))
            self._last_boot, self._last_mono = boot, mono
            events.extend(self._come_back(AwayReason.SUSPENDED, boot))
        self._last_boot, self._last_mono = boot, mono
        self._account(boot)
        return events

    def _account(self, until: float) -> None:
        delta = until - self._accounted_until
        if delta <= 0:
            return
        if self.present and not self._paused:
            for timer in self._timers.values():
                timer.elapsed += delta
            if self._prompt is not None:
                self._prompt_active_s += delta
        self._accounted_until = until

    def _go_away(self, reason: AwayReason, since: float) -> list[Event]:
        was_present = self.present
        self._away_reasons.add(reason)
        if not was_present:
            return []
        since = min(since, self._accounted_until)
        overcounted = self._accounted_until - since
        if overcounted > 0 and not self._paused:
            for timer in self._timers.values():
                timer.elapsed = max(0.0, timer.elapsed - overcounted)
            self._prompt_active_s = max(0.0, self._prompt_active_s - overcounted)
        self._accounted_until = since
        self._away_since = since
        return []

    def _come_back(self, reason: AwayReason, at: float) -> list[Event]:
        if reason not in self._away_reasons:
            return []
        self._away_reasons.discard(reason)
        if self._away_reasons:
            return []  # Still away for another reason (for example, still locked).
        away_for = at - (self._away_since if self._away_since is not None else at)
        self._away_since = None
        self._accounted_until = at

        credited: set[str] = set()
        for activity_id, timer in self._timers.items():
            threshold = timer.activity.done_if_away_s
            if threshold is not None and away_for >= threshold:
                timer.reset()
                credited.add(activity_id)

        prompt = self._prompt
        if prompt is None:
            return []
        shown = {a.id for a in prompt.activities if a.tracked} or {a.id for a in prompt.activities}
        if shown <= credited:
            return self._close_prompt(Outcome.AWAY)
        return []

    def _resume(self) -> list[Event]:
        self._paused = False
        self._paused_until = None
        self._accounted_until = self._last_boot
        return [PauseChanged(False, None)]

    def _expire_pause(self) -> list[Event]:
        until = self._paused_until
        if self._paused and until is not None and self._clock.now() >= until:
            return self._resume()
        return []

    def _expire_prompt(self) -> list[Event]:
        prompt = self._prompt
        if prompt is None:
            return []
        if prompt.gentle:
            if self._last_boot - prompt.opened_at >= self._config.gentle_visible_s:
                return self._close_prompt(Outcome.DISMISSED)
        elif self._prompt_active_s >= self._config.prompt_timeout_s:
            return self._close_prompt(Outcome.MISSED)
        return []

    def _maybe_open_prompt(self) -> list[Event]:
        if self._prompt is not None or self._paused or not self.present:
            return []
        now = self._last_boot
        if self._last_prompt_at is not None and now - self._last_prompt_at < self._config.min_gap_s:
            return []
        if not any(t.remaining <= 0 for t in self._timers.values()):
            return []

        candidates = [
            t for t in self._timers.values() if t.remaining <= self._config.merge_window_s
        ]
        candidate_ids = {t.activity.id for t in candidates}
        covered: set[str] = set()
        for timer in candidates:
            covered |= timer.activity.covers & candidate_ids
        shown = [t for t in candidates if t.activity.id not in covered] or candidates
        shown.sort(
            key=lambda t: (
                _IMPORTANCE_ORDER[t.activity.importance],
                t.remaining,
                t.activity.position,
            )
        )

        prompt = Prompt(
            id=self._next_prompt_id,
            activities=tuple(t.activity for t in shown),
            members=frozenset(candidate_ids),
            opened_at=now,
            can_snooze=all(t.snoozes < self._config.max_snoozes for t in candidates),
        )
        self._next_prompt_id += 1
        self._prompt = prompt
        self._prompt_active_s = 0.0
        self._last_prompt_at = now
        return [PromptOpened(prompt)]

    def _close_prompt(self, outcome: Outcome) -> list[Event]:
        prompt = self._prompt
        if prompt is None:
            return []
        self._prompt = None
        self._prompt_active_s = 0.0
        if outcome is not Outcome.CANCELLED:
            for activity_id in prompt.members:
                timer = self._timers.get(activity_id)
                if timer is None:
                    continue
                if outcome is Outcome.SNOOZED:
                    timer.snooze(self._config.snooze_s)
                elif outcome is Outcome.DONE:
                    self._reset_with_covers(timer)
                else:
                    timer.reset()
        return [PromptClosed(prompt, outcome)]

    def _reset_with_covers(self, timer: _Timer) -> None:
        timer.reset()
        for covered_id in timer.activity.covers:
            covered = self._timers.get(covered_id)
            if covered is not None:
                covered.reset()
