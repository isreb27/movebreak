# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Time sources, abstracted so the scheduler can be tested with a fake clock.

Two monotonic clocks are needed to notice a suspend without any extra
permission: ``CLOCK_BOOTTIME`` keeps counting while the machine sleeps,
``CLOCK_MONOTONIC`` does not. When the first advances much more than the
second between two ticks, the difference is the time spent asleep.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def boottime(self) -> float:
        """Seconds since boot, including time spent suspended."""
        ...

    def monotonic(self) -> float:
        """Seconds since boot, excluding time spent suspended."""
        ...

    def now(self) -> datetime:
        """Current wall-clock time, timezone-aware."""
        ...


class SystemClock:
    """The real clocks of a Linux system."""

    def boottime(self) -> float:
        return time.clock_gettime(time.CLOCK_BOOTTIME)

    def monotonic(self) -> float:
        return time.clock_gettime(time.CLOCK_MONOTONIC)

    def now(self) -> datetime:
        return datetime.now().astimezone()
