# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from movebreak.core.models import Importance, PlannedActivity
from movebreak.core.store import Store


class FakeClock:
    """A controllable clock. ``advance`` simulates use, ``suspend`` simulates sleep."""

    def __init__(self) -> None:
        self._boot = 1000.0
        self._mono = 1000.0
        self._wall = datetime(2026, 10, 7, 9, 0, tzinfo=timezone(timedelta(hours=2)))

    def boottime(self) -> float:
        return self._boot

    def monotonic(self) -> float:
        return self._mono

    def now(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._boot += seconds
        self._mono += seconds
        self._wall += timedelta(seconds=seconds)

    def suspend(self, seconds: float) -> None:
        self._boot += seconds
        self._wall += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


def planned(
    activity_id: str,
    *,
    interval_min: float,
    duration_s: int = 60,
    importance: Importance = Importance.NORMAL,
    away_min: float | None = None,
    covers: frozenset[str] = frozenset(),
    position: int = 0,
) -> PlannedActivity:
    """Build a PlannedActivity with readable minute-based arguments."""
    return PlannedActivity(
        id=activity_id,
        name=activity_id.title(),
        icon="emoji-people-symbolic",
        tips=(f"{activity_id} tip 1", f"{activity_id} tip 2"),
        covers=covers,
        interval_s=int(interval_min * 60),
        duration_s=duration_s,
        importance=importance,
        done_if_away_s=None if away_min is None else int(away_min * 60),
        position=position,
    )
