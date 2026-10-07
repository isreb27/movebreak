# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""SQLite persistence for activities, profiles, preferences and history.

One file holds everything, which keeps the app identical whether it is
installed natively or as a Flatpak and avoids compiling GSettings schemas.

Schema changes are applied by :data:`_MIGRATIONS`, tracked with
``PRAGMA user_version``. Never edit an existing migration; append a new one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from movebreak.core import presets
from movebreak.core.models import (
    MAX_NAME_LENGTH,
    Activity,
    ActivitySettings,
    Importance,
    Outcome,
    PlannedActivity,
    Profile,
)
from movebreak.core.scheduler import SchedulerConfig

_MIGRATIONS: tuple[str, ...] = (
    # 1: initial schema
    """
    CREATE TABLE activity (
        id           TEXT PRIMARY KEY,
        builtin      INTEGER NOT NULL,
        name         TEXT NOT NULL,
        icon         TEXT NOT NULL,
        tips         TEXT NOT NULL,      -- JSON array of strings
        covers       TEXT NOT NULL,      -- JSON array of activity ids
        position     INTEGER NOT NULL
    );
    CREATE TABLE profile (
        id           TEXT PRIMARY KEY,
        builtin      INTEGER NOT NULL,
        name         TEXT NOT NULL,
        position     INTEGER NOT NULL
    );
    CREATE TABLE activity_settings (
        profile_id     TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
        activity_id    TEXT NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
        enabled        INTEGER NOT NULL,
        interval_s     INTEGER NOT NULL,
        duration_s     INTEGER NOT NULL,
        importance     TEXT NOT NULL,
        done_if_away_s INTEGER,          -- NULL: never
        PRIMARY KEY (profile_id, activity_id)
    );
    CREATE TABLE preference (
        key          TEXT PRIMARY KEY,
        value        TEXT NOT NULL       -- JSON
    );
    CREATE TABLE event (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        at           TEXT NOT NULL,      -- ISO 8601, UTC
        activity_id  TEXT NOT NULL,      -- no foreign key: history outlives deleted activities
        profile_id   TEXT NOT NULL,
        outcome      TEXT NOT NULL
    );
    CREATE INDEX event_at ON event(at);
    """,
)

_PREF_ACTIVE_PROFILE = "active_profile"
_PREF_PAUSE = "pause"
_PREF_SCHEDULER = "scheduler"
_PREF_AUTOSTART = "autostart"
_PREF_TRAY_ICON = "tray_icon"


class StoreError(Exception):
    """The database cannot be used (for example, it comes from a newer version)."""


@dataclass(frozen=True, slots=True)
class PauseState:
    paused: bool
    until: datetime | None = None


def _to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("name must not be empty")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"name must be at most {MAX_NAME_LENGTH} characters")
    return cleaned


def _new_id() -> str:
    return f"custom-{uuid.uuid4().hex[:12]}"


class Store:
    """Typed access to the database. Not thread-safe: use it from the main loop."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        in_memory = str(path) == ":memory:"
        if not in_memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if not in_memory:
            self._db.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        self._ensure_builtins()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @property
    def schema_version(self) -> int:
        return int(self._db.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        current = self.schema_version
        if current > len(_MIGRATIONS):
            raise StoreError(
                f"The database uses schema {current}, but this version of Movebreak only "
                f"knows up to {len(_MIGRATIONS)}. Update Movebreak."
            )
        for version in range(current, len(_MIGRATIONS)):
            self._db.executescript(
                f"BEGIN;\n{_MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def _ensure_builtins(self) -> None:
        """Add built-in items and missing settings rows. Safe to run on every start."""
        with self._transaction() as db:
            for activity in presets.builtin_activities():
                db.execute(
                    "INSERT OR IGNORE INTO activity"
                    " (id, builtin, name, icon, tips, covers, position)"
                    " VALUES (?, 1, ?, ?, ?, ?, ?)",
                    (
                        activity.id,
                        activity.name,
                        activity.icon,
                        json.dumps(list(activity.tips)),
                        json.dumps(sorted(activity.covers)),
                        activity.position,
                    ),
                )
            for profile in presets.builtin_profiles():
                db.execute(
                    "INSERT OR IGNORE INTO profile (id, builtin, name, position)"
                    " VALUES (?, 1, ?, ?)",
                    (profile.id, profile.name, profile.position),
                )
            missing = db.execute(
                "SELECT p.id AS profile_id, a.id AS activity_id"
                " FROM profile p CROSS JOIN activity a"
                " WHERE NOT EXISTS (SELECT 1 FROM activity_settings s"
                "   WHERE s.profile_id = p.id AND s.activity_id = a.id)"
            ).fetchall()
            for row in missing:
                settings = presets.preset_settings(row["profile_id"], row["activity_id"])
                if settings is None:
                    settings = presets.fallback_settings(row["activity_id"], enabled=False)
                self._write_settings(db, row["profile_id"], row["activity_id"], settings)
            active = self._get_pref(_PREF_ACTIVE_PROFILE, None)
            if active is None or not self._profile_exists(active):
                self._set_pref(_PREF_ACTIVE_PROFILE, presets.DEFAULT_PROFILE_ID)

    # ------------------------------------------------------------------
    # Activities
    # ------------------------------------------------------------------

    @staticmethod
    def _activity_from_row(row: sqlite3.Row) -> Activity:
        return Activity(
            id=row["id"],
            name=row["name"],
            icon=row["icon"],
            tips=tuple(json.loads(row["tips"])),
            covers=frozenset(json.loads(row["covers"])),
            builtin=bool(row["builtin"]),
            position=row["position"],
        )

    def activities(self) -> list[Activity]:
        rows = self._db.execute("SELECT * FROM activity ORDER BY position, name").fetchall()
        return [self._activity_from_row(row) for row in rows]

    def activity(self, activity_id: str) -> Activity:
        row = self._db.execute("SELECT * FROM activity WHERE id = ?", (activity_id,)).fetchone()
        if row is None:
            raise KeyError(activity_id)
        return self._activity_from_row(row)

    def add_activity(
        self,
        *,
        name: str,
        icon: str,
        tips: tuple[str, ...],
        covers: frozenset[str],
        settings: ActivitySettings,
        profile_id: str,
    ) -> Activity:
        """Create a custom activity, enabled as given in ``profile_id`` and disabled elsewhere."""
        with self._transaction() as db:
            position = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM activity"
            ).fetchone()[0]
            activity = Activity(
                id=_new_id(),
                name=_clean_name(name),
                icon=icon,
                tips=_clean_tips(tips),
                covers=self._valid_covers(db, covers, exclude=None),
                position=position,
            )
            db.execute(
                "INSERT INTO activity (id, builtin, name, icon, tips, covers, position)"
                " VALUES (?, 0, ?, ?, ?, ?, ?)",
                (
                    activity.id,
                    activity.name,
                    activity.icon,
                    json.dumps(list(activity.tips)),
                    json.dumps(sorted(activity.covers)),
                    activity.position,
                ),
            )
            for row in db.execute("SELECT id FROM profile").fetchall():
                row_settings = (
                    settings if row["id"] == profile_id else replace(settings, enabled=False)
                )
                self._write_settings(db, row["id"], activity.id, row_settings)
        return activity

    def update_activity(self, activity: Activity) -> Activity:
        """Save the definition (name, icon, tips, covers) of an activity."""
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM activity WHERE id = ?", (activity.id,)).fetchone() is None:
                raise KeyError(activity.id)
            saved = replace(
                activity,
                name=_clean_name(activity.name),
                tips=_clean_tips(activity.tips),
                covers=self._valid_covers(db, activity.covers, exclude=activity.id),
            )
            db.execute(
                "UPDATE activity SET name = ?, icon = ?, tips = ?, covers = ? WHERE id = ?",
                (
                    saved.name,
                    saved.icon,
                    json.dumps(list(saved.tips)),
                    json.dumps(sorted(saved.covers)),
                    saved.id,
                ),
            )
        return saved

    def delete_activity(self, activity_id: str) -> None:
        """Delete a custom activity. Built-in ones can only be disabled or reset."""
        with self._transaction() as db:
            row = db.execute("SELECT builtin FROM activity WHERE id = ?", (activity_id,)).fetchone()
            if row is None:
                raise KeyError(activity_id)
            if row["builtin"]:
                raise ValueError("built-in activities cannot be deleted")
            db.execute("DELETE FROM activity WHERE id = ?", (activity_id,))
            for other in db.execute("SELECT id, covers FROM activity").fetchall():
                covers = json.loads(other["covers"])
                if activity_id in covers:
                    covers.remove(activity_id)
                    db.execute(
                        "UPDATE activity SET covers = ? WHERE id = ?",
                        (json.dumps(covers), other["id"]),
                    )

    def reset_activity(self, activity_id: str, profile_id: str) -> None:
        """Restore a built-in activity's definition, and its settings in ``profile_id``."""
        defaults = {a.id: a for a in presets.builtin_activities()}
        if activity_id not in defaults:
            raise ValueError("only built-in activities can be reset")
        original = defaults[activity_id]
        settings = presets.preset_settings(profile_id, activity_id) or presets.preset_settings(
            presets.DEFAULT_PROFILE_ID, activity_id
        )
        assert settings is not None  # Every built-in activity has a preset.
        with self._transaction() as db:
            db.execute(
                "UPDATE activity SET name = ?, icon = ?, tips = ?, covers = ? WHERE id = ?",
                (
                    original.name,
                    original.icon,
                    json.dumps(list(original.tips)),
                    json.dumps(sorted(original.covers)),
                    activity_id,
                ),
            )
            self._write_settings(db, profile_id, activity_id, settings)

    @staticmethod
    def _valid_covers(
        db: sqlite3.Connection, covers: frozenset[str], *, exclude: str | None
    ) -> frozenset[str]:
        known = {row["id"] for row in db.execute("SELECT id FROM activity").fetchall()}
        unknown = covers - known
        if unknown:
            raise ValueError(f"unknown activities in covers: {sorted(unknown)}")
        return frozenset(covers - {exclude} if exclude else covers)

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"], name=row["name"], builtin=bool(row["builtin"]), position=row["position"]
        )

    def _profile_exists(self, profile_id: str) -> bool:
        row = self._db.execute("SELECT 1 FROM profile WHERE id = ?", (profile_id,)).fetchone()
        return row is not None

    def profiles(self) -> list[Profile]:
        rows = self._db.execute("SELECT * FROM profile ORDER BY position, name").fetchall()
        return [self._profile_from_row(row) for row in rows]

    def profile(self, profile_id: str) -> Profile:
        row = self._db.execute("SELECT * FROM profile WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return self._profile_from_row(row)

    def find_profile(self, query: str) -> Profile | None:
        """Look a profile up by id or by name, ignoring case."""
        wanted = query.strip().casefold()
        for profile in self.profiles():
            if profile.id.casefold() == wanted or profile.name.casefold() == wanted:
                return profile
        return None

    def active_profile(self) -> Profile:
        profile_id = self._get_pref(_PREF_ACTIVE_PROFILE, presets.DEFAULT_PROFILE_ID)
        try:
            return self.profile(profile_id)
        except KeyError:
            return self.profile(presets.DEFAULT_PROFILE_ID)

    def set_active_profile(self, profile_id: str) -> None:
        if not self._profile_exists(profile_id):
            raise KeyError(profile_id)
        self._set_pref(_PREF_ACTIVE_PROFILE, profile_id)

    def create_profile(self, name: str, *, copy_from: str) -> Profile:
        """Create a profile with the same settings as ``copy_from``."""
        clean = _clean_name(name)
        with self._transaction() as db:
            self._check_unique_profile_name(db, clean, exclude=None)
            if db.execute("SELECT 1 FROM profile WHERE id = ?", (copy_from,)).fetchone() is None:
                raise KeyError(copy_from)
            position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM profile").fetchone()[
                0
            ]
            profile = Profile(id=_new_id(), name=clean, builtin=False, position=position)
            db.execute(
                "INSERT INTO profile (id, builtin, name, position) VALUES (?, 0, ?, ?)",
                (profile.id, profile.name, profile.position),
            )
            db.execute(
                "INSERT INTO activity_settings (profile_id, activity_id, enabled, interval_s,"
                " duration_s, importance, done_if_away_s)"
                " SELECT ?, activity_id, enabled, interval_s, duration_s, importance,"
                " done_if_away_s FROM activity_settings WHERE profile_id = ?",
                (profile.id, copy_from),
            )
        return profile

    def rename_profile(self, profile_id: str, name: str) -> Profile:
        clean = _clean_name(name)
        with self._transaction() as db:
            self._check_unique_profile_name(db, clean, exclude=profile_id)
            if (
                db.execute("UPDATE profile SET name = ? WHERE id = ?", (clean, profile_id)).rowcount
                == 0
            ):
                raise KeyError(profile_id)
        return self.profile(profile_id)

    def delete_profile(self, profile_id: str) -> None:
        """Delete a custom profile; the default becomes active if it was the active one."""
        profile = self.profile(profile_id)
        if profile.builtin:
            raise ValueError("built-in profiles cannot be deleted")
        with self._transaction() as db:
            db.execute("DELETE FROM profile WHERE id = ?", (profile_id,))
            if self._get_pref(_PREF_ACTIVE_PROFILE, None) == profile_id:
                self._set_pref(_PREF_ACTIVE_PROFILE, presets.DEFAULT_PROFILE_ID)

    def reset_profile(self, profile_id: str) -> None:
        """Restore a built-in profile's settings for built-in activities."""
        if not self.profile(profile_id).builtin:
            raise ValueError("only built-in profiles can be reset")
        with self._transaction() as db:
            for activity in presets.builtin_activities():
                settings = presets.preset_settings(profile_id, activity.id)
                if settings is not None:
                    self._write_settings(db, profile_id, activity.id, settings)

    @staticmethod
    def _check_unique_profile_name(
        db: sqlite3.Connection, name: str, *, exclude: str | None
    ) -> None:
        for row in db.execute("SELECT id, name FROM profile").fetchall():
            if row["id"] != exclude and row["name"].casefold() == name.casefold():
                raise ValueError(f"a profile named {name!r} already exists")

    # ------------------------------------------------------------------
    # Settings per profile
    # ------------------------------------------------------------------

    @staticmethod
    def _write_settings(
        db: sqlite3.Connection, profile_id: str, activity_id: str, settings: ActivitySettings
    ) -> None:
        db.execute(
            "INSERT INTO activity_settings (profile_id, activity_id, enabled, interval_s,"
            " duration_s, importance, done_if_away_s) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (profile_id, activity_id) DO UPDATE SET enabled = excluded.enabled,"
            " interval_s = excluded.interval_s, duration_s = excluded.duration_s,"
            " importance = excluded.importance, done_if_away_s = excluded.done_if_away_s",
            (
                profile_id,
                activity_id,
                int(settings.enabled),
                settings.interval_s,
                settings.duration_s,
                settings.importance.value,
                settings.done_if_away_s,
            ),
        )

    def settings(self, profile_id: str) -> dict[str, ActivitySettings]:
        rows = self._db.execute(
            "SELECT * FROM activity_settings WHERE profile_id = ?", (profile_id,)
        ).fetchall()
        return {
            row["activity_id"]: ActivitySettings(
                enabled=bool(row["enabled"]),
                interval_s=row["interval_s"],
                duration_s=row["duration_s"],
                importance=Importance(row["importance"]),
                done_if_away_s=row["done_if_away_s"],
            )
            for row in rows
        }

    def update_settings(
        self, profile_id: str, activity_id: str, settings: ActivitySettings
    ) -> None:
        with self._transaction() as db:
            self._write_settings(db, profile_id, activity_id, settings)

    def plan(self, profile_id: str | None = None) -> list[PlannedActivity]:
        """Enabled activities of a profile (the active one by default), in display order."""
        profile_id = profile_id or self.active_profile().id
        settings = self.settings(profile_id)
        return [
            PlannedActivity.from_parts(activity, settings[activity.id])
            for activity in self.activities()
            if activity.id in settings and settings[activity.id].enabled
        ]

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def _get_pref(self, key: str, default: Any) -> Any:
        row = self._db.execute("SELECT value FROM preference WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def _set_pref(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO preference (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def pause_state(self) -> PauseState:
        value = self._get_pref(_PREF_PAUSE, None)
        if not value:
            return PauseState(paused=False)
        until = value.get("until")
        return PauseState(paused=True, until=datetime.fromisoformat(until) if until else None)

    def set_pause_state(self, state: PauseState) -> None:
        if not state.paused:
            self._set_pref(_PREF_PAUSE, None)
            return
        until = _to_iso(state.until) if state.until else None
        self._set_pref(_PREF_PAUSE, {"until": until})

    def scheduler_config(self) -> SchedulerConfig:
        stored = self._get_pref(_PREF_SCHEDULER, {})
        defaults = SchedulerConfig()
        try:
            return SchedulerConfig(
                snooze_s=float(stored.get("snooze_s", defaults.snooze_s)),
                min_gap_s=float(stored.get("min_gap_s", defaults.min_gap_s)),
                merge_window_s=float(stored.get("merge_window_s", defaults.merge_window_s)),
                prompt_timeout_s=float(stored.get("prompt_timeout_s", defaults.prompt_timeout_s)),
            )
        except (TypeError, ValueError):
            return defaults

    def set_scheduler_config(self, config: SchedulerConfig) -> None:
        self._set_pref(
            _PREF_SCHEDULER,
            {
                "snooze_s": config.snooze_s,
                "min_gap_s": config.min_gap_s,
                "merge_window_s": config.merge_window_s,
                "prompt_timeout_s": config.prompt_timeout_s,
            },
        )

    def autostart_requested(self) -> bool:
        """Last autostart choice made in the app (needed in Flatpak, where the file is hidden)."""
        return bool(self._get_pref(_PREF_AUTOSTART, False))

    def set_autostart_requested(self, enabled: bool) -> None:
        self._set_pref(_PREF_AUTOSTART, enabled)

    def tray_icon_enabled(self) -> bool:
        """Whether to show the top-bar icon (on by default)."""
        return bool(self._get_pref(_PREF_TRAY_ICON, True))

    def set_tray_icon_enabled(self, enabled: bool) -> None:
        self._set_pref(_PREF_TRAY_ICON, enabled)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def log_outcome(
        self, *, activity_id: str, profile_id: str, outcome: Outcome, at: datetime
    ) -> None:
        if outcome is Outcome.CANCELLED:
            return
        self._db.execute(
            "INSERT INTO event (at, activity_id, profile_id, outcome) VALUES (?, ?, ?, ?)",
            (_to_iso(at), activity_id, profile_id, outcome.value),
        )

    def outcome_counts(self, since: datetime) -> dict[Outcome, int]:
        rows = self._db.execute(
            "SELECT outcome, COUNT(*) AS n FROM event WHERE at >= ? GROUP BY outcome",
            (_to_iso(since),),
        ).fetchall()
        counts = dict.fromkeys(Outcome, 0)
        for row in rows:
            counts[Outcome(row["outcome"])] = row["n"]
        return counts


def _clean_tips(tips: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(tip.strip() for tip in tips if tip.strip())
