# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""Evaluator-side access to a Python candidate's functions.

An evaluator calls the candidate through this class instead of importing it::

    from omega_transform.harness import Candidate, CandidateError

    cand = Candidate(sys.argv[1])
    try:
        tour = cand.call("solve", points, 2.0)
    except CandidateError as e:
        fail(str(e))
    cpu = cand.last_cpu  # CPU seconds of that call, measured in the candidate's process

Under the engine the candidate runs in a separate, stricter sandbox that cannot
read the problem directory (``candidate_runner.py``), so it cannot read the
evaluator or its hidden data. Run by hand (no runner), the candidate is
imported into the evaluator's process instead, with the same interface.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import traceback

from .candidate_runner import cpu_clock, decode, encode


class CandidateError(Exception):
    """The candidate failed: it could not be loaded, a call raised, or it returned
    something that cannot be passed back. ``str()`` is concise feedback."""

    def __init__(self, message: str, tb: str = ""):
        super().__init__(message + (f"\n{tb}" if tb else ""))
        self.message, self.traceback = message, tb


class RunResult:
    """Outcome of a compiled candidate program run through ``Candidate.run``."""

    def __init__(self, code, stdout: str, stderr: str, timed_out: bool):
        self.code, self.stdout, self.stderr, self.timed_out = code, stdout, stderr, timed_out


_channel: tuple | None = None  # the pipe pair to the runner, shared by every Candidate


def _connect():
    """Open the pipes to the candidate runner once per evaluator process. The runner
    ends when the request pipe closes, so all Candidate objects share one pair."""
    global _channel
    if _channel is None:
        runner = os.environ.get("OMEGA_RUNNER")
        if not runner:
            return None
        req, resp = runner.split(os.pathsep)
        if sys.platform == "win32":  # named pipes the runner creates, maybe not yet
            from .winjob import open_pipe
            _channel = (open_pipe(req, "w"), open_pipe(resp, "r"))
        else:
            _channel = (open(req, "w", encoding="utf-8"), open(resp, "r", encoding="utf-8"))
    return _channel


class Candidate:
    def __init__(self, path: str = ""):
        self.path = path
        self.last_cpu = 0.0
        self._module = None
        channel = _connect()
        self._req, self._resp = channel if channel else (None, None)

    @property
    def isolated(self) -> bool:
        """True when the candidate runs in its own sandboxed process."""
        return self._req is not None

    def run(self, argv: list[str], timeout: float | None = None) -> RunResult:
        """Run a compiled program (the candidate's binary) in the candidate's sandbox,
        where it cannot read the problem directory. ``last_cpu`` is its CPU time."""
        if self._req is not None:
            self._req.write(json.dumps({"run": [str(a) for a in argv],
                                        "timeout": timeout}) + "\n")
            self._req.flush()
            line = self._resp.readline()
            if not line:
                raise CandidateError("the program's process ended unexpectedly")
            out = json.loads(line)
            self.last_cpu = float(out.get("cpu") or 0.0)
            if not out.get("ok"):
                raise CandidateError(out.get("error", "unknown error"))
            r = out["result"]
            return RunResult(r["code"], r["stdout"], r["stderr"], r["timed_out"])
        import subprocess
        if sys.platform == "win32":
            from .winjob import run_measured
            p, self.last_cpu, _, _ = run_measured([str(a) for a in argv], timeout=timeout,
                                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                  text=True, encoding="utf-8", errors="replace")
            return RunResult(p.returncode, p.stdout, p.stderr, False) if p is not None else \
                RunResult(None, "", "", True)
        import resource
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            p = subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                               errors="replace", timeout=timeout)
            res = RunResult(p.returncode, p.stdout, p.stderr, False)
        except subprocess.TimeoutExpired:
            res = RunResult(None, "", "", True)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.last_cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
        return res

    def call(self, fn: str, *args, **kwargs):
        if self._req is not None:
            return self._remote(fn, args, kwargs)
        return self._local(fn, args, kwargs)

    def _remote(self, fn, args, kwargs):
        self._req.write(json.dumps({"fn": fn, "args": encode(list(args)),
                                    "kwargs": encode(kwargs)}) + "\n")
        self._req.flush()
        line = self._resp.readline()
        if not line:
            raise CandidateError("the program's process ended unexpectedly (crash, or "
                                 "killed for exceeding a limit)")
        out = json.loads(line)
        self.last_cpu = float(out.get("cpu") or 0.0)
        if not out.get("ok"):
            raise CandidateError(out.get("error", "unknown error"), out.get("traceback", ""))
        return decode(out.get("result"))

    def _local(self, fn, args, kwargs):
        load_cpu = 0.0
        if self._module is None:
            t_load = cpu_clock()
            try:
                spec = importlib.util.spec_from_file_location("candidate", self.path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except BaseException as e:  # noqa: BLE001 - becomes feedback
                raise CandidateError(f"{type(e).__name__}: {e}",
                                     _tail(traceback.format_exc())) from None
            self._module = module
            load_cpu = cpu_clock() - t_load  # work at the top level counts too
        f = getattr(self._module, fn, None)
        if not callable(f):
            raise CandidateError(f"the program defines no function {fn!r}")
        stdout, sys.stdout = sys.stdout, sys.stderr  # keep the evaluator's JSON line clean
        t0 = cpu_clock() - load_cpu
        try:
            # Round-trip through the wire format so results look the same as isolated.
            result = decode(encode(f(*args, **kwargs)))
        except BaseException as e:  # noqa: BLE001 - becomes feedback
            raise CandidateError(f"{type(e).__name__}: {e}",
                                 _tail(traceback.format_exc())) from None
        finally:
            self.last_cpu = cpu_clock() - t0
            sys.stdout = stdout
        return result

    def close(self) -> None:
        """Nothing to do: the shared pipes close when the evaluator exits."""


def _tail(tb: str, lines: int = 8) -> str:
    return "\n".join(tb.strip().splitlines()[-lines:])
