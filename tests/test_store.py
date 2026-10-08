# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence: seeding, profiles, activities, preferences and history."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from movebreak.core import presets
from movebreak.core.models import ActivitySettings, Importance, Outcome
from movebreak.core.scheduler import SchedulerConfig
from movebreak.core.store import PauseState, Store, StoreError


def test_seeds_builtins_with_settings_for_every_pair(store: Store) -> None:
    activity_ids = {a.id for a in store.activities()}
    assert {presets.WALK, presets.EYES, presets.STRETCH} <= activity_ids
    for profile in store.profiles():
        assert set(store.settings(profile.id)) == activity_ids
    assert store.active_profile().id == presets.DEFAULT_PROFILE_ID


def test_recommended_plan_enables_walk_eyes_stretch(store: Store) -> None:
    assert [a.id for a in store.plan()] == [presets.WALK, presets.EYES, presets.STRETCH]


def test_light_profile_has_fewer_reminders(store: Store) -> None:
    plan = {a.id: a for a in store.plan(presets.LIGHT)}
    assert presets.EYES not in plan
    assert plan[presets.WALK].interval_s == 45 * 60


def test_reopening_keeps_data_and_does_not_reseed(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    first = Store(path)
    first.update_settings(
        presets.RECOMMENDED,
        presets.WALK,
        ActivitySettings(True, 40 * 60, 120, Importance.NORMAL, 300),
    )
    first.close()
    second = Store(path)
    assert second.settings(presets.RECOMMENDED)[presets.WALK].interval_s == 40 * 60
    assert second.schema_version == 1


def test_newer_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    Store(path).close()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(StoreError):
        Store(path)


def test_custom_activity_is_enabled_only_in_its_profile(store: Store) -> None:
    pushups = store.add_activity(
        name="  Push-ups ",
        icon="emoji-activities-symbolic",
        tips=("10 push-ups", " "),
        covers=frozenset({presets.EYES}),
        settings=presets.new_activity_settings(),
        profile_id=presets.RECOMMENDED,
    )
    assert pushups.name == "Push-ups"
    assert pushups.tips == ("10 push-ups",)
    assert not pushups.builtin
    assert store.settings(presets.RECOMMENDED)[pushups.id].enabled
    assert not store.settings(presets.LIGHT)[pushups.id].enabled
    assert pushups.id in {a.id for a in store.plan()}


def test_deleting_custom_activity_cleans_up_covers(store: Store) -> None:
    pushups = store.add_activity(
        name="Push-ups",
        icon="emoji-activities-symbolic",
        tips=(),
        covers=frozenset(),
        settings=presets.new_activity_settings(),
        profile_id=presets.RECOMMENDED,
    )
    walk = store.activity(presets.WALK)
    store.update_activity(replace(walk, covers=walk.covers | {pushups.id}))
    store.delete_activity(pushups.id)
    assert pushups.id not in store.activity(presets.WALK).covers
    assert pushups.id not in store.settings(presets.RECOMMENDED)


def test_builtin_activity_cannot_be_deleted_but_can_be_reset(store: Store) -> None:
    with pytest.raises(ValueError):
        store.delete_activity(presets.WALK)
    walk = store.activity(presets.WALK)
    store.update_activity(replace(walk, name="Stroll", tips=("Go",)))
    store.update_settings(
        presets.RECOMMENDED, presets.WALK, ActivitySettings(False, 3600, 60, Importance.NORMAL)
    )
    store.reset_activity(presets.WALK, presets.RECOMMENDED)
    assert store.activity(presets.WALK) == walk
    assert store.settings(presets.RECOMMENDED)[presets.WALK] == presets.preset_settings(
        presets.RECOMMENDED, presets.WALK
    )


def test_unknown_covers_are_rejected(store: Store) -> None:
    walk = store.activity(presets.WALK)
    with pytest.raises(ValueError):
        store.update_activity(replace(walk, covers=frozenset({"nope"})))


def test_profile_lifecycle(store: Store) -> None:
    week = store.create_profile("Busy week", copy_from=presets.LIGHT)
    assert store.settings(week.id) == store.settings(presets.LIGHT)
    with pytest.raises(ValueError):
        store.create_profile("busy WEEK", copy_from=presets.LIGHT)

    store.set_active_profile(week.id)
    assert store.find_profile("busy week") == week
    renamed = store.rename_profile(week.id, "Crunch")
    assert renamed.name == "Crunch"

    store.delete_profile(week.id)
    assert store.active_profile().id == presets.DEFAULT_PROFILE_ID
    with pytest.raises(ValueError):
        store.delete_profile(presets.LIGHT)


def test_reset_builtin_profile(store: Store) -> None:
    store.update_settings(
        presets.LIGHT, presets.WALK, ActivitySettings(False, 600, 10, Importance.GENTLE)
    )
    store.reset_profile(presets.LIGHT)
    assert store.settings(presets.LIGHT)[presets.WALK] == presets.preset_settings(
        presets.LIGHT, presets.WALK
    )


def test_pause_state_round_trip(store: Store) -> None:
    assert store.pause_state() == PauseState(paused=False)
    until = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
    store.set_pause_state(PauseState(paused=True, until=until))
    assert store.pause_state() == PauseState(paused=True, until=until)
    store.set_pause_state(PauseState(paused=True))
    assert store.pause_state() == PauseState(paused=True, until=None)
    store.set_pause_state(PauseState(paused=False))
    assert not store.pause_state().paused


def test_scheduler_config_round_trip(store: Store) -> None:
    config = SchedulerConfig(snooze_s=300, min_gap_s=0, merge_window_s=60, prompt_timeout_s=120)
    store.set_scheduler_config(config)
    assert store.scheduler_config() == config


def test_history_counts(store: Store) -> None:
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    store.log_outcome(activity_id="walk", profile_id="recommended", outcome=Outcome.DONE, at=now)
    store.log_outcome(activity_id="walk", profile_id="recommended", outcome=Outcome.SKIPPED, at=now)
    store.log_outcome(
        activity_id="walk",
        profile_id="recommended",
        outcome=Outcome.DONE,
        at=now - timedelta(days=1),
    )
    store.log_outcome(
        activity_id="walk", profile_id="recommended", outcome=Outcome.CANCELLED, at=now
    )
    counts = store.outcome_counts(since=now - timedelta(hours=1))
    assert counts[Outcome.DONE] == 1
    assert counts[Outcome.SKIPPED] == 1
    assert counts[Outcome.CANCELLED] == 0


def test_tray_icon_preference(store: Store) -> None:
    assert store.tray_icon_enabled()
    store.set_tray_icon_enabled(False)
    assert not store.tray_icon_enabled()


def test_reminder_style_preference(store: Store) -> None:
    from movebreak.core.models import ReminderStyle

    assert store.reminder_style() is ReminderStyle.BANNER
    store.set_reminder_style(ReminderStyle.BREAK_SCREEN)
    assert store.reminder_style() is ReminderStyle.BREAK_SCREEN
