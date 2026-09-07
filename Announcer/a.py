"""
Screen Region Change Tracker
Monitors a specific region on screen and alerts on change.
"""

import array
import base64
import colorsys
import ctypes
import io
import math
import wave
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from collections import namedtuple
import json
import re
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
import socket
import ssl
from PIL import ImageGrab, ImageTk, ImageChops, ImageStat
import screeninfo
import sys
from pathlib import Path

# --- Configuration ---
POLL_INTERVAL_MS = 500      # how often to check (ms)
CHANGE_THRESHOLD = 2        # average pixel deviation (0-255)
FLASH_DURATION_MS = 3000    # how long to flash after detection
FLASH_INTERVAL_MS = 300     # flash blink speed
RAINBOW_BLINK_STEP_DEG = 47     # hue jump per blink (a clearly different colour)
# Moving rainbows (wave / spectrum): redrawn far more often than a blink so the
# motion looks continuous, with the colours shifted a little on every redraw.
GRADIENT_INTERVAL_MS = 60
GRADIENT_STEP = 3               # colour-wheel steps (of 256) per redraw
WAVE_BANDS = 3                  # rainbow rings between the centre and the edge
SPECTRUM_SPAN = 0.67            # share of the wheel laid across the width
BLUE_HUE_IDX = 171              # blue on the 0-255 wheel (left end of the spectrum)
# "Off" half of the image blink. Not 0: Windows lets the mouse through on
# chroma-keyed pixels only, so the silhouette must stay painted (and therefore
# clickable) even while it is visually gone.
FLASH_OFF_ALPHA = 0.02

# --- PV monitoring ---
PV_AVG_COUNT = 25   # number of recent values to average
PV_POLL_MS   = 500  # how often to read PVs (ms)

# --- Conditions ---
# A value condition looks at the WORST sample of the last few seconds, not at an
# average: one shot over the limit is the whole event, and averaging a minute of
# shots would hide it.
COND_PV_WINDOW_S    = 10
COND_MAX_REF_BYTES  = 1_000_000   # a reference lives inside presets.json
COND_DEFAULT_PV     = "L3-PM03-023:Energy"   # back-reflection energy

# --- Where the beam is going ---
# Two enums that together say where the shot ends up. They are archived ON
# CHANGE ONLY and change hours apart, so they are read with a widening
# look-back and on a much slower clock than everything else.
HALL_FATE_PV = "L3BT-MSS:Beam_fate"
HALL_PSS_PV  = "L3-PSS:STATE_EXH_EXTERNAL_HIGH_P"

# Beam fate value -> what it means. 0 is the switchyard on its way somewhere,
# which is a passing state, not a destination.
_HALLS = [(0, "switchyard moving"), (1, "E2"), (2, "E3"), (3, "E4"),
          (4, "E5 ELI-LUIS"), (5, "E5 ELI-MAIA")]
HALL_MOVING_VALUE = 0

_PSS_STATES = [(0, "internal only"), (1, "into the experiment")]

HALL_POLL_MS        = 2000        # they change hours apart; 500 ms buys nothing
HALL_MOVING_GRACE_S = 60          # how long "switchyard moving" is tolerated
# Widening look-back windows, in seconds. The first one that returns anything
# wins — a sparse enum can easily have written nothing for a week.
HALL_LOOKBACK_S     = (3600, 86400, 7 * 86400, 30 * 86400)


# Which draft keys one screen-area editor block works on, so a screen condition
# and the area attached to a value condition can live in one dict without
# stepping on each other.
#   (monitor, region, threshold, reference, decoded image)
_SCREEN_AREA_KEYS = ("monitor", "region", "threshold", "reference", "_ref_img")
_GATE_AREA_KEYS   = ("gate_monitor", "gate_region", "gate_threshold",
                     "gate_reference", "_gate_img")

# What a dropdown says when that half is switched off. Not "0": zero is a real
# beam fate and a real PSS state.
_NO_CHECK = "don't check"


def _value_for_label(table, label):
    """The number behind a dropdown entry, or None for "don't check"."""
    for num, text in table:
        if text == label:
            return float(num)
    return None


def _hall_name(value):
    """The hall a beam-fate number means, or the bare number if it means none."""
    if value is None:
        return "not readable"
    for num, label in _HALLS:
        if num == value:
            return label
    return f"unknown value {value:g}"


def _pss_name(value):
    if value is None:
        return "not readable"
    for num, label in _PSS_STATES:
        if num == value:
            return label
    return f"unknown value {value:g}"


def _hall_verdict(cond, fate, pss, moving_for_s):
    """Judge one hall condition. Pure, so it can be tested without a window.

    `fate` / `pss` are the newest archived values, or None when they could not
    be read at all. `moving_for_s` is how long the beam fate has been sitting on
    "switchyard moving", in seconds.

    Returns (verdict, sentence) where verdict is one of:
        "ok"      everything set matches
        "warn"    the switchyard is on its way, inside its grace period
        "unknown" something that is checked could not be read — never a trip,
                  because a missing reading is neither a good nor a bad one
        "trip"    the machine disagrees with what was set
    """
    want_hall = cond.get("hall")
    want_pss  = cond.get("pss")
    if want_hall is None and want_pss is None:
        return "unknown", "nothing to check — no hall and no PSS state set"

    missing = []
    if want_hall is not None and fate is None:
        missing.append("beam fate")
    if want_pss is not None and pss is None:
        missing.append("PSS state")
    if missing:
        return "unknown", f"{' and '.join(missing)} cannot be read"

    if want_pss is not None and pss != want_pss:
        return "trip", (f"PSS says {_pss_name(pss)}, "
                        f"but {_pss_name(want_pss)} was set")

    if want_hall is not None and fate != want_hall:
        if fate == HALL_MOVING_VALUE:
            grace = cond.get("moving_grace_s", HALL_MOVING_GRACE_S)
            if moving_for_s is not None and moving_for_s > grace:
                return "trip", (f"switchyard still moving after "
                                f"{moving_for_s:.0f} s — {_hall_name(want_hall)} "
                                f"was set")
            return "warn", f"switchyard moving — {_hall_name(want_hall)} was set"
        return "trip", (f"beam goes to {_hall_name(fate)}, "
                        f"but {_hall_name(want_hall)} was set")

    parts = []
    if want_hall is not None:
        parts.append(_hall_name(fate))
    if want_pss is not None:
        parts.append(_pss_name(pss))
    return "ok", " · ".join(parts)

# (channel, label, lo_orange, lo_red, hi_orange, hi_red, unit)
# `channel` is either a PV name (str) or a (minuend, subtrahend) tuple whose
# difference (minuend - subtrahend) is monitored.
_PV_MONITORS = [
    ("L3-UTIL-HEB03-001:PressOut_PSI", "Helium volume", 46, 45.5, 57, 60, "PSI"),
    ("HAPLS-VOLT_IN_CGL-SEEDER_ER3_ALPHA1:SeederPZTVoltage", "Alpha voltage", 1.2, 1.1, 1.9, 2.2, "V"),
]

# Chiller temperature deviations from setpoint (Temp - TempSP).
_CHILLER_LABELS = ["Chiller DA1", "Chiller DA2", "Chiller DA3", "Chiller DA4",
                   "Helium Chiller", "Utility chiller"]

# Absolute-temperature limits (°C) — an "external" condition checked on the raw
# Temp value, index-aligned with _PV_MONITORS. Outside this range => PURPLE alert
# (takes priority over the deviation thresholds). None = no absolute check.
# TODO: per-chiller values to be refined later.
_PV_ABS_RANGE = [None, None]   # Helium volume, Alpha voltage: no absolute range

for _i, _label in enumerate(_CHILLER_LABELS, start=1):
    _ch = f"L3-UTIL-CHL03-{_i:03d}"
    _PV_MONITORS.append(
        ((f"{_ch}:Temp", f"{_ch}:TempSP"), _label, -0.3, -0.6, 0.3, 0.6, "°C")
    )
    # DA1-DA4 + Helium Chiller: 7..17.5 °C ; Utility chiller: 18..22 °C
    _PV_ABS_RANGE.append((18.0, 22.0) if _i == 6 else (7.0, 17.5))


def _icon_app_id(prefix, ico_path):
    """Taskbar identity for `prefix`, tagged with the icon *and* this build.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it: an id that was once seen without a usable icon keeps drawing the
    generic placeholder for good, whatever icon the window later carries, and
    clearing the shell icon cache would have to be repeated on every PC.

    Hashing the icon's own bytes into the id was the first fix, but a
    content-only id can be poisoned just as well, and then it never recovers
    because it only changes when the picture is redrawn. Measured again on
    2026-09-03: Calibrations, CSS Logger, Git Work and Image Tools all drew
    the blank window placeholder on the taskbar while their title bars carried
    the right icon, and Diagnostic -- the only one whose id also carried its
    file name -- drew its icon. So the running build's own file name, which
    carries the version, goes into the hash too: every rebuild runs under an
    id Windows has never seen, so it cannot be serving a stale picture for it,
    on this PC or any other.

    Returns None when the icon cannot be read; the caller then sets no id at
    all rather than burning an id on a run that has no picture to give it.
    The same helper sits in every program here.
    """
    # A frozen build gets no taskbar identity at all, deliberately.
    # Windows caches the taskbar picture per AppUserModelID and never re-reads
    # it, so one bad cache entry breaks that build for good; tagging the id
    # with the build's file name only postponed it (Diagnostic v1.1.3's id
    # drew the blank placeholder within a day of the build). Measured
    # 2026-09-04 with three otherwise identical windows: the app's own id ->
    # placeholder, a never-seen id -> the right icon, no id at all -> the icon
    # from the exe's own resource, which the builder always embeds (verified
    # on a purpose-built PyInstaller exe). With no id Windows keys the button
    # on the exe itself, so there is no per-id cache left to go stale. An id
    # is still worth having when running from source, where the process is
    # python.exe and would otherwise wear the Python icon.
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return None
    if not ico_path:
        return None
    import hashlib
    import os
    import sys
    try:
        with open(ico_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    build = os.path.basename(sys.executable if getattr(sys, "frozen", False)
                             else (sys.argv[0] or __file__))
    tag = hashlib.sha1(data + b"\x00" + build.encode("utf-8", "replace"))
    return f"{prefix}.{tag.hexdigest()[:12]}"


def set_app_icon(win, ico_path, app_id=None):
    """Apply icon.ico to the title bar AND the Windows taskbar button.

    tkinter's iconbitmap only sets the title bar icon; the Windows 11 taskbar
    reads the small icon slots + window-class icon, which Tk leaves as its
    default feather. We force every slot from icon.ico via Win32.
    """
    import ctypes
    _aumid = _icon_app_id(app_id, ico_path) if app_id else None
    if _aumid:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass
    try:
        win.iconbitmap(default=ico_path)
    except Exception:
        pass
    try:
        u = ctypes.windll.user32
        hwnd = u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        big = u.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)
        sm  = u.LoadImageW(None, ico_path, 1, 16, 16, 0x10)
        for which, h in ((1, big), (0, sm), (2, sm)):
            if h:
                u.SendMessageW(hwnd, 0x0080, which, h)
        set_cls = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
        if big:
            set_cls(hwnd, -14, big)
        if sm:
            set_cls(hwnd, -34, sm)
    except Exception:
        pass


def _pv_key(channel):
    """Stable string key for a channel spec (str or (minuend, subtrahend) tuple)."""
    if isinstance(channel, tuple):
        return f"{channel[0]}-{channel[1]}"
    return channel


# Each entry: HTTP code -> (short label, plain-language explanation)
_HTTP_MESSAGES = {
    400: ("Bad request",
          "We sent the request in a form the archiver didn't accept. Likely a bug in the request, not your network."),
    401: ("Unauthorized",
          "The archiver wants credentials we didn't provide."),
    403: ("Access denied",
          "The archiver refused the request — you may not be allowed to read this channel."),
    404: ("Channel not found",
          "The archiver doesn't know this PV name. It may be misspelled or simply not archived."),
    500: ("Archiver server error",
          "The archiver was reached and answered, but crashed internally while handling the request. A server-side problem — usually temporary, not your network."),
    502: ("Archiver gateway error",
          "A gateway in front of the archiver got a broken reply from it."),
    503: ("Archiver unavailable",
          "The archiver is temporarily down, overloaded, or restarting. Usually clears up on its own."),
    504: ("Archiver gateway timeout",
          "A gateway forwarded the request but the archiver behind it never replied in time."),
}

# detail label -> plain-language explanation for the non-HTTP cases
_NETWORK_HINTS = {
    "Connection timed out":
        "The request left your PC but no reply came back within the time limit. The archiver is reachable in principle but too slow, overloaded, or the network is congested. (Compare: 'failed' = couldn't even start the connection.)",
    "Archiver unreachable":
        "We couldn't open a connection to the archiver at all — server is down, the address is wrong, or you're not on the right network/VPN. Nothing was sent.",
    "Connection failed":
        "The connection to the archiver was actively refused or dropped mid-way. The server (or a firewall) said 'no' rather than just staying silent.",
    "Invalid server response":
        "The archiver answered, but the data wasn't readable (not valid JSON). The server may be returning an error page instead of data.",
}


def _readable_pv_error(pv_name, exc):
    """Translate a raw fetch exception into (short message, plain explanation)."""
    if isinstance(exc, urllib.error.HTTPError):
        label, hint = _HTTP_MESSAGES.get(
            exc.code, ("Server error", "The archiver returned an unexpected error code."))
        detail = f"{label} (HTTP {exc.code})"
    elif isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        detail = "Connection timed out" if isinstance(reason, (socket.timeout, TimeoutError)) else "Archiver unreachable"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        detail = "Connection timed out"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, ConnectionError):
        detail = "Connection failed"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, (json.JSONDecodeError, ValueError)):
        detail = "Invalid server response"
        hint = _NETWORK_HINTS[detail]
    else:
        detail = str(exc) or exc.__class__.__name__
        hint = "An unexpected error occurred while reading this PV."
    return f"{pv_name} — {detail}", hint


_COLOR_CYCLES = ("fixed", "rainbow_blink", "rainbow_spectrum", "rainbow_wave")
_RESERVED_PRESET_KEYS = {"pv_thresholds", "window_geometry", "image_geometry", "flash_mode", "image_file",
                         "color_cycle", "flash_interval", "conditions", "sound_leadin",
                         "sound_device"}

# A Bluetooth speaker only carries audio once the link has been opened, which
# takes a moment when nothing has been played for a while. Every sound is
# therefore given a run-up of silence; without it the whole beep lands in the
# gap and the lab hears nothing at all.
SOUND_LEADIN_MS = 800           # default run-up of silence before the sound
SOUND_TAIL_MS = 150             # silence after it, so the end is not clipped
SOUND_RATE = 44100              # samples per second of the synthesised tone
SOUND_DEFAULT_DEVICE = "Default (Windows chooses)"


# ----------------------------------------------------------------------
# Playing a sound on a chosen output device
#
# Windows lets a sound go somewhere other than the speaker you expect: the
# volume mixer keeps a separate volume, and Windows 11 a separate output
# device, for each program. A program parked on the built-in output stays
# there even after a Bluetooth speaker is made the default, which is exactly
# how a lab can hear every system sound and nothing from this one. Naming the
# output device outright sidesteps the whole guessing game, so the sound is
# handed straight to the device the operator picked.
# ----------------------------------------------------------------------

_WAVE_MAPPER = 0xFFFFFFFF       # "let Windows choose" device number
_WHDR_DONE = 0x00000001


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_ushort),
                ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", ctypes.c_uint),
                ("nAvgBytesPerSec", ctypes.c_uint),
                ("nBlockAlign", ctypes.c_ushort),
                ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_char_p),
                ("dwBufferLength", ctypes.c_uint),
                ("dwBytesRecorded", ctypes.c_uint),
                ("dwUser", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint),
                ("dwLoops", ctypes.c_uint),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p)]


class _WAVEOUTCAPS(ctypes.Structure):
    _fields_ = [("wMid", ctypes.c_ushort),
                ("wPid", ctypes.c_ushort),
                ("vDriverVersion", ctypes.c_uint),
                ("szPname", ctypes.c_wchar * 32),
                ("dwFormats", ctypes.c_uint),
                ("wChannels", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("dwSupport", ctypes.c_uint)]


def _winmm():
    return ctypes.WinDLL("winmm")


def list_output_devices():
    """Every output the sound card layer offers, newest Bluetooth link included.

    The list is read fresh each time: a Bluetooth speaker only appears once it
    has connected, so a list built at start-up would never contain it."""
    names = [SOUND_DEFAULT_DEVICE]
    try:
        mm = _winmm()
        caps = _WAVEOUTCAPS()
        for i in range(mm.waveOutGetNumDevs()):
            if mm.waveOutGetDevCapsW(i, ctypes.byref(caps),
                                     ctypes.sizeof(caps)) == 0:
                name = caps.szPname.strip()
                if name and name not in names:
                    names.append(name)
    except Exception:
        pass
    return names


def _device_number(name):
    """Turn the picked device name back into the number the sound layer wants.

    A device that has since disconnected falls back to the Windows default
    rather than failing: a missing speaker must not silence the alarm."""
    if not name or name == SOUND_DEFAULT_DEVICE:
        return _WAVE_MAPPER
    try:
        mm = _winmm()
        caps = _WAVEOUTCAPS()
        for i in range(mm.waveOutGetNumDevs()):
            if mm.waveOutGetDevCapsW(i, ctypes.byref(caps),
                                     ctypes.sizeof(caps)) == 0:
                if caps.szPname.strip() == name:
                    return i
    except Exception:
        pass
    return _WAVE_MAPPER


def play_pcm(pcm, rate, channels, width, device_name=None):
    """Send raw sound samples to one output device and wait until they finish.

    Raises on any failure so the caller can say so on screen; a sound that
    quietly goes nowhere is the whole problem being fixed here."""
    mm = _winmm()
    fmt = _WAVEFORMATEX(1, channels, rate,
                        rate * channels * width, channels * width,
                        width * 8, 0)
    handle = ctypes.c_void_p()
    dev = _device_number(device_name)
    err = mm.waveOutOpen(ctypes.byref(handle), dev, ctypes.byref(fmt), 0, 0, 0)
    if err != 0 and dev != _WAVE_MAPPER:
        # The named device refused the format (or vanished mid-play) — rather
        # than stay silent, try again on whatever Windows would have used.
        err = mm.waveOutOpen(ctypes.byref(handle), _WAVE_MAPPER,
                             ctypes.byref(fmt), 0, 0, 0)
    if err != 0:
        raise OSError(f"cannot open the output device (code {err})")

    buf = ctypes.create_string_buffer(pcm, len(pcm))
    hdr = _WAVEHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
    hdr.dwBufferLength = len(pcm)
    try:
        err = mm.waveOutPrepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if err != 0:
            raise OSError(f"cannot prepare the sound (code {err})")
        err = mm.waveOutWrite(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if err != 0:
            mm.waveOutUnprepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            raise OSError(f"cannot play the sound (code {err})")
        seconds = len(pcm) / float(rate * channels * width)
        deadline = time.time() + seconds + 5.0
        while not (hdr.dwFlags & _WHDR_DONE) and time.time() < deadline:
            time.sleep(0.02)
        mm.waveOutUnprepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
    finally:
        mm.waveOutClose(handle)


def _parse_level(text):
    """One limit box: the number in it, or None when it is left empty.

    Empty means the level is switched off, which is not the same as zero — a
    zero limit on an energy would fire the moment the laser runs.
    """
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# Keeping windows on screen
# ----------------------------------------------------------------------
# Thickness of a normal window's title bar and border, used only until the
# window is on screen and can be measured for real.
_DEFAULT_INSETS = (11, 45, 11, 11)   # left, top, right, bottom

_Area = namedtuple("_Area", "x y w h primary")


def _screen_areas():
    """The usable part of every monitor (taskbar excluded), in the same
    coordinates windows are positioned with.

    Deliberately asked of Windows rather than taken from `screeninfo`: this
    program does not declare itself display-scaling aware, so a monitor set to
    150 % reports 1280x720 to us while `screeninfo` reports its real
    1920x1080. Windows are placed in the smaller space, so the `screeninfo`
    numbers would allow positions that are already past the right or bottom
    edge of the screen. `screeninfo` stays in use for the screenshot region,
    which does work in real pixels.
    """
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                            ctypes.POINTER(RECT), wintypes.LPARAM)
        def _collect(hmon, hdc, rect, data):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                found.append(_Area(r.left, r.top, r.right - r.left,
                                   r.bottom - r.top, bool(info.dwFlags & 1)))
            return True

        user32.EnumDisplayMonitors(0, None, _collect, 0)
        if found:
            return found
    except Exception:
        pass
    try:
        return [_Area(m.x, m.y, m.width, m.height,
                      bool(getattr(m, "is_primary", False)))
                for m in screeninfo.get_monitors()]
    except Exception:
        return []


def _fit_rect(x, y, w, h, insets=(0, 0, 0, 0)):
    """Nudge a window's top-left corner so the whole window — title bar and
    border included — stays on one screen. Returns the corrected corner.

    `x`/`y` are the top-left of the window's inside (what `winfo_rootx` and
    `winfo_rooty` report); `insets` says how far the frame reaches beyond that
    on each side. The screen chosen is the one the window already covers most
    of, so a window on the second monitor stays there.
    """
    areas = _screen_areas()
    if not areas:
        return x, y
    left, top, right, bottom = insets
    fx, fy = x - left, y - top
    fw, fh = w + left + right, h + top + bottom

    def covered(area):
        ox = max(0, min(fx + fw, area.x + area.w) - max(fx, area.x))
        oy = max(0, min(fy + fh, area.y + area.h) - max(fy, area.y))
        return ox * oy

    area = max(areas, key=covered)
    if covered(area) == 0:
        area = next((a for a in areas if a.primary), areas[0])
    # min/max order matters for a window larger than the screen: it is then
    # pinned to the top-left corner instead of being pushed off the other side.
    fx = min(max(fx, area.x), max(area.x, area.x + area.w - fw))
    fy = min(max(fy, area.y), max(area.y, area.y + area.h - fh))
    return fx + left, fy + top


class RegionSelector(tk.Toplevel):
    """Overlay window for selecting a screen region.

    Two-window approach:
      - main overlay: semi-transparent dim (alpha=0.15) so the monitor is visible
      - border_win:   fully opaque Toplevel drawn exactly over the drag rectangle,
                      so the red border is never affected by alpha
    """

    def __init__(self, parent, monitor, callback):
        super().__init__(parent)
        self.callback = callback
        self.monitor = monitor
        self.start_x = self.start_y = 0
        self._border_win = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.15)
        self.configure(bg="#000000")

        x, y = monitor.x, monitor.y
        w, h = monitor.width, monitor.height
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.canvas = tk.Canvas(self, cursor="cross", bg="#000000",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        lbl = tk.Label(self.canvas,
                       text="Drag to select region — ESC to cancel",
                       bg="black", fg="white", font=("Arial", 14))
        lbl.place(relx=0.5, rely=0.05, anchor="center")

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", self._cancel)

        self.bind("<Destroy>", lambda e: (parent._on_selector_closed() if e.widget is self else None))

    def _cancel(self, *_):
        if self._border_win:
            try: self._border_win.destroy()
            except Exception: pass
        self.destroy()

    def _on_press(self, event):
        self.start_x = event.x + self.monitor.x
        self.start_y = event.y + self.monitor.y

    def _on_drag(self, event):
        ex = event.x + self.monitor.x
        ey = event.y + self.monitor.y
        x1 = min(self.start_x, ex)
        y1 = min(self.start_y, ey)
        x2 = max(self.start_x, ex)
        y2 = max(self.start_y, ey)
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        T = 4  # border thickness in px
        if self._border_win is None:
            self._border_win = tk.Toplevel(self)
            self._border_win.overrideredirect(True)
            self._border_win.attributes("-topmost", True)
            self._border_win.attributes("-alpha", 1.0)
            self._border_win.configure(bg="#ff0000")
        # Resize/reposition to form a hollow red border using the window background
        self._border_win.geometry(f"{w}x{h}+{x1}+{y1}")
        # Draw hollow rectangle by placing a transparent inner frame
        for child in self._border_win.winfo_children():
            child.destroy()
        inner = tk.Frame(self._border_win, bg="#000000")
        inner.place(x=T, y=T, width=w - 2*T, height=h - 2*T)
        self._border_win.attributes("-transparentcolor", "#000000")

    def _on_release(self, event):
        if self._border_win:
            try: self._border_win.destroy()
            except Exception: pass
            self._border_win = None
        ex = event.x + self.monitor.x
        ey = event.y + self.monitor.y
        x1 = min(self.start_x, ex)
        y1 = min(self.start_y, ey)
        x2 = max(self.start_x, ex)
        y2 = max(self.start_y, ey)
        self.destroy()
        if (x2 - x1) > 5 and (y2 - y1) > 5:
            self.callback((x1, y1, x2, y2))


class ScreenTracker(tk.Tk):
    def __init__(self):
        # Before super().__init__(): Windows reads the identity when the first
        # taskbar button is created. See _icon_app_id() for the content tag.
        try:
            import ctypes as _ct
            _aumid = _icon_app_id("ELI.Announcer", self._get_icon_path())
            if _aumid:
                _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass
        super().__init__()
        self.title("Announcer")
        set_app_icon(self, self._get_icon_path())

        self.resizable(True, True)
        self.attributes("-topmost", True)

        self.region = None
        self.reference = None
        self.tracking = False
        self.changed = False
        self._selector_open = False

        self.change_threshold = tk.DoubleVar(value=CHANGE_THRESHOLD)
        self._flash_job = None
        self._poll_job = None
        self._flash_state = False
        self._flash_deadline = 0

        self._monitors = screeninfo.get_monitors()
        self._selected_monitor_idx = tk.IntVar(value=0)
        self._monitor_labels = [
            f"Monitor {i+1}"
            for i, m in enumerate(self._monitors)
        ]

        self._is_flashing = False
        self._preview_pinned = False
        self.status_var = tk.StringVar(value="Select a region on a monitor.")

        # PV monitoring state
        self._pv_poll_job = None
        self._next_hall_read = 0.0   # the hall values run on their own clock
        self._hall_read_once = False  # the Halls tab reads once when first opened
        self._hall_span_hint = {}     # PV -> the look-back window that worked
        self._pv_alert_rows: list[tk.Frame] = []
        self._pv_alert_labels: list[tk.Label] = []

        # Conditions state (the list itself is loaded once presets are read)
        self._conditions: list[dict] = []
        self._cond_uid_seq = 0
        self._cond_alert_rows: list[tk.Frame] = []
        self._cond_badges: dict[int, tuple] = {}   # condition uid -> (row, label)
        self._cond_editor = None

        # Message log state
        self._log_items: dict[str, tuple[str, int]] = {}   # message -> (item_id, count)
        self._log_hints: dict[str, str] = {}               # item_id -> plain explanation
        self._log_tip = None
        self._log_tip_item = None
        self._geom_before_hide = None

        # Separate image (flash) window — decoupled from the control window.
        self._image_win = None
        self._image_geometry = None
        self._flash_overlay = None

        if getattr(sys, "frozen", False):
            _base = Path(sys.executable).parent
        else:
            _base = Path(__file__).parent
        self._presets_path = _base / "presets.json"
        self._presets = self._load_presets()

        pv_saved = self._presets.get("pv_thresholds", {})
        self._pv_thr_vars = []
        for channel, _label, def_lo_o, def_lo_r, def_hi_o, def_hi_r, _unit in _PV_MONITORS:
            saved = pv_saved.get(_pv_key(channel), {})
            # backward-compat: old keys were "orange"/"red"
            lo_r_var = tk.DoubleVar(value=saved.get("lo_red",    saved.get("red",    def_lo_r)))
            lo_o_var = tk.DoubleVar(value=saved.get("lo_orange", saved.get("orange", def_lo_o)))
            hi_o_var = tk.DoubleVar(value=saved.get("hi_orange", def_hi_o))
            hi_r_var = tk.DoubleVar(value=saved.get("hi_red",    def_hi_r))
            for v in (lo_r_var, lo_o_var, hi_o_var, hi_r_var):
                v.trace_add("write", lambda *_: self._save_pv_thresholds())
            self._pv_thr_vars.append((lo_r_var, lo_o_var, hi_o_var, hi_r_var))

        self._conditions = self._load_conditions()

        self._build_ui()
        self.bind("<Button-1>", self._on_any_click)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        saved_geom = self._presets.get("window_geometry")
        if saved_geom:
            self._apply_geometry(self, self._at_least_requested(saved_geom))
        else:
            # Tall enough for the conditions list and the log underneath it.
            self.geometry("620x520")
        self._image_geometry = self._presets.get("image_geometry")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _on_any_click(self, event=None):
        if not self._is_flashing:
            return

        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None

        self._is_flashing = False
        self._hide_image_win()
        self._set_ui_visible(True)

    def _get_icon_path(self):
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        return str(base / "icon.ico")
    
    def _load_sound_files(self):
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        sounds_dir = base / "sounds"
        options = ["beep"]
        if sounds_dir.exists():
            for f in sorted(sounds_dir.glob("*.wav")):
                options.append(f.stem)
        return options

    def _images_dir(self):
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        return base / "images"

    def _load_image_files(self):
        """List stems of template images in the images/ folder (empty if none)."""
        images_dir = self._images_dir()
        options = []
        if images_dir.exists():
            for ext in ("*.png", "*.gif", "*.jpg", "*.jpeg"):
                for f in sorted(images_dir.glob(ext)):
                    if f.stem not in options:
                        options.append(f.stem)
        return options

    def _image_path(self, stem):
        """Resolve a stem to an existing image file path, or None."""
        if not stem:
            return None
        images_dir = self._images_dir()
        for ext in (".png", ".gif", ".jpg", ".jpeg"):
            p = images_dir / f"{stem}{ext}"
            if p.exists():
                return p
        return None

    def _image_mask(self, path, size):
        """Binarized alpha stencil of `path` at `size`, cached by file and size.

        Opening and resampling the template is far too slow to repeat for every
        step of the continuous colour cycle, while re-tinting a cached stencil is
        cheap. Binarizing matters too: LANCZOS anti-aliases the edges into
        partial-alpha pixels, and compositing those blends flash<->chroma into
        intermediate colours that the exact-match ``-transparentcolor`` cannot key
        out (the visible fringe / non-transparent silhouette). A hard threshold
        keeps every pixel either pure flash or pure chroma."""
        from PIL import Image
        try:
            stamp = path.stat().st_mtime
        except OSError:
            stamp = 0
        cache_key = (str(path), stamp, size)
        if getattr(self, "_mask_key", None) == cache_key:
            return self._mask
        src = Image.open(path).convert("RGBA").resize(size, resample=Image.LANCZOS)
        mask = src.split()[3].point(lambda a: 255 if a >= 128 else 0)
        self._mask_key, self._mask = cache_key, mask
        return mask

    _HUE_WHEEL = None

    @classmethod
    def _hue_wheel(cls):
        """The colour wheel as 256 fully saturated RGB triples, built once."""
        if cls._HUE_WHEEL is None:
            cls._HUE_WHEEL = [
                tuple(round(c * 255) for c in colorsys.hsv_to_rgb(i / 256.0, 1.0, 1.0))
                for i in range(256)]
        return cls._HUE_WHEEL

    def _coord_map(self, size, kind):
        """Grey map that says which colour of the rainbow a pixel gets.

        "wave" grows from 0 in the centre to 255 at the edges (rings), "spectrum"
        from 0 on the left to 255 on the right (bands). Cached per size, because
        every redraw of a moving rainbow reuses it."""
        from PIL import Image
        cache_key = (kind, size)
        if getattr(self, "_coord_key", None) == cache_key:
            return self._coord
        src = (Image.radial_gradient("L") if kind == "wave"
               else Image.linear_gradient("L").transpose(Image.ROTATE_90))
        self._coord_key = cache_key
        self._coord = src.resize(size, Image.BILINEAR)
        return self._coord

    def _gradient_photo(self, size, kind, phase, *, key=None, stencil=None):
        """One frame of a moving rainbow, as a chroma-keyed PhotoImage.

        "wave" runs rings out of the centre, "spectrum" lays blue-to-red across
        the width and drifts sideways; `phase` is how far the colours have
        travelled. `stencil` restricts the paint to the template silhouette (the
        rest stays keyed out / see-through); without one the whole window is
        painted, which is what the plain background-colour mode uses.

        The per-pixel work is done by mapping the cached grey map through a colour
        lookup table, so a frame costs a few milliseconds even full-screen."""
        from PIL import Image, ImageTk
        w, h = int(size[0]), int(size[1])
        if w < 1 or h < 1:
            return None
        try:
            coord = self._coord_map((w, h), kind)
            wheel = self._hue_wheel()
            if kind == "wave":
                start, span, travel = 0.0, WAVE_BANDS * 256.0, -phase
            else:
                start, span, travel = BLUE_HUE_IDX, -SPECTRUM_SPAN * 256.0, phase
            hues = [wheel[int(start + v * span / 255.0 + travel) % 256] for v in range(256)]
            rgb = Image.merge("RGB", [coord.point([c[ch] for c in hues]) for ch in range(3)])
            if stencil is None:
                return ImageTk.PhotoImage(rgb)
            base = Image.new("RGB", rgb.size, self._color_rgb(key or self._flash_key))
            return ImageTk.PhotoImage(Image.composite(rgb, base, stencil))
        except Exception as e:
            self._log_message(f"Rainbow frame failed: {e}")
            return None

    def _make_flash_photo(self, size, *, fade=None, key=None, color=None):
        """Build an ImageTk.PhotoImage of the selected template scaled to `size`.

        The template's alpha is used as a stencil and the result is a *chroma-keyed*
        RGB image: the silhouette is the flash colour and the surrounding area is
        the chroma colour, which the window's ``-transparentcolor`` turns
        see-through and click-through — so only the image itself is ever visible
        and only the image itself takes clicks.

        ``fade`` (0..1) blends the fill toward chroma to produce a faint "ghost"
        used for window alignment. ``color`` overrides the configured flash colour
        (used by the rainbow cycles). Returns None if no image is selected / found
        or the size is degenerate.
        """
        from PIL import Image
        path = self._image_path(self.image_file.get())
        w, h = size
        if path is None or w < 1 or h < 1:
            return None
        try:
            alpha = self._image_mask(path, (int(w), int(h)))
            chroma = self._color_rgb(key or self._chroma)
            flash = self._color_rgb(color or self.flash_color.get())
            # flash-coloured scorpion on a chroma (transparent) background
            base = Image.new("RGB", alpha.size, chroma)
            fill = Image.new("RGB", alpha.size, flash)
            out = Image.composite(fill, base, alpha)
            if fade is not None:
                ghost = Image.new("RGB", alpha.size, chroma)
                out = Image.blend(ghost, out, max(0.0, min(1.0, fade)))
            from PIL import ImageTk as _ImageTk
            return _ImageTk.PhotoImage(out)
        except Exception as e:
            self._log_message(f"Image load failed: {e}")
            return None

    def _color_rgb(self, color):
        """Return a Tk colour string as an (r, g, b) 0-255 tuple usable by PIL."""
        try:
            r, g, b = self.winfo_rgb(color)
            return (r // 256, g // 256, b // 256)
        except Exception:
            return (255, 34, 34)

    # Transparency key candidates for the flash window. Whatever is painted in
    # the active key becomes see-through AND click-through, so the key must never
    # equal the flash colour itself.
    # Near-black, because the rainbow cycle runs through every fully saturated
    # hue (magenta and green included) and would otherwise key itself away.
    _FLASH_KEY = "#010203"
    _FLASH_KEY_ALT = "#030201"

    def _resolve_flash_key(self):
        """Pick a transparency key that differs from the current flash colour.

        The key is matched exactly by ``-transparentcolor``, so only an exact hit
        is a problem: with the flash colour set to the key the whole flash
        (background fill or image silhouette) would be keyed away — invisible and
        letting the dismiss click fall through to the window behind it. In that
        one case fall back to the alternate key."""
        try:
            if self._color_rgb(self.flash_color.get()) == self._color_rgb(self._FLASH_KEY):
                return self._FLASH_KEY_ALT
        except Exception:
            pass
        return self._FLASH_KEY

    def _build_ui(self):
        pad = dict(padx=10, pady=5)

        # Theme background color — used as the chroma key for the transparent
        # (HUD) mode during tracking. Anything painted with this color becomes
        # fully transparent and click-through; only the circle / PV badges stay.
        self._chroma = ttk.Style().lookup("TFrame", "background") or self.cget("bg")
        self.configure(bg=self._chroma)

        # Dedicated chroma key for the flash images. The theme colour keys fine for
        # native widgets, but PIL-painted pixels must match the -transparentcolor
        # value EXACTLY, which a named/system theme colour can't guarantee. A unique
        # magenta we both paint and key on is reliably transparent / click-through.
        # Resolved (not hard-coded) so it can never equal the chosen flash colour.
        self._flash_key = self._resolve_flash_key()

        # Řádek 0: mon_frame vlevo, kolečko samostatně vpravo
        self._top_frame = ttk.Frame(self)
        self._top_frame.grid(row=0, column=0, sticky="ew", **pad)

        self._mon_frame = ttk.LabelFrame(self._top_frame, text="Monitor")
        self._mon_frame.pack(side="left")

        inner = ttk.Frame(self._mon_frame)
        inner.pack(fill="x", padx=6, pady=4)

        self._mon_combo = ttk.Combobox(inner, values=self._monitor_labels,
                                       state="readonly", width=12)
        self._mon_combo.current(0)
        self._mon_combo.pack(side="left", padx=(0, 4))
        self._mon_combo.bind("<<ComboboxSelected>>",
                             lambda e: self._selected_monitor_idx.set(self._mon_combo.current()))

        ttk.Button(inner, text="Identify",
                   command=self._identify_monitors).pack(side="left")

        # Kolečko hned za Monitor skupinou
        self._canvas_circle = tk.Canvas(self._top_frame, width=34, height=34,
                                        highlightthickness=0,
                                        bg=ttk.Style().lookup("TFrame", "background"))
        self._canvas_circle.pack(side="left", padx=(10, 4))
        self._circle = self._canvas_circle.create_oval(3, 3, 31, 31,
                                                       fill="#888888", outline="", width=0)
        self._canvas_circle.bind("<Button-1>", lambda e: self._toggle_tracking())
        self._canvas_circle.config(cursor="hand2")

        # Settings button hned za kolečkem
        self._settings_popup = None
        self._settings_btn = ttk.Button(self._top_frame, text="⚙ Settings",
                                        command=self._toggle_settings_popup)
        self._settings_btn.pack(side="left", padx=(4, 0))

        # The quick rectangle's own buttons and the saved regions now live on the
        # "Screen areas" tab, next to the thing they belong to.

        # PV alert panel (row=3) — individual badges shown only when condition
        # breached, laid out side by side and wrapping to new lines as needed.
        self._pv_frame = ttk.Frame(self)
        self._pv_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._pv_frame.grid_remove()
        self._pv_frame.grid_propagate(False)
        self._pv_last_width = 0
        self._pv_frame.bind("<Configure>", self._on_pv_frame_configure)
        self.columnconfigure(0, weight=1)

        for _pv_name, _label, *_thresholds in _PV_MONITORS:
            row_frame = tk.Frame(self._pv_frame, bg="#cc6600", padx=8, pady=5)
            row_frame._active = False
            lbl = tk.Label(row_frame, text="", bg="#cc6600", fg="white",
                           font=("Segoe UI", 9, "bold"), anchor="w")
            lbl.pack()
            self._pv_alert_rows.append(row_frame)
            self._pv_alert_labels.append(lbl)

        # Conditions panel (row=2) — built after the badge frame, because every
        # condition gets a badge of its own inside it.
        self._build_conditions_panel()

        # Message log (row=4) — shows runtime messages such as PV fetch errors.
        # Hidden while tracking (see _set_ui_visible).
        self._log_frame = ttk.LabelFrame(self, text="Message log")
        self._log_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.rowconfigure(4, weight=1)
        self._log_frame.rowconfigure(0, weight=1)
        self._log_frame.columnconfigure(0, weight=1)

        cols = ("time", "count", "msg")
        tv = ttk.Treeview(self._log_frame, columns=cols, show="headings", height=6)
        tv.heading("time", text="Time")
        tv.heading("count", text="#")
        tv.heading("msg", text="Message  (hover for details)")
        tv.column("time", width=70, anchor="w", stretch=False)
        tv.column("count", width=40, anchor="center", stretch=False)
        tv.column("msg", width=250, anchor="w", stretch=True)
        vsb = ttk.Scrollbar(self._log_frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(2, 6))
        vsb.grid(row=0, column=1, sticky="ns", pady=(2, 6), padx=(0, 6))
        ttk.Button(self._log_frame, text="Clear", width=6,
                   command=self._clear_log).grid(row=1, column=0, columnspan=2,
                                                 sticky="e", padx=6, pady=(0, 6))
        self._log_tree = tv
        tv.bind("<Motion>", self._on_log_hover)
        tv.bind("<Leave>", lambda e: self._hide_log_tip())

        # Pre-build Settings variables (popup builds widgets on first open)
        self.flash_color = tk.StringVar(value="#ff2222")
        self.flash_duration = tk.DoubleVar(value=0.0)
        self.sound_enabled = tk.BooleanVar(value=True)
        self.sound_freq = tk.IntVar(value=1500)
        self.sound_duration = tk.IntVar(value=500)
        self.sound_file = tk.StringVar(value="beep")
        self.sound_leadin = tk.IntVar(value=SOUND_LEADIN_MS)
        self.sound_device = tk.StringVar(value=SOUND_DEFAULT_DEVICE)
        self._sound_files = self._load_sound_files()
        self._flash_color_btn = None  # created in popup

        # Colour behaviour of the flash:
        #   "fixed"            - blink in the picked colour
        #   "rainbow_blink"    - blink, each flash a single different hue
        #   "rainbow_spectrum" - blink, rainbow laid across the width and drifting
        #   "rainbow_wave"     - no blinking, rainbow rings run out of the centre
        self.color_cycle = tk.StringVar(value="fixed")
        self.flash_interval = tk.DoubleVar(value=FLASH_INTERVAL_MS / 1000.0)
        self._flash_hue = 0.0           # position in the rainbow (degrees)
        self._flash_col = "#ff2222"     # colour of the current flash step
        self._flash_size = (0, 0)       # image-window size of the running flash
        self._flash_phase = 0.0         # travel of the moving rainbows
        self._blink_toggle_at = 0.0     # next lit/unlit switch (moving rainbows)

        # Flash-image (template) settings
        self.flash_mode = tk.StringVar(value="color")   # "color" | "image"
        self.image_file = tk.StringVar(value="")
        self._image_files = self._load_image_files()
        self._flash_photo = None        # cached PhotoImage for the current flash
        self._flash_img_label = None    # tk.Label showing the template while flashing
        self._align_photo = None        # ghost PhotoImage during window alignment
        self._align_label = None
        self._align_bind = None
        self._align_last_size = (0, 0)

        # Restore saved flash settings, then persist on change.
        saved_mode = self._presets.get("flash_mode")
        if saved_mode == "alternate":   # retired mode: the image alone blinks now
            saved_mode = "image"
        if saved_mode in ("color", "image"):
            self.flash_mode.set(saved_mode)
        saved_cycle = self._presets.get("color_cycle")
        if saved_cycle == "rainbow_smooth":   # retired: the wave is the no-blink mode
            saved_cycle = "rainbow_wave"
        if saved_cycle in _COLOR_CYCLES:
            self.color_cycle.set(saved_cycle)
        saved_iv = self._presets.get("flash_interval")
        if isinstance(saved_iv, (int, float)) and 0 <= saved_iv <= 3.0:
            self.flash_interval.set(float(saved_iv))
        saved_img = self._presets.get("image_file")
        if saved_img in self._image_files:
            self.image_file.set(saved_img)
        saved_lead = self._presets.get("sound_leadin")
        if isinstance(saved_lead, (int, float)) and 0 <= saved_lead <= 5000:
            self.sound_leadin.set(int(saved_lead))
        saved_dev = self._presets.get("sound_device")
        # Kept by name, not by number: the numbers shuffle every time a
        # Bluetooth speaker connects or drops.
        if isinstance(saved_dev, str) and saved_dev:
            self.sound_device.set(saved_dev)
        self.sound_leadin.trace_add("write", lambda *_: self._save_flash_settings())
        self.sound_device.trace_add("write", lambda *_: self._save_flash_settings())
        self.flash_mode.trace_add("write", lambda *_: self._save_flash_settings())
        self.color_cycle.trace_add("write", lambda *_: self._save_flash_settings())
        self.flash_interval.trace_add("write", lambda *_: self._save_flash_settings())
        self.image_file.trace_add("write", lambda *_: self._save_flash_settings())

    # ------------------------------------------------------------------
    # Conditions — the data
    # ------------------------------------------------------------------
    # A condition is one thing that has to stay true while the Announcer
    # watches. Two kinds:
    #   screen — a rectangle still looks like its reference picture
    #   pv     — an archived value stays under its limit
    # Any of them going false raises the alarm exactly like a change in the
    # ad-hoc region does, and says in words what needs doing.
    def _next_cond_uid(self):
        self._cond_uid_seq += 1
        return self._cond_uid_seq

    def _load_conditions(self):
        """Read the saved conditions, dropping anything unusable.

        The reference picture is decoded once, here, and kept in memory under
        "_ref_img"; keys starting with an underscore are never saved back.
        """
        out = []
        for raw in self._presets.get("conditions") or []:
            if not isinstance(raw, dict) or raw.get("kind") not in ("screen", "pv", "hall"):
                continue
            cond = dict(raw)
            cond["name"] = str(cond.get("name") or "").strip() or "(unnamed)"
            cond["enabled"] = bool(cond.get("enabled", True))
            cond["message"] = str(cond.get("message") or "")
            cond["_uid"] = self._next_cond_uid()
            if cond["kind"] == "screen":
                region = cond.get("region") or []
                if len(region) != 4:
                    continue
                try:
                    cond["region"] = [int(v) for v in region]
                    cond["threshold"] = float(cond.get("threshold", CHANGE_THRESHOLD))
                    cond["monitor"] = int(cond.get("monitor", 0))
                except (TypeError, ValueError):
                    continue
                cond["_ref_img"] = self._decode_reference(cond.get("reference"))
            elif cond["kind"] == "pv":
                cond["pv"] = str(cond.get("pv") or "").strip()
                if not cond["pv"]:
                    continue
                cond["warn"] = _parse_level(str(cond.get("warn") if cond.get("warn") is not None else ""))
                cond["trip"] = _parse_level(str(cond.get("trip") if cond.get("trip") is not None else ""))
                cond["unit"] = str(cond.get("unit") or "")
                # Optional second half: a screen area that must keep matching its
                # own picture. Absent keys = a plain value condition.
                gate = cond.get("gate_region") or []
                usable = len(gate) == 4
                if usable:
                    try:
                        cond["gate_region"] = [int(v) for v in gate]
                        cond["gate_threshold"] = float(cond.get("gate_threshold", CHANGE_THRESHOLD))
                        cond["gate_monitor"] = int(cond.get("gate_monitor", 0))
                    except (TypeError, ValueError):
                        usable = False
                    else:
                        cond["_gate_img"] = self._decode_reference(cond.get("gate_reference"))
                if not usable:
                    # An unreadable area is dropped whole, picture included, so
                    # the next save does not carry the wreckage forward. The
                    # value half of the condition is untouched.
                    for key in ("gate_region", "gate_reference", "gate_threshold",
                                "gate_monitor", "_gate_img"):
                        cond.pop(key, None)
            else:
                cond["hall"] = _parse_level(str(cond.get("hall") if cond.get("hall") is not None else ""))
                cond["pss"] = _parse_level(str(cond.get("pss") if cond.get("pss") is not None else ""))
                try:
                    cond["moving_grace_s"] = float(cond.get("moving_grace_s", HALL_MOVING_GRACE_S))
                except (TypeError, ValueError):
                    cond["moving_grace_s"] = HALL_MOVING_GRACE_S
            out.append(cond)
        return out

    def _save_conditions(self):
        self._presets["conditions"] = [
            {k: v for k, v in cond.items() if not k.startswith("_")}
            for cond in self._conditions]
        self._save_presets_file()

    @staticmethod
    def _decode_reference(text):
        """The stored reference picture as a PIL image, or None."""
        if not text:
            return None
        try:
            from PIL import Image
            # .copy() forces the lazy reader to read now, while the buffer lives.
            return Image.open(io.BytesIO(base64.b64decode(text))).copy()
        except Exception:
            return None

    def _encode_reference(self, img):
        """PNG-compress a reference crop to base64, or None if it is too big.

        References live inside presets.json, so a whole screen does not belong
        there — say so instead of writing a megabyte of base64.
        """
        try:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
        except Exception as e:
            self._log_message(f"Reference could not be stored: {e}")
            return None
        if len(data) > COND_MAX_REF_BYTES:
            self._log_message(
                f"Reference too big ({len(data) // 1024} kB) — watch a smaller area",
                "The reference picture is kept inside presets.json, so it has to "
                "stay small. Draw just the box that changes, not the whole screen.")
            return None
        return base64.b64encode(data).decode("ascii")

    def _cond_summary(self, cond):
        """The one-line description shown in the Conditions list."""
        if cond["kind"] == "screen":
            region = cond.get("region")
            if not region:
                return "area not drawn yet"
            x1, y1, x2, y2 = region
            ref = "reference taken" if cond.get("_ref_img") is not None else "NO REFERENCE"
            return (f"Monitor {cond.get('monitor', 0) + 1} · {x2 - x1}x{y2 - y1} px · "
                    f"change over {cond['threshold']:.1f} · {ref}")
        if cond["kind"] == "hall":
            parts = []
            if cond.get("hall") is not None:
                parts.append(f"beam to {_hall_name(cond['hall'])}")
            if cond.get("pss") is not None:
                parts.append(f"PSS {_pss_name(cond['pss'])}")
            return " · ".join(parts) if parts else "nothing set — it cannot fire"
        unit = f" {cond['unit']}" if cond.get("unit") else ""
        warn = "—" if cond.get("warn") is None else f"{cond['warn']:g}{unit}"
        trip = "—" if cond.get("trip") is None else f"{cond['trip']:g}{unit}"
        out = f"{cond['pv']} · warn over {warn} · trip over {trip}"
        if cond.get("gate_region"):
            gx1, gy1, gx2, gy2 = cond["gate_region"]
            gate_ref = "reference taken" if cond.get("_gate_img") is not None else "NO REFERENCE"
            out += (f" · + screen area {gx2 - gx1}x{gy2 - gy1} px on monitor "
                    f"{cond.get('gate_monitor', 0) + 1} ({gate_ref})")
        return out

    def _enabled_conditions(self, kind=None):
        return [c for c in self._conditions
                if c["enabled"] and (kind is None or c["kind"] == kind)]

    # ------------------------------------------------------------------
    # Conditions — the panel
    # ------------------------------------------------------------------
    # One notebook page per kind of condition. All three pages read and write
    # the SAME self._conditions list; a row's iid is its index in that list, so
    # everything that works on "the selected condition" keeps working whichever
    # page the selection happens to be on.
    _COND_TABS = (("screen", "Screen areas"), ("pv", "Values"), ("hall", "Halls"))

    def _build_conditions_panel(self):
        # Tab labels are painted explicitly: the ttk default on this machine
        # picks up the Windows dark theme and draws grey-on-grey.
        style = ttk.Style()
        style.configure("Cond.TNotebook.Tab", padding=(12, 4),
                        background="#dcdcdc", foreground="#101010")
        style.map("Cond.TNotebook.Tab",
                  background=[("selected", "#f5f5f5"), ("active", "#eaeaea")],
                  foreground=[("selected", "#101010"), ("active", "#101010")])

        self._cond_frame = ttk.Notebook(self, style="Cond.TNotebook")
        self._cond_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.rowconfigure(2, weight=1)

        self._cond_trees = {}
        for kind, title in self._COND_TABS:
            page = ttk.Frame(self._cond_frame, padding=(0, 4, 0, 0))
            self._cond_frame.add(page, text=title)
            page.columnconfigure(0, weight=1)
            page.rowconfigure(1, weight=1)
            self._build_cond_page(page, kind)

        # Opening the Halls tab reads the two values once, so the operator sees
        # what the machine is doing before setting a condition against it.
        # Reading is not alerting: it happens whether or not watching is on.
        self._cond_frame.bind("<<NotebookTabChanged>>", self._on_cond_tab_changed)

        self._refresh_cond_tree()

    def _on_cond_tab_changed(self, _event=None):
        try:
            kind = self._COND_TABS[self._cond_frame.index(self._cond_frame.select())][0]
        except Exception:
            return
        if kind == "hall" and not self._hall_read_once:
            self._hall_read_once = True
            self._read_hall_now()

    def _build_cond_page(self, page, kind):
        """The list + buttons of one tab, and the extras that tab owns."""
        # Row 0: whatever belongs only to this tab.
        if kind == "screen":
            self._build_quick_region_row(page)
        elif kind == "hall":
            self._build_hall_readout(page)

        cols = ("on", "name", "detail")
        tv = ttk.Treeview(page, columns=cols, show="headings", height=4)
        tv.heading("on", text="On")
        tv.heading("name", text="Condition")
        tv.heading("detail", text="Settings")
        tv.column("on", width=34, anchor="center", stretch=False)
        tv.column("name", width=150, anchor="w", stretch=False)
        tv.column("detail", width=320, anchor="w", stretch=True)
        vsb = ttk.Scrollbar(page, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 4))
        vsb.grid(row=1, column=1, sticky="ns", pady=(2, 4), padx=(0, 6))
        tv.bind("<Button-1>", self._on_cond_click)
        tv.bind("<Double-1>", lambda e: self._edit_condition())
        self._cond_trees[kind] = tv

        add_label = {"screen": "Add screen area", "pv": "Add value",
                     "hall": "Add hall check"}[kind]
        btns = ttk.Frame(page)
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(btns, text=add_label, width=16,
                   command=lambda k=kind: self._edit_condition(new=k)).pack(side="left")
        ttk.Button(btns, text="Edit", width=6,
                   command=self._edit_condition).pack(side="left", padx=(4, 0))
        ttk.Button(btns, text="Delete", width=7,
                   command=self._delete_condition).pack(side="left", padx=(4, 0))
        if kind in ("screen", "pv"):
            # Same arrow as the main window: re-take the reference picture.
            ttk.Button(btns, text="↺", width=3,
                       command=self._resnap_condition).pack(side="left", padx=(4, 0))

    def _build_quick_region_row(self, page):
        """The unnamed rectangle and its saved positions — they belong to the
        Screen areas tab, not to the window as a whole."""
        row = ttk.Frame(page)
        row.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 2))

        self._preview_popup = None
        self._preview_photo = None
        self.btn_preview = ttk.Button(row, text="Preview region", width=14)
        self.btn_preview.pack(side="left")
        self.btn_preview.bind("<Enter>", self._show_preview_popup)
        self.btn_preview.bind("<Leave>", lambda *_: self.after(100, self._check_hide_preview))
        self.btn_preview.bind("<Button-1>", self._toggle_preview_popup)

        self.btn_reference = ttk.Button(row, text="Set reference",
                                        command=self._select_region, width=13)
        self.btn_reference.pack(side="left", padx=2)

        self.btn_resnap = ttk.Button(row, text="↺", command=self._save_reference,
                                     state="disabled", width=3)
        self.btn_resnap.pack(side="left", padx=(2, 10))

        ttk.Label(row, text="Saved:").pack(side="left", padx=(0, 4))
        self._preset_var = tk.StringVar()
        self._preset_combo = ttk.Combobox(row, textvariable=self._preset_var,
                                          state="readonly", width=16)
        self._preset_combo.pack(side="left")
        self._refresh_preset_combo()
        ttk.Button(row, text="Load", command=self._load_preset,
                   width=6).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="Save region", command=self._save_preset,
                   width=11).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="Delete", command=self._delete_preset,
                   width=7).pack(side="left", padx=(4, 0))

    def _build_hall_readout(self, page):
        """One line saying what the two hall values are doing right now."""
        row = ttk.Frame(page)
        row.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 2))
        self._hall_readout = tk.StringVar(value="not read yet — press Read now")
        ttk.Label(row, textvariable=self._hall_readout,
                  wraplength=520, justify="left").pack(side="left")
        ttk.Button(row, text="Read now", width=10,
                   command=self._read_hall_now).pack(side="right")

    def _refresh_cond_tree(self):
        keep = {}
        for kind, tv in self._cond_trees.items():
            sel = tv.selection()
            keep[kind] = sel[0] if sel else None
            tv.delete(*tv.get_children())
        for i, cond in enumerate(self._conditions):
            tv = self._cond_trees.get(cond["kind"])
            if tv is None:
                continue
            tv.insert("", "end", iid=str(i),
                      values=("☑" if cond["enabled"] else "☐",
                              cond["name"],
                              self._cond_summary(cond)))
        for kind, tv in self._cond_trees.items():
            if keep[kind] is not None and keep[kind] in tv.get_children():
                tv.selection_set(keep[kind])
        self._rebuild_cond_badges()
        # An enabled condition is something to watch, so the circle must go
        # orange (ready) even with no ad-hoc region set.
        self._update_circle()

    def _selected_condition(self):
        """The condition selected on the tab that is open, else on any tab."""
        order = list(self._cond_trees.values())
        try:
            current = self._cond_frame.index(self._cond_frame.select())
            order.insert(0, order.pop(current))
        except Exception:
            pass
        for tv in order:
            sel = tv.selection()
            if not sel:
                continue
            idx = int(sel[0])
            if 0 <= idx < len(self._conditions):
                return idx, self._conditions[idx]
        return None, None

    def _on_cond_click(self, event):
        """A click in the "On" column switches that condition on or off."""
        tv = event.widget
        if tv.identify_region(event.x, event.y) != "cell" or tv.identify_column(event.x) != "#1":
            return None
        iid = tv.identify_row(event.y)
        if not iid:
            return None
        cond = self._conditions[int(iid)]
        cond["enabled"] = not cond["enabled"]
        self._save_conditions()
        self._refresh_cond_tree()
        return "break"

    def _delete_condition(self):
        idx, cond = self._selected_condition()
        if cond is None:
            messagebox.showwarning("No condition", "Select a condition first.", parent=self)
            return
        if not messagebox.askyesno("Delete condition",
                                   f'Delete "{cond["name"]}"?', parent=self):
            return
        del self._conditions[idx]
        self._save_conditions()
        self._refresh_cond_tree()

    def _resnap_condition(self):
        """Re-take the reference picture of whichever area the selection owns.

        A screen condition has one; a value condition has one only when a screen
        area was attached to it."""
        idx, cond = self._selected_condition()
        if cond is None:
            messagebox.showwarning("No condition", "Select a condition first.", parent=self)
            return
        if cond["kind"] == "screen":
            region, ref_key, img_key = cond.get("region"), "reference", "_ref_img"
        elif cond["kind"] == "pv" and cond.get("gate_region"):
            region, ref_key, img_key = cond["gate_region"], "gate_reference", "_gate_img"
        else:
            messagebox.showwarning(
                "No picture to take",
                "This condition has no screen area. Pick a screen area "
                "condition, or a value condition with an area attached.",
                parent=self)
            return
        img = self._grab_rect(region)
        if img is None:
            return
        encoded = self._encode_reference(img)
        if encoded is None:
            return
        cond[ref_key], cond[img_key] = encoded, img
        self._save_conditions()
        self._refresh_cond_tree()
        self.status_var.set(f'Reference taken for "{cond["name"]}".')

    def _build_area_block(self, win, parent, draft, keys, start_row, *, thumb_attr):
        """Monitor + sensitivity + Draw area + Take reference + a thumbnail.

        Used twice: by a screen condition, for its own area, and by a value
        condition, for the area attached to it. `keys` says which draft keys it
        reads and writes, so one dict can hold both without a collision.

        Returns (commit, next_row); `commit` writes the two dropdown values into
        the draft and is called from Save.
        """
        mon_key, region_key, thr_key, ref_key, img_key = keys
        r = start_row

        ttk.Label(parent, text="Monitor:").grid(row=r, column=0, sticky="w",
                                                padx=(0, 6), pady=3)
        mon_combo = ttk.Combobox(parent, values=self._monitor_labels,
                                 state="readonly", width=12)
        mon_combo.current(min(max(0, int(draft.get(mon_key) or 0)),
                              len(self._monitor_labels) - 1))
        mon_combo.grid(row=r, column=1, sticky="w", pady=3)

        thr_var = tk.DoubleVar(value=draft.get(thr_key) or CHANGE_THRESHOLD)
        ttk.Label(parent, text="Fires on a change over:").grid(row=r + 1, column=0,
                                                               sticky="w", padx=(0, 6), pady=3)
        ttk.Spinbox(parent, from_=0.5, to=50.0, increment=0.5, textvariable=thr_var,
                    width=6, format="%.1f").grid(row=r + 1, column=1, sticky="w", pady=3)

        ttk.Label(parent, text="Area:").grid(row=r + 2, column=0, sticky="w",
                                             padx=(0, 6), pady=3)
        area_lbl = ttk.Label(parent, text="")
        area_lbl.grid(row=r + 2, column=1, sticky="w", pady=3)

        thumb_lbl = tk.Label(parent, relief="solid", bd=1)
        thumb_lbl.grid(row=r + 3, column=1, sticky="w", pady=(3, 6))

        def refresh():
            region = draft.get(region_key)
            area_lbl.configure(
                text=(f"{region[2] - region[0]} x {region[3] - region[1]} px "
                      f"at {region[0]}, {region[1]}" if region else "not drawn yet"))
            img = draft.get(img_key)
            if img is None:
                setattr(win, thumb_attr, None)
                thumb_lbl.configure(image="", text="no reference taken",
                                    width=20, height=2)
            else:
                thumb = img.copy()
                thumb.thumbnail((300, 140), resample=0)
                setattr(win, thumb_attr, ImageTk.PhotoImage(thumb))
                thumb_lbl.configure(image=getattr(win, thumb_attr), text="",
                                    width=0, height=0)

        def take_reference():
            if not draft.get(region_key):
                messagebox.showwarning("No area", "Draw the area first.", parent=win)
                return
            img = self._grab_rect(draft[region_key])
            if img is None:
                return
            encoded = self._encode_reference(img)
            if encoded is None:
                return
            draft[ref_key], draft[img_key] = encoded, img
            refresh()

        def got_area(bbox):
            draft[region_key] = [int(v) for v in bbox]
            # Drawing a new area invalidates the old picture, so take a new one
            # straight away — that is what the drawing was for.
            self.after(300, take_reference)

        def draw_area():
            win.withdraw()
            self._open_region_selector(mon_combo.current(), got_area)

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=r + 4, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Button(btn_row, text="Draw area", width=12, command=draw_area).pack(side="left")
        ttk.Button(btn_row, text="Take reference", width=15,
                   command=take_reference).pack(side="left", padx=(6, 0))
        refresh()

        def commit():
            draft[mon_key] = mon_combo.current()
            try:
                draft[thr_key] = max(0.1, float(thr_var.get()))
            except Exception:
                draft[thr_key] = CHANGE_THRESHOLD

        return commit, r + 5

    def _edit_condition(self, new=None):
        """Add or change one condition. Nothing is written until Save."""
        if self._cond_editor is not None and self._cond_editor.winfo_exists():
            self._cond_editor.destroy()
            self._cond_editor = None

        if new:
            idx = None
            draft = {"kind": new, "name": "", "enabled": True, "message": ""}
            if new == "screen":
                draft.update(monitor=self._selected_monitor_idx.get(), region=None,
                             threshold=float(self.change_threshold.get()),
                             reference=None, _ref_img=None)
            elif new == "hall":
                draft.update(hall=None, pss=None, moving_grace_s=HALL_MOVING_GRACE_S)
            else:
                draft.update(pv=COND_DEFAULT_PV, warn=None, trip=None, unit="")
        else:
            idx, cond = self._selected_condition()
            if cond is None:
                messagebox.showwarning("No condition", "Select a condition first.", parent=self)
                return
            draft = dict(cond)

        win = tk.Toplevel(self)
        win.title({"screen": "Screen area condition",
                   "hall": "Where the beam goes",
                   "pv": "Value condition"}[draft["kind"]])
        win.transient(self)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        self._cond_editor = win

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)

        name_var = tk.StringVar(value=draft["name"])
        msg_var = tk.StringVar(value=draft["message"])

        ttk.Label(frame, text="Name:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(frame, textvariable=name_var, width=38).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="Say when it fires:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(frame, textvariable=msg_var, width=38).grid(row=1, column=1, sticky="w", pady=3)

        if draft["kind"] == "screen":
            commit_area, last_row = self._build_area_block(
                win, frame, draft, _SCREEN_AREA_KEYS, 2, thumb_attr="_thumb")
        elif draft["kind"] == "hall":
            hall_var = tk.StringVar(value=_hall_name(draft.get("hall"))
                                    if draft.get("hall") is not None else _NO_CHECK)
            pss_var = tk.StringVar(value=_pss_name(draft.get("pss"))
                                   if draft.get("pss") is not None else _NO_CHECK)
            grace_var = tk.DoubleVar(value=draft.get("moving_grace_s", HALL_MOVING_GRACE_S))

            ttk.Label(frame, text="Shooting into:").grid(row=2, column=0, sticky="w",
                                                         padx=(0, 6), pady=3)
            ttk.Combobox(frame, textvariable=hall_var, state="readonly", width=22,
                         values=[_NO_CHECK] + [label for num, label in _HALLS
                                               if num != HALL_MOVING_VALUE]
                         ).grid(row=2, column=1, sticky="w", pady=3)

            ttk.Label(frame, text="PSS state:").grid(row=3, column=0, sticky="w",
                                                     padx=(0, 6), pady=3)
            ttk.Combobox(frame, textvariable=pss_var, state="readonly", width=22,
                         values=[_NO_CHECK] + [label for _num, label in _PSS_STATES]
                         ).grid(row=3, column=1, sticky="w", pady=3)

            ttk.Label(frame, text="Switchyard may move for (s):").grid(
                row=4, column=0, sticky="w", padx=(0, 6), pady=3)
            ttk.Spinbox(frame, from_=0, to=600, increment=10, textvariable=grace_var,
                        width=6, format="%.0f").grid(row=4, column=1, sticky="w", pady=3)

            hall_lbl = ttk.Label(frame, text="", wraplength=340, justify="left")
            hall_lbl.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 4))

            def hall_read_now():
                hall_lbl.configure(text="reading…")

                def show(fate, pss):
                    if hall_lbl.winfo_exists():
                        hall_lbl.configure(text=self._hall_readout_text(fate, pss))

                def worker():
                    fate, pss = self._read_hall_pvs()
                    self.after(0, show, fate, pss)

                threading.Thread(target=worker, daemon=True).start()

            btn_row = ttk.Frame(frame)
            btn_row.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 4))
            ttk.Button(btn_row, text="Read now", width=12,
                       command=hall_read_now).pack(side="left")
            last_row = 7
        else:
            pv_var = tk.StringVar(value=draft.get("pv", ""))
            warn_var = tk.StringVar(value="" if draft.get("warn") is None else f"{draft['warn']:g}")
            trip_var = tk.StringVar(value="" if draft.get("trip") is None else f"{draft['trip']:g}")
            unit_var = tk.StringVar(value=draft.get("unit", ""))

            ttk.Label(frame, text="Value (PV):").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
            ttk.Entry(frame, textvariable=pv_var, width=38).grid(row=2, column=1, sticky="w", pady=3)

            ttk.Label(frame, text="Warn over (empty = off):").grid(row=3, column=0, sticky="w",
                                                                   padx=(0, 6), pady=3)
            ttk.Entry(frame, textvariable=warn_var, width=10).grid(row=3, column=1, sticky="w", pady=3)

            ttk.Label(frame, text="Trip over (empty = off):").grid(row=4, column=0, sticky="w",
                                                                   padx=(0, 6), pady=3)
            ttk.Entry(frame, textvariable=trip_var, width=10).grid(row=4, column=1, sticky="w", pady=3)

            ttk.Label(frame, text="Unit:").grid(row=5, column=0, sticky="w", padx=(0, 6), pady=3)
            ttk.Entry(frame, textvariable=unit_var, width=10).grid(row=5, column=1, sticky="w", pady=3)

            read_lbl = ttk.Label(frame, text="", wraplength=320, justify="left")
            read_lbl.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 4))

            def read_now():
                pv_name = pv_var.get().strip()
                if not pv_name:
                    return
                read_lbl.configure(text="reading…")

                def show(values):
                    if not read_lbl.winfo_exists():
                        return
                    if not values:
                        read_lbl.configure(text="nothing archived in the last minute")
                    else:
                        read_lbl.configure(
                            text=f"last minute — now {values[-1]:g}, "
                                 f"highest {max(values):g}, lowest {min(values):g}")

                def worker():
                    end_ns = int(time.time() * 1e9)
                    samples = self._fetch_samples(pv_name, end_ns - 60 * 1_000_000_000, end_ns)
                    self.after(0, show, [v for _t, v in samples])

                threading.Thread(target=worker, daemon=True).start()

            btn_row = ttk.Frame(frame)
            btn_row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 4))
            ttk.Button(btn_row, text="Read now", width=12, command=read_now).pack(side="left")

            # The optional second half: a screen area that must keep matching its
            # own picture. Both halves have to hold; either one failing trips.
            gate_on = tk.BooleanVar(value=bool(draft.get("gate_region")))
            ttk.Separator(frame, orient="horizontal").grid(row=8, column=0, columnspan=2,
                                                           sticky="ew", pady=(8, 0))
            gate_box = ttk.Frame(frame, padding=(0, 6, 0, 0))
            gate_box.grid(row=9, column=0, columnspan=2, sticky="ew")
            ttk.Checkbutton(gate_box, variable=gate_on,
                            text="Also require a screen area",
                            command=lambda: toggle_gate()).grid(row=0, column=0, columnspan=2,
                                                                sticky="w", pady=(0, 4))
            gate_inner = ttk.Frame(gate_box)
            gate_inner.grid(row=1, column=0, columnspan=2, sticky="ew")
            commit_gate, _ = self._build_area_block(
                win, gate_inner, draft, _GATE_AREA_KEYS, 0, thumb_attr="_gate_thumb")

            def toggle_gate():
                if gate_on.get():
                    gate_inner.grid()
                else:
                    gate_inner.grid_remove()

            toggle_gate()
            last_row = 10

        def save():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("No name", "Give the condition a name.", parent=win)
                return
            draft["name"] = name
            draft["message"] = msg_var.get().strip()
            if draft["kind"] == "screen":
                if not draft.get("region"):
                    messagebox.showwarning("No area", "Draw the area first.", parent=win)
                    return
                commit_area()
            elif draft["kind"] == "hall":
                draft["hall"] = _value_for_label(_HALLS, hall_var.get())
                draft["pss"] = _value_for_label(_PSS_STATES, pss_var.get())
                try:
                    draft["moving_grace_s"] = max(0.0, float(grace_var.get()))
                except Exception:
                    draft["moving_grace_s"] = HALL_MOVING_GRACE_S
                if draft["hall"] is None and draft["pss"] is None and not messagebox.askyesno(
                        "Nothing to check",
                        "Neither the hall nor the PSS state is set, so this "
                        "condition can never fire. Save it anyway?", parent=win):
                    return
            else:
                pv_name = pv_var.get().strip()
                if not pv_name:
                    messagebox.showwarning("No value", "Type the PV name.", parent=win)
                    return
                draft["pv"] = pv_name
                draft["unit"] = unit_var.get().strip()
                draft["warn"] = _parse_level(warn_var.get())
                draft["trip"] = _parse_level(trip_var.get())
                if gate_on.get():
                    if not draft.get("gate_region"):
                        messagebox.showwarning("No area", "Draw the screen area first, "
                                                          "or switch it off.", parent=win)
                        return
                    commit_gate()
                else:
                    for key in ("gate_region", "gate_reference", "gate_threshold",
                                "gate_monitor", "_gate_img"):
                        draft.pop(key, None)
                if draft["trip"] is None and not draft.get("gate_region") and not messagebox.askyesno(
                        "No trip level",
                        "Without a trip level and without a screen area this "
                        "condition can never raise the alarm. Save it anyway?",
                        parent=win):
                    return
            if "_uid" not in draft:
                draft["_uid"] = self._next_cond_uid()
            if idx is None:
                self._conditions.append(draft)
            else:
                self._conditions[idx] = draft
            self._save_conditions()
            self._refresh_cond_tree()
            win.destroy()

        ok_row = ttk.Frame(frame)
        ok_row.grid(row=last_row, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(ok_row, text="Save", width=8, command=save).pack(side="left")
        ttk.Button(ok_row, text="Cancel", width=8,
                   command=win.destroy).pack(side="left", padx=(6, 0))

        win.update_idletasks()
        self._place_popup(win, self.winfo_rootx() + 30, self.winfo_rooty() + 30)

    # ------------------------------------------------------------------
    # Region presets
    # ------------------------------------------------------------------
    def _load_presets(self):
        if self._presets_path.exists():
            try:
                data = json.loads(self._presets_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _save_presets_file(self):
        self._presets_path.write_text(
            json.dumps(self._presets, indent=2), encoding="utf-8")

    def _save_pv_thresholds(self):
        thr = {}
        for i, (channel, *_rest) in enumerate(_PV_MONITORS):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            try:
                thr[_pv_key(channel)] = {
                    "lo_red":    lo_r_var.get(),
                    "lo_orange": lo_o_var.get(),
                    "hi_orange": hi_o_var.get(),
                    "hi_red":    hi_r_var.get(),
                }
            except tk.TclError:
                pass
        self._presets["pv_thresholds"] = thr
        self._save_presets_file()

    def _save_flash_settings(self):
        self._presets["flash_mode"] = self.flash_mode.get()
        self._presets["color_cycle"] = self.color_cycle.get()
        try:
            self._presets["flash_interval"] = round(self.flash_interval.get(), 2)
        except tk.TclError:
            pass   # half-typed value in the spinbox — keep the stored one
        self._presets["image_file"] = self.image_file.get()
        try:
            self._presets["sound_leadin"] = int(self.sound_leadin.get())
        except tk.TclError:
            pass   # half-typed value in the spinbox — keep the stored one
        self._presets["sound_device"] = self.sound_device.get()
        self._save_presets_file()

    def _preset_names(self):
        return sorted(k for k in self._presets if k not in _RESERVED_PRESET_KEYS)

    def _refresh_preset_combo(self):
        names = self._preset_names()
        self._preset_combo["values"] = names
        if names and self._preset_var.get() not in names:
            self._preset_var.set(names[0])
        elif not names:
            self._preset_var.set("")

    def _save_preset(self):
        if not self.region:
            messagebox.showwarning("No region", "Select a region first.", parent=self)
            return
        name = simpledialog.askstring("Save preset", "Preset name:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self._presets:
            if not messagebox.askyesno("Overwrite?",
                                       f'Preset "{name}" already exists. Overwrite?',
                                       parent=self):
                return
        existing = self._presets.get(name)
        if isinstance(existing, dict):
            existing["region"] = list(self.region)
        else:
            self._presets[name] = list(self.region)
        self._save_presets_file()
        self._refresh_preset_combo()
        self._preset_var.set(name)

    def _load_preset(self):
        name = self._preset_var.get()
        if not name or name not in self._presets:
            messagebox.showwarning("No preset", "Select a preset first.", parent=self)
            return
        if self.tracking:
            self._stop_tracking()
        data = self._presets[name]
        if isinstance(data, list):
            coords = data
            preset_geom = None
            preset_img_geom = None
        elif isinstance(data, dict):
            coords = data.get("region") or []
            preset_geom = data.get("window_geometry")
            preset_img_geom = data.get("image_geometry")
        else:
            return
        if not coords:
            return
        self._region_selected(tuple(coords))
        self._set_ui_visible(True)
        geom = preset_geom or self._presets.get("window_geometry")
        if geom:
            self._apply_geometry(self, self._at_least_requested(geom))
        self._image_geometry = (preset_img_geom or self._presets.get("image_geometry")
                                or self._image_geometry)

    def _delete_preset(self):
        name = self._preset_var.get()
        if not name or name not in self._presets:
            messagebox.showwarning("No preset", "Select a preset first.", parent=self)
            return
        if not messagebox.askyesno("Delete preset",
                                   f'Delete preset "{name}"?', parent=self):
            return
        del self._presets[name]
        self._save_presets_file()
        self._refresh_preset_combo()

    # ------------------------------------------------------------------
    # Settings popup
    # ------------------------------------------------------------------
    def _toggle_settings_popup(self):
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None
            return
        self._build_settings_popup()

    def _build_settings_popup(self):
        popup = tk.Toplevel(self)
        popup.title("Settings")
        popup.resizable(False, False)
        popup.transient(self)
        self._settings_popup = popup

        frame = ttk.Frame(popup, padding=8)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        # Detection
        thr_frame = ttk.LabelFrame(frame, text="Detection")
        thr_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))

        ttk.Label(thr_frame, text="Threshold:").grid(row=0, column=0, padx=(6,2), pady=6)
        ttk.Spinbox(thr_frame, from_=0.5, to=50.0, increment=0.5,
                    textvariable=self.change_threshold,
                    width=6, format="%.1f").grid(row=0, column=1, padx=(0,6), pady=6)

        ttk.Label(thr_frame, text="Flash color:").grid(row=1, column=0, padx=(6,2), pady=(0,6))
        self._flash_color_btn = tk.Button(thr_frame, bg=self.flash_color.get(), width=3,
                                          relief="groove", command=self._pick_flash_color)
        self._flash_color_btn.grid(row=1, column=1, sticky="w", padx=(0,6), pady=(0,6))

        self._COLOR_CYCLE_LABELS = {
            "fixed":            "Picked color",
            "rainbow_blink":    "New color each blink",
            "rainbow_spectrum": "Spectrum blue-to-red, blinks",
            "rainbow_wave":     "Wave from center, no blink",
        }
        _cyc_to_label = self._COLOR_CYCLE_LABELS
        _label_to_cyc = {v: k for k, v in _cyc_to_label.items()}

        ttk.Label(thr_frame, text="Flash colors:").grid(row=2, column=0, padx=(6,2), pady=(0,6))
        cyc_combo = ttk.Combobox(thr_frame, state="readonly", width=28,
                                 values=list(_cyc_to_label.values()))
        cyc_combo.set(_cyc_to_label.get(self.color_cycle.get(), "Picked color"))
        cyc_combo.grid(row=2, column=1, sticky="w", padx=(0,6), pady=(0,6))
        cyc_combo.bind("<<ComboboxSelected>>",
                       lambda e: self.color_cycle.set(_label_to_cyc.get(cyc_combo.get(), "fixed")))

        ttk.Label(thr_frame, text="Blink speed (s):").grid(row=3, column=0, padx=(6,2), pady=(0,6))
        ttk.Spinbox(thr_frame, from_=0, to=3.0, increment=0.05,
                    textvariable=self.flash_interval,
                    width=6, format="%.2f").grid(row=3, column=1, sticky="w", padx=(0,6), pady=(0,6))

        ttk.Label(thr_frame, text="Flash duration (s):").grid(row=4, column=0, padx=(6,2), pady=(0,6))
        ttk.Spinbox(thr_frame, from_=0, to=60, increment=0.5,
                    textvariable=self.flash_duration,
                    width=6, format="%.1f").grid(row=4, column=1, sticky="w", padx=(0,6), pady=(0,6))

        # Flash mode: full-background color, or the template image alone
        self._FLASH_MODE_LABELS = {
            "color":     "Background color",
            "image":     "Image only",
        }
        _mode_to_label = self._FLASH_MODE_LABELS
        _label_to_mode = {v: k for k, v in _mode_to_label.items()}

        ttk.Label(thr_frame, text="Flash mode:").grid(row=5, column=0, padx=(6,2), pady=(0,6))
        mode_combo = ttk.Combobox(thr_frame, state="readonly", width=28,
                                  values=list(_mode_to_label.values()))
        mode_combo.set(_mode_to_label.get(self.flash_mode.get(), "Background color"))
        mode_combo.grid(row=5, column=1, sticky="w", padx=(0,6), pady=(0,6))
        mode_combo.bind("<<ComboboxSelected>>",
                        lambda e: self.flash_mode.set(_label_to_mode.get(mode_combo.get(), "color")))

        ttk.Label(thr_frame, text="Image:").grid(row=6, column=0, padx=(6,2), pady=(0,6))
        img_combo = ttk.Combobox(thr_frame, textvariable=self.image_file,
                                 state="readonly", width=28, values=self._image_files)
        if self.image_file.get() in self._image_files:
            img_combo.current(self._image_files.index(self.image_file.get()))
        img_combo.grid(row=6, column=1, sticky="w", padx=(0,6), pady=(0,6))

        ttk.Label(thr_frame,
                  text="Align the image via \"Set image window\" below.",
                  foreground="gray", wraplength=220, justify="left").grid(
            row=7, column=0, columnspan=2, padx=6, pady=(0, 6), sticky="w")

        # Sound
        sound_frame = ttk.LabelFrame(frame, text="Sound")
        sound_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))

        ttk.Checkbutton(sound_frame, text="Play sound on change",
                        variable=self.sound_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 2))

        ttk.Label(sound_frame, text="Freq (Hz):").grid(row=1, column=0, padx=(6,2), pady=(0,4))
        ttk.Spinbox(sound_frame, from_=200, to=4000, increment=100,
                    textvariable=self.sound_freq, width=6).grid(row=1, column=1, padx=(0,6), pady=(0,4))

        ttk.Label(sound_frame, text="Duration (ms):").grid(row=2, column=0, padx=(6,2), pady=(0,4))
        ttk.Spinbox(sound_frame, from_=50, to=3000, increment=50,
                    textvariable=self.sound_duration, width=6).grid(row=2, column=1, padx=(0,6), pady=(0,4))

        ttk.Label(sound_frame, text="Sound file:").grid(row=3, column=0, padx=(6,2), pady=(0,6))
        self._sound_combo = ttk.Combobox(sound_frame, textvariable=self.sound_file,
                                         values=self._sound_files, state="readonly", width=12)
        if self.sound_file.get() in self._sound_files:
            self._sound_combo.current(self._sound_files.index(self.sound_file.get()))
        self._sound_combo.grid(row=3, column=1, padx=(0,6), pady=(0,6))

        ttk.Label(sound_frame, text="Bluetooth run-up (ms):").grid(
            row=4, column=0, padx=(6,2), pady=(0,4))
        ttk.Spinbox(sound_frame, from_=0, to=3000, increment=100,
                    textvariable=self.sound_leadin, width=6).grid(
            row=4, column=1, padx=(0,6), pady=(0,4))

        # The device names run to 31 characters, so they get a row of their own
        # instead of being squeezed into the narrow right-hand column.
        sound_frame.columnconfigure(1, weight=1)
        ttk.Label(sound_frame, text="Play on:").grid(
            row=5, column=0, columnspan=2, padx=6, pady=(2,0), sticky="w")
        self._device_combo = ttk.Combobox(sound_frame, textvariable=self.sound_device,
                                          state="readonly", width=24)
        # Filled when the list is dropped down, not once at start-up: a
        # Bluetooth speaker that connects later would otherwise never show up.
        self._device_combo["postcommand"] = self._refresh_device_combo
        self._device_combo.grid(row=6, column=0, columnspan=2,
                                padx=6, pady=(0,4), sticky="ew")
        self._refresh_device_combo()

        ttk.Button(sound_frame, text="Test sound",
                   command=lambda: self._play_sound(report=True)).grid(
            row=7, column=0, columnspan=2, padx=6, pady=(0,6), sticky="ew")

        # Window position & size — two independent windows
        win_frame = ttk.LabelFrame(frame, text="Window position & size")
        win_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        win_frame.columnconfigure(0, weight=1)
        ttk.Label(win_frame,
                  text="Control panel (circle, presets, alerts):").grid(
            row=0, column=0, padx=(6, 4), pady=(4, 2), sticky="w")
        ttk.Button(win_frame, text="Set control window",
                   command=self._start_control_window_recording).grid(
            row=0, column=1, padx=(0, 6), pady=(4, 2))
        ttk.Label(win_frame,
                  text="Image window (where the alarm image flashes):").grid(
            row=1, column=0, padx=(6, 4), pady=(2, 4), sticky="w")
        ttk.Button(win_frame, text="Set image window",
                   command=self._start_image_window_recording).grid(
            row=1, column=1, padx=(0, 6), pady=(2, 4))

        # PV Limits table
        pv_lim_frame = ttk.LabelFrame(frame, text="PV Limits")
        pv_lim_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Column headers
        ttk.Label(pv_lim_frame, text="Channel",
                  font=("Segoe UI", 8, "bold")).grid(row=1, column=0, padx=(6, 8), pady=(0, 2), sticky="w")
        for _col, (_htxt, _hbg) in enumerate(
            [("Lolo", "#aa1c00"), ("Lo", "#b85a00"), ("Hi", "#b85a00"), ("HiHi", "#aa1c00")],
            start=1,
        ):
            tk.Label(pv_lim_frame, text=f" {_htxt} ", bg=_hbg, fg="white",
                     font=("Segoe UI", 8, "bold")).grid(row=1, column=_col, padx=3, pady=(0, 2))

        ttk.Separator(pv_lim_frame, orient="horizontal").grid(
            row=2, column=0, columnspan=5, sticky="ew", padx=4, pady=(0, 2))

        # Data rows
        for i, (_channel, label, default_lo_o, _lo_r, _hi_o, _hi_r, unit) in enumerate(_PV_MONITORS):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            # One arrow click steps a tenth on every channel; hundredths were
            # too fine to walk a limit anywhere with the arrows.
            inc = 0.1
            fmt = "%.2f" if abs(default_lo_o) < 10 else "%.1f"
            row_idx = i + 3
            ttk.Label(pv_lim_frame, text=f"{label} ({unit})", anchor="w").grid(
                row=row_idx, column=0, padx=(6, 8), pady=(2, 4), sticky="w")
            for _col, var in enumerate([lo_r_var, lo_o_var, hi_o_var, hi_r_var], start=1):
                ttk.Spinbox(pv_lim_frame, from_=-9999, to=9999, increment=inc,
                            textvariable=var, width=7, format=fmt).grid(
                    row=row_idx, column=_col, padx=3, pady=(2, 4))

        # Below the Settings button, but pulled back if the panel would hang off
        # the screen — it is tall, so the bottom edge is what usually would.
        self._place_popup(popup,
                          self.winfo_rootx() + 10,
                          self.winfo_rooty() + self._top_frame.winfo_height() + 10)

        # Close when clicking on the main window background (not on popup or its dropdowns)
        self._settings_close_bind = self.bind("<Button-1>", self._on_main_click_close_settings, "+")

    def _on_main_click_close_settings(self, _event):
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None
        try:
            self.unbind("<Button-1>", self._settings_close_bind)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Identify monitors
    # ------------------------------------------------------------------
    def _identify_monitors(self):
        labels = []
        for i, m in enumerate(self._monitors):
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.attributes("-alpha", 0.85)
            w.configure(bg="#111111")
            lbl = tk.Label(w, text=f"Monitor {i+1}\n{m.width}×{m.height}",
                           font=("Arial", 36, "bold"), fg="white", bg="#111111",
                           padx=30, pady=20)
            lbl.pack()
            w.update_idletasks()
            ww = w.winfo_reqwidth()
            wh = w.winfo_reqheight()
            self._place_popup(w,
                              m.x + (m.width - ww) // 2,
                              m.y + (m.height - wh) // 2)
            labels.append(w)
        self.after(2500, lambda: [w.destroy() for w in labels])

    # ------------------------------------------------------------------
    # Region selection
    # ------------------------------------------------------------------
    def _open_region_selector(self, monitor_idx, callback):
        """Dim the chosen screen and let the user drag out a rectangle."""
        mon = self._monitors[min(max(0, monitor_idx), len(self._monitors) - 1)]
        self._selector_open = True
        self.withdraw()
        self.after(150, lambda: RegionSelector(self, mon, callback))

    def _select_region(self):
        self._open_region_selector(self._selected_monitor_idx.get(), self._region_selected)

    def _on_selector_closed(self):
        if self._selector_open:
            self._selector_open = False
            self.after(50, self.deiconify)
            # The condition editor hides itself while the screen is being
            # dimmed; bring it back even when the selection was cancelled.
            if self._cond_editor is not None and self._cond_editor.winfo_exists():
                self.after(60, self._cond_editor.deiconify)

    def _region_selected(self, bbox):
        if bbox:
            self.region = bbox
            self.reference = None
            self.tracking = False
            self.changed = False
            self.btn_resnap.config(state="normal")

            self.status_var.set(f"Region: {bbox}  — set reference.")
            self.after(500, self._update_preview)
            self._save_reference()
            self._update_circle()
        else:
            self.status_var.set("Selection cancelled.")

    # ------------------------------------------------------------------
    # Reference
    # ------------------------------------------------------------------
    def _save_reference(self):
        if not self.region:
            return
        img = self._grab()
        if img is None:
            return
        self.reference = img.copy()
        self.tracking = False
        self.changed = False

        self.status_var.set("Reference saved. Start tracking.")
        self._update_preview(img)
        self._update_circle()

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------
    def _toggle_tracking(self):
        if self.tracking:
            self._stop_tracking()
        elif self.changed:
            self._reset()
        else:
            self._start_tracking()

    def _start_tracking(self):
        active = self._enabled_conditions()
        if self.reference is None and not active:
            self.status_var.set("Nothing to watch — set a reference or add a condition.")
            return
        # Say out loud which conditions cannot fire, instead of letting the user
        # believe a switched-on condition is being watched.
        for cond in active:
            if cond["kind"] == "screen" and cond.get("_ref_img") is None:
                self._log_message(
                    f'{cond["name"]} — no reference taken, it cannot fire',
                    "Select the condition and press the arrow button to take its "
                    "reference picture.")
            elif cond["kind"] == "pv":
                if cond.get("gate_region") and cond.get("_gate_img") is None:
                    self._log_message(
                        f'{cond["name"]} — its screen area has no reference picture',
                        "Select the condition and press the arrow button to take "
                        "the picture of the area attached to it.")
                elif cond.get("trip") is None and not cond.get("gate_region"):
                    self._log_message(
                        f'{cond["name"]} — no trip level set, it cannot fire',
                        "Edit the condition and type the value it must not go over.")
            elif cond["kind"] == "hall" and cond.get("hall") is None and cond.get("pss") is None:
                self._log_message(
                    f'{cond["name"]} — neither hall nor PSS state set, it cannot fire',
                    "Edit the condition and choose where the beam is supposed to "
                    "go, or which PSS state is expected.")
        self._next_hall_read = 0.0      # read the hall values on the first poll
        self.tracking = True
        self.changed = False
        self.status_var.set("Tracking active…")
        self._poll()
        self._update_circle()
        self._set_ui_visible(False)
        self._pv_frame.grid()
        self._poll_pvs()

    def _stop_tracking(self):
        self.tracking = False
        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None
        self._is_flashing = False
        self._hide_image_win()
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
            self._pv_poll_job = None
        for row in self._pv_alert_rows:
            row._active = False
            row.place_forget()
        self._pv_frame.grid_remove()
        self.status_var.set("Tracking stopped.")
        self._update_circle()
        self._set_ui_visible(True)

    def _poll(self):
        if not self.tracking:
            return
        # The ad-hoc region drawn with "Set reference" (no name, no message).
        if self.reference is not None:
            img = self._grab()
            if img is not None:
                diff = self._picture_diff(img, self.reference)
                if diff is not None and diff > self.change_threshold.get():
                    self._on_change_detected(diff)
                    return
        # The named screen conditions, each against its own reference, and the
        # areas attached to value conditions — the same comparison either way.
        for cond, region, reference, threshold in self._watched_areas():
            img = self._grab_rect(region)
            if img is None:
                continue
            if img.size != reference.size:
                # Screen resolution or scaling changed under us: the pictures
                # cannot be compared, and pretending everything is fine would be
                # the one wrong answer.
                self._on_condition_failed(
                    cond, f"area is now {img.size[0]}x{img.size[1]} px, "
                          f"reference {reference.size[0]}x{reference.size[1]}")
                return
            diff = self._picture_diff(img, reference)
            if diff is not None and diff > threshold:
                self._on_condition_failed(cond, f"screen area changed (diff={diff:.1f})")
                return
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll)

    def _watched_areas(self):
        """Every (condition, region, reference, threshold) the poll compares.

        A screen condition contributes its own area; a value condition
        contributes the area attached to it, if it has one. Both must keep
        matching their picture — a picture that stopped matching is the
        condition no longer being met, whichever kind it belongs to.
        """
        out = []
        for cond in self._enabled_conditions("screen"):
            if cond.get("_ref_img") is not None and cond.get("region"):
                out.append((cond, cond["region"], cond["_ref_img"], cond["threshold"]))
        for cond in self._enabled_conditions("pv"):
            if cond.get("_gate_img") is not None and cond.get("gate_region"):
                out.append((cond, cond["gate_region"], cond["_gate_img"],
                            cond.get("gate_threshold", CHANGE_THRESHOLD)))
        return out

    def _on_change_detected(self, diff):
        self._raise_alarm(f"CHANGE DETECTED  (diff={diff:.1f})")

    def _on_condition_failed(self, cond, detail):
        """One condition stopped being met: say what it was and raise the alarm."""
        message = cond.get("message") or f'{cond["name"]} needs attention'
        self._set_cond_badge(cond, "#cc2200", f"⛔ {message}")
        self._log_message(f'{cond["name"]} — {message}  ({detail})')
        self._raise_alarm(f'{cond["name"]}: {detail}')

    def _raise_alarm(self, status):
        """Flash, sound, and stop watching so it cannot re-alarm by itself."""
        self.tracking = False
        self.changed = True
        # Tracking is over — stop polling PVs as well (no point fetching once
        # we're no longer watching; otherwise stray archiver errors keep logging).
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
            self._pv_poll_job = None
        self.status_var.set(status)
        self._start_flash()
        if self.sound_enabled.get():
            self.after(0, self._play_sound)
        self._update_circle()

    # ------------------------------------------------------------------
    # Image (flash) window
    # ------------------------------------------------------------------
    def _ensure_image_win(self):
        """Create (once) the borderless, chroma-keyed window used for the flash.

        It is a separate Toplevel from the control window so the alarm image can
        be positioned and sized independently. Kept withdrawn until a flash."""
        if self._image_win is not None and self._image_win.winfo_exists():
            return self._image_win
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=self._flash_key)
        try:
            win.attributes("-transparentcolor", self._flash_key)
        except Exception:
            pass
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        win.withdraw()
        self._image_win = win
        self._flash_overlay = tk.Frame(win, bg=self._flash_key)
        self._flash_img_label = tk.Label(self._flash_overlay, bd=0,
                                         highlightthickness=0, bg=self._flash_key)
        self._align_label = tk.Label(win, bd=0, highlightthickness=0, bg=self._chroma)
        return win

    @staticmethod
    def _frame_insets(win):
        """How far a window's title bar and border reach beyond its inside.

        Measured from the window itself once it is on screen, because the
        thickness depends on the Windows theme; a borderless window has none.
        """
        try:
            if win.wm_overrideredirect():
                return (0, 0, 0, 0)
            if not win.winfo_ismapped():
                return _DEFAULT_INSETS
            side = win.winfo_rootx() - win.winfo_x()
            top = win.winfo_rooty() - win.winfo_y()
            if not (0 <= side <= 200) or not (0 <= top <= 200):
                return _DEFAULT_INSETS
            return (side, top, side, side)
        except Exception:
            return _DEFAULT_INSETS

    def _at_least_requested(self, geom):
        """A remembered size for the control window, never smaller than what the
        widgets in it need.

        A position recorded before the Conditions panel existed is too short for
        it, and restoring it verbatim would cut the message log off at the bottom
        edge of the window. Only the size is touched; the position is kept.
        """
        match = re.match(r"^(\d+)x(\d+)(.*)$", geom or "")
        if not match:
            return geom          # position-only strings are left alone
        self.update_idletasks()
        width = max(int(match.group(1)), self.winfo_reqwidth())
        height = max(int(match.group(2)), self.winfo_reqheight())
        return f"{width}x{height}{match.group(3)}"

    def _clamp_geometry(self, geom, insets=None):
        """Correct a remembered window position so the window is fully visible.

        A remembered position can point at a monitor that is not there any
        more, or sit so close to an edge that most of the window would hang
        off it — both open the window where it cannot be seen or dragged back.
        A string without a position is returned unchanged (Windows then picks
        the spot)."""
        if not geom:
            return geom
        m = re.match(r"(?:(\d+)x(\d+))?\+(-?\d+)\+(-?\d+)$", geom)
        if not m:
            return geom
        gw, gh, x, y = m.groups()
        w = int(gw) if gw else max(1, self.winfo_width())
        h = int(gh) if gh else max(1, self.winfo_height())
        nx, ny = _fit_rect(int(x), int(y), w, h,
                           _DEFAULT_INSETS if insets is None else insets)
        prefix = f"{gw}x{gh}" if gw else ""
        return f"{prefix}+{nx}+{ny}"

    def _apply_geometry(self, win, geom, _retry=True):
        """Put a window back exactly where it was remembered, on screen.

        Remembered positions are the top-left of the window's *inside*, which
        is what is read back when the position is stored. Windows counts the
        position from the *outside* of the title bar instead, so writing a
        remembered position straight back moved the window down and to the
        right by the height of its title bar — every save-and-reload nudged it
        further, until it walked off the screen. Setting the position, then
        measuring where the window actually landed and correcting the
        difference, keeps normal and borderless windows on the same spot.
        """
        if not geom:
            return
        insets = self._frame_insets(win)
        geom = self._clamp_geometry(geom, insets=insets)
        m = re.match(r"(?:(\d+)x(\d+))?\+(-?\d+)\+(-?\d+)$", geom)
        try:
            if m is None:
                win.geometry(geom)
                return
            x, y = int(m.group(3)), int(m.group(4))
            size = f"{m.group(1)}x{m.group(2)}" if m.group(1) else ""
            mapped = win.winfo_ismapped()
            if mapped:
                win.geometry(geom)
            else:
                # Not drawn yet, so nothing can be measured: aim by the usual
                # title-bar thickness, which lands it right in almost every
                # case and leaves no visible jump when corrected below.
                win.geometry(f"{size}+{x - insets[0]}+{y - insets[1]}")
            win.update_idletasks()
            if mapped:
                dx = win.winfo_rootx() - x
                dy = win.winfo_rooty() - y
                if (dx or dy) and abs(dx) <= 200 and abs(dy) <= 200:
                    win.geometry(f"+{x - dx}+{y - dy}")
            if _retry:
                # One late check: the title bar is not always back in place the
                # instant it is asked for (leaving HUD mode, a window that was
                # still hidden), and until it is, there is nothing to measure.
                win.after(120, lambda: self._apply_geometry(win, geom, False))
        except Exception:
            pass

    def _place_popup(self, win, x, y):
        """Show a small window at a wanted spot, pulled back onto the screen if
        it would stick out (settings panel, preview, tooltips, dialogs).

        `x`/`y` are where the inside of the window is wanted; the title bar and
        border are added on top of that, because a position is always counted
        from the outside of the frame and the whole frame has to fit."""
        try:
            win.update_idletasks()
            left, top, right, bottom = self._frame_insets(win)
            w = max(1, win.winfo_reqwidth()) + left + right
            h = max(1, win.winfo_reqheight()) + top + bottom
            nx, ny = _fit_rect(x - left, y - top, w, h)
            win.geometry(f"+{nx}+{ny}")
        except Exception:
            pass

    def _resolve_image_geometry(self):
        """Geometry string for the image window; fall back to where the control
        window sits so behaviour matches the old single-window setup until the
        user sets a dedicated image window."""
        return (self._image_geometry
                or self._presets.get("image_geometry")
                or (f"{self.winfo_width()}x{self.winfo_height()}"
                    f"+{self.winfo_rootx()}+{self.winfo_rooty()}"))

    def _set_flash_alpha(self, value):
        """Fade the whole image window (1.0 = fully visible).

        Windows keeps a faded window clickable — only the chroma-keyed pixels let
        the mouse through — which is what makes the "off" half of the blink still
        respond to a click on the image."""
        win = self._image_win
        if win is None or not win.winfo_exists():
            return
        try:
            win.attributes("-alpha", value)
        except Exception:
            pass

    def _hide_image_win(self):
        self._set_flash_alpha(1.0)
        if self._flash_overlay is not None:
            self._flash_overlay.place_forget()
        if self._flash_img_label is not None:
            self._flash_img_label.place_forget()
        if self._image_win is not None and self._image_win.winfo_exists():
            self._image_win.withdraw()

    # ------------------------------------------------------------------
    # Flash and sound
    # ------------------------------------------------------------------
    def _start_flash(self):
        self._is_flashing = True
        d = self.flash_duration.get()
        self._flash_deadline = time.time() + d if d > 0 else float("inf")

        # Re-resolve the transparency key so a flash colour picked since the last
        # flash cannot collide with it.
        self._flash_key = self._resolve_flash_key()

        # Position/show the dedicated image window at its own geometry.
        win = self._ensure_image_win()
        win.overrideredirect(True)
        win.configure(bg=self._flash_key)
        try:
            win.attributes("-transparentcolor", self._flash_key)
        except Exception:
            pass
        # Borderless: no title bar to account for, so the remembered position
        # can be used as it is once it has been pulled onto a visible screen.
        win.geometry(self._clamp_geometry(self._resolve_image_geometry(),
                                          insets=(0, 0, 0, 0)))
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.update_idletasks()

        # Decide effective mode: fall back to "color" if image is requested but
        # no usable template is available.
        mode = self.flash_mode.get()
        self._flash_photo = None
        self._flash_size = (win.winfo_width(), win.winfo_height())
        self._flash_hue = 0.0
        self._flash_phase = 0.0
        self._blink_toggle_at = 0.0
        self._flash_col = self.flash_color.get()
        if mode == "image":
            size = self._flash_size
            self._flash_photo = self._make_flash_photo(size, key=self._flash_key)
            if self._flash_photo is None:
                self._log_message("Flash image not set or not found — using background color.")
                mode = "color"
        self._flash_active_mode = mode

        # First frame of a moving rainbow, so the window never shows up blank.
        if self._is_gradient_cycle():
            photo = self._gradient_photo(
                self._flash_size,
                "wave" if self.color_cycle.get() == "rainbow_wave" else "spectrum",
                0.0, key=self._flash_key,
                stencil=self._flash_stencil() if mode == "image" else None)
            if photo is not None:
                self._flash_photo = photo

        # Overlay frame covers the whole image window — avoids widget bg gaps.
        self._set_flash_alpha(1.0)
        self._flash_overlay.configure(bg=self._flash_key)
        self._flash_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self._flash_overlay.lift()
        win.bind("<Button-1>", self._on_any_click)
        self._flash_overlay.bind("<Button-1>", self._on_any_click)

        # The image label carries the picture in image mode, and also the painted
        # rainbow in background-colour mode (a plain colour needs no picture).
        if self._flash_photo is not None:
            self._flash_img_label.configure(image=self._flash_photo, bg=self._flash_key)
            self._flash_img_label.bind("<Button-1>", self._on_any_click)
            self._flash_img_label.place(x=0, y=0, relwidth=1, relheight=1)
            self._flash_img_label.lift()

        # Esc as a reliable dismiss (clicks only land on opaque areas). Needs focus
        # because the flashing window is borderless (overrideredirect).
        try:
            win.focus_force()
        except Exception:
            pass
        win.bind("<Escape>", self._on_any_click)
        self.bind("<Escape>", self._on_any_click)
        self._do_flash()

    def _do_flash(self):
        if time.time() > self._flash_deadline:
            self._is_flashing = False
            self._hide_image_win()
            self._set_ui_visible(True)
            return
        cycle = self.color_cycle.get()
        mode = getattr(self, "_flash_active_mode", "color")
        chroma = self._flash_key   # keyed areas are see-through via -transparentcolor
        moving = self._is_gradient_cycle()
        now = time.time()

        if moving:
            # Redrawn on its own fast tick so the colours travel smoothly; the
            # lit/unlit switch keeps to the configured blink speed. The wave mode
            # never goes dark — its travelling colours are the alarm.
            self._flash_phase += GRADIENT_STEP * self._motion_factor()
            if cycle == "rainbow_wave" or self._is_steady():
                self._flash_state = True
            elif now >= self._blink_toggle_at:
                self._flash_state = not self._flash_state
                self._blink_toggle_at = now + self._blink_interval_ms() / 1000.0
            kind = "wave" if cycle == "rainbow_wave" else "spectrum"
            stencil = self._flash_stencil() if mode == "image" else None
            photo = self._gradient_photo(self._flash_size, kind, self._flash_phase,
                                         key=chroma, stencil=stencil)
            if photo is not None:
                self._flash_photo = photo
            self._flash_overlay.configure(bg=chroma)
            self._flash_img_label.configure(image=self._flash_photo, bg=chroma)
            self._flash_img_label.place(x=0, y=0, relwidth=1, relheight=1)
            self._flash_img_label.lift()
            self._set_flash_alpha(1.0 if self._flash_state else FLASH_OFF_ALPHA)
            self._flash_job = self.after(GRADIENT_INTERVAL_MS, self._do_flash)
            return

        self._flash_state = True if self._is_steady() else not self._flash_state
        # One fresh hue per lit half of the blink.
        if self._flash_state:
            self._flash_col = self._step_flash_color()
        flash_col = self._flash_col
        if mode == "image" and cycle != "fixed":
            # Re-tint the silhouette; the stencil itself is cached, so this is cheap.
            photo = self._make_flash_photo(self._flash_size, key=chroma, color=flash_col)
            if photo is not None:
                self._flash_photo = photo
        if mode == "image":
            # Nothing but the image ever shows: surroundings stay transparent and
            # the colour-filled silhouette blinks on / off.
            #
            # The blink fades the whole window instead of removing the image,
            # because a removed (or fully transparent) image would also stop
            # taking clicks — the silhouette must stay clickable through both
            # halves of the blink so a click on it always returns to the menu.
            self._flash_overlay.configure(bg=chroma)
            self._flash_img_label.configure(image=self._flash_photo, bg=chroma)
            self._flash_img_label.place(x=0, y=0, relwidth=1, relheight=1)
            self._flash_img_label.lift()
            self._set_flash_alpha(1.0 if self._flash_state else FLASH_OFF_ALPHA)
        else:
            color = flash_col if self._flash_state else self._dim_color(flash_col)
            self._flash_overlay.configure(bg=color)
        self._flash_job = self.after(self._blink_interval_ms(), self._do_flash)

    def _is_gradient_cycle(self):
        """True for the modes that paint a moving rainbow instead of one colour."""
        return self.color_cycle.get() in ("rainbow_wave", "rainbow_spectrum")

    def _blink_interval_ms(self):
        """Configured blink speed in milliseconds (half a blink).

        Zero means "do not blink at all", which the callers handle separately, so
        here it only sets how often the flash is refreshed."""
        ms = self._blink_setting_ms()
        return FLASH_INTERVAL_MS if ms == 0 else max(50, ms)

    def _blink_setting_ms(self):
        """Raw blink-speed setting in milliseconds; 0 = stay lit."""
        try:
            return max(0, int(round(self.flash_interval.get() * 1000)))
        except tk.TclError:
            return FLASH_INTERVAL_MS

    def _is_steady(self):
        """True when the blink speed is set to zero: light on, no blinking."""
        return self._blink_setting_ms() == 0

    def _motion_factor(self):
        """How fast the moving rainbows travel, tied to the blink speed so one
        control covers every mode."""
        return max(0.25, min(4.0, FLASH_INTERVAL_MS / max(50, self._blink_interval_ms())))

    def _flash_stencil(self):
        """Cached silhouette of the selected template at the flash window size."""
        path = self._image_path(self.image_file.get())
        if path is None:
            return None
        try:
            return self._image_mask(path, (int(self._flash_size[0]), int(self._flash_size[1])))
        except Exception:
            return None

    def _step_flash_color(self):
        """Colour of the current blink: the picked one, or the next hue along."""
        cycle = self.color_cycle.get()
        if cycle == "fixed":
            return self.flash_color.get()
        self._flash_hue = (self._flash_hue + RAINBOW_BLINK_STEP_DEG) % 360.0
        r, g, b = colorsys.hsv_to_rgb(self._flash_hue / 360.0, 1.0, 1.0)
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

    def _dim_color(self, color, factor=0.25):
        """Darkened `color` for the unlit half of a blink.

        Never returns the transparency key: that would punch a hole in the window
        for half of every blink, and a click landing in it would fall through
        instead of dismissing the alarm."""
        r, g, b = self._color_rgb(color)
        out = "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))
        if self._color_rgb(out) == self._color_rgb(self._flash_key):
            return "#000000"
        return out

    def _refresh_device_combo(self):
        """Re-read the output devices, keeping a saved one that is still absent.

        The speaker chosen yesterday may not be connected yet today; dropping
        it from the list would silently reset the choice to the default."""
        names = list_output_devices()
        picked = self.sound_device.get()
        if picked and picked not in names:
            names.append(picked)
        self._device_combo["values"] = names

    def _leadin_ms(self):
        """Run-up of silence in front of every sound, clamped to something sane."""
        try:
            return max(0, min(5000, int(self.sound_leadin.get())))
        except (tk.TclError, ValueError):
            return SOUND_LEADIN_MS

    @staticmethod
    def _wav_bytes(frames, rate, channels, width):
        """Wrap raw samples as a WAV file held in memory."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(width)
            w.setframerate(rate)
            w.writeframes(frames)
        return buf.getvalue()

    def _tone_pcm(self):
        """Build the beep as real sound samples instead of asking for a beep.

        winsound.Beep() drives the motherboard beeper through the kernel and
        never reaches a Bluetooth speaker, which is why the lab stayed silent
        while the same script was audible on the PC's own output."""
        freq = max(50, min(10000, int(self.sound_freq.get())))
        duration_ms = max(20, min(10000, int(self.sound_duration.get())))
        n = int(SOUND_RATE * duration_ms / 1000)
        fade = min(n // 2, int(SOUND_RATE * 0.005))     # 5 ms, kills the click
        step = 2.0 * math.pi * freq / SOUND_RATE
        samples = array.array("h", bytes(2 * n))
        for i in range(n):
            level = 1.0
            if fade:
                if i < fade:
                    level = i / fade
                elif i > n - fade:
                    level = (n - i) / fade
            samples[i] = int(16000 * level * math.sin(step * i))
        pad = self._silence(self._leadin_ms(), SOUND_RATE, 1, 2)
        tail = self._silence(SOUND_TAIL_MS, SOUND_RATE, 1, 2)
        return pad + samples.tobytes() + tail, SOUND_RATE, 1, 2

    @staticmethod
    def _silence(ms, rate, channels, width):
        return bytes(int(rate * ms / 1000) * channels * width)

    def _file_pcm(self, path):
        """The chosen sound file with the silent run-up put in front of it."""
        with wave.open(str(path), "rb") as r:
            channels, width, rate = r.getnchannels(), r.getsampwidth(), r.getframerate()
            body = r.readframes(r.getnframes())
        if width == 1:      # 8-bit WAV is unsigned: silence is 128, not 0
            pad = bytes([128]) * (int(rate * self._leadin_ms() / 1000) * channels)
        else:
            pad = self._silence(self._leadin_ms(), rate, channels, width)
        return pad + body, rate, channels, width

    def _sound_pcm(self):
        """The sound to play right now, or None when the file is missing."""
        chosen = self.sound_file.get()
        if chosen == "beep":
            return self._tone_pcm()
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        wav = base / "sounds" / f"{chosen}.wav"
        if not wav.exists():
            return None
        return self._file_pcm(wav)

    def _play_sound(self, report=False):
        """Play the alarm sound on the picked output device, off the main thread.

        With report on, a failure is written to the status line — that is how
        the Test button tells you the sound went nowhere instead of leaving
        you guessing, which is what a silent lab looked like."""
        import threading

        def _beep():
            try:
                made = self._sound_pcm()
                if made is None:
                    if report:
                        self.after(0, lambda: self.status_var.set(
                            "Sound file not found in the sounds folder."))
                    return
                pcm, rate, channels, width = made
                play_pcm(pcm, rate, channels, width, self.sound_device.get())
                if report:
                    self.after(0, lambda: self.status_var.set(
                        f"Sound played on: {self.sound_device.get()}"))
            except Exception as exc:
                msg = f"Sound failed: {exc}"
                if report:
                    self.after(0, lambda m=msg: self.status_var.set(m))
                # Never let a sound problem stop the alarm: the flash is the
                # part that matters, so fall back to the old beep and move on.
                try:
                    import winsound
                    winsound.Beep(self.sound_freq.get(), self.sound_duration.get())
                except Exception:
                    pass

        threading.Thread(target=_beep, daemon=True).start()
    def _reset(self):
        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None
        self._is_flashing = False
        self._hide_image_win()
        self.changed = False
        self.tracking = False
        self.status_var.set("Ready.")
        self._clear_cond_badges()
        self._update_circle()

    def _update_circle(self):
        if self.tracking:
            fill, outline = "#22cc22", "#117711"   # green  — active
        elif self.changed:
            fill, outline = "#cc2222", "#881111"   # red    — change detected
        elif self.reference is not None or self._enabled_conditions():
            fill, outline = "#ddaa00", "#886600"   # orange — ready, has reference
        else:
            fill, outline = "#888888", ""          # grey   — no reference yet
        self._canvas_circle.itemconfig(self._circle, fill=fill, outline=outline, width=2)
        
    def _set_ui_visible(self, visible):
        if visible:
            self._mon_frame.pack(side="left", before=self._canvas_circle)
            self._settings_btn.pack(side="left", padx=(4, 0))
            self._cond_frame.grid()
            self._log_frame.grid()
            self._set_transparent(False)
        else:
            self._geom_before_hide = (f"{self.winfo_width()}x{self.winfo_height()}"
                                      f"+{self.winfo_rootx()}+{self.winfo_rooty()}")
            self._mon_frame.pack_forget()
            self._settings_btn.pack_forget()
            self._cond_frame.grid_remove()
            self._log_frame.grid_remove()
            self._set_transparent(True)

    def _set_transparent(self, on):
        """HUD mode: drop window chrome and chroma-key the background so only the
        circle (and any PV alert badges) remain visible and clickable."""
        try:
            if on:
                self.overrideredirect(True)
                self.attributes("-transparentcolor", self._chroma)
                self.attributes("-topmost", True)
                # Position only — the window has just been emptied down to the
                # circle and must keep that small size. Without this, losing the
                # title bar slides the circle up into the space it used to take.
                pos = re.search(r"\+(-?\d+)\+(-?\d+)$", self._geom_before_hide or "")
                if pos:
                    self.update_idletasks()
                    self._apply_geometry(self, f"+{pos.group(1)}+{pos.group(2)}")
            else:
                self.attributes("-transparentcolor", "")
                self.overrideredirect(False)
                self.attributes("-topmost", True)
                if self._geom_before_hide:
                    self._apply_geometry(self, self._geom_before_hide)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Message log
    # ------------------------------------------------------------------
    def _log_message(self, msg, hint=None):
        """Thread-safe: append/refresh a message in the log table.

        `hint` is an optional plain-language explanation shown as a tooltip when
        the user hovers the row.
        """
        self.after(0, self._do_log, msg, hint)

    def _do_log(self, msg, hint=None):
        tv = self._log_tree
        ts = time.strftime("%H:%M:%S")
        entry = self._log_items.get(msg)
        if entry and tv.exists(entry[0]):
            item_id, count = entry[0], entry[1] + 1
            tv.set(item_id, "time", ts)
            tv.set(item_id, "count", count)
            tv.move(item_id, "", "end")
            self._log_items[msg] = (item_id, count)
            tv.see(item_id)
            return
        item_id = tv.insert("", "end", values=(ts, 1, msg))
        self._log_items[msg] = (item_id, 1)
        if hint:
            self._log_hints[item_id] = hint
        children = tv.get_children("")
        if len(children) > 500:
            oldest = children[0]
            for m, (iid, _c) in list(self._log_items.items()):
                if iid == oldest:
                    del self._log_items[m]
                    break
            self._log_hints.pop(oldest, None)
            tv.delete(oldest)
        tv.see(item_id)

    def _clear_log(self):
        self._log_tree.delete(*self._log_tree.get_children(""))
        self._log_items.clear()
        self._log_hints.clear()
        self._hide_log_tip()

    def _on_log_hover(self, event):
        tv = self._log_tree
        item = tv.identify_row(event.y)
        hint = self._log_hints.get(item) if item else None
        if not hint:
            self._hide_log_tip()
            return
        if item == self._log_tip_item and self._log_tip and self._log_tip.winfo_exists():
            return
        self._hide_log_tip()
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=hint, bg="#ffffe0", fg="#222222", justify="left",
                 relief="solid", bd=1, font=("Segoe UI", 9),
                 wraplength=340, padx=6, pady=4).pack()
        self._place_popup(tip, event.x_root + 14, event.y_root + 16)
        self._log_tip = tip
        self._log_tip_item = item

    def _hide_log_tip(self):
        if self._log_tip and self._log_tip.winfo_exists():
            self._log_tip.destroy()
        self._log_tip = None
        self._log_tip_item = None

    def _pick_flash_color(self):
        from tkinter.colorchooser import askcolor
        color = askcolor(color=self.flash_color.get(), title="Pick flash color")
        if color and color[1]:
            self.flash_color.set(color[1])
            self._flash_color_btn.config(bg=color[1])

    # ------------------------------------------------------------------
    # Window position & size recorders (control window + image window)
    # ------------------------------------------------------------------
    def _open_geometry_recorder(self, *, key, prep, teardown, fetch_geom,
                                fit_action, title, instr):
        """Shared recorder dialog: scope radios + DONE/Cancel. `prep`/`teardown`
        show and restore the target window, `fetch_geom` returns the geometry to
        persist under `key` (global or per-preset)."""
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None

        prep()

        rec_win = tk.Toplevel(self)
        rec_win.title(title)
        rec_win.attributes("-topmost", True)
        rec_win.resizable(False, False)

        ttk.Label(rec_win, text=instr, justify="center").pack(padx=16, pady=(12, 8))

        scope_var = tk.StringVar(value="global")
        scope_frame = ttk.LabelFrame(rec_win, text="Save for")
        scope_frame.pack(padx=12, pady=(0, 8), fill="x")
        ttk.Radiobutton(scope_frame, text="All presets (global)",
                        variable=scope_var, value="global").pack(anchor="w", padx=8, pady=(4, 2))

        current_preset = self._preset_var.get()
        if current_preset and current_preset in self._presets:
            ttk.Radiobutton(scope_frame,
                            text=f'Selected preset only: "{current_preset}"',
                            variable=scope_var, value="preset").pack(anchor="w", padx=8, pady=(0, 4))
        else:
            ttk.Label(scope_frame, text="(select a preset to enable preset-only save)",
                      foreground="gray").pack(anchor="w", padx=8, pady=(0, 4))

        if fit_action is not None:
            fit_frame = ttk.Frame(rec_win)
            fit_frame.pack(padx=12, pady=(0, 8), fill="x")
            ttk.Button(fit_frame, text="Fit window to image (1:1 pixels)",
                       command=fit_action).pack(fill="x")
            ttk.Label(fit_frame,
                      text="Sets the window to the image's captured pixel size, then just drag to position.",
                      foreground="gray", wraplength=300, justify="left").pack(anchor="w", pady=(2, 0))

        btn_frame = ttk.Frame(rec_win)
        btn_frame.pack(pady=(0, 12))

        def on_done():
            geom = fetch_geom()
            teardown()
            self._save_geometry(geom, scope_var.get(), current_preset, key)
            rec_win.destroy()

        def on_cancel():
            teardown()
            rec_win.destroy()

        ttk.Button(btn_frame, text="DONE", command=on_done, width=10).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Cancel", command=on_cancel, width=10).pack(side="left")
        rec_win.protocol("WM_DELETE_WINDOW", on_cancel)

        # Next to the control window, or wherever it still fits on that screen.
        self.update_idletasks()
        self._place_popup(rec_win,
                          self.winfo_rootx() + self.winfo_width() + 10,
                          self.winfo_rooty())

    def _start_control_window_recording(self):
        """Record position/size of the control window (this window)."""
        self._open_geometry_recorder(
            key="window_geometry",
            prep=lambda: (self.lift(), self.attributes("-topmost", True)),
            teardown=lambda: None,
            fetch_geom=lambda: (f"{self.winfo_width()}x{self.winfo_height()}"
                                f"+{self.winfo_rootx()}+{self.winfo_rooty()}"),
            fit_action=None,
            title="Set control window position & size",
            instr=("Move and resize the CONTROL window (circle, presets, alerts)\n"
                   "to the desired position, then click DONE."))

    def _start_image_window_recording(self):
        """Record position/size of the separate image (flash) window."""
        win = self._ensure_image_win()
        geom = (self._resolve_image_geometry()
                or f"400x300+{self.winfo_rootx() + 40}+{self.winfo_rooty() + 40}")
        has_image = (self.flash_mode.get() == "image"
                     and self._image_path(self.image_file.get()) is not None)

        def prep():
            # Decorated + semi-transparent so the borderless window can be dragged
            # and the desktop shows through for alignment.
            win.overrideredirect(False)
            win.title("Image window — drag to position, then DONE")
            try: win.attributes("-transparentcolor", "")
            except Exception: pass
            try: win.attributes("-alpha", 0.6)
            except Exception: pass
            win.configure(bg=self._chroma)
            win.deiconify()
            win.lift()
            win.attributes("-topmost", True)
            # Positioned after it is shown: while it wears a title bar, the
            # remembered position has to be corrected for that title bar, and
            # that can only be measured on a window already on screen. Keeps
            # the rectangle exactly where the alarm will flash.
            self._apply_geometry(win, geom)
            if has_image:
                self._start_align_ghost()
            else:
                # No template: show a solid flash-colour rectangle so the bounds
                # are visible while positioning.
                self._flash_overlay.configure(bg=self.flash_color.get())
                self._flash_overlay.place(x=0, y=0, relwidth=1, relheight=1)

        def teardown():
            self._stop_align_ghost()
            if self._flash_overlay is not None:
                self._flash_overlay.place_forget()
            try: win.attributes("-alpha", 1.0)
            except Exception: pass
            win.configure(bg=self._flash_key)
            win.overrideredirect(True)
            try: win.attributes("-transparentcolor", self._flash_key)
            except Exception: pass
            win.withdraw()

        def fetch_geom():
            g = (f"{win.winfo_width()}x{win.winfo_height()}"
                 f"+{win.winfo_rootx()}+{win.winfo_rooty()}")
            self._image_geometry = g
            return g

        def fit_action():
            p = self._image_path(self.image_file.get())
            if p is None:
                return
            try:
                from PIL import Image
                iw, ih = Image.open(p).size
            except Exception as e:
                self._log_message(f"Image size read failed: {e}")
                return
            # Keep the corner where it is, but pulled back if the bigger window
            # would now reach past the edge of the screen.
            self._apply_geometry(
                win, f"{iw}x{ih}+{win.winfo_rootx()}+{win.winfo_rooty()}")
            win.update_idletasks()
            self._align_last_size = (0, 0)
            self._refresh_align_ghost()

        self._open_geometry_recorder(
            key="image_geometry",
            prep=prep, teardown=teardown, fetch_geom=fetch_geom,
            fit_action=fit_action if has_image else None,
            title="Set image window position & size",
            instr=("Move and resize the IMAGE window (where the alarm flashes)\n"
                   "to the desired position, then click DONE."))

    def _start_align_ghost(self):
        """During image-window recording, overlay a faint template ghost that
        rescales with the window so the user can align it over the monitor
        object. Operates on the dedicated image window."""
        if self.flash_mode.get() != "image":
            return
        if self._image_path(self.image_file.get()) is None:
            self._log_message("No template image selected to align.")
            return
        win = self._image_win
        if win is None or self._align_label is None or not self._align_label.winfo_exists():
            return
        self._align_last_size = (0, 0)
        self._align_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._align_label.lift()
        self._align_bind = win.bind("<Configure>", self._on_align_configure, "+")
        self.after(50, self._refresh_align_ghost)

    def _on_align_configure(self, event):
        if event.widget is self._image_win:
            self._refresh_align_ghost()

    def _refresh_align_ghost(self):
        win = self._image_win
        if win is None or self._align_label is None or not self._align_label.winfo_exists():
            return
        size = (win.winfo_width(), win.winfo_height())
        if size == self._align_last_size:
            return
        self._align_last_size = size
        self._align_photo = self._make_flash_photo(size, fade=0.5)
        if self._align_photo is not None:
            self._align_label.configure(image=self._align_photo)
            self._align_label.lift()

    def _stop_align_ghost(self):
        win = self._image_win
        if self._align_bind is not None and win is not None:
            try:
                win.unbind("<Configure>", self._align_bind)
            except Exception:
                pass
            self._align_bind = None
        if self._align_label is not None:
            self._align_label.place_forget()
        self._align_photo = None

    def _save_geometry(self, geom, scope, preset_name, key):
        if scope == "global":
            self._presets[key] = geom
        elif scope == "preset" and preset_name and preset_name in self._presets:
            data = self._presets[preset_name]
            if isinstance(data, list):
                self._presets[preset_name] = {"region": data, key: geom}
            elif isinstance(data, dict):
                data[key] = geom
        self._save_presets_file()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _grab(self):
        return self._grab_rect(self.region)

    def _grab_rect(self, rect):
        """A screenshot of one rectangle, or None (the error goes on screen)."""
        if not rect:
            return None
        try:
            return ImageGrab.grab(bbox=tuple(rect), all_screens=True)
        except Exception as e:
            self.status_var.set(f"Screenshot error: {e}")
            return None

    @staticmethod
    def _picture_diff(img, reference):
        """Average pixel deviation (0-255) between two crops, or None."""
        try:
            stat = ImageStat.Stat(ImageChops.difference(img, reference))
            return sum(stat.mean) / len(stat.mean)
        except Exception:
            return None

    def _update_preview(self, img=None):
        if img is None:
            img = self._grab()
        if img is None:
            self.status_var.set("_update_preview: grab vrátil None")
            return
        thumb = img.copy()
        thumb.thumbnail((640, 400), resample=0)
        self._preview_photo = ImageTk.PhotoImage(thumb)
        if self.btn_preview.cget("state") == "disabled":
            self.btn_preview.config(state="normal")
        if self._preview_popup and self._preview_popup.winfo_exists():
            self._preview_popup_label.config(image=self._preview_photo)

    def _show_preview_popup(self, event=None):
        if self._preview_photo is None:
            return
        if self._preview_popup and self._preview_popup.winfo_exists():
            return
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        lbl = tk.Label(popup, image=self._preview_photo, relief="solid", bd=1)
        lbl.pack()
        self._preview_popup_label = lbl
        # Above the button, or below/beside it if there is no room above.
        self._place_popup(
            popup, self.btn_preview.winfo_rootx(),
            self.btn_preview.winfo_rooty() - self._preview_photo.height() - 8)
        popup.bind("<Enter>", lambda e: None)
        self._preview_popup = popup

    def _hide_preview_popup(self, event=None):
        if self._preview_popup and self._preview_popup.winfo_exists():
            self._preview_popup.destroy()
        self._preview_popup = None

    def _toggle_preview_popup(self, event=None):
        if self._preview_pinned:
            self._preview_pinned = False
            self.btn_preview.config(text="Preview region")
            self._hide_preview_popup()
        else:
            self._preview_pinned = True
            self.btn_preview.config(text="Preview region ✓")
            self._show_preview_popup()

    def _check_hide_preview(self):
        if self._preview_pinned:
            return
        if not (self._preview_popup and self._preview_popup.winfo_exists()):
            return
        x, y = self.winfo_pointerxy()
        p = self._preview_popup
        bx = self.btn_preview.winfo_rootx()
        by = self.btn_preview.winfo_rooty()
        bw = self.btn_preview.winfo_width()
        bh = self.btn_preview.winfo_height()
        over_btn = (bx <= x <= bx + bw and by <= y <= by + bh)
        over_popup = (p.winfo_rootx() <= x <= p.winfo_rootx() + p.winfo_width() and
                      p.winfo_rooty() <= y <= p.winfo_rooty() + p.winfo_height())
        if over_btn or over_popup:
            self.after(100, self._check_hide_preview)
        else:
            self._hide_preview_popup()

    _CPVA_URL = "https://10.78.0.57:8443/api/1.0/cpva/samples"

    @staticmethod
    def _cpva_context():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _fetch_samples(self, pv_name, start_ns, end_ns, context=None):
        """Every archived (time_ns, value) pair of one PV in a window, oldest first.

        Returns an empty list both when the window holds nothing and when the
        read failed — a failed read is logged in plain language here, and the
        callers must never treat "no data" as a good value.
        """
        try:
            params = urllib.parse.urlencode({
                "channelName": pv_name,
                "start": str(start_ns),
                "end":   str(end_ns),
            })
            req = urllib.request.Request(
                f"{self._CPVA_URL}?{params}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0,
                                        context=context or self._cpva_context()) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            msg, hint = _readable_pv_error(pv_name, e)
            self._log_message(msg, hint)
            return []
        samples = []
        for sample in data:
            v = sample.get("value")
            if isinstance(v, list):
                v = v[0] if v else None
            try:
                samples.append((int(sample.get("time")), float(v)))
            except (TypeError, ValueError):
                continue
        samples.sort(key=lambda s: s[0])
        return samples

    def _fetch_latest(self, pv_name, now_ns, context=None):
        """The newest archived (value, time_ns) of one PV, or None.

        For a value that is written ON CHANGE ONLY — the beam fate and the PSS
        state both are — the last minute is empty nearly always, so the window
        widens until something comes back. Not _condition_value: that returns
        the HIGHEST sample of the window, which is meaningless for an enum.

        The window that worked last time is tried first, and remembered. Without
        that, a channel that is not archived at all costs four requests on every
        single read — and the beam fate is exactly that channel today. Starting
        wide is never wrong: the newest sample of a wide window is the same
        sample, and these channels hold a handful of points a month.
        """
        # Only ever widen from the hint. A narrower window is a subset of a
        # wider one, so once the hinted window comes back empty there is
        # nothing a smaller one could still hold.
        for i in range(self._hall_span_hint.get(pv_name, 0), len(HALL_LOOKBACK_S)):
            samples = self._fetch_samples(pv_name,
                                          now_ns - HALL_LOOKBACK_S[i] * 1_000_000_000,
                                          now_ns, context)
            if samples:
                self._hall_span_hint[pv_name] = i
                return samples[-1][1], samples[-1][0]
        # Nothing anywhere: next time, ask the widest window only.
        self._hall_span_hint[pv_name] = len(HALL_LOOKBACK_S) - 1
        return None

    def _read_hall_pvs(self, context=None):
        """(beam fate, PSS state) as (value, time_ns) pairs, either may be None."""
        now_ns = int(time.time() * 1e9)
        return (self._fetch_latest(HALL_FATE_PV, now_ns, context),
                self._fetch_latest(HALL_PSS_PV, now_ns, context))

    @staticmethod
    def _hall_readout_text(fate, pss):
        """The one line saying what the two values are doing, in plain words."""
        def one(label, reading, namer):
            if reading is None:
                return f"{label}: cannot be read"
            value, ts = reading
            when = time.strftime("%d.%m. %H:%M", time.localtime(ts / 1e9))
            return f"{label}: {namer(value)} (since {when})"
        return (one("Beam", fate, _hall_name) + "   ·   "
                + one("PSS", pss, _pss_name))

    def _read_hall_now(self):
        """The Read now button on the Halls tab."""
        self._hall_readout.set("reading…")

        def show(fate, pss):
            self._hall_readout.set(self._hall_readout_text(fate, pss))

        def worker():
            fate, pss = self._read_hall_pvs()
            self.after(0, show, fate, pss)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _condition_value(samples, since_ns):
        """The one number a value condition is judged on.

        The highest sample since `since_ns`: one shot over the limit is the whole
        event, so an average would hide it. The archiver only writes a sample when
        the value CHANGES, so a value that went over the limit and then sat
        perfectly still produces nothing recent at all — in that case the newest
        sample of the whole window is the value the machine is still holding, and
        that is what gets judged. Nothing at all means no judgement (None).
        """
        recent = [v for t, v in samples if t >= since_ns]
        if recent:
            return max(recent)
        return samples[-1][1] if samples else None

    def _poll_pvs(self):
        if not self.tracking:
            return

        _ssl = self._cpva_context()
        # Read the condition list here, on the UI thread, so the worker cannot
        # trip over it being edited mid-fetch.
        pv_conds = [c for c in self._enabled_conditions("pv") if c.get("trip") is not None
                    or c.get("warn") is not None]
        # The two hall values change hours apart and need a wide look-back, so
        # they ride the same worker but on their own, much slower clock.
        hall_conds = self._enabled_conditions("hall")
        read_hall = bool(hall_conds) and time.time() >= self._next_hall_read
        if read_hall:
            self._next_hall_read = time.time() + HALL_POLL_MS / 1000.0

        def fetch_avg(pv_name):
            samples = self._fetch_samples(pv_name, start_ns, now_ns, _ssl)
            recent = [v for _t, v in samples][-PV_AVG_COUNT:]
            return sum(recent) / len(recent) if recent else None

        def fetch_condition(pv_name):
            samples = self._fetch_samples(pv_name, start_ns, now_ns, _ssl)
            return self._condition_value(samples, cond_start_ns)

        def worker():
            results = []
            for channel, _label, *_thresholds in _PV_MONITORS:
                if isinstance(channel, tuple):
                    a = fetch_avg(channel[0])
                    sub = channel[1]
                    b = sub if isinstance(sub, (int, float)) else fetch_avg(sub)
                    avg = (a - b) if (a is not None and b is not None) else None
                    abs_val = a          # raw temperature for the absolute check
                else:
                    avg = fetch_avg(channel)
                    abs_val = avg
                results.append((avg, abs_val))
            cond_results = [(cond, fetch_condition(cond["pv"])) for cond in pv_conds]
            hall = self._read_hall_pvs(_ssl) if read_hall else None
            self.after(0, lambda r=results, c=cond_results, h=hall:
                       self._update_pv_display(r, c, h))

        now_ns   = int(time.time() * 1e9)
        start_ns = now_ns - 60 * 1_000_000_000   # last 60 s
        cond_start_ns = now_ns - COND_PV_WINDOW_S * 1_000_000_000

        threading.Thread(target=worker, daemon=True).start()
        self._pv_poll_job = self.after(PV_POLL_MS, self._poll_pvs)

    def _check_pv_conditions(self, cond_results):
        """Badge, or alarm, for every value condition that was just read.

        A value that could not be read (archiver down, nothing archived) never
        trips — it was already logged as an error by the fetch.
        """
        for cond, value in cond_results:
            if cond not in self._conditions:
                continue                      # edited or deleted while fetching
            if value is None:
                self._set_cond_badge(cond, None)
                continue
            unit = f" {cond['unit']}" if cond.get("unit") else ""
            trip, warn = cond.get("trip"), cond.get("warn")
            if trip is not None and value > trip:
                self._on_condition_failed(
                    cond, f"{value:g}{unit} is over {trip:g}{unit}")
                return True
            if warn is not None and value > warn:
                self._set_cond_badge(
                    cond, "#cc6600",
                    f"⚠  {cond['name']}: {value:g}{unit} ↑ (warn {warn:g})")
            else:
                self._set_cond_badge(cond, None)
        return False

    def _check_hall_conditions(self, hall):
        """Badge, or alarm, for every hall condition, from one pair of readings.

        `hall` is (fate, pss) as returned by _read_hall_pvs, each either a
        (value, time_ns) pair or None. A value that could not be read never
        trips, but it does not go quiet either — the badge says so, because
        "nothing shown" would read as "everything is fine".
        """
        fate_read, pss_read = hall
        fate = fate_read[0] if fate_read else None
        pss = pss_read[0] if pss_read else None

        # How long the switchyard has been on the move: the archiver's own
        # timestamp for the sample, not our clock, which runs ahead of the
        # facility's.
        moving_for_s = None
        if fate_read is not None and fate == HALL_MOVING_VALUE:
            moving_for_s = max(0.0, (time.time() * 1e9 - fate_read[1]) / 1e9)

        if hasattr(self, "_hall_readout"):
            self._hall_readout.set(self._hall_readout_text(fate_read, pss_read))

        for cond in self._enabled_conditions("hall"):
            if cond not in self._conditions:
                continue                      # edited or deleted while fetching
            verdict, sentence = _hall_verdict(cond, fate, pss, moving_for_s)
            if verdict == "trip":
                self._on_condition_failed(cond, sentence)
                return True
            if verdict == "warn":
                self._set_cond_badge(cond, "#cc6600", f"⚠  {cond['name']}: {sentence}")
            elif verdict == "unknown":
                self._set_cond_badge(cond, "#cc6600", f"⚠  {cond['name']}: {sentence}")
            else:
                self._set_cond_badge(cond, None)
        return False

    def _rebuild_cond_badges(self):
        """One badge per condition, appended to the PV badge list so that
        _relayout_pv_alerts lays them out and wraps them with everything else."""
        for row in self._cond_alert_rows:
            if row in self._pv_alert_rows:
                self._pv_alert_rows.remove(row)
            row.destroy()
        self._cond_alert_rows = []
        self._cond_badges = {}
        for cond in self._conditions:
            row = tk.Frame(self._pv_frame, bg="#cc2200", padx=8, pady=5)
            row._active = False
            lbl = tk.Label(row, text="", bg="#cc2200", fg="white",
                           font=("Segoe UI", 9, "bold"), anchor="w")
            lbl.pack()
            self._cond_alert_rows.append(row)
            self._pv_alert_rows.append(row)
            self._cond_badges[cond["_uid"]] = (row, lbl)
        self._relayout_pv_alerts()

    def _set_cond_badge(self, cond, bg, text=""):
        """Show (bg + text) or hide (bg=None) one condition's badge."""
        pair = self._cond_badges.get(cond.get("_uid"))
        if pair is None:
            return
        row, lbl = pair
        if bg is None:
            row._active = False
        else:
            row.configure(bg=bg)
            lbl.configure(bg=bg, text=text)
            row._active = True
        self._relayout_pv_alerts()

    def _clear_cond_badges(self):
        for row in self._cond_alert_rows:
            row._active = False
        self._relayout_pv_alerts()

    def _update_pv_display(self, results: list, cond_results=(), hall=None):
        for i, ((avg, abs_val), (_, label, _lo_o, _lo_r, _hi_o, _hi_r, unit)) in enumerate(zip(results, _PV_MONITORS)):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            lo_r = lo_r_var.get()
            lo_o = lo_o_var.get()
            hi_o = hi_o_var.get()
            hi_r = hi_r_var.get()
            row = self._pv_alert_rows[i]
            lbl = self._pv_alert_labels[i]

            # External condition: raw temperature outside its absolute range
            abs_range = _PV_ABS_RANGE[i]
            abs_breach = (abs_range is not None and abs_val is not None
                          and (abs_val < abs_range[0] or abs_val > abs_range[1]))

            if abs_breach:
                bg = "#7a1fa0"   # purple — out of absolute range
            elif avg is not None and (avg < lo_r or avg > hi_r):
                bg = "#cc2200"
            elif avg is not None and (avg < lo_o or avg > hi_o):
                bg = "#cc6600"
            else:
                bg = None

            if bg is not None:
                if abs_breach:
                    fmt = ".2f" if abs(abs_val) < 10 else ".1f"
                    arrow = " ↑" if abs_val > abs_range[1] else " ↓"
                    text = f"⛔ {label}: {abs_val:{fmt}} °C{arrow}"
                else:
                    suffix = f" {unit}" if unit else ""
                    fmt = ".2f" if abs(avg) < 10 else ".1f"
                    arrow = " ↑" if avg > hi_o else " ↓"
                    text = f"⚠  {label}: {avg:{fmt}}{suffix}{arrow}"
                row.configure(bg=bg)
                lbl.configure(bg=bg, text=text)
                row._active = True
            else:
                row._active = False

        self._relayout_pv_alerts()
        if self._check_pv_conditions(cond_results):
            return
        if hall is not None:
            self._check_hall_conditions(hall)

    def _on_pv_frame_configure(self, event):
        # Re-flow only when the available width actually changes (placing
        # children / setting the frame height fire <Configure> with same width).
        if event.width != self._pv_last_width:
            self._pv_last_width = event.width
            self._relayout_pv_alerts()

    def _relayout_pv_alerts(self):
        """Lay active alert badges left-to-right, wrapping to a new line so
        nothing overlaps or gets clipped at the right edge."""
        for row in self._pv_alert_rows:
            row.place_forget()
        active = [r for r in self._pv_alert_rows if getattr(r, "_active", False)]
        if not active:
            self._pv_frame.configure(height=1)
            return

        self.update_idletasks()
        avail = self._pv_frame.winfo_width()
        if avail <= 1:
            avail = max(1, self.winfo_width() - 20)

        gap = 4
        x = y = line_h = 0
        for row in active:
            w = row.winfo_reqwidth()
            h = row.winfo_reqheight()
            if x > 0 and x + w > avail:   # wrap to next line
                x = 0
                y += line_h + gap
                line_h = 0
            row.place(x=x, y=y)
            x += w + gap
            line_h = max(line_h, h)
        self._pv_frame.configure(height=y + line_h)

    def _on_close(self):
        self.tracking = False
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
        self.destroy()


if __name__ == "__main__":
    app = ScreenTracker()
    app.mainloop()
