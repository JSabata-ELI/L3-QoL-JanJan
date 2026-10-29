"""Laser settings — which set-up the deformable mirror is on, read off a picture.

The deformable mirror (DM) is driven by "Mirror Control Window" on a PC that
nothing here can reach: no network share, no internet, no way to install a
program. The only way in is the Remote Desktop window that OPR1 keeps open to it
(192.168.1.107). So OPR1 takes a picture of that window — exactly the way
`ls_screen` photographs the L3BT MSS window — and finds the LEFT-hand 6 × 6
grid, "Applied voltages", in it. That is what is really on the mirror; the
right-hand "Set-up" grid is only what the operator is about to apply (user,
12.10.2026: "se musí kontrolovat to nastavení vlevo!! ne vpravo"). It was the
right-hand one until then.

Since 14.10.2026 the Set-up grid is read TOO, but only so that a set-up can be
saved from it before it is applied ("uložit z pravé mřížky a sledovat levou
mřížku v programu"). The verdict — which set-up the mirror is on — still comes
from the Applied grid alone. The two grids of one set-up differ by a few levels
(see `REFERENCE_SETUPS`), well inside `MATCH_TOL`, so a set-up saved off the
right grid is recognised on the left one once it is applied.

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
  2. Both grids have such a scale. Applied voltages' is on its LEFT, so the
     grid wanted is the one found by walking RIGHT from a scale. Set-up's
     scale has only black background and the "150 / 0 / -150" labels on its
     right, so it is never taken. SID4's own scale (red, yellow, green, blue)
     has no white middle.
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
    <scratch>\\Software\\Laser settings\\shared\\dm_state.json    what OPR1 saw last
    <scratch>\\Software\\Laser settings\\shared\\dm_setups.json   the saved set-ups

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
    STATUS_OK: "The Applied voltages grid was read.",
    STATUS_MINIMISED: "The Remote Desktop window is minimised, so it cannot "
                      "be read.",
    STATUS_NO_WINDOW: "The Remote Desktop window is not open.",
    STATUS_NO_GRID: "The Applied voltages grid of Mirror Control Window is "
                    "not visible in the Remote Desktop window.",
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

    `setup_colours` is the right-hand Set-up grid, read the same way, when it
    could be seen — None otherwise. It is never judged: it is only there so a
    set-up can be SAVED from it before it is applied (14.10.2026, "uložit z
    pravé mřížky a sledovat levou mřížku v programu").
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
    setup_colours: list | None = None   # the right-hand Set-up grid, if seen


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
    """The 6 × 6 grid whose scale is `bar`, on the scale's LEFT, or None.
    That is the Set-up grid — what the operator means to apply."""
    return _grid_beside(pic, bar, -1)


def grid_right_of(pic, bar):
    """The 6 × 6 grid whose scale is `bar`, on the scale's RIGHT, or None.
    That is the Applied voltages grid — what is really on the mirror."""
    return _grid_beside(pic, bar, 1)


def _grid_beside(pic, bar, step):
    """The grid next to a scale: `step` -1 looks left of it, +1 right."""
    height = bar.y1 - bar.y0 + 1
    reach = max(6, int(height * 0.12))
    rows = _lines(bar.y0, bar.y1)

    start = bar.x0 - 1 if step < 0 else bar.x1 + 1
    stop = (max(-1, start - reach) if step < 0
            else min(pic.width, start + reach))
    nears = []
    for y in rows:
        # The scale's own edge column may lie just past `bar`: its width is
        # measured on one row near the top, where Remote Desktop blended that
        # column, while further down it is plain scale colour — (255, 23, 20)
        # on OPR1, 27.10.2026, which passes for a red cell, so the grid was
        # "found" one pixel wide. Step over what is still on the scale first;
        # three pixels at most, so the grey frame can never carry the search
        # into the grid.
        first = start
        while (first != stop and abs(first - start) < 3
               and _on_scale(_rgb(pic, first, y))):
            first += step
        for x in range(first, stop, step):
            if _is_cell(_rgb(pic, x, y)):
                nears.append(x)
                break
    if len(nears) < GRID_N - 1:
        return None
    x_near = _median(nears)

    fars = []
    for y in rows:
        if not _is_cell(_rgb(pic, x_near, y)):
            continue
        fars.append(_walk(pic, x_near, y, step, 0)[0])
    if len(fars) < GRID_N - 1:
        return None
    x_far = _median(fars)
    x_left, x_right = min(x_near, x_far), max(x_near, x_far)
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
    """Find the Applied voltages grid in a picture of the Remote Desktop
    window — the LEFT one, what is really on the mirror (12.10.2026)."""
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
            spot = grid_right_of(pic, bar)
            if spot is None:
                continue
            colours = cell_colours(pic, spot)
            if colours is not None:
                return DmRead(STATUS_OK, colours, now, bar=bar,
                              setup_colours=read_setup_grid(pic, bar, spot))
        if tried_all:
            break
        tried_all = True
        candidates = find_bars(pic)
    return DmRead(STATUS_NO_GRID, None, now,
                  problem=f"{STATUS_TEXT[STATUS_NO_GRID]} "
                          f"({describe_picture(pic, candidates)})")


def read_setup_grid(pic, applied_bar, applied_spot):
    """The right-hand Set-up grid's 36 colours, or None when it is not seen.

    Its scale stands on its RIGHT and level with the Applied voltages scale, so
    it is looked for along a few rows near the top of that scale, to the right
    of the Applied grid: every red run met there is tried as a scale, and the
    first one with a grid on its left is it. A red cell of the Set-up grid's
    own top row is met first and turned down by `_bar_at` (it is not a red →
    white → blue strip). No full search of the picture, so this costs about
    nothing on top of the Applied grid's read.

    Never needed for the verdict, so a covered or missing Set-up grid is
    simply None — it cannot make the Applied grid unreadable.
    """
    height = applied_bar.y1 - applied_bar.y0 + 1
    for dy in (1, 3, max(4, height // 20)):
        y = applied_bar.y0 + dy
        if y >= pic.height:
            break
        x = applied_spot.x1 + 1
        while x < pic.width:
            if not _is_red(_rgb(pic, x, y)):
                x += 1
                continue
            end = x
            while end + 1 < pic.width and _is_red(_rgb(pic, end + 1, y)):
                end += 1
            bar = _bar_at(pic, (x + end) // 2, y)
            if bar is not None:
                spot = grid_left_of(pic, bar)
                if spot is not None and spot.x0 > applied_spot.x1:
                    return cell_colours(pic, spot)
            x = end + 1
    return None


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
            f"found, but no 6 × 6 grid on the right of "
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
# colours are the Applied voltages grid (the left one; re-read 12.10.2026 when
# the reading moved there — up to 5 levels off the Set-up grid's, which was
# what they were before) of the two screenshots the user sent on 8.10.2026
# (testing/dm_0727.png = High power, testing/dm_0731.png = Low power), read with
# `read_picture` and written in here so the program never needs the pictures.
# The names came with the pictures but only their times survived; 8.10 guessed
# 7:27 = Low and it was the wrong way round — on 9.10.2026 OPR1 read the 7:27
# grid while the mirror was on high power ("když tam je high power nastavení,
# tak to ukazuje low power a naopak").
# They are on the saved list of every PC as "built in" — but only as DEFAULTS
# (21.10.2026: a new Low power grid could not be saved over the old one, and the
# crew had to make a third set-up). Since then the shared file can:
#   - replace their grid: an entry of that name WITH colours (`replace_setup`;
#     the grid it replaced is kept as "previous" for `undo_replace`),
#   - delete them: an entry `{"removed": true}` (`for_file`), brought back by
#     `restore_builtins`,
#   - rename them (12.10.2026): an entry under the new name with `"replaces"`.
# An entry of that name holding only "power" is just its tick, as before.

POWER_LOW = "Low power"
POWER_HIGH = "High power"

REFERENCE_SETUPS = {
    POWER_HIGH: [
        (200, 200, 255), (205, 205, 255), (255, 181, 181), (230, 230, 255),
        (255, 255, 255), (255, 245, 245),
        (200, 200, 255), (101, 101, 255), (225, 225, 255), (108, 108, 255),
        (125, 125, 255), (255, 235, 235),
        (255, 255, 255), (255, 230, 230), (255, 230, 230), (200, 200, 255),
        (205, 205, 255), (255, 245, 245),
        (255, 205, 205), (255, 158, 158), (80, 80, 255), (255, 240, 240),
        (200, 200, 255), (185, 185, 255),
        (255, 255, 255), (255, 220, 220), (185, 185, 255), (133, 133, 255),
        (255, 200, 200), (163, 163, 255),
        (145, 145, 255), (255, 210, 210), (255, 210, 210), (255, 255, 255),
        (181, 181, 255), (225, 225, 255),
    ],
    POWER_LOW: [
        (200, 200, 255), (200, 200, 255), (255, 230, 230), (240, 240, 255),
        (255, 255, 255), (255, 210, 210),
        (200, 200, 255), (122, 122, 255), (230, 230, 255), (122, 122, 255),
        (145, 145, 255), (255, 235, 235),
        (255, 240, 240), (255, 200, 200), (255, 225, 225), (200, 200, 255),
        (220, 220, 255), (255, 215, 215),
        (255, 210, 210), (255, 141, 141), (111, 111, 255), (255, 215, 215),
        (200, 200, 255), (200, 200, 255),
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
    """True for the names Low power and High power (built in, or replaced)."""
    return name in REFERENCE_SETUPS


def is_builtin_entry(name, entry):
    """True when `entry` is the built-in set-up `name` as the program knows
    it — not replaced, not renamed onto. Only its tick goes into the file."""
    if not is_reference(name) or not isinstance(entry, dict):
        return False
    return (entry.get("by") == BUILT_IN_BY and not entry.get("replaced")
            and not entry.get("replaces")
            and colour_difference(entry.get("colours"),
                                  REFERENCE_SETUPS[name]) == 0)


def name_key(name):
    """What two names are compared by: case and spacing do not count, so
    "low power " is "Low power" (21.10.2026)."""
    return " ".join(str(name or "").split()).casefold()


def find_name(setups, name):
    """The set-up in `setups` called `name` (any case, any spacing), or None."""
    key = name_key(name)
    for existing in (setups or {}):
        if name_key(existing) == key:
            return existing
    return None


def tidy_name(name):
    """The name as it is saved: spacing tidied, and a built-in name in its
    own spelling ("low power" → "Low power")."""
    name = " ".join(str(name or "").split())
    for ref in REFERENCE_SETUPS:
        if name_key(ref) == name_key(name):
            return ref
    return name


def replaced_references(setups):
    """The built-in set-ups that have been renamed: an own entry carries
    `"replaces": <built-in name>` (12.10.2026, `rename_setup`)."""
    return {e.get("replaces") for e in (setups or {}).values()
            if isinstance(e, dict) and is_reference(e.get("replaces"))}


def _has_colours(entry):
    return isinstance(entry, dict) and isinstance(entry.get("colours"), list)


def with_references(setups):
    """The saved set-ups with the two built-in ones first.

    The shared file's entry of a built-in name decides what it is: one with
    colours replaces the built-in grid, `{"removed": true}` takes it off the
    list, one with only "power" is its tick. A built-in one that has been
    renamed is not added either: its colours are on the list under the new
    name, as an entry of the file (`rename_setup`).
    """
    setups = setups or {}
    gone = replaced_references(setups)
    out = {}
    for name in (POWER_LOW, POWER_HIGH):            # this order on the list
        entry = setups.get(name) or {}
        tag = entry.get("power")
        tag = tag if tag in TAGS else REFERENCE_TAGS[name]
        if _has_colours(entry):
            out[name] = {**entry, "power": tag}
            continue
        if entry.get("removed") or name in gone:
            continue
        colours = REFERENCE_SETUPS[name]
        out[name] = {"colours": [list(c) for c in colours], "saved": "",
                     "by": BUILT_IN_BY, "power": tag}
    for name, entry in setups.items():
        if name not in out and not is_reference(name):
            out[name] = entry
    return out


def missing_builtins(setups):
    """The built-in names no set-up on the list carries (deleted or renamed)."""
    return [n for n in (POWER_LOW, POWER_HIGH) if find_name(setups, n) is None]


def ticked_twice(setups):
    """{TAG_HIGH / TAG_LOW: [names]} where more than one set-up is ticked for
    it — the Overview then accepts any of them, an old grid as well."""
    out = {}
    for tag in (TAG_HIGH, TAG_LOW):
        names = [n for n in (setups or {}) if tag_of(n, setups) == tag]
        if len(names) > 1:
            out[tag] = names
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
    """What of `setups` goes into the shared file: every own set-up; of a
    built-in one as the program knows it only a tick that differs from its
    name; a replaced built-in one whole; and a built-in one missing from the
    list (deleted, not renamed) as `{"removed": true}`."""
    out = {}
    for name, entry in setups.items():
        if not is_builtin_entry(name, entry):
            out[name] = entry
            continue
        tag = tag_of(name, setups)
        if tag != REFERENCE_TAGS[name]:
            out[name] = {"power": tag}
    renamed = replaced_references(setups)
    for name in REFERENCE_SETUPS:
        if name not in setups and name not in renamed:
            out[name] = {"removed": True}
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


def setup_grid(state, now_s=None):
    """The right-hand Set-up grid from the state file — 36 colours, only while
    the file is fresh and the Applied grid was read with it; otherwise None.

    For saving a set-up before it is applied. Never judged: what is on the
    mirror is the Applied voltages grid alone.
    """
    if not isinstance(state, dict) or state.get("status") != STATUS_OK:
        return None
    now = time.time() if now_s is None else now_s
    try:
        if now - float(state.get("at")) > STALE_AFTER_S:
            return None
    except (TypeError, ValueError):
        return None
    colours = state.get("setup_colours")
    if not isinstance(colours, list) or len(colours) != CELLS:
        return None
    return colours


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
# Inside the program's own folder on the share (14.10.2026), not a second
# top-level folder beside it. Dev Tools never deletes a "shared" subfolder on
# deploy (SHARED_DATA_DIR_NAMES in Dev Tools/dt_core.py).
SHARE_SUBDIR = os.path.join("Laser settings", "shared")
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
        """Laser settings\\shared on the share, or None when out of reach."""
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
    setup_colours = ([list(c) for c in read.setup_colours]
                     if colours and read.setup_colours else None)
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
        # The right-hand Set-up grid, for "Save the Set-up grid" on every PC.
        # A reader of an older writer's file simply finds no key.
        "setup_colours": setup_colours,
    }


def read_json(path):
    """The document in a JSON file; None when there is none. Raises on a
    broken one, so "unreadable" is never mistaken for "empty"."""
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_colours(colours):
    """36 [r, g, b] lists, or None when `colours` is not that."""
    if not isinstance(colours, list) or len(colours) != CELLS:
        return None
    try:
        colours = [[max(0, min(255, int(v))) for v in c] for c in colours]
    except (TypeError, ValueError):
        return None
    if any(len(c) != 3 for c in colours):
        return None
    return colours


def clean_setups(raw):
    """{name: {"colours": [[r, g, b] × 36], "saved", "by", "power", …}}, bad
    ones dropped. A built-in name may also hold only its tick ({"power"}) or
    `{"removed": true}`."""
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
        colours = _clean_colours(entry.get("colours"))
        if is_reference(name) and colours is None:
            if entry.get("removed"):
                out[name] = {"removed": True}
            elif tag in TAGS:
                out[name] = {"power": tag}
            continue
        if colours is None:
            continue
        default_tag = REFERENCE_TAGS.get(name, TAG_NONE)
        out[name] = {"colours": colours,
                     "saved": str(entry.get("saved") or ""),
                     "by": str(entry.get("by") or ""),
                     "power": tag if tag in TAGS else default_tag}
        if is_reference(entry.get("replaces")):
            out[name]["replaces"] = entry["replaces"]
        if entry.get("replaced"):
            out[name]["replaced"] = str(entry["replaced"])
            out[name]["replaced_by"] = str(entry.get("replaced_by") or "")
        prev = entry.get("previous")
        prev_colours = _clean_colours(prev.get("colours")) \
            if isinstance(prev, dict) else None
        if prev_colours is not None:
            out[name]["previous"] = {
                "colours": prev_colours,
                "replaced": str(prev.get("replaced") or ""),
                "replaced_by": str(prev.get("replaced_by") or "")}
    return out


def setups_document(setups):
    return {"version": 1, "setups": setups}


def _stamp(now_s=None):
    now = time.time() if now_s is None else now_s
    return datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")


def _saved_elsewhere(colours, setups, but=None):
    """The other set-up these colours already are, or None."""
    others = {n: e for n, e in (setups or {}).items() if n != but}
    return match(colours, others).name


def add_setup(setups, name, colours, now_s=None):
    """A copy of `setups` with this one added, or (None, why not).

    A deleted built-in name can be taken again ("Low power" saved anew); it
    starts ticked as its name says, like the built-in one did.
    """
    name = tidy_name(name)
    if not name:
        return None, "Type a name for the set-up first."
    existing = find_name(setups, name)
    if existing is not None:
        return None, (f"A set-up called \"{existing}\" already exists. Click "
                      f"the save button again to replace its grid.")
    if not colours or len(colours) != CELLS:
        return None, "There is no fresh reading of the grid to save."
    twin = _saved_elsewhere(colours, setups)
    if twin is not None:
        return None, (f"These colours are already saved, as \"{twin}\".")
    out = dict(setups)
    out[name] = {"colours": [list(c) for c in colours],
                 "saved": _stamp(now_s),
                 "by": socket.gethostname()}
    if is_reference(name):
        out[name]["power"] = REFERENCE_TAGS[name]
    return out, None


def replace_setup(setups, name, colours, now_s=None):
    """A copy of `setups` with the grid of `name` replaced by `colours`, or
    (None, why not) — 21.10.2026.

    The name, its place on the list and its High power / Low power tick stay.
    The grid it had is kept as "previous", so `undo_replace` can bring it back.
    A built-in one becomes an entry of the file holding the new grid.
    """
    actual = find_name(setups, name)
    if actual is None:
        return None, f"\"{name}\" is not a saved set-up any more."
    if not colours or len(colours) != CELLS:
        return None, "There is no fresh reading of the grid to save."
    twin = _saved_elsewhere(colours, setups, but=actual)
    if twin is not None:
        return None, (f"These colours are already saved, as \"{twin}\".")
    entry = setups[actual]
    d = colour_difference(colours, entry.get("colours"))
    if d is not None and d <= MATCH_TOL:
        return None, f"\"{actual}\" already has this grid."
    new = {k: v for k, v in entry.items() if k != "previous"}
    new.update({
        "colours": [list(c) for c in colours],
        "power": tag_of(actual, setups),
        "replaced": _stamp(now_s),
        "replaced_by": socket.gethostname(),
        "previous": {"colours": [list(c) for c in entry.get("colours") or []],
                     "replaced": str(entry.get("replaced") or ""),
                     "replaced_by": str(entry.get("replaced_by") or "")},
    })
    if colour_difference(new["previous"]["colours"], colours) is None:
        del new["previous"]
    out = dict(setups)
    out[actual] = new
    return out, None


def undo_replace(setups, name):
    """A copy of `setups` with `name` back on the grid it had before its last
    replace, or (None, why not). Only one step back is kept."""
    if name not in setups:
        return None, f"\"{name}\" is not a saved set-up any more."
    entry = setups[name]
    prev = entry.get("previous")
    if not isinstance(prev, dict) or not prev.get("colours"):
        return None, f"\"{name}\" has not been replaced: nothing to undo."
    twin = _saved_elsewhere(prev["colours"], setups, but=name)
    if twin is not None:
        return None, (f"The previous grid of \"{name}\" is saved, as "
                      f"\"{twin}\", already.")
    new = {k: v for k, v in entry.items()
           if k not in ("previous", "replaced", "replaced_by")}
    new["colours"] = [list(c) for c in prev["colours"]]
    if prev.get("replaced"):
        new["replaced"] = prev["replaced"]
        new["replaced_by"] = prev.get("replaced_by", "")
    out = dict(setups)
    out[name] = new
    return out, None


def restore_builtins(setups):
    """(a copy of `setups` with the deleted built-in set-ups back first,
    the sentence to show) — (None, why not) when there is none to bring back.

    One whose grid is already saved under another name is not brought back
    twice; the sentence says where it is.
    """
    missing = missing_builtins(setups)
    if not missing:
        return None, "Low power and High power are both on the list."
    back, notes = [], []
    for name in missing:
        twin = _saved_elsewhere(REFERENCE_SETUPS[name], setups)
        if twin is not None:
            notes.append(f"The built-in {name} grid is saved, as \"{twin}\".")
        else:
            back.append(name)
    if not back:
        return None, " ".join(notes)
    out = {}
    for name in (POWER_LOW, POWER_HIGH):
        if name in back:
            out[name] = {"colours": [list(c) for c in REFERENCE_SETUPS[name]],
                         "saved": "", "by": BUILT_IN_BY,
                         "power": REFERENCE_TAGS[name]}
    out.update(setups)
    words = " and ".join(back)
    return out, " ".join([f"Built-in {words} restored."] + notes)


def rename_setup(setups, old, new):
    """A copy of `setups` with `old` called `new`, or (None, why not).

    A built-in set-up can be renamed too (12.10.2026): it becomes an entry of
    the shared file under the new name, holding the built-in colours, its
    tick and `"replaces": old`, and `with_references` stops adding the
    built-in one. A built-in name nobody carries can be given to another
    set-up (21.10.2026).
    """
    new = tidy_name(new)
    if old not in setups:
        return None, f"\"{old}\" is not a saved set-up any more."
    if not new:
        return None, "Type the new name first."
    if new == old:
        return None, None
    clash = find_name(setups, new)
    if clash is not None and clash != old:
        return None, f"A set-up called \"{clash}\" already exists."
    out = {}
    for name, entry in setups.items():
        if name == old and is_builtin_entry(old, entry):
            entry = {"colours": [list(c) for c in REFERENCE_SETUPS[old]],
                     "saved": "", "by": BUILT_IN_BY,
                     "power": tag_of(old, setups), "replaces": old}
        elif name == old:
            entry = {**entry, "power": tag_of(old, setups)}
        out[new if name == old else name] = entry
    return out, None


def delete_setups(setups, names):
    """Without `names` — the built-in ones too since 21.10.2026 (`for_file`
    then writes them as removed; `restore_builtins` brings them back)."""
    gone = set(names)
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
