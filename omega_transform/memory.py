# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Memory guard: never load a model that would push the machine into swap.

The check runs before a model is loaded, against the memory that is free
right now (other applications included), and keeps a reserve for the system
and for the evaluation jobs that run next to the model.
"""
from __future__ import annotations

import os
import re
import subprocess

# Loaded size ~ weights x 1.06 plus context cache and buffers (measured: 17.4 GB
# of weights -> 18.4 GB loaded at a 32k context); the margin errs on the safe side.
LOAD_FACTOR = 1.1
LOAD_OVERHEAD_GB = 1.0
DEFAULT_RESERVE_GB = 6.0
# Added to a measured loaded size: the context cache grows a little with use.
MEASURED_MARGIN_GB = 0.5


def guard_disabled() -> bool:
    """OMEGA_NO_MEMORY_GUARD=1 loads models whatever the free memory, leaving it to the
    operating system to swap: for when progress matters more than a responsive machine."""
    return os.environ.get("OMEGA_NO_MEMORY_GUARD", "") not in ("", "0")


def estimate_model_gb(size_bytes: int) -> float:
    return size_bytes / 1e9 * LOAD_FACTOR + LOAD_OVERHEAD_GB


def available_gb() -> float | None:
    """Memory that can be used without swapping: free plus reclaimable pages."""
    try:
        if os.uname().sysname == "Darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+)", out).group(1))
            pages = 0
            for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
                m = re.search(rf"{key}:\s+(\d+)", out)
                if m:
                    pages += int(m.group(1))
            return pages * page / 1e9
        with open("/proc/meminfo") as f:
            m = re.search(r"MemAvailable:\s+(\d+) kB", f.read())
            return int(m.group(1)) * 1024 / 1e9 if m else None
    except (OSError, AttributeError, ValueError, subprocess.TimeoutExpired):
        return None


def fits(size_bytes: int, reserve_gb: float = DEFAULT_RESERVE_GB,
         already_loaded_gb: float = 0.0, measured_gb: float | None = None) -> tuple[bool, str]:
    """Whether a model of this size can be loaded now; and a reason when not.

    ``measured_gb``: the model's loaded size as measured before (``omega models``);
    used instead of the conservative estimate. ``already_loaded_gb``: memory of a
    model that will be unloaded first and so counts as available.
    """
    need = measured_gb + MEASURED_MARGIN_GB if measured_gb else estimate_model_gb(size_bytes)
    if guard_disabled():
        return True, f"memory guard off (needs about {need:.1f} GB)"
    avail = available_gb()
    if avail is None:
        return True, "free memory unknown"
    avail += already_loaded_gb
    if need + reserve_gb > avail:
        return False, (f"needs about {need:.1f} GB plus a {reserve_gb:g} GB reserve, "
                       f"but only {avail:.1f} GB are free")
    return True, f"needs about {need:.1f} GB of {avail:.1f} GB free"


def wait_until_fits(size_bytes: int, reserve_gb: float = DEFAULT_RESERVE_GB,
                    measured_gb: float | None = None, minutes: float = 0.0,
                    log=None, poll_seconds: float = 30.0) -> tuple[bool, str]:
    """Like ``fits``, but wait up to ``minutes`` for memory to become free (other
    applications come and go on a shared machine) instead of failing at once."""
    import time
    ok, why = fits(size_bytes, reserve_gb, measured_gb=measured_gb)
    if ok or minutes <= 0:
        return ok, why
    if log:
        log(f"  waiting up to {minutes:g} min for memory: {why}")
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        ok, why = fits(size_bytes, reserve_gb, measured_gb=measured_gb)
        if ok:
            return ok, why
    return False, why + f" (after waiting {minutes:g} min)"
