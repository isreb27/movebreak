# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in catalogue consistency and user-facing wording."""

from __future__ import annotations

from conftest import planned
from movebreak.core import presets
from movebreak.core.models import ActivitySettings, Importance
from movebreak.core.scheduler import Prompt
from movebreak.core.text import (
    TipRotation,
    describe_settings,
    format_duration,
    format_remaining,
    reminder_text,
)


def test_every_builtin_profile_covers_every_builtin_activity() -> None:
    activity_ids = {a.id for a in presets.builtin_activities()}
    for profile in presets.builtin_profiles():
        for activity_id in activity_ids:
            assert presets.preset_settings(profile.id, activity_id) is not None


def test_covers_reference_existing_activities() -> None:
    activities = presets.builtin_activities()
    ids = {a.id for a in activities}
    for activity in activities:
        assert activity.covers <= ids
        assert activity.icon in presets.ICON_CHOICES


def test_builtin_ids_are_unique() -> None:
    ids = [a.id for a in presets.builtin_activities()]
    assert len(ids) == len(set(ids))


def test_format_duration() -> None:
    assert format_duration(20) == "20 s"
    assert format_duration(90) == "1 min 30 s"
    assert format_duration(180) == "3 min"
    assert format_duration(3600) == "1 h"


def test_format_remaining_rounds_up() -> None:
    assert format_remaining(10) == "now"
    assert format_remaining(61) == "in 2 min"
    assert format_remaining(90 * 60) == "in 1 h 30 min"


def test_describe_settings() -> None:
    settings = ActivitySettings(True, 1200, 20, Importance.GENTLE)
    assert describe_settings(settings) == "Every 20 min · 20 s · gentle"


def test_tips_rotate() -> None:
    walk = planned("walk", interval_min=30)
    rotation = TipRotation()
    assert [rotation.next_tip(walk) for _ in range(3)] == ["walk tip 1", "walk tip 2", "walk tip 1"]


def test_reminder_text_for_merged_prompt() -> None:
    walk = planned("walk", interval_min=30, duration_s=180)
    stretch = planned("stretch", interval_min=60, duration_s=90)
    prompt = Prompt(
        id=1,
        activities=(walk, stretch),
        members=frozenset({"walk", "stretch"}),
        opened_at=0,
        can_snooze=True,
    )
    title, body = reminder_text(prompt, TipRotation())
    assert title == "Walk + Stretch · 3 min"
    assert body == "walk tip 1\nstretch tip 1"
