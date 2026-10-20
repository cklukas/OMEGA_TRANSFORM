# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""Windows process control: Job Objects and named pipes, through ctypes.

On macOS and Linux the engine keeps each job's process tree in a process
group, kills it with ``killpg`` and reads its CPU time and peak memory from
``getrusage``. Windows has none of these; a Job Object takes their place. Every
process a member of a job starts joins the job too, so one job holds a whole
tree: it can be terminated at once, and it accounts the CPU time and the peak
memory of all its members, ended ones included. Jobs nest (Windows 8 and
later), so a measured step can run inside the job of the stage it belongs to.

A process is assigned to its job right after it is created. In that instant it
has not yet run any code of its own, so it cannot have started a child that
would escape the job.

The pipes between the evaluator and the candidate runner (FIFOs on POSIX) are
named pipes here: the runner creates them as the server, the evaluator opens
them as ordinary files (``harness.py``).

Windows advances a process's CPU times (GetProcessTimes, and with it
``time.process_time``) only once per scheduler tick, 15.6 ms. ``process_cpu``
is precise instead: QueryProcessCycleTime counts the CPU cycles all threads of
the process ran, in timestamp-counter ticks; with an invariant TSC (every x86
CPU of the last decade) these tick at a constant rate, so cycles divided by the
TSC frequency are CPU seconds.

Standard library only, and loadable by path: ``candidate_runner.py`` runs
without the package on its path and loads this file next to it.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_JobObjectBasicAccountingInformation = 1
_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_ALL_ACCESS = 0x1F0FFF
_PIPE_ACCESS_INBOUND, _PIPE_ACCESS_OUTBOUND = 0x1, 0x2
_PIPE_WAIT = 0x0
_ERROR_PIPE_CONNECTED = 535


class _BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                               "OtherOperationCount", "ReadTransferCount",
                                               "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                           wintypes.DWORD, wintypes.LPDWORD]
_k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                         wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CreateNamedPipeW.restype = wintypes.HANDLE
_k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.LPVOID]
_k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetCurrentThread.restype = wintypes.HANDLE
_k32.QueryProcessCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]
_k32.QueryThreadCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]


def _check(ok) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


class Job:
    """A Job Object; closing the last handle to it (``close``, or the end of this
    process) kills every process still in it."""

    def __init__(self):
        self.handle = _k32.CreateJobObjectW(None, None)
        _check(self.handle)
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(_k32.SetInformationJobObject(self.handle, _JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info)))

    def assign(self, proc: subprocess.Popen) -> None:
        """Put a process just started with Popen into this job."""
        h = _k32.OpenProcess(_PROCESS_ALL_ACCESS, False, proc.pid)
        _check(h)
        try:
            _check(_k32.AssignProcessToJobObject(self.handle, h))
        finally:
            _k32.CloseHandle(h)

    def terminate(self, code: int = 1) -> None:
        if self.handle:
            _k32.TerminateJobObject(self.handle, code)

    def usage(self) -> tuple[float, float, int]:
        """(CPU seconds, user CPU seconds, peak committed bytes) of every process that
        has been in the job, ended ones included."""
        acc, ext = _BasicAccounting(), _ExtendedLimit()
        _check(_k32.QueryInformationJobObject(self.handle, _JobObjectBasicAccountingInformation,
                                              ctypes.byref(acc), ctypes.sizeof(acc), None))
        _check(_k32.QueryInformationJobObject(self.handle, _JobObjectExtendedLimitInformation,
                                              ctypes.byref(ext), ctypes.sizeof(ext), None))
        user = acc.TotalUserTime / 1e7  # 100 ns units
        return user + acc.TotalKernelTime / 1e7, user, ext.PeakJobMemoryUsed

    def peak_mb(self) -> float:
        try:
            return self.usage()[2] / 2**20
        except OSError:
            return 0.0

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def run_measured(argv: list[str], timeout=None, **kw):
    """``subprocess.run`` in a job of its own: returns (CompletedProcess or None on
    timeout, CPU seconds, user CPU seconds, peak bytes) of the whole process tree."""
    job = Job()
    try:
        proc = subprocess.Popen(argv, **kw)
        job.assign(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
            done = subprocess.CompletedProcess(argv, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            job.terminate()
            proc.communicate()
            done = None
        cpu, user, peak = job.usage()
        return done, cpu, user, peak
    finally:
        job.close()


def _thread_cycles() -> int:
    c = ctypes.c_uint64()
    _check(_k32.QueryThreadCycleTime(_k32.GetCurrentThread(), ctypes.byref(c)))
    return c.value


_tsc_hz: list[float] = []


def tsc_hz() -> float:
    """The rate of the cycle counts (the TSC frequency), measured once and passed on to
    child processes in OMEGA_TSC_HZ. Measured by busy-waiting: the thread's cycles
    over the elapsed time, best of three (a preemption can only lower the estimate)."""
    if not _tsc_hz:
        hz = float(os.environ.get("OMEGA_TSC_HZ") or 0)
        if hz <= 0:
            for _ in range(3):
                c0, t0 = _thread_cycles(), time.perf_counter()
                while time.perf_counter() - t0 < 0.01:
                    pass
                hz = max(hz, (_thread_cycles() - c0) / (time.perf_counter() - t0))
            os.environ["OMEGA_TSC_HZ"] = repr(hz)
        _tsc_hz.append(hz)
    return _tsc_hz[0]


def process_cpu() -> float:
    """CPU seconds of this process, all threads, user and kernel: a precise
    ``time.process_time``."""
    c = ctypes.c_uint64()
    _check(_k32.QueryProcessCycleTime(_k32.GetCurrentProcess(), ctypes.byref(c)))
    return c.value / tsc_hz()


def pipe_name(tag: str) -> str:
    return rf"\\.\pipe\omega-{os.getpid()}-{time.monotonic_ns()}-{tag}"


def serve_pipe(name: str, inbound: bool):
    """Create the named pipe ``name``, wait for its one client to open it, and return
    it as a text file (read from it if ``inbound``, else write to it)."""
    import msvcrt
    access = _PIPE_ACCESS_INBOUND if inbound else _PIPE_ACCESS_OUTBOUND
    h = _k32.CreateNamedPipeW(name, access, _PIPE_WAIT, 1, 65536, 65536, 0, None)
    if h == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    if not _k32.ConnectNamedPipe(h, None) and ctypes.get_last_error() != _ERROR_PIPE_CONNECTED:
        err = ctypes.get_last_error()
        _k32.CloseHandle(h)
        raise ctypes.WinError(err)
    fd = msvcrt.open_osfhandle(h, os.O_RDONLY if inbound else os.O_WRONLY)
    return open(fd, "r" if inbound else "w", encoding="utf-8", newline="\n")


def open_pipe(name: str, mode: str, wait: float = 30.0):
    """Open the client end of a named pipe the runner creates; it may not exist yet."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return open(name, mode, encoding="utf-8", newline="\n")
        except (FileNotFoundError, PermissionError, OSError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def available_bytes() -> int:
    """Physical memory available without paging (GlobalMemoryStatusEx)."""
    class _MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + \
            [(n, ctypes.c_uint64) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                            "ullAvailPageFile", "ullTotalVirtual",
                                            "ullAvailVirtual", "ullAvailExtendedVirtual")]
    st = _MemStatus()
    st.dwLength = ctypes.sizeof(st)
    _check(_k32.GlobalMemoryStatusEx(ctypes.byref(st)))
    return st.ullAvailPhys


def total_bytes() -> int:
    """Installed physical memory."""
    kb = ctypes.c_uint64()
    _check(_k32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)))
    return kb.value * 1024
