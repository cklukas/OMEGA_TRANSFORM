# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""The Windows sandbox: AppContainers, through ctypes.

Usage: python -I winsandbox.py [--candidate] [--grant DIR]... WORKDIR -- COMMAND [ARG]...

Runs COMMAND in an AppContainer, the isolation of Windows Store apps and
browser sandboxes, and passes its exit code on. Like ``sandbox-exec`` on macOS
and ``bwrap`` on Linux it is a command prefix (``evaluator.sandbox_prefix``).

A process in a container has no network access: it holds none of the
capabilities that grant it (internetClient and the like), loopback included. It
opens a file only if the file's access list admits its container or one of its
capabilities, or "all application packages", as the Windows and Program Files
trees do; so the system, LLVM and the Visual Studio tools stay readable. Its
token has low integrity besides, so it writes only where the mandatory label is
low as well. Nothing else is reachable.

There are two containers, each with a profile (Windows starts no process in a
container without one): ``omega_transform.evaluator`` and
``omega_transform.candidate`` (``--candidate``). ``create_profiles`` makes them
once and takes away their access to the folder and registry key that come with
a profile, which every job would share. Access is granted to capabilities,
which the processes hold besides their container:

* ``omega_transform.runtime`` (every process) may read the Python installation
  that runs the jobs and this package, and list the directory above it;
* ``omega_transform.evaluator`` (evaluators only) may also read each directory
  passed with ``--grant``: the problem directory;
* ``omega_transform.job.<hash of the path>`` may change the job directory, which
  is labelled low. Only that job's processes hold it.

So candidate code cannot read the problem directory (its own job directory,
which may lie inside it, carries an entry of its own), and no job reaches
another job's directory. The access entries stay in place.

Windows points TEMP, TMP and LOCALAPPDATA of a process started in a container to
the profile folder; a stub (``--inside``) therefore starts the program inside
the container with the values of this process restored: the job directory.
Each process gets a job object that ends it with this wrapper; it is also in
the job objects of the wrapper, so the evaluator's limits and accounting cover
it.

Nothing in a container may need user32.dll, which cannot initialize there (a
container has no access to a window station; we give it none). Python's
``_ctypes`` links it only indirectly: it imports one COM helper,
ProgIDFromCLSID, from ole32.dll, and ole32 links user32. ``user32_free_ctypes``
makes a copy of ``_ctypes`` that takes the same function from combase.dll, which
does not link user32. Python's ``_ssl`` and ``_hashlib`` (OpenSSL) still need
user32 and do not load; hashlib falls back to its own algorithms.

Python in a container sets itself up with ``wincontainer.py``: it loads that copy
of ``_ctypes`` and moves multiprocessing's named pipes into the container's
namespace. Processes find it through the ``sitecustomize.py`` in
``winsandbox_site``, which this wrapper puts first on PYTHONPATH (with the copy
of ``_ctypes``); those started with ``-I`` call it themselves (the candidate
runner, and ``winjob.py``).

A container creates named pipes only in its own namespace (``pipe_dir``); the
runner creates its pipes there, with an access list that admits the evaluator
(``pipe_security``).

Standard library only, and runnable by path (no package on sys.path).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_KEEP = "OMEGA_SANDBOX_ENV"
_REDIRECTED = ("TEMP", "TMP", "LOCALAPPDATA")


def inside(argv: list[str]) -> int:
    """The stub in the container: run ``argv`` with the environment restored. It runs
    before ctypes is imported below, which it does not need."""
    env = dict(os.environ)
    env.update(json.loads(env.pop(_KEEP, "{}")))
    try:
        return subprocess.run(argv, env=env).returncode
    except OSError as e:
        print(f"winsandbox: cannot run {argv[0]}: {e}", file=sys.stderr)
        return 127


if __name__ == "__main__" and sys.argv[1:3] == ["--inside", "--"]:
    sys.exit(inside(sys.argv[3:]))

import ctypes  # noqa: E402
import hashlib  # noqa: E402
import shutil  # noqa: E402
import struct  # noqa: E402
from ctypes import wintypes  # noqa: E402

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_ue = ctypes.WinDLL("userenv", use_last_error=True)
_kb = ctypes.WinDLL("kernelbase", use_last_error=True)
_combase = ctypes.WinDLL("combase", use_last_error=True)  # not ole32, which needs user32

RUNTIME, EVALUATOR = "omega_transform.runtime", "omega_transform.evaluator"

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_LABEL_SECURITY_INFORMATION = 0x10
_GRANT_ACCESS, _REVOKE_ACCESS = 1, 4
_SE_REGISTRY_KEY = 4
_TRUSTEE_IS_SID = 0
_NO_INHERITANCE, _SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0, 3
_ACCESS_ALLOWED_ACE_TYPE, _SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0, 0x11
_INHERIT_ONLY_ACE = 0x8
_FILE_ALL_ACCESS = 0x1F01FF
_FILE_GENERIC_READ_EXECUTE = 0x1200A9
_FILE_LIST = 0x100081  # list directory, read attributes, synchronize: no file contents
_SECURITY_MANDATORY_LOW_RID = 0x1000
_SE_GROUP_ENABLED = 0x4
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x20002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x20009
_EXTENDED_STARTUPINFO_PRESENT = 0x80000
_CREATE_SUSPENDED = 0x4
_STARTF_USESTDHANDLES = 0x100
_HANDLE_FLAG_INHERIT = 0x1
_INFINITE = 0xFFFFFFFF
_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p),
                ("Capabilities", ctypes.POINTER(_SidAndAttributes)),
                ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]


class _Trustee(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int),
                ("ptstrName", ctypes.c_void_p)]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wintypes.DWORD), ("grfAccessMode", ctypes.c_int),
                ("grfInheritance", wintypes.DWORD), ("Trustee", _Trustee)]


class _AceHeader(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
                ("AceSize", wintypes.WORD)]


class _Ace(ctypes.Structure):  # ACCESS_ALLOWED_ACE and SYSTEM_MANDATORY_LABEL_ACE
    _fields_ = [("Header", _AceHeader), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


_ue.DeriveAppContainerSidFromAppContainerName.argtypes = [wintypes.LPCWSTR,
                                                          ctypes.POINTER(ctypes.c_void_p)]
_ue.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
_ue.CreateAppContainerProfile.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                          ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.c_void_p)]
_ue.CreateAppContainerProfile.restype = ctypes.c_long
_ue.GetAppContainerFolderPath.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_wchar_p)]
_ue.GetAppContainerFolderPath.restype = ctypes.c_long
_combase.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_kb.DeriveCapabilitySidsFromName.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
_adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                       ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(ctypes.c_void_p)]
_adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
_adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p]
_adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
_adv.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.POINTER(_ExplicitAccess),
                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_adv.SetEntriesInAclW.restype = wintypes.DWORD
_adv.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_adv.GetLengthSid.argtypes = [ctypes.c_void_p]
_adv.GetLengthSid.restype = wintypes.DWORD
_adv.CopySid.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
_adv.FreeSid.argtypes = [ctypes.c_void_p]
_adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
_adv.GetSecurityDescriptorSacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                           ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.POINTER(wintypes.BOOL)]
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_k32.LocalFree.argtypes = [ctypes.c_void_p]
_k32.LocalFree.restype = ctypes.c_void_p
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                   wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
_k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                           ctypes.c_void_p]
_k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
_k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx),
                                ctypes.POINTER(_ProcessInformation)]
_k32.GetStdHandle.argtypes = [wintypes.DWORD]
_k32.GetStdHandle.restype = wintypes.HANDLE
_k32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
_k32.ResumeThread.argtypes = [wintypes.HANDLE]
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
_k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                         wintypes.DWORD]
_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]



def _check(ok) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _check_status(err: int) -> None:
    if err:
        raise ctypes.WinError(err)


class _Sid:
    """A SID in memory of our own (the derivation functions return memory that must
    be freed in different ways; a copy needs no further care)."""

    def __init__(self, ptr):
        n = _adv.GetLengthSid(ptr)
        self.buf = ctypes.create_string_buffer(n)
        _check(_adv.CopySid(n, self.buf, ptr))

    @property
    def ptr(self):
        return ctypes.cast(self.buf, ctypes.c_void_p)

    def __str__(self) -> str:
        s = wintypes.LPWSTR()
        _check(_adv.ConvertSidToStringSidW(self.ptr, ctypes.byref(s)))
        try:
            return s.value
        finally:
            _k32.LocalFree(s)


def create_profiles() -> None:
    """Create the two container profiles, unless they exist (Windows starts no process
    in a container without a profile). The engine does this once, in
    ``evaluator.sandbox_backend``: concurrent calls can fail with E_UNEXPECTED."""
    import time
    for name in (container_name(False), container_name(True)):
        for attempt in range(5):  # another engine may be creating it right now
            p = ctypes.c_void_p()
            hr = _ue.CreateAppContainerProfile(name, name, "OMEGA TRANSFORM job sandbox", None,
                                               0, ctypes.byref(p))
            if hr >= 0:
                _adv.FreeSid(p)
            if hr >= 0 or hr & 0xFFFFFFFF == 0x800700B7:  # created, or ERROR_ALREADY_EXISTS
                break
            time.sleep(0.2)
        else:
            raise OSError(f"cannot create the AppContainer {name!r} (0x{hr & 0xFFFFFFFF:08x})")
        # A profile comes with a folder and a registry key its container may write to,
        # shared by every job: take that access away.
        sid = container_sid(name)
        folder = ctypes.c_wchar_p()
        if _ue.GetAppContainerFolderPath(str(sid), ctypes.byref(folder)) >= 0:
            try:
                revoke(folder.value, _SE_FILE_OBJECT, sid)
            finally:
                _combase.CoTaskMemFree(folder)
        revoke(r"CURRENT_USER\Software\Classes\Local Settings\Software\Microsoft\Windows"
               rf"\CurrentVersion\AppContainer\Storage\{name}", _SE_REGISTRY_KEY, sid)


_sids: dict[str, _Sid] = {}


def container_sid(name: str) -> _Sid:
    """The SID of the AppContainer ``name``."""
    if name not in _sids:
        p = ctypes.c_void_p()
        hr = _ue.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(p))
        if hr < 0:
            raise OSError(f"no SID for the AppContainer {name!r} (0x{hr & 0xFFFFFFFF:08x})")
        try:
            _sids[name] = _Sid(p)
        finally:
            _adv.FreeSid(p)
    return _sids[name]


def capability_sid(name: str) -> _Sid:
    if "cap:" + name in _sids:
        return _sids["cap:" + name]
    groups, caps = ctypes.c_void_p(), ctypes.c_void_p()
    ng, nc = wintypes.DWORD(), wintypes.DWORD()
    _check(_kb.DeriveCapabilitySidsFromName(name, ctypes.byref(groups), ctypes.byref(ng),
                                            ctypes.byref(caps), ctypes.byref(nc)))
    try:
        sid = _sids["cap:" + name] = _Sid(ctypes.cast(caps, ctypes.POINTER(ctypes.c_void_p))[0])
        return sid
    finally:
        for arr, n in ((groups, ng.value), (caps, nc.value)):
            for i in range(n):
                _k32.LocalFree(ctypes.cast(arr, ctypes.POINTER(ctypes.c_void_p))[i])
            _k32.LocalFree(arr)


def container_name(candidate: bool) -> str:
    return "omega_transform.candidate" if candidate else "omega_transform.evaluator"


def job_capability(workdir: str) -> str:
    """The capability that admits to one job directory, named after its path."""
    h = hashlib.sha256(os.path.normcase(os.path.abspath(workdir)).encode("utf-8")).hexdigest()
    return f"omega_transform.job.{h[:32]}"


def _aces(acl):
    if not acl:
        return
    count = ctypes.cast(acl, ctypes.POINTER(wintypes.WORD))[2]  # ACL.AceCount
    for i in range(count):
        p = ctypes.c_void_p()
        _check(_adv.GetAce(acl, i, ctypes.byref(p)))
        ace = ctypes.cast(p, ctypes.POINTER(_Ace)).contents
        yield ace, p.value + _Ace.SidStart.offset


def grant(path: str, sid: _Sid, mask: int, inherit: bool) -> bool:
    """Allow ``sid`` the rights ``mask`` on ``path`` (and, if ``inherit``, everything
    below it), unless it holds them already. True if the access list changed."""
    acl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    _check_status(_adv.GetNamedSecurityInfoW(path, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
                                             None, None, ctypes.byref(acl), None,
                                             ctypes.byref(sd)))
    try:
        for ace, ace_sid in _aces(acl.value):
            if ace.Header.AceType == _ACCESS_ALLOWED_ACE_TYPE and \
                    not ace.Header.AceFlags & _INHERIT_ONLY_ACE and \
                    ace.Mask & mask == mask and _adv.EqualSid(ace_sid, sid.ptr) and \
                    (not inherit or ace.Header.AceFlags & 0x3 == 0x3):
                return False
        ea = _ExplicitAccess(mask, _GRANT_ACCESS,
                             _SUB_CONTAINERS_AND_OBJECTS_INHERIT if inherit else _NO_INHERITANCE,
                             _Trustee(None, 0, _TRUSTEE_IS_SID, 0, sid.ptr))
        new = ctypes.c_void_p()
        _check_status(_adv.SetEntriesInAclW(1, ctypes.byref(ea), acl, ctypes.byref(new)))
        try:
            _check_status(_adv.SetNamedSecurityInfoW(path, _SE_FILE_OBJECT,
                                                     _DACL_SECURITY_INFORMATION, None, None,
                                                     new, None))
        finally:
            _k32.LocalFree(new)
        return True
    finally:
        _k32.LocalFree(sd)


def revoke(path: str, kind: int, sid: _Sid) -> None:
    """Remove every access entry for ``sid`` from the object ``path`` and below."""
    acl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    if _adv.GetNamedSecurityInfoW(path, kind, _DACL_SECURITY_INFORMATION, None, None,
                                  ctypes.byref(acl), None, ctypes.byref(sd)):
        return  # no such object
    try:
        if not any(_adv.EqualSid(ace_sid, sid.ptr) for _, ace_sid in _aces(acl.value)):
            return
        ea = _ExplicitAccess(0, _REVOKE_ACCESS, _NO_INHERITANCE,
                             _Trustee(None, 0, _TRUSTEE_IS_SID, 0, sid.ptr))
        new = ctypes.c_void_p()
        _check_status(_adv.SetEntriesInAclW(1, ctypes.byref(ea), acl, ctypes.byref(new)))
        try:
            _check_status(_adv.SetNamedSecurityInfoW(path, kind, _DACL_SECURITY_INFORMATION,
                                                     None, None, new, None))
        finally:
            _k32.LocalFree(new)
    finally:
        _k32.LocalFree(sd)


def label_low(path: str) -> None:
    """Give ``path`` and everything below it the low integrity label, so containers
    (whose tokens have low integrity) may write there."""
    sacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    _check_status(_adv.GetNamedSecurityInfoW(path, _SE_FILE_OBJECT, _LABEL_SECURITY_INFORMATION,
                                             None, None, None, ctypes.byref(sacl),
                                             ctypes.byref(sd)))
    try:
        for ace, ace_sid in _aces(sacl.value):
            if ace.Header.AceType == _SYSTEM_MANDATORY_LABEL_ACE_TYPE and \
                    ctypes.cast(ace_sid, ctypes.POINTER(wintypes.DWORD))[2] <= \
                    _SECURITY_MANDATORY_LOW_RID:  # the RID of S-1-16-<rid>
                return
    finally:
        _k32.LocalFree(sd)
    sd = ctypes.c_void_p()
    _check(_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "S:(ML;OICI;NW;;;LW)", 1, ctypes.byref(sd), None))
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        sacl = ctypes.c_void_p()
        _check(_adv.GetSecurityDescriptorSacl(sd, ctypes.byref(present), ctypes.byref(sacl),
                                              ctypes.byref(defaulted)))
        _check_status(_adv.SetNamedSecurityInfoW(path, _SE_FILE_OBJECT,
                                                 _LABEL_SECURITY_INFORMATION, None, None, None,
                                                 sacl))
    finally:
        _k32.LocalFree(sd)


def _user_sid() -> str:
    token = wintypes.HANDLE()
    _check(_adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x8, ctypes.byref(token)))  # QUERY
    try:
        n = wintypes.DWORD()
        _adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(n))  # TokenUser
        buf = ctypes.create_string_buffer(n.value)
        _check(_adv.GetTokenInformation(token, 1, buf, n, ctypes.byref(n)))
        return str(_Sid(ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]))
    finally:
        _k32.CloseHandle(token)


def pipe_security() -> str:
    """SDDL for the runner's named pipes: open to this user, also inside containers
    that hold the evaluator capability (an access check in a container must pass for
    the user and for the container), and to low integrity."""
    return (f"D:P(A;;GA;;;SY)(A;;GA;;;{_user_sid()})(A;;GA;;;{capability_sid(EVALUATOR)})"
            "S:(ML;;NW;;;LW)")


def pipe_dir() -> str:
    """The namespace in which a process in the candidate's container may create named
    pipes (its own); the evaluator opens them there by this full name."""
    session = wintypes.DWORD()
    _check(_k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)))
    return (rf"\\.\pipe\Sessions\{session.value}\AppContainerNamedObjects"
            rf"\{container_sid(container_name(True))}")


_msvc: list[dict] = []


def msvc_env() -> dict[str, str]:
    """Where the Visual C++ tools are, for clang in a container: clang asks the Visual
    Studio setup service (COM) for them, which a container cannot reach, and falls
    back to ``VCToolsInstallDir``. Found with vswhere, once; empty without them."""
    if not _msvc:
        env = {}
        if not os.environ.get("VCToolsInstallDir"):
            vswhere = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                                   "Microsoft Visual Studio", "Installer", "vswhere.exe")
            try:
                root = subprocess.run(
                    [vswhere, "-latest", "-products", "*", "-requires",
                     "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property",
                     "installationPath"], capture_output=True, text=True, timeout=60).stdout.strip()
                with open(os.path.join(root, "VC", "Auxiliary", "Build",
                                       "Microsoft.VCToolsVersion.default.txt")) as f:
                    tools = os.path.join(root, "VC", "Tools", "MSVC", f.read().strip())
                if root and os.path.isdir(tools):
                    env["VCToolsInstallDir"] = tools + os.sep
            except (OSError, subprocess.SubprocessError):
                pass
        _msvc.append(env)
    return _msvc[0]


CTYPES_ENV = "OMEGA_CTYPES"
_CTYPES_PATCH = "1"  # bump when the patch changes


def _ctypes_cache() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        "omega_transform", "ctypes")


def _patch_ole32_import(data: bytearray) -> bool:
    """Make the PE image ``data`` import from combase.dll what it imports from
    ole32.dll; False if it imports nothing from ole32. The new name goes into the
    unused end of the section that holds the old one, and the Authenticode
    signature, which no longer matches, is removed."""
    u16 = lambda o: struct.unpack_from("<H", data, o)[0]  # noqa: E731
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]  # noqa: E731
    pe = u32(0x3C)
    if data[pe:pe + 4] != b"PE\0\0" or u16(pe + 24) != 0x20B:  # PE32+
        raise ValueError("not a 64-bit PE image")
    opt = pe + 24
    ddir = opt + 112
    sections = [opt + u16(pe + 20) + 40 * i for i in range(u16(pe + 6))]

    def section(rva):  # header offset, virtual size, address, raw size, raw offset
        for h in sections:
            vs, va, rs, rp = struct.unpack_from("<IIII", data, h + 8)
            if va <= rva < va + max(vs, rs):
                return h, vs, va, rs, rp
        raise ValueError(f"RVA {rva:#x} outside every section")

    def offset(rva):
        _, _, va, _, rp = section(rva)
        return rp + rva - va

    desc = offset(u32(ddir + 8))  # the import directory: 20-byte descriptors
    while u32(desc + 12):
        name = offset(u32(desc + 12))
        if data[name:data.index(b"\0", name)].lower() == b"ole32.dll":
            break
        desc += 20
    else:
        return False
    h, vs, va, rs, rp = section(u32(desc + 12))
    new = b"combase.dll\0"
    at = (vs + 7) & ~7
    following = min([u32(o + 12) for o in sections if u32(o + 12) > va] or [1 << 32])
    if at + len(new) > rs or va + at + len(new) > following:  # in the file, and in memory
        raise ValueError("no room for the new DLL name")
    data[rp + at:rp + at + len(new)] = new
    struct.pack_into("<I", data, h + 8, at + len(new))  # the section's virtual size
    struct.pack_into("<I", data, desc + 12, va + at)
    cert, size = struct.unpack_from("<II", data, ddir + 4 * 8)  # file offset, not RVA
    if cert:
        struct.pack_into("<II", data, ddir + 4 * 8, 0, 0)
        del data[cert:cert + size]
    return True


def user32_free_ctypes() -> str | None:
    """A directory with a copy of this Python's ``_ctypes`` that does not need
    user32 (see the module docstring) and the libffi it needs; made once per
    ``_ctypes`` build. None if ``_ctypes`` does not need ole32 in the first place."""
    src_dir = os.path.join(sys.base_prefix, "DLLs")
    src = os.path.join(src_dir, "_ctypes.pyd")
    with open(src, "rb") as f:
        data = bytearray(f.read())
    key = hashlib.sha256(bytes(data) + _CTYPES_PATCH.encode()).hexdigest()[:16]
    out = os.path.join(_ctypes_cache(), key)
    if os.path.exists(os.path.join(out, "_ctypes.pyd")):
        return out
    if not _patch_ole32_import(data):
        return None
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(src_dir):  # the DLLs _ctypes loads from its own directory
        if name.lower().startswith("libffi") and name.lower().endswith(".dll"):
            shutil.copy2(os.path.join(src_dir, name), out)
    tmp = os.path.join(out, f"_ctypes.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.replace(tmp, os.path.join(out, "_ctypes.pyd"))
    except OSError:  # another engine made it meanwhile, and it is in use
        os.unlink(tmp)
    return out


def runtime_dirs() -> list[str]:
    """What every container may read: the Python installation, this package and the
    copies of ``_ctypes``."""
    os.makedirs(_ctypes_cache(), exist_ok=True)
    dirs = {os.path.dirname(os.path.abspath(__file__)), _ctypes_cache()}
    for d in (sys.prefix, sys.base_prefix):
        dirs.add(os.path.abspath(d))
    return sorted(dirs)


def prepare(workdir: str, grants: list[str]) -> None:
    """Set the access entries a job's containers need (idempotent)."""
    runtime, evaluator = capability_sid(RUNTIME), capability_sid(EVALUATOR)
    package = os.path.dirname(os.path.abspath(__file__))
    for d in runtime_dirs():
        grant(d, runtime, _FILE_GENERIC_READ_EXECUTE, inherit=True)
    # The package's parent is on PYTHONPATH: Python lists it to find the package.
    grant(os.path.dirname(package), runtime, _FILE_LIST, inherit=False)
    for d in grants:
        grant(d, evaluator, _FILE_GENERIC_READ_EXECUTE, inherit=True)
    grant(workdir, capability_sid(job_capability(workdir)), _FILE_ALL_ACCESS, inherit=True)
    label_low(workdir)


def run(argv: list[str], workdir: str, candidate: bool) -> int:
    """Run ``argv`` in the container of ``workdir`` with the inherited standard
    handles, environment and working directory; return its exit code."""
    caps = [capability_sid(RUNTIME), capability_sid(job_capability(workdir))] + \
        ([] if candidate else [capability_sid(EVALUATOR)])
    attrs = (_SidAndAttributes * len(caps))(*[_SidAndAttributes(c.ptr, _SE_GROUP_ENABLED)
                                              for c in caps])
    sid = container_sid(container_name(candidate))
    sc = _SecurityCapabilities(sid.ptr, attrs, len(caps), 0)
    # Windows points TEMP, TMP and LOCALAPPDATA of a process it starts in a container
    # to the container's profile folder, whatever environment it is given; only the
    # processes started inside the container keep theirs. So a stub starts the program
    # inside, with the values of this environment (the job directory) restored.
    os.environ[_KEEP] = json.dumps({k: os.environ[k] for k in _REDIRECTED if k in os.environ})
    # Python in the container sets itself up (wincontainer.py): through the
    # sitecustomize.py of winsandbox_site, or itself if started with -I.
    os.environ["OMEGA_APPCONTAINER"] = "1"
    first = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "winsandbox_site")]
    ctypes_dir = user32_free_ctypes()
    if ctypes_dir:  # found first by Python
        os.environ[CTYPES_ENV] = ctypes_dir
        first.append(ctypes_dir)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        first + [p for p in [os.environ.get("PYTHONPATH")] if p])
    exe = sys.executable
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(
        [exe, "-I", "-S", os.path.abspath(__file__), "--inside", "--"] + argv))

    std = [_k32.GetStdHandle(n & 0xFFFFFFFF) for n in (-10, -11, -12)]  # in, out, err
    handles = []  # the child inherits these and nothing else
    for h in std:
        if h and h != wintypes.HANDLE(-1).value and h not in handles and \
                _k32.SetHandleInformation(h, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT):
            handles.append(h)
    hlist = (wintypes.HANDLE * max(1, len(handles)))(*handles)

    size = ctypes.c_size_t()
    _k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    alist = ctypes.create_string_buffer(size.value)
    _check(_k32.InitializeProcThreadAttributeList(alist, 2, 0, ctypes.byref(size)))
    job = None
    pi = _ProcessInformation()
    try:
        _check(_k32.UpdateProcThreadAttribute(alist, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                              ctypes.byref(sc), ctypes.sizeof(sc), None, None))
        if handles:
            _check(_k32.UpdateProcThreadAttribute(alist, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                                  hlist, ctypes.sizeof(wintypes.HANDLE) *
                                                  len(handles), None, None))
        si = _StartupInfoEx()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = std
        si.lpAttributeList = ctypes.cast(alist, ctypes.c_void_p)
        _check(_k32.CreateProcessW(exe, cmdline, None, None, bool(handles),
                                   _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED, None, None,
                                   ctypes.byref(si), ctypes.byref(pi)))
        # Started suspended: a job of its own ends it, and whatever it starts, when this
        # wrapper ends (it is in the job of the wrapper's caller as well).
        job = _k32.CreateJobObjectW(None, None)
        _check(job)
        info = (ctypes.c_byte * 144)()  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, x64
        ctypes.cast(info, ctypes.POINTER(wintypes.DWORD))[4] = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(_k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, info,
                                            ctypes.sizeof(info)))
        _check(_k32.AssignProcessToJobObject(job, pi.hProcess))
        _k32.ResumeThread(pi.hThread)
        _k32.WaitForSingleObject(pi.hProcess, _INFINITE)
        code = wintypes.DWORD()
        _check(_k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code)))
        return code.value
    finally:
        if pi.hProcess and not job:  # not started after all
            _k32.TerminateProcess(pi.hProcess, 1)
        for h in (pi.hThread, pi.hProcess, job):
            if h:
                _k32.CloseHandle(h)
        _k32.DeleteProcThreadAttributeList(alist)


def main(argv: list[str]) -> int:
    candidate, grants = False, []
    while argv and argv[0].startswith("--"):
        opt = argv.pop(0)
        if opt == "--candidate":
            candidate = True
        elif opt == "--grant":
            grants.append(argv.pop(0))
        else:
            break
    workdir, sep, cmd = argv[0], argv[1], argv[2:]
    if sep != "--" or not cmd:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    prepare(workdir, [] if candidate else grants)
    return run(cmd, workdir, candidate)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
