# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Application identity and file locations.

``APP_ID`` is the single source of truth for the application ID. The
installer (``scripts/install.py``) reads it from this file, so changing it
here renames every installed file.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_ID = "io.github.isreb27.Movebreak"
APP_NAME = "Movebreak"
PROJECT_URL = "https://github.com/isreb27/movebreak"
ISSUES_URL = f"{PROJECT_URL}/issues"
DEVELOPER_NAME = "isreb27"

MIN_LIBADWAITA = (1, 5)
"""Oldest libadwaita the UI is written for (Ubuntu 24.04 ships 1.5)."""


def _xdg_dir(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable)
    if value and Path(value).is_absolute():
        return Path(value)
    return Path.home() / fallback


def data_dir() -> Path:
    """Directory for the database. ``MOVEBREAK_DATA_DIR`` overrides it (tests, development)."""
    override = os.environ.get("MOVEBREAK_DATA_DIR")
    if override:
        return Path(override)
    return _xdg_dir("XDG_DATA_HOME", ".local/share") / "movebreak"


def config_dir() -> Path:
    """The XDG config directory (``~/.config`` unless overridden)."""
    return _xdg_dir("XDG_CONFIG_HOME", ".config")


def database_path() -> Path:
    return data_dir() / "movebreak.sqlite3"


def is_flatpak() -> bool:
    """Whether we run inside a Flatpak sandbox."""
    return Path("/.flatpak-info").exists()
