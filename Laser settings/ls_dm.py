"""Laser settings — which set-up the deformable mirror is on, read off a picture.

The deformable mirror (DM) is driven by "Mirror Control Window" on a PC that
nothing here can reach: no network share, no internet, no way to install a
program. The only way in is the Remote Desktop window that OPR1 keeps open to it
(192.168.1.107). So OPR1 takes a picture of that window — exactly the way
`ls_screen` photographs the L3BT MSS window — and finds the right-hand 6 × 6
grid, "Set-up", in it.

WHAT IS READ, AND WHAT IS NOT
-----------------------------
The numbers 0–35 in the cells are only the electrode numbers; they are the same
in every set-up. The voltage of each electrode is shown only as the COLOUR of
its cell (blue −150 V, white 0, red +150 V). No voltage is worked out from it —
asked for on 8.10.2026: "nemusí to psát hodnotu, ta je k ničemu, ale musí to
rozpoznat, jaký nastavení tam je použitý". So the 36 colours are compared with
set-ups somebody saved under a name ("Low power", "High power", …), and the
answer is the name of the one that matches, or "not a saved set-up".

HOW THE GRID IS FOUND WHEREVER IT IS
------------------------------------
The Remote Desktop window can be any size (the picture is scaled with it), the
Mirror Control Window can be anywhere on the remote screen, and SID4's window
can lie over parts of it. So nothing is at a fixed place:

  1. The colour scale is found first: a vertical strip, pure red at the top,
     white in the middle, pure blue at the bottom. Red pixels are found with one
     byte-pattern search over the whole picture, which costs a few milliseconds.
  2. Both grids have such a scale. Set-up's is on its RIGHT, so the grid wanted
     is the one found by walking LEFT from a scale. Applied voltages has only
     black background and the "150 / 0 / -150" labels there, so it is never
     taken. SID4's own scale (red, yellow, green, blue) has no white middle.
  3. The cells touch each other with no lines between them, and every cell
     colour has red or blue at full strength. So the grid's edges are where that
     stops — at the grey frame — measured along several lines and the middle
     value taken, so a cursor or a stray window corner does not move them.
  4. Each cell's colour is the middle value of eight spots in a ring around the
     black electrode number, never on it.

Where the scale was found is remembered and tried first next time.

Two set-ups match when no cell differs by more than `MATCH_TOL` in any colour
channel. The two screenshots this was built from (testing/dm_0727.png and
dm_0731.png) differ by up to 49 levels in single cells and are told apart
easily; the same set-up read twice is identical to the last level.

THE FILES ON THE SHARE
----------------------
    <scratch>\\Software\\LaserSettings\\dm_state.json    what OPR1 saw last
    <scratch>\\Software\\LaserSettings\\dm_setups.json   the saved set-ups

OPR1 rewrites the first every 5 s. When the Remote Desktop window is minimised
it cannot be photographed (Windows does not draw a minimised window), and the
file then says so with NO colours — asked for on 8.10.2026, "Prázdné hodnoty":
the old colours are not carried on as if they were still true.

No Qt in here. The picture is plain bytes, so all of this is tested from saved
screenshots (`testing/test_dm_grid.py`).
"""

from __future__ import annotations

import ctypes
import datetime
import json
import os
import re
import socket
import struct
import sys
import threading
import time
import zlib
from pathlib import Path
from typing import NamedTuple

import ls_screen
from ls_screen import Picture

# ─────────────────────────────────────────────────────────────────────────────
# What is read
# ─────────────────────────────────────────────────────────────────────────────

GRID_N = 6                      # 6 × 6 electrodes
CELLS = GRID_N * GRID_N

# The text that identifies the Remote Desktop window: its title is
# "192.168.1.107 - Remote Desktop Connection".
DEFAULT_TITLE = "192.168.1.107"

# How often OPR1 looks and writes, and when a reading stops counting.
PERIOD_S = 5.0
STALE_AFTER_S = 30.0

# Two colours are the same set-up when no channel of any cell is further apart
# than this. The copy of the screen is lossless, but Remote Desktop itself is
# not: on OPR1 (9.10.2026) the Low power grid came through up to 7 levels off
# the 8.10 screenshots ((255, 4, 1) for pure red). Low and High power differ by
# up to 51, in 22 cells by more than 10, so 15 still tells them apart.
MATCH_TOL = 15

# What the state file says.
STATUS_OK = "ok"
STATUS_MINIMISED = "minimised"
STATUS_NO_WINDOW = "no window"
STATUS_NO_GRID = "no grid"
STATUS_FAILED = "failed"

STATUS_TEXT = {
    STATUS_OK: "The Set-up grid was read.",
    STATUS_MINIMISED: "The Remote Desktop window is minimised, so it cannot "
                      "be read.",
    STATUS_NO_WINDOW: "The Remote Desktop window is not open.",
    STATUS_NO_GRID: "The Set-up grid of Mirror Control Window is not visible "
                    "in the Remote Desktop window.",
    STATUS_FAILED: "The Remote Desktop window could not be read.",
}


class Bar(NamedTuple):
    """A colour scale: its columns and rows, inclusive."""
    x0: int
    x1: int
    y0: int
    y1: int


class Spot(NamedTuple):
    """The grid's cells, without the frame: inclusive pixel edges."""
    x0: int
    y0: int
    x1: int
    y1: int


class DmRead(NamedTuple):
    """One look at the Remote Desktop window.

    `colours` is 36 (r, g, b) tuples, electrode 0 first, only when `status` is
    STATUS_OK — otherwise None. `bar` is where the scale was, to try first next
    time. `problem` is a sentence for the operator when something went wrong.
    """
    status: str
    colours: list | None
    at: float
    bar: Bar | None = None
    problem: str | None = None
    took_ms: float | None = None    # the whole look, picture included
    picture: Picture | None = None  # what was photographed; dropped once used
    screen_picture: Picture | None = None   # the screen copy, when also tried
    how: str | None = None          # HOW_WINDOW or HOW_SCREEN: what was read


# ─────────────────────────────────────────────────────────────────────────────
# Pixels
# ─────────────────────────────────────────────────────────────────────────────

def _rgb(pic, x, y):
    i = (y * pic.width + x) * 4
    d = pic.data
    return d[i + 2], d[i + 1], d[i]


def _is_red(c):
    return c[0] >= 200 and c[1] <= 70 and c[2] <= 70


def _is_blue(c):
    return c[2] >= 200 and c[0] <= 70 and c[1] <= 70


def _on_scale(c):
    """On the red → white → blue path: (255, t, t) or (t, t, 255)."""
    r, g, b = c
    return ((r >= 200 and abs(g - b) <= 40)
            or (b >= 200 and abs(r - g) <= 40))


def _is_cell(c):
    """A cell's colour: red or blue at full strength, the other two equal.

    The grey frame (217 / 179 / 89) and the black background never are; the
    black electrode numbers never are.
    """
    r, g, b = c
    return max(r, b) >= 235 and abs(g - min(r, b)) <= 30


# Red pixels are found without a Python loop over the picture: each colour
# channel is cut out with one slice, turned into a 0/1 byte per pixel with one
# translate, the three are ANDed as big integers, and runs of three red pixels
# are found with a regex. A byte pattern on the BGRA bytes themselves does not
# work: shifted by a byte, a black pixel's opaque alpha (255) passes for red,
# and a black screen was half a million false starts.
_HIGH = bytes(1 if v >= 200 else 0 for v in range(256))
_LOW = bytes(1 if v <= 70 else 0 for v in range(256))
_RED_RUN = re.compile(rb"\x01{3,}")


def _red_mask(pic):
    """One byte per pixel, 1 where the pixel is pure red."""
    d = pic.data
    n = pic.width * pic.height
    r = int.from_bytes(d[2::4].translate(_HIGH), "big")
    g = int.from_bytes(d[1::4].translate(_LOW), "big")
    b = int.from_bytes(d[0::4].translate(_LOW), "big")
    return (r & g & b).to_bytes(n, "big")


def _median(values):
    values = sorted(values)
    return values[len(values) // 2]


# ─────────────────────────────────────────────────────────────────────────────
# Finding the scale and the grid
# ─────────────────────────────────────────────────────────────────────────────

def _bar_at(pic, x, y):
    """The colour scale through a red pixel at (x, y), or None."""
    if not _is_red(_rgb(pic, x, y)):
        return None
    top = y
    while top > 0 and _is_red(_rgb(pic, x, top - 1)):
        top -= 1
    bottom = top
    while bottom + 1 < pic.height and _on_scale(_rgb(pic, x, bottom + 1)):
        bottom += 1
    height = bottom - top + 1
    if height < 40 or not _is_blue(_rgb(pic, x, bottom)):
        return None
    # White in the middle: SID4's rainbow scale goes through yellow and green.
    mid = range(top + int(height * 0.4), top + int(height * 0.6) + 1)
    if not any(min(_rgb(pic, x, yy)) >= 225 for yy in mid):
        return None
    row = top + 1
    x0 = x
    while x0 > 0 and _is_red(_rgb(pic, x0 - 1, row)):
        x0 -= 1
    x1 = x
    while x1 + 1 < pic.width and _is_red(_rgb(pic, x1 + 1, row)):
        x1 += 1
    width = x1 - x0 + 1
    if width < 3 or width > height / 2:
        return None
    return Bar(x0, x1, top, bottom)


def find_bars(pic):
    """Every red-to-blue colour scale in the picture, left to right."""
    bars = []
    rejected = {}               # column -> the last row it was turned down on
    for m in _RED_RUN.finditer(_red_mask(pic)):
        x, y = m.start() % pic.width, m.start() // pic.width
        if x + 2 >= pic.width:
            continue            # the run wrapped onto the next row
        # The run's MIDDLE, not its first pixel: through Remote Desktop the
        # scale's edge column is blended with the black beside it (OPR1,
        # 9.10.2026: (193, 122, 119) half-way down), so a walk down that
        # column stops short and the scale was never found.
        x += (min(m.end() - m.start(), pic.width - x) - 1) // 2
        if any(b.x0 <= x <= b.x1 and b.y0 <= y <= b.y1 for b in bars):
            continue
        # A red block that is not a scale is met again on each of its rows,
        # starting at the same column: one look per block is enough.
        last = rejected.get(x)
        if last is not None and y - last <= 2:
            rejected[x] = y
            continue
        bar = _bar_at(pic, x, y)
        if bar is None:
            rejected[x] = y
            continue
        bars.append(bar)
    bars.sort(key=lambda b: b.x0)
    return bars


# The cells are NOT the grid's width divided by six. The graph cuts the last
# column and the last row short: on the 8.10.2026 screenshots the cells are
# 34 px apart and the last ones are 28 px, so the grid is 5 + 28/34 cells wide.
# The electrode numbers sit in the middle of the FULL 34 px. This ratio is the
# first guess; the real distance is then measured from where the colours change.
_SPAN_CELLS = 5 + 28 / 34


def _lines(first, last):
    """One line through each of the six rows (or columns) between two edges,
    a fifth of a cell in — well clear of the electrode number."""
    pitch = (last - first + 1) / _SPAN_CELLS
    return [min(last, first + int(pitch * (k + 0.2))) for k in range(GRID_N)]


def _walk(pic, x, y, dx, dy):
    """From a cell pixel, the last cell pixel in one direction.

    A gap of up to two pixels is stepped over: in a window drawn smaller, the
    edge between a red cell and a blue one blends into a colour that is
    neither. The frame and the black around the grid are wider than that.
    """
    last = (x, y)
    gap = 0
    while True:
        x += dx
        y += dy
        if not (0 <= x < pic.width and 0 <= y < pic.height):
            return last
        if _is_cell(_rgb(pic, x, y)):
            last = (x, y)
            gap = 0
        else:
            gap += 1
            if gap > 2:
                return last


def grid_left_of(pic, bar):
    """The 6 × 6 grid whose scale is `bar`, on the scale's LEFT, or None."""
    height = bar.y1 - bar.y0 + 1
    reach = max(6, int(height * 0.12))
    rows = _lines(bar.y0, bar.y1)

    rights = []
    for y in rows:
        for x in range(bar.x0 - 1, max(-1, bar.x0 - 1 - reach), -1):
            if _is_cell(_rgb(pic, x, y)):
                rights.append(x)
                break
    if len(rights) < GRID_N - 1:
        return None
    x_right = _median(rights)

    lefts = []
    for y in rows:
        if not _is_cell(_rgb(pic, x_right, y)):
            continue
        lefts.append(_walk(pic, x_right, y, -1, 0)[0])
    if len(lefts) < GRID_N - 1:
        return None
    x_left = _median(lefts)
    width = x_right - x_left + 1
    if width < 30:
        return None

    mid_y = (bar.y0 + bar.y1) // 2
    cols = _lines(x_left, x_right)
    tops, bottoms = [], []
    for x in cols:
        if not _is_cell(_rgb(pic, x, mid_y)):
            continue
        tops.append(_walk(pic, x, mid_y, 0, -1)[1])
        bottoms.append(_walk(pic, x, mid_y, 0, 1)[1])
    if len(tops) < GRID_N - 1:
        return None
    y_top, y_bottom = _median(tops), _median(bottoms)
    grid_h = y_bottom - y_top + 1
    if not (0.8 * height <= grid_h <= 1.25 * height):
        return None
    if not (0.5 <= width / grid_h <= 2.0):
        return None
    return Spot(x_left, y_top, x_right, y_bottom)


def _pitch(pic, spot, across):
    """The distance between cell edges, along x (`across`) or along y."""
    if across:
        start, end = spot.x0, spot.x1
        lines = _lines(spot.y0, spot.y1)
    else:
        start, end = spot.y0, spot.y1
        lines = _lines(spot.x0, spot.x1)
    guess = (end - start + 1) / _SPAN_CELLS
    edges = set()
    for line in lines:
        prev = None
        for pos in range(start, end + 1):
            c = _rgb(pic, pos, line) if across else _rgb(pic, line, pos)
            if prev is not None and c != prev \
                    and _is_cell(c) and _is_cell(prev):
                edges.add(pos)
            prev = c
    # A scaled picture blends an edge over a pixel or two: each run of
    # neighbouring change points is one edge, at its middle.
    merged, run = [], []
    for pos in sorted(edges):
        if run and pos - run[-1] > 2:
            merged.append(sum(run) / len(run))
            run = []
        run.append(pos)
    if run:
        merged.append(sum(run) / len(run))
    fits = []
    for e in merged:
        k = round((e - start) / guess)
        if 1 <= k < GRID_N and abs(e - start - k * guess) < guess * 0.25:
            fits.append((e - start) / k)
    return sum(fits) / len(fits) if fits else guess


# Eight spots in a ring around the electrode number, as fractions of a cell.
_RING = ((0.15, 0.15), (0.5, 0.15), (0.85, 0.15), (0.15, 0.5),
         (0.85, 0.5), (0.15, 0.85), (0.5, 0.85), (0.85, 0.85))


def cell_colours(pic, spot):
    """The 36 cell colours, electrode 0 (top left) first, or None."""
    cw = _pitch(pic, spot, True)
    ch = _pitch(pic, spot, False)
    out = []
    for row in range(GRID_N):
        for col in range(GRID_N):
            found = []
            for fx, fy in _RING:
                x = int(spot.x0 + (col + fx) * cw)
                y = int(spot.y0 + (row + fy) * ch)
                # The last column and row are cut short: a spot past the
                # grid's edge is left out rather than read off the frame.
                if x > spot.x1 - 2 or y > spot.y1 - 2:
                    continue
                c = _rgb(pic, x, y)
                if _is_cell(c):
                    found.append(c)
            if len(found) < 3:
                return None
            out.append(tuple(_median([c[i] for c in found]) for i in range(3)))
    return out


def read_picture(pic, hint=None, now_s=None):
    """Find the Set-up grid in a picture of the Remote Desktop window."""
    now = time.time() if now_s is None else now_s
    if pic.width < 60 or pic.height < 60:
        return DmRead(STATUS_NO_GRID, None, now,
                      problem="The Remote Desktop window is too small to read.")
    candidates = []
    if hint is not None and hint.x1 < pic.width and hint.y1 < pic.height:
        bar = _bar_at(pic, hint.x0 + (hint.x1 - hint.x0) // 2, hint.y0 + 1)
        if bar is not None:
            candidates.append(bar)
    tried_all = False
    while True:
        for bar in candidates:
            spot = grid_left_of(pic, bar)
            if spot is None:
                continue
            colours = cell_colours(pic, spot)
            if colours is not None:
                return DmRead(STATUS_OK, colours, now, bar=bar)
        if tried_all:
            break
        tried_all = True
        candidates = find_bars(pic)
    return DmRead(STATUS_NO_GRID, None, now,
                  problem=f"{STATUS_TEXT[STATUS_NO_GRID]} "
                          f"({describe_picture(pic, candidates)})")


def describe_picture(pic, bars=None):
    """A few words on what a picture with no grid in it does hold.

    Added 9.10.2026: OPR1 said "not visible" while the grid was plainly on its
    screen, and the sentence alone could not tell a black picture (Windows did
    not draw the remote screen into it) from a grid that was there but not
    recognised.
    """
    n = pic.width * pic.height
    step = max(1, n // 20000)
    d = pic.data
    seen = dark = 0
    for i in range(0, n, step):
        j = i * 4
        seen += 1
        if d[j] < 16 and d[j + 1] < 16 and d[j + 2] < 16:
            dark += 1
    words = f"picture {pic.width} × {pic.height}"
    if seen and dark >= 0.98 * seen:
        return (f"{words}, almost all black — Windows did not draw the remote "
                f"screen into it")
    bars = find_bars(pic) if bars is None else bars
    if not bars:
        return f"{words}, no red-white-blue colour scale in it"
    return (f"{words}, {len(bars)} colour scale{'s' if len(bars) != 1 else ''} "
            f"found, but no 6 × 6 grid on the left of "
            f"{'them' if len(bars) != 1 else 'it'}")


def png_bytes(pic):
    """The picture as a PNG file, without Qt or PIL (the dm thread has
    neither, and the builder needs nothing new)."""
    w, h = pic.width, pic.height
    d = bytes(pic.data)
    rgb = bytearray(w * h * 3)
    rgb[0::3] = d[2::4]
    rgb[1::3] = d[1::4]
    rgb[2::3] = d[0::4]
    stride = w * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride]
                   for y in range(h))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


# ─────────────────────────────────────────────────────────────────────────────
# Comparing with the saved set-ups
# ─────────────────────────────────────────────────────────────────────────────

def colour_difference(a, b):
    """The largest difference of any channel of any cell; None if unusable."""
    if not a or not b or len(a) != CELLS or len(b) != CELLS:
        return None
    try:
        return max(abs(int(x) - int(y))
                   for ca, cb in zip(a, b) for x, y in zip(ca, cb))
    except (TypeError, ValueError):
        return None


def cells_differing(a, b, tol=MATCH_TOL):
    """The electrode numbers whose colour differs by more than `tol`."""
    if not a or not b or len(a) != CELLS or len(b) != CELLS:
        return []
    out = []
    for i, (ca, cb) in enumerate(zip(a, b)):
        try:
            if max(abs(int(x) - int(y)) for x, y in zip(ca, cb)) > tol:
                out.append(i)
        except (TypeError, ValueError):
            out.append(i)
    return out


class Match(NamedTuple):
    """`name` is the matching set-up or None; `closest` the nearest one
    whatever the distance, with `differing` its electrodes that differ."""
    name: str | None
    closest: str | None
    difference: int | None
    differing: list


def match(colours, setups):
    """Which saved set-up these colours are."""
    best, best_d = None, None
    for name, entry in (setups or {}).items():
        d = colour_difference(colours, (entry or {}).get("colours"))
        if d is None:
            continue
        if best_d is None or d < best_d:
            best, best_d = name, d
    if best is None:
        return Match(None, None, None, [])
    differing = cells_differing(colours, setups[best].get("colours"))
    return Match(best if best_d <= MATCH_TOL else None, best, best_d, differing)


# ─────────────────────────────────────────────────────────────────────────────
# The two set-ups the program knows by itself
# ─────────────────────────────────────────────────────────────────────────────
# Low power and High power are the two set-ups the Overview judges against
# SBW4's energy, so they cannot depend on somebody having saved them first. The
# colours are the Set-up grid of the two screenshots the user sent on 8.10.2026
# (testing/dm_0727.png = High power, testing/dm_0731.png = Low power), read with
# `read_picture` and written in here so the program never needs the pictures.
# The names came with the pictures but only their times survived; 8.10 guessed
# 7:27 = Low and it was the wrong way round — on 9.10.2026 OPR1 read the 7:27
# grid while the mirror was on high power ("když tam je high power nastavení,
# tak to ukazuje low power a naopak").
# They are on the saved list of every PC as "built in": they cannot be deleted,
# and an entry of the same name in the shared file is overruled. Since
# 12.10.2026 they CAN be renamed — see `rename_setup` and `"replaces"`.

POWER_LOW = "Low power"
POWER_HIGH = "High power"

REFERENCE_SETUPS = {
    POWER_HIGH: [
        (195, 195, 255), (205, 205, 255), (255, 181, 181), (230, 230, 255),
        (255, 255, 255), (255, 245, 245),
        (195, 195, 255), (101, 101, 255), (225, 225, 255), (108, 108, 255),
        (125, 125, 255), (255, 235, 235),
        (255, 255, 255), (255, 230, 230), (255, 230, 230), (195, 195, 255),
        (205, 205, 255), (255, 245, 245),
        (255, 205, 205), (255, 158, 158), (83, 83, 255), (255, 240, 240),
        (195, 195, 255), (185, 185, 255),
        (255, 255, 255), (255, 220, 220), (185, 185, 255), (133, 133, 255),
        (255, 200, 200), (163, 163, 255),
        (145, 145, 255), (255, 210, 210), (255, 210, 210), (255, 255, 255),
        (181, 181, 255), (225, 225, 255),
    ],
    POWER_LOW: [
        (195, 195, 255), (200, 200, 255), (255, 230, 230), (240, 240, 255),
        (255, 255, 255), (255, 210, 210),
        (200, 200, 255), (122, 122, 255), (230, 230, 255), (122, 122, 255),
        (145, 145, 255), (255, 230, 230),
        (255, 240, 240), (255, 200, 200), (255, 225, 225), (195, 195, 255),
        (225, 225, 255), (255, 215, 215),
        (255, 210, 210), (255, 141, 141), (111, 111, 255), (255, 215, 215),
        (195, 195, 255), (195, 195, 255),
        (255, 225, 225), (255, 200, 200), (220, 220, 255), (145, 145, 255),
        (255, 176, 176), (181, 181, 255),
        (137, 137, 255), (255, 205, 205), (255, 220, 220), (255, 210, 210),
        (220, 220, 255), (250, 250, 255),
    ],
}
BUILT_IN_BY = "built in"

# Which set-ups the Overview accepts for high-power and for low-power shots:
# ticked on the DM settings tab, any number of each (9.10.2026). Kept as the
# entry's "power" in the shared file, so every PC judges alike. A set-up is
# ticked for one of the two or for neither, never both. The built-in two start
# ticked as their names say; unticking one is written to the file as an entry
# of that name holding only "power" (its colours stay built in).
TAG_HIGH = "high"
TAG_LOW = "low"
TAG_NONE = ""
TAGS = (TAG_HIGH, TAG_LOW, TAG_NONE)
REFERENCE_TAGS = {POWER_LOW: TAG_LOW, POWER_HIGH: TAG_HIGH}


def is_reference(name):
    """True for Low power and High power, the two set-ups nobody can delete."""
    return name in REFERENCE_SETUPS


def replaced_references(setups):
    """The built-in set-ups that have been renamed: an own entry carries
    `"replaces": <built-in name>` (12.10.2026, `rename_setup`)."""
    return {e.get("replaces") for e in (setups or {}).values()
            if isinstance(e, dict) and is_reference(e.get("replaces"))}


def with_references(setups):
    """The saved set-ups with the two built-in ones first, overruling an entry
    of the same name in the shared file — all but its tick.

    A built-in one that has been renamed is not added: its colours are on the
    list under the new name, as an entry of the file (`rename_setup`).
    """
    setups = setups or {}
    gone = replaced_references(setups)
    out = {}
    for name in (POWER_LOW, POWER_HIGH):            # this order on the list
        if name in gone:
            continue
        colours = REFERENCE_SETUPS[name]
        tag = (setups.get(name) or {}).get("power")
        out[name] = {"colours": [list(c) for c in colours], "saved": "",
                     "by": BUILT_IN_BY,
                     "power": tag if tag in TAGS else REFERENCE_TAGS[name]}
    for name, entry in setups.items():
        if name not in out:
            out[name] = entry
    return out


def tag_of(name, setups):
    """TAG_HIGH, TAG_LOW or TAG_NONE: what the set-up `name` is ticked for."""
    entry = (setups or {}).get(name) or {}
    tag = entry.get("power")
    if tag in TAGS:
        return tag
    return REFERENCE_TAGS.get(name, TAG_NONE)


def set_tag(setups, name, tag):
    """A copy of `setups` with `name` ticked for `tag`, or (None, why not)."""
    if name not in setups:
        return None, f"\"{name}\" is not a saved set-up any more."
    if tag not in TAGS:
        return None, None
    out = dict(setups)
    out[name] = {**out[name], "power": tag}
    return out, None


def for_file(setups):
    """What of `setups` goes into the shared file: every own set-up, and of the
    built-in two only a tick that differs from their name."""
    out = {}
    for name, entry in setups.items():
        if not is_reference(name):
            out[name] = entry
            continue
        tag = tag_of(name, setups)
        if tag != REFERENCE_TAGS[name]:
            out[name] = {"power": tag}
    return out


def mirror_now(view, now_s=None):
    """(what to write, tag, the sentence behind it) for the DM settings line
    of the Overview and the Limits tab, from the engine's last `DmView`.

    `tag` is None when the mirror is not known (not read, stale); otherwise
    what the matching set-up is ticked for — TAG_NONE also for a grid that is
    no saved set-up. The text is the set-up's name, or the banner's own words
    ("Unknown", "No fresh reading", "Not a saved set-up", "Not read yet").
    """
    state = view.state if view is not None else None
    setups = view.setups if view is not None else with_references({})
    v = verdict(state, setups, now_s)
    if v.colours is None:
        tag = None
    elif v.match is not None and v.match.name is not None:
        tag = tag_of(v.match.name, setups)
    else:
        tag = TAG_NONE
    return v.title, tag, v.detail


# ─────────────────────────────────────────────────────────────────────────────
# What the DM settings tab says
# ─────────────────────────────────────────────────────────────────────────────

VERDICT_MATCH = "match"         # a saved set-up
VERDICT_OTHER = "other"         # read fine, but not a saved set-up
VERDICT_UNKNOWN = "unknown"     # OPR1 could not read the grid
VERDICT_STALE = "stale"         # OPR1 has not written for a while
VERDICT_NONE = "none"           # no state file at all


class Verdict(NamedTuple):
    kind: str
    title: str                  # the big words
    detail: str                 # one line under them
    colours: list | None        # what to draw, only when fresh and read
    match: Match | None


def clock_text(at):
    try:
        return datetime.datetime.fromtimestamp(float(at)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def age_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def verdict(state, setups, now_s=None):
    """What the tab says about the state file OPR1 wrote."""
    now = time.time() if now_s is None else now_s
    if not isinstance(state, dict):
        return Verdict(VERDICT_NONE, "Not read yet",
                       "No PC has written the mirror's state yet.", None, None)
    host = str(state.get("host") or "the reading PC")
    at = state.get("at")
    try:
        age = now - float(at)
    except (TypeError, ValueError):
        age = None
    if age is None or age > STALE_AFTER_S:
        when = (f"last written {age_text(age)} ago, at {clock_text(at)}"
                if age is not None else "never written")
        return Verdict(VERDICT_STALE, "No fresh reading",
                       f"{host} has stopped writing — {when}.", None, None)
    status = state.get("status")
    colours = state.get("colours")
    if status != STATUS_OK or not isinstance(colours, list) \
            or len(colours) != CELLS:
        why = STATUS_TEXT.get(status) or str(state.get("problem")
                                             or STATUS_TEXT[STATUS_FAILED])
        return Verdict(VERDICT_UNKNOWN, "Unknown",
                       f"{why} (on {host}, {clock_text(at)})", None, None)
    m = match(colours, setups)
    if m.name is not None:
        return Verdict(VERDICT_MATCH, m.name,
                       f"Read on {host} at {clock_text(at)}.", colours, m)
    if m.closest is None:
        detail = "No set-up has been saved yet."
    else:
        n = len(m.differing)
        detail = (f"Closest saved set-up: {m.closest} — {n} electrode"
                  f"{'s' if n != 1 else ''} differ.")
    return Verdict(VERDICT_OTHER, "Not a saved set-up",
                   f"{detail} Read on {host} at {clock_text(at)}.", colours, m)


# ─────────────────────────────────────────────────────────────────────────────
# The Remote Desktop window (Windows only)
# ─────────────────────────────────────────────────────────────────────────────

# The Remote Desktop client's own window class. Another program whose title
# happens to carry the address (a browser tab, an Explorer folder) is skipped by
# `ls_screen._EXCLUDED_CLASSES`; this one is preferred when there are several.
_RDP_CLASS = "tscshellcontainerclass"


def find_rdp_window(title=DEFAULT_TITLE):
    """(hwnd, minimised) of the window whose title contains `title`.

    Minimised windows ARE found here — unlike `ls_screen.find_window` — so that
    "minimised" can be said instead of "not open". A window that is not
    minimised wins over one that is, the Remote Desktop client over anything
    else.
    """
    api = ls_screen._win32()
    user32 = api["user32"]
    needle = (title or "").strip().lower()
    if not needle:
        return None, False
    found = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if needle not in (buf.value or "").lower():
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        name = (cls.value or "").lower()
        if name in ls_screen._EXCLUDED_CLASSES:
            return True
        found.append((bool(user32.IsIconic(hwnd)), name != _RDP_CLASS, hwnd))
        return True

    user32.EnumWindows(api["enum_proc"](_cb), 0)
    if not found:
        return None, False
    found.sort(key=lambda f: (f[0], f[1]))
    iconic, _other, hwnd = found[0]
    return hwnd, iconic


HOW_WINDOW = "window"           # PrintWindow: the window drawn into memory
HOW_SCREEN = "screen"           # its rectangle copied off the screen

# Set once the screen copy has read the grid; only the dm thread touches it.
_screen_first = False


def _read_both_ways(hwnd, hint, now):
    """The window drawn into memory first; when no grid is in that, the
    window's rectangle copied off the screen.

    PrintWindow reads a window under others, but on 9.10.2026 OPR1's Remote
    Desktop window — maximised, nothing over it — gave a picture with no grid,
    while a screenshot of the screen showed it. The screen copy needs the
    window uncovered; the first way does not. Both are kept for the picture
    saved to the share.

    Once the screen copy has worked it is tried FIRST, so a PC where the first
    way never works does not pay for both every 5 s.
    """
    global _screen_first
    if _screen_first:
        try:
            shot = ls_screen.grab_screen_of(hwnd)
            read = read_picture(shot, hint, now)
        except Exception:
            read = None
        if read is not None and read.status == STATUS_OK:
            return read._replace(screen_picture=shot, how=HOW_SCREEN)
        _screen_first = False           # covered now, perhaps: both again
    try:
        pic = ls_screen.grab_window(hwnd)
        first = read_picture(pic, hint, now)._replace(picture=pic,
                                                      how=HOW_WINDOW)
    except Exception as exc:
        first = DmRead(STATUS_FAILED, None, now, how=HOW_WINDOW,
                       problem=f"The window could not be drawn: {exc}.")
    if first.status == STATUS_OK:
        return first
    try:
        shot = ls_screen.grab_screen_of(hwnd)
    except Exception as exc:
        return first._replace(problem=f"{first.problem}. The screen could "
                                      f"not be copied either: {exc}.")
    second = read_picture(shot, hint, now)
    if second.status == STATUS_OK:
        _screen_first = True
        return second._replace(picture=first.picture, screen_picture=shot,
                               how=HOW_SCREEN)
    why = (second.problem or "").replace(STATUS_TEXT[STATUS_NO_GRID], "")
    return first._replace(screen_picture=shot,
                          problem=f"{first.problem}. Copied off the screen, "
                                  f"no grid either {why.strip()}.")


def read_window(title=DEFAULT_TITLE, hint=None, now_s=None):
    """Find the Remote Desktop window, photograph it, read the grid.

    Raises nothing. Never activates, moves or resizes the window: PrintWindow
    draws it into memory and nothing else is asked of it.
    """
    now = time.time() if now_s is None else now_s
    started = time.perf_counter()
    if sys.platform != "win32":
        return DmRead(STATUS_FAILED, None, now,
                      problem="The window can only be read on Windows.")
    try:
        hwnd, minimised = find_rdp_window(title)
        if not hwnd:
            read = DmRead(STATUS_NO_WINDOW, None, now,
                          problem=STATUS_TEXT[STATUS_NO_WINDOW])
        elif minimised:
            read = DmRead(STATUS_MINIMISED, None, now,
                          problem=STATUS_TEXT[STATUS_MINIMISED])
        else:
            read = _read_both_ways(hwnd, hint, now)
    except Exception as exc:
        read = DmRead(STATUS_FAILED, None, now,
                      problem=f"The Remote Desktop window could not be read: "
                              f"{exc}.")
    return read._replace(took_ms=(time.perf_counter() - started) * 1000.0)


# ─────────────────────────────────────────────────────────────────────────────
# The share
# ─────────────────────────────────────────────────────────────────────────────
# Same two names, same order and same probing as `Diagnostic/shared_pvs.py`:
# the office leg first, and every probe on a daemon thread under a deadline,
# because a single look at the leg this PC cannot reach blocks for ~48 s.

SHARE_CANDIDATES = (
    r"\\hapls-share.cs.eli-beams.eu\scratch\Software",     # office leg
    r"\\hapls-share.lcs.local\scratch\Software",           # lab leg
)
SHARE_SUBDIR = "LaserSettings"
STATE_NAME = "dm_state.json"
SETUPS_NAME = "dm_setups.json"
PROBE_TIMEOUT_S = 3.0
REPROBE_AFTER_S = 60.0


def _first_reachable(roots, timeout_s):
    state = {}
    cond = threading.Condition()

    def probe(root):
        try:
            ok = os.path.isdir(root)
        except OSError:
            ok = False
        with cond:
            state[root] = ok
            cond.notify_all()

    for root in roots:
        threading.Thread(target=probe, args=(root,), daemon=True,
                         name="dm-share-probe").start()
    deadline = time.monotonic() + max(0.1, timeout_s)
    with cond:
        while True:
            undecided = False
            for root in roots:
                if root not in state:
                    undecided = True
                    break
                if state[root]:
                    return root
            if not undecided:
                return ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            cond.wait(remaining)
        for root in roots:
            if state.get(root):
                return root
    return ""


class Share:
    """The share folder, found once and kept; looked for again after a miss.

    Used from ONE thread, the engine's "dm" worker.
    """

    def __init__(self, roots=SHARE_CANDIDATES):
        self._roots = list(roots)
        self._root = ""
        self._missed_at = None

    def folder(self):
        """The LaserSettings folder on the share, or None when out of reach."""
        if not self._root:
            if (self._missed_at is not None
                    and time.monotonic() - self._missed_at < REPROBE_AFTER_S):
                return None
            self._root = _first_reachable(self._roots, PROBE_TIMEOUT_S)
            if not self._root:
                self._missed_at = time.monotonic()
                return None
        return Path(self._root) / SHARE_SUBDIR

    def lost(self):
        """A read or write failed: look for the share again next time."""
        self._root = ""
        self._missed_at = time.monotonic()


def write_json_atomic(path, obj):
    """Readers only ever see the old file or the new one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def state_document(read, setups=None, every_s=PERIOD_S):
    """The state file's contents for one look. `every_s` is how often the
    writing PC looks; the reading PCs read the file that often."""
    colours = ([list(c) for c in read.colours]
               if read.status == STATUS_OK and read.colours else None)
    m = match(read.colours, setups) if colours else None
    return {
        "version": 1,
        "at": read.at,
        "at_text": datetime.datetime.fromtimestamp(read.at).isoformat(
            timespec="seconds"),
        "host": socket.gethostname(),
        "status": read.status,
        "status_text": STATUS_TEXT.get(read.status, ""),
        "problem": read.problem,
        "setup": m.name if m else None,
        "difference": m.difference if m else None,
        "took_ms": (round(read.took_ms, 1) if read.took_ms is not None
                    else None),
        "how": read.how,
        "every_s": every_s,
        "colours": colours,
    }


def read_json(path):
    """The document in a JSON file; None when there is none. Raises on a
    broken one, so "unreadable" is never mistaken for "empty"."""
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def clean_setups(raw):
    """{name: {"colours": [[r, g, b] × 36], "saved", "by"}}, bad ones dropped."""
    out = {}
    if not isinstance(raw, dict):
        return out
    items = raw.get("setups") if isinstance(raw.get("setups"), dict) else raw
    for name, entry in items.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(entry, dict):
            continue
        name = name.strip()
        tag = entry.get("power")
        if is_reference(name):
            # Only the tick of a built-in set-up is kept in the file.
            if tag in TAGS:
                out[name] = {"power": tag}
            continue
        colours = entry.get("colours")
        if not isinstance(colours, list) or len(colours) != CELLS:
            continue
        try:
            colours = [[max(0, min(255, int(v))) for v in c] for c in colours]
        except (TypeError, ValueError):
            continue
        if any(len(c) != 3 for c in colours):
            continue
        out[name] = {"colours": colours,
                     "saved": str(entry.get("saved") or ""),
                     "by": str(entry.get("by") or ""),
                     "power": tag if tag in TAGS else TAG_NONE}
        if is_reference(entry.get("replaces")):
            out[name]["replaces"] = entry["replaces"]
    return out


def setups_document(setups):
    return {"version": 1, "setups": setups}


def add_setup(setups, name, colours, now_s=None):
    """A copy of `setups` with this one added, or (None, why not)."""
    name = (name or "").strip()
    if not name:
        return None, "Type a name for the set-up first."
    if is_reference(name):
        return None, f"\"{name}\" is the name of a built-in set-up."
    if name in setups:
        return None, (f"A set-up called \"{name}\" already exists. Delete it "
                      f"first, or pick another name.")
    if not colours or len(colours) != CELLS:
        return None, "There is no fresh reading of the grid to save."
    m = match(colours, setups)
    if m.name is not None:
        return None, (f"These colours are already saved, as \"{m.name}\".")
    now = time.time() if now_s is None else now_s
    out = dict(setups)
    out[name] = {"colours": [list(c) for c in colours],
                 "saved": datetime.datetime.fromtimestamp(now).isoformat(
                     timespec="seconds"),
                 "by": socket.gethostname()}
    return out, None


def rename_setup(setups, old, new):
    """A copy of `setups` with `old` called `new`, or (None, why not).

    A built-in set-up can be renamed too (12.10.2026): it becomes an entry of
    the shared file under the new name, holding the built-in colours, its
    tick and `"replaces": old`, and `with_references` stops adding the
    built-in one. Deleting that entry brings the built-in name back.
    """
    new = (new or "").strip()
    if old not in setups:
        return None, f"\"{old}\" is not a saved set-up any more."
    if not new:
        return None, "Type the new name first."
    if new == old:
        return None, None
    if is_reference(new):
        return None, f"\"{new}\" is the name of a built-in set-up."
    if new in setups:
        return None, f"A set-up called \"{new}\" already exists."
    out = {}
    for name, entry in setups.items():
        if name == old and is_reference(old):
            entry = {"colours": [list(c) for c in REFERENCE_SETUPS[old]],
                     "saved": "", "by": BUILT_IN_BY,
                     "power": tag_of(old, setups), "replaces": old}
        out[new if name == old else name] = entry
    return out, None


def delete_setups(setups, names):
    """Without `names`. The two built-in set-ups stay whatever is asked."""
    gone = {n for n in names if not is_reference(n)}
    return {n: e for n, e in setups.items() if n not in gone}


class DmView(NamedTuple):
    """Everything the two tabs draw, from one pass of the dm thread.

    `state` is the state file's document (on the writing PC, the one it has
    just written), `setups` the saved set-ups, `local` this PC's own look at
    the window (None unless this PC writes), `problem` a sentence when the share
    could not be used, `note` what came of a save, rename or delete.
    """
    state: dict | None
    setups: dict
    local: DmRead | None
    problem: str | None
    note: str | None
    at: float
    picture_note: str | None = None     # where the last picture was saved


def picture_name(how=HOW_WINDOW):
    """This PC's picture of the Remote Desktop window on the share — one file
    per way of taking it, overwritten each time, so nothing piles up."""
    host = re.sub(r"[^A-Za-z0-9_-]+", "_", socket.gethostname()) or "pc"
    return f"dm_look_{host}_{how}.png"


def _save_pictures(folder, pics, at):
    """Write the look's pictures next to the state file; a sentence either
    way. `pics` is [(how, Picture)]."""
    saved = []
    try:
        for how, pic in pics:
            path = Path(folder) / picture_name(how)
            data = png_bytes(pic)
            tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except Exception:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            saved.append(f"{path} ({len(data) // 1024} kB)")
    except Exception as exc:
        return f"The picture could not be saved: {exc}"
    return (f"Picture{'s' if len(saved) != 1 else ''} of the {clock_text(at)} "
            f"look saved: " + ", ".join(saved) + ".")


def exchange(share, local, edits, save_picture=False, every_s=PERIOD_S):
    """One pass on the share: apply the edits, write the state, read it back.

    Runs on the engine's dm thread. The set-ups file is read BEFORE it is
    changed and only written when it was read: an unreadable file is reported,
    never replaced by a list holding just the one new set-up.

    `save_picture` also writes what was photographed (`picture_name`). The
    picture itself never leaves this function: the view keeps the look
    without it, so a whole screen is not held in memory between looks.
    """
    picture_note = None
    pics = []
    if local is not None:
        pics = [(how, p) for how, p in ((HOW_WINDOW, local.picture),
                                        (HOW_SCREEN, local.screen_picture))
                if p is not None]
        local = local._replace(picture=None, screen_picture=None)
    pic = pics or None
    now = time.time()
    folder = share.folder()
    if folder is None:
        problem = ("The scratch share cannot be reached, so the mirror's state "
                   "is neither written nor read.")
        note = ("Nothing was saved: the scratch share cannot be reached."
                if edits else None)
        refs = with_references({})
        state = state_document(local, refs) if local is not None else None
        if save_picture and pic is not None:
            picture_note = ("The picture could not be saved: the scratch share "
                            "cannot be reached.")
        return DmView(state, refs, local, problem, note, now, picture_note)
    notes = []
    try:
        setups_path = folder / SETUPS_NAME
        setups = clean_setups(read_json(setups_path))
        changed = False
        for edit in edits:
            # A bug in one edit is that edit's failure, said as such — not the
            # share's, and never a reason to drop the list on the floor.
            # The edit sees the built-in set-ups too (so their colours cannot
            # be saved again under another name); they never go into the file.
            try:
                new, note = edit(with_references(setups))
            except Exception as exc:
                new, note = None, f"The change could not be made: {exc}"
            if note:
                notes.append(note)
            if new is not None:
                setups = for_file(new)
                changed = True
        if changed:
            write_json_atomic(setups_path, setups_document(setups))
        setups = with_references(setups)
        state_path = folder / STATE_NAME
        if local is not None:
            state = state_document(local, setups, every_s)
            write_json_atomic(state_path, state)
        else:
            state = read_json(state_path)
        if save_picture and pic is not None:
            picture_note = _save_pictures(folder, pics, local.at)
    except Exception as exc:
        share.lost()
        if edits and not notes:
            notes.append("Nothing was saved: the set-ups file could not be "
                         "used.")
        refs = with_references({})
        state = state_document(local, refs) if local is not None else None
        return DmView(state, refs, local,
                      f"The files on the scratch share could not be used: "
                      f"{exc}", " ".join(notes) or None, now, picture_note)
    return DmView(state if isinstance(state, dict) else None, setups, local,
                  None, " ".join(notes) or None, now, picture_note)


def share_folder_text():
    """Where the files are, for the tabs to show."""
    return " or ".join(str(Path(r) / SHARE_SUBDIR) for r in SHARE_CANDIDATES)
