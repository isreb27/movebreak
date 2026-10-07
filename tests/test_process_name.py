# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The process shows up as "Movebreak" instead of "python3"."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_set_process_name_changes_comm() -> None:
    code = (
        "from movebreak.process_name import set_process_name\n"
        "assert set_process_name('Movebreak')\n"
        "print(open('/proc/self/comm').read().strip())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "Movebreak"
