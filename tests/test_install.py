# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The user installer writes every file, fills every placeholder and cleans up."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import install
from movebreak import config


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(install, "stop_running_instance", lambda _app_id: None)
    return tmp_path


def test_app_id_is_read_from_config() -> None:
    assert install.read_app_id() == config.APP_ID


def test_install_and_uninstall(home: Path) -> None:
    prefix = home / "prefix"
    assert install.main(["--prefix", str(prefix), "--no-check", "--python", sys.executable]) == 0

    layout = install.Layout(prefix, config.APP_ID, dev=False)
    for path in layout.generated_files():
        assert path.is_file(), path
        assert not re.search(r"@[A-Z_]+@", path.read_text(encoding="utf-8")), path
    assert (layout.lib / "movebreak" / "config.py").is_file()
    assert (layout.lib / "movebreak" / "icons" / "movebreak-walk-symbolic.svg").is_file()
    assert layout.bin.stat().st_mode & 0o111

    desktop = layout.desktop.read_text(encoding="utf-8")
    assert f"Exec={layout.bin}" in desktop
    assert f"Icon={config.APP_ID}" in desktop
    launcher = layout.bin.read_text(encoding="utf-8")
    assert str(layout.lib) in launcher and sys.executable in launcher

    data = home / "share" / "movebreak"
    data.mkdir(parents=True)
    assert install.main(["--prefix", str(prefix), "--uninstall"]) == 0
    for path in layout.generated_files():
        assert not path.exists(), path
    assert not layout.lib.exists()
    assert data.exists()  # Kept without --purge.

    assert install.main(["--prefix", str(prefix), "--uninstall", "--purge"]) == 0
    assert not data.exists()


def test_dev_install_points_at_the_checkout(home: Path) -> None:
    prefix = home / "prefix"
    install.main(["--prefix", str(prefix), "--dev", "--no-check", "--python", sys.executable])
    layout = install.Layout(prefix, config.APP_ID, dev=True)
    assert str(install.ROOT / "src") in layout.bin.read_text(encoding="utf-8")
    install.main(["--prefix", str(prefix), "--uninstall"])
    assert (install.ROOT / "src" / "movebreak").is_dir()  # Never deleted.


def test_flatpak_layout_uses_plain_command(home: Path) -> None:
    prefix = home / "app"
    install.main(["--prefix", str(prefix), "--flatpak", "--python", sys.executable])
    layout = install.Layout(prefix, config.APP_ID, dev=False)
    assert "Exec=movebreak\n" in layout.desktop.read_text(encoding="utf-8")
    assert 'MOVEBREAK_LAUNCHER="/app/bin/movebreak"' in layout.bin.read_text(encoding="utf-8")
