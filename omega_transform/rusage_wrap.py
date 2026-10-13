# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Run a command and record the CPU time and peak memory it used.

Usage: python -m omega_transform.rusage_wrap OUT.json COMMAND [ARGS...]

The evaluator starts every build and stage through this wrapper. Being the
only child of its own process, the wrapper can read exact figures for the
command and everything the command waited for (an evaluator script and the
benchmark binary it runs, for example) from RUSAGE_CHILDREN. The exit status
of the command is passed on unchanged.
"""
from __future__ import annotations

import json
import resource
import subprocess
import sys
import time


def main(argv: list[str]) -> int:
    out, cmd = argv[0], argv[1:]
    t0 = time.monotonic()
    try:
        code = subprocess.call(cmd)
    except OSError as e:
        print(f"cannot run {cmd[0]}: {e}", file=sys.stderr)
        return 127
    wall = time.monotonic() - t0
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is in bytes on macOS and in kilobytes on Linux.
    peak = r.ru_maxrss if sys.platform == "darwin" else r.ru_maxrss * 1024
    try:
        with open(out, "w") as f:
            json.dump({"cpu_seconds": round(r.ru_utime + r.ru_stime, 4),
                       "user_seconds": round(r.ru_utime, 4),
                       "wall_seconds": round(wall, 4),
                       "peak_mb": round(peak / 2**20, 1)}, f)
    except OSError:
        pass
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
