# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Run a command and record the CPU time and peak memory it used.

Usage: python -m omega_transform.rusage_wrap OUT.json COMMAND [ARGS...]

The evaluator starts every build and stage through this wrapper. Being the
only child of its own process, the wrapper can read exact figures for the
command and everything the command waited for (an evaluator script and the
benchmark binary it runs, for example) from RUSAGE_CHILDREN. On Windows the
command runs in a Job Object of its own instead, which accounts the same figures
for the whole tree (``winjob.py``). The exit status of the command is passed on
unchanged.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time


def _windows(cmd: list[str]) -> tuple[int, float, float, int]:
    from pathlib import Path
    import importlib.util
    spec = importlib.util.spec_from_file_location("winjob", Path(__file__).with_name("winjob.py"))
    winjob = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(winjob)
    done, cpu, user, peak = winjob.run_measured(cmd)
    return done.returncode, cpu, user, peak


def _posix(cmd: list[str]) -> tuple[int, float, float, int]:
    import resource
    code = subprocess.call(cmd)
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is in bytes on macOS and in kilobytes on Linux.
    peak = r.ru_maxrss if sys.platform == "darwin" else r.ru_maxrss * 1024
    return code, r.ru_utime + r.ru_stime, r.ru_utime, peak


def main(argv: list[str]) -> int:
    out, cmd = argv[0], argv[1:]
    t0 = time.monotonic()
    try:
        code, cpu, user, peak = (_windows if sys.platform == "win32" else _posix)(cmd)
    except OSError as e:
        print(f"cannot run {cmd[0]}: {e}", file=sys.stderr)
        return 127
    wall = time.monotonic() - t0
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"cpu_seconds": round(cpu, 4),
                       "user_seconds": round(user, 4),
                       "wall_seconds": round(wall, 4),
                       "peak_mb": round(peak / 2**20, 1)}, f)
    except OSError:
        pass
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
