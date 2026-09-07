"""
spfe_t.py  --  the SPFE Values window.

Everything is inside one QWidget, ``SPFEValuesWidget``, and that widget has no
tab bar of its own: it is one page, so it can be dropped into another program as
a single tab with one addTab call. What used to be the History and the Log tab
are windows opened from the toolbar.

The archiver functions are reached through the adapter block below: inside CSS
Logger they come from that program's own cpva_core, here from spfe_core. Both
carry the same names, so no other line in this file has to change when it moves.

The look follows Chiller Log, the sibling program this one will eventually join:
Segoe UI 9, Consolas 9 in the log, and its four semantic colours -- blue for
"working on it", green for done, red for wrong, grey for neutral. Colours are
set explicitly everywhere. Nothing is left to the theme, the style or the OS --
an inherited palette is how a table ends up with black text on a dark red row.
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from typing import NamedTuple

from PySide6.QtCore import (
    QDate, QEvent, QObject, QRegularExpression, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath,
    QPalette, QPen, QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from spfe_sections import CollapsibleSection

import spfe_limits as limits
import spfe_record as recorder
import spfe_store as store
from spfe_store import (
    SLOT_EVENING, SLOT_MORNING, SLOT_NOW, Record, Store, load_config,
    load_fields, scheduled_slots, catch_up_start,
)

try:                                   # inside CSS Logger
    from cpva_core import TZ_PRAGUE
except ImportError:                    # standalone
    from spfe_core import TZ_PRAGUE


def _import_daypicker():
    """Load the shared day picker from the sibling daypicker.py, by path.

    The same rule as in Image Tools and CSS Logger: the builder passes only the
    program's own folder to PyInstaller, so the file has to sit here and be
    loaded from here -- a plain `import daypicker` would find whichever copy
    happens to be on the path. Registered in sys.modules BEFORE it is executed,
    so a re-entrant import cannot run the module twice.
    """
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _Path

    mod = _sys.modules.get("daypicker")
    if mod is not None:
        return mod
    p = _Path(__file__).resolve().parent / "daypicker.py"
    spec = _ilu.spec_from_file_location("daypicker", p)
    mod = _ilu.module_from_spec(spec)
    _sys.modules["daypicker"] = mod
    spec.loader.exec_module(mod)
    return mod


daypicker = _import_daypicker()


# ── Colours ──────────────────────────────────────────────────────────────────
# Chiller Log's palette (its FONT/COLOR constants) plus the neutrals of the
# house Qt table style, so the two programs read as one when they are joined.
INK          = "#111111"
INK_MUTED    = "#666666"
PAPER        = "#FFFFFF"
GROUND       = "#F5F5F5"       # the ground the panels sit on
BAND         = "#E0E0E0"       # header sections
GROUP_BAND   = "#E8E8E8"       # the group column: a band, so it has to be
                               # darker than the paper beside it
DAY_BAND     = "#C9C9C9"       # the day: a step darker than the table header,
                               # so the line that starts a day is the heavier
                               # one. Matches the workbook's own day fill.
LINE         = "#D0D0D0"       # grid lines
LINE_DARK    = "#9A9A9A"       # under the header, and the panel borders
FLAG_BG      = "#FFE0B2"       # light amber; red ink stays readable on it
FLAG_INK     = "#E53935"       # Chiller Log marks a bad row in this red
# The two reference colours. A background is never chosen without its ink:
# * amber-yellow is a light fill, so it keeps BLACK text, bold;
# * red is a dark fill, so the number turns white -- and gets a black outline
#   drawn round it by the delegate, because white on this red is exactly the
#   pair that goes grey on a projector and on a printout.
REF_WARN_BG  = "#FFEB3B"
REF_WARN_INK = "#000000"
REF_BAD_BG   = "#C62828"
REF_BAD_INK  = "#FFFFFF"
REF_BAD_EDGE = "#000000"
# The cells the paper form expects somebody to fill in. Every cell can be
# typed into, so this is not "editable" -- it is "this one is yours". A light
# tint, therefore black ink, like every other light cell here.
TYPED_BG     = "#FFF8E1"
# Chiller Log's green is #4CAF50, which is a fill colour: as text on the light
# ground it is too pale to read, so the status line uses a darker one.
OK_GREEN     = "#2E7D32"
WARN_RED     = "#E53935"
BLUE         = "#1976D2"
LINK_BLUE    = "#1976D2"

FONT_FAMILY  = "Segoe UI"
FONT_PT      = 9
# The numbers are what the table is for, so they are a point larger than the
# page -- but only a point. At 11 pt in a 165 px column the table was half a
# metre of white with a four-digit number in the middle of it.
VALUE_PT     = 10
FONT_MONO    = "Consolas"

# ── how wide the table is allowed to get ─────────────────────────────────────
# Every column is measured from what is actually in it and then held between
# these two numbers. Nothing stretches to fill the window: a stretched table is
# how the Detail column ended up 10 cm wide around a 1 cm label. What is left
# over stays the page's own grey, so the table visibly ENDS where its content
# does, and the window can simply be made narrower.
# One NUMBER's cell. X, Y and SUM stand side by side inside the moment they
# were recorded in, so a value column is now a narrow box holding one number --
# 13900 is the widest thing that ever goes in one.
VALUE_COL_MIN = 54
VALUE_COL_MAX = 110
_GROUP_MIN    = 62
_GROUP_MAX    = 120
_DETAIL_MIN   = 150
_DETAIL_MAX   = 300            # past this a long label wraps instead
# The References window keeps a column of its own for the name of a number; the
# day table says it in the heading instead.
_PART_MIN     = 34
_PART_MAX     = 96
# The padding a cell actually loses: 6 px each side from the stylesheet, plus
# what Qt keeps for itself around an item. Measured, not guessed -- at 18 the
# word 'intensity' came out as 'intens...' in a column supposedly wide enough
# for it.
_CELL_PAD     = 24
# A row is as tall as its text needs and no taller. The leftover height is
# shared out as before, but a row may not more than half again its own size --
# sixteen short rows in a tall window used to become sixteen tall empty boxes.
_ROW_MIN      = 22
_ROW_GROW_MAX = 1.5

# The first table column that holds a recorded value. Two label columns come
# first: the group and the quantity. Which number of the quantity a cell is
# (X, Y, SUM) is said by the heading above it, not by a column of its own --
# the numbers stand side by side, the way the paper form has always had them.
_FIRST_VALUE = 2

# A column that the table shows but nobody has recorded yet: it must never be
# handed to the store as if it existed. Typing into one creates it.
PENDING = "pending"

# What each value cell remembers about itself. Carried on the item rather than
# worked out from its row and column number: the table is not always the shape
# the code assumes (an empty day has one placeholder column), and the text the
# cell was FILLED with is the only way to tell a real change from a commit that
# changed nothing.
_ROLE_KEY   = Qt.ItemDataRole.UserRole + 1      # which quantity
_ROLE_STAMP = Qt.ItemDataRole.UserRole + 2      # which recorded moment
_ROLE_TEXT  = Qt.ItemDataRole.UserRole + 3      # the text as it was filled in
_ROLE_HELD  = Qt.ItemDataRole.UserRole + 4      # showing a default, not a value
_ROLE_COL   = Qt.ItemDataRole.UserRole + 5      # WHICH NUMBER of the quantity
_ROLE_LEVEL = Qt.ItemDataRole.UserRole + 6      # "", "warn" or "bad"

# The buttons are sized to their text: a small label inside a tall bar reads as
# an empty button, so the padding is tight and the text one point larger than
# the page.
_BTN = (
    f"QPushButton{{background:#F0F0F0;color:{INK};border:1px solid #ADADAD;"
    "border-radius:2px;padding:3px 8px;font-size:10pt;min-height:19px;}"
    "QPushButton:hover{background:#E5F1FB;border-color:#0078D7;}"
    "QPushButton:pressed{background:#CCE4F7;border-color:#005499;}"
    "QPushButton:disabled{background:#F0F0F0;color:#9A9A9A;border-color:#CCCCCC;}"
)
# The pair that folds every section at once. Deliberately smaller than a
# working button: they arrange the panel, they do not do anything to the data.
_BTN_SMALL = (
    f"QPushButton{{background:#EDEDED;color:{INK_MUTED};border:1px solid #C4C4C4;"
    "border-radius:2px;padding:2px 6px;font-size:9pt;min-height:16px;}"
    f"QPushButton:hover{{background:#E5F1FB;border-color:#0078D7;color:{INK};}}"
    "QPushButton:pressed{background:#CCE4F7;border-color:#005499;}"
)
_BTN_PRIMARY = (
    f"QPushButton{{background:{BLUE};color:#FFFFFF;border:1px solid #12599C;"
    "border-radius:2px;padding:5px 10px;font-size:11pt;font-weight:700;}"
    "QPushButton:hover{background:#1E88E5;}"
    "QPushButton:pressed{background:#145EA8;}"
    "QPushButton:disabled{background:#BFD4E8;color:#F0F0F0;border-color:#9AA9BA;}"
)
_TABLE = (
    f"QTableWidget{{background:{PAPER};color:{INK};gridline-color:{LINE};"
    f"border:1px solid {LINE_DARK};"
    f"selection-background-color:#CFE3FF;selection-color:{INK};}}"
    # Nothing is glued to a grid line: every cell keeps a margin of its own.
    "QTableWidget::item{padding:2px 6px;}"
    f"QHeaderView::section{{background:{BAND};color:{INK};font-weight:700;"
    f"padding:5px 8px;border:none;border-bottom:1px solid {LINE_DARK};"
    "border-right:1px solid #CCCCCC;}"
    f"QTableCornerButton::section{{background:{BAND};border:none;}}"
)
# The band the date and the campaign stand in, over the table: a shade darker
# than the table's own header, so it reads as the line that starts the day
# rather than another header row, with enough padding that the date is not
# sitting on the border.
#
# The selector is the container widget, not a QLabel: a plain QWidget ignores a
# background in a stylesheet unless it is told to draw one, which is what
# _styled_ground does.
_DAY_BAND = (
    f"QWidget{{background:{DAY_BAND};color:{INK};border:1px solid {LINE_DARK};"
    "border-bottom:none;border-top-left-radius:2px;border-top-right-radius:2px;}"
    # Everything inside sits on the band and must not draw a box of its own.
    "QLabel{border:none;background:transparent;}"
)
_CAMPAIGN_EDIT = (
    f"QLineEdit{{background:{PAPER};color:{INK};border:1px solid {LINE_DARK};"
    "border-radius:2px;padding:2px 6px;font-size:10pt;}"
    f"QLineEdit:focus{{border:1px solid {BLUE};}}"
)
_LOG_STYLE = (
    "QPlainTextEdit{background:#1E1E1E;color:#D4D4D4;border:1px solid #444444;"
    f"font-family:{FONT_MONO},'Courier New',monospace;font-size:9pt;}}"
    # The console is the one thing that is meant to be dark, so its scroll bar
    # is dark too instead of taking the light one from the page.
    "QScrollBar:vertical{background:#2A2A2A;width:14px;border:1px solid #3C3C3C;}"
    "QScrollBar::handle{background:#5A5A5A;border-radius:3px;min-height:24px;"
    "margin:2px;}"
    "QScrollBar::handle:hover{background:#767676;}"
    "QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;}"
    "QScrollBar::add-page,QScrollBar::sub-page{background:#2A2A2A;}"
)

# Everything the theme would otherwise paint. Windows here is set to dark mode,
# and any widget left to the theme comes out black -- including the plain
# QWidget the whole page sits on, the scroll bars and the tooltips. The page
# also has to carry WA_StyledBackground: a plain QWidget ignores a background
# in a stylesheet unless it is told to draw one (_styled_ground below).
_GROUND_STYLE = (
    f"QWidget{{background:{GROUND};color:{INK};}}"
    f"QToolTip{{background:#FFFFE1;color:{INK};border:1px solid {LINE_DARK};"
    "padding:3px;}"
    # A fallback for buttons nobody styled by hand -- the ones a message box
    # makes for itself.
    f"QPushButton{{background:#F0F0F0;color:{INK};border:1px solid #ADADAD;"
    "border-radius:2px;padding:5px 10px;}"
    "QPushButton:hover{background:#E5F1FB;border-color:#0078D7;}"
    f"QScrollBar:vertical{{background:#F0F0F0;width:14px;margin:0;"
    "border:1px solid #DADADA;}"
    "QScrollBar:horizontal{background:#F0F0F0;height:14px;margin:0;"
    "border:1px solid #DADADA;}"
    "QScrollBar::handle{background:#C4C4C4;border-radius:3px;min-height:24px;"
    "min-width:24px;margin:2px;}"
    "QScrollBar::handle:hover{background:#A6A6A6;}"
    "QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;}"
    "QScrollBar::add-page,QScrollBar::sub-page{background:#F0F0F0;}"
)


def _styled_ground(widget):
    """Make a plain QWidget actually paint the background its stylesheet asks
    for. Without this the theme paints it, which under Windows dark mode is
    black -- the frame around the page, and the strip under the toolbar."""
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


def _light_palette(pal: QPalette) -> QPalette:
    """A light palette on the page, inherited by every child.

    The stylesheet cannot reach everything a widget draws -- a message box, a
    combo popup, the text cursor. Setting the palette as well means that
    whatever the stylesheet misses is still light rather than the theme's black.
    """
    for role, colour in (
            (QPalette.ColorRole.Window, GROUND),
            (QPalette.ColorRole.WindowText, INK),
            (QPalette.ColorRole.Base, PAPER),
            (QPalette.ColorRole.AlternateBase, "#F4F4F4"),
            (QPalette.ColorRole.Text, INK),
            (QPalette.ColorRole.Button, "#F0F0F0"),
            (QPalette.ColorRole.ButtonText, INK),
            (QPalette.ColorRole.ToolTipBase, "#FFFFE1"),
            (QPalette.ColorRole.ToolTipText, INK),
            (QPalette.ColorRole.Highlight, "#CFE3FF"),
            (QPalette.ColorRole.HighlightedText, INK),
            (QPalette.ColorRole.PlaceholderText, INK_MUTED),
    ):
        pal.setColor(QPalette.ColorGroup.Active, role, QColor(colour))
        pal.setColor(QPalette.ColorGroup.Inactive, role, QColor(colour))
    pal.setColor(QPalette.ColorGroup.Disabled,
                 QPalette.ColorRole.Text, QColor("#9A9A9A"))
    pal.setColor(QPalette.ColorGroup.Disabled,
                 QPalette.ColorRole.ButtonText, QColor("#9A9A9A"))
    pal.setColor(QPalette.ColorGroup.Disabled,
                 QPalette.ColorRole.WindowText, QColor("#9A9A9A"))
    return pal


class _Line(NamedTuple):
    """ONE NUMBER of the table: not one row of it, and not one quantity.

    A quantity like "Input - BA2Loop2 (X,Y,SUM)" was once a single cell holding
    "150;-250;2500" -- unreadable, and the only way to correct Y was to retype
    all three. Every number is its own cell now, and this is what one of them
    knows about itself. Where the cell goes is a separate question: the three
    numbers stand SIDE BY SIDE on one line of the table, under the moment they
    were recorded in, which is the shape of the paper form they come from.
    """
    row: object            # store.Row -- the quantity this number belongs to
    column: str            # its CSV column: 'ba2loop2_2'
    part: str              # 'Y', or '' when the quantity is a single number
    index: int             # which number of the quantity, from 0
    count: int             # how many numbers the quantity has

    @property
    def first(self) -> bool:
        return self.index == 0


class _HeaderBand(QWidget):
    """The upper half of the table's heading: the name of each moment.

    It is a widget of its own rather than a two-level QHeaderView because
    QHeaderView paints one section at a time, clipped to that section: a name
    that has to stand across three sub-columns simply gets cut off. Here the
    whole strip is painted at once, so "Morning (09:00)" is centred over its
    own three numbers.

    It never drifts from the table under it: the widths come from the table
    itself, after `_fit_columns` has set them, and the strip is drawn shifted
    by the table's own horizontal scroll.
    """

    menu_wanted = Signal(int, object)      # (grid column, position)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._table: QTableWidget | None = None
        self._spans: list[tuple[int, int, str]] = []   # first col, width, text
        self.setFixedHeight(26)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_menu)

    def bind(self, table: QTableWidget) -> None:
        self._table = table
        # The table carries its own band, so the code that fills a table does
        # not have to know WHICH table it is filling: the day page and the View
        # day window are the same two tables through the same methods.
        table._spfe_band = self
        table.horizontalScrollBar().valueChanged.connect(
            lambda _=0: self.update())

    def set_spans(self, spans: list[tuple[int, int, str]]) -> None:
        """(first grid column, how many columns, the heading) per moment."""
        self._spans = list(spans)
        self.update()

    def _offset(self) -> int:
        """Where grid column 0 starts, in this widget's own coordinates."""
        if self._table is None:
            return 0
        return (self._table.frameWidth()
                - self._table.horizontalScrollBar().value())

    def _rect_of(self, first: int, count: int) -> tuple[int, int]:
        """(left, width) of a run of grid columns, in widget coordinates."""
        table = self._table
        left = self._offset()
        for c in range(first):
            left += table.columnWidth(c)
        width = sum(table.columnWidth(first + i) for i in range(count))
        return left, width

    def column_at(self, x: int) -> int:
        """Which grid column the point is over, -1 for none."""
        if self._table is None:
            return -1
        left = self._offset()
        for c in range(self._table.columnCount()):
            w = self._table.columnWidth(c)
            if left <= x < left + w:
                return c
            left += w
        return -1

    def _on_menu(self, pos):
        col = self.column_at(pos.x())
        if col >= 0:
            self.menu_wanted.emit(col, self.mapToGlobal(pos))

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        # The whole strip first: the part that no moment covers is the same
        # band colour, so the heading reads as one bar rather than as boxes
        # floating on the page. Nothing is left to the theme -- Windows here is
        # in dark mode and an unpainted widget comes out black.
        painter.fillRect(rect, QColor(BAND))
        painter.fillRect(rect.left(), rect.bottom() - 1, rect.width(), 2,
                         QColor(LINE_DARK))
        if self._table is None or not self._spans:
            painter.end()
            return

        font = QFont(FONT_FAMILY, FONT_PT)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        for first, count, text in self._spans:
            left, width = self._rect_of(first, count)
            if width <= 0:
                continue
            # A hairline between one moment and the next, so two moments do not
            # read as one wide heading.
            painter.fillRect(left - 1, rect.top() + 4, 1, rect.height() - 8,
                             QColor("#CCCCCC"))
            painter.setPen(QColor(INK))
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                       max(10, width - 10))
            painter.drawText(left, rect.top(), width, rect.height() - 2,
                             int(Qt.AlignmentFlag.AlignCenter), shown)
        painter.end()


class _CellDelegate(QStyledItemDelegate):
    """Every cell of the table goes through here, for two reasons.

    The first is the line that separates one group of quantities from the next.
    The grid alone draws every row the same, so Dazzlers and XPW run into each
    other; a group boundary is a dark rule across the whole width, which is
    what makes the table readable as blocks.

    The second is the editor of a number: a plain box that takes nothing but
    the numbers of that row. The unit stands in the Detail column, never in the
    cell, so there is nothing in the box but the numbers themselves. Both live
    in one class because a row delegate replaces the table's, so a number row
    would otherwise lose its line.

    The third is a value that is outside the range somebody set for it: red,
    with the number in white and a black outline round it. An item can only be
    given one ink, and plain white on that red is the pair that goes grey on a
    projector and on a printout.

    ``boundaries`` is the set the table fills in, shared by both instances.
    """

    def __init__(self, parent=None, *, numeric: bool = False,
                 boundaries: set | None = None):
        super().__init__(parent)
        self._numeric = numeric
        self.boundaries = boundaries if boundaries is not None else set()
        # The editor that is open, if any. Qt destroys an editor without
        # committing it whenever the table is rebuilt, so leaving a day with a
        # half-typed cell would throw the value away.
        self._open = None

    def paint(self, painter, option, index):
        if index.data(_ROLE_LEVEL) == "bad":
            self._paint_out_of_range(painter, option, index)
        else:
            super().paint(painter, option, index)
        if index.row() in self.boundaries and index.row() > 0:
            r = option.rect
            painter.save()
            painter.fillRect(r.left(), r.top(), r.width(), 2,
                             QColor(LINE_DARK))
            painter.restore()

    def _paint_out_of_range(self, painter, option, index) -> None:
        """A value outside its reference: white on red, outlined in black.

        Painted here rather than left to the item's own colours, because a
        QTableWidgetItem can only be given ONE ink -- and plain white on this
        red is the pair that turns into an unreadable grey smudge on a
        projector, on a printout and on a screen somebody has turned down. The
        outline is a black stroke round the glyphs, so the number stays a
        number whatever the red does.

        The rectangle is filled by hand as well: the item's own background is
        drawn by the base class, which is not being called.
        """
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(rect, QColor(REF_BAD_BG))

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if text:
            font = index.data(Qt.ItemDataRole.FontRole) or option.font
            font = QFont(font)
            font.setBold(True)
            metrics = QFontMetrics(font)
            inner = rect.adjusted(6, 2, -6, -2)
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                       inner.width())
            x = inner.left() + max(0, (inner.width()
                                       - metrics.horizontalAdvance(shown)) / 2)
            y = inner.top() + (inner.height() + metrics.capHeight()) / 2

            path = QPainterPath()
            path.addText(x, y, font, shown)
            # The outline goes down FIRST and the white fill over it. Drawing
            # both at once centres the stroke on the edge of every glyph, so
            # half of it lands inside the letter -- at this size that is the
            # whole letter, and the number came out a black smudge.
            pen = QPen(QColor(REF_BAD_EDGE))
            pen.setWidthF(1.6)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
            painter.fillPath(path, QColor(REF_BAD_INK))

        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor("#FFFFFF"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.restore()

    def createEditor(self, parent, option, index):
        if not self._numeric:
            return super().createEditor(parent, option, index)
        edit = QLineEdit(parent)
        edit.setValidator(self._validator(edit, index))
        edit.setStyleSheet(
            f"QLineEdit{{background:{PAPER};color:{INK};border:1px solid {BLUE};"
            f"padding:1px 3px;font-size:{VALUE_PT}pt;}}")
        self._open = edit
        return edit

    def destroyEditor(self, editor, index):
        if editor is self._open:
            self._open = None
        super().destroyEditor(editor, index)
        # A rebuild that was asked for while this cell was open can happen now.
        closed = getattr(self.parent(), "_editor_closed", None)
        if callable(closed):
            closed()

    def commit_open_editor(self) -> None:
        """Write an editor that is still open into the cell.

        The only way to get a value out of a QTableWidget editor without the
        user pressing Enter. Called before the table is rebuilt and when the
        window closes -- otherwise Qt simply deletes the editor and the value
        with it.
        """
        edit = self._open
        if edit is None:
            return
        self._open = None
        try:
            self.commitData.emit(edit)
            self.closeEditor.emit(edit, QStyledItemDelegate.EndEditHint.NoHint)
        except RuntimeError:
            pass                       # the editor was already gone

    def _validator(self, parent, index):
        """A box that takes one number, and nothing else.

        One number, because every number of a quantity now has a line of its
        own: X, Y and SUM are three cells, each typed into on its own. What the
        pattern keeps:

        * a full stop is the decimal point, never a comma -- the CSV has to
          stay something that can be added up;
        * `nan`, `inf` and `1e5` are refused, all of which float() would take
          and which would poison the statistics for ever;
        * an EMPTY box is acceptable, so a value can be cleared. With
          QDoubleValidator it could not: an empty box is "not finished yet", so
          Enter was refused and clicking away threw the edit away silently.
        """
        num = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)?"
        v = QRegularExpressionValidator(QRegularExpression(f"^{num}$"), parent)
        return v


def _pretty_day(iso: str) -> str:
    """2026-09-01 -> 01.09.2026 (Tue). The stored form is never shown as it is:
    nobody reads a day list in the machine's order of the numbers."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return iso
    return f"{d:%d.%m.%Y} ({d:%a})"


def _open_path(path_str: str) -> None:
    """Open a folder in the file manager. Raises, so the window can say why.

    The same three-line shape as CSS Logger's own _open_path: os.startfile is
    the one that works with a UNC path on Windows.
    """
    import subprocess
    import sys as _sys
    if _sys.platform.startswith("win"):
        os.startfile(path_str)                       # noqa: S606
    elif _sys.platform == "darwin":
        subprocess.Popen(["open", path_str])
    else:
        subprocess.Popen(["xdg-open", path_str])


def _clamp(value: int, low: int, high: int) -> int:
    """A measured width held between a floor and a ceiling."""
    return max(low, min(high, int(value)))


def _elide_middle(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    keep = width - 3
    head = keep // 2
    return f"{text[:head]}...{text[-(keep - head):]}"


class _Bridge(QObject):
    """One signal carrier for the whole widget.

    Deliberately created once and reused by every background job. A fresh
    QObject per worker, parented to the widget, is never destroyed and leaks a
    few kilobytes each time -- which adds up to hundreds of megabytes of commit
    over a week in a program that is left open.
    """
    log = Signal(str)
    status = Signal(str, str)          # message, colour
    finished = Signal(object)          # a callable to run on the UI thread
    busy = Signal(bool)


class SPFEValuesWidget(QWidget):
    """The whole program. Also the future 'SPFE values' tab of CSS Logger."""

    def __init__(self, parent=None):
        super().__init__(parent)
        _styled_ground(self)
        self.setStyleSheet(_GROUND_STYLE)
        self.setPalette(_light_palette(self.palette()))
        base = QFont(FONT_FAMILY, FONT_PT)
        self.setFont(base)

        self.cfg = load_config()
        try:
            self.fields = load_fields()
            self._fields_error = ""
        except Exception as exc:
            self.fields = store.Fields([])
            self._fields_error = str(exc)

        self.store = Store(self.cfg, self.fields)
        self._rows: list[dict] = []
        self._day = datetime.now(TZ_PRAGUE).date()
        self._columns: list[Record] = []          # the visible day's columns
        self._flags: dict[str, dict[str, str]] = {}   # stamp -> {number: reason}
        # The ranges somebody said each number has to be in, and how close to
        # an edge still counts as "nearly out". Read from the files on start-up
        # and again whenever the References window writes them; kept in memory
        # because every cell of every repaint asks for them.
        self._refs: dict = {}
        self._warn_pct = float(self.cfg.get("warn_pct", 5.0))
        self._refs_window = None
        self._working = False
        self._last_slot_check = ""

        # Built before the window is: the log collects messages from the very
        # first second, whether or not its window has ever been opened.
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet(_LOG_STYLE)
        self._log_window = None
        self._day_window = None
        # The rows that start a group. Filled in when the table is built and
        # shared by both delegates, which draw the line above such a row.
        self._group_starts: set[int] = set()
        self._plain_delegate = _CellDelegate(
            self, boundaries=self._group_starts)
        self._num_delegate = _CellDelegate(
            self, numeric=True, boundaries=self._group_starts)

        # Typed values save themselves. What that needs:
        #   _fill_depth   a COUNTER, not a flag: filling the table writes every
        #                 cell, and a nested fill must not un-guard the outer
        #                 one.
        #   _pending      {stamp: {column: text}} waiting to be written.
        #   _save_timer   one write for a whole row of cells tabbed through,
        #                 instead of one per cell -- each write rewrites a file
        #                 on the share.
        #   _saving       a single writer thread; two rewrites of one file race
        #                 by construction.
        #   _refresh_wanted  a rebuild asked for while a cell was open. A
        #                 rebuild destroys the editor without committing it.
        self._fill_depth = 0
        self._pending: dict[str, dict[str, str]] = {}
        self._pending_lock = threading.Lock()
        self._saving = False
        self._refresh_wanted = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._flush_pending)
        self._campaign_shown = ""

        # The folding sections of the toolbar, and which of them were left
        # open. Read before the panel is built, or every section would open on
        # every run.
        self._sections: dict = {}
        self._ui_state: dict = self._load_ui_state()

        self._bridge = _Bridge()
        self._bridge.log.connect(self._append_log)
        self._bridge.status.connect(self._set_status)
        self._bridge.finished.connect(lambda fn: fn())
        self._bridge.busy.connect(self._set_busy)
        self.store.set_log(self._bridge.log.emit)

        self._build_ui()

        if self._fields_error:
            self._set_status(f"spfe_fields.json: {self._fields_error}", WARN_RED)
        else:
            self._append_log("SPFE Values started.")

        # The slot watcher only matters while the program happens to be open;
        # a missed slot is filled in by the catch-up, so nothing depends on it.
        self._slot_timer = QTimer(self)
        self._slot_timer.setInterval(60_000)
        self._slot_timer.timeout.connect(self._check_slot_due)
        self._slot_timer.start()

        QTimer.singleShot(0, self._start_up)

    # ── construction ─────────────────────────────────────────────────────
    def _build_ui(self):
        """One page: the toolbar on the left, the day on the right.

        No tab bar of its own -- a tab bar inside a tab bar is what this widget
        has to avoid, because it is going to be one tab of the joined program.
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"QSplitter::handle{{background:{LINE};}}")
        split.addWidget(self._build_controls())
        split.addWidget(self._build_day_page())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        # Neither half may be dragged away, and the toolbar keeps its width:
        # squeezed narrower, the buttons lose their labels and become blank bars.
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        split.setSizes([290, 1010])
        outer.addWidget(split, 1)

        self.lbl_status = QLabel("Starting...")
        self.lbl_status.setStyleSheet(f"color:{INK_MUTED};padding:3px 2px;")
        self.lbl_status.setWordWrap(True)
        outer.addWidget(self.lbl_status)

    def _build_day_page(self) -> QWidget:
        page = _styled_ground(QWidget())
        lv = QVBoxLayout(page)
        lv.setContentsMargins(0, 0, 0, 0)
        # No gap: the date band is the top edge of the table, not a line
        # floating above it.
        lv.setSpacing(0)

        # The heading stays over the table even though the buttons moved: it
        # names the day that is on screen, which the buttons do not. It sits in
        # a band of its own, with room around the date -- against the bare page
        # the date looked dropped on the table's corner. The campaign shares
        # the band, because it names the same day.
        band = _styled_ground(QWidget())
        band.setStyleSheet(_DAY_BAND)
        # As tall as the date and no taller. The table below it is capped at
        # its own last row, so whatever height is left over has to be allowed
        # to stay UNUSED at the foot of the page -- given to this band instead
        # it became a hand's breadth of bare grey above the table.
        band.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)
        bl = QHBoxLayout(band)
        bl.setContentsMargins(14, 6, 14, 6)
        bl.setSpacing(8)

        self.lbl_day = QLabel()
        f = QFont(FONT_FAMILY, 13)
        f.setBold(True)
        self.lbl_day.setFont(f)
        self.lbl_day.setSizePolicy(QSizePolicy.Policy.Fixed,
                                   QSizePolicy.Policy.Preferred)
        bl.addWidget(self.lbl_day)
        bl.addSpacing(18)

        lbl_camp = QLabel("Campaign")
        lbl_camp.setStyleSheet(f"color:{INK};font-weight:700;")
        lbl_camp.setSizePolicy(QSizePolicy.Policy.Fixed,
                               QSizePolicy.Policy.Preferred)
        bl.addWidget(lbl_camp)
        self.ed_campaign = QLineEdit()
        self.ed_campaign.setStyleSheet(_CAMPAIGN_EDIT)
        # A campaign name runs to sixty characters -- "77 Borghesi / E5
        # ELI70157 Spadova" is one of the short ones. The box takes whatever
        # width the band has left instead of stopping at a third of a name;
        # there is no stretch beside it any more, so the free space is its own.
        self.ed_campaign.setMinimumWidth(360)
        self.ed_campaign.setMaximumWidth(720)
        self.ed_campaign.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)
        self.ed_campaign.setToolTip(
            "What this day is working on. It carries on to the following days\n"
            "until somebody types something else, and it stands beside the date\n"
            "in the workbook.")
        # editingFinished, never textChanged: one save per name, not one per
        # keystroke -- each save writes the share.
        self.ed_campaign.editingFinished.connect(self._on_campaign_typed)
        bl.addWidget(self.ed_campaign, 1)
        lv.addWidget(band)

        # The upper half of the table's heading: which moment each group of
        # sub-columns belongs to. See _HeaderBand for why it is not part of the
        # table's own header.
        self.band_head = _HeaderBand()
        lv.addWidget(self.band_head)

        self.table = QTableWidget(0, 2)
        self.table.setStyleSheet(_TABLE)
        # Per PIXEL, not per column. Qt's default counts the scrollbar in
        # WHOLE COLUMNS, and the heading strip above reads that number as a
        # number of pixels: scrolled right, the moment names stayed where they
        # were and stood over the wrong numbers.
        self.table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.itemChanged.connect(self._on_cell_edited)
        self.table.cellClicked.connect(self._on_cell_clicked)
        # Right-click a recorded extra column to delete it.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_menu)
        hh = self.table.horizontalHeader()
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._on_header_menu)
        # Every time the frame changes size the notes row is re-measured, so
        # the table always reaches the bottom of its frame.
        self.table.viewport().installEventFilter(self)
        # A stretch far larger than the spacer's below: the table is served
        # first and takes everything up to its own cap, and only what it cannot
        # use goes to the spacer. Sharing the height evenly with the spacer cut
        # the last two rows off the bottom of the table.
        lv.addWidget(self.table, 1000)
        # The height the table does not need. Without a spacer to take it,
        # QVBoxLayout shares the slack out BETWEEN the widgets -- the date
        # band, the heading strip and the table each floated in a band of bare
        # grey. The table is capped at its own last row on purpose; the room
        # left over belongs at the bottom of the page.
        lv.addStretch(1)
        self.band_head.bind(self.table)
        self.band_head.menu_wanted.connect(self._column_menu)
        return page

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            ref = getattr(self, "ref_table", None)
            if ref is not None and obj is ref.viewport():
                self._fit_ref_columns()
                return super().eventFilter(obj, event)
            for table in (getattr(self, "table", None),
                          getattr(self, "hist_table", None)):
                if table is not None and obj is table.viewport():
                    self._fit_columns(table)
                    self._measure_rows(table)
                    self._fit_rows(table)
        return super().eventFilter(obj, event)

    def _build_controls(self) -> QWidget:
        """The toolbar: one big button, then a coloured section per job.

        The same shape the rest of the suite uses -- Image Tools' collapsible
        sections, colour-coded, in a scroll area -- so a person moving between
        the programs is looking at the same panel. Each section can be folded
        away, and which ones are open is remembered between runs.
        """
        outer = _styled_ground(QWidget())
        outer.setMinimumWidth(272)
        outer.setMaximumWidth(348)
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 6, 0)
        ov.setSpacing(6)

        self.btn_now = QPushButton("Record now")
        self.btn_now.setStyleSheet(_BTN_PRIMARY)
        self.btn_now.setToolTip(
            "Read every PV at this moment and add a column headed with the time.\n"
            "Works at any time of day - that is the point of it.")
        self.btn_now.clicked.connect(self._on_record_now)
        ov.addWidget(self.btn_now)

        fold_row = QHBoxLayout()
        fold_row.setSpacing(4)
        for text, want in (("Expand all", True), ("Collapse all", False)):
            b = QPushButton(text)
            b.setStyleSheet(_BTN_SMALL)
            b.clicked.connect(lambda _=False, e=want: self._set_all_sections(e))
            fold_row.addWidget(b)
        ov.addLayout(fold_row)

        # The sections themselves scroll: folded open they are taller than a
        # short window, and a panel that cannot scroll simply squashes them.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}"
            f"QScrollBar:vertical{{background:{GROUND};width:9px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:#B8B8B8;min-height:26px;"
            "border-radius:4px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
            "QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}")
        panel = _styled_ground(QWidget())
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(0)

        def section(key: str, title: str, accent: str,
                    expanded: bool = True) -> CollapsibleSection:
            sec = CollapsibleSection(
                title, key, bool(self._ui_state.get(key, expanded)),
                accent=accent)
            sec.toggled.connect(self._on_section_toggled)
            self._sections[key] = sec
            v.addWidget(sec)
            return sec

        # ── the two columns of every day ─────────────────────────────────
        sec_slots = section("slots", "Scheduled columns", "#2f6fd0")
        slot_row = QHBoxLayout()
        slot_row.setSpacing(5)
        self.btn_morning = QPushButton("Morning")
        self.btn_evening = QPushButton("At the end")
        for b, slot in ((self.btn_morning, SLOT_MORNING),
                        (self.btn_evening, SLOT_EVENING)):
            b.setStyleSheet(_BTN)
            b.setToolTip(
                f"Record the {b.text().lower()} column of the day on screen -- "
                f"the values as they stood at {self._slot_time(slot)}.")
            b.clicked.connect(lambda _=False, s=slot: self._on_record_slot(s))
            slot_row.addWidget(b)
        sec_slots.body_layout.addLayout(slot_row)
        self.btn_update = QPushButton("Fill missing days")
        self.btn_update.setStyleSheet(_BTN)
        self.btn_update.setToolTip(
            "Ask the archiver what every missing weekday slot read, back to the\n"
            "last recorded day. Nothing has to have been running at the time.")
        self.btn_update.clicked.connect(self._on_update)
        sec_slots.body_layout.addWidget(self.btn_update)

        # ── which day is on screen ───────────────────────────────────────
        sec_day = section("day", "Day", "#2e9e5b")
        # Two short buttons share a line. A button per line, each one as wide as
        # the panel, wastes the height and makes every button look the same size
        # as every other -- the pairs here belong together anyway.
        step_row = QHBoxLayout()
        step_row.setSpacing(5)
        for text, step in (("< Previous", -1), ("Next >", 1)):
            b = QPushButton(text)
            b.setStyleSheet(_BTN)
            b.clicked.connect(lambda _=False, d=step: self._step_day(d))
            step_row.addWidget(b)
        sec_day.body_layout.addLayout(step_row)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(5)
        btn_today = QPushButton("Today")
        btn_today.setStyleSheet(_BTN)
        btn_today.clicked.connect(self._go_today)
        pick_row.addWidget(btn_today)
        btn_view = QPushButton("View day")
        btn_view.setStyleSheet(_BTN)
        btn_view.setToolTip("Look through the days that have already been recorded.")
        btn_view.clicked.connect(self._open_view_day)
        pick_row.addWidget(btn_view)
        sec_day.body_layout.addLayout(pick_row)

        # ── reaching back further than the log goes ──────────────────────
        # "Fill missing days" above never can: it starts at the newest day
        # already recorded.
        sec_past = section("past", "Earlier days", "#d08a1e")
        past_row = QHBoxLayout()
        past_row.setSpacing(5)
        self.btn_fill_day = QPushButton("Fill this day")
        self.btn_fill_day.setStyleSheet(_BTN)
        self.btn_fill_day.setToolTip(
            "Ask the archiver for both columns of the day on screen, however\n"
            "long ago it was. Values already recorded are left alone.")
        self.btn_fill_day.clicked.connect(self._on_fill_this_day)
        past_row.addWidget(self.btn_fill_day)
        self.btn_pick_days = QPushButton("Pick days")
        self.btn_pick_days.setStyleSheet(_BTN)
        self.btn_pick_days.setToolTip(
            "Choose the days on a calendar and fill them all in.\n"
            "Ctrl+click adds a day, Ctrl+Shift+click a stretch of days.")
        self.btn_pick_days.clicked.connect(self._on_pick_days)
        past_row.addWidget(self.btn_pick_days)
        sec_past.body_layout.addLayout(past_row)

        # ── the ranges the colours come from ─────────────────────────────
        sec_ref = section("refs", "What is acceptable", "#b0396b")
        self.btn_refs = QPushButton("References")
        self.btn_refs.setStyleSheet(_BTN)
        self.btn_refs.setToolTip(
            "Set the range each number is allowed to be in.\n"
            "A value near the edge of its range turns yellow, one outside it "
            "turns red.")
        self.btn_refs.clicked.connect(self._open_references)
        sec_ref.body_layout.addWidget(self.btn_refs)

        # ── where everybody else reads it ────────────────────────────────
        sec_share = section("share", "Shared folder", "#1a9e9e")
        self.lbl_share = QLabel("Looking for it...")
        self.lbl_share.setWordWrap(True)
        self.lbl_share.setStyleSheet(f"color:{INK_MUTED};padding:0 1px 2px 1px;")
        sec_share.body_layout.addWidget(self.lbl_share)
        share_row = QHBoxLayout()
        share_row.setSpacing(5)
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.setStyleSheet(_BTN)
        self.btn_sync.clicked.connect(self._on_sync)
        share_row.addWidget(self.btn_sync)
        self.btn_open_share = QPushButton("Open folder")
        self.btn_open_share.setStyleSheet(_BTN)
        self.btn_open_share.setToolTip("Open the shared folder in Explorer.")
        self.btn_open_share.setEnabled(False)
        self.btn_open_share.clicked.connect(self._on_open_share)
        share_row.addWidget(self.btn_open_share)
        sec_share.body_layout.addLayout(share_row)

        v.addStretch(1)
        scroll.setWidget(panel)
        ov.addWidget(scroll, 1)

        # On its own at the foot of the panel, away from the working buttons:
        # this one only shows what the program has been doing.
        btn_log = QPushButton("Log")
        btn_log.setStyleSheet(_BTN)
        btn_log.setToolTip("What the program did while fetching. Read this when "
                           "an update looks wrong.")
        btn_log.clicked.connect(self._open_log)
        ov.addWidget(btn_log)
        return outer

    # ── which sections are folded away ───────────────────────────────────
    def _ui_state_path(self):
        return store.local_dir() / "spfe_ui_state.json"

    def _load_ui_state(self) -> dict:
        """Never allowed to stop the program: a panel that will not open
        because a settings file went bad is worse than a panel that opens with
        every section showing."""
        try:
            import json
            with self._ui_state_path().open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_ui_state(self) -> None:
        try:
            import json
            path = self._ui_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(self._ui_state, fh, indent=2, sort_keys=True)
        except Exception:
            pass

    def _on_section_toggled(self, key: str, expanded: bool) -> None:
        self._ui_state[key] = bool(expanded)
        self._save_ui_state()

    def _set_all_sections(self, expanded: bool) -> None:
        for key, sec in self._sections.items():
            sec.set_expanded(expanded)
            self._ui_state[key] = bool(expanded)
        self._save_ui_state()

    # ── the two windows that used to be tabs ─────────────────────────────
    def _open_view_day(self):
        """The recorded days, to look at without leaving the day on screen."""
        if self._day_window is None:
            self._day_window = self._make_view_day_window()
        self._refresh_day_list()
        self._day_window.show()
        self._day_window.raise_()
        self._day_window.activateWindow()

    def _make_view_day_window(self) -> QDialog:
        dlg = _styled_ground(QDialog(self))
        dlg.setWindowTitle("View a day")
        dlg.resize(1100, 760)
        dlg.setFont(self.font())
        dlg.setPalette(_light_palette(dlg.palette()))
        dlg.setStyleSheet(_GROUND_STYLE)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"QSplitter::handle{{background:{LINE};}}")

        self.lst_days = QListWidget()
        self.lst_days.setStyleSheet(
            f"QListWidget{{background:{PAPER};color:{INK};border:1px solid #B0B0B0;}}"
            f"QListWidget::item{{padding:4px 6px;color:{INK};}}"
            f"QListWidget::item:selected{{background:#CFE3FF;color:{INK};}}")
        self.lst_days.currentItemChanged.connect(
            lambda cur, _prev: self._show_history_day(
                cur.data(Qt.ItemDataRole.UserRole) if cur else ""))
        split.addWidget(self.lst_days)

        # The table and the strip that names its moments, as one block: the
        # heading is two levels here too, or the day being looked at would be
        # six unlabelled columns of numbers.
        right = _styled_ground(QWidget())
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.hist_band = _HeaderBand()
        rv.addWidget(self.hist_band)
        self.hist_table = QTableWidget(0, 2)
        self.hist_table.setStyleSheet(_TABLE)
        self.hist_table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.hist_table.verticalHeader().setVisible(False)
        self.hist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.hist_table.viewport().installEventFilter(self)
        # Same as the day page: the table is served first and the leftover
        # height goes to the spacer at the bottom, not into a gap under the
        # heading strip.
        rv.addWidget(self.hist_table, 1000)
        rv.addStretch(1)
        self.hist_band.bind(self.hist_table)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 860])
        v.addWidget(split, 1)

        row = QHBoxLayout()
        self.lbl_hist_count = QLabel("")
        self.lbl_hist_count.setStyleSheet(f"color:{INK_MUTED};")
        row.addWidget(self.lbl_hist_count)
        row.addStretch(1)
        btn_show = QPushButton("Show this day in the table")
        btn_show.setStyleSheet(_BTN_PRIMARY)
        btn_show.clicked.connect(self._on_show_picked_day)
        row.addWidget(btn_show)
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(_BTN)
        btn_close.clicked.connect(dlg.hide)
        row.addWidget(btn_close)
        v.addLayout(row)
        return dlg

    def _open_log(self):
        if self._log_window is None:
            dlg = _styled_ground(QDialog(self))
            dlg.setWindowTitle("SPFE Values - Log")
            dlg.resize(900, 560)
            dlg.setFont(self.font())
            dlg.setPalette(_light_palette(dlg.palette()))
            dlg.setStyleSheet(_GROUND_STYLE)
            v = QVBoxLayout(dlg)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(6)
            v.addWidget(self.txt_log, 1)
            row = QHBoxLayout()
            row.addStretch(1)
            btn_clear = QPushButton("Clear")
            btn_clear.setStyleSheet(_BTN)
            btn_clear.clicked.connect(self.txt_log.clear)
            row.addWidget(btn_clear)
            btn_close = QPushButton("Close")
            btn_close.setStyleSheet(_BTN)
            btn_close.clicked.connect(dlg.hide)
            row.addWidget(btn_close)
            v.addLayout(row)
            self._log_window = dlg
        self._log_window.show()
        self._log_window.raise_()
        self._log_window.activateWindow()

    # ── the references ───────────────────────────────────────────────────
    #
    # The other half of "is this number all right?". The statistical judge
    # (spfe_stats) needs no setting up and asks "is this unlike the other
    # days?"; it can never catch a quantity that has been drifting out of spec
    # for a fortnight, because by then the drift IS the history. A reference is
    # a person saying what the number is actually allowed to be, and it is
    # judged fresh on every repaint -- so a range typed today colours the days
    # already on the screen.

    # The last one is the filler: the same trick as the day table, so no real
    # column has to be stretched to make the table reach its own frame.
    _REF_HEADS = ["", "Quantity", "Number", "Unit",
                  "Lowest", "Highest", "Yellow within %", ""]

    def _open_references(self):
        """The window where the acceptable range of every number is typed."""
        if self._fields_error:
            QMessageBox.warning(
                self, "SPFE Values",
                f"spfe_fields.json cannot be read:\n\n{self._fields_error}")
            return
        if self._refs_window is None:
            self._refs_window = self._make_references_window()
        self._fill_references()
        self._refs_window.show()
        self._refs_window.raise_()
        self._refs_window.activateWindow()

    def _make_references_window(self) -> QDialog:
        dlg = _styled_ground(QDialog(self))
        dlg.setWindowTitle("References")
        dlg.resize(880, 640)
        dlg.setFont(self.font())
        dlg.setPalette(_light_palette(dlg.palette()))
        dlg.setStyleSheet(_GROUND_STYLE)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        lead = QLabel(
            "The range each number is allowed to be in. A value outside its "
            "range is shown red, a value close to an edge yellow. Leave both "
            "boxes empty and the number is never coloured.")
        lead.setWordWrap(True)
        lead.setStyleSheet(f"color:{INK};")
        v.addWidget(lead)

        top = QHBoxLayout()
        top.setSpacing(6)
        lbl = QLabel("Yellow when within")
        lbl.setStyleSheet(f"color:{INK};font-weight:700;")
        top.addWidget(lbl)
        self.ed_warn_pct = QLineEdit(f"{self._warn_pct:g}")
        self.ed_warn_pct.setStyleSheet(_CAMPAIGN_EDIT)
        self.ed_warn_pct.setFixedWidth(70)
        self.ed_warn_pct.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,3}(?:\.\d*)?$"), self.ed_warn_pct))
        top.addWidget(self.ed_warn_pct)
        tail = QLabel(
            "% of the range. A number that wants its own gets it in the last "
            "column.")
        tail.setStyleSheet(f"color:{INK};")
        top.addWidget(tail)
        top.addStretch(1)
        v.addLayout(top)

        self.ref_table = QTableWidget(0, len(self._REF_HEADS))
        self.ref_table.setStyleSheet(_TABLE)
        self.ref_table.verticalHeader().setVisible(False)
        self.ref_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.ref_table.setAlternatingRowColors(False)
        self.ref_table.setHorizontalHeaderLabels(self._REF_HEADS)
        self.ref_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        # The same number-only editor the day table uses, so a range cannot be
        # typed with a comma -- these go into a JSON file that has to stay
        # readable by a machine.
        self._ref_delegate = _CellDelegate(dlg, numeric=True)
        self.ref_table.setItemDelegate(self._ref_delegate)
        self.ref_table.viewport().installEventFilter(self)
        v.addWidget(self.ref_table, 1)

        row = QHBoxLayout()
        btn_clear = QPushButton("Clear this line")
        btn_clear.setStyleSheet(_BTN)
        btn_clear.setToolTip("Empty the three boxes of the line the cursor is "
                             "on, so that number is no longer coloured.")
        btn_clear.clicked.connect(self._clear_reference_line)
        row.addWidget(btn_clear)
        row.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        for btn in buttons.buttons():
            btn.setStyleSheet(_BTN)
        buttons.accepted.connect(self._save_references)
        buttons.rejected.connect(dlg.hide)
        row.addWidget(buttons)
        v.addLayout(row)
        return dlg

    def _reference_lines(self) -> list[_Line]:
        """Every line that can have a range: the numbers, never the notes."""
        return [ln for ln in self._table_lines() if not ln.row.is_text]

    def _fill_references(self):
        """Put the stored ranges into the window."""
        table = self.ref_table
        lines = self._reference_lines()
        table.clearContents()
        table.clearSpans()
        table.setRowCount(len(lines))
        self.ed_warn_pct.setText(f"{self._warn_pct:g}")

        group_start, group_name = 0, None
        for r, line in enumerate(lines):
            row_def = line.row
            if row_def.group != group_name:
                if group_name is not None and r - group_start > 1:
                    table.setSpan(group_start, 0, r - group_start, 1)
                group_start, group_name = r, row_def.group
            entry = limits.normalise(self._refs.get(line.column) or {})

            self._ref_label(table, r, 0, row_def.group if r == group_start else "",
                            band=True, centre=True)
            self._ref_label(table, r, 1,
                            row_def.base_label if line.first else "")
            self._ref_label(table, r, 2, line.part, centre=True)
            self._ref_label(table, r, 3, row_def.unit, centre=True)
            for c, value in ((4, entry["min"]), (5, entry["max"]),
                             (6, entry["warn_pct"])):
                cell = QTableWidgetItem("" if value is None else f"{value:g}")
                cell.setBackground(QColor(TYPED_BG))
                cell.setForeground(QColor(INK))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled
                              | Qt.ItemFlag.ItemIsSelectable
                              | Qt.ItemFlag.ItemIsEditable)
                if c == 4:
                    cell.setData(_ROLE_COL, line.column)
                if c == 6:
                    cell.setToolTip(
                        "Leave empty to use the percentage at the top of the "
                        "window.")
                table.setItem(r, c, cell)
            self._ref_label(table, r, 7, "")
            table.setRowHeight(r, 26)
        if group_name is not None and len(lines) - group_start > 1:
            table.setSpan(group_start, 0, len(lines) - group_start, 1)

        self._fit_ref_columns()

    def _fit_ref_columns(self) -> None:
        """The References window's own columns, measured the same way.

        Called again on every resize: when the window is first filled it has
        not been laid out yet, so its viewport is not the width it will end up
        being, and the filler would come out as nothing.
        """
        table = getattr(self, "ref_table", None)
        if table is None or table.columnCount() < 8:
            return
        head = QFontMetrics(QFont(FONT_FAMILY, FONT_PT, QFont.Weight.Bold))
        rows = range(table.rowCount())
        limits_ = ((_GROUP_MIN, _GROUP_MAX), (140, 300),
                   (_PART_MIN, _PART_MAX), (44, 80),
                   (86, 130), (86, 130), (120, 150))
        widths = []
        for c, (low, high) in enumerate(limits_):
            need = max(self._text_width(table, c, rows) + _CELL_PAD,
                       head.horizontalAdvance(self._REF_HEADS[c]) + 18)
            widths.append(_clamp(need, low, high))

        bar = table.verticalScrollBar()
        room = (table.viewport().width() - sum(widths) - 2
                - (bar.width() if bar.isVisible() else 0))
        # The name of the quantity is the one that gains from a wide window --
        # it is the only column here that can be too long for its box.
        if room > 0:
            give = min(room, limits_[1][1] - widths[1])
            widths[1] += give
            room -= give
        for c, w in enumerate(widths):
            table.setColumnWidth(c, w)
        table.setColumnWidth(7, max(0, room))

    def _ref_label(self, table: QTableWidget, r: int, c: int, text: str, *,
                   band: bool = False, centre: bool = False) -> None:
        """One read-only label of the References window.

        Its own method because every one of them has to say what its ink is:
        an item left to the theme is white on white here, and the window is
        four fifths labels.
        """
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setBackground(QColor(GROUP_BAND if band else PAPER))
        item.setForeground(QColor(INK))
        if band:
            fnt = item.font()
            fnt.setBold(True)
            item.setFont(fnt)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter if centre
                              else (Qt.AlignmentFlag.AlignLeft
                                    | Qt.AlignmentFlag.AlignVCenter))
        table.setItem(r, c, item)

    def _clear_reference_line(self):
        row = self.ref_table.currentRow()
        if row < 0:
            return
        self._ref_delegate.commit_open_editor()
        for c in (4, 5, 6):
            item = self.ref_table.item(row, c)
            if item is not None:
                item.setText("")

    def _save_references(self):
        """Read the window, write both copies, and repaint the day."""
        self._ref_delegate.commit_open_editor()
        entries: dict = {}
        for r in range(self.ref_table.rowCount()):
            anchor = self.ref_table.item(r, 4)
            if anchor is None:
                continue
            column = str(anchor.data(_ROLE_COL) or "")
            if not column:
                continue
            low = limits.as_number(anchor.text())
            item_hi = self.ref_table.item(r, 5)
            item_pct = self.ref_table.item(r, 6)
            high = limits.as_number(item_hi.text() if item_hi else "")
            pct = limits.as_number(item_pct.text() if item_pct else "")
            if low is None and high is None:
                continue          # a line nobody filled in is simply not stored
            if low is not None and high is not None and low > high:
                low, high = high, low
            entry = {"min": low, "max": high}
            if pct is not None and pct >= 0:
                entry["warn_pct"] = pct
            entries[column] = entry

        pct = limits.as_number(self.ed_warn_pct.text())
        if pct is not None and pct >= 0:
            self._warn_pct = pct
            self.cfg["warn_pct"] = pct
            store.save_config(self.cfg)

        self._refs_window.hide()
        self._use_references(entries)
        self._set_status("Saving the references...", BLUE)

        def body():
            res = self.store.set_references(entries)
            self._bridge.status.emit(
                res.problem or f"{len(entries)} reference(s) saved.",
                WARN_RED if res.problem else OK_GREEN)

        threading.Thread(target=body, daemon=True, name="spfe-refs").start()

    def _use_references(self, entries: dict) -> None:
        """Take a reference table into use and repaint what is on screen."""
        if entries == self._refs:
            return
        self._refs = dict(entries)
        self._refresh_today()

    # ── background jobs ──────────────────────────────────────────────────
    def _run(self, fn, name: str):
        """Run `fn` off the UI thread. Every result comes back through the one
        shared bridge, never a per-job signal object."""
        if self._working:
            self._set_status("Something is already running - wait for it.", WARN_RED)
            return
        self._bridge.busy.emit(True)

        def body():
            try:
                fn()
            except Exception as exc:
                self._bridge.log.emit(
                    f"{name} failed: {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc())
                self._bridge.status.emit(
                    f"{name} failed: {type(exc).__name__}: {exc}", WARN_RED)
            finally:
                self._bridge.busy.emit(False)

        threading.Thread(target=body, daemon=True, name=f"spfe-{name}").start()

    def _start_up(self):
        # Show what is already on this PC before anything touches the network.
        # The share probe and the catch-up can take seconds; an empty table for
        # that whole time reads as "the log is gone".
        #
        # The deleted moments are dropped here too, from the local note alone:
        # otherwise a column somebody deleted yesterday is on screen again for
        # the first few seconds of every start.
        gone = self.store.deleted_stamps()
        self._rows = [r for r in store.read_csv(self.store.local_csv)
                      if r.get("datetime", "") not in gone]
        # The local copy of the references, before the share is looked for: the
        # colours have to be right on the very first table, not a probe later.
        self._refs = store.read_json_map(self.store.local_references, "limits")
        self._refresh_all()
        if self._rows:
            self._set_status(
                f"{len(self._rows)} recorded moments on this PC. "
                "Looking for the shared folder...", BLUE)

        def job():
            self.store.connect()
            root = self.store.share_root
            if root:
                self._bridge.finished.emit(
                    lambda r=root: self._show_share_path(r))
            else:
                self._bridge.finished.emit(lambda: (
                    self.lbl_share.setText(
                        "Neither share name answered.\nWorking on this PC only."),
                    self.lbl_share.setStyleSheet(
                        f"color:{WARN_RED};font-weight:700;")))
            res = self.store.sync()
            if res.share_rows:
                self._bridge.log.emit(f"Sent {res.share_rows} queued rows to the share.")
            merged = self.store.references()
            self._bridge.finished.emit(lambda e=merged: self._use_references(e))
            self._reload_rows()
            if self.cfg.get("catch_up_on_start", True) and not self._fields_error:
                self._catch_up()

        self._run(job, "Start-up")

    def _reload_rows(self):
        self._rows = self.store.all_rows()
        self._bridge.finished.emit(self._refresh_all)

    # ── recording ────────────────────────────────────────────────────────
    def _on_record_now(self):
        if not self._guard_fields():
            return

        def job():
            self._bridge.status.emit("Reading the PVs...", BLUE)
            reading = recorder.record_now(
                self.fields, self._rows, self.cfg, log_fn=self._bridge.log.emit)
            self._store_reading(reading, "Record now")

        self._run(job, "Record now")

    def _on_record_slot(self, slot: str):
        if not self._guard_fields():
            return
        hhmm = self.cfg.get("slot_morning" if slot == SLOT_MORNING else "slot_evening")
        h, m = store._parse_hhmm(hhmm)
        when = datetime.combine(self._day, datetime.min.time()).replace(
            hour=h, minute=m, tzinfo=TZ_PRAGUE)
        if when > datetime.now(TZ_PRAGUE):
            self._set_status(
                f"{store.SLOT_HEADINGS[slot]} on {self._day:%d.%m.%Y} has not "
                "happened yet - the archiver has nothing to give.", WARN_RED)
            return

        def job():
            self._bridge.status.emit("Reading the PVs...", BLUE)
            reading = recorder.read_and_judge(
                self.fields, when, slot, self._rows, self.cfg,
                log_fn=self._bridge.log.emit)
            self._store_reading(reading, store.SLOT_HEADINGS[slot])

        self._run(job, "Record slot")

    def _on_update(self):
        if not self._guard_fields():
            return
        self._run(self._catch_up, "Update")

    def _catch_up(self):
        """Fill in every missing weekday slot from the last record up to now.

        This is why nothing has to run at 09:00: the archiver is asked
        afterwards what the value was then.
        """
        today = datetime.now(TZ_PRAGUE).date()
        start = catch_up_start(self._rows, self.cfg, today)
        self._fill_days(start, datetime.now(TZ_PRAGUE))

    def _on_fill_this_day(self):
        """Both columns of the day on screen, however far back it is.

        The catch-up cannot do this: it starts at the newest day already
        recorded, so the past was simply out of reach.
        """
        if not self._guard_fields():
            return
        day = self._day
        now = datetime.now(TZ_PRAGUE)
        if day > now.date():
            self._set_status(
                f"{day:%d.%m.%Y} has not happened yet - the archiver has "
                "nothing to give.", WARN_RED)
            return
        end = min(now, datetime(day.year, day.month, day.day, 23, 59,
                                tzinfo=TZ_PRAGUE))
        self._run(lambda: self._fill_days(day, end, one_day=True,
                                          asked_for=True),
                  "Fill this day")

    def _on_pick_days(self):
        """The house calendar: pick the days to fill in and fill them all."""
        if not self._guard_fields():
            return
        days = self._ask_for_days()
        if not days:
            return
        now = datetime.now(TZ_PRAGUE)
        first, last = min(days), max(days)
        end = min(now, datetime(last.year, last.month, last.day, 23, 59,
                                tzinfo=TZ_PRAGUE))
        picked = set(days)
        self._run(lambda: self._fill_days(first, end, only=picked,
                                          asked_for=True), "Pick days")

    def _ask_for_days(self) -> list:
        """One calendar, the same rules as everywhere else in the suite: a
        plain click picks one day, Ctrl adds a day, Ctrl+Shift a stretch, and
        nothing happens until OK.

        No time window: the two moments of a day are fixed here, so the per-day
        From/To of the full picker would be a control with nothing to control.
        """
        dlg = _styled_ground(QDialog(self))
        dlg.setWindowTitle("Pick the days to fill in")
        dlg.setFont(self.font())
        dlg.setPalette(_light_palette(dlg.palette()))
        dlg.setStyleSheet(_GROUND_STYLE)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        frame, cal = daypicker.make_calendar(
            daypicker.date_to_qdate(self._day))
        v.addWidget(frame)

        lbl = QLabel("")
        lbl.setStyleSheet(f"color:{INK_MUTED};")
        v.addWidget(lbl)

        picked: list = [self._day]
        anchor: list = [self._day]

        def repaint():
            cal.day_delegate.set_selected(
                [daypicker.date_to_qdate(d) for d in picked])
            if len(picked) == 1:
                lbl.setText(f"1 day: {picked[0]:%d.%m.%Y}")
            elif picked:
                lbl.setText(f"{len(picked)} days: "
                            f"{min(picked):%d.%m.%Y} - {max(picked):%d.%m.%Y}")
            else:
                lbl.setText("No day picked.")
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                bool(picked))

        def clicked(qd: QDate):
            day = daypicker.qdate_to_date(qd)
            mods = QApplication.keyboardModifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            # The weekday gate is Mon-Fri: a stretch of days follows the same
            # working week the two scheduled moments do, and a Ctrl+click still
            # takes any single day, weekend included.
            picked[:] = daypicker.compute_click(
                picked, day, anchor[0], ctrl=ctrl, shift=shift,
                weekday_gate=set(range(5)))
            anchor[0] = day
            repaint()

        # Built before the first repaint: it enables its own OK button.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        for btn in buttons.buttons():
            btn.setStyleSheet(_BTN)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)

        cal.clicked.connect(clicked)
        repaint()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return []
        return sorted(picked)

    def _fill_days(self, start: date, end_dt: datetime, *,
                   one_day: bool = False, only: set | None = None,
                   asked_for: bool = False):
        """Ask the archiver for every scheduled moment in a span.

        The one backfill path, pointed anywhere: the catch-up hands it "from
        the last recorded day", Fill this day one day, Pick days a chosen set.
        A moment already in the log is skipped, so running it twice costs
        nothing.

        `asked_for` means somebody chose these days by hand, and then a weekend
        is filled in like any other day. The automatic catch-up still skips
        weekends -- it is filling in a working week nobody was here for -- but
        refusing a Saturday the operator deliberately clicked on reads as a
        broken button.
        """
        cfg = dict(self.cfg, weekdays_only=False) if asked_for else self.cfg
        wanted = scheduled_slots(start, end_dt, cfg)
        if only is not None:
            wanted = [(ts, slot) for ts, slot in wanted if ts.date() in only]
        have = {r.get("datetime", "") for r in self._rows}
        todo = [(ts, slot) for ts, slot in wanted
                if f"{ts:%Y-%m-%d %H:%M:%S}" not in have]

        if not todo:
            self._bridge.status.emit(
                f"{start:%d.%m.%Y} is already recorded." if one_day
                else "Everything asked for is already recorded.", OK_GREEN)
            return

        self._bridge.log.emit(f"Filling in {len(todo)} missing moments from {start}.")
        rows = list(self._rows)
        records: list[Record] = []
        flags: dict[str, dict[str, str]] = {}

        for i, (ts, slot) in enumerate(todo, 1):
            self._bridge.status.emit(
                f"Filling in {ts:%d.%m.} {store.SLOT_HEADINGS[slot]} "
                f"({i} of {len(todo)})...", BLUE)
            reading = recorder.read_and_judge(
                self.fields, ts, slot, rows, self.cfg, log_fn=self._bridge.log.emit)
            if not reading.measured:
                self._bridge.log.emit(
                    f"   {ts:%Y-%m-%d %H:%M} - no PV answered, nothing recorded.")
                continue
            records.append(reading.record)
            if reading.flags:
                flags[reading.record.stamp] = reading.flags
            rows.append(reading.record.to_csv_row(self.fields.columns))

        if not records:
            self._bridge.status.emit(
                "Nothing was recorded - no PV answered. See the Log window.", WARN_RED)
            return

        res = self.store.save(records, flags)
        self._rows = self.store.all_rows()
        msg = f"Filled in {len(records)} moments."
        if flags:
            msg += f" {sum(len(v) for v in flags.values())} value(s) marked as unusual."
        self._bridge.status.emit(
            res.problem or msg, WARN_RED if res.problem else OK_GREEN)
        self._bridge.finished.emit(self._refresh_all)

    def _store_reading(self, reading: recorder.Reading, what: str):
        rec = reading.record
        if not reading.measured:
            self._bridge.status.emit(
                f"{what}: no PV answered - nothing was recorded. See the Log window.",
                WARN_RED)
            return
        flags = {rec.stamp: reading.flags} if reading.flags else {}
        res = self.store.save([rec], flags)
        self._rows = self.store.all_rows()

        bits = [f"{what}: {reading.measured} value(s) recorded"]
        if reading.missing:
            bits.append(f"{len(reading.missing)} PV(s) gave nothing")
        if reading.flags:
            bits.append(f"{len(reading.flags)} value(s) look unusual - "
                        "see the amber cells")
        msg = res.problem or (", ".join(bits) + ".")
        colour = WARN_RED if (res.problem or reading.flags) else OK_GREEN
        self._bridge.status.emit(msg, colour)
        for key, reason in reading.flags.items():
            self._bridge.log.emit(f"   unusual - {key}: {reason}")
        self._bridge.finished.emit(self._go_today)

    def _on_sync(self):
        def job():
            if not self.store.share_root:
                self.store.connect()
            res = self.store.sync()
            if res.problem:
                self._bridge.status.emit(res.problem, WARN_RED)
            elif res.share_rows:
                self._bridge.status.emit(
                    f"Sent {res.share_rows} row(s) to the shared folder.", OK_GREEN)
            else:
                self._bridge.status.emit("The shared folder is already up to date.", OK_GREEN)
            self._reload_rows()

        self._run(job, "Sync")

    def _check_slot_due(self):
        """Record a slot the moment it passes, if the program happens to be open.

        Only ever a convenience: whatever this misses, the catch-up fills in.
        """
        if self._working or self._fields_error:
            return
        now = datetime.now(TZ_PRAGUE)
        if self.cfg.get("weekdays_only", True) and now.weekday() >= 5:
            return
        for key, slot in (("slot_morning", SLOT_MORNING), ("slot_evening", SLOT_EVENING)):
            h, m = store._parse_hhmm(self.cfg.get(key))
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if not (due <= now < due + timedelta(minutes=5)):
                continue
            stamp = f"{due:%Y-%m-%d %H:%M:%S}"
            if stamp == self._last_slot_check:
                return
            if any(r.get("datetime") == stamp for r in self._rows):
                return
            self._last_slot_check = stamp
            self._append_log(f"{store.SLOT_HEADINGS[slot]} is due - recording.")
            self._on_record_slot(slot)
            return

    # ── typed values save themselves ─────────────────────────────────────
    #
    # There is no Save button. A cell is written the moment its editor closes
    # -- Enter, Tab, or a click anywhere else. What that costs is the four
    # guards below; what it buys is that nobody loses a value by walking away
    # from the PC.

    def _on_cell_edited(self, item: QTableWidgetItem):
        if self._fill_depth:
            return                     # the table is painting itself
        if item.column() < _FIRST_VALUE:
            return

        key = item.data(_ROLE_KEY)
        column = item.data(_ROLE_COL)
        if not key or not column:
            return
        row_def = self.fields.row_by_key(str(key))
        if row_def is None:
            return
        column = str(column)

        text = item.text().strip()
        # The no-op guard, and it is not cosmetic. The cell shows a ROUNDED
        # number; committing it unchanged would write the rounded value over
        # the measured one, in both logs, for ever.
        if text == str(item.data(_ROLE_TEXT) or ""):
            return

        stamp = item.data(_ROLE_STAMP)
        if not stamp:
            # A column that was never recorded -- every column of a day the
            # archiver has nothing for. Typing into it writes the day down by
            # hand instead of refusing the keystroke.
            self._create_manual_column(
                (item.column() - _FIRST_VALUE) // self._parts(), column, text)
            return

        rec = self._record_for(str(stamp))
        if rec is None:
            return

        # One cell is one number now, so there is nothing to take apart: the
        # text goes into its own column and touches nothing else. Correcting Y
        # used to mean retyping X and SUM with it.
        edits: dict[str, dict[str, str]] = {
            stamp: {column: ("" if text in ("", "-") else text)}}

        # The first change to a day also carries the day's held defaults into
        # the files. Otherwise the screen would go on showing settings the log
        # does not have -- the Save button used to do this as a side effect.
        for other_row in self.fields.rows:
            if other_row.key == row_def.key or not other_row.default_applies(rec.day):
                continue
            other_col = other_row.columns[0]
            if str(rec.values.get(other_col, "") or ""):
                continue
            other_item = self._item_for(other_col, str(stamp))
            if other_item is not None and other_item.data(_ROLE_HELD):
                edits[stamp][other_col] = other_row.default

        self._queue_edits(edits, rec)

    def _create_manual_column(self, index: int, column: str, text: str) -> None:
        """Write down a moment nobody recorded, because somebody typed in it.

        The archiver has nothing for some days -- a weekend, a shutdown, a PV
        that did not exist yet. The column is still on screen, because the
        table is the shape of the paper form, and typing in it now creates the
        record instead of quietly refusing.
        """
        if not (0 <= index < len(self._columns)):
            return
        pending = self._columns[index]
        if pending.source != PENDING:
            return

        rec = Record(when=pending.when, slot=pending.slot, source="manual")
        if text in ("", "-"):
            return
        rec.values[column] = text
        store.apply_defaults(self.fields, rec.values, rec.day)

        def job():
            res = self.store.save([rec])
            self._bridge.status.emit(
                res.problem or f"{self._heading_for(rec)} on "
                f"{rec.day:%d.%m.%Y} written down by hand.",
                WARN_RED if res.problem else OK_GREEN)
            self._reload_rows()

        self._run(job, "Write down by hand")

    def _queue_edits(self, edits: dict, rec: Record):
        """Put the change on the queue and repaint the cell from the record.

        Written into the queue file straight away, before anything slow: an
        edit has to survive the program being closed a heartbeat later.
        """
        with self._pending_lock:
            for stamp, patch in edits.items():
                self._pending.setdefault(stamp, {}).update(patch)
        self.store.queue_edits(edits)

        for stamp, patch in edits.items():
            target = self._record_for(stamp)
            if target is None:
                continue
            for column, value in patch.items():
                if value in ("", None):
                    target.values.pop(column, None)
                else:
                    target.values[column] = value
            self._patch_row_cache(stamp, patch)
        # The verdict was computed on the old number.
        self._flags.pop(rec.stamp, None)
        self._repaint_column(rec.stamp)

        self._set_status("Saving...", BLUE)
        self._save_timer.start()

    def _flush_pending(self):
        """Write everything queued, in one go, on one thread.

        One writer, never one per cell: each write rewrites a whole file on the
        share, and two rewrites of the same file race by construction. It also
        stays out of _run, whose "something is already running" refusal would
        drop an edit while the start-up job holds the program for a minute.
        """
        if self._saving:
            return                     # the writer re-arms this timer itself
        with self._pending_lock:
            if not self._pending:
                return
            batch = {s: dict(p) for s, p in self._pending.items()}
        self._saving = True

        def body():
            try:
                res = self.store.update_values(batch)
                if res.problem:
                    self._bridge.status.emit(res.problem, WARN_RED)
                else:
                    cells = sum(len(p) for p in batch.values())
                    self._bridge.status.emit(
                        f"Saved {cells} value(s).", OK_GREEN)
            except Exception as exc:
                # The value stays on screen and in the queue file. Putting the
                # old text back would be the one way to actually lose it.
                self._bridge.log.emit(f"Saving failed: {type(exc).__name__}: {exc}")
                self._bridge.status.emit(
                    f"Could not save: {exc}. The value is kept and will be "
                    "written again with Sync now.", WARN_RED)
            finally:
                with self._pending_lock:
                    for stamp, patch in batch.items():
                        left = self._pending.get(stamp)
                        if left is None:
                            continue
                        for column, value in patch.items():
                            if left.get(column) == value:
                                left.pop(column, None)
                        if not left:
                            self._pending.pop(stamp, None)
                    more = bool(self._pending)
                self._saving = False
                if more:
                    self._bridge.finished.emit(self._save_timer.start)

        threading.Thread(target=body, daemon=True, name="spfe-save").start()

    def _record_for(self, stamp: str) -> Record | None:
        for rec in self._columns:
            if rec.stamp == stamp:
                return rec
        return None

    def _item_for(self, column: str, stamp: str) -> QTableWidgetItem | None:
        """The cell of one NUMBER in one recorded moment."""
        for r, row_def in enumerate(self.fields.rows):
            for line in self._lines_for(row_def):
                if line.column != column:
                    continue
                for c, rec in enumerate(self._columns):
                    if rec.stamp == stamp:
                        return self.table.item(r, self._grid_col(c, line.index))
        return None

    def _patch_row_cache(self, stamp: str, patch: dict) -> None:
        """Keep self._rows in step without re-reading both logs.

        The statistics read self._rows, and re-reading the files would rebuild
        the table -- which destroys an editor the operator has open on the next
        cell without committing it.
        """
        for row in self._rows:
            if row.get("datetime") == stamp:
                row.update({k: ("" if v is None else v) for k, v in patch.items()})
                return

    def _repaint_column(self, stamp: str) -> None:
        """Redraw one column from the record, quietly.

        The cell has to show what the file holds, not what was typed: type one
        number into an X;Y;SUM cell and the other two must appear again.
        """
        for c, rec in enumerate(self._columns):
            if rec.stamp != stamp:
                continue
            flags = self._flags_for(rec, self._day)
            self._fill_depth += 1
            try:
                for r, row_def in enumerate(self.fields.rows):
                    for line in self._lines_for(row_def):
                        item = self.table.item(r, self._grid_col(c, line.index))
                        if item is None:
                            continue
                        text, held = self._cell_text(line, rec, self._day)
                        reason = flags.get(line.column, "")
                        item.setText(text)
                        item.setData(_ROLE_TEXT, text)
                        item.setData(_ROLE_HELD, held)
                        self._paint_cell(item, line, rec, reason, held,
                                         day=self._day)
            finally:
                self._fill_depth -= 1
            return

    # ── the campaign ─────────────────────────────────────────────────────
    def _show_campaign(self):
        """Put the day's campaign in the box.

        A name it was given itself is real text; a name carried over from an
        earlier day is placeholder grey, so the two read differently. Skipped
        while the box has the cursor in it -- a background refresh would
        otherwise wipe what is being typed.
        """
        if self.ed_campaign.hasFocus():
            return
        try:
            name, own = self.store.campaign_for_day(self._day)
        except Exception:
            name, own = "", False
        self._campaign_shown = name if own else ""
        self.ed_campaign.setText(name if own else "")
        self.ed_campaign.setPlaceholderText(
            name if (name and not own) else "not named")

    def _on_campaign_typed(self):
        """editingFinished fires on Enter AND on leaving the box, so the last
        saved name is compared first: otherwise one name is written twice."""
        name = self.ed_campaign.text().strip()
        if name == self._campaign_shown:
            return
        self._campaign_shown = name
        day = self._day

        def job():
            res = self.store.set_campaign(day, name)
            if res.problem:
                self._bridge.status.emit(res.problem, WARN_RED)
            elif name:
                self._bridge.status.emit(
                    f"{day:%d.%m.%Y} and the days after it: {name}.", OK_GREEN)
            else:
                self._bridge.status.emit(
                    f"The campaign ends on {day:%d.%m.%Y}.", OK_GREEN)
            self._bridge.finished.emit(self._show_campaign)

        self._run(job, "Campaign")

    # ── deleting an extra column ─────────────────────────────────────────
    def _on_header_menu(self, pos):
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        self._column_menu(col, self.table.horizontalHeader().mapToGlobal(pos))

    def _on_table_menu(self, pos):
        col = self.table.columnAt(pos.x())
        self._column_menu(col, self.table.viewport().mapToGlobal(pos))

    def _column_menu(self, col: int, where):
        """Only an extra moment can be deleted. Morning and At the end are the
        shape of the day and stay; re-reading one is what Morning / At the end
        are for."""
        rec = self._column_at(col)
        if rec is None or rec.source == PENDING or rec.slot != SLOT_NOW:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{PAPER};color:{INK};border:1px solid {LINE_DARK};}}"
            f"QMenu::item:selected{{background:#CFE3FF;color:{INK};}}")
        act = menu.addAction(f"Delete the {rec.when:%H:%M} column")
        if menu.exec(where) is act:
            self._delete_column(rec)

    def _column_at(self, col: int) -> Record | None:
        """The moment a grid column belongs to. Every moment is `parts` columns
        wide now -- X, Y and SUM -- so the column is divided, not subtracted."""
        if col < _FIRST_VALUE:
            return None
        moment = (col - _FIRST_VALUE) // self._parts()
        if moment >= len(self._columns):
            return None
        return self._columns[moment]

    def _delete_column(self, rec: Record):
        answer = QMessageBox.question(
            self, "SPFE Values",
            f"Delete the {rec.when:%H:%M} column of {rec.day:%d.%m.%Y}?\n\n"
            "Its values go from the log, from the shared log and from the "
            "workbook. Nothing in this program can bring them back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        def job():
            res = self.store.delete_record(rec)
            self._bridge.status.emit(
                res.problem or f"The {rec.when:%H:%M} column of "
                f"{rec.day:%d.%m.%Y} is deleted.",
                WARN_RED if res.problem else OK_GREEN)
            self._bridge.log.emit(f"Deleted the moment {rec.stamp}.")
            self._flags.pop(rec.stamp, None)
            self._reload_rows()

        self._run(job, "Delete column")

    # ── display ──────────────────────────────────────────────────────────
    def _step_day(self, delta: int):
        self._commit_open_editor()
        self._day += timedelta(days=delta)
        self._refresh_today()

    def _go_today(self):
        self._commit_open_editor()
        self._day = datetime.now(TZ_PRAGUE).date()
        self._refresh_today()

    def _editor_closed(self):
        """A cell is no longer open, so a rebuild that was waiting can run."""
        if self._refresh_wanted:
            QTimer.singleShot(0, self._refresh_all)

    def _commit_open_editor(self):
        """Write a cell that is still being edited into the table.

        Leaving the day, or closing the window, must not throw away what is in
        an open editor: Qt destroys it without committing.
        """
        for delegate in (self._num_delegate, self._plain_delegate):
            delegate.commit_open_editor()

    def _refresh_all(self):
        # The verdicts depend on the history, which has just grown, so they are
        # thrown away rather than kept: a number can stop being unusual.
        self._flags.clear()
        self._refresh_today()
        self._refresh_day_list()

    def _columns_for(self, day: date) -> list[Record]:
        """The day's columns, in the order they appear in the table.

        Morning and At the end are always present, empty if they have not been
        recorded yet, so the table on screen is the same shape as the paper
        form and a missing slot is visible rather than absent.
        """
        want = day.isoformat()
        recs = []
        for r in self._rows:
            if r.get("date") != want:
                continue
            rec = store.record_from_csv(r, self.fields)
            if rec is not None:
                recs.append(rec)

        for key, slot in (("slot_morning", SLOT_MORNING),
                          ("slot_evening", SLOT_EVENING)):
            if any(r.slot == slot for r in recs):
                continue
            h, m = store._parse_hhmm(self.cfg.get(key))
            recs.append(Record(
                when=datetime(day.year, day.month, day.day, h, m, tzinfo=TZ_PRAGUE),
                slot=slot, source=PENDING))

        order = {SLOT_MORNING: 0, SLOT_EVENING: 1}
        recs.sort(key=lambda r: (order.get(r.slot, 2), r.when))
        return recs

    def _refresh_today(self):
        # A rebuild throws away an open editor without committing it, so it
        # waits until the cell is closed. Every background job ends in a
        # refresh, and one of them landing mid-word is how a typed value
        # disappeared.
        if self.table.state() == QAbstractItemView.State.EditingState:
            self._refresh_wanted = True
            return
        self._refresh_wanted = False
        self._columns = self._columns_for(self._day)
        self.lbl_day.setText(f"{self._day:%d.%m.%Y}  ({self._day:%A})")
        self._show_campaign()
        self._fill_table(self.table, self._day, self._columns, editable=True)

    def _table_lines(self) -> list[_Line]:
        """Every number of the table, quantity by quantity.

        NOT one entry per table row any more: a quantity with three numbers is
        one row holding three of these. `_line_at` is what turns a cell back
        into the number it stands for.
        """
        out: list[_Line] = []
        for row_def in self.fields.rows:
            cols = row_def.columns
            for i, column in enumerate(cols):
                out.append(_Line(row_def, column, row_def.part_label(column),
                                 i, len(cols)))
        return out

    def _lines_for(self, row_def) -> list[_Line]:
        """The numbers of one quantity, left to right."""
        cols = row_def.columns
        return [_Line(row_def, column, row_def.part_label(column), i, len(cols))
                for i, column in enumerate(cols)]

    def _parts(self) -> int:
        """How many numbers the widest quantity has: 3, for X, Y and SUM.

        Derived, never written down: it is how many sub-columns every moment
        gets, and a quantity added to the field file with a fourth number has
        to widen the table rather than fall off the end of it.
        """
        try:
            return max((len(r.columns) for r in self.fields.rows), default=1)
        except Exception:
            return 1

    def _part_headings(self) -> list[str]:
        """The name of each sub-column: ['X', 'Y', 'SUM'].

        Only a name every multi-number quantity agrees on at that position is
        used. Two quantities calling their second number different things means
        the heading cannot speak for both, and it is left blank -- the Detail
        column still says the order in words, e.g. "Input - BA2Loop2 (X,Y,SUM)".
        """
        parts = self._parts()
        out: list[str] = []
        for i in range(parts):
            names = {row.component_names[i]
                     for row in self.fields.rows
                     if len(row.columns) > 1 and i < len(row.component_names)}
            out.append(names.pop() if len(names) == 1 else "")
        return out

    def _fill_table(self, table: QTableWidget, day: date,
                    columns: list[Record], *, editable: bool):
        # A counter, not a flag: filling writes every cell, and a nested fill
        # clearing a flag in its finally would un-guard the outer one.
        self._fill_depth += 1
        try:
            rows_def = self.fields.rows
            parts = self._parts()
            table.clear()
            # Spans survive clear(), so a table rebuilt with a different
            # quantity list would keep the old merged blocks over the new rows.
            table.clearSpans()
            table.setRowCount(len(rows_def))
            # Two label columns, then every moment split into `parts`
            # sub-columns -- X, Y and SUM side by side -- and one filler at the
            # end. The filler is what lets every real column be only as wide as
            # what is in it: without it the slack had to go somewhere, and it
            # went into the Detail column, which is how a 1 cm label ended up
            # in a 10 cm box.
            table.setColumnCount(_FIRST_VALUE + len(columns) * parts + 1)
            heads = self._part_headings()
            table.setHorizontalHeaderLabels(
                ["", "Detail"] + heads * len(columns) + [""])
            for c, rec in enumerate(columns):
                tip = self._heading_tip(rec)
                for i in range(parts):
                    head = table.horizontalHeaderItem(self._grid_col(c, i))
                    if head is not None:
                        head.setToolTip(tip)

            value_font = QFont(FONT_FAMILY, VALUE_PT)
            hh = table.horizontalHeader()
            for c in range(table.columnCount()):
                hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)

            flags_by_stamp = {rec.stamp: self._flags_for(rec, day) for rec in columns}
            text_rows: set[int] = set()

            # Where each group begins. The delegates read this set to draw the
            # rule that separates one group from the next, so it is rebuilt in
            # place -- both delegates hold a reference to this same set.
            self._group_starts.clear()
            r = 0
            for group, group_rows in self.fields.groups:
                first = r
                self._group_starts.add(first)
                for row_def in group_rows:
                    lines = self._lines_for(row_def)
                    g = QTableWidgetItem(group if r == first else "")
                    g.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    g.setBackground(QColor(GROUP_BAND))
                    g.setForeground(QColor(INK))
                    fnt = g.font()
                    fnt.setBold(True)
                    g.setFont(fnt)
                    g.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(r, 0, g)

                    # The unit lives here, in the label, so a value cell holds
                    # nothing but the number the operator has to type. The
                    # LABEL, not base_label: with the numbers side by side the
                    # "(X,Y,SUM)" tail is the only thing that says which of
                    # them is which when the heading cannot.
                    lab = QTableWidgetItem(self._detail_label(row_def))
                    lab.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    lab.setBackground(QColor(PAPER))
                    lab.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                         | Qt.AlignmentFlag.AlignVCenter)
                    if row_def.url:
                        lab.setForeground(QColor(LINK_BLUE))
                        fnt = lab.font()
                        fnt.setUnderline(True)
                        lab.setFont(fnt)
                        lab.setToolTip("Opens in the browser:\n" + row_def.url)
                    else:
                        lab.setForeground(QColor(INK))
                    table.setItem(r, 1, lab)

                    # Every number row gets the number-only editor, whether
                    # the number is typed or read from a PV -- both can be
                    # corrected. Only the free-text row keeps the plain
                    # editor.
                    if row_def.is_text:
                        table.setItemDelegateForRow(r, self._plain_delegate)
                    else:
                        table.setItemDelegateForRow(r, self._num_delegate)

                    for c, rec in enumerate(columns):
                        for line in lines:
                            reason = flags_by_stamp.get(
                                rec.stamp, {}).get(line.column, "")
                            text, held = self._cell_text(line, rec, day)
                            cell = QTableWidgetItem(text)
                            cell.setFont(value_font)
                            cell.setData(_ROLE_KEY, row_def.key)
                            cell.setData(_ROLE_COL, line.column)
                            cell.setData(_ROLE_TEXT, text)
                            cell.setData(_ROLE_HELD, held)
                            if rec.source != PENDING:
                                cell.setData(_ROLE_STAMP, rec.stamp)
                            self._paint_cell(cell, line, rec, reason, held,
                                             day=day, editable=editable)
                            table.setItem(r, self._grid_col(c, line.index),
                                          cell)

                        # A quantity that IS one number takes the whole moment;
                        # one with two numbers (BA1Loop4) leaves the third box
                        # empty, and it is given an item of its own so that it
                        # is WHITE -- Windows here is in dark mode and a cell
                        # nobody painted comes out black.
                        if len(lines) < parts:
                            for i in range(len(lines), parts):
                                spare = QTableWidgetItem("")
                                spare.setFlags(Qt.ItemFlag.ItemIsEnabled)
                                spare.setBackground(QColor(PAPER))
                                table.setItem(r, self._grid_col(c, i), spare)
                        if len(lines) == 1 and parts > 1:
                            table.setSpan(r, self._grid_col(c, 0), 1, parts)

                    # The filler at the far right, white for the same reason.
                    blank = QTableWidgetItem("")
                    blank.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    blank.setBackground(QColor(PAPER))
                    table.setItem(r, table.columnCount() - 1, blank)

                    if row_def.is_text:
                        text_rows.add(r)
                    r += 1
                if r - 1 > first:
                    table.setSpan(first, 0, r - first, 1)

            if not columns:
                head = table.horizontalHeaderItem(_FIRST_VALUE)
                if head is not None:
                    head.setText("nothing recorded")
            table._spfe_text_rows = text_rows
            table._spfe_rows = list(rows_def)
            # The moments this table is showing, so that measuring its columns
            # does not have to know which of the two tables it is looking at.
            table._spfe_columns = list(columns)
            self._fit_columns(table)
            self._measure_rows(table)
            self._fit_rows(table)
            self._update_header_band(table, columns)
        finally:
            self._fill_depth -= 1

    def _grid_col(self, moment: int, part: int) -> int:
        """Which table column one number of one moment sits in."""
        return _FIRST_VALUE + moment * self._parts() + part

    def _detail_label(self, row_def) -> str:
        """What stands in the Detail column.

        The full label, including the "(X,Y,SUM)" tail, plus the unit. With the
        numbers side by side and no column naming each of them, this line is
        what says which number is which -- the heading can only do it when
        every quantity agrees on the names.
        """
        label = row_def.label
        if not row_def.unit:
            return label
        return f"{label} ({row_def.unit})" if label else f"({row_def.unit})"

    def _update_header_band(self, table: QTableWidget,
                            columns: list[Record]) -> None:
        """Put the moment names over their own sub-columns."""
        band = getattr(table, "_spfe_band", None)
        if band is None:
            return
        parts = self._parts()
        band.set_spans([(self._grid_col(c, 0), parts, self._heading_for(rec))
                        for c, rec in enumerate(columns)])

    # ── how wide and how tall ────────────────────────────────────────────
    #
    # Everything below measures what is actually in the table and gives each
    # column and each row that much. Nothing is stretched to fill the frame.
    # The complaint this answers was a Detail column ten centimetres wide
    # around a one-centimetre label, because the slack of a wide window had
    # nowhere else to go.

    def _text_width(self, table: QTableWidget, column: int,
                    rows: range | list) -> int:
        """The widest thing in one column, measured with its own font."""
        widest = 0
        for r in rows:
            item = table.item(r, column)
            if item is None or not item.text():
                continue
            metrics = QFontMetrics(item.font())
            widest = max(widest, metrics.horizontalAdvance(item.text()))
        return widest

    def _fit_columns(self, table: QTableWidget) -> None:
        """Every column as wide as what is in it; the slack goes to the filler.

        Both label columns and every value column are measured and then held
        between a floor and a ceiling of their own. A label longer than its
        ceiling wraps onto a second line instead of widening the column, which
        is why the Detail column has a ceiling at all.

        The sub-columns of ONE moment all come out the same width. They have to:
        the moment's name is painted across all of them by the header band, and
        a name centred over three boxes of different widths does not look
        centred over anything.

        Whatever is left of the window goes into the empty column at the far
        right. That column is the whole trick: the table still fills its frame,
        so there is no bare strip beside it, and no real column has to be
        inflated to make it do so.
        """
        if table.columnCount() <= _FIRST_VALUE:
            return
        rows = range(table.rowCount())
        text_rows = getattr(table, "_spfe_text_rows", set())
        head = QFontMetrics(QFont(FONT_FAMILY, FONT_PT, QFont.Weight.Bold))
        parts = self._parts()
        moments = max(0, (table.columnCount() - 1 - _FIRST_VALUE) // parts)

        widths = [
            _clamp(self._text_width(table, 0, rows) + _CELL_PAD,
                   _GROUP_MIN, _GROUP_MAX),
            _clamp(self._text_width(table, 1, rows) + _CELL_PAD,
                   _DETAIL_MIN, _DETAIL_MAX),
        ]
        band_font = QFontMetrics(QFont(FONT_FAMILY, FONT_PT, QFont.Weight.Bold))
        for m in range(moments):
            need = 0
            widest_merged = 0
            for i in range(parts):
                c = self._grid_col(m, i)
                item = table.horizontalHeaderItem(c)
                need = max(need,
                           head.horizontalAdvance(item.text() if item else "")
                           + 16)
                for r in rows:
                    if r in text_rows:
                        continue          # the note wraps; it never sets a width
                    cell = table.item(r, c)
                    if cell is None or not cell.text():
                        continue
                    w = QFontMetrics(cell.font()).horizontalAdvance(cell.text())
                    if table.columnSpan(r, c) > 1:
                        # A single-number quantity lies across the whole moment,
                        # so it asks each sub-column for its share, not for all
                        # of it.
                        widest_merged = max(widest_merged, w)
                    else:
                        need = max(need, w + _CELL_PAD)
            if widest_merged:
                need = max(need, -(-(widest_merged + _CELL_PAD) // parts))
            # The moment's own name is painted across the sub-columns by the
            # header band, so it has to fit across them.
            shown = getattr(table, "_spfe_columns", [])
            if getattr(table, "_spfe_band", None) is not None and m < len(shown):
                title = band_font.horizontalAdvance(
                    self._heading_for(shown[m])) + 18
                need = max(need, -(-title // parts))
            width = _clamp(need, VALUE_COL_MIN, VALUE_COL_MAX)
            widths.extend([width] * parts)

        # What is left of the window is offered back to the columns, but never
        # past the ceiling each of them has: a wide window makes the table more
        # comfortable, it does not make one column of it enormous. The Detail
        # column is served first -- it is the only one here that can be too
        # short for what is in it -- and then every moment grows by the same
        # amount in every one of its sub-columns. Only then does the rest go to
        # the filler.
        room = table.viewport().width() - sum(widths) - 2
        if room > 0:
            give = min(room, _DETAIL_MAX - widths[1])
            widths[1] += give
            room -= give
        while room >= parts and moments:
            hungry = [m for m in range(moments)
                      if widths[2 + m * parts] < VALUE_COL_MAX]
            if not hungry:
                break
            step = max(1, room // (len(hungry) * parts))
            grew = False
            for m in hungry:
                i = 2 + m * parts
                give = min(step, VALUE_COL_MAX - widths[i], room // parts)
                if give <= 0:
                    continue
                for k in range(parts):
                    widths[i + k] += give
                room -= give * parts
                grew = True
            if not grew:
                break

        for c, w in enumerate(widths):
            table.setColumnWidth(c, w)
        table.setColumnWidth(table.columnCount() - 1, max(0, room))
        # The moment names are painted from these widths, so they are redrawn
        # with them -- a resize reaches _fit_columns without going through
        # _fill_table.
        band = getattr(table, "_spfe_band", None)
        if band is not None:
            band.update()

    def _measure_rows(self, table: QTableWidget) -> None:
        """How tall each row wants to be, with the columns at their real width.

        Measured after the widths are set, never before: the Notes row wraps
        inside its value column, so how tall it has to be depends on how wide
        that column turned out.
        """
        text_rows = getattr(table, "_spfe_text_rows", set())
        metrics = QFontMetrics(QFont(FONT_FAMILY, VALUE_PT))
        label_metrics = QFontMetrics(QFont(FONT_FAMILY, FONT_PT))
        line_h = metrics.height()
        parts = self._parts()
        heights: list[int] = []
        for r in range(table.rowCount()):
            # The labels first. A label longer than its column wraps rather
            # than being cut short, and a wrapped label needs the height for
            # its second line. Nothing is merged downwards any more -- one
            # quantity is one row -- so a label asks for all of what it needs.
            needed = 0
            for c in (0, 1):
                item = table.item(r, c)
                if item is None or not item.text():
                    continue
                box = label_metrics.boundingRect(
                    0, 0, max(20, table.columnWidth(c) - _CELL_PAD), 10_000,
                    int(Qt.TextFlag.TextWordWrap), item.text())
                needed = max(needed, box.height())

            if r not in text_rows:
                heights.append(max(_ROW_MIN, line_h + 8, int(needed) + 8))
                continue
            # The free-text row wraps inside its own value cell, and that cell
            # is the whole moment -- all of its sub-columns. Every moment is
            # measured at ITS width: one shared width sized the note by the
            # narrowest column in the day and made the row three times as tall
            # as it had to be.
            moments = max(0, (table.columnCount() - 1 - _FIRST_VALUE) // parts)
            for m in range(moments):
                c = self._grid_col(m, 0)
                item = table.item(r, c)
                if item is None or not item.text():
                    continue
                room = sum(table.columnWidth(c + i) for i in range(parts))
                box = metrics.boundingRect(
                    0, 0, max(40, room - _CELL_PAD), 10_000,
                    int(Qt.TextFlag.TextWordWrap), item.text())
                needed = max(needed, box.height())
            # A floor of three lines, so an empty Notes row still reads as a
            # box to write in rather than as another value cell.
            heights.append(int(max(line_h * 3 + 12, needed + 12)))
        table._spfe_row_heights = heights

    def _cell_text(self, line: _Line, rec: Record, day: date) -> tuple[str, bool]:
        """What one value cell shows, and whether that is a held default.

        Three cases, and the third is the one that needed saying:

        * a value -> the value, rounded as the field file asks;
        * nothing, on a day the default applies to -> the default, so the
          operator sees the setting that is in force and can type over it;
        * nothing, on a day it does not -> an EMPTY cell for a hand-typed row,
          a dash for a fetched one. On a hand-typed row a dash reads as a
          value; an empty box reads as a box waiting to be filled in, which is
          exactly what it is.
        """
        row_def = line.row
        if row_def.is_text:
            v = rec.values.get(line.column, "")
            return ("" if v in ("", None) else str(v)), False
        text = store.format_value(row_def, line.column, rec.values)
        if text == "-":
            if line.first and row_def.default_applies(day):
                return row_def.default, True
            if row_def.is_manual:
                return "", False
        return text, False

    def _raw_value(self, line: _Line, rec: Record, day: date):
        """The number a cell stands for, before it was rounded for the screen.

        The references are judged on this, not on the text: a value rounded to
        the nearest ten is up to five out, which at the edge of a range is the
        difference between amber and red.
        """
        raw = rec.values.get(line.column, "")
        if raw in ("", None) and line.first and line.row.default_applies(day):
            raw = line.row.default
        return raw

    def _limit_level(self, line: _Line, rec: Record, day: date):
        """The reference verdict for one cell: (level, sentence)."""
        if line.row.is_text:
            return "", ""
        entry = self._refs.get(line.column)
        if not limits.has_range(entry):
            return "", ""
        verdict = limits.judge(self._raw_value(line, rec, day), entry,
                               default_warn_pct=self._warn_pct)
        return verdict.level, verdict.reason

    def _paint_cell(self, cell: QTableWidgetItem, line: _Line, rec: Record,
                    reason: str, held: bool, *, day: date | None = None,
                    editable: bool = True) -> None:
        """One value cell's colours, tooltip and whether it can be typed into.

        Its own method because a cell is painted twice: when the table is
        built, and again after a value is saved. The two must not drift.

        A background is never set without deciding the ink with it, and the
        order of precedence is:

        * OUTSIDE its reference -- red, white text, black outline round it. The
          outline is drawn by the delegate; the item only carries the level.
        * NEARLY outside -- strong yellow, black bold text.
        * unlike the other days (the statistical remark) -- pale amber, red
          text, which is what this table has always used.
        * a cell somebody is expected to fill in -- a pale tint.

        A reference is a decision somebody made about the machine, so it wins:
        the statistical remark is only "this differs from the other days", and
        it stays in the tooltip underneath.
        """
        row_def = line.row
        typed_here = row_def.is_manual and rec.source != PENDING
        level, limit_reason = self._limit_level(line, rec, day or self._day)
        cell.setData(_ROLE_LEVEL, level)

        if level == "bad":
            # Painted by the delegate. The item still carries the pair, so a
            # copy of the table, a tooltip or a future export sees them.
            cell.setBackground(QColor(REF_BAD_BG))
            cell.setForeground(QColor(REF_BAD_INK))
        elif level == "warn":
            cell.setBackground(QColor(REF_WARN_BG))
            cell.setForeground(QColor(REF_WARN_INK))
        elif reason:
            cell.setBackground(QColor(FLAG_BG))
            cell.setForeground(QColor(FLAG_INK))
        elif typed_here:
            cell.setBackground(QColor(TYPED_BG))
            cell.setForeground(QColor(INK))
        else:
            cell.setBackground(QColor(PAPER))
            cell.setForeground(QColor(INK))

        fnt = cell.font()
        fnt.setBold(bool(reason) or bool(level))
        cell.setFont(fnt)

        tips = []
        if limit_reason:
            tips.append(limit_reason)
        elif level == "":
            entry = self._refs.get(line.column)
            described = limits.describe(entry, default_warn_pct=self._warn_pct)
            if described:
                tips.append(described)
        if reason:
            tips.append(reason)
        if held:
            tips.append("The usual value. Type over it to record a different "
                        "one for this day.")
        cell.setToolTip("\n".join(tips))

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if editable:
            # Every cell of a recorded column can be corrected, the fetched
            # ones included. A placeholder column is created by typing into it.
            flags |= Qt.ItemFlag.ItemIsEditable
        cell.setFlags(flags)

        if row_def.is_text:
            # A long note reads from the top of its cell, not from the middle
            # of a tall empty box.
            cell.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                  | Qt.AlignmentFlag.AlignTop)
        else:
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def _slot_time(self, slot: str) -> str:
        key = "slot_morning" if slot == SLOT_MORNING else "slot_evening"
        h, m = store._parse_hhmm(self.cfg.get(key))
        return f"{h:02d}:{m:02d}"

    def _heading_for(self, rec: Record) -> str:
        """What stands above a column. The scheduled ones say their hour: "the
        morning value" means nothing until you know which minute it is read
        at, and the operator was left guessing."""
        if rec.slot in (SLOT_MORNING, SLOT_EVENING):
            return f"{store.SLOT_HEADINGS[rec.slot]} ({self._slot_time(rec.slot)})"
        return rec.heading()

    def _heading_tip(self, rec: Record) -> str:
        if rec.slot in (SLOT_MORNING, SLOT_EVENING):
            when = self._slot_time(rec.slot)
            tip = (f"The values as they stood at {when}: for each quantity, the "
                   f"last reading the archiver holds before {when}.")
        else:
            tip = (f"An extra moment, recorded at {rec.when:%H:%M} on "
                   f"{rec.day:%d.%m.%Y}.")
        if rec.source == PENDING:
            tip += "\nNot recorded yet - type into it to write it down by hand."
        return tip

    def _fit_rows(self, table: QTableWidget):
        """Share the leftover height out, but only so far.

        A row still grows when there is room, so the table has no bare strip
        under its last line -- but no further than half again its own height.
        Sixteen short lines in a tall window used to become sixteen tall empty
        boxes, which is the same complaint as the ten-centimetre Detail column,
        one axis over.

        What the cap leaves over is given back by capping the TABLE itself: it
        ends where its rows end and the page's own grey carries on below, which
        reads as "the table stops here" rather than as a missing row.

        Always recomputed from the natural heights, never from what is on
        screen, or each resize would add to the last one.
        """
        nat = getattr(table, "_spfe_row_heights", None)
        if not nat or len(nat) != table.rowCount():
            return
        total = sum(nat)
        if total <= 0:
            return
        free = table.viewport().height() - total - 2
        if free <= 0:
            for r, h in enumerate(nat):
                table.setRowHeight(r, h)
            self._cap_table_height(table, total)
            return

        room = min(free, int(total * (_ROW_GROW_MAX - 1.0)))
        given = 0
        for r, h in enumerate(nat):
            share = int(room * h / total)
            given += share
            table.setRowHeight(r, h + share)
        # The rows lose up to one pixel each to rounding, and those pixels
        # show as a bare strip under the last row. They go to the tallest row,
        # which is the one that can use them.
        if given < room:
            tallest = max(range(len(nat)), key=lambda i: nat[i])
            table.setRowHeight(tallest,
                               table.rowHeight(tallest) + (room - given))
            given = room
        self._cap_table_height(table, total + given)

    def _cap_table_height(self, table: QTableWidget, content: int) -> None:
        """Stop the table below its last row.

        Only ever set when it actually changes: a widget that is given the
        maximum it already has still asks its parent to lay out again, and a
        resize is what called this in the first place -- that pair is a
        relayout loop that pins a core at 100 %.
        """
        header = table.horizontalHeader().height()
        frame = 2 * table.frameWidth()
        bar = table.horizontalScrollBar()
        want = content + header + frame + (bar.height() if bar.isVisible() else 0)
        if table.maximumHeight() != want:
            table.setMaximumHeight(want)
    def _flags_for(self, rec: Record, day: date) -> dict[str, str]:
        """Judge a stored column again on the way to the screen.

        Recomputed rather than remembered: the history grows every day, so the
        same number can stop being unusual, and a verdict frozen at write time
        would keep an old alarm on screen forever.

        Keyed by the NUMBER, not by the quantity: X, Y and SUM have a line each
        now, and marking all three because one of them is unusual is how an
        alarm stops meaning anything.
        """
        cached = self._flags.get(rec.stamp)
        if cached is not None:
            return cached
        if self._fields_error:
            return {}
        try:
            flags = recorder.judge_columns(rec, self.fields, self._rows, self.cfg)
        except Exception:
            flags = {}
        self._flags[rec.stamp] = flags
        return flags

    def _recorded_days(self) -> list[str]:
        """The days that have at least one recorded moment, newest first."""
        return sorted({r.get("date", "") for r in self._rows if r.get("date")},
                      reverse=True)

    def _refresh_day_list(self):
        """The day list of the View day window, if it has ever been opened."""
        if self._day_window is None:
            return
        days = self._recorded_days()
        counts: dict[str, int] = {}
        for r in self._rows:
            d = r.get("date", "")
            if d:
                counts[d] = counts.get(d, 0) + 1

        item = self.lst_days.currentItem()
        keep = item.data(Qt.ItemDataRole.UserRole) if item else ""

        self.lst_days.blockSignals(True)
        self.lst_days.clear()
        for d in days:
            entry = QListWidgetItem(f"{_pretty_day(d)}    {counts.get(d, 0)}")
            entry.setData(Qt.ItemDataRole.UserRole, d)
            entry.setForeground(QColor(INK))
            self.lst_days.addItem(entry)
        self.lst_days.blockSignals(False)

        self.lbl_hist_count.setText(
            f"{len(self._rows)} recorded moments over {len(days)} days")
        if not days:
            self.hist_table.clear()
            self.hist_table.setRowCount(0)
            return
        want = keep if keep in days else days[0]
        self.lst_days.setCurrentRow(days.index(want))

    def _show_history_day(self, text: str):
        if not text:
            return
        try:
            day = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return
        self._fill_table(self.hist_table, day, self._columns_for(day), editable=False)

    def _on_show_picked_day(self):
        """Take the day picked in the View day window over to the main table."""
        item = self.lst_days.currentItem()
        if item is None:
            return
        self._commit_open_editor()
        try:
            self._day = datetime.strptime(
                item.data(Qt.ItemDataRole.UserRole), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return
        self._refresh_today()
        self._day_window.hide()

    def _on_cell_clicked(self, row: int, column: int):
        """A quantity that carries a link opens it."""
        if column != 1:
            return
        lines = self._table_lines()
        if not (0 <= row < len(lines)) or not lines[row].row.url:
            return
        QDesktopServices.openUrl(QUrl(lines[row].row.url))

    # ── small helpers ────────────────────────────────────────────────────
    def _guard_fields(self) -> bool:
        if self._fields_error:
            QMessageBox.warning(self, "SPFE Values",
                                f"spfe_fields.json cannot be read:\n\n{self._fields_error}")
            return False
        if not self.fields.pv_names:
            QMessageBox.information(
                self, "SPFE Values",
                "No PV names are filled in yet.\n\nOpen spfe_fields.json and put the "
                "PV name of each quantity into its \"pvs\" list. Until then every "
                "value cell stays empty.")
            return False
        return True

    def _show_share_path(self, root: str):
        """The path in a 270 px panel. Shortened in the middle, never at the
        end: the machine name at the front and the folder at the back are the
        two halves that say which share this is."""
        folder = str(store.share_dir(root, self.cfg))
        self.lbl_share.setText("Writing to\n" + _elide_middle(folder, 46))
        self.lbl_share.setToolTip(folder)
        self.lbl_share.setStyleSheet(f"color:{INK_MUTED};")
        self.btn_open_share.setEnabled(not self._working)

    def _on_open_share(self):
        """Open the shared folder in Explorer."""
        folder = self.store.share_folder
        if folder is None:
            self._set_status("The shared folder has not answered yet.", WARN_RED)
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass                       # opening it may still work
        try:
            _open_path(str(folder))
            self._set_status(f"Opened {folder}.", INK_MUTED)
        except Exception as exc:
            self._set_status(f"Could not open the folder: {exc}", WARN_RED)

    def _set_busy(self, busy: bool):
        self._working = busy
        for b in (self.btn_now, self.btn_morning, self.btn_evening,
                  self.btn_update, self.btn_sync, self.btn_fill_day,
                  self.btn_pick_days):
            b.setEnabled(not busy)
        self.btn_open_share.setEnabled(
            not busy and self.store.share_root != "")

    def _set_status(self, message: str, colour: str = INK_MUTED):
        """The status line, Chiller Log style: a plain line under the table,
        grey while there is nothing to say and coloured when there is."""
        self.lbl_status.setText(message)
        self.lbl_status.setStyleSheet(
            f"color:{colour};padding:3px 2px;"
            + ("font-weight:700;" if colour == WARN_RED else ""))

    def _append_log(self, message: str):
        stamp = datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{stamp}] {message}")

    # ── shutdown ─────────────────────────────────────────────────────────
    def shutdown(self):
        """Stop the timers and get the last typed value out of the way safely.

        Called from the window's closeEvent, and from CSS Logger's closeEvent
        once this is a tab there -- a tab that leaves a timer running keeps the
        whole suite awake.

        The order matters. A cell may still be open, and a value may still be
        waiting for the 400 ms writer -- which runs on a daemon thread and dies
        with the interpreter. So the editor is committed, whatever is queued is
        written to the local queue file (instant, cannot block, and replayed by
        the next Sync), and only then is the writer given a bounded moment to
        finish.
        """
        try:
            self._slot_timer.stop()
            self._save_timer.stop()
        except Exception:
            pass
        try:
            self._commit_open_editor()
        except Exception:
            pass
        try:
            with self._pending_lock:
                left = {s: dict(p) for s, p in self._pending.items()}
            if left:
                self.store.queue_edits(left)
                self._append_log(f"{sum(len(p) for p in left.values())} typed "
                                 "value(s) are queued and will be sent on the "
                                 "next start.")
        except Exception:
            pass
        for _ in range(20):            # at most two seconds, never more
            if not self._saving:
                break
            QApplication.processEvents()
            time.sleep(0.1)
