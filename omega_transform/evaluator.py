# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Candidate evaluation as isolated compute jobs.

Each candidate gets its own job directory. For C/C++ the build step runs
first; then the evaluator command runs once per cascade stage. Each step is a
separate process in its own process group (on Windows: its own Job Object,
``winjob.py``), so a timeout kills the whole tree.

Evaluator protocol: the command prints one JSON object as its last stdout
line, for example::

    {"score": 2.51, "metrics": {"sum_radii": 2.51}, "features": {"n_iter": 40},
     "valid": true, "feedback": "free text for the LLM"}

``score`` is maximized. ``valid: false`` (or a missing score) marks the
candidate invalid, and its ``feedback`` is shown to the LLM. An optional
``solution`` holds what the candidate produced in a drawable form (circles,
points, a tour, ...); it is stored with the program and drawn on the example
pages (``figures.py``), but never shown to the LLM.

Stages marked ``exclusive`` (timing benchmarks) take a write lock, so they run
with no other evaluation in parallel.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ProblemConfig


_OPERATOR_NEW_DELETE = ("_Znw", "_Zna", "_Zdl", "_Zda")


def foreign_symbols(obj: Path, exports: list[str]) -> list[str]:
    """Global symbols an object defines beyond its allowed interface.

    C-level names (unmangled) outside ``exports`` could replace a function the
    harness or the C library provides at link time (``clock_gettime``, ``memcmp``),
    and replacing the global ``operator new``/``delete`` would change the harness's
    allocations too: both are refused. Other mangled C++ symbols, such as template
    instantiations of the standard library, cannot collide harmfully and pass.
    """
    nm = shutil.which("nm") or shutil.which("llvm-nm") or "nm"
    out = subprocess.run([nm, "-g", str(obj)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60).stdout
    if sys.platform == "win32":
        return filter_symbols_coff(out, exports)
    return filter_symbols(out, exports, darwin=sys.platform == "darwin")


# Compiler-generated helpers a C/C++ source cannot otherwise define.
_COMPILER_SYMBOLS = {"__clang_call_terminate"}


def filter_symbols(nm_output: str, exports: list[str], darwin: bool) -> list[str]:
    """The disallowed global definitions in ``nm -g`` output (see foreign_symbols)."""
    allowed = set(exports)
    bad = []
    for line in nm_output.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-2] in ("U", "u", "w", "v"):
            continue  # undefined (or a weak reference): used, not defined
        name = parts[-1]
        if darwin and name.startswith("_"):
            name = name[1:]  # Mach-O prefixes C symbols with an underscore
        if name in allowed or name in _COMPILER_SYMBOLS:
            continue
        if "." in name:
            continue  # compiler-generated (e.g. DW.ref.__gxx_personality_v0): no C name
        if name.startswith("_Z") and not name.startswith(_OPERATOR_NEW_DELETE):
            continue
        bad.append(name)
    return sorted(set(bad))


# MSVC-mangled global operator new/delete (scalar and array).
_MSVC_NEW_DELETE = ("??2@", "??3@", "??_U@", "??_V@")
# What the Windows C runtime's and the MSVC STL's headers define, so that every object
# using them carries a definition of its own: the printf/scanf family and their helpers,
# and names reserved for the implementation (underscore and a capital letter, such as
# the STL's _Avx2WmemEnabledWeakValue).
_UCRT_INLINE = re.compile(r"_*v?[fs]?n?w?(printf|scanf)(_s|_p)?(_l)?|__local_stdio_\w+_options"
                          r"|_[A-Z]\w*")
_C_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def filter_symbols_coff(nm_output: str, exports: list[str]) -> list[str]:
    """``filter_symbols`` for COFF objects (Windows, MSVC ABI): C++ names are mangled with
    a leading ``?``, and names that are no C identifier (``__real@...`` constants) are
    compiler-generated."""
    allowed = set(exports)
    bad = []
    for line in nm_output.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-2] in ("U", "u", "w", "v"):
            continue
        name = parts[-1]
        if name.startswith("?"):
            if name.startswith(_MSVC_NEW_DELETE):
                bad.append(name)
            continue
        if name in allowed or name in _COMPILER_SYMBOLS or not _C_IDENTIFIER.fullmatch(name) \
                or _UCRT_INLINE.fullmatch(name):
            continue
        bad.append(name)
    return sorted(set(bad))


WINDOWS = sys.platform == "win32"


class _Runner:
    """A started candidate runner and the pipe pair the evaluator talks through.
    ``job``: on Windows, the Job Object the runner and the evaluator share."""

    def __init__(self, proc: subprocess.Popen, channel: str, job=None):
        self.proc, self.channel, self.job = proc, channel, job

    def stop(self) -> None:
        if self.job is not None:
            self.job.terminate()
            self.job.close()
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


class RWLock:
    """Many shared holders or one exclusive holder. Waiting writers block new readers."""

    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    def acquire_shared(self):
        with self._cond:
            while self._writer or self._waiting_writers:
                self._cond.wait()
            self._readers += 1

    def release_shared(self):
        with self._cond:
            self._readers -= 1
            self._cond.notify_all()

    def acquire_exclusive(self):
        with self._cond:
            self._waiting_writers += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._waiting_writers -= 1
            self._writer = True

    def release_exclusive(self):
        with self._cond:
            self._writer = False
            self._cond.notify_all()


@dataclass
class EvalResult:
    status: str  # ok | compile_error | runtime_error | timeout | invalid | filtered
    score: float | None = None
    metrics: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    feedback: str = ""
    stage: str = ""
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)  # per build/stage: cpu_seconds, peak_mb, ...
    solution: dict | None = None  # the evaluator's drawable "solution", if it gives one

    def usage_metrics(self) -> dict:
        """Flat metrics such as ``bench_cpu_s`` and ``bench_peak_mb``, one pair per step."""
        out = {}
        for step, u in self.usage.items():
            out[f"{step}_cpu_s"] = u.get("cpu_seconds")
            out[f"{step}_peak_mb"] = u.get("peak_mb")
        return out


@dataclass
class _Run:
    code: int | None  # None = killed (see ``killed``)
    out: str
    err: str
    seconds: float
    killed: str = ""  # "timeout" | "memory"
    peak_mb: float = 0.0


def group_rss_mb(pgid: int) -> float:
    """Resident memory of a whole process group (the job and all its children)."""
    try:
        out = subprocess.run(["ps", "-axo", "pgid=,rss="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    kb = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid):
            kb += int(parts[1])
    return kb / 1024


def _run(cmd: list[str], cwd: Path, timeout: float, env: dict,
         max_memory_mb: float = 0.0, runner: _Runner | None = None) -> _Run:
    """Run ``cmd`` in its own process group, or join the candidate runner's group; kill
    the whole group on timeout or memory excess. On Windows a Job Object (the runner's,
    or one of its own) takes the place of the group."""
    t0 = time.monotonic()
    job = None
    if WINDOWS:
        from .winjob import Job
        job = runner.job if runner is not None else Job()
        placement = {}
    elif runner is not None:
        placement = {"process_group": runner.proc.pid}
    else:
        placement = {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", **placement)
    if job is not None:
        job.assign(proc)
    pgid = runner.proc.pid if runner is not None else proc.pid
    result: dict[str, str] = {}

    def collect():
        result["out"], result["err"] = proc.communicate()

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    killed, peak, next_check = "", 0.0, t0 + 0.3
    while reader.is_alive():
        reader.join(0.1)
        now = time.monotonic()
        if not reader.is_alive():
            break
        if now - t0 > timeout:
            killed = "timeout"
        elif max_memory_mb and now >= next_check:
            next_check = now + 0.5
            peak = max(peak, job.peak_mb() if job is not None else group_rss_mb(pgid))
            if peak > max_memory_mb:
                killed = "memory"
        if killed:
            if job is not None:
                job.terminate()
            else:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            reader.join()
            break
    if job is not None and runner is None:
        job.close()
    return _Run(None if killed else proc.returncode, result.get("out", ""),
                result.get("err", ""), time.monotonic() - t0, killed, peak)


RUSAGE_WRAPPER = str(Path(__file__).with_name("rusage_wrap.py"))


def read_usage(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


SANDBOX_PROFILE = """(version 1)
(allow default)
(deny network*)
(allow network* (remote unix-socket))
(deny file-write*)
(allow file-write* (subpath "{workdir}") (subpath "/dev"))
"""

# The candidate runner's sandbox: as above, and it cannot read the problem
# directory (evaluator, hidden data, reference solutions), except its own job
# directory, which may lie inside it. In SBPL the last matching rule wins.
CANDIDATE_PROFILE = SANDBOX_PROFILE + """(deny file-read* (subpath "{problem_dir}"))
(allow file-read* (subpath "{workdir}"))
"""
CANDIDATE_RUNNER = str(Path(__file__).with_name("candidate_runner.py"))
PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)


_backend: list = []  # cached result of sandbox_backend()
_problem: list = []  # why no sandbox is available, if so
_net_by_environment: list = []  # [True] when bwrap omits its network namespace (see below)


def _only_loopback() -> bool:
    """True when this process sees no network interface besides loopback, as in a CI
    step that already runs in an empty network namespace of its own."""
    # /proc/net/dev lists the interfaces of this process's own network namespace;
    # /sys/class/net would show the namespace /sys was mounted in, often the host's.
    try:
        with open("/proc/net/dev") as f:
            names = {line.split(":", 1)[0].strip() for line in f if ":" in line}
    except OSError:
        return False
    return names <= {"lo"}


def _bwrap_probe(net: bool) -> subprocess.CompletedProcess:
    return subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev"]
                          + (["--unshare-net"] if net else []) + ["--die-with-parent", "true"],
                          capture_output=True, text=True, timeout=20)


def sandbox_backend() -> str | None:
    """The sandbox this machine offers: "sandbox-exec" (macOS), "bwrap" (Linux
    bubblewrap, if it can create its namespaces here) or None."""
    if not _backend:
        found = None
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            found = "sandbox-exec"
        elif sys.platform.startswith("linux") and shutil.which("bwrap"):
            try:  # inside containers or CI jobs, namespaces may be unavailable
                p = _bwrap_probe(net=True)
                ok = p.returncode == 0
                if not ok and _only_loopback() and _bwrap_probe(net=False).returncode == 0:
                    # A network namespace of its own failed (e.g. netlink sockets are
                    # forbidden, as in a hardened CI runner), but this environment has
                    # no network beyond loopback anyway: confine the filesystem only.
                    ok = True
                    _net_by_environment.append(True)
                if not ok:
                    _problem.append(f"bwrap cannot create its sandbox here: "
                                    f"{(p.stderr or p.stdout).strip()[:300]}")
            except (OSError, subprocess.TimeoutExpired) as e:
                ok = False
                _problem.append(f"bwrap failed: {e}")
            found = "bwrap" if ok else None
        elif sys.platform.startswith("linux"):
            _problem.append("bubblewrap (bwrap) is not installed")
        elif sys.platform != "darwin":
            _problem.append(f"no sandbox implementation for {sys.platform}")
        _backend.append(found)
    return _backend[0]


def sandbox_available() -> bool:
    return sandbox_backend() is not None


def sandbox_status() -> str:
    """One line for humans: which sandbox jobs run in, or why they run unconfined."""
    backend = sandbox_backend()
    if backend and _net_by_environment:
        return (f"sandbox: {backend} (filesystem only; the environment itself provides "
                "no network beyond loopback)")
    if backend:
        return f"sandbox: {backend}"
    return "sandbox: none (" + (_problem[0] if _problem else "unavailable") + \
        "); jobs run unconfined"


def sandbox_prefix(workdir: Path, hide: Path | None = None) -> list[str]:
    """Command prefix that confines a process: no network, writable only in
    ``workdir`` (and /dev), and with ``hide`` unreadable (the problem directory,
    for candidate code). Empty when no sandbox is available."""
    backend = sandbox_backend()
    if backend == "sandbox-exec":
        profile = SANDBOX_PROFILE.format(workdir=workdir) if hide is None else \
            CANDIDATE_PROFILE.format(workdir=workdir, problem_dir=hide)
        return ["sandbox-exec", "-p", profile]
    if backend == "bwrap":
        # Everything read-only; the hidden directory is covered by an empty tmpfs,
        # and the job directory (which may lie inside it) is mounted writable on top.
        args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--die-with-parent"]
        if not _net_by_environment:
            args.append("--unshare-net")
        if hide is not None:
            args += ["--tmpfs", str(hide)]
        return args + ["--bind", str(workdir), str(workdir)]  # cwd is kept
    return []


def _tail(text: str, lines: int = 25, chars: int = 2500) -> str:
    t = "\n".join(text.strip().splitlines()[-lines:])
    return t[-chars:]


def compiler_digest(stderr: str, max_lines: int = 25) -> str:
    """Keep the error lines of compiler output (and a little context), drop the noise."""
    lines = stderr.splitlines()
    keep = [i for i, ln in enumerate(lines) if re.search(r"\berror\b", ln, re.I)]
    if not keep:
        return _tail(stderr, max_lines)
    picked: list[str] = []
    for i in keep:
        for ln in lines[i:i + 3]:
            if ln not in picked[-3:]:
                picked.append(ln)
        if len(picked) >= max_lines:
            break
    return "\n".join(picked[:max_lines])


def parse_result_json(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(v, dict):
                return v
    return None


class Evaluator:
    def __init__(self, cfg: ProblemConfig, jobs_dir: Path, quiet=None):
        """``quiet``: optional lock held during exclusive stages besides the job gate;
        the engine passes its LLM slot so benchmarks never overlap model inference."""
        self.quiet = quiet
        self.cfg = cfg
        self.ev = cfg.evaluator
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.gate = RWLock()
        self.sandboxed = self.ev.sandbox if self.ev.sandbox is not None else sandbox_available()
        # Python candidates run in their own process (candidate_runner.py), stricter
        # sandboxed than the evaluator, when the evaluator uses omega_transform.harness.
        self.isolate = self.ev.isolate if self.ev.isolate is not None else \
            cfg.lang_tag in ("python", "c", "cpp")
        if self.sandboxed and not sandbox_available():
            raise RuntimeError("evaluator.sandbox = true, but no sandbox is available "
                               "(macOS sandbox-exec, or Linux bwrap with user namespaces)")

    def _candidate_prefix(self, wd: Path) -> list[str]:
        """Command prefix for code that must not read the problem directory."""
        prefix = list(self.ev.launcher)
        if self.sandboxed:
            prefix += sandbox_prefix(wd, hide=self.cfg.problem_dir.resolve())
        return prefix

    def _build_step(self, step, template, prefix, m, wd, env, mem, usage, measured, clean):
        """Run one build command; None on success, else the compile_error result."""
        self.gate.acquire_shared()
        try:
            cmd, ufile = measured(step, self._subst(template, m))
            if prefix is not None:  # replace the evaluator sandbox with the stricter one
                cmd = prefix + cmd[len(self._evaluator_prefix(wd)):]
            r = _run(cmd, wd, self.ev.build_timeout, env, mem)
            usage[step] = read_usage(ufile)
        finally:
            self.gate.release_shared()
        if r.code is None:
            return EvalResult("compile_error", feedback=f"{step} killed ({r.killed})", stage=step)
        if r.code != 0:
            return EvalResult("compile_error", stage=step,
                              feedback=clean(compiler_digest(r.err + r.out)))
        return None

    def _evaluator_prefix(self, wd: Path) -> list[str]:
        prefix = list(self.ev.launcher)
        if self.sandboxed:
            prefix += sandbox_prefix(wd)
        return prefix

    def _start_runner(self, cand: Path, wd: Path, env: dict) -> _Runner:
        """Start the candidate's own process before the evaluator, which joins its group."""
        if WINDOWS:  # named pipes, which the runner creates as their server
            from .winjob import pipe_name
            req, resp = pipe_name("req"), pipe_name("resp")
        else:
            req, resp = wd / ".omega-req", wd / ".omega-resp"
            for fifo in (req, resp):
                if fifo.exists():
                    fifo.unlink()
                os.mkfifo(fifo)
        cmd = self._candidate_prefix(wd) + [self.ev.python, "-I", CANDIDATE_RUNNER, str(cand),
                                            str(req), str(resp)]
        renv = {k: v for k, v in env.items() if k != "OMEGA_RUNNER"}
        if WINDOWS:
            from .winjob import Job
            job = Job()
            proc = subprocess.Popen(cmd, cwd=wd, env=renv, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            job.assign(proc)
            return _Runner(proc, f"{req}{os.pathsep}{resp}", job)
        # A new process group in this session (not a new session): the evaluator can
        # only join a group of its own session.
        proc = subprocess.Popen(cmd, cwd=wd, env=renv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                process_group=0)
        return _Runner(proc, f"{req}{os.pathsep}{resp}")

    def _subst(self, args: list[str], m: dict[str, str]) -> list[str]:
        return [a.format(**m) for a in args]

    def evaluate(self, code: str, job_id: str) -> EvalResult:
        t0 = time.monotonic()
        res = self._evaluate(code, job_id)
        res.seconds = time.monotonic() - t0
        return res

    def _evaluate(self, code: str, job_id: str) -> EvalResult:
        wd = (self.jobs_dir / job_id).resolve()
        wd.mkdir(parents=True, exist_ok=True)
        cand = wd / self.cfg.candidate_name
        cand.write_text(code, encoding="utf-8", newline="\n")
        binary = wd / "candidate.bin"
        m = {"python": self.ev.python, "candidate": str(cand), "binary": str(binary),
             "object": str(wd / "candidate.o"),
             "workdir": str(wd), "problem_dir": str(self.cfg.problem_dir), "stage": ""}
        env = dict(os.environ, OMEGA_CANDIDATE=str(cand), OMEGA_BINARY=str(binary),
                   OMEGA_WORKDIR=str(wd), OMEGA_PROBLEM_DIR=str(self.cfg.problem_dir),
                   PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(wd), PYTHONUTF8="1",
                   PYTHONPATH=os.pathsep.join(
                       [PACKAGE_PARENT] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
        if WINDOWS:  # where Windows programs put temporary files; the cycle-clock rate
            from .winjob import tsc_hz
            env.update(TMP=str(wd), TEMP=str(wd), OMEGA_TSC_HZ=repr(tsc_hz()))
        prefix = list(self.ev.launcher)
        if self.sandboxed:
            prefix += sandbox_prefix(wd)
        mem = self.ev.max_memory_mb if not self.ev.launcher else 0.0
        usage: dict[str, dict] = {}

        def measured(step: str, cmd: list[str]) -> tuple[list[str], Path]:
            """Wrap ``cmd`` so the CPU time and peak memory of its process tree are recorded."""
            out = wd / f".usage-{step}.json"
            return prefix + [sys.executable, "-I", RUSAGE_WRAPPER, str(out)] + cmd, out

        def clean(text: str) -> str:  # job paths only cost the LLM context
            return text.replace(str(wd) + os.sep, "").replace(str(wd) + "/", "")

        try:
            if self.ev.compile:
                # Separate compilation: the candidate is its own translation unit, so its
                # macros and declarations cannot reach the harness. It is compiled without
                # read access to the problem directory, checked for symbols it must not
                # define, and only then linked with the harness.
                strict = self._candidate_prefix(wd)
                for step, template, pre in (("compile", self.ev.compile, strict),
                                            ("link", self.ev.link, None)):
                    failure = self._build_step(step, template, pre, m, wd, env, mem, usage,
                                               measured, clean)
                    if failure is not None:
                        return failure
                    if step == "compile" and self.ev.exports:
                        bad = foreign_symbols(Path(m["object"]), self.ev.exports)
                        if bad:
                            return EvalResult("compile_error", stage="compile", feedback=(
                                "your code defines global symbols that are not allowed: "
                                f"{', '.join(bad[:8])}. Only {', '.join(self.ev.exports)} may "
                                "be global: make helpers static or put them in an anonymous "
                                "namespace, and do not define functions that the harness or "
                                "the C library provide."))
            elif self.ev.build:
                failure = self._build_step("build", self.ev.build, None, m, wd, env, mem, usage,
                                           measured, clean)
                if failure is not None:
                    return failure
            result = EvalResult("invalid")
            for k, st in enumerate(self.ev.stages):
                m["stage"] = st.name
                env["OMEGA_STAGE"] = st.name
                cmd, ufile = measured(st.name, self._subst(self.ev.command + st.args, m))
                runner = self._start_runner(cand, wd, env) if self.isolate else None
                if runner is not None:
                    env["OMEGA_RUNNER"] = runner.channel
                lock = (self.gate.acquire_exclusive, self.gate.release_exclusive) if st.exclusive \
                    else (self.gate.acquire_shared, self.gate.release_shared)
                lock[0]()
                quiet = self.quiet if (st.exclusive and self.quiet is not None) else None
                if quiet is not None:
                    quiet.acquire()
                try:
                    r = _run(cmd, self.cfg.problem_dir, st.timeout, env, mem, runner=runner)
                    usage[st.name] = read_usage(ufile)
                finally:
                    if runner is not None:
                        runner.stop()
                        # The candidate ran beside the evaluator, not under it: add its share.
                        cu = read_usage(wd / ".usage-candidate.json")
                        if cu and st.name in usage:
                            u = usage[st.name]
                            u["cpu_seconds"] = round(u.get("cpu_seconds", 0) + cu["cpu_seconds"], 4)
                            u["peak_mb"] = max(u.get("peak_mb", 0), cu["peak_mb"])
                    if quiet is not None:
                        quiet.release()
                    lock[1]()
                if r.killed == "memory":
                    return EvalResult("runtime_error", stage=st.name, feedback=(
                        f"killed: memory use exceeded the limit of {mem:.0f} MB"))
                if r.code is None:
                    return EvalResult("timeout", feedback=f"stage {st.name!r} exceeded "
                                      f"{st.timeout:.0f}s", stage=st.name)
                data = parse_result_json(r.out)
                if data is None:
                    status = "runtime_error" if r.code != 0 else "invalid"
                    msg = _tail(r.err) or _tail(r.out) or f"exit code {r.code}, no output"
                    return EvalResult(status, feedback=clean(msg), stage=st.name)
                score = data.get("score")
                fb = clean(str(data.get("feedback", "") or ""))
                if data.get("valid", True) is False or not isinstance(score, (int, float)):
                    if not fb:
                        fb = _tail(r.err) or "evaluator reported the candidate invalid"
                    return EvalResult("invalid", metrics=data.get("metrics") or {},
                                      feedback=fb, stage=st.name)
                sol = data.get("solution")
                result = EvalResult("ok", float(score), data.get("metrics") or {},
                                    data.get("features") or {}, fb, st.name, usage=usage,
                                    solution=sol if isinstance(sol, dict) else None)
                last = k == len(self.ev.stages) - 1
                if not last and st.min_score is not None and score < st.min_score:
                    result.status = "filtered"
                    result.feedback = (fb + "\n" if fb else "") + \
                        f"stopped after stage {st.name!r}: score {score:.6g} < gate {st.min_score:g}"
                    return result
            return result
        finally:
            if not self.ev.keep_jobs:
                shutil.rmtree(wd, ignore_errors=True)
