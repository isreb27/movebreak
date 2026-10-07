# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduler behaviour, driven by a fake clock in 10-second ticks like the app."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import FakeClock, planned
from movebreak.core.models import Importance, Outcome
from movebreak.core.scheduler import (
    ActivityCompleted,
    AwayReason,
    Event,
    PauseChanged,
    Prompt,
    PromptClosed,
    PromptOpened,
    Scheduler,
    SchedulerConfig,
)

TICK = 10.0

WALK = planned("walk", interval_min=30, duration_s=180, away_min=5, covers=frozenset({"eyes"}))
EYES = planned("eyes", interval_min=20, duration_s=20, importance=Importance.GENTLE, away_min=1)
STRETCH = planned("stretch", interval_min=60, duration_s=90, position=2)


def run(scheduler: Scheduler, clock: FakeClock, seconds: float) -> list[Event]:
    """Advance time in ticks and collect every event."""
    events: list[Event] = []
    elapsed = 0.0
    while elapsed < seconds:
        step = min(TICK, seconds - elapsed)
        clock.advance(step)
        elapsed += step
        events.extend(scheduler.tick())
    return events


def opened(events: list[Event]) -> list[Prompt]:
    return [e.prompt for e in events if isinstance(e, PromptOpened)]


def closed(events: list[Event]) -> list[tuple[Prompt, Outcome]]:
    return [(e.prompt, e.outcome) for e in events if isinstance(e, PromptClosed)]


@pytest.fixture
def scheduler(clock: FakeClock) -> Scheduler:
    return Scheduler(clock)


def test_reminds_after_interval_of_active_time(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    assert opened(run(scheduler, clock, 29 * 60)) == []
    prompts = opened(run(scheduler, clock, 60))
    assert [p.primary.id for p in prompts] == ["walk"]


def test_idle_time_does_not_count(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 20 * 60)
    scheduler.user_away(AwayReason.IDLE)
    run(scheduler, clock, 3 * 60)  # Shorter than the 5-minute credit.
    scheduler.user_back(AwayReason.IDLE)
    remaining = scheduler.remaining("walk")
    assert remaining == pytest.approx(10 * 60)


def test_idle_is_backdated(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 10 * 60)
    # The idle monitor notices 60 s of inactivity only after those 60 s.
    scheduler.user_away(AwayReason.IDLE, idle_for=60)
    assert scheduler.remaining("walk") == pytest.approx(21 * 60)


def test_long_absence_counts_as_done(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK, STRETCH])
    run(scheduler, clock, 25 * 60)
    scheduler.user_away(AwayReason.LOCKED)
    run(scheduler, clock, 45 * 60)  # Lunch.
    events = scheduler.user_back(AwayReason.LOCKED)
    assert events == []
    assert scheduler.remaining("walk") == pytest.approx(30 * 60)
    # Stretch has no away credit, so it keeps its progress.
    assert scheduler.remaining("stretch") == pytest.approx(35 * 60)


def test_still_away_while_any_reason_remains(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    scheduler.user_away(AwayReason.IDLE)
    scheduler.user_away(AwayReason.LOCKED)
    run(scheduler, clock, 60)
    scheduler.user_back(AwayReason.IDLE)  # A key press on the lock screen.
    assert not scheduler.present
    scheduler.user_back(AwayReason.LOCKED)
    assert scheduler.present


def test_suspend_is_detected_and_credited(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 25 * 60)
    clock.suspend(2 * 60 * 60)
    events = scheduler.tick()
    assert opened(events) == []  # No reminder fires straight after waking up.
    assert scheduler.remaining("walk") == pytest.approx(30 * 60)


def test_short_suspend_is_not_counted_as_active(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 10 * 60)
    clock.suspend(2 * 60)  # Shorter than the 5-minute credit.
    scheduler.tick()
    assert scheduler.remaining("walk") == pytest.approx(20 * 60)


def test_due_activities_merge_and_covered_ones_are_hidden(
    scheduler: Scheduler, clock: FakeClock
) -> None:
    eyes = planned("eyes", interval_min=30, importance=Importance.GENTLE, away_min=1)
    scheduler.set_plan([WALK, eyes, STRETCH])
    prompts = opened(run(scheduler, clock, 30 * 60))
    assert len(prompts) == 1
    prompt = prompts[0]
    # Eyes are due too, but the walk covers them.
    assert [a.id for a in prompt.activities] == ["walk"]
    assert prompt.members == {"walk", "eyes"}


def test_walk_and_stretch_merge_on_the_hour(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK, STRETCH])
    first = opened(run(scheduler, clock, 30 * 60))[0]
    scheduler.respond(first.id, Outcome.DONE)
    second = opened(run(scheduler, clock, 30 * 60))[0]
    assert [a.id for a in second.activities] == ["walk", "stretch"]


def test_done_resets_covered_activities(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK, EYES])
    events = run(scheduler, clock, 20 * 60)
    eye_prompt = opened(events)[0]
    assert eye_prompt.gentle
    events = run(scheduler, clock, 10 * 60)
    walk_prompt = opened(events)[0]
    scheduler.respond(walk_prompt.id, Outcome.DONE)
    assert scheduler.remaining("eyes") == pytest.approx(20 * 60)


def test_gentle_reminders_dismiss_themselves(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([EYES])
    run(scheduler, clock, 20 * 60)
    events = run(scheduler, clock, 30)
    assert [outcome for _, outcome in closed(events)] == [Outcome.DISMISSED]


def test_minimum_gap_between_reminders(scheduler: Scheduler, clock: FakeClock) -> None:
    a = planned("a", interval_min=10, importance=Importance.GENTLE)
    b = planned("b", interval_min=14)  # Due 4 minutes after "a": outside the merge window.
    scheduler.set_plan([a, b])
    run(scheduler, clock, 10 * 60)
    events = run(scheduler, clock, 4 * 60 + TICK)
    assert opened(events) == []  # "b" is due but must wait for the 5-minute gap.
    events = run(scheduler, clock, 60)
    assert [p.primary.id for p in opened(events)] == ["b"]


def test_snooze_then_no_more_snoozes(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    prompt = opened(run(scheduler, clock, 30 * 60))[0]
    for _ in range(2):
        assert prompt.can_snooze
        scheduler.respond(prompt.id, Outcome.SNOOZED)
        assert opened(run(scheduler, clock, 9 * 60)) == []
        prompt = opened(run(scheduler, clock, 60 + TICK))[0]
    assert not prompt.can_snooze
    events = scheduler.respond(prompt.id, Outcome.SNOOZED)
    assert events == []  # Refused: only Done or Skip are left.
    assert scheduler.prompt == prompt


def test_unanswered_reminder_expires_as_missed(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 30 * 60)
    events = run(scheduler, clock, 5 * 60)
    assert [outcome for _, outcome in closed(events)] == [Outcome.MISSED]
    assert scheduler.remaining("walk") == pytest.approx(30 * 60, abs=TICK)


def test_coming_back_after_walking_away_closes_the_reminder(
    scheduler: Scheduler, clock: FakeClock
) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 30 * 60)
    scheduler.user_away(AwayReason.IDLE)
    run(scheduler, clock, 6 * 60)
    events = scheduler.user_back(AwayReason.IDLE)
    assert [outcome for _, outcome in closed(events)] == [Outcome.AWAY]


def test_pause_freezes_clocks_and_resumes_on_time(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 10 * 60)
    events = scheduler.pause(until=clock.now() + timedelta(hours=1))
    assert any(isinstance(e, PauseChanged) and e.paused for e in events)
    events = run(scheduler, clock, 60 * 60 + TICK)
    assert opened(events) == []
    assert any(isinstance(e, PauseChanged) and not e.paused for e in events)
    assert scheduler.remaining("walk") == pytest.approx(20 * 60, abs=TICK)


def test_pause_withdraws_open_reminder(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    run(scheduler, clock, 30 * 60)
    events = scheduler.pause()
    assert [outcome for _, outcome in closed(events)] == [Outcome.CANCELLED]
    assert scheduler.paused_until is None


def test_complete_outside_a_reminder(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK, EYES])
    run(scheduler, clock, 15 * 60)
    events = scheduler.complete("walk")
    assert events == [ActivityCompleted("walk")]
    assert scheduler.remaining("walk") == pytest.approx(30 * 60)
    assert scheduler.remaining("eyes") == pytest.approx(20 * 60)


def test_set_plan_keeps_progress_and_cancels_removed(
    scheduler: Scheduler, clock: FakeClock
) -> None:
    scheduler.set_plan([WALK, STRETCH])
    run(scheduler, clock, 30 * 60)
    assert scheduler.prompt is not None
    events = scheduler.set_plan([STRETCH])
    assert [outcome for _, outcome in closed(events)] == [Outcome.CANCELLED]
    assert scheduler.remaining("stretch") == pytest.approx(30 * 60, abs=TICK)


def test_stale_answers_are_ignored(scheduler: Scheduler, clock: FakeClock) -> None:
    scheduler.set_plan([WALK])
    prompt = opened(run(scheduler, clock, 30 * 60))[0]
    scheduler.respond(prompt.id, Outcome.DONE)
    assert scheduler.respond(prompt.id, Outcome.SKIPPED) == []


def test_invalid_answer_is_rejected(scheduler: Scheduler, clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        scheduler.respond(1, Outcome.MISSED)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        SchedulerConfig(min_gap_s=-1)
    with pytest.raises(ValueError):
        SchedulerConfig(snooze_s=0)
