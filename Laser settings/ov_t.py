"""Laser settings — the Overview tab: the five halls and the watched channels.

The whole tab answers two questions from across the room:

    which hall are we shooting into?      the five big squares
    is everything set right for it?       the tick or the cross on every row

A square is white while that hall is idle and green while we are shooting into
it. When something watched is outside that hall's limits the green square starts
BLINKING to white, so the hall itself carries the fault — you do not have to
read a single row to know there is one.

WHERE THE SQUARES SIT IS THE OPERATOR'S, NOT THIS FILE'S
--------------------------------------------------------
The five squares used to be five equal cells of one row and there was nothing to
be done about it. They are now placed BY HAND on a board on the Settings tab,
the way Image Slider's cameras are: dragged and resized freely, with the edges
snapping to each other and to the band's own middle. `_HallBand` is the strip
they live in — its height is a setting, the rectangles inside it are fractions
of it, and `ls_core.hall_tiles` answers with either the hand-made arrangement or
the even row it falls back to.

The word that says what is being shot into and what is not — SHOOTING, idle,
not read — is painted INSIDE the square by `ls_ui.HallSquare`. It was a label
on its own line underneath, which meant the square could not be moved without
dragging its caption along. It can be switched off on the Settings tab.

THE ONE-SQUARE MODE (`halls["single"]`, a tick box on the Settings tab) shows a
single square instead of the five, named after the hall the beam is going into
(`ls_core.single_hall` — Int whenever it is going into none of the others), with
the list beside it rather than under it. `band_px` is that square's side.

Only a hall whose limits are IN FORCE can blink, and which those are is
`ls_core.halls_in_force`: normally the halls being shot into, and while the
experimental valves cannot be read at all, Int. Int's square is then WHITE, not
green — we are watching Int, we are not claiming the beam is in it — and it
blinks red the same way if something is out of range.

A LIST, NOT A TABLE, AND NOTHING BEHIND IT
------------------------------------------
      Front end delay:  12     ns  [tick]
    Amplifier 1 volt.: -23.4   kV  [cross]

The name with a colon, the number, the unit, the verdict square — names flush
right, so they end on one line; after the colon one space and a slot for a
minus, then the digits, flush LEFT, so every number starts on one line; the
units in a column of their own, under each other; and the squares in one
column straight after the longest unit (2.10.2026; until then the numbers
were flush right). Before 1.10.2026 it
was name, square, number, packed together, asked for on 30.9.2026. It was a
table with a `Name` heading before, and a table hands its spare width to a
column: the name column ran the whole width of the window and the square ended
up on the far right, yards from the name it belongs to.

So there is no heading row and no table. Every row is one widget, the names all
get the same width so the squares line up down the list, and the room left over
is behind the number where it is nobody's business.

Nothing is painted BEHIND the list either — no sheet, no box, no stripes, and
no red band on the row that is out of range. That row is marked by the red
cross in its square, which is what the square is for. The text is written
straight onto the window.

How big it is written, and in what, is a SETTING — the Settings tab and
`ls_core.LOOK_DEFAULTS`. Nothing in this file decides it.

AND THE WINDOW IS ANY SIZE AT ALL. Nothing on this tab claims a minimum; the
three pieces are placed by hand in `_place_children`, not by a layout, because
a layout both arbitrates the band's height and makes its own minimum the
window's. With `halls["fit"]` on, `_fit_to_window` works the four sizes out
from the window and WRITES THEM INTO THE SETTINGS DOCUMENT — so the numbers on
the Settings tab are the numbers on screen. `ls_core.fit_sizes` is the rule.

The limits in force, the verdict in words and the channel or formula behind a
row are the row's TOOLTIP. This tab is read from across the room and a wall of
sentences is not. What the program as a whole is doing — whose limits are in
force, how many channels, when the last reading arrived — goes into the log
file; it used to be a grey line along the bottom of this one, then a tab of its
own, and the tab was taken off on 1.10.2026.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QSizePolicy,
                               QSpacerItem, QVBoxLayout, QWidget)

import ls_core as C
import ls_dm as D
import ls_ui as U

# How big the text is, how big the square is and which typeface they use are a
# SETTING — `ls_core.LOOK_DEFAULTS` and the Font tab. Nothing here decides it.

# The name column: never narrower than this, never wider. The bottom stops a
# list of two-letter names from putting the squares hard against the left edge;
# the cap stops one very long name from pushing every square out of reach, which
# is the whole complaint the table version was replaced over. A name longer than
# the cap is cut with an ellipsis and its full text is in the tooltip.
#
# Both are counted in text SIZES rather than in pixels, because the size is the
# operator's now: a 140 px floor that was comfortable at 13 px left the squares
# sitting in the middle of the names at 30 px.
# EVERY NAME IS SHOWN IN FULL. Asked for on 1.10.2026 — "udělej to tak, aby
# vždy byly vidět slova a hodnoty" — after "SBW4 Compressed" came out as "SBW4
# Compres…". So the name column takes what the longest name really needs, and
# it is the TEXT SIZE that gives way: the fit shrinks the text until the whole
# row fits the window. There is no share-of-the-width cap any more (there was a
# flat `NAME_HARD_MAX = 520`, then nine twentieths of the list) because a cap is
# the thing that produced the ellipsis.
#
# `NAME_MIN_EM` stays: it stops a list of two-letter names from putting the
# squares hard against the left edge. The ellipsis stays too, but only as the
# last resort — when even the smallest text cannot fit the name, which is a
# name of a few hundred characters and not anything anybody types.
NAME_MIN_EM = 8
NAME_MAX_EM = 26                # only while the fit is OFF; see `_name_width_for`
NAME_PAD = 8                    # so the longest name never touches the square
VALUE_PAD = 4                   # and the number never touches the right edge

# The value column never measures narrower than this, whatever is on screen.
# Three short readings must not let the text grow to a size that clips the day
# a long one arrives.
VALUE_FLOOR_TEXT = "-000000.0 " + C.unit_text("fs^3")

# Nothing has been read at all for this long: the red exclamation mark appears
# beside the halls. The same five minutes the sibling program uses.
NO_DATA_S = 300.0

# The word inside each square. Everything that is not a plain yes or no gets its
# own word: a hall with no channel set and one whose valve could not be read
# both draw the same grey square, but they are two different problems and the
# operator does something different about each.
#
# No colour per word any more. It is painted inside the square now, in the
# square's own ink — a green square's caption cannot be dark green and still be
# read, and the state is already in the colour of the square itself.
_HALL_WORDS = {
    "shooting": "SHOOTING",
    "fault":    "OUT OF RANGE",
    # Not "shooting": the valve says nobody is. It means "this is the hall whose
    # limits are being judged, because none of the others can answer".
    "watch":    "WATCHING",
    "idle":     "idle",
    # L3 only: SGV501 is open and no hall's valve is (`ls_core.l3_waiting`).
    "waiting":  "waiting for hall",
    "unknown":  "not read",
    "unset":    "no channel",
}
# Added to the word of a square being shot into while the Scanning box is ticked
# and a shown row has scan limits — one word, as asked: "SHOOTING + SCANNING".
_SCAN_SUFFIX = " + SCANNING"

# The one ink the list is written in. There is no second colour and no
# background: a row that is out of range is marked by the RED CROSS in its
# square, which is what the square is there for — said in those words on
# 30.9.2026, when the light red band went the same way as the zebra and the
# white sheet.
ROW_INK = "#16202c"


MARK_GAP = 12                   # the number → its verdict square

# What every colour and mark of the little square means, at the foot of each
# row's tooltip (asked for on 12.10.2026: "ať user ví, co znamená šedá,
# zelená, oranžová… křížek, fajfka").
_MARK_LEGEND = (
    "The square:\n"
    "  green, tick — shooting, and the number is within its limits\n"
    "  red, cross — shooting, and the number is out of its limits;\n"
    "      the hall's big square blinks\n"
    "  orange, tick / cross — read fine, but nothing is being shot, so it\n"
    "      is not checked; the mark says where the number would stand\n"
    "  white, dash — read fine, but there is nothing to judge it by\n"
    "      (no Min / Max set, or Alarm unticked)\n"
    "  grey, dash — the number cannot be read (archiver or network\n"
    "      error, nothing archived, or not read for 30 s)")


def shot_counter(win):
    """The window's `ls_core.ShotCounter`, or None (a test's stand-in window)."""
    get = getattr(win, "shots", None)
    return get() if get is not None else None


def shot_number(win):
    """The number the shot counter shows, or None when there is no counter."""
    counter = shot_counter(win)
    return None if counter is None else counter.shown


def _set_tip(widget, text):
    """A tooltip, only when it says something new. `setToolTip` sends an event
    every time it is called, and the refresh runs once a second."""
    if widget.toolTip() != text:
        widget.setToolTip(text)


def name_shown(name):
    """A row's name as it is written: with a colon after it ("GDD:")."""
    name = name or ""
    return name + ":" if name else ""


def name_gap(fm):
    """The gap after the colon: one space of the row's own font."""
    return fm.horizontalAdvance(" ")


def split_value(text):
    """("-12000", "fs²") out of `C.value_text`'s "-12000 fs²".

    The number and its unit are two columns on screen, so the numbers can
    start right after the colon AND the units stand under each other.
    """
    number, _, unit = (text or "").partition(" ")
    return number, unit.strip()


def value_advance(fm, number):
    """How wide a number is drawn: a minus slot, then its digits.

    The slot is there whether the number has a minus or not, so every row's
    digits start on one line and a minus stands in front of them.
    """
    number = number or ""
    return fm.horizontalAdvance("-") + fm.horizontalAdvance(number.lstrip("-"))


def unit_advance(fm, unit):
    """How wide a unit is drawn: a space before it, and the trailing pad.

    The pad is always there, unit or none, so the number never touches the
    square; a row with no unit has an empty cell in the unit column.
    """
    gap = fm.horizontalAdvance(" ") + fm.horizontalAdvance(unit) if unit else 0
    return gap + VALUE_PAD


class _NameEdit(QLineEdit):
    """The box a row's name is retyped in. `done(True)` on Enter or when it
    loses the focus, `done(False)` on Esc — once, whichever comes first."""

    done = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._finished = False
        self.editingFinished.connect(lambda: self._finish(True))

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self._finish(False)
            return
        super().keyPressEvent(ev)

    def _finish(self, keep):
        if not self._finished:
            self._finished = True
            self.done.emit(keep)


class _Row(QFrame):
    """One watched channel: its name, its verdict square, its number.

    Nothing is painted behind it. Everything is written only when it has
    actually changed — `refresh()` runs once a second on every row, and a
    stylesheet re-applied a second is a re-layout a second for a picture that
    never moved.
    """

    def __init__(self, look=None, parent=None):
        super().__init__(parent)
        self.setObjectName("listRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)

        # Name, number, unit, square. The names flush RIGHT, so they end on one
        # line; after the colon one space, then a slot for a minus, then the
        # digits — the numbers flush LEFT, all starting on one line. The unit
        # is a column of its own, so the units stand under each other, and the
        # squares stand in one column straight after the longest unit. Asked
        # for on 2.10.2026:
        #
        #               GDD:  23800 fs²  [tick]
        #               TOD: -88000 fs³  [tick]
        #   SBW4 Compressed:  19.81 J    [tick]
        self._name = QLabel("")
        self._name.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        self._mark = U.VerdictMark(C.LOOK_DEFAULTS["mark_px"])
        self._value = QLabel("")
        self._value.setAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter)
        self._unit = QLabel("")
        self._unit.setAlignment(Qt.AlignmentFlag.AlignLeft
                                | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._name)
        # The space after the colon, sized to the font in `apply_look`.
        self._gap = QSpacerItem(0, 0, QSizePolicy.Policy.Fixed,
                                QSizePolicy.Policy.Minimum)
        lay.addItem(self._gap)
        lay.addWidget(self._value)
        lay.addWidget(self._unit)
        lay.addSpacing(MARK_GAP)
        lay.addWidget(self._mark)
        # NO trailing stretch. Both columns are given a fixed width, so the row
        # is exactly as wide as its contents and the list can be centred as one
        # block — asked for on 1.10.2026, "zarovnej tu tabulku na střed". A
        # stretch here would make every row as wide as the window, and a
        # centred row that is already full width is not centred at all.

        self._full_name = None
        self._value_text = None
        self._tip = None
        self._name_width = 0
        self._value_width = 0           # number column + unit column
        self._widths = (0, 0)
        # Double-click renames (12.10.2026, "přejmenovávat PVčka podle svého"):
        # the tab hands in what to do with the new name; None = not renamable.
        self.on_rename = None
        self._edit = None
        self.apply_look(look or C.LOOK_DEFAULTS)

    # ── renaming in place ──────────────────────────────────────────────────
    def mouseDoubleClickEvent(self, ev):
        if self.on_rename is None or self._edit is not None:
            super().mouseDoubleClickEvent(ev)
            return
        self.start_rename()

    def start_rename(self):
        """A box over the name, holding the name, all of it selected. Enter or
        clicking away keeps what was typed, Esc keeps the old name."""
        edit = _NameEdit(self)
        edit.setFont(self._name.font())
        size = self._name.font().pixelSize()
        edit.setStyleSheet(
            f"QLineEdit {{ color: {ROW_INK}; background: #ffffff;"
            f" border: 1px solid {U.ACCENT}; padding: 0px 2px;"
            f" font-family: \"{self._name.font().family()}\";"
            f" font-size: {size}px;"
            f" font-weight: {700 if self._name.font().bold() else 400}; }}")
        edit.setText(self._full_name or "")
        edit.selectAll()
        geo = self._name.geometry()
        fm = QFontMetrics(self._name.font())
        width = max(geo.width(), fm.horizontalAdvance(edit.text() + "MM"))
        edit.setGeometry(geo.x(), geo.y(), width, geo.height())
        edit.done.connect(self._rename_done)
        self._edit = edit
        edit.show()
        edit.setFocus()

    def _rename_done(self, keep):
        edit, self._edit = self._edit, None
        if edit is None:
            return
        text = edit.text().strip()
        edit.hide()
        edit.deleteLater()
        if keep and text and text != (self._full_name or "") \
                and self.on_rename is not None:
            self.set_name(text)
            self.on_rename(text)

    # ── how big it is ──────────────────────────────────────────────────────
    def apply_look(self, look):
        """The typeface, the size and the square, from the settings document.

        Written TWICE on purpose, and both are needed:

        * as a `QFont` on the two labels, because the name is measured with
          `QFontMetrics(label.font())` and a stylesheet never reaches `font()`
          — the column would be measured in one typeface and painted in
          another;
        * in the row's own stylesheet, because `ls_ui.APP_QSS` sets
          `font-family` on every QWidget, and a stylesheet BEATS `setFont`.
          Measured on 30.9.2026: with the font alone, picking Consolas changed
          the settings file and nothing on screen.

        The two say the same thing, so nothing can drift between them.
        """
        family = look["family"]
        size = int(look["size_px"])
        weight = 700 if look["bold"] else 400
        # A stylesheet is the expensive part (a re-polish of the row and both
        # labels), and the fit hands every row the look again on each solve —
        # usually the very same one.
        key = (family, size, bool(look["bold"]), int(look["mark_px"]))
        if key == getattr(self, "_look_key", None):
            return
        self._look_key = key
        font = self.font()
        font.setFamily(family)
        font.setPixelSize(size)
        font.setBold(bool(look["bold"]))
        self._name.setFont(font)
        self._value.setFont(font)
        self._unit.setFont(font)
        # Scoped by object name, because a QLabel IS a QFrame: an unscoped
        # `QFrame` rule here would paint the two labels as well.
        self.setStyleSheet(
            f"#listRow {{ background: transparent; }}"
            f"#listRow QLabel {{ color: {ROW_INK}; background: transparent;"
            f" font-family: \"{family}\"; font-size: {size}px;"
            f" font-weight: {weight}; }}")
        self._mark.set_side(int(look["mark_px"]))
        self._gap.changeSize(name_gap(QFontMetrics(font)), 0,
                             QSizePolicy.Policy.Fixed,
                             QSizePolicy.Policy.Minimum)
        self.layout().invalidate()
        self._draw_name()
        self._draw_value()

    # ── what it says ───────────────────────────────────────────────────────
    def set_name(self, text):
        """The name, cut with an ellipsis if it is wider than its column."""
        if text == self._full_name:
            return
        self._full_name = text
        self._draw_name()

    def set_value(self, text):
        if text != self._value_text:
            self._value_text = text
            self._draw_value()

    def _draw_value(self):
        """The number, with the minus slot as an indent when it has no minus,
        and the unit in its own column, a space after the number column."""
        number, unit = split_value(self._value_text)
        fm = QFontMetrics(self._value.font())
        self._value.setIndent(0 if number.startswith("-")
                              else fm.horizontalAdvance("-"))
        self._value.setText(number)
        self._unit.setIndent(fm.horizontalAdvance(" ") if unit else 0)
        self._unit.setText(unit)

    def set_state(self, state):
        self._mark.set_state(state)

    def set_tip(self, text):
        # On the row AND on each of the three widgets in it: Qt does not hand a
        # tooltip down to a child, and a row whose middle is silent reads as a
        # row with nothing to say.
        if text == self._tip:
            return
        self._tip = text
        for widget in (self, self._name, self._mark, self._value, self._unit):
            widget.setToolTip(text)

    # ── the two columns ────────────────────────────────────────────────────
    # Both are given one width for the whole list, so the verdict squares stand
    # in a straight line and the numbers start on one. Both widths are FIXED,
    # which is also what lets the row be exactly as wide as its contents and
    # the list be centred as one block.
    def name_width_wanted(self):
        """How wide this row's name needs to be, in full."""
        fm = QFontMetrics(self._name.font())
        return fm.horizontalAdvance(name_shown(self._full_name)) + NAME_PAD

    def value_width_wanted(self):
        """(number, unit): how wide this row's two columns need to be."""
        fm = QFontMetrics(self._value.font())
        number, unit = split_value(self._value_text)
        return value_advance(fm, number), unit_advance(fm, unit)

    def set_name_width(self, width):
        if width == self._name_width:
            return
        self._name_width = int(width)
        self._name.setFixedWidth(self._name_width)
        self._draw_name()

    def set_value_width(self, number_w, unit_w):
        """Fixed widths for the number column and the unit column."""
        widths = (int(number_w), int(unit_w))
        if widths == self._widths:
            return
        self._widths = widths
        self._value_width = sum(widths)
        self._value.setFixedWidth(widths[0])
        self._unit.setFixedWidth(widths[1])

    def block_size(self):
        """(width, height) of the row, worked out from its own columns.

        Not `sizeHint()`: a layout's hint is only brought up to date on the
        next turn of the event loop, and the one-square mode places the list
        straight after the columns were given new widths. Measured 1.10.2026:
        the hint still said 16 px there and the list came out a sliver.
        """
        lay = self.layout()
        m = lay.contentsMargins()
        side = self._mark.side()
        fm = QFontMetrics(self._name.font())
        w = (self._name_width + name_gap(fm) + self._value_width + MARK_GAP
             + side + m.left() + m.right())
        h = max(fm.height(), side) + m.top() + m.bottom()
        return w, h

    def _draw_name(self):
        text = name_shown(self._full_name)
        if self._name_width > 0:
            fm = QFontMetrics(self._name.font())
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                 self._name_width)
        self._name.setText(text)

    # ── what the tests read back ───────────────────────────────────────────
    def texts(self):
        return self._full_name or "", self._value_text or ""

    def state(self):
        return self._mark.state()


class _HallBand(QWidget):
    """The strip the five squares live in, and where each one sits inside it.

    No layout. The squares are placed by hand — `ls_core.hall_tiles` gives their
    rectangles as fractions of this widget and `_place` turns them into pixels
    on every resize, so a hand-made arrangement keeps its proportions when the
    window is dragged wider. A QHBoxLayout would simply overrule all of it.

    The band's HEIGHT is a setting (`halls["band_px"]`), and this widget does
    nothing about it but remember it. `set_band` records what the band WANTS;
    the tab decides what it gets, in `OverviewTab._place_children`.

    No `setFixedHeight`, no minimum and no maximum here, on purpose. Any of
    them is a floor under the whole window — a band of 400 px meant the window
    could not be made shorter than about 500, which is half of why it would not
    go small on 1.10.2026, and "úplně bez minima" was the answer.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hallBand")
        # Stated, not inherited: this PC is in Windows dark mode and an
        # unpainted widget is a gamble.
        self.setStyleSheet(f"#hallBand {{ background: {U.PAPER}; }}")
        self._halls = dict(C.HALLS_DEFAULTS)
        self._wanted = int(C.HALLS_DEFAULTS["band_px"])
        self.squares = {}
        for hall in C.HALLS:
            # Int's square says L3 — `ls_core.hall_shown`.
            self.squares[hall] = U.HallSquare(C.hall_shown(hall), self)
        # The mark means one thing only: nothing at all has been read for five
        # minutes. It is pinned to the top right corner of the band and is not
        # part of the arrangement — it must not be draggable, and it must not
        # move when a square does.
        self.mark = U.AlertMark(40, self)
        self.mark.setToolTip("Nothing has been read for over five minutes.")
        self.mark.hide()

    def apply_halls(self, halls):
        """The band's settings: the word and the arrangement. Its height and
        the word's size are `set_band`'s — the tab works them out from the
        window, see `OverviewTab._fit_to_window`."""
        self._halls = halls
        for square in self.squares.values():
            square.set_word_visible(bool(halls["show_word"]))
        self._place()

    def set_band(self, height_px, word_px):
        """How tall the band wants to be, and how big the word in it is.

        Only `wanted` is recorded; the geometry is the TAB's, which places this
        widget by hand in `OverviewTab._place_children`. Nothing here sets a
        minimum or a maximum of its own, because a widget's minimum is its
        window's minimum and the window may be any size.
        """
        self._wanted = max(1, int(height_px))
        for square in self.squares.values():
            square.set_word_px(int(word_px))
        self._place()

    def wanted(self):
        return self._wanted

    def relayout(self):
        """Place the five squares again, now.

        Called by the tab straight after it has moved this widget, and NOT left
        to `resizeEvent`: Qt does not deliver a resize to a widget that has
        never been shown, it holds it until the widget is. So on a window that
        is built and measured without being shown — which is every test and
        every rendered picture in `testing/` — the squares stayed laid out for
        the band's previous height. Measured 1.10.2026: a band moved from 150
        to 220 kept squares sized for 150.
        """
        self._place()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place()

    def _place(self):
        w, h = max(1, self.width()), max(1, self.height())
        tiles = C.hall_tiles(self._halls, w, h)
        for hall, square in self.squares.items():
            x, y, tw, th = tiles[hall]
            square.setGeometry(int(round(x * w)), int(round(y * h)),
                               max(1, int(round(tw * w))),
                               max(1, int(round(th * h))))
        # Moved, never resized: `AlertMark` fixes its own size from the height
        # it was built with, so a setGeometry here would be ignored for the size
        # and only confuse the next person to read it.
        self.mark.move(max(0, w - self.mark.width() - 4), 4)
        self.mark.raise_()


class OverviewTab(QWidget):
    """The five squares and the list of readings."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._squares = {}
        self._rows_on_screen = []       # row dicts, in list order
        self._row_widgets = []          # one `_Row` each, same order
        self._built_at = time.time()
        self._fitting = False
        self._fitted = None
        self._firing = C.FiringWatch()
        self._build()

        # The fit, one solve per event-loop turn however many resizes arrive.
        # See `_fit_soon`.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_to_window)

        # One beat for all five squares, so they cannot drift apart on screen.
        # Precise, because a coarse timer makes a blink visibly uneven.
        self._blink = QTimer(self)
        self._blink.setTimerType(Qt.TimerType.PreciseTimer)
        self._blink.setInterval(C.BLINK_MS)
        self._blink.timeout.connect(self._on_blink)
        self._blink_on = True
        self._blink.start()

        self.reload()

    # ── building ───────────────────────────────────────────────────────────
    # NO LAYOUT ON THIS TAB, and that is deliberate. The three pieces — the
    # band, the line, the list — are placed by hand in `_place_children`.
    #
    # It was a QVBoxLayout until 1.10.2026 and the layout kept arbitrating the
    # one thing that is not negotiable: the band is as tall as the setting says
    # it is. Measured that day on a 480 px tab with the band set to 150 — the
    # layout handed it 113 and gave the extra to the scroll area, through three
    # different size policies. And a layout's minimum is the window's minimum,
    # which is the other thing that had to go ("úplně bez minima").
    #
    # Placed by hand, both fall out for free: the band gets exactly what it is
    # set to whenever that fits, the list gets the rest, and the tab asks the
    # window for nothing at all. It is the same reason `_HallBand` places its
    # own five squares by hand.
    MARGIN = 10                     # all four sides
    GAP = 8                         # band → line, line → list
    LIST_MIN = 40                   # the list may never vanish altogether

    def _build(self):
        # The band, not a row of layout cells: where each square sits is the
        # operator's and is set on the Settings tab. See `_HallBand`.
        self._band = _HallBand(self)
        self._squares = self._band.squares
        self._mark = self._band.mark

        self._sep = U.hsep()
        self._sep.setParent(self)

        # The one-square mode: one square whose name follows the hall being
        # shot into, with the list BESIDE it. Built always and shown only in
        # that mode, so switching is a placement and not a rebuild. Its own
        # mark, because the band's lives inside the band and the band is
        # hidden in this mode.
        self._single = U.HallSquare(C.hall_shown(C.FALLBACK_HALL), self)
        self._single.hide()
        self._single_mark = U.AlertMark(40, self)
        self._single_mark.setToolTip(
            "Nothing has been read for over five minutes.")
        self._single_mark.hide()

        # The list: rows stacked on the window's own background. No sheet, no
        # box, no stripes — "prostě měj napsané GDD, čtvereček value … jen tak
        # na pozadí", 30.9.2026. The grey is still stated at every level rather
        # than left to Qt: `APP_QSS` paints a bare QWidget this grey, but this
        # PC is in Windows dark mode and anything unpainted is a gamble.
        self._sheet = QWidget()
        self._sheet.setObjectName("listSheet")
        self._sheet.setStyleSheet(f"#listSheet {{ background: {U.PAPER}; }}")
        self._list = QVBoxLayout(self._sheet)
        self._list.setContentsMargins(0, 2, 0, 2)
        # A little air between the lines, now that no band separates them.
        self._list.setSpacing(2)
        self._list.addStretch(1)        # the rows go in above this, always last

        self._scroll = QScrollArea(self)
        self._scroll.setWidget(self._sheet)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Never sideways. A name too long for its column is ELIDED, with the
        # full text in the row's tooltip, so there is nothing off to the right
        # to scroll to — and a horizontal bar that can come and go changes the
        # viewport's HEIGHT, which is one of the two ways the fit could have
        # been made to oscillate. The other one is handled in `_list_width`.
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {U.PAPER}; border: 0px; }}"
            + U.SCROLLBAR_QSS)
        self._scroll.viewport().setStyleSheet(f"background: {U.PAPER};")

        # The shot counter: one line ABOVE the list, "Shot counter: 12", and
        # not a row of it — no verdict square, smaller text of its own size
        # (5.10.2026). Hidden while its Show is unticked on the Limits tab.
        self._shot_row = None
        self._shot_label = QLabel("", self)
        self._shot_label.setObjectName("shotLine")
        self._shot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._shot_label.hide()

        # Scanning: ONE switch for the whole list (12.10.2026), on the same
        # line as the shot counter, at its left end. Ticked, every row with a
        # Scan min / Scan max is judged against them instead of the hall's.
        self._scan_loading = False
        self._scan_box = QCheckBox("Scanning", self)
        self._scan_box.setToolTip(
            "Ticked: every channel that has Scan min or Scan max set on the "
            "Limits tab is checked against those instead of its hall's Min and "
            "Max. Channels with no scan limits keep their normal limits.\n\n"
            "Untick it when the scan is over — the normal limits apply at once.")
        self._style_scan_box(False)
        self._scan_box.toggled.connect(self._scan_toggled)
        self._place_children()

    def _shot_h(self):
        """How tall the shot counter's line is, 0 when it is not shown."""
        if self._shot_row is None:
            return 0
        return QFontMetrics(self._shot_label.font()).height() + 2

    def _line_h(self):
        """How tall the line above the list is: the Scanning box, and the shot
        counter beside it when that is shown. Never 0 — the box is always
        there."""
        return max(self._shot_h(), self._scan_box.sizeHint().height())

    def _place_line(self, x, y, w, h):
        """The Scanning box at the left of the line, the shot counter centred
        on the line's full width (clear of the box on both sides)."""
        box_w = self._scan_box.sizeHint().width()
        box_h = self._scan_box.sizeHint().height()
        self._scan_box.setGeometry(x, y + (h - box_h) // 2, box_w, box_h)
        shot_h = self._shot_h()
        if shot_h:
            pad = box_w + self.GAP
            self._shot_label.setGeometry(x + pad, y + (h - shot_h) // 2,
                                         max(1, w - 2 * pad), shot_h)
        self._shot_label.setVisible(bool(shot_h))

    def _apply_shot_look(self):
        """The line's typeface from the list's look, its size from its own
        setting. Written as a font AND in the stylesheet — `ls_ui.APP_QSS`
        sets the family on every widget, and a stylesheet beats `setFont`."""
        get = getattr(self._win, "shot_settings", None)
        shots = get() if get is not None else {
            "size_px": C.SHOT_SIZE_PX_DEFAULT, "bold": C.SHOT_BOLD_DEFAULT}
        family = self._win.look()["family"]
        size, bold = int(shots["size_px"]), bool(shots["bold"])
        font = self._shot_label.font()
        font.setFamily(family)
        font.setPixelSize(size)
        font.setBold(bold)
        self._shot_label.setFont(font)
        self._shot_label.setStyleSheet(
            f"#shotLine {{ color: {ROW_INK}; background: transparent;"
            f" font-family: \"{family}\"; font-size: {size}px;"
            f" font-weight: {700 if bold else 400}; }}")

    def _place_children(self):
        """The band, the line and the list, by hand. See the note above.

        Returns the band's real height: the band's SETTING whenever it fits,
        what is left when it does not, and never more than the even row's
        squares need (`ls_core.band_needed`) — the extra goes to the list.

        In the one-square mode it is `_place_single` instead, and the band's
        height is 0.
        """
        if self._is_single():
            return self._place_single()
        self._single.hide()
        self._single_mark.hide()
        self._sep.show()
        m, g = self.MARGIN, self.GAP
        w = max(1, self.width() - 2 * m)
        sep_h = max(1, self._sep.sizeHint().height())
        room = self.height() - 2 * m - 2 * g - sep_h - self.LIST_MIN
        # Never taller than the squares in it: a band higher than their side is
        # an empty strip, and that room belongs to the list. "Height of the
        # band" is the upper limit; the squares decide the rest.
        band_h = max(0, min(self._band.wanted(), room,
                            C.band_needed(self._win.halls(), w,
                                          self._band.wanted())))
        self._band.setGeometry(m, m, w, band_h)
        self._band.setVisible(band_h > 0)
        self._band.relayout()
        y = m + band_h + g
        self._sep.setGeometry(m, y, w, sep_h)
        y += sep_h + g
        line_h = self._line_h()
        self._place_line(m, y, w, line_h)
        y += line_h + g
        self._scroll.setGeometry(m, y, w, max(1, self.height() - m - y))
        return band_h

    # ── the one-square mode ────────────────────────────────────────────────
    def _is_single(self):
        return bool(self._win.halls().get("single"))

    def _single_side(self):
        """The one square's side: its setting (`band_px`), never more than the
        tab's height, and never so much that the list has no room beside it."""
        m = self.MARGIN
        h = max(1, self.height() - 2 * m)
        w = max(1, self.width() - 2 * m)
        room = w - C.SINGLE_GAP_PX - self.LIST_MIN
        return max(1, min(int(self._band.wanted()), h, room))

    def _content_size(self):
        """How wide and tall the list's rows are, from the rows themselves
        (see `_Row.block_size`). With no rows, the note's own hint."""
        lm = self._list.contentsMargins()
        if not self._row_widgets:
            hint = self._sheet.sizeHint()
            return hint.width(), hint.height()
        sizes = [r.block_size() for r in self._row_widgets]
        n = len(sizes)
        w = max(s[0] for s in sizes) + lm.left() + lm.right()
        # `n` gaps: the trailing stretch takes one too, see `_overhead`.
        h = (sum(s[1] for s in sizes) + n * self._list.spacing()
             + lm.top() + lm.bottom())
        return w, h

    def _place_single(self):
        """Square on the left, list beside it, the pair centred as one block.

        The list's area is as tall and as wide as its rows (up to what the tab
        has), so the rows sit next to the square rather than at the top of a
        column of empty space.
        """
        m = self.MARGIN
        h = max(1, self.height() - 2 * m)
        w = max(1, self.width() - 2 * m)
        self._band.hide()
        self._sep.hide()
        side = self._single_side()
        bar = self._scroll.verticalScrollBar().sizeHint().width()
        frame = 2 * self._scroll.frameWidth()
        content_w, content_h = self._content_size()
        room = max(1, w - side - C.SINGLE_GAP_PX)
        # The line (Scanning, shot counter) sits over the list, in the list's
        # column, and the pair is centred against the square as one block.
        line_h = self._line_h()
        line_block = line_h + self.GAP
        list_w = max(1, min(room, content_w + bar + frame))
        list_h = max(1, min(h - line_block, content_h + frame))
        x0 = m + max(0, (w - (side + C.SINGLE_GAP_PX + list_w)) // 2)
        self._single.setGeometry(x0, m + (h - side) // 2, side, side)
        self._single.show()
        x_list = x0 + side + C.SINGLE_GAP_PX
        y_list = m + max(0, (h - list_h - line_block) // 2) + line_block
        line_w = list_w
        if self._shot_h():
            text_w = QFontMetrics(self._shot_label.font()).horizontalAdvance(
                self._shot_label.text()) + 4
            line_w = max(line_w, text_w + 2 * (
                self._scan_box.sizeHint().width() + self.GAP))
        self._place_line(x_list, y_list - line_block, min(room, line_w),
                         line_h)
        self._scroll.setGeometry(x_list, y_list, list_w, list_h)
        # Moved, never resized — see `_HallBand._place`.
        self._single_mark.move(
            max(0, self._single.x() + side - self._single_mark.width() - 4),
            self._single.y() + 4)
        self._single_mark.raise_()
        return 0

    def minimumSizeHint(self):
        # Nothing. The tab must never be what stops the window being made
        # smaller — "úplně bez minima", 1.10.2026 — and with everything placed
        # by hand there is nothing it needs to ask for.
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(640, 420)

    # ── the beat ───────────────────────────────────────────────────────────
    def _on_blink(self):
        self._blink_on = not self._blink_on
        for square in self._squares.values():
            square.set_blink(self._blink_on)
        self._single.set_blink(self._blink_on)

    # ── filling ────────────────────────────────────────────────────────────
    def _clear_list(self):
        """Every row off the sheet. The trailing stretch is left where it is."""
        while self._list.count() > 1:
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._row_widgets = []

    def reload(self):
        """The list of channels changed: build the rows again, then paint them.

        The rows never come and go as the laser moves from hall to hall. Show is
        one switch for the whole program, so a row that is here is here in every
        hall — losing your place in the list every time the machine switched
        halls would be worse than a grey row.
        """
        # The band first: its height, the arrangement and the word inside the
        # squares all come off the same settings document the list's look does,
        # and a change to any of them arrives here as a rebuild.
        self._band.apply_halls(self._win.halls())
        self._single.set_word_visible(bool(self._win.halls()["show_word"]))
        look = self._win.look()
        shown = [r for r in self._win.rows() if C.is_shown(r)]
        # The shot counter is the line above the list, not a row of it.
        self._shot_row = next((r for r in shown if C.is_shot_row(r)), None)
        self._apply_shot_look()
        rows = [r for r in shown if not C.is_shot_row(r)]
        self._rows_on_screen = rows
        self._clear_list()
        for i, row in enumerate(rows):
            widget = _Row(look, self._sheet)
            widget.set_name(C.row_label(row))
            widget.on_rename = lambda text, r=row: self._renamed(r, text)
            # Centred. Every row is exactly as wide as the block, so the three
            # columns sit in the middle of the window as one table — "zarovnej
            # tu tabulku na střed", 1.10.2026.
            self._list.insertWidget(i, widget, 0,
                                    Qt.AlignmentFlag.AlignHCenter)
            self._row_widgets.append(widget)
        if not rows:
            note = QLabel("Nothing is being watched yet — add channels on the "
                          "Settings tab and tick Show.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {U.QUIET}; font-size: 12px;"
                               " background: transparent; padding: 8px 0px;")
            self._list.insertWidget(0, note)
        self._fit_to_window(force=True)
        self.refresh()

    def _renamed(self, row, text):
        """A name was retyped on this tab: into the row, and the Limits table
        built again so its Name cell says the same. Deferred one turn — we are
        inside the edit box's own signal, and a rebuild deletes it."""
        row["name"] = text
        hook = getattr(self._win, "rows_changed", None)
        if hook is not None:
            QTimer.singleShot(0, self, hook)
        else:
            QTimer.singleShot(0, self, self._win.settings_changed)

    def _scan_toggled(self, on):
        if self._scan_loading:
            return
        set_scanning = getattr(self._win, "set_scanning", None)
        if set_scanning is not None:
            set_scanning(on)
        self._style_scan_box(on)

    def _style_scan_box(self, on):
        """Ticked, the caption goes bold. No coloured ground — the amber one
        was taken off on 12.10.2026 ("divný žlutý obrys"). Only when it
        changes."""
        if getattr(self, "_scan_styled", None) == bool(on):
            return
        self._scan_styled = bool(on)
        extra = "QCheckBox { font-weight: 700; }" if on else ""
        self._scan_box.setStyleSheet(U.chk_qss() + extra)

    def restyle(self):
        """The LOOK changed, not the list: the same rows, repainted in place.

        Every box on the Settings tab and every drag on its board arrives here.
        It used to be a `reload()` — every row deleted and built again, once per
        wheel notch and once per mouse move of a drag — which is what made the
        tab flicker and lag. The rows stay; `_fit_to_window(force=True)` hands
        them the new look, sizes the band and the columns and places everything.
        Updates are held off so the whole change lands in one paint.
        """
        self.setUpdatesEnabled(False)
        try:
            halls = self._win.halls()
            self._band.apply_halls(halls)
            self._single.set_word_visible(bool(halls["show_word"]))
            self._apply_shot_look()
            self._fit_to_window(force=True)
        finally:
            self.setUpdatesEnabled(True)

    def rearrange(self):
        """A square was dragged on the Settings board: move the squares, and
        leave the sizes to the coalesced fit — never a solve per mouse move."""
        self._band.apply_halls(self._win.halls())
        self._place_children()
        if self._win.halls().get("fit", True):
            self._fit_soon()

    # ── filling the window ─────────────────────────────────────────────────
    # Asked for on 1.10.2026: the window any size, and the squares and the list
    # then filling it "bez nějakých větších mezer". The switch is
    # `halls["fit"]`; the rule is `ls_core.fit_sizes`, which has no Qt in it;
    # this is the part that MEASURES — how tall and how wide a row is at a given
    # text size, which only Qt can answer.
    #
    # IT WRITES WHAT IT WORKED OUT INTO THE SETTINGS DOCUMENT. The first attempt
    # kept a scale factor here and multiplied at drawing time, so the Settings
    # tab said "Height of the band 400 px" over a band 253 px tall. The numbers
    # on that tab are now the numbers being used, always.
    #
    # Run on a resize and a reload only, never on the once-a-second refresh: a
    # number getting one digit longer must not make the whole tab breathe.
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # Place first, so the three pieces are never seen at the old size, then
        # work the sizes out on the next turn of the event loop.
        self._place_children()
        self._fit_soon()

    def _base_font(self, look, size):
        font = self.font()
        font.setFamily(look["family"])
        font.setPixelSize(max(1, int(size)))
        font.setBold(bool(look["bold"]))
        return font

    def _overhead(self, n):
        """The vertical pixels that never grow, whatever the sizes are.

        The tab's own margins and gaps (the same constants `_place_children`
        places by), the line, the list layout's margins, and each row's 2 + 2
        of padding plus the gap after it.

        `n` GAPS, NOT `n - 1`. The list layout ends in `addStretch(1)`, and a
        `QBoxLayout` puts its spacing before a spacer exactly as it does before
        a widget — so `n` rows plus the stretch is `n + 1` items and `n` gaps.
        The first attempt at this used `n - 1` and came out two pixels short per
        row, which on a long list is a whole row of overflow.
        """
        lm = self._list.contentsMargins()
        return (2 * self.MARGIN + 2 * self.GAP
                + self._line_h() + self.GAP
                + self._sep.sizeHint().height()
                + 2 * self._scroll.frameWidth()
                + lm.top() + lm.bottom()
                + n * (4 + self._list.spacing()))    # each row's 2 + 2, + gap

    def _band_width(self):
        return max(1, self.width() - 2 * self.MARGIN)

    def _list_width(self):
        """The width one row really has.

        The vertical scrollbar is ALWAYS counted, whether it is showing or not.
        Without that: the list fits, the bar hides, the viewport gets sixteen
        pixels wider, a bigger text now fits, the list overflows, the bar comes
        back — for ever, flickering. Sixteen pixels of width is a cheap price
        for a picture that stands still.
        """
        bar = self._scroll.verticalScrollBar().sizeHint().width()
        width = self._band_width()
        if self._is_single():
            width -= self._single_side() + C.SINGLE_GAP_PX
        return max(1, width - 2 * self._scroll.frameWidth() - bar)

    def _name_width_for(self, fm, size, list_w, name_text=None):
        """How wide the name column comes out at this text size.

        The ONE rule, used both by the fit (to know whether a text size fits
        the window) and by `_fit_columns` (to actually set the width), so the
        fit can never pick a size the drawing then needs more room for than the
        fit allowed.

        THE LONGEST NAME IS HONOURED IN FULL. It is the text SIZE that gives
        way — the fit shrinks it until the whole row fits — which is how "vždy
        byly vidět slova a hodnoty" is kept. The only ceiling is the window
        itself: a column wider than the list it sits in could never be drawn,
        and at that point the name is elided and its full text is in the row's
        tooltip. With the fit OFF there is nothing to shrink the text, so
        `NAME_MAX_EM` holds there and keeps the squares reachable.
        """
        size = max(1, int(size))
        ceiling = max(size, int(list_w))
        if not self._win.halls().get("fit", True):
            ceiling = min(ceiling, NAME_MAX_EM * size)
        floor = min(NAME_MIN_EM * size, ceiling)
        if name_text is None:
            name_text = self._widest()[0]
        wanted = fm.horizontalAdvance(name_text) + NAME_PAD
        return max(floor, min(int(wanted), ceiling))

    def _widest(self):
        """(longest name, longest number, every unit) on screen, as strings.

        Picked once per solve, not once per measurement: the bisection asks
        about ten text sizes and measuring thirty rows at each of them is three
        hundred advances for an answer a few strings can give. The units are
        all kept — there are only a handful and their widths do not follow
        their lengths ("mbar" against "fs³").
        """
        names = [name_shown(w.texts()[0]) for w in self._row_widgets]
        numbers = [split_value(w.texts()[1])[0] for w in self._row_widgets]
        values = [n for n in numbers if n and n != "—"]
        units = sorted({split_value(w.texts()[1])[1]
                        for w in self._row_widgets} - {""})
        if not values:
            number, unit = split_value(VALUE_FLOOR_TEXT)
            units = sorted(set(units) | {unit})
        # The numbers REALLY on screen, plus one digit of headroom, so a
        # reading that gains a digit still fits. It used to be a worst case,
        # "-000000.0 fs³", whatever was on screen — and on three short readings
        # that left the text small with half the window unused, "nevyužívá
        # potenciál daného místa", 1.10.2026. A number that outgrows the
        # headroom makes `refresh` solve again (`_value_room_changed`).
        # Before anything has been read there is nothing to measure, and the
        # worst case is the honest guess. Picked by LENGTH: it is a decision
        # about which string to measure, made before there is a font.
        number = ("0" + max(values, key=len)) if values else number
        return max(names, key=len, default=""), number, units

    def _value_room_changed(self):
        """True when the numbers on screen no longer match what the fit was
        solved for: one has outgrown the digit of headroom, or they have all
        got two digits shorter (room the text could take back). One digit up
        or down inside that never re-solves, so the tab does not breathe."""
        fitted = getattr(self, "_fit_value_len", None)
        now = len(self._widest()[1])
        return fitted is None or now > fitted or now < fitted - 1

    def _row_measures(self, look, list_w):
        """`(row_h, row_w)` — one row's height and width at a text size.

        Handed to `ls_core.fit_sizes`, which cannot measure text itself. The
        metrics are memoised: the bisection asks for the same size more than
        once and building a `QFontMetrics` is the expensive part.
        """
        cache = {}
        name_text, number_text, units = self._widest()

        def metrics(t):
            if t not in cache:
                cache[t] = QFontMetrics(self._base_font(look, t))
            return cache[t]

        def row_h(t):
            # The verdict square is the taller of the two at this ratio, but
            # measure rather than assume — a typeface with tall ascenders could
            # go either way at the small sizes.
            mark = int(round(t * C.FIT_MARK_RATIO))
            return max(metrics(t).height(), mark)

        def row_w(t):
            fm = metrics(t)
            mark = int(round(t * C.FIT_MARK_RATIO))
            # The same pieces `_Row.block_size` adds up: the space after the
            # colon, the number with its minus slot, the unit column, the gap
            # to the square.
            unit_w = max([unit_advance(fm, u) for u in units]
                         or [unit_advance(fm, "")])
            return (self._name_width_for(fm, t, list_w, name_text)
                    + name_gap(fm) + value_advance(fm, number_text) + unit_w
                    + MARK_GAP + mark)

        return row_h, row_w

    def _fit_soon(self):
        """Coalesce a burst of resizes into one solve, next event-loop turn.

        Dragging a window edge fires `resizeEvent` dozens of times a second,
        and each one would otherwise write the settings document in the middle
        of Qt's own layout pass — which is the worst possible place for it. One
        timer member, restarted only when it is not already waiting, so a
        hundred resizes cost one solve. Not `QTimer.singleShot`, which queues
        one call per resize.
        """
        if not self._fit_timer.isActive():
            self._fit_timer.start()

    def _fit_to_window(self, force=False):
        # Re-entry guard: giving the band a new wanted height re-lays-out this
        # tab, and a layout pass can come back through here.
        if getattr(self, "_fitting", False):
            return
        look = self._win.look()
        halls = self._win.halls()
        n = len(self._row_widgets)
        parked = False
        self._fit_value_len = len(self._widest()[1])
        if (halls.get("fit", True) and halls.get("single")
                and self.height() > 40 and self.width() > 40):
            # The one square beside the list. The name ceiling is measured
            # against the room the list has beside the biggest square the
            # height allows — the square only shrinks when the list needs it.
            m = self.MARGIN
            inner_w = self._band_width()
            inner_h = max(1, self.height() - 2 * m)
            bar = self._scroll.verticalScrollBar().sizeHint().width()
            spare = bar + 2 * self._scroll.frameWidth()
            # Half the full square: the least `fit_single` will leave it.
            side_guess = 0.5 * min(inner_h, inner_w * C.SINGLE_SQUARE_FRAC)
            list_w = max(1, int(inner_w - side_guess - C.SINGLE_GAP_PX - spare))
            row_h, row_w = self._row_measures(look, list_w)
            lm = self._list.contentsMargins()
            list_h = (inner_h - 2 * self._scroll.frameWidth()
                      - (self._line_h() + self.GAP)
                      - lm.top() - lm.bottom()
                      - n * (4 + self._list.spacing()))
            size, mark, band, word = C.fit_single(
                inner_w, inner_h, list_h, spare, n, row_h, row_w)
            if size is not None:
                look["size_px"], look["mark_px"] = size, mark
            halls["band_px"], halls["word_px"] = band, word
            parked = True
        elif halls.get("fit", True) and self.height() > 40 and self.width() > 40:
            list_w = self._list_width()
            row_h, row_w = self._row_measures(look, list_w)
            size, mark, band, word = C.fit_sizes(
                self._band_width(), list_w,
                self.height() - self._overhead(n), n, row_h, row_w,
                auto=bool(halls.get("auto")),
                band_frac=halls.get("band_frac"))
            # Into the document, so the Settings tab's boxes are the truth.
            # `size` is None when there is no row to measure — then the text
            # keeps whatever it had, because an empty list says nothing about
            # how big a row should be.
            if size is not None:
                look["size_px"], look["mark_px"] = size, mark
            halls["band_px"], halls["word_px"] = band, word
            parked = True
        size, mark = int(look["size_px"]), int(look["mark_px"])
        band, word = int(halls["band_px"]), int(halls["word_px"])

        now = (size, mark, band, word, bool(halls.get("single")))
        if not force and now == getattr(self, "_fitted", None):
            return
        self._fitting = True
        try:
            self._fitted = now
            for widget in self._row_widgets:
                widget.apply_look(look)
            # The band first: `_fit_columns` measures the list's width beside
            # the one square, and the square's side is the band's wanted size.
            self._band.set_band(band, word)
            self._single.set_word_px(word)
            self._fit_columns()
            self._place_children()
        finally:
            self._fitting = False
        if parked:
            # The Settings tab's boxes follow. Through the window, and never
            # through `settings_changed` — a rebuild from in here would call
            # this tab's own `reload()`, which solves again. `getattr`, because
            # the test harness's stand-in window has no such method.
            hook = getattr(self._win, "fit_parked", None)
            if hook is not None:
                hook()

    def band_shape(self):
        """The band's real size on screen, for the Settings tab's board."""
        return self._band.width(), self._band.height()

    def _fit_columns(self):
        """One width for the names and one for the numbers, for the whole list.

        Both fixed, so the verdict squares stand in a straight line, the
        numbers all start on one, and every row is exactly as wide as the block
        — which is what lets the list be centred.
        """
        if not self._row_widgets:
            return
        self._cols_key = self._columns_key()
        look = self._win.look()
        size = int(look["size_px"])
        list_w = self._list_width()
        fm = QFontMetrics(self._base_font(look, size))
        name_w = self._name_width_for(fm, size, list_w)
        # The widest number ACTUALLY on screen and not a byte more, so the
        # numbers sit right after the names — "za GDD hned byly ty čísla",
        # 1.10.2026. It used to be at least `VALUE_FLOOR_TEXT` wide, and that
        # floor was the gap between "GDD:" and its number. The floor stays in
        # the FIT (`_widest`), so the text size still leaves room for a longer
        # reading; a number that gains a digit re-fits the columns in
        # `refresh`.
        # The units the same way: as wide as the widest unit, so they all start
        # on one line and the squares follow straight after.
        wanted = [w.value_width_wanted() for w in self._row_widgets]
        number_w = max(n for n, _u in wanted)
        unit_w = max(u for _n, u in wanted)
        for widget in self._row_widgets:
            widget.set_name_width(name_w)
            widget.set_value_width(number_w, unit_w)
        if self._is_single():
            # The list's area is cut to the rows' own width there, so wider
            # columns mean a wider area.
            self._place_single()

    def _columns_key(self):
        """What `_fit_columns` measures, reduced to what can change a width:
        every name, and every number's length and unit. Digits are all one
        width in the list's typefaces, so a reading that only changes its
        digits never needs the columns measured again."""
        out = []
        for w in self._row_widgets:
            name, value = w.texts()
            number, unit = split_value(value)
            out.append((name, len(number), unit))
        return tuple(out)

    def refresh(self):
        """A pass came back, or the beat ticked: put every picture in step."""
        engine = self._win.engine()
        shooting = self._win.shooting()
        readings = engine.readings()
        last_ok = engine.last_ok()
        screen = engine.screen()
        now = time.time()

        # ── whose limits are in force ──────────────────────────────────────
        # Normally the halls being shot into. While the experimental valves
        # cannot be read at all — not from their channels and not off the
        # L3BT MSS window either — it is Int, and `fallback` says so: that
        # hall's square goes white rather than green, because nobody is
        # shooting into it and the square must not claim otherwise.
        active, fallback = C.halls_in_force(shooting, readings, last_ok, now,
                                            screen)
        states = {}
        tips = {}
        for hall in C.HALLS:
            state, why = C.hall_state(shooting, hall, readings, last_ok, now,
                                      screen)
            states[hall] = "watch" if fallback and hall in active else state
            tips[hall] = why

        # ── which of them has something wrong ──────────────────────────────
        rows = self._win.rows()
        # Every number on the tab comes from this one call, worked out once:
        # the formulas among the rows are evaluated here and nowhere else.
        values = C.row_values(rows, readings, last_ok, now,
                              shots=shot_number(self._win))
        # The Scanning box says what the window holds — set without its
        # signal, so this is never mistaken for a click.
        scan_on = bool(getattr(self._win, "scanning", bool)())
        if self._scan_box.isChecked() != scan_on:
            self._scan_loading = True
            try:
                self._scan_box.setChecked(scan_on)
            finally:
                self._scan_loading = False
        self._style_scan_box(scan_on)
        faults = {}
        for hall in active:
            bad = C.hall_faults(rows, values, hall, scan_on)
            if bad:
                faults[hall] = bad
                states[hall] = "fault"

        # ── is anything being fired at all ─────────────────────────────────
        # The valves say where the beam WOULD go; SBW4's energy says whether
        # it does (`ls_core.firing`). Not firing: no green and no blink — the
        # square is white "idle". The rows are still judged above, so their
        # ticks and crosses are unchanged. A shot counts for
        # `C.FIRING_HOLD_S` after it arrived, then the square is idle again
        # until the next one.
        mono = time.monotonic()
        fire, fire_why = self._firing.update(readings, last_ok, now, mono)
        if not fire:
            for hall in C.HALLS:
                if states[hall] in ("shooting", "fault"):
                    states[hall] = "idle"
                    tips[hall] = f"{fire_why}\n\n{tips[hall]}"
                    faults.pop(hall, None)

        # ── the deformable mirror ──────────────────────────────────────────
        # The DM settings line, while its Show is ticked (8.10.2026): Low or
        # High power, a cross when a fresh SBW4 shot contradicts it
        # (`ls_core.dm_judge`). Its cross makes the halls in force blink AFTER
        # the not-firing gate above: a High power fault is a low-energy shot,
        # which is exactly when that gate calls the hall idle.
        dm_line = self._dm_line(rows, engine, active, now, mono)
        if dm_line is not None and dm_line[1] == C.STATE_BAD:
            for hall in active:
                states[hall] = "fault"
                faults.setdefault(hall, []).append(dm_line[3])

        # ── L3 with nowhere to go ──────────────────────────────────────────
        # SGV501 open and no hall's valve open: "waiting for hall". Only over
        # a plain idle / watching square — a fault that is blinking keeps
        # blinking, because that is the one thing that must never be hidden.
        waiting, wait_why = C.l3_waiting(shooting, readings, last_ok, now,
                                         screen)
        l3 = C.FALLBACK_HALL
        if waiting and states[l3] in ("idle", "watch"):
            states[l3] = C.STATE_WAITING
            tips[l3] = f"{wait_why}\n\n{tips[l3]}"

        # ── a scan going on ────────────────────────────────────────────────
        # Scanning ticked and at least one shown row with scan limits: a
        # square that is being shot into says so in the same word —
        # "SHOOTING + SCANNING", or "OUT OF RANGE + SCANNING" while it blinks.
        # Only while shooting.
        scanning = C.scanning_rows(rows, scan_on)
        if scanning:
            names = ", ".join(C.row_label(r) for r in scanning[:6])
            for hall in C.HALLS:
                if states[hall] in ("shooting", "fault"):
                    tips[hall] = (f"Scanning: {names} — judged against the "
                                  f"scan limits.\n\n{tips[hall]}")

        def word_for(state):
            word = _HALL_WORDS.get(state, _HALL_WORDS["unknown"])
            if scanning and state in ("shooting", "fault"):
                word += _SCAN_SUFFIX
            return word

        for hall in C.HALLS:
            square = self._squares[hall]
            state = states[hall]
            square.set_state(state)
            square.set_word(word_for(state))
            _set_tip(square, self._hall_tip(hall, state, tips[hall], faults,
                                            fallback))

        # The one square: the hall the beam is going into, which is Int when
        # it is going into none of the others. Its picture is that hall's.
        hall = C.single_hall(shooting, readings, last_ok, now, screen)
        idle_hall = hall
        state = states[hall]
        self._single.set_label(C.hall_shown(hall))
        self._single.set_state(state)
        self._single.set_word(word_for(state))
        _set_tip(self._single, self._hall_tip(hall, state, tips[hall], faults,
                                              fallback))

        # ── the shot counter's line ────────────────────────────────────────
        if self._shot_row is not None:
            number = shot_number(self._win)
            text = (f"{name_shown(C.row_label(self._shot_row))} "
                    f"{'—' if number is None else number}")
            if text != self._shot_label.text():
                self._shot_label.setText(text)
                if self._is_single():
                    self._place_single()    # its width follows the text there
            counter = shot_counter(self._win)
            _set_tip(self._shot_label, counter.sentence() if counter is not None
                     else "Counts the shots.")

        # ── the rows ───────────────────────────────────────────────────────
        rewritten = False
        for i, row in enumerate(self._rows_on_screen):
            if i >= len(self._row_widgets):
                break
            widget = self._row_widgets[i]
            value = values.get(int(row.get("id", 0)))
            name = C.row_label(row)
            if C.is_dm_row(row) and dm_line is not None:
                # A set-up name, not a number: a non-breaking space, so it is
                # not split into a number and a unit (`split_value`).
                title, state, why, _row = dm_line
                shown = title.replace(" ", " ")
                if (name, shown) != widget.texts():
                    rewritten = True
                widget.set_name(name)
                widget.set_state(state)
                widget.set_value(shown)
                widget.set_tip(
                    f"{name}\n{why}\n\nThe square:\n"
                    f"  green, tick — the set-up fits the SBW4 energy\n"
                    f"  red, cross — it does not; the hall's square blinks\n"
                    f"  grey, dash — the set-up is not known, it is ticked "
                    f"for neither\n      High nor Low power, no hall is "
                    f"shot into, or Alarm is unticked\n\n"
                    f"Double-click to rename.")
                continue
            # Grey = cannot be read; orange = read, but nothing is being shot
            # (with the tick or cross it would get); green / red = judged on
            # a shot (12.10.2026, `ls_core.row_mark`).
            state, text = C.row_mark(row, value, active, fire, idle_hall,
                                     scan_on)
            shown = C.value_text(None if value is None else value.value,
                                 row.get("unit") or "")
            was_name, was_value = widget.texts()
            if name != was_name or shown != was_value:
                rewritten = True
            widget.set_name(name)
            widget.set_state(state)
            widget.set_value(shown)
            # Everything the dropped columns used to say is here, on the whole
            # row, so it is found wherever you point.
            widget.set_tip(self._row_tip(row, rows, text,
                                         active if fire else [idle_hall],
                                         scan_on))
        if rewritten and self._columns_key() != getattr(self, "_cols_key", None):
            # Only when what the columns are measured from has changed — a
            # name, a number's LENGTH or a unit. A reading that changes its
            # digits but not their count keeps its column, which is the case
            # on almost every pass.
            #
            # A name typed on the Limits tab reaches this tab as a refresh and
            # not a reload, and a longer name needs a wider column. So does a
            # number that has gained a digit — and that is why the columns are
            # re-fitted on a NEW VALUE too: without it the number was clipped
            # by a column measured for the shorter one, and "vždy byly vidět
            # slova a hodnoty" was the whole point.
            self._fit_columns()
            # And the TEXT SIZE again when the numbers no longer match what it
            # was solved for — the fit measures the real numbers now, not a
            # worst case, so a first reading or a much longer one re-solves.
            if self._win.halls().get("fit", True) and self._value_room_changed():
                self._fit_soon()

        # ── the mark ───────────────────────────────────────────────────────
        # It means "nothing at all has arrived for five minutes". Before the
        # first pass there is no newest read to measure from, so the five
        # minutes are counted from when the tab was built — otherwise a program
        # that has never managed a single read would never raise the mark.
        newest = max(last_ok.values()) if last_ok else None
        since = newest if newest is not None else self._built_at
        silent = self._win.has_channels() and now - since > NO_DATA_S
        single = self._is_single()
        self._mark.setVisible(silent and not single)
        self._single_mark.setVisible(silent and single)

    def _dm_line(self, rows, engine, active, now, mono):
        """(text, state, sentence, row) of the DM settings line, or None when
        its Show is unticked. Judged like any row: not while no hall's limits
        are in force, and only a grey dash while its Alarm is unticked."""
        row = next((r for r in rows if C.is_dm_row(r) and C.is_shown(r)),
                   None)
        if row is None:
            return None
        get = getattr(engine, "dm", None)
        title, tag, detail = D.mirror_now(get() if get is not None else None,
                                          now)
        shutter = C.shutter_open(engine.readings(), engine.last_ok(), now)
        state, why = C.dm_judge(tag, self._firing.fresh_energy(mono), title,
                                shutter)
        if not active:
            state, why = C.STATE_UNKNOWN, f"no hall is being shot into — {why}"
        elif not C.is_alarmed(row):
            state, why = C.STATE_UNKNOWN, f"alarm off — {why}"
        return title, state, f"{detail}\n\n{why}", row

    # ── the words behind a tooltip ─────────────────────────────────────────
    def _row_tip(self, row, rows, verdict, active, scanning=False):
        """One row's whole story: what it reads, how it is doing, its limits.

        This is what the "Limits in force", "State" and "Channel or formula"
        columns used to say. The list shows a name, a square and a number and
        nothing else, so the sentences live here — findable, and not in the way.
        """
        if C.is_shot_row(row):
            counter = shot_counter(self._win)
            head = (counter.sentence() if counter is not None
                    else "Counts the shots.")
        elif C.is_formula(row):
            head = "= " + C.expr_to_display(row, rows)
        else:
            head = (row.get("pv") or "").strip() or "(no channel)"
        lines = [C.row_label(row), head, "", verdict, ""]
        scan = C.is_scanning(row, scanning)
        if C.has_scan_limits(row):
            span = C.range_text(row.get("scan_min"), row.get("scan_max"))
            mark = "  ← in force" if scan else ""
            lines.append(f"Scan, every hall:  {span}{mark}")
        for hall in C.HALLS:
            entry = C.limits_for(row, hall)
            span = C.range_text(entry.get("min"), entry.get("max"))
            mark = "  ← in force" if hall in active and not scan else ""
            lines.append(f"{hall}:  {span}{mark}")
        lines += ["", _MARK_LEGEND, "", "Double-click to rename."]
        return "\n".join(lines)

    def _hall_tip(self, hall, state, why, faults, fallback):
        """What the square says when you hover it.

        `why` is `ls_core.hall_state`'s own sentence about the gate valve — it
        already names the channel it came from — so the tooltip is that plus
        what it means for this hall.
        """
        # While the fallback is on, the hall is NOT being shot into and the
        # tooltip says so: the limits are in force, the beam is not in there.
        doing = "watching" if fallback else "shooting"
        # The square's own name (L3 for Int) heads the tooltip; the limits are
        # still the hall's, which is what the Limits tab calls it.
        name = C.hall_shown(hall)
        if state == C.STATE_WAITING:
            return f"{name} — waiting for hall.\n\n{why}"
        if state == "unset":
            return f"{name} — {why}"
        if state == "fault":
            bad = faults.get(hall, [])
            names = ", ".join(C.row_label(r) for r in bad[:6])
            if len(bad) > 6:
                names += f", and {len(bad) - 6} more"
            return f"{name} — {doing}, but out of range:\n{names}\n\n{why}"
        if state == "watch":
            return (f"{name} — nobody is shooting into it, but the other "
                    f"halls' gate valves cannot be read, so {hall}'s limits "
                    f"are the ones being judged. Everything in range."
                    f"\n\n{why}")
        if state == "shooting":
            return f"{name} — shooting, everything in range.\n\n{why}"
        return f"{name} — {why}"
