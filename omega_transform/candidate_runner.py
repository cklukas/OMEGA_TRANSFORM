# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""Run a Python candidate in its own process and serve calls to its functions.

Usage: python -I candidate_runner.py CANDIDATE.py REQUEST_PIPE RESPONSE_PIPE

The engine starts this process next to the evaluator, in a stricter sandbox:
it cannot read the problem directory, so a candidate cannot read the
evaluator, its hidden data or its reference solutions. The evaluator calls
the candidate's functions through two named pipes (``omega_transform.harness``):
FIFOs the engine creates on POSIX, Windows named pipes this runner creates as
their server (``winjob.py``).

Protocol: one JSON object per line. A request is
``{"fn": name, "args": [...], "kwargs": {...}}``; the answer is
``{"ok": true, "result": ..., "cpu": seconds}`` or
``{"ok": false, "error": "Type: message", "traceback": "..."}``.
Bytes travel as ``{"__bytes__": base64}``; tuples become lists. JSON keeps the
exchange safe: nothing the candidate returns is executed by the evaluator.

Standard library only, and runnable by path: it must not import the package
(on Windows it loads ``winjob.py`` from its own directory).
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
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


_winjob = None
_children = [0.0, 0]  # Windows: CPU seconds and peak bytes of the programs run so far


def winjob():
    """The Windows helpers (``winjob.py``), loaded by path from this directory."""
    global _winjob
    if _winjob is None:
        spec = importlib.util.spec_from_file_location(
            "omega_winjob", os.path.join(os.path.dirname(os.path.abspath(__file__)), "winjob.py"))
        _winjob = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_winjob)
    return _winjob


def cpu_clock():
    """CPU seconds of this process: ``time.process_time``, except on Windows, whose
    process times move in 15.6 ms steps (``winjob.process_cpu``)."""
    return winjob().process_cpu() if sys.platform == "win32" else time.process_time()


def _run_program(argv: list[str], timeout) -> dict:
    """Run a compiled candidate; its CPU time comes from this process's children
    (on Windows, from a job of its own)."""
    import subprocess
    if sys.platform == "win32":
        try:
            p, cpu, _, peak = winjob().run_measured(argv, timeout=timeout, stdout=subprocess.PIPE,
                                                    stderr=subprocess.PIPE, text=True,
                                                    encoding="utf-8", errors="replace")
        except OSError as e:
            return {"ok": False, "error": f"cannot run {argv[0]}: {e}", "traceback": ""}
        _children[0] += cpu
        _children[1] = max(_children[1], peak)
        result = {"code": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                  "timed_out": False} if p is not None else \
            {"code": None, "stdout": "", "stderr": "", "timed_out": True}
        return {"ok": True, "result": result, "cpu": cpu}
    import resource
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


def _self_usage() -> tuple[float, int]:
    """CPU seconds and peak bytes of this process and the programs it ran."""
    if sys.platform == "win32":
        peak = 0
        try:
            from ctypes import wintypes
            import ctypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
                    [(n, ctypes.c_size_t) for n in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            c = _Counters()
            c.cb = ctypes.sizeof(c)
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                    wintypes.DWORD]
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                peak = c.PeakWorkingSetSize
        except (OSError, AttributeError):
            pass
        return cpu_clock() + _children[0], max(peak, _children[1])
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF)
    c = resource.getrusage(resource.RUSAGE_CHILDREN)  # programs it ran
    scale = 1 if sys.platform == "darwin" else 1024
    return r.ru_utime + r.ru_stime + c.ru_utime + c.ru_stime, max(r.ru_maxrss, c.ru_maxrss) * scale


def _record_usage() -> None:
    """CPU time and peak memory of this process so far, for the engine's job accounting
    (the runner is killed, not ended, so it records after every call)."""
    try:
        cpu, peak = _self_usage()
        with open(".usage-candidate.json", "w", encoding="utf-8") as f:
            json.dump({"cpu_seconds": round(cpu, 4), "peak_mb": round(peak / 2**20, 1)}, f)
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
    if sys.platform == "win32":  # this runner is the server of both named pipes
        req = winjob().serve_pipe(req_path, inbound=True)
        resp = winjob().serve_pipe(resp_path, inbound=False)
    else:
        req = open(req_path, "r", encoding="utf-8")
        resp = open(resp_path, "w", encoding="utf-8")
    with req, resp:
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
                t_load = cpu_clock()
                try:
                    spec = importlib.util.spec_from_file_location("candidate", path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                except BaseException as e:  # noqa: BLE001 - reported to the evaluator
                    load_error = (f"{type(e).__name__}: {e}", _tail(traceback.format_exc()))
                load_cpu = cpu_clock() - t_load
            if load_error is not None:
                out = {"ok": False, "error": load_error[0], "traceback": load_error[1]}
            else:
                fn = getattr(module, msg.get("fn", ""), None)
                if not callable(fn):
                    out = {"ok": False, "error": f"the program defines no function "
                                                 f"{msg.get('fn')!r}", "traceback": ""}
                else:
                    t0 = cpu_clock() - load_cpu  # the first call carries the load
                    load_cpu = 0.0
                    try:
                        result = fn(*decode(msg.get("args", [])), **decode(msg.get("kwargs", {})))
                        cpu = cpu_clock() - t0
                        out = {"ok": True, "result": encode(result), "cpu": cpu}
                    except BaseException as e:  # noqa: BLE001 - reported to the evaluator
                        out = {"ok": False, "error": f"{type(e).__name__}: {e}",
                               "traceback": _tail(traceback.format_exc()),
                               "cpu": cpu_clock() - t0}
            _record_usage()
            resp.write(json.dumps(out) + "\n")
            resp.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
