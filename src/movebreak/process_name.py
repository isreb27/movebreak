# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Show "Movebreak" instead of "python3" in process lists.

Linux keeps a short process name (``/proc/<pid>/comm``, at most 15 bytes)
that GNOME System Monitor, ``top`` and ``ps -o comm`` display. ``prctl``
changes it. The full command line (``python3 -m movebreak``) stays the same,
so ``ps aux`` still shows that.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

log = logging.getLogger(__name__)

_PR_SET_NAME = 15
_MAX_LENGTH = 15


def set_process_name(name: str) -> bool:
    """Rename the current process. Returns whether it worked (Linux only)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        encoded = name.encode()[:_MAX_LENGTH]
        if libc.prctl(_PR_SET_NAME, ctypes.c_char_p(encoded), 0, 0, 0) != 0:
            log.debug("prctl failed with errno %d", ctypes.get_errno())
            return False
    except (OSError, AttributeError) as error:
        log.debug("Cannot rename the process: %s", error)
        return False
    return True
