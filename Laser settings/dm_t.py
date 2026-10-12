"""Laser settings — the DM settings tab: which set-up the deformable mirror is on.

OPR1 photographs the Remote Desktop window to the mirror's PC every 5 s and
writes what the Set-up grid shows into a file on the scratch share (`ls_dm`,
switched on there on the DM writer tab). This tab, on every PC, reads that file
and says which SAVED set-up the 36 colours are:

    the name        big, on green — the grid is one of the saved set-ups
    Not a saved…    on yellow — read fine, but nothing saved matches; the
                    closest one is named, with how many electrodes differ
    Unknown         grey — OPR1 could not read the grid (window minimised, not
                    open, Mirror Control Window covered). No colours at all:
                    an old picture is never shown as if it were the mirror now
    No fresh…       grey — OPR1 has stopped writing

Under it the grid as read and, beside it, a saved set-up to compare with — the
one it matches, or the one picked in the list — with every electrode that
differs drawn with a thick outline.

The list of saved set-ups is shared by every PC (it is a file on the share).
Save the grid that is on the mirror NOW under a name, rename one, delete some.
The edits are done on the engine's dm thread, never here: the share can take
48 s to answer and the window must not stop for it.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QScrollArea, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

import ls_dm as D
import ls_ui as U

# The banner's ground and ink for each verdict — always both, never one.
_BANNER = {
    D.VERDICT_MATCH: U.STATE_COLORS["ok"],
    D.VERDICT_OTHER: U.STATE_COLORS["warn"],
    D.VERDICT_UNKNOWN: U.STATE_COLORS["unknown"],
    D.VERDICT_STALE: U.STATE_COLORS["stale"],
    D.VERDICT_NONE: U.STATE_COLORS["unknown"],
}

# How long the Delete button waits for its second click.
_ARM_MS = 4000

# The list's columns.
_C_NAME, _C_HIGH, _C_LOW, _C_SAVED, _C_ON = range(5)
_TAG_OF_COLUMN = {_C_HIGH: D.TAG_HIGH, _C_LOW: D.TAG_LOW}


class DmGrid(QWidget):
    """The 6 × 6 electrodes in their colours, numbered like the original.

    `marked` electrodes get a thick outline, black inside white, which shows on
    any cell colour. Without colours every cell is grey with a dash: nothing is
    claimed.
    """

    EMPTY = "#d9dde2"
    EMPTY_INK = "#6a737d"
    LINE = "#7d8590"

    def __init__(self, caption="", parent=None):
        super().__init__(parent)
        self._caption = caption
        self._colours = None
        self._marked = set()
        self.setMinimumSize(150, 170)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return int(width) + 24

    def sizeHint(self):
        return QSize(260, 284)

    def set_grid(self, colours, marked=(), caption=None):
        colours = ([tuple(int(v) for v in c) for c in colours]
                   if colours and len(colours) == D.CELLS else None)
        marked = set(marked or ())
        if caption is not None:
            self._caption = caption
        if (colours, marked) != (self._colours, self._marked) \
                or caption is not None:
            self._colours, self._marked = colours, marked
            self.update()

    def caption(self):
        return self._caption

    def paintEvent(self, _ev):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            cap_h = 22
            side = max(10, min(self.width() - 2, self.height() - cap_h - 2))
            x0 = (self.width() - side) / 2.0
            y0 = cap_h
            font = QFont(self.font())
            font.setPixelSize(13)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor("#16202c"))
            p.drawText(QRectF(0, 0, self.width(), cap_h - 4),
                       Qt.AlignmentFlag.AlignCenter, self._caption)
            cell = side / D.GRID_N
            num = QFont(self.font())
            num.setPixelSize(max(8, int(cell * 0.32)))
            p.setFont(num)
            for i in range(D.CELLS):
                row, col = divmod(i, D.GRID_N)
                r = QRectF(x0 + col * cell, y0 + row * cell, cell, cell)
                if self._colours:
                    rr, gg, bb = self._colours[i]
                    ground = QColor(rr, gg, bb)
                    light = 0.299 * rr + 0.587 * gg + 0.114 * bb
                    ink = QColor("#111111" if light >= 140 else "#ffffff")
                    text = str(i)
                else:
                    ground = QColor(self.EMPTY)
                    ink = QColor(self.EMPTY_INK)
                    text = "–"
                p.fillRect(r, ground)
                p.setPen(QPen(QColor(self.LINE), 1))
                p.drawRect(r)
                p.setPen(ink)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for i in sorted(self._marked):
                row, col = divmod(i, D.GRID_N)
                r = QRectF(x0 + col * cell, y0 + row * cell, cell, cell)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffffff"), 5))
                p.drawRect(r.adjusted(3, 3, -3, -3))
                p.setPen(QPen(QColor("#111111"), 3))
                p.drawRect(r.adjusted(3, 3, -3, -3))
        finally:
            p.end()


class DmTab(QWidget):
    """Which saved set-up the mirror is on, and the list of saved set-ups."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._view = None
        self._names = []
        self._shown = None
        self._armed = []
        self._pending = {}              # name -> tag clicked, not yet back
        self._arm_timer = QTimer(self)
        self._arm_timer.setSingleShot(True)
        self._arm_timer.setInterval(_ARM_MS)
        self._arm_timer.timeout.connect(self._disarm)
        self._build()

    # ── building ───────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {U.PAPER};"
                             " border: 0px; }" + U.SCROLLBAR_QSS)
        scroll.viewport().setStyleSheet(f"background: {U.PAPER};")
        page = QWidget()
        page.setObjectName("dmPage")
        page.setStyleSheet(f"#dmPage {{ background: {U.PAPER}; }}")
        root = QVBoxLayout(page)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        scroll.setWidget(page)
        outer.addWidget(scroll)

        self._banner = QLabel("Not read yet")
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setWordWrap(True)
        self._banner.setMinimumHeight(70)
        root.addWidget(self._banner)
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setStyleSheet("color: #16202c; font-size: 13px;")
        root.addWidget(self._detail)
        self._problem = QLabel("")
        self._problem.setWordWrap(True)
        self._problem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._problem.setStyleSheet(
            f"color: {U.STATE_COLORS['trip'][1]}; font-size: 12px;")
        self._problem.hide()
        root.addWidget(self._problem)

        grids = QHBoxLayout()
        grids.setSpacing(24)
        grids.addStretch(1)
        self._now = DmGrid("On the mirror now")
        self._saved = DmGrid("Saved set-up")
        self._now.setToolTip("The Set-up grid of Mirror Control Window, as "
                             "read off the Remote Desktop window on OPR1.\n"
                             "Electrodes drawn with a thick outline differ "
                             "from the saved set-up beside it.")
        self._saved.setToolTip("The saved set-up the mirror matches — or, when "
                               "it matches none, the closest one. Pick another "
                               "in the list below to compare with it.")
        grids.addWidget(self._now, 3)
        grids.addWidget(self._saved, 3)
        grids.addStretch(1)
        root.addLayout(grids)

        box = QGroupBox("Saved set-ups (shared by every PC)")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "High power", "Low power", "Saved", "On"])
        for col, words in ((_C_HIGH, "a fresh SBW4 shot over"),
                           (_C_LOW, "a fresh SBW4 shot at or under")):
            self._table.horizontalHeaderItem(col).setToolTip(
                f"Ticked set-ups are accepted on the Overview's DM settings "
                f"line when {words} 0.05 J arrives.\nTick as many as you like: "
                f"the mirror on any one of them is OK. A set-up ticked for "
                f"neither is a cross whenever a shot arrives.")
        self._table.setStyleSheet(U.TABLE_QSS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(_C_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (_C_HIGH, _C_LOW, _C_SAVED, _C_ON):
            head.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        for col in _TAG_OF_COLUMN:
            self._table.setItemDelegateForColumn(
                col, U.CenteredCheckDelegate(self._table))
        self._table.setMinimumHeight(140)
        self._table.itemSelectionChanged.connect(self._picked)
        self._table.itemChanged.connect(self._ticked)
        lay.addWidget(self._table)

        tools = QGridLayout()
        tools.setHorizontalSpacing(8)
        tools.setVerticalSpacing(6)
        tools.addWidget(QLabel("Name"), 0, 0)
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. High power")
        self._name.setMinimumWidth(220)
        self._name.returnPressed.connect(self._save)
        tools.addWidget(self._name, 0, 1)
        self._save_btn = U.button(
            "Save the grid on the mirror now", icon_name="save", primary=True,
            tip="Save the colours of the Set-up grid as they are on the mirror "
                "now, under the name typed beside it.\nEvery PC sees the same "
                "list.")
        self._save_btn.clicked.connect(self._save)
        tools.addWidget(self._save_btn, 0, 2)
        self._rename_btn = U.button(
            "Rename", icon_name="pencil",
            tip="Give the set-up picked in the list the name typed in the box."
                "\nLow power and High power can be renamed too; deleting a "
                "renamed one brings its built-in name back.")
        self._rename_btn.clicked.connect(self._rename)
        tools.addWidget(self._rename_btn, 0, 3)
        self._delete_btn = U.button(
            "Delete", icon_name="trash", danger=True,
            tip="Delete the set-ups picked in the list. Click once, then once "
                "more to confirm.")
        self._delete_btn.clicked.connect(self._delete)
        tools.addWidget(self._delete_btn, 0, 4)
        tools.setColumnStretch(5, 1)
        lay.addLayout(tools)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #16202c; font-size: 12px;")
        lay.addWidget(self._note)
        root.addWidget(box)

        where = QLabel(f"Files: {D.share_folder_text()}")
        where.setWordWrap(True)
        where.setStyleSheet(f"color: {U.QUIET}; font-size: 11px;")
        where.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(where)
        root.addStretch(1)
        self._paint_banner(D.verdict(None, {}))
        self._enable()

    # ── what the window calls ──────────────────────────────────────────────
    def reload(self):
        self.refresh()

    def refresh(self):
        engine = self._win.engine()
        view = engine.dm() if engine is not None else None
        setups = view.setups if view is not None else {}
        v = D.verdict(view.state if view is not None else None, setups)
        self._paint_banner(v)
        problem = view.problem if view is not None else None
        self._problem.setText(problem or "")
        self._problem.setVisible(bool(problem))
        if view is not self._view:
            self._view = view
            if view is not None and view.note:
                self._note.setText(view.note)
                # The edits have been done (or refused): the list now says
                # what the file says.
                self._pending.clear()
            self._fill_table(setups, v)
        self._show_grids(setups, v)
        self._enable()

    # ── drawing ────────────────────────────────────────────────────────────
    def _paint_banner(self, v):
        ground, ink = _BANNER.get(v.kind, U.STATE_COLORS["unknown"])
        size = 34 if v.kind == D.VERDICT_MATCH else 26
        self._banner.setStyleSheet(
            f"QLabel {{ background: {ground}; color: {ink};"
            f" border: 1px solid {ink}; border-radius: 6px;"
            f" font-size: {size}px; font-weight: 700; padding: 10px; }}")
        if self._banner.text() != v.title:
            self._banner.setText(v.title)
        if self._detail.text() != v.detail:
            self._detail.setText(v.detail)

    def _fill_table(self, setups, v):
        picked = self._picked_names()
        self._names = list(setups)
        now_name = v.match.name if v.match is not None else None
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            self._table.setRowCount(len(self._names))
            for row, name in enumerate(self._names):
                entry = setups[name]
                saved = entry.get("saved", "").replace("T", " ")
                by = entry.get("by", "")
                if D.is_reference(name):
                    when = "built in"
                elif entry.get("replaces"):
                    when = f"built in, was \"{entry['replaces']}\""
                else:
                    when = f"{saved}  ({by})" if by else saved
                # A tick clicked here shows at once, before the dm thread has
                # written it and handed back a view that carries it.
                tag = D.tag_of(name, setups)
                if self._pending.get(name) == tag:
                    del self._pending[name]
                tag = self._pending.get(name, tag)
                cells = {_C_NAME: name, _C_HIGH: "", _C_LOW: "",
                         _C_SAVED: when,
                         _C_ON: "on the mirror now" if name == now_name
                         else ""}
                for col, text in cells.items():
                    item = QTableWidgetItem(text)
                    if col == _C_NAME:
                        f = item.font()
                        f.setBold(True)
                        item.setFont(f)
                    if col in _TAG_OF_COLUMN:
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled
                                      | Qt.ItemFlag.ItemIsSelectable
                                      | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(
                            Qt.CheckState.Checked
                            if tag == _TAG_OF_COLUMN[col]
                            else Qt.CheckState.Unchecked)
                    if col == _C_ON and text:
                        ground, ink = U.STATE_COLORS["ok"]
                        item.setBackground(QColor(ground))
                        item.setForeground(QColor(ink))
                    self._table.setItem(row, col, item)
                if name in picked:
                    self._table.selectRow(row)
            if not self._names:
                U.empty_table_note(self._table, "Nothing saved yet.")
        finally:
            self._table.blockSignals(False)

    def _show_grids(self, setups, v):
        colours = v.colours
        picked = [n for n in self._picked_names() if n in setups]
        if picked:
            name = picked[0]
        elif v.match is not None and v.match.closest in setups:
            name = v.match.closest
        else:
            name = None
        saved = setups[name]["colours"] if name else None
        marked = D.cells_differing(colours, saved) if colours and saved else ()
        self._now.set_grid(colours, marked)
        caption = f"Saved: {name}" if name else "Saved set-up"
        if caption != self._saved.caption():
            self._saved.set_grid(saved, marked, caption)
        else:
            self._saved.set_grid(saved, marked)

    # ── the list ───────────────────────────────────────────────────────────
    def _picked_names(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        return [self._names[r] for r in rows if 0 <= r < len(self._names)]

    def _picked(self):
        picked = self._picked_names()
        if len(picked) == 1:
            self._name.setText(picked[0])
        self._disarm()
        self.refresh()

    def _fresh_colours(self):
        view = self._view
        if view is None:
            return None
        v = D.verdict(view.state, view.setups)
        return v.colours

    def _enable(self):
        picked = self._picked_names()
        self._save_btn.setEnabled(self._fresh_colours() is not None)
        # Low power and High power are built in: never deleted, but renamed
        # like any other (12.10.2026) — see `ls_dm.rename_setup`.
        own = [n for n in picked if not D.is_reference(n)]
        self._rename_btn.setEnabled(len(picked) == 1)
        self._delete_btn.setEnabled(bool(own))

    # ── the three edits, done on the dm thread ─────────────────────────────
    def _save(self):
        colours = self._fresh_colours()
        name = self._name.text().strip()
        if colours is None:
            self._note.setText("There is no fresh reading of the grid to save.")
            return
        if not name:
            self._note.setText("Type a name for the set-up first.")
            return
        frozen = [list(c) for c in colours]
        self._note.setText(f"Saving \"{name}\"…")
        self._win.engine().dm_edit(
            lambda setups: self._result(D.add_setup(setups, name, frozen),
                                        f"Saved \"{name}\"."))

    def _rename(self):
        picked = self._picked_names()
        new = self._name.text().strip()
        if len(picked) != 1:
            return
        old = picked[0]
        self._note.setText(f"Renaming \"{old}\"…")
        self._win.engine().dm_edit(
            lambda setups: self._result(D.rename_setup(setups, old, new),
                                        f"\"{old}\" is now \"{new}\"."))

    def _delete(self):
        picked = [n for n in self._picked_names() if not D.is_reference(n)]
        if not picked:
            return
        if self._armed != picked:
            self._armed = picked
            self._delete_btn.setText(f"Click again to delete {len(picked)}")
            self._arm_timer.start()
            return
        self._disarm()
        names = list(picked)
        self._note.setText("Deleting…")
        self._win.engine().dm_edit(
            lambda setups: (D.delete_setups(setups, names),
                            "Deleted " + ", ".join(f"\"{n}\"" for n in names)
                            + "."))

    def _ticked(self, item):
        """A High power / Low power tick clicked: one of the two or neither."""
        col, row = item.column(), item.row()
        if col not in _TAG_OF_COLUMN or not 0 <= row < len(self._names):
            return
        name = self._names[row]
        on = item.checkState() == Qt.CheckState.Checked
        tag = _TAG_OF_COLUMN[col] if on else D.TAG_NONE
        other = _C_LOW if col == _C_HIGH else _C_HIGH
        partner = self._table.item(row, other)
        if on and partner is not None \
                and partner.checkState() == Qt.CheckState.Checked:
            self._table.blockSignals(True)
            try:
                partner.setCheckState(Qt.CheckState.Unchecked)
            finally:
                self._table.blockSignals(False)
        self._pending[name] = tag
        words = {D.TAG_HIGH: "ticked for High power",
                 D.TAG_LOW: "ticked for Low power",
                 D.TAG_NONE: "ticked for neither"}[tag]
        self._note.setText(f"Saving: \"{name}\" {words}…")
        self._win.engine().dm_edit(
            lambda setups: self._result(D.set_tag(setups, name, tag),
                                        f"\"{name}\" is {words}."))

    def _disarm(self):
        self._armed = []
        self._arm_timer.stop()
        self._delete_btn.setText("Delete")

    @staticmethod
    def _result(outcome, done):
        """(setups, why not) from ls_dm into (setups, the sentence to show)."""
        setups, why = outcome
        if setups is None:
            return None, why            # refused, or nothing to do
        return setups, done

    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh()
