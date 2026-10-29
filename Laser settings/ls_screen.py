"""Laser settings — the stopgap: which valves are open, read off the L3BT MSS window.

SGV502 to SGV505 do not publish their position yet, so the program cannot tell
which experimental hall the beam is going into. The L3BT MSS window on the
operator PC (L3-OPR03) does show it, in a small box under each valve's name:

    [ O ■ ]   the valve is OPEN    — the black square on the right
    [ ■ C ]   the valve is CLOSED  — the black square on the left
    [ ⊠ ⊠ ]   crossed out          — taken as closed, as asked for

This module finds that window, takes a picture of it and reads the four boxes.
The answer is used ONLY while a valve's own position channels cannot be read —
`ls_core.hall_state` asks for it last — so the day the PVs publish, they win and
this module stops mattering without anybody switching it off.

HOW IT STAYS RIGHT WHEN THINGS MOVE
-----------------------------------
* The window is found by its exact TITLE and photographed with PrintWindow, which
  draws the window into memory: it does not matter which monitor it is on, how
  the monitors are arranged, or whether another window is lying on top of it. A
  minimised window cannot be photographed and is reported as not found.
* Where each box should be is stored as a position inside the window (measured
  on 1.10.2026 from a 1190 × 629 window), scaled to the window's real size, and
  the box is then SEARCHED for around that spot: its black outline is found, so
  a few pixels of drift change nothing.
* A box that cannot be found is no answer at all, never "closed". The hall then
  says it does not know, exactly as it does today without this module.

No Qt in here: the pixels are plain bytes, so the reading is tested headless
from saved screenshots (`testing/test_screen_hall.py`).
"""

from __future__ import annotations

import ctypes
import re
import sys
import time
from typing import NamedTuple

OPEN = "open"
CLOSED = "closed"

WINDOW_TITLE = "L3BT MSS"

# The window the positions below were measured on, as DWM reports its frame.
_REF_W = 1190
_REF_H = 629

# Each hall's valve box: the valve's name and the box's centre in that window.
VALVE_BOXES = {
    "E2": ("SGV502", 318.0, 352.5),
    "E3": ("SGV503", 556.0, 418.5),
    "E4": ("SGV504", 738.0, 352.5),
    "E5": ("SGV505", 697.0, 418.5),
}

# The box itself, outline included, in that window: 31 × 16 pixels.
_BOX_W = 31
_BOX_H = 16

# How far from its expected centre a box is looked for. The nearest other box
# (SGV504's and SGV505's) sits 78 px away, so this cannot pick up a neighbour.
_SEARCH_PX = 30

# A pixel is ink when none of its colours is bright. The red arrows beside the
# boxes are bright red, so they are never ink. Mid grey still counts, because a
# window drawn at another scale smears a one-pixel line into two grey ones.
_INK_MAX = 170
# How much of a line must be ink to count as the box's outline, and how much of
# a half must be ink to count as the solid black square.
_OUTLINE_SHARE = 0.9
_SOLID_SHARE = 0.8


class ScreenRead(NamedTuple):
    """What one look at the window found.

    `positions` holds OPEN or CLOSED for every hall whose box was read; a hall
    missing from it is not known. `problem` is a sentence for the tooltip when
    something could not be read, else None. `at` is time.time() of the look.
    """
    positions: dict
    problem: str | None
    at: float


class Picture(NamedTuple):
    """A picture as 32-bit BGRA bytes, top row first."""
    width: int
    height: int
    data: bytes


# ─────────────────────────────────────────────────────────────────────────────
# Reading the boxes
# ─────────────────────────────────────────────────────────────────────────────

def _ink(pic, x, y):
    i = (y * pic.width + x) * 4
    d = pic.data
    return d[i] < _INK_MAX and d[i + 1] < _INK_MAX and d[i + 2] < _INK_MAX


def _row_share(pic, y, x0, x1):
    return sum(_ink(pic, x, y) for x in range(x0, x1 + 1)) / (x1 - x0 + 1)


def _col_share(pic, x, y0, y1):
    return sum(_ink(pic, x, y) for y in range(y0, y1 + 1)) / (y1 - y0 + 1)


def _area_share(pic, x0, y0, x1, y1):
    if x1 < x0 or y1 < y0:
        return 0.0
    n = (x1 - x0 + 1) * (y1 - y0 + 1)
    return sum(_ink(pic, x, y)
               for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)) / n


def find_box(pic, cx, cy, scale):
    """The box outline nearest (cx, cy): (x0, y0, x1, y1) inclusive, or None.

    A box is a run of ink as wide as a box, a second run under it at a box's
    height, and two ink columns joining their ends. The solid black half alone
    is about half a box wide, so it is never taken for the box.
    """
    bw, bh = _BOX_W * scale, _BOX_H * scale
    reach = _SEARCH_PX * scale
    min_w, max_w = int(bw * 0.7), int(bw * 1.4) + 1
    min_h, max_h = max(3, int(bh * 0.6)), int(bh * 1.5) + 1
    xa = max(0, int(cx - reach - bw))
    xb = min(pic.width - 1, int(cx + reach + bw))
    ya = max(0, int(cy - reach - bh))
    yb = min(pic.height - 1, int(cy + reach + bh))

    best, best_d = None, None
    for y in range(ya, yb + 1):
        x = xa
        while x <= xb:
            if not _ink(pic, x, y):
                x += 1
                continue
            x0 = x
            while x <= xb and _ink(pic, x, y):
                x += 1
            x1 = x - 1
            if not (min_w <= x1 - x0 + 1 <= max_w):
                continue
            for y1 in range(y + min_h, min(pic.height - 1, y + max_h) + 1):
                if (_row_share(pic, y1, x0, x1) >= _OUTLINE_SHARE
                        and _col_share(pic, x0, y, y1) >= _OUTLINE_SHARE
                        and _col_share(pic, x1, y, y1) >= _OUTLINE_SHARE):
                    mx, my = (x0 + x1) / 2.0, (y + y1) / 2.0
                    if abs(mx - cx) <= reach and abs(my - cy) <= reach:
                        d = (mx - cx) ** 2 + (my - cy) ** 2
                        if best_d is None or d < best_d:
                            best, best_d = (x0, y, x1, y1), d
                    break
    return best


def box_position(pic, box):
    """OPEN, CLOSED, or None for a box that makes no sense (both halves black).

    The black square on the right is OPEN, on the left CLOSED. No black square
    at all is the crossed-out box, which counts as closed.
    """
    x0, y0, x1, y1 = box
    mid = (x0 + x1) / 2.0
    left = _area_share(pic, x0 + 1, y0 + 1, int(mid) - 1, y1 - 1)
    right = _area_share(pic, int(mid + 0.5) + 1, y0 + 1, x1 - 1, y1 - 1)
    left_solid = left >= _SOLID_SHARE
    right_solid = right >= _SOLID_SHARE
    if left_solid and right_solid:
        return None
    if right_solid:
        return OPEN
    return CLOSED


def read_picture(pic, now_s=None):
    """Read the four valve boxes out of a picture of the whole window."""
    now = time.time() if now_s is None else now_s
    if pic.width < 50 or pic.height < 50:
        return ScreenRead({}, f"The {WINDOW_TITLE} window is too small to read.",
                          now)
    sx = pic.width / _REF_W
    sy = pic.height / _REF_H
    positions, missing = {}, []
    for hall, (valve, rx, ry) in VALVE_BOXES.items():
        box = find_box(pic, rx * sx, ry * sy, (sx + sy) / 2.0)
        position = box_position(pic, box) if box else None
        if position is None:
            missing.append(valve)
        else:
            positions[hall] = position
    problem = None
    if missing:
        problem = (f"The {', '.join(missing)} box could not be read on the "
                   f"{WINDOW_TITLE} window.")
    return ScreenRead(positions, problem, now)


# ─────────────────────────────────────────────────────────────────────────────
# Finding and photographing the window (Windows only)
# ─────────────────────────────────────────────────────────────────────────────
# Adapted from Screenshots/ssq_core.py (`find_window_by_title_substring`,
# `take_screenshot_window_png`), kept to plain ctypes so the build needs nothing
# new. Coordinates are physical pixels: Qt 6 makes the process per-monitor DPI
# aware before any of this runs.

_NORM_RE = re.compile(r"[^a-z0-9]+")

# Everyday applications whose title can be the window's name by accident — a
# browser tab, an Explorer folder, a picture of it in the Photos app.
_EXCLUDED_CLASSES = {
    "chrome_widgetwin_1", "cabinetwclass", "notepad",
    "applicationframewindow", "windows.ui.core.corewindow",
    "winuidesktopwin32windowclass",
    "framework::cframe", "rctrl_renwnd32", "tktoplevel", "tk",
}

_api = None


def _win32():
    """The ctypes functions, set up once, on first use."""
    global _api
    if _api is not None:
        return _api
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32")
    dwm = ctypes.WinDLL("dwmapi")

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                   wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.HDC,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                          ctypes.POINTER(RECT), wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.HRESULT
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int

    _api = dict(user32=user32, gdi32=gdi32, dwm=dwm, enum_proc=enum_proc,
                RECT=RECT, BITMAPINFO=BITMAPINFO,
                BITMAPINFOHEADER=BITMAPINFOHEADER)
    return _api


def _norm(text):
    return _NORM_RE.sub("", (text or "").lower())


def find_window(title=WINDOW_TITLE):
    """The window's handle, or None. Hidden and minimised windows are skipped.

    The title must match EXACTLY (case, spaces and punctuation aside). A title
    that only contains it is never taken: on 1.10.2026 the Photos app showing a
    screenshot called "L3bt mss2.png" was found that way, and a picture of the
    window is exactly the thing that must not be read as the window.
    """
    api = _win32()
    user32 = api["user32"]
    needle = _norm(title)
    exact = []

    def _text(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        if _norm(_text(hwnd)) != needle:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if (buf.value or "").lower() not in _EXCLUDED_CLASSES:
            exact.append(hwnd)
        return True

    user32.EnumWindows(api["enum_proc"](_cb), 0)
    return exact[0] if exact else None


def grab_window(hwnd):
    """A Picture of the window, cut to its visible frame (no Win 11 shadow).

    Raises RuntimeError when the window cannot be drawn.
    """
    api = _win32()
    user32, gdi32, dwm, RECT = (api["user32"], api["gdi32"], api["dwm"],
                                api["RECT"])
    win = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
        raise RuntimeError("the window's size could not be read")
    bw = max(1, win.right - win.left)
    bh = max(1, win.bottom - win.top)

    crop = (0, 0, bw, bh)
    frame = RECT()
    if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame),
                                 ctypes.sizeof(RECT)) == 0:
        crop = (max(0, frame.left - win.left), max(0, frame.top - win.top),
                min(bw, frame.right - win.left),
                min(bh, frame.bottom - win.top))

    hdc = user32.GetDC(hwnd)
    if not hdc:
        raise RuntimeError("the window could not be drawn")
    try:
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, bw, bh)
        if not mem or not bmp:
            if mem:
                gdi32.DeleteDC(mem)
            raise RuntimeError("no memory to draw the window into")
        old = gdi32.SelectObject(mem, bmp)
        try:
            if not user32.PrintWindow(hwnd, mem, 2):    # PW_RENDERFULLCONTENT
                if not user32.PrintWindow(hwnd, mem, 0):
                    raise RuntimeError("the window refused to be drawn")
            bmi = api["BITMAPINFO"]()
            bmi.bmiHeader.biSize = ctypes.sizeof(api["BITMAPINFOHEADER"])
            bmi.bmiHeader.biWidth = bw
            bmi.bmiHeader.biHeight = -bh                # top row first
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            buf = ctypes.create_string_buffer(bw * bh * 4)
            if not gdi32.GetDIBits(mem, bmp, 0, bh, buf, ctypes.byref(bmi), 0):
                raise RuntimeError("the window's picture could not be read")
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
    finally:
        user32.ReleaseDC(hwnd, hdc)

    raw = buf.raw
    x0, y0, x1, y1 = crop
    if (x0, y0, x1, y1) == (0, 0, bw, bh):
        return Picture(bw, bh, raw)
    stride = bw * 4
    rows = [raw[y * stride + x0 * 4: y * stride + x1 * 4] for y in range(y0, y1)]
    return Picture(x1 - x0, y1 - y0, b"".join(rows))


def grab_screen_of(hwnd):
    """A Picture of the SCREEN where the window is, cut to its visible frame.

    The second way in, for a window PrintWindow draws wrong: on 9.10.2026 the
    Remote Desktop window on OPR1 was maximised with nothing over it and its
    PrintWindow picture still held no grid, while a screenshot of the screen
    did. This copies only the window's rectangle off the screen (never the
    whole desktop), so whatever lies over the window is in the picture too.

    Raises RuntimeError when it cannot be done.
    """
    api = _win32()
    user32, gdi32, dwm, RECT = (api["user32"], api["gdi32"], api["dwm"],
                                api["RECT"])
    rect = RECT()
    if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect),
                                 ctypes.sizeof(RECT)) != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("the window's size could not be read")
    w = max(1, rect.right - rect.left)
    h = max(1, rect.bottom - rect.top)
    screen = user32.GetDC(None)
    if not screen:
        raise RuntimeError("the screen could not be read")
    try:
        mem = gdi32.CreateCompatibleDC(screen)
        bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
        if not mem or not bmp:
            if mem:
                gdi32.DeleteDC(mem)
            raise RuntimeError("no memory to copy the screen into")
        old = gdi32.SelectObject(mem, bmp)
        try:
            # SRCCOPY | CAPTUREBLT: layered windows are copied as well.
            if not gdi32.BitBlt(mem, 0, 0, w, h, screen, rect.left, rect.top,
                                0x00CC0020 | 0x40000000):
                raise RuntimeError("the screen could not be copied")
            bmi = api["BITMAPINFO"]()
            bmi.bmiHeader.biSize = ctypes.sizeof(api["BITMAPINFOHEADER"])
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            buf = ctypes.create_string_buffer(w * h * 4)
            if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
                raise RuntimeError("the screen's picture could not be read")
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
    finally:
        user32.ReleaseDC(None, screen)
    return Picture(w, h, buf.raw)


def read_window(title=WINDOW_TITLE, now_s=None):
    """Find the window, photograph it, read the boxes. Raises nothing."""
    now = time.time() if now_s is None else now_s
    if sys.platform != "win32":
        return ScreenRead({}, "The window can only be read on Windows.", now)
    try:
        hwnd = find_window(title)
        if not hwnd:
            return ScreenRead(
                {}, f"The {title} window is not open (or is minimised).", now)
        pic = grab_window(hwnd)
    except Exception as exc:
        return ScreenRead({}, f"The {title} window could not be read: {exc}.",
                          now)
    return read_picture(pic, now)
