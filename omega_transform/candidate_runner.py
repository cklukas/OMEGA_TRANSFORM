# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""Run a Python candidate in its own process and serve calls to its functions.

Usage: python -I candidate_runner.py CANDIDATE.py REQUEST_FIFO RESPONSE_FIFO

The engine starts this process next to the evaluator, in a stricter sandbox:
it cannot read the problem directory, so a candidate cannot read the
evaluator, its hidden data or its reference solutions. The evaluator calls
the candidate's functions through two named pipes (``omega_transform.harness``).

Protocol: one JSON object per line. A request is
``{"fn": name, "args": [...], "kwargs": {...}}``; the answer is
``{"ok": true, "result": ..., "cpu": seconds}`` or
``{"ok": false, "error": "Type: message", "traceback": "..."}``.
Bytes travel as ``{"__bytes__": base64}``; tuples become lists. JSON keeps the
exchange safe: nothing the candidate returns is executed by the evaluator.

Standard library only, and runnable by path: it must not import the package.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import time
import traceback


def encode(obj):
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"__bytes__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [encode(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    try:  # numpy scalars and arrays, if a candidate uses numpy
        return encode(obj.tolist())
    except AttributeError:
        raise TypeError(f"cannot return a value of type {type(obj).__name__}") from None


def decode(obj):
    if isinstance(obj, dict):
        if set(obj) == {"__bytes__"}:
            return base64.b64decode(obj["__bytes__"])
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    return obj


def _run_program(argv: list[str], timeout) -> dict:
    """Run a compiled candidate; its CPU time comes from this process's children."""
    import resource
    import subprocess
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
        result = {"code": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                  "timed_out": False}
    except subprocess.TimeoutExpired as e:
        result = {"code": None, "stdout": (e.stdout or b"").decode(errors="replace")
                  if isinstance(e.stdout, bytes) else (e.stdout or ""),
                  "stderr": "", "timed_out": True}
    except OSError as e:
        return {"ok": False, "error": f"cannot run {argv[0]}: {e}", "traceback": ""}
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    return {"ok": True, "result": result, "cpu": cpu}


def _record_usage() -> None:
    """CPU time and peak memory of this process so far, for the engine's job accounting
    (the runner is killed, not ended, so it records after every call)."""
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF)
        c = resource.getrusage(resource.RUSAGE_CHILDREN)  # programs it ran
        scale = 1 if sys.platform == "darwin" else 1024
        peak = max(r.ru_maxrss, c.ru_maxrss) * scale
        with open(".usage-candidate.json", "w") as f:
            json.dump({"cpu_seconds": round(r.ru_utime + r.ru_stime + c.ru_utime + c.ru_stime, 4),
                       "peak_mb": round(peak / 2**20, 1)}, f)
    except (OSError, ImportError):
        pass


def _tail(tb: str, lines: int = 8) -> str:
    return "\n".join(tb.strip().splitlines()[-lines:])


def main(argv: list[str]) -> int:
    path, req_path, resp_path = argv[:3]
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # whatever the candidate prints must not disturb anything
    module = None
    load_error = None
    load_cpu = 0.0  # loading time counts: work at a module's top level is still work
    with open(req_path, "r", encoding="utf-8") as req, \
            open(resp_path, "w", encoding="utf-8") as resp:
        for line in req:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "run" in msg:  # execute a compiled program inside this sandbox
                out = _run_program(msg["run"], msg.get("timeout"))
                _record_usage()
                resp.write(json.dumps(out) + "\n")
                resp.flush()
                continue
            if module is None and load_error is None:
                t_load = time.process_time()
                try:
                    spec = importlib.util.spec_from_file_location("candidate", path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                except BaseException as e:  # noqa: BLE001 - reported to the evaluator
                    load_error = (f"{type(e).__name__}: {e}", _tail(traceback.format_exc()))
                load_cpu = time.process_time() - t_load
            if load_error is not None:
                out = {"ok": False, "error": load_error[0], "traceback": load_error[1]}
            else:
                fn = getattr(module, msg.get("fn", ""), None)
                if not callable(fn):
                    out = {"ok": False, "error": f"the program defines no function "
                                                 f"{msg.get('fn')!r}", "traceback": ""}
                else:
                    t0 = time.process_time() - load_cpu  # the first call carries the load
                    load_cpu = 0.0
                    try:
                        result = fn(*decode(msg.get("args", [])), **decode(msg.get("kwargs", {})))
                        cpu = time.process_time() - t0
                        out = {"ok": True, "result": encode(result), "cpu": cpu}
                    except BaseException as e:  # noqa: BLE001 - reported to the evaluator
                        out = {"ok": False, "error": f"{type(e).__name__}: {e}",
                               "traceback": _tail(traceback.format_exc()),
                               "cpu": time.process_time() - t0}
            _record_usage()
            resp.write(json.dumps(out) + "\n")
            resp.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
