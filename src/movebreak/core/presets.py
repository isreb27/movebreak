# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in activities and profiles.

The defaults follow the evidence summarised in ``docs/EVIDENCE.md``:

* a short walk every 30 minutes of sitting is the best-supported habit;
* 20-second eye breaks every 20 minutes help while they are kept up;
* stretching is framed as "move and switch position";
* strength snacks, water, breathing and standing are opt-in.

IDs of built-in items are stable strings and must never change, because
user settings refer to them.
"""

from __future__ import annotations

from dataclasses import replace

from movebreak.core.models import Activity, ActivitySettings, Importance, Profile
from movebreak.i18n import _

# Built-in activity IDs.
WALK = "walk"
EYES = "eyes"
STRETCH = "stretch"
STRENGTH = "strength"
WATER = "water"
BREATHING = "breathing"
SIT_STAND = "sit-stand"

# Built-in profile IDs.
RECOMMENDED = "recommended"
LIGHT = "light"
EYES_FIRST = "eyes-first"
DEFAULT_PROFILE_ID = RECOMMENDED

DEFAULT_CUSTOM_ICON = "movebreak-timer-symbolic"

ICON_CHOICES: tuple[str, ...] = (
    "movebreak-walk-symbolic",
    "view-reveal-symbolic",
    "movebreak-stretch-symbolic",
    "movebreak-strength-symbolic",
    "movebreak-water-symbolic",
    "movebreak-breathing-symbolic",
    "movebreak-stand-symbolic",
    "movebreak-timer-symbolic",
    "movebreak-heart-symbolic",
)
"""Icons offered in the activity editor. The ``movebreak-*`` ones ship in
``movebreak/icons``; ``view-reveal-symbolic`` is bundled with GTK 4. Both
render everywhere, including inside the Flatpak sandbox."""


def _minutes(value: int) -> int:
    return value * 60


def builtin_activities() -> list[Activity]:
    """The activity catalogue, in display order."""
    return [
        Activity(
            id=WALK,
            name=_("Walk break"),
            icon="movebreak-walk-symbolic",
            tips=(
                _("Walk to refill your water bottle."),
                _("Take the stairs, up and down."),
                _("Walk while you take your next call."),
                _("Walk to a window and back."),
                _("Do a lap of the office or the house."),
            ),
            covers=frozenset({EYES}),
            builtin=True,
            position=0,
        ),
        Activity(
            id=EYES,
            name=_("Eye break"),
            icon="view-reveal-symbolic",
            tips=(
                _("Look at something at least 6 metres (20 feet) away for 20 seconds."),
                _("Look out of a window and let your eyes relax."),
                _("Blink slowly ten times, then look into the distance."),
            ),
            builtin=True,
            position=1,
        ),
        Activity(
            id=STRETCH,
            name=_("Stretch and switch position"),
            icon="movebreak-stretch-symbolic",
            tips=(
                _("Slowly turn your head left and right, five times each way."),
                _("Roll your shoulders backwards ten times."),
                _("Stretch your wrists: arm out, gently pull the fingers back."),
                _("Stand up and do a gentle hip-flexor lunge on each side."),
                _("Clasp your hands behind your back and open your chest."),
            ),
            covers=frozenset({EYES}),
            builtin=True,
            position=2,
        ),
        Activity(
            id=STRENGTH,
            name=_("Strength snack"),
            icon="movebreak-strength-symbolic",
            tips=(
                _("10 push-ups: wall, desk or floor."),
                _("15 squats."),
                _("Climb two flights of stairs briskly."),
                _("30-second wall sit."),
            ),
            covers=frozenset({EYES}),
            builtin=True,
            position=3,
        ),
        Activity(
            id=WATER,
            name=_("Drink water"),
            icon="movebreak-water-symbolic",
            tips=(_("Drink a glass of water."), _("Refill your bottle.")),
            builtin=True,
            position=4,
        ),
        Activity(
            id=BREATHING,
            name=_("Breathing reset"),
            icon="movebreak-breathing-symbolic",
            tips=(
                _(
                    "Cyclic sighing: two inhales through the nose, one long exhale "
                    "through the mouth. Repeat for five minutes."
                ),
            ),
            covers=frozenset({EYES}),
            builtin=True,
            position=5,
        ),
        Activity(
            id=SIT_STAND,
            name=_("Sit–stand switch"),
            icon="movebreak-stand-symbolic",
            tips=(
                _("Switch between sitting and standing, then take a few steps."),
                _("Change position: if you are standing, sit; if you are sitting, stand."),
            ),
            builtin=True,
            position=6,
        ),
    ]


def builtin_profiles() -> list[Profile]:
    return [
        Profile(id=RECOMMENDED, name=_("Recommended"), builtin=True, position=0),
        Profile(id=LIGHT, name=_("Light"), builtin=True, position=1),
        Profile(id=EYES_FIRST, name=_("Eyes first"), builtin=True, position=2),
    ]


_RECOMMENDED: dict[str, ActivitySettings] = {
    WALK: ActivitySettings(True, _minutes(30), 180, Importance.NORMAL, _minutes(5)),
    EYES: ActivitySettings(True, _minutes(20), 20, Importance.GENTLE, _minutes(1)),
    STRETCH: ActivitySettings(True, _minutes(60), 90, Importance.NORMAL),
    STRENGTH: ActivitySettings(False, _minutes(120), 60, Importance.NORMAL),
    WATER: ActivitySettings(False, _minutes(90), 30, Importance.GENTLE),
    BREATHING: ActivitySettings(False, _minutes(240), 300, Importance.NORMAL),
    SIT_STAND: ActivitySettings(False, _minutes(45), 30, Importance.GENTLE),
}

_PROFILE_OVERRIDES: dict[str, dict[str, ActivitySettings]] = {
    RECOMMENDED: {},
    # About 1.3 prompts an hour: a walk every 45 minutes, with a stretch on
    # every second walk, and no eye breaks.
    LIGHT: {
        WALK: replace(_RECOMMENDED[WALK], interval_s=_minutes(45), duration_s=120),
        EYES: replace(_RECOMMENDED[EYES], enabled=False),
        STRETCH: replace(_RECOMMENDED[STRETCH], interval_s=_minutes(90), duration_s=60),
    },
    # For tired or dry eyes: eye breaks every 20 minutes, walks every hour.
    EYES_FIRST: {
        WALK: replace(_RECOMMENDED[WALK], interval_s=_minutes(60)),
    },
}


def preset_settings(profile_id: str, activity_id: str) -> ActivitySettings | None:
    """Default settings of a built-in activity in a built-in profile, if both exist."""
    overrides = _PROFILE_OVERRIDES.get(profile_id)
    base = _RECOMMENDED.get(activity_id)
    if overrides is None or base is None:
        return None
    return overrides.get(activity_id, base)


def fallback_settings(activity_id: str, *, enabled: bool) -> ActivitySettings:
    """Settings for a pairing no preset covers (custom profile or custom activity)."""
    base = _RECOMMENDED.get(activity_id) or new_activity_settings()
    return replace(base, enabled=enabled)


def new_activity_settings(*, enabled: bool = True) -> ActivitySettings:
    """Starting point for a custom activity: once an hour, one minute."""
    return ActivitySettings(enabled, _minutes(60), 60, Importance.NORMAL)
