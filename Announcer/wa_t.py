"""Announcer — the Watch tab: everything being watched, on one screen.

This is the operating view. One table, one row per watched thing, whatever kind
it is — because from here what matters is not whether something is a number or a
picture but whether it is holding.

The table is in three blocks, each under its own heading: what is watched and in
range, then what is watched and is not, then what is not watched at all. Mixed
together those three read as noise.

Inside a block the order is the one the operator arranged, and a row only ever
moves when it changes block — a table that re-sorted itself twice a second would
pull the row out from under the pointer as it is clicked. When a row does move,
the scroll position and the selected row are put back where they were.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QLabel, QPlainTextEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

import ann_core as C
import ann_ui as U

_COLS = ["On", "Name", "What it watches", "Reading now", "State"]
_C_ON, _C_NAME, _C_WATCHES, _C_READING, _C_STATE = range(5)

# The words the operator reads, per verdict. "unknown" is deliberately not
# "error": the commonest cause is a channel the archiver holds nothing for.
_STATE_WORDS = {
    C.STATE_OK:      "in range",
    C.STATE_WARN:    "warning",
    C.STATE_TRIP:    "FIRED",
    C.STATE_STALE:   "not refreshed",
    C.STATE_UNKNOWN: "no reading",
    C.STATE_OFF:     "off",
}

# The three blocks the rows are gathered into, in the order they are shown.
_G_OK, _G_BAD, _G_OFF = range(3)
_G_TITLES = {
    _G_OK:  "Watched · in range",
    _G_BAD: "Watched · not in range",
    _G_OFF: "Not watched",
}
# The heading row's own band: a shade darker than the cells, dark ink on it, so
# it reads as a heading and not as a row with a verdict of its own.
_HEAD_BAND = ("#dde3ec", "#16202c")


class WatchTab(QWidget):
    """The table and the log. Renders from the engine; decides nothing."""

    def __init__(self, engine, on_edit=None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._on_edit = on_edit
        self._updating = False
        # None, not []: an empty list is what the plan looks like when there is
        # nothing to watch, and then the first refresh would decide the table
        # was already right and never put the "nothing here yet" line in.
        self._shown_plan = None
        self._log_lines = 0
        # What each row and the banner were last painted with, so saying the
        # same thing again costs nothing. `_stale` remembers that a refresh was
        # skipped because the tab was not on screen.
        self._painted = {}
        self._banner_said = None
        self._stale = False
        self._build()
        engine.changed.connect(self._refresh)
        engine.logged.connect(self.log)
        # The banner's "last read" wording has to keep moving even when nothing
        # else changes, or a program that has stopped reading looks calm.
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_banner)
        self._tick.start()
        self._refresh()

    # ── building ────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._banner = QLabel("Reading…")
        self._banner.setWordWrap(True)
        self._banner.setMinimumHeight(34)
        root.addWidget(self._banner)

        split = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(split, stretch=1)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        hh = self._table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        # The reading sits in the middle of its column, so its heading does too.
        self._table.horizontalHeaderItem(_C_READING).setTextAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        # Every column as wide as its widest cell, heading included — and every
        # one of them still draggable. See `U.ColumnFitter`: the three hand-set
        # widths here were guesses, and "State" was stretched, which is the mode
        # that ignores what is in the column. `fit()` runs after every refresh.
        self._fit = U.ColumnFitter(self._table)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(U.TABLE_QSS)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.doubleClicked.connect(self._on_double_click)
        split.addWidget(self._table)

        log_box = QWidget()
        log_lay = QVBoxLayout(log_box)
        log_lay.setContentsMargins(0, 4, 0, 0)
        log_lay.setSpacing(3)
        head = QHBoxLayout()
        head.addWidget(U.section_label("Message log"))
        head.addStretch(1)
        self._btn_stats = U.button("Reading report", icon_name="eye",
                                   tip="Write how many reads have been made, "
                                       "how many failed and how many were "
                                       "written off, into the log.")
        self._btn_stats.clicked.connect(self._log_stats)
        head.addWidget(self._btn_stats)
        self._btn_clear = U.button("Clear log", icon_name="trash")
        self._btn_clear.clicked.connect(self._clear_log)
        head.addWidget(self._btn_clear)
        log_lay.addLayout(head)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)
        self._log.setFont(U.mono_font(9))
        self._log.setStyleSheet(
            "QPlainTextEdit { background: #ffffff; color: #16202c;"
            " border: 1px solid #b6b6b6; }" + U.SCROLLBAR_QSS)
        log_lay.addWidget(self._log)
        split.addWidget(log_box)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setSizes([520, 150])
        self._split = split

    # ── the table ───────────────────────────────────────────────────────────
    def _refresh(self):
        # Nothing is repainted for a table nobody can see. This runs twice a
        # second for as long as the program is open, and while it is watching
        # the whole window is hidden and only the circle is on screen — so this
        # used to rewrite seventy cells, with a tooltip and a font each, into a
        # table that was not on screen at all, for hours.
        if not self.isVisible():
            self._stale = True
            return
        plan = self._plan(self._engine.items())
        key = self._plan_key(plan)
        if key != self._shown_plan:
            self._rebuild(plan, key)
        elif self._repaint(plan):
            # Only when a cell really changed: the verdict column grows by a
            # whole sentence when something goes out of range, and re-measuring
            # five columns twice a second to find nothing new is wasted work.
            self._fit.fit()
        self._refresh_banner()

    def showEvent(self, ev):
        # Back on screen: repaint once, straight away, so it never shows the
        # picture from the moment it was hidden.
        super().showEvent(ev)
        if getattr(self, "_stale", False):
            self._stale = False
            self._refresh()

    def _plan(self, items):
        """The rows to show, in order: a heading, then its block, three times.

        An empty block is left out entirely — a heading over nothing is one more
        line to read for no news.
        """
        blocks = {_G_OK: [], _G_BAD: [], _G_OFF: []}
        for it in items:
            st = self._engine.state_of(int(it["id"]))
            if not it.get("on"):
                group = _G_OFF
            elif st.state == C.STATE_OK and not st.fired:
                group = _G_OK
            else:
                group = _G_BAD
            blocks[group].append(it)
        plan = []
        for group in (_G_OK, _G_BAD, _G_OFF):
            if not blocks[group]:
                continue
            plan.append(("head", group, len(blocks[group])))
            plan.extend(("item", group, it) for it in blocks[group])
        return plan

    @staticmethod
    def _plan_key(plan):
        """What the table is built out of — not what the rows say.

        The block a row is in is part of the key, so a value moving from "in
        range" to "not in range" rebuilds; a value merely changing its number
        does not.
        """
        return [(what, group,
                 payload if what == "head" else int(payload["id"]))
                for what, group, payload in plan]

    def _rebuild(self, plan, key):
        # The rows are about to be thrown away and made again, so where the
        # table was scrolled to and which row was clicked have to be carried
        # over by hand, by id — otherwise every reshuffle jumps back to the top.
        scroll = self._table.verticalScrollBar().value()
        picked = self._selected_id()
        self._updating = True
        try:
            self._table.clearSpans()
            self._table.setRowCount(len(plan))
            for row, (what, group, payload) in enumerate(plan):
                if what == "head":
                    self._put_heading(row, group, payload)
                    continue
                for col in range(len(_COLS)):
                    cell = QTableWidgetItem("")
                    if col == _C_ON:
                        cell.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                                      | Qt.ItemFlag.ItemIsEnabled
                                      | Qt.ItemFlag.ItemIsSelectable)
                    self._table.setItem(row, col, cell)
                self._table.item(row, _C_ON).setData(
                    Qt.ItemDataRole.UserRole, int(payload["id"]))
            U.empty_table_note(
                self._table,
                "Nothing is being watched yet — add a value on the Values tab "
                "or a screen area on the Areas tab.")
            self._shown_plan = key
            # New cells, so nothing that was painted before still applies.
            self._painted = {}
        finally:
            self._updating = False
        self._repaint(plan)
        self._fit.fit()
        self._reselect(picked)
        self._table.verticalScrollBar().setValue(scroll)

    def _put_heading(self, row, group, count):
        """One heading line across the whole width, dark ink on a grey band."""
        ground, ink = _HEAD_BAND
        line_h = self._table.verticalHeader().defaultSectionSize()
        for col in range(len(_COLS)):
            cell = QTableWidgetItem(
                f"{_G_TITLES[group]}   ({count})" if col == 0 else "")
            # The heading is spanned across the whole width, so its own text
            # must not be what the FIRST column is sized to, or "On" comes out
            # as wide as the whole sentence. `U.ColumnFitter` skips spanned
            # cells and so no longer needs this; it is kept because Qt's own
            # `resizeColumnToContents` does not, and this row is the thing that
            # trips it.
            cell.setSizeHint(QSize(1, line_h))
            cell.setFlags(Qt.ItemFlag.NoItemFlags)
            cell.setBackground(QColor(ground))
            cell.setForeground(QColor(ink))
            font = cell.font()
            font.setBold(True)
            cell.setFont(font)
            self._table.setItem(row, col, cell)
        self._table.setSpan(row, 0, 1, len(_COLS))

    def _selected_id(self):
        rows = {ix.row() for ix in self._table.selectedIndexes()}
        for row in rows:
            cell = self._table.item(row, _C_ON)
            iid = None if cell is None else cell.data(Qt.ItemDataRole.UserRole)
            if iid is not None:
                return int(iid)
        return None

    def _reselect(self, item_id):
        if item_id is None:
            return
        for row in range(self._table.rowCount()):
            cell = self._table.item(row, _C_ON)
            iid = None if cell is None else cell.data(Qt.ItemDataRole.UserRole)
            if iid is not None and int(iid) == item_id:
                self._table.selectRow(row)
                return

    def _repaint(self, plan):
        """Write the cells that changed; says whether any did."""
        wrote = False
        self._updating = True
        try:
            for row, (what, _group, payload) in enumerate(plan):
                if what != "item":
                    continue
                st = self._engine.state_of(int(payload["id"]))
                wrote = self._paint_row(row, payload, st) or wrote
        finally:
            self._updating = False
        return wrote

    def _paint_row(self, row, item, st):
        on = bool(item.get("on"))
        state = st.state if on else C.STATE_OFF

        # A row that is fine gets no band at all — plain white and zebra, like
        # any table. Fourteen healthy values painted green is a wall of colour
        # in which the one thing that is wrong does not stand out, which is the
        # opposite of what the colour is for. Only a verdict worth looking at
        # gets a ground, and then it gets its ink with it.
        if st.fired and on:
            # The thing that actually raised the alarm, painted on the items —
            # never left to Qt's selection colour, because the selection is
            # where the operator last clicked, and that is a different question
            # from what went wrong.
            ground, ink = U.FIRED_BAND
        elif state == C.STATE_OK:
            ground, ink = None, U.INK
        else:
            ground, ink = U.STATE_COLORS.get(state, U.STATE_COLORS["unknown"])

        kind_word = "value" if item.get("kind") == "value" else "screen area"
        fires_word = ("raises the alarm" if item.get("fires") == "alarm"
                      else "shows only")
        presets = ", ".join(item.get("presets") or [])
        reading = self._reading_text(item, st)
        words = _STATE_WORDS.get(state, state)
        # On an ok row the verdict is just "in range": the number is already in
        # the column to its left, and saying it twice reads as two readings.
        state_text = (words if state in (C.STATE_OK, C.STATE_OFF) or not st.text
                      else f"{words} — {st.text}")

        texts = {_C_NAME: item.get("name") or "(unnamed)",
                 _C_WATCHES: C.short_summary(item),
                 _C_READING: reading,
                 _C_STATE: state_text}
        tip = (f"{kind_word} · {fires_word}"
               + (f" · presets: {presets}" if presets else " · no preset")
               + f"\n{C.item_summary(item)}"
               + (f"\n\n{item.get('message')}" if item.get("message") else "")
               + (f"\n\n{st.hint}" if st.hint else "")
               + "\n\nDouble-click to change it.")

        # Nothing is written unless something actually changed. Every one of
        # these calls is a real repaint and a style repolish, and with the lab
        # idle every value is legitimately out of range — so this was seventy
        # cells rewritten twice a second, for ever, to say the same thing.
        stamp = (on, state, st.fired, ground, ink, tip,
                 tuple(sorted(texts.items())))
        if getattr(self, "_painted", {}).get(row) == stamp:
            return False
        self._painted[row] = stamp

        for col in range(len(_COLS)):
            cell = self._table.item(row, col)
            if cell is None:
                continue
            if col == _C_ON:
                cell.setCheckState(Qt.CheckState.Checked if on
                                   else Qt.CheckState.Unchecked)
            else:
                cell.setText(texts.get(col, ""))
            # A background is set on EVERY cell of the row, or on none of them.
            # Set on one cell only, the band breaks and the row reads as two
            # different things.
            if ground is None:
                cell.setData(Qt.ItemDataRole.BackgroundRole, None)
            else:
                cell.setBackground(QColor(ground))
            cell.setForeground(QColor(ink))
            cell.setToolTip(tip)
            font = cell.font()
            font.setBold(state == C.STATE_TRIP)
            cell.setFont(font)
        # Numbers right-aligned so a column of them can be read down the column.
        self._table.item(row, _C_READING).setTextAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        return True

    def _reading_text(self, item, st):
        if item.get("kind") == "area":
            return "—" if st.diff is None else f"difference {st.diff:.1f}"
        if st.value is None:
            return "—"
        unit = item.get("unit") or ""
        return f"{st.value:.4g}{(' ' + unit) if unit else ''}"

    # ── the banner ──────────────────────────────────────────────────────────
    def _refresh_banner(self):
        items = self._engine.items()
        on = [it for it in items if it.get("on")]
        if not items:
            self._set_banner("unknown", "Nothing is being watched yet — add a "
                                        "value or a screen area.")
            return
        worst_state, worst_item, worst_st = None, None, None
        for it in on:
            st = self._engine.state_of(int(it["id"]))
            if worst_state is None or C.state_rank(st.state) > C.state_rank(worst_state):
                worst_state, worst_item, worst_st = st.state, it, st
        counts = {}
        for it in on:
            s = self._engine.state_of(int(it["id"])).state
            counts[s] = counts.get(s, 0) + 1
        tally = ", ".join(f"{n} {_STATE_WORDS.get(s, s)}"
                          for s, n in sorted(counts.items(),
                                             key=lambda kv: -C.state_rank(kv[0])))
        watching = ("watching" if self._engine.watching
                    else "not watching — readings only")
        head = f"{len(on)} of {len(items)} switched on · {watching}"
        if worst_state in (C.STATE_OK, None):
            self._set_banner("ok", f"Everything is in range.   {head}")
            return
        name = worst_item.get("name") or "(unnamed)"
        self._set_banner(worst_state,
                         f"{_STATE_WORDS.get(worst_state, worst_state)}: "
                         f"{name} — {worst_st.text}\n{head}   ·   {tally}")

    def _set_banner(self, state, text):
        # The same sentence in the same colour is not worth a stylesheet
        # re-parse. This is called from a 2 Hz signal and a 1 Hz timer.
        if self._banner_said == (state, text):
            return
        self._banner_said = (state, text)
        ground, ink = U.STATE_COLORS.get(state, U.STATE_COLORS["unknown"])
        self._banner.setText(text)
        self._banner.setStyleSheet(
            f"QLabel {{ background: {ground}; color: {ink};"
            f" border: 1px solid {ink}; border-radius: 4px;"
            f" padding: 6px 10px; font-weight: 600; }}")

    # ── reacting ────────────────────────────────────────────────────────────
    def _on_item_changed(self, cell):
        if self._updating or cell.column() != _C_ON:
            return
        iid = cell.data(Qt.ItemDataRole.UserRole)
        if iid is None:      # a heading line, or the "nothing here yet" note
            return
        item = self._engine.item_by_id(iid)
        if item is None:
            return
        item["on"] = cell.checkState() == Qt.CheckState.Checked
        if self._on_edit:
            self._on_edit()

    def _on_double_click(self, index):
        cell = self._table.item(index.row(), _C_ON)
        if cell is None:
            return
        iid = cell.data(Qt.ItemDataRole.UserRole)
        if iid is None:      # a heading line, or the "nothing here yet" note
            return
        item = self._engine.item_by_id(iid)
        if item is not None and self._on_edit:
            self._on_edit(item)

    # ── the log ─────────────────────────────────────────────────────────────
    def log(self, message, hint=None):
        """One line, stamped. The hint goes in as an indented second line.

        A raw traceback tells an operator nothing, so nothing raw reaches here:
        every failure has already been turned into a sentence.
        """
        from datetime import datetime
        self._log_lines += 1
        self._log.appendPlainText(
            f"[{datetime.now().strftime('%H:%M:%S')}]  {message}")
        if hint:
            self._log.appendPlainText(f"             {hint}")
        self._log.ensureCursorVisible()

    def _clear_log(self):
        self._log.clear()
        self._log_lines = 0

    def _log_stats(self):
        self.log(self._engine.stats_line())
