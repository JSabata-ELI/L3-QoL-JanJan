"""Image Slider — read a camera's reference frame off the camera program's window.

The camera program draws a white reference rectangle (where the beam belongs) on
its live picture. That rectangle is never written into the archived PNG, so the
Slider cannot see it in the data. This module takes a picture of the camera
program's window, finds the camera picture inside it and the white rectangle
inside that, and turns the rectangle into fractions of the frame — the same
form `CamRefRectConfig` in is_t.py keeps it in.

HOW THE WINDOW IS READ
----------------------
* The window is photographed with PrintWindow, which draws it into memory: it
  does not matter which monitor it is on or whether something lies over it, and
  nothing is activated — the keyboard focus stays where it is. A window that
  PrintWindow draws as one flat colour (some GPU-drawn programs do) is copied off
  the screen instead, which only works while it is visible.
* The camera picture is the largest block of the window that is NOT the window's
  own flat background colour and has the camera frame's width-to-height ratio.
  The camera program is assumed to show the whole frame, not zoomed in. When the
  block cannot be found, the caller can hand over the area by hand.
* The rectangle is a closed outline of near-white, colourless pixels whose inside
  is mostly not white. Filled white (a saturated beam spot) is taken out first,
  so it is never mistaken for the frame, even where it touches the outline.

No Qt in here: the pictures are numpy arrays, so the reading is tested headless
(`testing/test_window_ref_rect.py`).
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import NamedTuple

import numpy as np

# A pixel belongs to the window's background when it is this close to the
# window's most common colour (largest channel difference).
_BG_TOL = 10
# The camera picture's width/height must match the frame's within this share.
_ASPECT_TOL = 0.03
# The camera picture must cover at least this share of the window.
_MIN_AREA_SHARE = 0.03
# A row or column of the picture's block counts as picture when at least this
# share of it is not background (a dark picture with grey patches still counts).
_AREA_LINE_SHARE = 0.85

# Near-white and colourless: every channel at least this bright, and the
# channels at most this far apart.
_WHITE_MIN = 225
_WHITE_SPREAD = 30
# White lines are at most this thick; anything thicker is a filled area.
_MAX_LINE_PX = 4
# Each side of the outline must be at least this much white…
_EDGE_SHARE = 0.7
# …and the inside of the outline at most this much white.
_INSIDE_SHARE = 0.3
# The outline must be at least this share of the picture in both directions.
_MIN_RECT_SHARE = 0.05
# An outline this close to the picture's whole size is the picture's own border.
_MAX_RECT_SHARE = 0.97


class WindowRead(NamedTuple):
    """What one look at the camera window found.

    `image` is the window's picture (H x W x 3, RGB). `area` is the camera
    picture inside it as (x0, y0, x1, y1), end exclusive, or None. `rect` is the
    white rectangle in window pixels (line centres), or None. `fractions` is
    (left, top, right, bottom) in [0, 1] of the frame, or None. `problem` is a
    sentence to show when something was not found, else None.
    """
    image: "np.ndarray | None"
    area: "tuple | None"
    rect: "tuple | None"
    fractions: "tuple | None"
    problem: "str | None"


# ─────────────────────────────────────────────────────────────────────────────
# Finding the camera picture in the window
# ─────────────────────────────────────────────────────────────────────────────

def background_colour(img: np.ndarray) -> np.ndarray:
    """The window's own background: the most common colour (quantised to steps of
    4) along the window's left and right edges. Not the whole window's, because a
    large, mostly black camera picture can cover more of the window than the
    background; not the top and bottom edges, where the title, tool and status
    bars are."""
    h, w = img.shape[:2]
    m = max(2, min(h, w) // 40)
    y0, y1 = h // 10, h - h // 10
    sides = np.concatenate([img[y0:y1, :m, :3].reshape(-1, 3),
                            img[y0:y1, -m:, :3].reshape(-1, 3)])
    q = (sides // 4).astype(np.int32)[None, :, :]
    code = (q[:, :, 0] << 12) | (q[:, :, 1] << 6) | q[:, :, 2]
    vals, counts = np.unique(code, return_counts=True)
    c = int(vals[np.argmax(counts)])
    return np.array([((c >> 12) & 63) * 4 + 2, ((c >> 6) & 63) * 4 + 2,
                     (c & 63) * 4 + 2], dtype=np.int16)


def _longest_run(flags: np.ndarray) -> "tuple[int, int]":
    """Start and end (exclusive) of the longest run of True in a 1-D array."""
    best = (0, 0)
    start = None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def _aspect_ok(w: int, h: int, frame_w: "int | None", frame_h: "int | None") -> bool:
    if not frame_w or not frame_h:
        return True
    want = frame_w / frame_h
    return abs((w / h) / want - 1.0) <= _ASPECT_TOL


def find_picture_area(img: np.ndarray, frame_w: "int | None" = None,
                      frame_h: "int | None" = None) -> "tuple | None":
    """The camera picture inside the window as (x0, y0, x1, y1), or None."""
    from scipy.ndimage import find_objects, label as _label
    h, w = img.shape[:2]
    bg = background_colour(img)
    diff = np.abs(img[:, :, :3].astype(np.int16) - bg).max(axis=2)
    not_bg = diff > _BG_TOL
    labels, n = _label(not_bg)
    if n == 0:
        return None
    best = None
    best_size = 0
    min_px = _MIN_AREA_SHARE * w * h
    for idx, sl in enumerate(find_objects(labels), start=1):
        if sl is None:
            continue
        ys, xs = sl
        bw, bh = xs.stop - xs.start, ys.stop - ys.start
        if bw * bh < min_px:
            continue
        block = labels[ys, xs] == idx
        cands = []
        # The block as it is. A thin line of the background's own colour drawn
        # on the picture (a white frame on a white window) leaves holes in it,
        # but its outline is still the picture's.
        if block.mean() >= 0.6:
            cands.append((xs.start, ys.start, xs.stop, ys.stop))
        # The block with what touches the picture but is not part of it (a
        # toolbar, a scale bar) trimmed off: the longest run of rows and of
        # columns that are almost all picture.
        r0, r1 = _longest_run(block.mean(axis=1) >= _AREA_LINE_SHARE)
        c0, c1 = _longest_run(block.mean(axis=0) >= _AREA_LINE_SHARE)
        if r1 - r0 >= 8 and c1 - c0 >= 8:
            inner = block[r0:r1, c0:c1]
            r0b, r1b = _longest_run(inner.mean(axis=1) >= _AREA_LINE_SHARE)
            c0b, c1b = _longest_run(inner.mean(axis=0) >= _AREA_LINE_SHARE)
            cands.append((xs.start + c0 + c0b, ys.start + r0 + r0b,
                          xs.start + c0 + c1b, ys.start + r0 + r1b))
        for x0, y0, x1, y1 in cands:
            aw, ah = x1 - x0, y1 - y0
            if aw * ah < min_px or not _aspect_ok(aw, ah, frame_w, frame_h):
                continue
            if aw * ah > best_size:
                best, best_size = (int(x0), int(y0), int(x1), int(y1)), aw * ah
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Finding the white rectangle in the picture
# ─────────────────────────────────────────────────────────────────────────────

def white_mask(img: np.ndarray) -> np.ndarray:
    rgb = img[:, :, :3].astype(np.int16)
    return (rgb.min(axis=2) >= _WHITE_MIN) & \
           ((rgb.max(axis=2) - rgb.min(axis=2)) <= _WHITE_SPREAD)


def _line_centre(shares: np.ndarray, start: int, step: int) -> float:
    """Centre of the band of white lines that begins at `start` and runs in
    direction `step` (+1 or -1) while each line is at least half white."""
    i = start
    n = 0
    while 0 <= i < len(shares) and shares[i] >= 0.5 and n < _MAX_LINE_PX + 2:
        i += step
        n += 1
    n = max(n, 1)
    return start + step * (n - 1) / 2.0


def find_white_rect(img: np.ndarray, area: tuple) -> "tuple | None":
    """The white outline inside `area` as (left, top, right, bottom) in window
    pixels, measured at the centre of each line; or None."""
    from scipy.ndimage import (binary_dilation, binary_opening, find_objects,
                               label as _label)
    x0, y0, x1, y1 = area
    sub = img[y0:y1, x0:x1]
    ah, aw = sub.shape[:2]
    white = white_mask(sub)
    # Filled white (thicker than any line) is not part of an outline.
    k = _MAX_LINE_PX + 2
    filled = binary_opening(white, structure=np.ones((k, k), bool))
    if filled.any():
        filled = binary_dilation(filled, structure=np.ones((3, 3), bool))
        white = white & ~filled
    labels, n = _label(white, structure=np.ones((3, 3), bool))
    best = None
    best_size = 0
    for idx, sl in enumerate(find_objects(labels), start=1):
        if sl is None:
            continue
        ys, xs = sl
        bw, bh = xs.stop - xs.start, ys.stop - ys.start
        if bw < _MIN_RECT_SHARE * aw or bh < _MIN_RECT_SHARE * ah:
            continue
        if bw > _MAX_RECT_SHARE * aw and bh > _MAX_RECT_SHARE * ah:
            continue
        comp = labels[ys, xs] == idx
        band = min(_MAX_LINE_PX, max(1, min(bw, bh) // 4))
        top = comp[:band].any(axis=0).mean()
        bottom = comp[-band:].any(axis=0).mean()
        left = comp[:, :band].any(axis=1).mean()
        right = comp[:, -band:].any(axis=1).mean()
        if min(top, bottom, left, right) < _EDGE_SHARE:
            continue
        m = band + 2
        inside = white[ys.start + m: ys.stop - m, xs.start + m: xs.stop - m]
        if inside.size and inside.mean() > _INSIDE_SHARE:
            continue
        if bw * bh <= best_size:
            continue
        rows = comp.mean(axis=1)
        cols = comp.mean(axis=0)
        t = _line_centre(rows, 0, +1)
        b = _line_centre(rows, bh - 1, -1)
        l = _line_centre(cols, 0, +1)
        r = _line_centre(cols, bw - 1, -1)
        best = (x0 + xs.start + l, y0 + ys.start + t,
                x0 + xs.start + r, y0 + ys.start + b)
        best_size = bw * bh
    return best


def to_fractions(rect: tuple, area: tuple) -> tuple:
    """The rectangle's edges as fractions [0, 1] of the camera picture.

    A line centre at window pixel x covers the frame from x to x+1, so its
    centre in the picture is (x + 0.5 - x0) / width."""
    x0, y0, x1, y1 = area
    aw, ah = x1 - x0, y1 - y0
    l, t, r, b = rect
    clamp = lambda v: min(1.0, max(0.0, v))
    return (clamp((l + 0.5 - x0) / aw), clamp((t + 0.5 - y0) / ah),
            clamp((r + 0.5 - x0) / aw), clamp((b + 0.5 - y0) / ah))


def read_image(img: np.ndarray, frame_w: "int | None" = None,
               frame_h: "int | None" = None, area: "tuple | None" = None) -> WindowRead:
    """Read a window picture. `area` given by hand skips the picture search."""
    if area is None:
        area = find_picture_area(img, frame_w, frame_h)
        if area is None:
            return WindowRead(img, None, None, None,
                              "The camera picture was not found in the window. "
                              "Drag its area in the preview and read again.")
    rect = find_white_rect(img, area)
    if rect is None:
        return WindowRead(img, area, None, None,
                          "No white reference frame was found in the camera picture.")
    return WindowRead(img, area, rect, to_fractions(rect, area), None)


# ─────────────────────────────────────────────────────────────────────────────
# Windows: listing and photographing
# ─────────────────────────────────────────────────────────────────────────────

# Shell and system windows that are never a camera program.
_EXCLUDED_CLASSES = {
    "progman", "shell_traywnd", "shell_secondarytraywnd", "workerw",
    "windows.ui.core.corewindow", "applicationframewindow",
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

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
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
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
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
                RECT=RECT, BITMAPINFO=BITMAPINFO, BITMAPINFOHEADER=BITMAPINFOHEADER,
                DWORD=wintypes.DWORD)
    return _api


def list_windows() -> "list[tuple[int, str]]":
    """Visible, not minimised top-level windows with a title, as (handle, title),
    sorted by title. Image Tools' own windows are left out."""
    if sys.platform != "win32":
        return []
    api = _win32()
    user32 = api["user32"]
    own_pid = os.getpid()
    found = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if (cls.value or "").lower() in _EXCLUDED_CLASSES:
            return True
        pid = api["DWORD"]()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        found.append((int(hwnd), title))
        return True

    user32.EnumWindows(api["enum_proc"](_cb), 0)
    found.sort(key=lambda t: t[1].lower())
    return found


def find_window(title: str) -> "int | None":
    """Handle of a listed window with exactly this title, or None."""
    for hwnd, t in list_windows():
        if t == title:
            return hwnd
    return None


def _bgra_to_rgb(raw: bytes, w: int, h: int) -> np.ndarray:
    a = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)
    return np.ascontiguousarray(a[:, :, 2::-1])


def _frame_rect(hwnd):
    """The window's visible frame on screen (no Win 11 shadow) and its full rect."""
    api = _win32()
    RECT = api["RECT"]
    win = RECT()
    if not api["user32"].GetWindowRect(hwnd, ctypes.byref(win)):
        raise RuntimeError("the window's size could not be read")
    frame = RECT()
    if api["dwm"].DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame),
                                        ctypes.sizeof(RECT)) != 0:
        frame = win
    return win, frame


def _read_bitmap(api, mem, bmp, w, h) -> bytes:
    bmi = api["BITMAPINFO"]()
    bmi.bmiHeader.biSize = ctypes.sizeof(api["BITMAPINFOHEADER"])
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h                 # top row first
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    if not api["gdi32"].GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
        raise RuntimeError("the window's picture could not be read")
    return buf.raw


def _print_window(hwnd) -> np.ndarray:
    api = _win32()
    user32, gdi32 = api["user32"], api["gdi32"]
    win, frame = _frame_rect(hwnd)
    bw = max(1, win.right - win.left)
    bh = max(1, win.bottom - win.top)
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
            raw = _read_bitmap(api, mem, bmp, bw, bh)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
    finally:
        user32.ReleaseDC(hwnd, hdc)
    img = _bgra_to_rgb(raw, bw, bh)
    x0 = max(0, frame.left - win.left)
    y0 = max(0, frame.top - win.top)
    x1 = min(bw, frame.right - win.left)
    y1 = min(bh, frame.bottom - win.top)
    return img[y0:y1, x0:x1].copy()


def _copy_screen(hwnd) -> np.ndarray:
    """The window's frame copied off the screen (whatever lies over it too)."""
    api = _win32()
    user32, gdi32 = api["user32"], api["gdi32"]
    _win, rect = _frame_rect(hwnd)
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
            raw = _read_bitmap(api, mem, bmp, w, h)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
    finally:
        user32.ReleaseDC(None, screen)
    return _bgra_to_rgb(raw, w, h)


def grab_window(hwnd) -> np.ndarray:
    """The window's picture as an RGB array. Raises RuntimeError on failure.

    Nothing is activated or raised, so the keyboard focus stays put."""
    if sys.platform != "win32":
        raise RuntimeError("reading a window works on Windows only")
    api = _win32()
    if not api["user32"].IsWindow(hwnd):
        raise RuntimeError("the window is closed")
    if api["user32"].IsIconic(hwnd):
        raise RuntimeError("the window is minimised")
    img = _print_window(hwnd)
    if img.size and int(img.max()) - int(img.min()) <= 2:
        # Drawn as one flat colour: a GPU-drawn program. Copy it off the screen.
        img = _copy_screen(hwnd)
    return img


def read_window(hwnd, frame_w: "int | None" = None, frame_h: "int | None" = None,
                area: "tuple | None" = None) -> WindowRead:
    """Photograph the window and read it. Raises nothing."""
    try:
        img = grab_window(hwnd)
    except Exception as exc:
        return WindowRead(None, None, None, None,
                          f"The window could not be read: {exc}.")
    if area is not None:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = area
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            area = None
    return read_image(img, frame_w, frame_h, area)
