"""Where the program says what happened to it, so a crash leaves evidence.

Announcer wrote nothing to disk. On 2026-09-17 it died three times inside two
minutes and the only record anywhere was Windows' own event log: faulting
module `Qt6Core.dll`, exception `0xc0000005`, three different garbage pointers.
That is enough to say "a destroyed object was used again" and not one word more
— no file, no function, no thread.

Four things are installed here, and each one catches a different kind of death:

  * `faulthandler`  — the one that matters. An access violation is not a Python
    exception: there is no traceback and no `except` that can see it. The C
    handler prints the Python stack of every thread straight into the file as
    the process goes down.
  * `sys.excepthook` — an ordinary uncaught exception. A frozen windowed build
    has no console, so without this it is lost too.
  * `threading.excepthook` — the same, on a worker thread, where it is silent
    even with a console.
  * Qt's own message handler — `QBackingStore::endPaint() called with active
    painter`, `Signal source has been deleted`, `QObject::startTimer: Timers can
    only be used with threads started with QThread`. Every one of those is Qt
    telling us about exactly this class of bug, minutes before it kills us.

The file lives under LOCALAPPDATA and never next to the program: the program
directory is OneDrive here, and a copied build's own folder can be read-only.
"""

from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import threading
from pathlib import Path

APP = "Announcer"
FILE_NAME = "announcer_crash.log"

# Rotated, not trimmed: the interesting part of a crash log is the END, and a
# file that grows for months is a file nobody opens.
MAX_BYTES = 2_000_000

# The open file, kept for the life of the process ON PURPOSE. faulthandler
# writes to this file descriptor from a signal handler; closing it, or letting
# it be garbage collected, turns the next crash into a silent one.
_fh = None
_lock = threading.Lock()


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = str(Path.home())
    return Path(base) / APP


def log_path() -> Path:
    return log_dir() / FILE_NAME


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def note(text: str) -> None:
    """One timestamped line. Never raises, whatever the disk is doing."""
    if _fh is None:
        return
    try:
        with _lock:
            _fh.write(f"{_stamp()}  {text}\n")
            _fh.flush()
    except Exception:
        pass


def install() -> Path | None:
    """Open the log and hook everything into it. Returns the path, or None.

    Call it as early as possible — before Qt is imported, so a failure inside
    Qt's own import is recorded too.
    """
    global _fh
    if _fh is not None:
        return log_path()
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / FILE_NAME
        if p.exists() and p.stat().st_size > MAX_BYTES:
            old = d / (FILE_NAME + ".1")
            try:
                old.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
            try:
                p.replace(old)
            except Exception:
                pass
        # Line buffered, so the last line written is on disk when the process
        # is killed rather than sitting in a buffer that dies with it.
        _fh = open(p, "a", buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        _fh = None
        return None

    try:
        faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        pass

    def _hook(kind, value, tb):
        import traceback
        note("UNCAUGHT EXCEPTION on the main thread")
        try:
            with _lock:
                traceback.print_exception(kind, value, tb, file=_fh)
                _fh.flush()
        except Exception:
            pass
        sys.__excepthook__(kind, value, tb)

    def _thread_hook(args):
        import traceback
        note(f"UNCAUGHT EXCEPTION on thread {args.thread and args.thread.name}")
        try:
            with _lock:
                traceback.print_exception(args.exc_type, args.exc_value,
                                          args.exc_traceback, file=_fh)
                _fh.flush()
        except Exception:
            pass

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    note(f"───── {APP} started  (pid {os.getpid()}, python "
         f"{sys.version.split()[0]}) ─────")
    return log_path()


def install_qt_handler() -> None:
    """Send Qt's own warnings to the same file. Call it after Qt is imported."""
    if _fh is None:
        return
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    words = {
        QtMsgType.QtDebugMsg: "Qt debug",
        QtMsgType.QtInfoMsg: "Qt info",
        QtMsgType.QtWarningMsg: "Qt WARNING",
        QtMsgType.QtCriticalMsg: "Qt CRITICAL",
        QtMsgType.QtFatalMsg: "Qt FATAL",
    }

    def handler(kind, _ctx, message):
        # Debug chatter is not worth a line on disk; everything else is.
        if kind == QtMsgType.QtDebugMsg:
            return
        note(f"{words.get(kind, 'Qt')}: {message}")

    try:
        qInstallMessageHandler(handler)
    except Exception:
        pass
