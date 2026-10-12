"""Laser settings — the Limits tab: one hall at a time, and its limits.

Called "Settings" on the tab bar until 1.10.2026, when the Font tab took that
name and this one became Limits. The MODULE and the class keep their names —
`main.py` loads every tab by module and class name and the build config lists
both, so a rename would cost three more files and buy nothing anybody can see.

Pick a hall at the top; the table below is that hall's. There is ONE list of
channels shared by all five halls, so a channel added here appears in every
hall — what belongs to the hall alone is the two limit columns:

    Min, Max   the range that counts as right for THIS hall

Everything else in a row — Show, the channel name, what it is called on the
first tab, its unit — is the same wherever you look at it. Show in particular:
it is ONE switch, and unticking it in E2 unwatches the channel in Int and E5
too. Asked for in those words.

The limits are edited IN THE TABLE, by clicking a cell and typing, because
nudging a limit is the one thing this tab is asked to make easy. An empty box
means that side is not limited; it never means zero.

The live reading sits in the last column while you type, so a limit can be set
against the number the machine is actually holding rather than from memory.

FORMULAS
--------
Every row wears a LETTER, and a row can be a formula over those letters instead
of a channel: `A * 0.749` watches the energy after the compressor without
anybody having to archive it. The letters are the rows' positions, so they move
as the list is edited — what a formula MEANS is stored as the rows it points
at, and the box is redrawn from that. A formula may use any row on the list, and
a formula that cannot be worked out wears a red box and says why it cannot.

THE ORDER IS THE OPERATOR'S (12.10.2026): rows are dragged into place in the
table (`_RowTable`). The order is the list's — the Overview and the letters
follow it. Shown rows stay above the "Not shown" line and unticked ones below
it; a drag moves a row within its own part.
"""

from __future__ import annotations

import re
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup,
                               QDialog, QDialogButtonBox, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QScrollArea, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import ls_core as C
import ls_cpva as A
import ls_dm as D
import ls_ui as U

# The gate valve box says three things and no sentences: the channel, the button
# that finds one, and what the channel is reading. The line under it — "shooting
# into this hall", "this hall's limits are in force" — was taken off on
# 30.9.2026: the five squares on the Overview and their tooltips say all of that
# already, and they say it for all five halls at once.

# Scan min and Scan max (8.10.2026) belong to the ROW, not to the hall picked
# at the top: one scan pair for every hall, the user's choice. Whether they are
# in force is the Overview's one Scanning box (12.10.2026), not a column here.
_COLS = ["Show", "Alarm", "Letter", "Channel or formula",
         "Name on the first tab", "Unit", "Min", "Max", "Scan min",
         "Scan max", "Reading now"]
(_C_SHOW, _C_ALARM, _C_LETTER, _C_PV, _C_NAME, _C_UNIT, _C_MIN,
 _C_MAX, _C_SMIN, _C_SMAX, _C_NOW) = range(len(_COLS))
_CHECKS = (_C_SHOW, _C_ALARM)

_EDITABLE = {_C_PV, _C_NAME, _C_UNIT, _C_MIN, _C_MAX, _C_SMIN, _C_SMAX}

# The shot counter's own table, under the channels' one.
_SCOLS = ["Show", "Letter", "Name on the first tab",
          "Start again from 1 after", "Text size", "Bold", "Shots now"]
(_S_SHOW, _S_LETTER, _S_NAME, _S_RESET, _S_SIZE, _S_BOLD,
 _S_NOW) = range(len(_SCOLS))
# The tick box, the letter and the two limits sit in the middle of their own
# narrow columns, heading included — a right-aligned number under a
# left-aligned heading looks like it belongs to the column next door.
_CENTRED = {_C_SHOW, _C_ALARM, _C_LETTER, _C_MIN, _C_MAX, _C_SMIN,
            _C_SMAX, _C_NOW}

# What a formula's box says when it is wrong: the light red band and dark red
# ink the Overview uses for an out-of-range row. Never a dark ground with black
# text on it.
_BAD_BAND, _BAD_INK = U.FIRED_BAND

# The "Not shown" line between the watched rows and the unticked ones: a grey
# band a shade darker than the heading, with dark ink on it.
_SEP_BAND, _SEP_INK = "#e4e7eb", "#16202c"

_FORMULA_TIP = (
    "A formula works its number out from other rows on the list — above or "
    "below it — which it names by their letters:\n\n"
    "    A * 0.749          three quarters of what row A reads\n"
    "    (A + B) / 2        the two of them, averaged\n\n"
    "+ − * / and brackets, and abs, min, max, round and math.\n"
    "A decimal comma is fine: 0,749 is stored as 0.749.\n"
    "Inside brackets it is NOT — there a comma separates the arguments, so\n"
    "write min(A, 0.749) and never min(A, 0,749).\n"
    "The letters follow the rows, so moving a row rewrites the box itself.")

_HALL_BTN_QSS = (
    "QPushButton { padding: 7px 20px; border: 1px solid #b6b6b6;"
    " border-radius: 3px; background: #efefef; color: #16202c;"
    " font-weight: 700; font-size: 13px; }"
    "QPushButton:hover { background: #d9e8ff; }"
    "QPushButton:checked { background: #2d7dff; color: #ffffff;"
    " border: 1px solid #1f5fc4; }"
)


class _RowTable(QTableWidget):
    """The limits table, with rows dragged into a new order.

    Qt is never allowed to move the items itself: `dropEvent` works out which
    table rows were dragged and where to, hands that to `on_drop`, and answers
    the drag with IgnoreAction — a MoveAction would make Qt clear the source
    cells behind our back. The tab rewrites the list and rebuilds the table.
    """

    def __init__(self, rows, cols, parent=None):
        super().__init__(rows, cols, parent)
        self.on_drop = None             # (table rows dragged, drop row) → None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def drop_row_at(self, y):
        """The table row a drop at viewport height `y` goes in front of:
        the lower half of a row means after it."""
        t = self.rowAt(y)
        if t < 0:
            return self.rowCount()
        rect = self.visualRect(self.model().index(t, 0))
        return t + 1 if y > rect.center().y() else t

    def dropEvent(self, ev):
        if ev.source() is not self or self.on_drop is None:
            ev.ignore()
            return
        sources = sorted({i.row() for i in self.selectedIndexes()})
        target = self.drop_row_at(ev.position().toPoint().y())
        ev.setDropAction(Qt.DropAction.IgnoreAction)
        ev.accept()
        if sources:
            self.on_drop(sources, target)


class ChannelPicker(QDialog):
    """Type a few words, get the channels that carry all of them.

    Nothing has to be at the start and nothing has to touch: "chl temp" finds
    `L3-UTIL-CHL03-001:Temp`. A fragment that fits several channels returns
    those channels and says how many — it is a filter, never an error.
    """

    def __init__(self, parent=None, preset=""):
        super().__init__(parent)
        self.setWindowTitle("Find a channel")
        self.setModal(True)
        self.resize(620, 520)
        U.paint_dialog(self)
        self.chosen = None
        self._names = []

        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        self._search = QLineEdit(preset)
        self._search.setPlaceholderText("a few words from the name — chl temp")
        self._search.textChanged.connect(self._refilter)
        lay.addWidget(self._search)

        self._count = QLabel("")
        self._count.setStyleSheet(f"color: {U.QUIET};")
        lay.addWidget(self._count)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #ffffff; color: #111111;"
            " border: 1px solid #b0b0b0; }"
            "QListWidget::item { padding: 2px 3px; }"
            "QListWidget::item:selected { background: #cfe4fb; color: #111111; }"
            "QListWidget::item:hover { background: #eef5fd; }"
            + U.SCROLLBAR_QSS)
        self._list.setFont(U.mono_font(9))
        self._list.itemDoubleClicked.connect(lambda _i: self._accept())
        lay.addWidget(self._list, stretch=1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Use this channel")
        ok.setStyleSheet(U.PRIMARY_QSS)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(U.BTN_QSS)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        QTimer.singleShot(0, self._load)

    def _load(self):
        """Ask for the list on a worker thread and look for the answer every
        tenth of a second. On the GUI thread a slow or unreachable archiver held
        the whole window for up to six seconds — long enough for Windows to grey
        it out as "Not Responding". Polled rather than signalled, so no Qt
        object ever crosses a thread, and a dialog closed meanwhile simply
        stops looking (its timer dies with it)."""
        self._count.setText("Asking the archiver for the channel list…")
        box = {}

        def work():
            try:
                box["names"] = A.fetch_channels(timeout=6.0)
            except Exception as exc:
                box["error"] = exc

        threading.Thread(target=work, name="channel-list", daemon=True).start()
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(lambda: self._loaded(box))
        self._poll.start()

    def _loaded(self, box):
        if "names" not in box and "error" not in box:
            return                          # still asking
        self._poll.stop()
        exc = box.get("error")
        if exc is None:
            self._names = box["names"]
        else:
            msg, hint = C.readable_pv_error("the channel list", exc)
            self._count.setText(msg)
            self._count.setToolTip(hint or "")
            self._empty_row("The channel list could not be read — "
                            "type the name by hand instead.")
            return
        self._refilter()

    def _empty_row(self, text):
        """An empty list gets one greyed, unclickable line, never a blank box."""
        self._list.clear()
        row = QListWidgetItem(text)
        row.setForeground(QColor("#8a8a8a"))
        row.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(row)

    def _refilter(self):
        if not self._names:
            return
        query = self._search.text()
        if not query.strip():
            self._count.setText(f"{len(self._names)} channels — type a few "
                                f"words from the one you want")
            self._empty_row("Type something to search.")
            return
        matches, total = C.search_channels(self._names, query)
        if not matches:
            self._count.setText(f"Nothing carries all of those words "
                                f"({len(self._names)} channels searched)")
            self._empty_row("No channel carries all of those words.")
            return
        self._count.setText(f"showing {len(matches)} of {total}"
                            if total > len(matches) else f"{total} found")
        self._list.clear()
        for name in matches:
            self._list.addItem(QListWidgetItem(name))
        self._list.setCurrentRow(0)

    def _accept(self):
        row = self._list.currentItem()
        if row is None or not (row.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        self.chosen = row.text()
        self.accept()


class SettingsTab(QWidget):
    """The hall switcher, that hall's shooting channel, and its limits."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._hall = win.active_hall()
        self._loading = False
        self._rows_on_screen = []       # the list's order
        self._table_rows = []           # the table's order; None = "Not shown"
        self._build()
        self.reload()

    # ── building ───────────────────────────────────────────────────────────
    def _build(self):
        # In a scroller, like the Settings tab. The window has no minimum size
        # any more (1.10.2026, "úplně bez minima"), and Qt takes a window's
        # minimum from the widest thing any of its tabs demands — the hall
        # buttons, the valve box and the four action buttons on this tab were
        # that widest thing. Scrolled, the tab asks for nothing and the window
        # can be squeezed to whatever he likes.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {U.PAPER};"
                             " border: 0px; }" + U.SCROLLBAR_QSS)
        scroll.viewport().setStyleSheet(f"background: {U.PAPER};")
        page = QWidget()
        page.setObjectName("limitsPage")
        page.setStyleSheet(f"#limitsPage {{ background: {U.PAPER}; }}")
        outer.addWidget(scroll)
        scroll.setWidget(page)
        self._page = scroll

        root = QVBoxLayout(page)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        switch = QHBoxLayout()
        switch.setSpacing(6)
        switch.addWidget(U.section_label("Hall"))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._hall_buttons = {}
        for hall in C.HALLS:
            btn = QPushButton(hall)
            btn.setCheckable(True)
            btn.setStyleSheet(_HALL_BTN_QSS)
            btn.setToolTip(f"Show and edit the limits that apply while we are "
                           f"shooting into {hall}.")
            btn.clicked.connect(lambda _c, h=hall: self._pick_hall(h))
            self._group.addButton(btn)
            switch.addWidget(btn)
            self._hall_buttons[hall] = btn
        switch.addStretch(1)
        root.addLayout(switch)

        self._gate_box = QGroupBox("Gate valve")
        gate = QGridLayout(self._gate_box)
        gate.setHorizontalSpacing(6)
        gate.setVerticalSpacing(4)

        self._gate_caption = QLabel("PV for the valve")
        gate.addWidget(self._gate_caption, 0, 0)
        self._gate_edit = QLineEdit()
        self._gate_edit.setPlaceholderText(
            "the channel that reads the valve's position")
        # 160, not 340: a minimum width here is a minimum width for the whole
        # window, and the box stretches to fill whatever the window has anyway
        # (`gate.setColumnStretch(1, 1)` below).
        self._gate_edit.setMinimumWidth(160)
        self._gate_edit.editingFinished.connect(self._gate_typed)
        gate.addWidget(self._gate_edit, 0, 1)
        find = U.button("Find", icon_name="eye",
                        tip="Search the archiver's channel list.")
        find.clicked.connect(self._find_gate)
        gate.addWidget(find, 0, 2)
        self._gate_now = QLabel("")
        # No minimum width. It held 190 px so that "value=1 : open" never
        # wrapped, and that 190 was part of the window's own minimum.
        self._gate_now.setStyleSheet(f"color: {U.QUIET};")
        gate.addWidget(self._gate_now, 0, 3)
        gate.setColumnStretch(1, 1)

        # Which valve position means "shooting into this hall", and the name of
        # the fallback channel, are a TOOLTIP and not a line on screen. They are
        # reference material — read once, true for ever. Put on all three of the
        # caption, the box and the group box, so it is found wherever the
        # operator happens to point.
        root.addWidget(self._gate_box)

        root.addWidget(U.hsep())

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self._btn_add = U.button("Add channel", icon_name="plus", primary=True,
                                 tip="Search the archiver and put a channel on "
                                     "the list. It is added for every hall.")
        self._btn_add.clicked.connect(self._add)
        tools.addWidget(self._btn_add)
        self._btn_formula = U.button("Add formula", icon_name="plus",
                                     tip="Put a row on the list that works its "
                                         "number out of the rows above it.\n\n"
                                         + _FORMULA_TIP)
        self._btn_formula.clicked.connect(self._add_formula)
        tools.addWidget(self._btn_formula)
        self._btn_find = U.button("Change channel", icon_name="pencil",
                                  tip="Search the archiver and put a different "
                                      "channel name in the picked row.")
        self._btn_find.clicked.connect(self._change_channel)
        tools.addWidget(self._btn_find)
        self._btn_remove = U.button("Remove", icon_name="trash", danger=True,
                                    tip="Take the picked channels off the list "
                                        "— in every hall, not just this one.")
        self._btn_remove.clicked.connect(self._remove)
        tools.addWidget(self._btn_remove)
        tools.addStretch(1)
        # No sentence about what belongs to a hall and what does not. It was
        # here, it was taken off on 30.9.2026, and both ReadMes say it — a
        # paragraph that is true for ever does not belong over the work.
        root.addLayout(tools)

        self._table = _RowTable(0, len(_COLS))
        self._table.on_drop = self.move_rows
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setStyleSheet(U.TABLE_QSS)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        for col in _CHECKS:
            self._table.setItemDelegateForColumn(
                col, U.CenteredCheckDelegate(self._table))
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self._table.itemChanged.connect(self._cell_edited)
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        # The leftover width is shared by the two text columns, not given to
        # the last one: channel names and the operator's own names are the long
        # things here, and a "Reading now" column stretched across half the
        # window is a grey strip with a heading on it.
        self._fitter = U.ColumnFitter(self._table,
                                      stretch_col=(_C_PV, _C_NAME))
        # As tall as its rows, no scrollbar of its own: the shot counter's
        # table sits straight under the last row, and the page scrolls.
        self._table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._table)

        # The shot counter: a table of its own at the very bottom, with
        # headings that say what its settings are (5.10.2026 — "ať se to
        # nejmenuje min a max nebo unit"). One row.
        self._shots = QTableWidget(1, len(_SCOLS))
        self._shots.setHorizontalHeaderLabels(_SCOLS)
        self._shots.setStyleSheet(U.TABLE_QSS)
        self._shots.verticalHeader().setVisible(False)
        self._shots.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._shots.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for col in (_S_SHOW, _S_BOLD):
            self._shots.setItemDelegateForColumn(
                col, U.CenteredCheckDelegate(self._shots))
        self._shots.itemChanged.connect(self._shot_edited)
        self._shot_fitter = U.ColumnFitter(self._shots, stretch_col=_S_NAME)
        root.addWidget(self._shots)
        root.addStretch(1)

        self._menu = U.RowMenu(self._table, [
            ("Change the channel", "pencil", self._change_channel),
            None,
            (lambda n: f"Remove {n} channel{'' if n == 1 else 's'}",
             "trash", self._remove),
        ])

        # No counting line under the table. "4 rows on the list, 3 watched, 4
        # with a limit set for Int" was here until 1.10.2026 and was taken off
        # in those words — "zabírá tam akorát místo". Every one of those three
        # numbers can be counted off the table itself, which is right above it.

    # ── the hall ───────────────────────────────────────────────────────────
    def _pick_hall(self, hall):
        if hall == self._hall:
            return
        self._hall = hall
        self._win.set_active_hall(hall)
        # Only what belongs to a hall changes: the gate box and the Min / Max
        # cells. The table is not refilled, so nothing jumps — not the columns,
        # not the scroll position, not the selection.
        self._loading = True
        try:
            self._fill_gate()
            for t, row in enumerate(self._table_rows):
                if row is None:
                    continue
                entry = C.limits_for(row, self._hall)
                for col, key in ((_C_MIN, "min"), (_C_MAX, "max")):
                    cell = self._table.item(t, col)
                    if cell is not None:
                        cell.setText(C.level_text(entry.get(key)))
        finally:
            self._loading = False
        self.refresh()

    def hall(self):
        return self._hall

    def showEvent(self, ev):
        # `refresh` skips while the tab is behind another one: catch up now.
        super().showEvent(ev)
        self.refresh()

    # ── filling ────────────────────────────────────────────────────────────
    def _fill_gate(self):
        """The hall buttons and the gate valve box, for `self._hall`. Called
        under `self._loading`."""
        for hall, btn in self._hall_buttons.items():
            btn.setChecked(hall == self._hall)
        gate = C.gate_for(self._win.shooting(), self._hall)
        self._gate_box.setTitle(f"Gate valve of {self._hall}")
        self._gate_edit.setText(gate.get("open") or "")
        word = "shut" if C.shoot_when(self._hall) == C.CLOSED else "open"
        tip = (f"We are shooting into {self._hall} while this valve is "
               f"{word}.\n\nWhat a 1 on this channel means comes from the "
               f"channel's own name: an _IN_OPEN_POSITION channel reads 1 "
               f"while the valve is open, an _IN_CLOSED_POSITION one reads "
               f"1 while it is shut.\n\nIf it cannot be read, the closed "
               f"switch is used instead: "
               f"{C.closed_channel(self._hall) or '(none)'} — that name is "
               f"in the program and cannot be changed here.")
        for widget in (self._gate_box, self._gate_caption, self._gate_edit):
            widget.setToolTip(tip)

    def reload(self):
        # A refill must not move the page: where it was scrolled to and which
        # cell was current are put back afterwards, and the whole refill is
        # one paint.
        page = getattr(self, "_page", None)
        page_y = page.verticalScrollBar().value() if page is not None else None
        current = (self._table.currentRow(), self._table.currentColumn())
        self.setUpdatesEnabled(False)
        try:
            self._reload()
            if 0 <= current[0] < self._table.rowCount():
                self._table.setCurrentCell(*current)
            if page_y is not None:
                bar = page.verticalScrollBar()
                bar.setValue(page_y)
                # Again once the page has been laid out at its new height —
                # until then the bar's range may still be the old one.
                QTimer.singleShot(0, bar, lambda: bar.setValue(page_y))
        finally:
            self.setUpdatesEnabled(True)

    def _reload(self):
        self._loading = True
        try:
            self._fill_gate()
            rows = list(self._win.rows())
            # Two orders. `_rows_on_screen` is the LIST's order — the letters
            # and the formulas go by it, so it is never changed here.
            # `_table_rows` is the TABLE's: the shown rows first, then a
            # "Not shown" line, then the unticked ones, so the rows nobody is
            # watching are out of the way (2.10.2026). None is that line.
            self._rows_on_screen = rows
            # The shot counter is not in this table: it has a table of its own
            # under it, with its own headings (5.10.2026).
            shown = [r for r in rows if C.is_shown(r) and not C.is_shot_row(r)]
            hidden = [r for r in rows
                      if not C.is_shown(r) and not C.is_shot_row(r)]
            self._table_rows = shown + ([None] + hidden if hidden else [])
            pos = {id(r): i for i, r in enumerate(rows)}
            self._table.clearSpans()
            self._table.setRowCount(0)
            self._table.setRowCount(len(self._table_rows))
            for t, row in enumerate(self._table_rows):
                if row is None:
                    self._fill_separator(t)
                else:
                    self._fill_row(t, row, pos[id(row)])
            if not self._table_rows:
                U.empty_table_note(
                    self._table,
                    "No channels yet — press Add channel to put one on the list.")
            self._fill_shots(rows, pos)
        finally:
            self._loading = False
        self._sync_buttons()
        self._fitter.fit()
        self._shot_fitter.fit()
        self._fit_heights()
        self.refresh()

    def _fill_separator(self, t):
        """The "Not shown" line: one cell across the table, grey band, dark
        ink, nothing to click. The rows under it are on the list but unticked."""
        self._table.setSpan(t, 0, 1, len(_COLS))
        cell = QTableWidgetItem("Not shown")
        cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
        cell.setBackground(QColor(_SEP_BAND))
        cell.setForeground(QColor(_SEP_INK))
        font = cell.font()
        font.setBold(True)
        cell.setFont(font)
        cell.setToolTip("The rows below are on the list, but Show is unticked: "
                        "they are not on the first tab and make no hall blink. "
                        "Tick Show to move one back up.")
        self._table.setItem(t, 0, cell)

    def _fill_row(self, i, row, pos=None):
        """Table row `i` from `row`, whose place in the LIST is `pos` — the
        letter is the list's, which is not the table's once rows are split."""
        pos = i if pos is None else pos
        entry = C.limits_for(row, self._hall)
        # `is_formula_row`: a formula whose box is still empty is drawn as
        # "= " and not as an empty channel name, or there is nothing on screen
        # to tell the two kinds of row apart.
        formula = C.is_formula_row(row)
        if formula:
            what = "= " + C.expr_to_display(row, self._rows_on_screen)
        else:
            what = row.get("pv") or ""
        values = {
            _C_SHOW: "",
            _C_ALARM: "",
            _C_LETTER: C.row_letter(pos),
            _C_PV: what,
            _C_NAME: row.get("name") or "",
            _C_UNIT: row.get("unit") or "",
            _C_MIN: C.level_text(entry.get("min")),
            _C_MAX: C.level_text(entry.get("max")),
            _C_SMIN: C.level_text(row.get("scan_min")),
            _C_SMAX: C.level_text(row.get("scan_max")),
            _C_NOW: "",
        }
        for col, text in values.items():
            cell = QTableWidgetItem(text)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if col in _EDITABLE:
                flags |= Qt.ItemFlag.ItemIsEditable
            if col == _C_SHOW:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                cell.setCheckState(Qt.CheckState.Checked if C.is_shown(row)
                                   else Qt.CheckState.Unchecked)
                cell.setToolTip("Watched, in every hall — this one switch is "
                                "the whole program. Unticked, the channel "
                                "leaves the first tab and can make no hall "
                                "blink.\n\nMin and Max are what belongs to one "
                                "hall.")
            elif col == _C_ALARM:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                cell.setCheckState(Qt.CheckState.Checked if C.is_alarmed(row)
                                   else Qt.CheckState.Unchecked)
                cell.setToolTip("Ticked: out of range makes the hall blink. "
                                "Unticked: the number stays on the first tab, "
                                "but nothing is reported — for a scan that "
                                "leaves the limits on purpose. Tick it again "
                                "when the scan is over.\n\nIn every hall.")
            cell.setFlags(flags)
            if col in _CENTRED:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == _C_PV:
                cell.setFont(U.mono_font(8))
            self._table.setItem(i, col, cell)
        self._table.item(i, _C_LETTER).setToolTip(
            f"This row is {C.row_letter(pos)} — the name a formula calls it by.")
        self._table.item(i, _C_MIN).setToolTip(
            "Empty means no lower limit. The limit itself counts as in range.")
        self._table.item(i, _C_MAX).setToolTip(
            "Empty means no upper limit. The limit itself counts as in range.")
        self._mark_scan(i, row)
        self._mark_formula(i, row)
        if C.is_dm_row(row):
            self._mark_dm(i)
        # Every cell can be grabbed to drag the row (`_RowTable`). Last, after
        # the marks above have set their own flags.
        for col in range(len(_COLS)):
            cell = self._table.item(i, col)
            if cell is not None:
                cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsDragEnabled)

    def _mark_dm(self, i):
        """The DM settings row: no channel, no unit, no limits to type. What
        it says and when it is a cross is fixed (`ls_core.dm_judge`)."""
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        rule = (f"Which saved set-up the deformable mirror is on, read off the "
                f"Set-up grid by OPR1 (DM settings tab).\n\n"
                f"A cross, and the hall blinks, when a fresh SBW4 shot "
                f"contradicts it:\n"
                f"    over {C.DM_SPLIT_J:g} J while on a set-up not ticked "
                f"High power\n"
                f"    {C.DM_SPLIT_J:g} J or less — or the "
                f"{C.DM_SHUTTER_NAME} shutter open "
                f"with no shot over {C.DM_SPLIT_J:g} J — while on a set-up not "
                f"ticked Low power\n"
                f"Which set-ups are ticked is chosen on the DM settings tab.\n"
                f"No fresh shot: a tick.")
        pv = self._table.item(i, _C_PV)
        pv.setText("mirror set-up, from OPR1")
        pv.setFont(self._table.font())
        for col in (_C_PV, _C_UNIT, _C_MIN, _C_MAX):
            cell = self._table.item(i, col)
            cell.setFlags(base)
            cell.setToolTip(rule)

    def _mark_scan(self, i, row):
        """The two scan limits of table row `i`: editable on every row but the
        built-in ones, which have no limits at all."""
        lo = self._table.item(i, _C_SMIN)
        hi = self._table.item(i, _C_SMAX)
        if lo is None or hi is None:
            return
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if C.can_scan(row):
            for cell, key, side in ((lo, "scan_min", "lower"),
                                    (hi, "scan_max", "upper")):
                cell.setFlags(base | Qt.ItemFlag.ItemIsEditable)
                cell.setText(C.level_text(row.get(key)))
                cell.setToolTip(f"The {side} limit while Scanning is ticked "
                                f"on the Overview — the same in every hall. "
                                f"Empty means no {side} limit; with both "
                                f"empty this row keeps Min and Max while "
                                f"scanning.")
            return
        for cell in (lo, hi):
            cell.setFlags(base)
            cell.setText("")
            cell.setToolTip("This row has no limits.")

    def _mark_formula(self, i, row):
        """A formula's box: its tooltip, and red when it cannot be worked out.

        The tooltip spells out every letter, because a formula nobody can read
        back is worse than no formula at all.
        """
        cell = self._table.item(i, _C_PV)
        if cell is None:
            return
        if not C.is_formula_row(row):
            cell.setData(Qt.ItemDataRole.BackgroundRole, None)
            cell.setData(Qt.ItemDataRole.ForegroundRole, None)
            cell.setToolTip("The archived channel this row reads.")
            return
        if not C.is_formula(row):
            # A formula nobody has typed into yet. Not red: it is not wrong,
            # it is new, and a box that is red before the operator has had a
            # chance to type teaches them to ignore red boxes.
            cell.setData(Qt.ItemDataRole.BackgroundRole, None)
            cell.setData(Qt.ItemDataRole.ForegroundRole, None)
            cell.setToolTip("Type the formula here.\n\n" + _FORMULA_TIP)
            return
        lines = []
        letters = C.letters_for(self._rows_on_screen)
        by_id = {int(r.get("id", 0)): r for r in self._rows_on_screen}
        # The tooltip is written in TODAY's letters, the ones on screen, so the
        # bindings are turned round into {letter now → the row it means}.
        means = {letters[int(rid)]: by_id[int(rid)]
                 for rid in (row.get("bind") or {}).values()
                 if int(rid) in by_id}
        for letter in C.expr_vars(C.expr_to_display(row, self._rows_on_screen)):
            src = means.get(letter)
            if src is None:
                lines.append(f"{letter} = ?   (no such row)")
            else:
                lines.append(f"{letter} = {C.row_label(src)}"
                             + (f"   {src.get('pv')}" if src.get("pv") else ""))
        trouble = C.formula_problems(row, self._rows_on_screen)
        if trouble:
            cell.setBackground(QColor(_BAD_BAND))
            cell.setForeground(QColor(_BAD_INK))
            lines = trouble + ([""] + lines if lines else [])
        else:
            cell.setData(Qt.ItemDataRole.BackgroundRole, None)
            cell.setData(Qt.ItemDataRole.ForegroundRole, None)
        cell.setToolTip("\n".join(lines + ["", _FORMULA_TIP]))

    def refresh(self):
        """A pass came back: the live column, and the gate valve's own state."""
        # Nobody can see this tab while another one is in front: skip the work,
        # and catch up the moment it is shown (`showEvent`). Only while the
        # window itself is up — a window built and never shown (the tests, the
        # pictures) still wants the column filled.
        if self.window().isVisible() and not self.isVisible():
            return
        engine = self._win.engine()
        readings = engine.readings()
        last_ok = engine.last_ok()
        now = time.time()

        gate = C.gate_for(self._win.shooting(), self._hall)
        word, colour = self._switch_word(gate, readings, last_ok, now)
        if self._gate_now.text() != word:
            self._gate_now.setText(word)
        # A stylesheet is a re-polish: only when the colour really changes.
        if getattr(self, "_gate_colour", None) != colour:
            self._gate_colour = colour
            self._gate_now.setStyleSheet(f"color: {colour};")
        # What it MEANS for the hall is not said here. `ls_core.hall_state`'s
        # sentence is on the hall's square on the Overview, where all five halls
        # say it at once.
        tip = C.hall_state(self._win.shooting(), self._hall,
                           readings, last_ok, now, engine.screen())[1]
        if self._gate_now.toolTip() != tip:
            self._gate_now.setToolTip(tip)

        counter = getattr(self._win, "shots", None)
        values = C.row_values(self._rows_on_screen, readings, last_ok, now,
                              shots=counter().shown if counter else None)
        self._loading = True
        try:
            for i, row in enumerate(self._table_rows):
                if row is None:
                    continue                # the "Not shown" line
                cell = self._table.item(i, _C_NOW)
                if cell is None:
                    continue
                value = values.get(int(row.get("id", 0)))
                if C.is_dm_row(row):
                    dm = getattr(engine, "dm", None)
                    title, _power, detail = D.mirror_now(
                        dm() if dm is not None else None, now)
                    cell.setText(title)
                    cell.setToolTip(detail)
                elif value is None or value.value is None:
                    cell.setText("—")
                    cell.setToolTip((value.problem if value else "Not read yet.")
                                    or "Not read yet.")
                else:
                    cell.setText(C.value_text(value.value, row.get("unit") or ""))
                    cell.setToolTip("")
            now_cell = (self._shots.item(0, _S_NOW)
                        if self._shots.rowCount() else None)
            if now_cell is not None:
                text = str(counter().shown) if counter else "—"
                if now_cell.text() != text:
                    now_cell.setText(text)
        finally:
            self._loading = False

    # ── editing ────────────────────────────────────────────────────────────
    def _cell_edited(self, cell):
        if self._loading:
            return
        i, col = cell.row(), cell.column()
        if i >= len(self._table_rows) or self._table_rows[i] is None:
            return
        row = self._table_rows[i]
        entry = C.limits_for(row, self._hall)
        if col == _C_SHOW:
            # A rebuild, not a repaint: Show decides whether the row is on the
            # first tab at all, so the table there has to be built again. With
            # only a repaint the row stayed on the Overview until the next
            # restart, which is what was reported on 30.9.2026.
            #
            # Deferred by one turn of the event loop, because a rebuild clears
            # and refills THIS table too and we are inside a handler for one of
            # its own items — deleting the item Qt is still holding a pointer to
            # is how a table rebuilt from its own `itemChanged` takes the
            # program with it.
            # Only the two tabs whose ROWS depend on Show — `rows_changed`.
            row["show"] = cell.checkState() == Qt.CheckState.Checked
            QTimer.singleShot(0, self._rows_changed)
            return
        elif col == _C_ALARM:
            row["alarm"] = cell.checkState() == Qt.CheckState.Checked
        elif col == _C_PV and C.is_builtin_row(row):
            return                          # it reads no channel
        elif col == _C_PV:
            typed = cell.text().strip()
            # `is_formula_row`, not `is_formula`: a formula row that has not
            # been typed into yet is still a formula row, and what goes into
            # this box must not become a channel name.
            if C.is_formula_row(row) or typed.startswith("="):
                # A formula. The letters are resolved to the ROWS they mean
                # here and now, so the expression survives the list moving
                # under it.
                expr, bind = C.expr_from_display(typed, self._rows_on_screen)
                row["expr"], row["bind"] = expr, bind
                # A plain row turned into a formula by typing a leading "=":
                # the channel it used to read belongs to nobody now, and its
                # remembered look-back window would sit in the engine for the
                # rest of the day.
                if row.get("pv"):
                    self._win.engine().forget(row["pv"])
                row["pv"] = ""
                self._loading = True
                try:
                    cell.setText("= " + C.expr_to_display(row,
                                                          self._rows_on_screen))
                finally:
                    self._loading = False
                self._mark_formula(i, row)
            else:
                old = row.get("pv") or ""
                if typed != old:
                    row["pv"] = typed
                    # The old name's remembered look-back window and its last
                    # reading belong to a channel nobody is watching any more.
                    self._win.engine().forget(old)
        elif col == _C_NAME:
            row["name"] = cell.text().strip()
        elif col == _C_UNIT:
            row["unit"] = cell.text().strip()
        elif col in (_C_MIN, _C_MAX):
            key = "min" if col == _C_MIN else "max"
            entry[key] = C.parse_level(cell.text())
            # Written back, so a typo that is not a number empties the box
            # rather than sitting there looking like a limit.
            self._loading = True
            try:
                cell.setText(C.level_text(entry[key]))
            finally:
                self._loading = False
            self._warn_if_crossed(row, entry)
        elif col in (_C_SMIN, _C_SMAX):
            key = "scan_min" if col == _C_SMIN else "scan_max"
            row[key] = C.parse_level(cell.text())
            self._loading = True
            try:
                cell.setText(C.level_text(row[key]))
            finally:
                self._loading = False
            self._warn_if_crossed(row, {"min": row.get("scan_min"),
                                        "max": row.get("scan_max")},
                                  where="the scan limits")
        else:
            return
        # Repaints every tab, this one included.
        self._win.settings_changed()

    def move_rows(self, sources, target):
        """Table rows `sources` dragged to just in front of table row `target`.

        The list is rewritten in place, in the slots the table's rows held, so
        the shot counter (not in this table) keeps its own place. A row
        dropped on the other side of the "Not shown" line changes its Show to
        match. Then both lists are built again, one event-loop turn later —
        we are inside the table's own drop.
        """
        table = self._table_rows
        if not table:
            return
        sep = table.index(None) if None in table else len(table)
        moving = [table[t] for t in sources
                  if 0 <= t < len(table) and table[t] is not None]
        if not moving:
            return
        moving_ids = {id(r) for r in moving}
        shown = [r for r in table[:sep] if id(r) not in moving_ids]
        hidden = [r for r in table[sep + 1:] if id(r) not in moving_ids]
        target = max(0, min(int(target), len(table)))
        if target <= sep:
            # Among the shown rows: count the ones staying that sit above.
            at = sum(1 for r in table[:target] if r is not None
                     and id(r) not in moving_ids)
            shown[at:at] = moving
            want_show = True
        else:
            at = sum(1 for r in table[sep + 1:target]
                     if id(r) not in moving_ids)
            hidden[at:at] = moving
            want_show = False
        for row in moving:
            row["show"] = want_show
        order = iter(shown + hidden)
        in_table = {id(r) for r in table if r is not None}
        rows = self._win.rows()
        rows[:] = [next(order) if id(r) in in_table else r for r in rows]
        QTimer.singleShot(0, self, self._rows_changed)

    def _rows_changed(self):
        """Show was ticked or unticked: the Overview's list and this table are
        built again, and nothing else (`main.rows_changed`). `getattr` for the
        stand-in window the pictures are rendered against."""
        hook = getattr(self._win, "rows_changed", None)
        if hook is not None:
            hook()
        else:
            self._win.settings_changed(rebuild=True)

    def _warn_if_crossed(self, row, entry, where=None):
        lo, hi = entry.get("min"), entry.get("max")
        if lo is not None and hi is not None and lo > hi:
            self._win.log(
                f"{C.row_label(row)} in {where or self._hall}: the minimum "
                f"({C.level_text(lo)}) is above the maximum "
                f"({C.level_text(hi)}), so nothing can ever be in range.")

    def _switch_word(self, gate, readings, last_ok, now):
        """What the valve's channel is reading, in words: (text, colour).

        The NUMBER first, then the position it stands for:

            value=1 : open        an _IN_OPEN_POSITION channel, switch made
            value=0 : closed      the same channel, switch not made
            value=1 : closed      an _IN_CLOSED_POSITION channel, made

        The word comes from `ls_core.switch_sense`, never from the number: 1 is
        open on one channel and shut on the other, and a label that claimed
        otherwise would be wrong on half the channels an operator can pick. A
        name that says neither word gets the number on its own — nothing is
        claimed that cannot be known.
        """
        channel = (gate.get("open") or "").strip()
        if not channel:
            return "not set", U.QUIET
        reading = readings.get(channel)
        seen = last_ok.get(channel)
        if reading is None:
            return "not read yet", U.QUIET
        if reading.error:
            return reading.error.split(" — ")[-1], "#8d1c12"
        if reading.value is None:
            return "nothing archived", "#8d1c12"
        if seen is not None and now - seen > C.STALE_AFTER_S:
            return f"not read for {int(now - seen)} s", "#8d1c12"

        made = C.switch_made(reading.value)
        number = "1" if made else C.value_text(reading.value)
        sense = C.switch_sense(channel)
        # Dark ink, not green and not red: this is what the channel reads, and
        # whether that is good news depends on the hall. The verdict is the
        # hall's square on the Overview.
        if sense is None:
            return f"value={number}", "#16202c"
        if sense == C.OPEN:
            word = "open" if made else "closed"
        else:
            word = "closed" if made else "open"
        return f"value={number} : {word}", "#16202c"

    def _gate_typed(self):
        new = self._gate_edit.text().strip()
        gate = C.gate_for(self._win.shooting(), self._hall)
        old = (gate.get("open") or "").strip()
        if new == old:
            return
        gate["open"] = new
        self._win.engine().forget(old)
        # Repaints every tab, this one included.
        self._win.settings_changed()

    def _find_gate(self):
        dlg = ChannelPicker(self, self._gate_edit.text().strip())
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
            return
        self._gate_edit.setText(dlg.chosen)
        self._gate_typed()

    # ── the list ───────────────────────────────────────────────────────────
    def _selected_rows(self):
        picked = sorted({i.row() for i in self._table.selectedIndexes()})
        return [self._table_rows[i] for i in picked
                if i < len(self._table_rows) and self._table_rows[i] is not None]

    def _sync_buttons(self):
        picked = self._selected_rows()
        # The shot counter and DM settings are always on the list: Remove
        # passes them by.
        self._btn_remove.setEnabled(
            any(not C.is_builtin_row(r) for r in picked))
        self._btn_find.setEnabled(len(picked) == 1
                                  and not C.is_builtin_row(picked[0]))

    def _shot_reset_s(self):
        get = getattr(self._win, "shot_settings", None)
        return float(get()["reset_s"]) if get else C.SHOT_RESET_S_DEFAULT

    def _shot_settings(self):
        """The counter's settings, or the defaults for a stand-in window."""
        get = getattr(self._win, "shot_settings", None)
        if get is not None:
            return get()
        return {"reset_s": C.SHOT_RESET_S_DEFAULT,
                "size_px": C.SHOT_SIZE_PX_DEFAULT, "bold": C.SHOT_BOLD_DEFAULT}

    # ── the shot counter's own table ───────────────────────────────────────
    def _shot_row(self):
        return next((r for r in self._win.rows() if C.is_shot_row(r)), None)

    def _fill_shots(self, rows, pos):
        """The one row of the shot counter's table. Called inside `reload`,
        under `self._loading`."""
        row = self._shot_row()
        self._shots.setRowCount(0)
        self._shots.setRowCount(1 if row is not None else 0)
        if row is None:
            return
        shots = self._shot_settings()
        letter = C.row_letter(pos[id(row)])
        values = {
            _S_SHOW: "",
            _S_LETTER: letter,
            _S_NAME: row.get("name") or "",
            _S_RESET: f"{int(shots['reset_s'])} s",
            _S_SIZE: f"{int(shots['size_px'])} px",
            _S_BOLD: "",
            _S_NOW: "",
        }
        base = Qt.ItemFlag.ItemIsEnabled
        for col, text in values.items():
            cell = QTableWidgetItem(text)
            flags = base
            if col in (_S_NAME, _S_RESET, _S_SIZE):
                flags |= Qt.ItemFlag.ItemIsEditable
            if col in (_S_SHOW, _S_BOLD):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                on = C.is_shown(row) if col == _S_SHOW else shots["bold"]
                cell.setCheckState(Qt.CheckState.Checked if on
                                   else Qt.CheckState.Unchecked)
            cell.setFlags(flags)
            if col != _S_NAME:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._shots.setItem(0, col, cell)
        tips = {
            _S_SHOW: "Shown: the \"Shot counter: 12\" line above the list on "
                     "the Overview. Unticked, the line is not there.",
            _S_LETTER: f"This row is {letter} — the name a formula calls it "
                       f"by.",
            _S_NAME: "What the line on the Overview calls it.",
            _S_RESET: "After this many seconds without a shot the number stays "
                      "on screen and the next shot counts from 1. Set it "
                      "longer than the time between two shots — at 0.1 Hz "
                      f"that is 10 s. Type the seconds "
                      f"({C.SHOT_RESET_LIMITS[0]}–{C.SHOT_RESET_LIMITS[1]}).",
            _S_SIZE: "How tall the line on the Overview is. Type the pixels "
                     f"({C.SHOT_SIZE_LIMITS[0]}–{C.SHOT_SIZE_LIMITS[1]}).",
            _S_BOLD: "The line on the Overview in bold.",
            _S_NOW: "A shot is the rows with Show ticked changing; rows "
                    "changing one after another for the same shot count "
                    "once.",
        }
        for col, tip in tips.items():
            self._shots.item(0, col).setToolTip(tip)

    def _shot_edited(self, cell):
        """A cell of the shot counter's table was changed.

        The number cells are written back either way, so they show the
        setting in force and never what was typed. Size and Bold reload the
        Overview only (`shot_look_changed`), never this tab under the cell
        being typed into.
        """
        if self._loading:
            return
        row = self._shot_row()
        if row is None:
            return
        col = cell.column()
        shots = self._shot_settings()
        if col == _S_SHOW:
            row["show"] = cell.checkState() == Qt.CheckState.Checked
            # Deferred one turn, like Show in the channels' table: a rebuild
            # refills this table, and we are inside its own `itemChanged`.
            QTimer.singleShot(0, self._rows_changed)
            return
        if col == _S_NAME:
            row["name"] = cell.text().strip()
            self._win.settings_changed()
            return
        if col == _S_BOLD:
            shots["bold"] = cell.checkState() == Qt.CheckState.Checked
        elif col in (_S_RESET, _S_SIZE):
            key, (low, high), unit = (
                ("reset_s", C.SHOT_RESET_LIMITS, "s") if col == _S_RESET
                else ("size_px", C.SHOT_SIZE_LIMITS, "px"))
            value = _whole_number_in(cell.text())
            if value is not None:
                shots[key] = max(low, min(high, value))
            self._loading = True
            try:
                cell.setText(f"{int(shots[key])} {unit}")
            finally:
                self._loading = False
        else:
            return
        if col == _S_RESET:
            self._win.shot_reset_changed()
        else:
            hook = getattr(self._win, "shot_look_changed", None)
            if hook is not None:
                hook()

    def _fit_heights(self):
        """Both tables exactly as tall as their rows, so the shot counter's
        sits straight under the last channel and nothing scrolls inside a
        table — the page scrolls instead."""
        for table in (self._table, self._shots):
            height = table.horizontalHeader().sizeHint().height()
            for r in range(table.rowCount()):
                height += table.rowHeight(r)
            bar = table.horizontalScrollBar()
            if bar.isVisible():
                height += bar.sizeHint().height()
            table.setFixedHeight(height + 2 * table.frameWidth())

    def _add(self):
        dlg = ChannelPicker(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
            return
        rows = self._win.rows()
        existing = next((r for r in rows if (r.get("pv") or "") == dlg.chosen),
                        None)
        if existing is not None:
            QMessageBox.information(
                self, "Already on the list",
                f"{dlg.chosen} is already watched — it is the row called "
                f"\"{C.row_label(existing)}\".")
            return
        row = C.new_row(C.next_id(rows), dlg.chosen, _suggest_name(dlg.chosen))
        rows.append(row)
        self._win.settings_changed(rebuild=True)

    def _add_formula(self):
        """A new formula row, at the bottom, with the caret in its box.

        At the bottom because a formula may only use the rows ABOVE it, so the
        end of the list is the one place a new one is always allowed.

        The box opens holding "= ", so the row SAYS it is a formula before
        anything is typed into it. It is not what makes it one — the row carries
        the `expr` key and `is_formula_row` reads that — it is so the operator
        can see which kind of row they are looking at.
        """
        rows = self._win.rows()
        rows.append(C.new_formula_row(C.next_id(rows), name="new formula"))
        self._win.settings_changed(rebuild=True)
        # Its TABLE row: a new row is shown, so it is the last one above the
        # "Not shown" line, not the table's last row.
        new = self._rows_on_screen[-1] if self._rows_on_screen else None
        last = next((t for t, r in enumerate(self._table_rows) if r is new),
                    -1) if new is not None else -1
        if last >= 0:
            cell = self._table.item(last, _C_PV)
            self._loading = True
            try:
                cell.setText("= ")
            finally:
                self._loading = False
            self._table.setCurrentCell(last, _C_PV)
            self._table.editItem(cell)

    def _change_channel(self):
        picked = self._selected_rows()
        if len(picked) != 1:
            return
        row = picked[0]
        if C.is_builtin_row(row):
            return                          # it reads no channel to change
        if C.is_formula(row):
            QMessageBox.information(
                self, "This row is a formula",
                f"\"{C.row_label(row)}\" works its number out of the rows "
                f"above it — it reads no channel of its own.\n\nEdit the "
                f"formula itself in the \"Channel or formula\" column.")
            return
        dlg = ChannelPicker(self, row.get("pv") or "")
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
            return
        old = row.get("pv") or ""
        row["pv"] = dlg.chosen
        if not (row.get("name") or "").strip():
            row["name"] = _suggest_name(dlg.chosen)
        self._win.engine().forget(old)
        self._win.settings_changed(rebuild=True)

    def _remove(self):
        picked = [r for r in self._selected_rows()
                  if not C.is_builtin_row(r)]
        if not picked:
            return
        doomed_ids = {int(r["id"]) for r in picked}
        orphaned = [r for r in self._win.rows()
                    if C.is_formula(r) and int(r["id"]) not in doomed_ids
                    and doomed_ids & {int(v) for v
                                      in (r.get("bind") or {}).values()}]
        broken = ("\n\nThese formulas use them and will stop working:\n"
                  + U.name_list(orphaned, fallback="(no name)")
                  if orphaned else "")
        answer = QMessageBox.question(
            self, "Take these off the list",
            "These channels will stop being watched in EVERY hall, and their "
            "limits will be forgotten:\n\n"
            + U.name_list(picked, fallback="(no name)") + broken +
            "\n\nRemove them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        rows = self._win.rows()
        rows[:] = [r for r in rows if int(r["id"]) not in doomed_ids]
        for row in picked:
            self._win.engine().forget(row.get("pv") or "")
        self._win.settings_changed(rebuild=True)


def _whole_number_in(text):
    """The whole number typed into a cell, or None. "30", "30 s" and "12,6"
    all work; the first number in the cell is taken."""
    found = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not found:
        return None
    return int(round(float(found.group(0).replace(",", "."))))


def _suggest_name(channel):
    """A first guess at what to call a channel on the first tab.

    The tail after the colon, which is the part that says what the value is —
    `L3-UTIL-CHL03-001:Temp` suggests "Temp". A guess, and the operator types
    over it; it is here so a freshly added row is not nameless.
    """
    tail = (channel or "").rsplit(":", 1)[-1]
    return tail.strip() or channel
