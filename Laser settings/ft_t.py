"""Laser settings — the Settings tab: how big the first tab is, and where.

Everything on the Overview that is a matter of taste rather than of the machine
lives here. Nothing on this tab reads a channel and nothing on it can change a
limit — the limits are the Limits tab.

    fit to the window   work the four sizes out from the window's own size
    the five squares    their size and their place, arranged by hand on a board
    the word in them    SHOOTING / idle / not read, on or off, and how big
    the list            the typeface, the size, bold, and the verdict square
    the window          what size the Maximise button makes it
    saved settings      named sets of all the above and more, on the share

SAVED SETTINGS (12.10.2026): one set per operator, on the scratch share so
every PC sees the same list (`ls_core.SET_PARTS`, `settings_sets.json`). Pick
one in the drop-down and press Load: the ticked parts of it replace this PC's.
The share is read and written on a thread of its own (a missing share costs
seconds), and polled from here; the file is read again before every change, so
a set another PC saved meanwhile is never lost.

THE FIT WRITES ITS ANSWER INTO THE BOXES. `ov_t.OverviewTab._fit_to_window`
works the four numbers out and puts them in the settings document; `park()`
puts them in the spin boxes with their signals blocked. So whatever a box says
is what is really on screen — which the first version of this did not manage,
and "Height of the band 400 px" over a band 253 px tall was the complaint.

`_loading` is held around every programmatic write (`reload()` and `park()`)
and every handler returns on it, so A SIGNAL THAT GETS THROUGH IS ALWAYS A
HUMAN. That is what lets `_size_typed` switch the fit off in one line without
having to guess who moved the box. Copied from `Image Tools/is_t.py`'s Auto
sliders, which say the same thing in `_refresh_auto_bc_sliders`.

THE BOARD
---------
A scale model of the strip the squares live in on the Overview. Drag a square
to move it, drag an edge or a corner to resize it, and the edges snap to the
other squares and to the band's own middle — the same free-form board Image
Slider uses for its cameras, and the same shake-to-free: waggle the pointer
during a drag and the snapping lets go for the rest of that drag.

The first drag takes the arrangement off automatic and it stays where it is put,
in fractions of the band, so it keeps its proportions when the window is dragged
wider. "Arrange them again" is the way back to the even row of five.

Renamed on 1.10.2026. This tab was called Font and held the three list
settings only; it is the Settings tab now, and what was the Settings tab is the
Limits tab. The FILE is still `ft_t.py`: a module has exactly one home in this
repo and renaming it would cost the build config, both ReadMes and the tests
for nothing anybody can see.

A change is applied at once and written to the settings file a second later,
like every other setting in this program.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFontComboBox, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

import ls_core as C
import ls_dm as D
import ls_ui as U
from ov_t import _Row

# Delete takes two clicks within this time, as on the DM settings tab — no
# modal "are you sure", which would freeze every other window.
_ARM_MS = 4000

# The saved-settings drop-down and the name box: wide enough for a name, no
# wider, so the buttons sit right beside them.
_SET_FIELD_W = 220

# A number box holds two or three digits and " px"; it does not need the width
# of a font name. A grid cell stretches whatever is put in it, so both are also
# aligned left rather than left floating in the middle of the column.
_BOX_W = 96
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

# What the preview says. Three rows, one per verdict, so the tick, the cross
# and the grey dash are all in front of the operator while they pick a size —
# and one long name, because that is the one that decides the column's width.
#
# The numbers go through `C.value_text`, the same call the first tab's readings
# go through, so the preview shows an energy to hundredths and `fs²` with the
# superscript lifted — not the raw `fs^2` and four decimals it used to show,
# which was a preview of something the program does not do.
_PREVIEW = [
    ("GDD", C.value_text(24900.0, "fs^2"), C.STATE_OK),
    ("SBW4 Compressed", C.value_text(20.0552, "J"), C.STATE_BAD),
    ("Spare diagnostic", C.value_text(None), C.STATE_UNKNOWN),
]

_BOARD_TIP = (
    "Drag a square to move it, drag an edge or a corner to resize it.\n"
    "The edges snap to the other squares and to the middle of the band;\n"
    "waggle the pointer during a drag and the snapping lets go.\n\n"
    "The first drag takes the arrangement off automatic. Use\n"
    "\"Arrange them again\" to go back to the even row of five.")


class _LayoutBoard(QWidget):
    """A scale model of the Overview's hall band, arranged with the mouse.

    Every square is stored as [x, y, w, h] in fractions of the band, which is
    what the Overview places them from — nothing here is in pixels, so the
    arrangement survives a window of any width.

    Copied in shape from `Image Tools/is_t.py`'s `_LayoutCanvasWidget`, cut to
    five fixed tiles: the magnetic snapping, the guide lines and the
    shake-to-free are the same behaviour, because an operator who has arranged
    the cameras once should not have to learn a second way of doing it. The repo
    keeps no shared library, so keep the two in step by hand.
    """

    EDGE = 10               # pixels from an edge that count as "grab the edge"
    DEAD_PX = 3             # movement below this is a click, not a drag
    SNAP_FRAC = 0.02        # magnetic snap threshold, as a fraction of the band
    SHAKE_WINDOW = 8        # recent move increments inspected for a shake
    SHAKE_FLIPS = 4         # direction reversals in the window that free an edge
    SHAKE_TRAVEL_FRAC = 0.06

    # Dark ink on a light ground, both for the tiles and for the handles. A pale
    # handle on a pale tile is an invisible control.
    BOARD    = "#ffffff"
    OUTSIDE  = "#e4e7ea"
    BORDER   = "#9aa1a9"
    GUIDE    = "#dfe4ea"
    TILE     = "#dbe8fb"
    TILE_EDGE = "#2d7dff"
    TILE_SEL  = "#1f5fc4"
    TILE_INK = "#16202c"
    HANDLE   = "#16202c"
    SNAP     = "#1d6b29"

    changed = Signal()

    def __init__(self, halls, band_px=(1140, 150), parent=None):
        super().__init__(parent)
        self._halls = halls
        # The SIZE of the real band on the Overview, in real pixels — not just
        # its shape. The board is a scale MODEL of it: the arrangement is worked
        # out for these numbers, so the fractions are the very ones the first
        # tab will use, and everything measured in absolute pixels (the word
        # inside a square) is multiplied by `_scale()` when it is drawn.
        #
        # Passing only the aspect and then arranging the squares at the BOARD's
        # own size is what made the board disagree with the first tab: five
        # squares in a 478 px model of a 970 px band came out 88 px wide where
        # the real ones are 186, so the model showed a different arrangement
        # altogether. Reported 1.10.2026. Same trap, same fix as Image Slider's
        # `_LayoutCanvasWidget._canvas_px`.
        # Every piece of state first, and only then anything that can call
        # `reload()`: `set_band_px` does, and `reload()` asks `_drag_idx`.
        self._band_px = (1140, 150)
        self._tiles = []                # [x, y, w, h] per hall, in HALLS order
        self._drag_idx = -1
        self._drag_mode = ""
        self._drag_start_pos = None
        self._drag_start_tile = None
        self._selected = -1
        self._snap_off = False
        self._moves = []
        self._last_pos = None
        self._guides = []
        # Small minimum, generous HINT. The minimum is what the window's own
        # minimum would be built from, so it stays low; the hint is what the
        # board actually gets, and the board is letterboxed to a band that is
        # five to twelve times wider than it is tall — so a short board is a
        # narrow model, and the squares in it come out too small to judge.
        self.setMinimumHeight(self.BOARD_MIN_H)
        # Said in the policy as well as by the override: some layouts ask one,
        # some the other.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setToolTip(_BOARD_TIP)
        self.setMouseTracking(True)
        self.set_band_px(band_px)
        self.reload()

    # The board is as wide as its box and as tall as the band's SHAPE makes it,
    # so it is a scale model at about 1:1 — the box is nearly as wide as the
    # Overview's band. A board held at 90 px tall was a model a quarter of the
    # size, and its squares looked nothing like the real ones (1.10.2026). The
    # page scrolls, so a tall board costs nothing.
    BOARD_MIN_H = 90
    BOARD_MAX_H = 900

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        h = int(round(max(1, width - 2) / self._aspect())) + 2
        return max(self.BOARD_MIN_H, min(self.BOARD_MAX_H, h))

    def sizeHint(self):
        return QSize(520, self.heightForWidth(520))

    # ── what it is showing ─────────────────────────────────────────────────
    def set_band_px(self, band_px):
        """The band's real size on the Overview, in real pixels.

        The board is letterboxed to its shape, so a square dragged here is a
        square there, and the arrangement is worked out for these numbers
        rather than for the board's own.
        """
        try:
            w, h = int(band_px[0]), int(band_px[1])
        except (TypeError, ValueError, IndexError):
            return
        if w < 20 or h < 10:
            return                      # no real band to model yet
        if (w, h) == self._band_px:
            return
        self._band_px = (w, h)
        # A new shape is a new height for the same width.
        self.updateGeometry()
        if self._halls.get("auto"):
            self.reload()
        self.update()

    def _aspect(self):
        w, h = self._band_px
        return max(0.2, w / float(max(1, h)))

    def _scale(self):
        """Board pixels per real pixel. Anything absolute is drawn through it."""
        return max(0.02, self._board().height() / float(self._band_px[1]))

    def reload(self):
        """Read the arrangement back out of the settings document.

        A no-op while a square is being dragged: writing a rectangle asks the
        window to refresh every tab, this one included, and re-reading the
        document under the operator's own hand would fight them.
        """
        if self._drag_idx >= 0:
            return
        # The REAL band's size, not the board's. See `set_band_px`.
        tiles = C.hall_tiles(self._halls, *self._band_px)
        self._tiles = [list(tiles[h]) for h in C.HALLS]
        self.update()

    def arrange_again(self):
        """Back to the even row of five, and back on automatic."""
        self._halls["auto"] = True
        self._halls["layout"] = {}
        self._selected = -1
        self.reload()
        self.changed.emit()

    # ── geometry ───────────────────────────────────────────────────────────
    def _board(self):
        """The largest rectangle with the band's shape, centred in the widget."""
        w = max(1, self.width() - 2)
        h = max(1, self.height() - 2)
        aspect = self._aspect()
        if w / h > aspect:
            bw, bh = max(1, int(aspect * h)), h
        else:
            bw, bh = w, max(1, int(w / aspect))
        return QRect(1 + (w - bw) // 2, 1 + (h - bh) // 2, bw, bh)

    def _tile_rect(self, i):
        b = self._board()
        x, y, tw, th = self._tiles[i]
        return QRect(b.left() + int(round(x * b.width())),
                     b.top() + int(round(y * b.height())),
                     max(2, int(round(tw * b.width()))),
                     max(2, int(round(th * b.height()))))

    def _hit_test(self, pos):
        # Backwards: the last tile is drawn on top, so it is hit first.
        for i in range(len(self._tiles) - 1, -1, -1):
            r = self._tile_rect(i)
            if not r.contains(pos):
                continue
            rx, ry = pos.x() - r.left(), pos.y() - r.top()
            left = rx <= self.EDGE
            right = (r.width() - rx) <= self.EDGE
            top = ry <= self.EDGE
            bottom = (r.height() - ry) <= self.EDGE
            if top and left:
                return i, "top-left"
            if top and right:
                return i, "top-right"
            if bottom and left:
                return i, "bottom-left"
            if bottom and right:
                return i, "bottom-right"
            if left:
                return i, "left"
            if right:
                return i, "right"
            if top:
                return i, "top"
            if bottom:
                return i, "bottom"
            return i, "move"
        return -1, ""

    # ── snapping ───────────────────────────────────────────────────────────
    def _shaking(self, w, h):
        """True if the recent motion reversed direction over a short travel."""
        if len(self._moves) < self.SHAKE_WINDOW:
            return False

        def flips(vals):
            out, prev = 0, 0
            for v in vals:
                s = (v > 0) - (v < 0)
                if s != 0 and prev != 0 and s != prev:
                    out += 1
                if s != 0:
                    prev = s
            return out

        dxs = [d[0] for d in self._moves]
        dys = [d[1] for d in self._moves]
        small = max(8.0, self.SHAKE_TRAVEL_FRAC * max(w, h))
        return ((flips(dxs) >= self.SHAKE_FLIPS
                 and sum(abs(v) for v in dxs) < small)
                or (flips(dys) >= self.SHAKE_FLIPS
                    and sum(abs(v) for v in dys) < small))

    def _snap_lines(self):
        """Where an edge may snap: the band's sides and middle, and the others."""
        xs, ys = [0.0, 0.5, 1.0], [0.0, 0.5, 1.0]
        for j, t in enumerate(self._tiles):
            if j == self._drag_idx:
                continue
            xs += [t[0], t[0] + t[2]]
            ys += [t[1], t[1] + t[3]]
        return xs, ys

    def _nearest(self, value, candidates):
        best, best_d = None, self.SNAP_FRAC
        for c in candidates:
            d = abs(value - c)
            if d < best_d:
                best, best_d = c, d
        return best

    def _apply_snap(self, mode, t):
        xs, ys = self._snap_lines()
        m = C.TILE_MIN_FRAC
        left, right = t[0], t[0] + t[2]
        top, bottom = t[1], t[1] + t[3]
        if mode == "move":
            sl, sr = self._nearest(left, xs), self._nearest(right, xs)
            if sl is not None and (sr is None or abs(left - sl) <= abs(right - sr)):
                t[0] = max(0.0, min(1.0 - t[2], sl))
                self._guides.append(("v", sl))
            elif sr is not None:
                t[0] = max(0.0, min(1.0 - t[2], sr - t[2]))
                self._guides.append(("v", sr))
            st, sb = self._nearest(top, ys), self._nearest(bottom, ys)
            if st is not None and (sb is None or abs(top - st) <= abs(bottom - sb)):
                t[1] = max(0.0, min(1.0 - t[3], st))
                self._guides.append(("h", st))
            elif sb is not None:
                t[1] = max(0.0, min(1.0 - t[3], sb - t[3]))
                self._guides.append(("h", sb))
            return
        if "left" in mode:
            s = self._nearest(left, xs)
            if s is not None and s <= right - m:
                t[2] = right - s
                t[0] = s
                self._guides.append(("v", s))
        if "right" in mode:
            s = self._nearest(right, xs)
            if s is not None and s >= t[0] + m:
                t[2] = s - t[0]
                self._guides.append(("v", s))
        if "top" in mode:
            s = self._nearest(top, ys)
            if s is not None and s <= bottom - m:
                t[3] = bottom - s
                t[1] = s
                self._guides.append(("h", s))
        if "bottom" in mode:
            s = self._nearest(bottom, ys)
            if s is not None and s >= t[1] + m:
                t[3] = s - t[1]
                self._guides.append(("h", s))

    # ── the mouse ──────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position().toPoint()
        idx, mode = self._hit_test(pos)
        if idx < 0:
            if self._selected != -1:
                self._selected = -1
                self.update()
            return
        self._drag_idx = idx
        self._drag_mode = mode
        self._drag_start_pos = pos
        self._drag_start_tile = list(self._tiles[idx])
        self._selected = idx
        self._snap_off = False
        self._moves = []
        self._last_pos = pos
        self._guides = []
        self.update()

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()
        if self._drag_idx < 0:
            _idx, mode = self._hit_test(pos)
            self.setCursor({
                "top-left": Qt.CursorShape.SizeFDiagCursor,
                "bottom-right": Qt.CursorShape.SizeFDiagCursor,
                "top-right": Qt.CursorShape.SizeBDiagCursor,
                "bottom-left": Qt.CursorShape.SizeBDiagCursor,
                "left": Qt.CursorShape.SizeHorCursor,
                "right": Qt.CursorShape.SizeHorCursor,
                "top": Qt.CursorShape.SizeVerCursor,
                "bottom": Qt.CursorShape.SizeVerCursor,
                "move": Qt.CursorShape.SizeAllCursor,
            }.get(mode, Qt.CursorShape.ArrowCursor))
            return
        b = self._board()
        w, h = b.width(), b.height()
        if w < 1 or h < 1:
            return
        if abs(pos.x() - self._drag_start_pos.x()) < self.DEAD_PX \
                and abs(pos.y() - self._drag_start_pos.y()) < self.DEAD_PX:
            # A few pixels of hand tremor while picking a square must not count
            # as arranging it by hand.
            return
        dx = (pos.x() - self._drag_start_pos.x()) / float(w)
        dy = (pos.y() - self._drag_start_pos.y()) / float(h)
        st = self._drag_start_tile
        t = self._tiles[self._drag_idx]
        m = C.TILE_MIN_FRAC
        mode = self._drag_mode
        if mode == "move":
            t[0] = max(0.0, min(1.0 - st[2], st[0] + dx))
            t[1] = max(0.0, min(1.0 - st[3], st[1] + dy))
        else:
            if "right" in mode:
                t[2] = max(m, min(1.0 - st[0], st[2] + dx))
            if "bottom" in mode:
                t[3] = max(m, min(1.0 - st[1], st[3] + dy))
            if "left" in mode:
                x = max(0.0, min(st[0] + st[2] - m, st[0] + dx))
                t[2] = st[2] + (st[0] - x)
                t[0] = x
            if "top" in mode:
                y = max(0.0, min(st[1] + st[3] - m, st[1] + dy))
                t[3] = st[3] + (st[1] - y)
                t[1] = y

        if self._last_pos is not None:
            self._moves.append((pos.x() - self._last_pos.x(),
                                pos.y() - self._last_pos.y()))
            self._moves = self._moves[-self.SHAKE_WINDOW:]
            if not self._snap_off and self._shaking(w, h):
                self._snap_off = True
        self._last_pos = pos

        self._guides = []
        if not self._snap_off:
            self._apply_snap(mode, t)
        self._store()
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _ev):
        if self._drag_idx < 0:
            return
        self._drag_idx = -1
        self._drag_mode = ""
        self._snap_off = False
        self._moves = []
        self._last_pos = None
        self._guides = []
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._halls.get("auto"):
            # The automatic arrangement is worked out from the band's real pixel
            # size, so a board of a different shape wants it worked out again.
            self.reload()
        self.update()

    def _store(self):
        """The rectangles into the settings document, and off automatic.

        Only the rectangles. The band's SHARE of the window goes in too, but
        the tab does that in `FontTab._board_changed` — it is the one that can
        ask the Overview how tall its band really is, and this widget knows
        nothing but fractions.
        """
        self._halls["auto"] = False
        self._halls["layout"] = {h: list(t)
                                 for h, t in zip(C.HALLS, self._tiles)}

    # ── the drawing ────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        # try/finally, like every paintEvent in this program: an exception that
        # escapes here leaves the painter active and kills the program later,
        # somewhere that cannot be understood.
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            b = self._board()
            # Everything outside the board is not part of the band — no
            # arrangement can ever reach it.
            p.fillRect(self.rect(), QColor(self.OUTSIDE))
            p.fillRect(b, QColor(self.BOARD))

            guide = QPen(QColor(self.GUIDE))
            guide.setStyle(Qt.PenStyle.DashLine)
            p.setPen(guide)
            for frac in (0.25, 1 / 3.0, 0.5, 2 / 3.0, 0.75):
                gx = b.left() + int(frac * b.width())
                gy = b.top() + int(frac * b.height())
                p.drawLine(gx, b.top(), gx, b.bottom())
                p.drawLine(b.left(), gy, b.right(), gy)

            p.setPen(QPen(QColor(self.BORDER)))
            p.drawRect(b.adjusted(0, 0, -1, -1))

            word = bool(self._halls.get("show_word"))
            for i, hall in enumerate(C.HALLS):
                if i >= len(self._tiles):
                    break
                r = self._tile_rect(i)
                sel = (i == self._selected)
                p.setBrush(QColor(self.TILE))
                edge = QPen(QColor(self.TILE_SEL if sel else self.TILE_EDGE))
                edge.setWidth(3 if sel else 2)
                p.setPen(edge)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
                self._draw_label(p, r, hall, word)
                self._draw_handles(p, r)

            if self._guides:
                sp = QPen(QColor(self.SNAP))
                sp.setWidth(1)
                p.setPen(sp)
                for kind, frac in self._guides:
                    if kind == "v":
                        x = b.left() + int(frac * b.width())
                        p.drawLine(x, b.top(), x, b.bottom())
                    else:
                        y = b.top() + int(frac * b.height())
                        p.drawLine(b.left(), y, b.right(), y)
        finally:
            p.end()

    def _draw_label(self, p, r, hall, word):
        """The hall's name, and the word under it when it is switched on.

        Handed straight to `ls_ui.paint_hall_label`, which is what the real
        square uses — with this board's SCALE, so the word, which is an
        absolute pixel size, comes out at the model's size and not at the
        window's. Drawing it here in its own words is how the board came to
        disagree with the first tab.
        """
        U.paint_hall_label(p, QRectF(r), C.hall_shown(hall),
                           "SHOOTING" if word else "",
                           int(self._halls.get("word_px") or 11),
                           QColor(self.TILE_INK), scale=self._scale())

    def _draw_handles(self, p, r):
        """Dark L-shapes at the four corners, so the grab points are visible."""
        pen = QPen(QColor(self.HANDLE))
        pen.setWidth(3)
        p.setPen(pen)
        e = min(self.EDGE, max(3, min(r.width(), r.height()) // 3))
        for x, y, sx, sy in ((r.left(), r.top(), 1, 1),
                             (r.right(), r.top(), -1, 1),
                             (r.left(), r.bottom(), 1, -1),
                             (r.right(), r.bottom(), -1, -1)):
            p.drawLine(x, y, x + sx * e, y)
            p.drawLine(x, y, x, y + sy * e)

    # ── what the tests read back ───────────────────────────────────────────
    def tiles(self):
        return [list(t) for t in self._tiles]


class FontTab(QWidget):
    """The Settings tab: where the squares sit, and how the list is written.

    The class keeps its name. `main.py` loads every tab by module and class
    name and the build config lists both, so renaming the class would cost two
    more files and buy nothing.
    """

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._loading = False
        self._rows = []
        # The saved sets: what the share held at the last look, the job on
        # the thread (a plain dict it fills in), and its poll.
        self._sets = {}
        self._sets_share = D.Share()
        self._sets_reply = None
        self._sets_listed = False
        self._sets_timer = QTimer(self)
        self._sets_timer.setInterval(100)
        self._sets_timer.timeout.connect(self._sets_poll)
        self._armed = None
        self._arm_timer = QTimer(self)
        self._arm_timer.setSingleShot(True)
        self._arm_timer.setInterval(_ARM_MS)
        self._arm_timer.timeout.connect(self._disarm)
        self._build()
        self.reload()

    # ── building ───────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # In a scroller: the board, the word and the list's own look are three
        # boxes and the window may be 560 px tall. A control that cannot be
        # reached is a control that is not there.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {U.PAPER};"
                             " border: 0px; }" + U.SCROLLBAR_QSS)
        scroll.viewport().setStyleSheet(f"background: {U.PAPER};")
        page = QWidget()
        page.setObjectName("settingsPage")
        page.setStyleSheet(f"#settingsPage {{ background: {U.PAPER}; }}")
        root = QVBoxLayout(page)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        scroll.setWidget(page)
        outer.addWidget(scroll)

        # No shot counter box: its settings sit in its own row on the Limits
        # tab since 5.10.2026 (`st_t.SettingsTab._shot_settings_cell`).
        root.addWidget(self._build_sets_box())
        root.addWidget(self._build_window_box())
        root.addWidget(self._build_halls_box())
        root.addWidget(self._build_list_box())
        root.addStretch(1)

    # ── saved settings ─────────────────────────────────────────────────────
    def _build_sets_box(self):
        """Named sets of settings on the share: pick, Load, Save, Rename,
        Delete, and which parts take part. See `ls_core.SET_PARTS`."""
        box = QGroupBox("Saved settings")
        box.setToolTip(
            "Settings saved under a name — one set per operator, say — on the "
            "scratch share, so every PC sees the same list.\nPick one and press "
            "Load: the ticked parts of it replace this PC's settings.\nThe DM "
            "writer switch is never saved or loaded: it belongs to the PC.")
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        pick = QHBoxLayout()
        pick.setSpacing(6)
        pick.addWidget(QLabel("Settings"))
        self._set_combo = QComboBox()
        self._set_combo.currentIndexChanged.connect(self._set_picked)
        # Short, with the buttons right beside it — stretched across the
        # page, it pushed them off the right edge (13.10.2026).
        self._set_combo.setFixedWidth(_SET_FIELD_W)
        pick.addWidget(self._set_combo)
        self._set_load = U.button(
            "Load", icon_name="check", primary=True,
            tip="Replace this PC's settings by the ticked parts of the picked "
                "set.")
        self._set_load.clicked.connect(self._set_load_clicked)
        pick.addWidget(self._set_load)
        self._set_save = U.button(
            "Save", icon_name="save",
            tip="Save this PC's settings (the ticked parts) into the picked "
                "set, over what it held.")
        self._set_save.clicked.connect(self._set_save_clicked)
        pick.addWidget(self._set_save)
        self._set_delete = U.button(
            "Delete", icon_name="trash", danger=True,
            tip="Take the picked set off the list, for every PC. Click twice.")
        self._set_delete.clicked.connect(self._set_delete_clicked)
        pick.addWidget(self._set_delete)
        pick.addStretch(1)
        lay.addLayout(pick)

        name = QHBoxLayout()
        name.setSpacing(6)
        name.addWidget(QLabel("Name"))
        self._set_name = QLineEdit()
        self._set_name.setPlaceholderText("new name")
        self._set_name.setToolTip("A name for a new set (Save as new), or the "
                                  "new name of the picked one (Rename).")
        self._set_name.setFixedWidth(_SET_FIELD_W)
        name.addWidget(self._set_name)
        self._set_new = U.button(
            "Save as new", icon_name="plus",
            tip="Save this PC's settings (the ticked parts) as a new set under "
                "the name typed beside it.")
        self._set_new.clicked.connect(self._set_new_clicked)
        name.addWidget(self._set_new)
        self._set_rename = U.button(
            "Rename", icon_name="pencil",
            tip="Give the picked set the name typed beside it.")
        self._set_rename.clicked.connect(self._set_rename_clicked)
        name.addWidget(self._set_rename)
        name.addStretch(1)
        lay.addLayout(name)

        parts = QHBoxLayout()
        parts.setSpacing(12)
        parts.addWidget(QLabel("Parts"))
        self._set_parts = {}
        for key in C.SET_PART_KEYS:
            check = QCheckBox(C.SET_PART_CAPTIONS[key])
            check.setStyleSheet(U.chk_qss())
            check.setChecked(key in C.SET_PARTS_DEFAULT)
            check.setToolTip("Saved with Save / Save as new, and replaced by "
                             "Load, only while ticked.")
            check.toggled.connect(self._sync_set_buttons)
            parts.addWidget(check)
            self._set_parts[key] = check
        parts.addStretch(1)
        lay.addLayout(parts)

        self._set_note = QLabel("")
        self._set_note.setWordWrap(True)
        self._set_note.setStyleSheet(f"color: {U.QUIET};")
        self._set_note.hide()
        lay.addWidget(self._set_note)
        self._sync_set_buttons()
        return box

    def _say(self, text):
        """The line under the box: what the last action did. Hidden when it
        has nothing to say, so it is not an empty strip."""
        self._set_note.setText(text or "")
        self._set_note.setVisible(bool(text))

    def _ticked_parts(self):
        return [k for k, c in self._set_parts.items() if c.isChecked()]

    def _picked_set(self):
        name = self._set_combo.currentData()
        return name if name in self._sets else None

    def _sync_set_buttons(self, *_a):
        busy = self._sets_reply is not None
        picked = self._picked_set() is not None
        parts = bool(self._ticked_parts())
        self._set_load.setEnabled(not busy and picked and parts)
        self._set_save.setEnabled(not busy and picked and parts)
        self._set_delete.setEnabled(not busy and picked)
        self._set_rename.setEnabled(not busy and picked)
        self._set_new.setEnabled(not busy and parts)

    def _fill_set_combo(self, select=None):
        keep = select if select is not None else self._set_combo.currentData()
        self._set_combo.blockSignals(True)
        try:
            self._set_combo.clear()
            for name, entry in self._sets.items():
                self._set_combo.addItem(name, name)
                held = ", ".join(C.SET_PART_CAPTIONS[k]
                                 for k in C.parts_in(entry["settings"]))
                when = entry["saved"].replace("T", " ")
                tip = (f"Saved {when} on {entry['by'] or 'an unknown PC'}.\n"
                       f"Holds: {held or 'nothing'}.")
                self._set_combo.setItemData(self._set_combo.count() - 1, tip,
                                            Qt.ItemDataRole.ToolTipRole)
            if not self._sets:
                self._set_combo.addItem("(nothing saved yet)", None)
            at = self._set_combo.findData(keep) if keep else -1
            self._set_combo.setCurrentIndex(max(0, at))
        finally:
            self._set_combo.blockSignals(False)
        self._set_picked()

    def _set_picked(self, *_a):
        self._disarm()
        name = self._picked_set()
        tip = (self._set_combo.itemData(self._set_combo.currentIndex(),
                                        Qt.ItemDataRole.ToolTipRole)
               if name else "")
        self._set_combo.setToolTip(tip or "")
        self._sync_set_buttons()

    def _set_load_clicked(self):
        name = self._picked_set()
        parts = self._ticked_parts()
        if name is None or not parts:
            return

        def then(sets):
            entry = sets.get(name)
            if entry is None:
                return f"\"{name}\" is not on the list any more."
            held = [p for p in parts if p in C.parts_in(entry["settings"])]
            if not held:
                return f"\"{name}\" holds none of the ticked parts."
            self._win.apply_settings(entry["settings"], held)
            return (f"Loaded \"{name}\": "
                    + ", ".join(C.SET_PART_CAPTIONS[p] for p in held) + ".")

        self._say(f"Loading \"{name}\"…")
        self._sets_run(None, "", then=then, select=name)

    def _set_save_clicked(self):
        name = self._picked_set()
        if name is None:
            return
        settings = C.settings_parts(self._win.settings_document(),
                                    self._ticked_parts())
        self._say(f"Saving \"{name}\"…")
        self._sets_run(lambda sets: C.overwrite_set(sets, name, settings),
                       f"Saved \"{name}\".", select=name)

    def _set_new_clicked(self):
        name = self._set_name.text().strip()
        settings = C.settings_parts(self._win.settings_document(),
                                    self._ticked_parts())
        self._say(f"Saving \"{name}\"…")
        self._sets_run(lambda sets: C.add_set(sets, name, settings),
                       f"Saved \"{name}\".", select=name)

    def _set_rename_clicked(self):
        old = self._picked_set()
        new = self._set_name.text().strip()
        if old is None:
            return
        self._say(f"Renaming \"{old}\"…")
        self._sets_run(lambda sets: C.rename_set(sets, old, new),
                       f"\"{old}\" is now \"{new}\".", select=new)

    def _set_delete_clicked(self):
        name = self._picked_set()
        if name is None:
            return
        if self._armed != name:
            self._armed = name
            self._set_delete.setText("Click again to delete")
            self._arm_timer.start()
            return
        self._disarm()
        self._say(f"Deleting \"{name}\"…")
        self._sets_run(lambda sets: C.delete_set(sets, name),
                       f"Deleted \"{name}\".")

    def _disarm(self):
        self._armed = None
        self._arm_timer.stop()
        if hasattr(self, "_set_delete"):
            self._set_delete.setText("Delete")

    def _sets_run(self, edit, done, then=None, select=None):
        """Read the sets file on a thread, apply `edit` (sets → (new sets or
        None, why not)) and write it back when there is something new. The
        reply is a plain dict polled by `_sets_poll`; no Qt object crosses
        threads. `then(sets)` runs on the GUI thread afterwards and returns
        the sentence to show."""
        if self._sets_reply is not None:
            return
        reply = {"done": False}
        self._sets_reply = (reply, done, then, select)
        share = self._sets_share

        def work():
            try:
                folder = share.folder()
                if folder is None:
                    reply["error"] = ("The scratch share cannot be reached — "
                                      "the saved settings are not available.")
                    return
                path = folder / C.SETS_NAME
                try:
                    raw = D.read_json(path)
                except Exception as exc:
                    reply["error"] = (f"{C.SETS_NAME} on the share could not "
                                      f"be read ({exc}) — nothing was "
                                      f"changed.")
                    return
                sets = C.clean_sets(raw) if raw is not None else {}
                why = None
                if edit is not None:
                    new, why = edit(sets)
                    if new is not None:
                        D.write_json_atomic(path, C.sets_document(new))
                        sets = new
                    reply["changed"] = new is not None
                reply["sets"] = sets
                reply["why"] = why
            except Exception as exc:
                share.lost()
                reply["error"] = f"The scratch share failed: {exc}"
            finally:
                reply["done"] = True

        threading.Thread(target=work, daemon=True,
                         name="settings-sets").start()
        self._sync_set_buttons()
        self._sets_timer.start()

    def _sets_poll(self):
        if self._sets_reply is None:
            self._sets_timer.stop()
            return
        reply, done, then, select = self._sets_reply
        if not reply.get("done"):
            return
        self._sets_timer.stop()
        self._sets_reply = None
        if "error" in reply:
            self._say(reply["error"])
            self._sync_set_buttons()
            return
        self._sets = reply["sets"]
        changed = reply.get("changed", True)
        self._fill_set_combo(select if changed else None)
        if reply.get("why"):
            text = reply["why"]
        elif then is not None:
            try:
                text = then(self._sets)
            except Exception as exc:
                text = f"Could not load: {exc}"
        else:
            text = done
            if changed and select is not None:
                self._set_name.clear()
        self._say(text or "")

    def _build_window_box(self):
        """What the Maximise button does. See `ls_core.WINDOW_MAX_DEFAULTS`."""
        box = QGroupBox("The window")
        # Two lines, not one: on one line the box was wider than a normal
        # window and the page scrolled sideways (13.10.2026).
        rows = QVBoxLayout(box)
        rows.setSpacing(6)
        lay = QHBoxLayout()
        lay.setSpacing(8)
        rows.addLayout(lay)

        self._max_on = QCheckBox("Maximise to this size")
        self._max_on.setStyleSheet(U.chk_qss())
        self._max_on.setToolTip(
            "Ticked: the Maximise button in the title bar makes the window this "
            "size, at this place on its screen. Restore goes back as usual.\n"
            "Unticked: Maximise fills the screen.")
        self._max_on.toggled.connect(self._max_typed)
        lay.addWidget(self._max_on)

        low, high = C.WINDOW_MAX_LIMITS
        lay.addWidget(QLabel("Width"))
        self._max_w = QSpinBox()
        self._max_w.setRange(low, high)
        self._max_w.setSuffix(" px")
        self._max_w.setFixedWidth(_BOX_W)
        self._max_w.valueChanged.connect(self._max_typed)
        lay.addWidget(self._max_w)

        lay.addWidget(QLabel("Height"))
        self._max_h = QSpinBox()
        self._max_h.setRange(low, high)
        self._max_h.setSuffix(" px")
        self._max_h.setFixedWidth(_BOX_W)
        self._max_h.valueChanged.connect(self._max_typed)
        lay.addWidget(self._max_h)
        lay.addStretch(1)
        lay = QHBoxLayout()
        lay.setSpacing(8)
        rows.addLayout(lay)

        # Where. Counted from the top left corner of the screen's free area
        # (the screen minus the taskbar) — see `ls_core.WINDOW_MAX_DEFAULTS`.
        low, high = C.WINDOW_POS_LIMITS
        boxes = []
        for label, tip in (("From the left", "How far the window's left edge "
                            "is from the left edge of the screen."),
                           ("From the top", "How far the window's top edge is "
                            "from the top of the screen.")):
            lay.addWidget(QLabel(label))
            box_ = QSpinBox()
            box_.setRange(low, high)
            box_.setSuffix(" px")
            box_.setFixedWidth(_BOX_W)
            box_.setToolTip(tip)
            box_.valueChanged.connect(self._max_typed)
            lay.addWidget(box_)
            boxes.append(box_)
        self._max_x, self._max_y = boxes

        take = U.button("Use the current size and place", icon_name="monitor",
                        tip="Put where the window is now and how big it is "
                            "into the four boxes.")
        take.clicked.connect(self._max_take_current)
        lay.addWidget(take)
        lay.addStretch(1)
        return box

    def _max_typed(self, *_a):
        if self._loading:
            return
        wm = self._win.window_max()
        wm["on"] = bool(self._max_on.isChecked())
        wm["w"] = int(self._max_w.value())
        wm["h"] = int(self._max_h.value())
        wm["x"] = int(self._max_x.value())
        wm["y"] = int(self._max_y.value())
        self._win.window_max_changed()

    def _max_take_current(self):
        # The window as it is seen, title bar and frame included — the same
        # thing the boxes mean. When maximised, the size and place it restores
        # to: `normalGeometry` is the inside only, so the frame is added back.
        win = self._win
        frame = win.frameGeometry()
        inside = win.geometry()
        if win.isMaximized():
            normal = win.normalGeometry()
            w = normal.width() + frame.width() - inside.width()
            h = normal.height() + frame.height() - inside.height()
            left = normal.x() - (inside.x() - frame.x())
            top = normal.y() - (inside.y() - frame.y())
        else:
            w, h = frame.width(), frame.height()
            left, top = frame.x(), frame.y()
        # From the free area of the screen the window is on, not the desktop.
        screen = win.screen()
        free = screen.availableGeometry() if screen is not None else None
        if free is not None:
            left -= free.x()
            top -= free.y()
        self._loading = True
        try:
            self._max_w.setValue(int(w))
            self._max_h.setValue(int(h))
            self._max_x.setValue(max(0, int(left)))
            self._max_y.setValue(max(0, int(top)))
        finally:
            self._loading = False
        self._max_typed()

    def _build_halls_box(self):
        box = QGroupBox("The hall squares on the Overview tab")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        self._single = QCheckBox("One square that follows the hall being shot "
                                 "into")
        self._single.setStyleSheet(U.chk_qss())
        self._single.setToolTip(
            "Ticked: one square instead of five, named after the hall the beam "
            "is going into (L3 when it is going into none of the others), "
            "with the list beside it.\n"
            "Unticked: the five squares above the list.")
        self._single.toggled.connect(self._halls_typed)
        lay.addWidget(self._single)

        self._fit = QCheckBox("Fit everything to the window")
        self._fit.setStyleSheet(U.chk_qss())
        self._fit.setToolTip(
            "Size the window how you want it, then tick this: the program works "
            "out the band, the text and the verdict square so that the squares "
            "and the list fill that window with nothing left over, and writes "
            "the four numbers into the boxes below — so they are always what is "
            "really on screen.\n\n"
            "The verdict square comes out one and a half times the text.\n"
            "Type into any of the four boxes and this switches itself off and "
            "your own number applies; tick it again and your numbers are put "
            "back when you switch it off next time.")
        self._fit.toggled.connect(self._fit_toggled)
        lay.addWidget(self._fit)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self._band_label = QLabel("Height of the band")
        tools.addWidget(self._band_label)
        self._band = QSpinBox()
        low, high = C.HALLS_LIMITS["band_px"]
        self._band.setRange(low, high)
        self._band.setSuffix(" px")
        self._band.setFixedWidth(_BOX_W)
        self._band.setToolTip("How tall the strip with the five squares is, in "
                              "pixels — exactly what is on the first tab. What "
                              "is left goes to the list underneath.")
        self._band.valueChanged.connect(self._size_typed)
        tools.addWidget(self._band)

        self._show_word = QCheckBox("Write SHOOTING / idle inside the squares")
        self._show_word.setStyleSheet(U.chk_qss())
        self._show_word.setToolTip("What is being shot into and what is not, "
                                   "written in the square itself.")
        self._show_word.toggled.connect(self._halls_typed)
        tools.addWidget(self._show_word)

        # Labelled, although the tick box next to it already names the word: a
        # bare "11 px" box in a row of controls does not say what it measures.
        self._word_label = QLabel("Its size")
        tools.addWidget(self._word_label)
        self._word_px = QSpinBox()
        low, high = C.HALLS_LIMITS["word_px"]
        self._word_px.setRange(low, high)
        self._word_px.setSuffix(" px")
        self._word_px.setFixedWidth(_BOX_W)
        self._word_px.setToolTip("How big that word is.")
        self._word_px.valueChanged.connect(self._size_typed)
        tools.addWidget(self._word_px)
        tools.addStretch(1)

        again = U.button("Arrange them again", icon_name="refresh",
                         tip="Back to the even row of five, evenly spaced and "
                             "centred in the band.")
        again.clicked.connect(self._arrange_again)
        tools.addWidget(again)
        self._again = again
        lay.addLayout(tools)

        self._board = _LayoutBoard(self._win.halls(), self._band_shape(), box)
        self._board.changed.connect(self._board_changed)
        lay.addWidget(self._board, stretch=1)
        return box

    def _build_list_box(self):
        box = QGroupBox("The list on the Overview tab")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("Typeface"), 0, 0)
        self._family = QFontComboBox()
        self._family.setMinimumWidth(260)
        self._family.setToolTip("Every font installed on this PC.")
        self._family.currentFontChanged.connect(self._typed)
        grid.addWidget(self._family, 0, 1)

        grid.addWidget(QLabel("Text size"), 1, 0)
        self._size = QSpinBox()
        low, high = C.LOOK_LIMITS["size_px"]
        self._size.setRange(low, high)
        self._size.setSuffix(" px")
        self._size.setFixedWidth(_BOX_W)
        self._size.setToolTip("How tall the names and the numbers are.")
        self._size.valueChanged.connect(self._size_typed)
        # Left, or a number box as wide as the typeface box next to it reads as
        # an empty text field with a lonely 19 in it.
        grid.addWidget(self._size, 1, 1, _LEFT)

        self._bold = QCheckBox("Bold")
        self._bold.setStyleSheet(U.chk_qss())
        self._bold.toggled.connect(self._typed)
        grid.addWidget(self._bold, 2, 1)

        grid.addWidget(QLabel("Verdict square"), 3, 0)
        self._mark = QSpinBox()
        low, high = C.LOOK_LIMITS["mark_px"]
        self._mark.setRange(low, high)
        self._mark.setSuffix(" px")
        self._mark.setFixedWidth(_BOX_W)
        self._mark.setToolTip("The tick, the cross and the dash beside a "
                              "reading.")
        self._mark.valueChanged.connect(self._size_typed)
        grid.addWidget(self._mark, 3, 1, _LEFT)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        back = U.button("Back to the defaults", icon_name="refresh",
                        tip=f"{C.LOOK_DEFAULTS['family']}, "
                            f"{C.LOOK_DEFAULTS['size_px']} px, bold, "
                            f"{C.LOOK_DEFAULTS['mark_px']} px square.")
        back.clicked.connect(self._defaults)
        buttons.addWidget(back)
        buttons.addStretch(1)
        grid.addLayout(buttons, 4, 0, 1, 3)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)

        lay.addWidget(U.hsep())
        for name, value, state in _PREVIEW:
            row = _Row(self._win.look(), box)
            row.set_name(name)
            row.set_value(value)
            row.set_state(state)
            # Centred, like the real list is.
            lay.addWidget(row, 0, Qt.AlignmentFlag.AlignHCenter)
            self._rows.append(row)
        return box

    def _band_shape(self):
        """The band's real SIZE on the Overview, in real pixels.

        Asked of the Overview itself, because it is the one that knows: in the
        even row the band is never taller than its squares, and with the fit on
        its height follows the window — so the setting alone does not say what
        the band looks like, and the board must not pretend it does.

        The fallback is for the stand-in window the pictures are rendered
        against, which has no Overview to ask.
        """
        overview = getattr(self._win, "_overview", None)
        try:
            bw, bh = overview.band_shape()
            if bw > 50 and bh > 10:
                return bw, bh
        except Exception:
            pass
        halls = self._win.halls()
        band = max(1, int(halls["band_px"]))
        try:
            width = int(self._win.width()) - 20
        except Exception:
            width = 0
        if width < 200:
            width = 1140                    # the window's own usual width
        return width, band

    # ── reading and writing the settings ───────────────────────────────────
    def reload(self):
        """Put the boxes where the settings file says. Also after a rebuild."""
        look = self._win.look()
        halls = self._win.halls()
        self._loading = True
        try:
            font = self._family.currentFont()
            font.setFamily(look["family"])
            self._family.setCurrentFont(font)
            self._size.setValue(int(look["size_px"]))
            self._mark.setValue(int(look["mark_px"]))
            self._bold.setChecked(bool(look["bold"]))
            self._band.setValue(int(halls["band_px"]))
            self._word_px.setValue(int(halls["word_px"]))
            self._show_word.setChecked(bool(halls["show_word"]))
            self._fit.setChecked(bool(halls.get("fit", True)))
            self._single.setChecked(bool(halls.get("single")))
            self._word_px.setEnabled(bool(halls["show_word"]))
            self._word_label.setEnabled(bool(halls["show_word"]))
            self._show_single(bool(halls.get("single")))
            # `getattr`: the stand-in window the pictures are rendered against
            # has no Maximise setting.
            wm_of = getattr(self._win, "window_max", None)
            wm = wm_of() if wm_of else dict(C.WINDOW_MAX_DEFAULTS)
            self._max_on.setChecked(bool(wm["on"]))
            self._max_w.setValue(int(wm["w"]))
            self._max_h.setValue(int(wm["h"]))
            self._max_x.setValue(int(wm.get("x", 0)))
            self._max_y.setValue(int(wm.get("y", 0)))
        finally:
            self._loading = False
        self._board.set_band_px(self._band_shape())
        self._board.reload()
        self._paint_preview(look)

    def refresh(self):
        """A pass came back. Nothing here reads the machine."""

    # ── what the four boxes do ─────────────────────────────────────────────
    # `self._loading` is the one guard that matters and it was already here:
    # `reload()` and `park()` hold it around every write, and every handler
    # returns on it. SO A SIGNAL THAT GETS THROUGH IS ALWAYS A HUMAN — which is
    # what lets "typing switches the fit off" be one line rather than a guess.
    # Copied from `Image Tools/is_t.py`'s Auto sliders, which say the same
    # thing in `_refresh_auto_bc_sliders`.

    def _push(self):
        """Read every box into the document, repaint, and tell the window."""
        look = self._win.look()
        halls = self._win.halls()
        look["family"] = self._family.currentFont().family()
        look["size_px"] = int(self._size.value())
        look["mark_px"] = int(self._mark.value())
        look["bold"] = bool(self._bold.isChecked())
        halls["band_px"] = int(self._band.value())
        halls["word_px"] = int(self._word_px.value())
        halls["show_word"] = bool(self._show_word.isChecked())
        halls["single"] = bool(self._single.isChecked())
        self._word_px.setEnabled(halls["show_word"])
        self._word_label.setEnabled(halls["show_word"])
        self._show_single(halls["single"])
        self._paint_preview(look)
        # The look, not the list: the Overview repaints the rows it has. A
        # rebuild of every tab here ran once per wheel notch and flickered.
        self._win.look_changed()
        # After the restyle: the Overview has placed its band by then, and its
        # real shape is what the board shows.
        self._board.set_band_px(self._band_shape())
        self._board.update()

    def _show_single(self, single):
        """The board and "Arrange them again" only mean something for five
        squares, so they are hidden in the one-square mode — a disabled board
        still looks exactly like a working one. There the band's height is
        the square's size, and the label says so."""
        self._board.setVisible(not single)
        self._again.setVisible(not single)
        self._band_label.setText("Size of the square" if single
                                 else "Height of the band")

    def _typed(self, *_a):
        """The typeface, bold, the word switch — things that are not a SIZE.

        They change what the fit measures, so the fit runs again and stays on.
        """
        if self._loading:
            return
        self._push()

    def _halls_typed(self, *_a):
        """The word switch. Same as `_typed`: it is not a size."""
        if self._loading:
            return
        self._push()

    def _size_typed(self, *_a):
        """One of the four numbers the fit owns. That switches the fit OFF.

        It has to: the fit would work the number out again on the next resize
        and his would be gone, which is the thing an Auto control must never do
        to a value somebody typed.
        """
        if self._loading:
            return
        halls = self._win.halls()
        halls["fit"] = False
        self._loading = True
        try:
            self._fit.setChecked(False)
        finally:
            self._loading = False
        self._push()
        # Remembered AFTER the push, so it is the repaired number that is kept.
        halls["manual"] = C.manual_sizes(self._win.look(), halls)

    def _fit_toggled(self, on):
        if self._loading:
            return
        halls = self._win.halls()
        look = self._win.look()
        if on:
            # The fit was off, so what is in the boxes now IS his own work.
            # Snapshot it before the fit overwrites it — without this, ticking
            # the box on and off once would silently replace his sizes with the
            # fit's, with no way back to them.
            halls["manual"] = C.manual_sizes(look, halls)
            halls["fit"] = True
        else:
            halls["fit"] = False
            C.apply_manual(look, halls, halls.get("manual"))
            self.park()
        self._win.look_changed()
        self._board.set_band_px(self._band_shape())
        self._board.update()

    def park(self):
        """Put the four boxes where the fit has just worked them out.

        Signals blocked AND `_loading` held — two guards, because they fail
        differently: `_loading` stops the handler running, `blockSignals` stops
        the signal existing at all. Between them, a parked number can never be
        mistaken for a typed one.
        """
        look = self._win.look()
        halls = self._win.halls()
        self._loading = True
        try:
            for box, value in ((self._size, look["size_px"]),
                               (self._mark, look["mark_px"]),
                               (self._band, halls["band_px"]),
                               (self._word_px, halls["word_px"])):
                blocked = box.blockSignals(True)
                try:
                    box.setValue(int(value))
                finally:
                    box.blockSignals(blocked)
        finally:
            self._loading = False
        self._paint_preview(look)
        # The fit may have changed the band's height: the board follows.
        self._board.set_band_px(self._band_shape())

    def showEvent(self, ev):
        # The window may have been resized since the board was last drawn.
        super().showEvent(ev)
        self._board.set_band_px(self._band_shape())
        self._board.update()
        # The saved sets are looked up the first time the tab is opened, not
        # at start: a PC off the share would wait for nothing.
        if not self._sets_listed:
            self._sets_listed = True
            self._sets_run(None, "")

    def _board_changed(self):
        """A square was dragged. The board has already written the rectangles.

        What it could not write is the band's SHARE of the window. A hand-made
        arrangement is fractions of the band, so the fit has no square to size
        the band by and uses this share instead — and the band's real height
        right now, which only the Overview knows, is the one he arranged
        against.
        """
        if self._loading:
            return
        self._remember_band_share()
        # Called on every mouse move of a drag: the Overview moves its squares
        # and nothing is rebuilt — a rebuild here rebuilt every tab per move.
        self._win.halls_arranged()
        self._board.set_band_px(self._band_shape())

    def _remember_band_share(self):
        halls = self._win.halls()
        tab = getattr(self._win, "_overview", None)
        try:
            avail = tab.height() - tab._overhead(len(tab._row_widgets))
        except Exception:
            return                      # no real tab to ask: the test harness
        if avail > 0:
            halls["band_frac"] = min(0.92, max(
                0.08, float(halls["band_px"]) / float(avail)))

    def _arrange_again(self):
        # The board emits `changed` itself, which is what saves and repaints —
        # a second `settings_changed` here would rebuild every tab twice.
        self._board.arrange_again()

    def _defaults(self):
        look = self._win.look()
        look.update(C.LOOK_DEFAULTS)
        self.reload()
        self._win.look_changed()

    def _paint_preview(self, look):
        """The three example rows, AT THE SIZE THEY WILL REALLY BE.

        They were drawn smaller for a while, capped at 28 px with the
        proportions kept, so that a parked 140 px could not turn this box into
        five hundred pixels of scrolling. That was the wrong trade: "v záložce
        nastavení neodpovídá ... stejně tak dané texty", 1.10.2026. A preview
        that is not the real size is not a preview. The page is in a scroller,
        so a very big setting simply makes this box tall — which is itself
        worth seeing before the first tab is relied on.
        """
        for row in self._rows:
            row.apply_look(look)
        # The names, the numbers and the units are given one width each, for
        # the same reason the Overview does it: the units and the squares have
        # to stand in straight lines, and the whole block is centred.
        name_w = max(row.name_width_wanted() for row in self._rows)
        wanted = [row.value_width_wanted() for row in self._rows]
        number_w = max(n for n, _u in wanted)
        unit_w = max(u for _n, u in wanted)
        for row in self._rows:
            row.set_name_width(int(name_w))
            row.set_value_width(number_w, unit_w)
