"""Windows memory figures for a process that is meant to stay up for weeks.

Why COMMIT and not just RAM. Windows hands a process memory in two steps: it
first promises the space (commit) and only later puts it in physical RAM
(working set). The promise is what runs out first on a PC left running for
days: every promise made by every process counts against one system-wide
commit limit (RAM + page file), and once that limit is reached nothing new can
start — programs fail to launch, dialogs come up empty — while Task Manager
still shows free RAM. So the number worth watching on an always-on program is
its committed memory, with the working set (real RAM) only as a second figure.

Read with ctypes on purpose: no psutil, so nothing extra has to be installed
or bundled for the app and the Webex listener to be able to report this.

Everything returns None off Windows or if the call fails, so a caller can
simply skip the display instead of guarding each field.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    """PrivateUsage is this process's commit charge — the figure Task Manager
    calls "Commit size". WorkingSetSize is its "Memory (active private
    working set)", i.e. the part actually in RAM."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    """ullTotalPageFile / ullAvailPageFile are the SYSTEM commit limit and how
    much of it is still free — despite the names, they are not the page file's
    size but RAM plus page file."""

    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass(frozen=True)
class MemSnapshot:
    """One reading. Bytes throughout; use fmt() for display."""

    proc_commit: int        # this process's promised memory
    proc_ram: int           # this process's part that is really in RAM
    sys_commit_used: int     # everything promised on this PC
    sys_commit_limit: int    # the ceiling — RAM + page file
    sys_ram_used: int
    sys_ram_total: int

    @property
    def sys_commit_pct(self) -> float:
        if self.sys_commit_limit <= 0:
            return 0.0
        return 100.0 * self.sys_commit_used / self.sys_commit_limit


def _psapi_call(pmc: _PROCESS_MEMORY_COUNTERS_EX) -> bool:
    """GetProcessMemoryInfo, from whichever DLL exports it here.

    It lives in psapi.dll, but Windows also re-exports it from kernel32 as
    K32GetProcessMemoryInfo; trying both means a stripped-down or redirected
    psapi cannot take the whole display down with it.

    The argument types have to be spelled out. Without them ctypes passes the
    process handle as a 32-bit int, and on 64-bit Windows the call then gets a
    truncated handle and fails — silently, with a zeroed struct that looks
    exactly like "this PC cannot report memory".
    """
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    handle = k32.GetCurrentProcess()
    size = ctypes.sizeof(pmc)
    pmc.cb = size
    for dll, fn in ((k32, "K32GetProcessMemoryInfo"),
                    (ctypes.windll.psapi, "GetProcessMemoryInfo")):
        try:
            func = getattr(dll, fn)
        except (AttributeError, OSError):
            continue
        func.restype = wintypes.BOOL
        func.argtypes = [wintypes.HANDLE,
                         ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
                         wintypes.DWORD]
        if func(handle, ctypes.byref(pmc), size):
            return True
    return False


def read() -> "MemSnapshot | None":
    """Current memory figures, or None if they cannot be read."""
    if not sys.platform.startswith("win"):
        return None
    try:
        pmc = _PROCESS_MEMORY_COUNTERS_EX()
        if not _psapi_call(pmc):
            return None
        ms = _MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(ms)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return None
        return MemSnapshot(
            proc_commit=int(pmc.PrivateUsage),
            proc_ram=int(pmc.WorkingSetSize),
            sys_commit_used=int(ms.ullTotalPageFile - ms.ullAvailPageFile),
            sys_commit_limit=int(ms.ullTotalPageFile),
            sys_ram_used=int(ms.ullTotalPhys - ms.ullAvailPhys),
            sys_ram_total=int(ms.ullTotalPhys),
        )
    except Exception:      # noqa: BLE001 - a display must never kill the caller
        return None


def fmt(n: "int | None") -> str:
    """Bytes as the shortest sensible unit: 412 MB, 1.8 GB."""
    if n is None:
        return "?"
    mb = n / (1024 * 1024)
    if mb < 1024:
        return f"{mb:.0f} MB"
    return f"{mb / 1024:.1f} GB"


def short_line(snap: "MemSnapshot | None" = None) -> str:
    """One-line summary for a status bar. Empty string when unreadable, so it
    can be concatenated without a guard."""
    snap = snap if snap is not None else read()
    if snap is None:
        return ""
    return (f"mem {fmt(snap.proc_commit)} (RAM {fmt(snap.proc_ram)})  ·  "
            f"PC {fmt(snap.sys_commit_used)}/{fmt(snap.sys_commit_limit)} "
            f"({snap.sys_commit_pct:.0f}%)")


def long_line(snap: "MemSnapshot | None" = None,
              since: "int | None" = None) -> str:
    """Fuller wording for the log, optionally with the growth since launch.

    `since` is an earlier reading's proc_commit; the difference is the part
    that matters over days — a figure that keeps climbing is a leak, a figure
    that settles is just how much the program needs.
    """
    snap = snap if snap is not None else read()
    if snap is None:
        return "Memory figures are not available on this system."
    growth = ""
    if since is not None:
        d = snap.proc_commit - since
        sign = "+" if d >= 0 else "-"
        growth = f", {sign}{fmt(abs(d))} since start"
    return (f"Memory: this app {fmt(snap.proc_commit)} committed "
            f"({fmt(snap.proc_ram)} in RAM){growth}. "
            f"Whole PC {fmt(snap.sys_commit_used)} of "
            f"{fmt(snap.sys_commit_limit)} committed "
            f"({snap.sys_commit_pct:.0f}%), RAM "
            f"{fmt(snap.sys_ram_used)} of {fmt(snap.sys_ram_total)}.")


if __name__ == "__main__":
    print(f"pid {os.getpid()}")
    print(long_line())
