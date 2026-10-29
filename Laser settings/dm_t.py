"""Laser settings — the DM settings tab: which set-up the deformable mirror is on.

OPR1 photographs the Remote Desktop window to the mirror's PC every 5 s and
writes what the Applied voltages grid (the left one) shows into a file on the scratch share (`ls_dm`,
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
Save the grid that is on the mirror NOW under a name, replace the grid of a
saved one (and undo that), rename one, delete some — Low power and High power
too since 21.10.2026, with Restore built-in to bring them back. Whatever loses
a grid (replace, undo, delete) wants a second click.
The edits are done on the engine's dm thread, never here: the share can take
48 s to answer and the window must not stop for it.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame,
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

# How long a button that loses a grid waits for its second click.
_ARM_MS = 4000

_TIP_SAVE_EXISTING = ("\nA name that is already on the list (any case) replaces "
                      "that set-up's grid after a second click; its High "
                      "power / Low power tick stays.")

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
        self._armed = None              # (button, what) awaiting click two
        self._texts = {}                # button -> its own text
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
        self._now.setToolTip("The Applied voltages grid (the left one) of "
                             "Mirror Control Window — what is really on the "
                             "mirror — as "
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
        for col, words in ((_C_HIGH, "a fresh SBW4 shot over 0.05 J "
                                     "arrives"),
                           (_C_LOW, "SBW4 shots at or under 0.05 J keep "
                                    "coming (one or two are a trigger, not "
                                    "low power)")):
            self._table.horizontalHeaderItem(col).setToolTip(
                f"Ticked set-ups are accepted on the Overview's DM settings "
                f"line when {words}.\nTick as many as you like: "
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

        # Two ticked for the same power: the Overview takes either, so an old
        # grid left ticked beside its replacement still counts as OK.
        self._twice = QLabel("")
        self._twice.setWordWrap(True)
        ground, ink = U.STATE_COLORS["warn"]
        self._twice.setStyleSheet(
            f"QLabel {{ background: {ground}; color: {ink};"
            f" border: 1px solid {ink}; border-radius: 4px;"
            f" font-size: 12px; padding: 4px 8px; }}")
        self._twice.hide()
        lay.addWidget(self._twice)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(QLabel("Name"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. High power")
        self._name.setMinimumWidth(220)
        self._name.returnPressed.connect(self._save)
        self._name.textEdited.connect(lambda _t: self._disarm())
        tools.addWidget(self._name)
        self._save_btn = U.button(
            "Save the grid on the mirror now", icon_name="save", primary=True,
            tip="Save the colours of the Applied voltages grid as they are on "
                "the mirror "
                "now, under the name typed beside it.\nEvery PC sees the same "
                "list." + _TIP_SAVE_EXISTING)
        self._save_btn.clicked.connect(self._save)
        tools.addWidget(self._save_btn)
        # The right-hand grid (14.10.2026): a set-up can be saved while it is
        # only prepared in Set-up, before it is applied. The Overview still
        # judges the Applied voltages grid alone.
        self._save_setup_btn = U.button(
            "Save the Set-up grid", icon_name="save",
            tip="Save the colours of the right-hand Set-up grid — what is about "
                "to be applied, not yet on the mirror — under the name typed "
                "beside it.\nOnce it is applied, the Applied voltages grid shows "
                "the same set-up and it is recognised by this name."
                + _TIP_SAVE_EXISTING)
        self._save_setup_btn.clicked.connect(self._save_setup)
        tools.addWidget(self._save_setup_btn)
        tools.addStretch(1)
        lay.addLayout(tools)

        edits = QHBoxLayout()
        edits.setSpacing(8)
        self._replace_btn = U.button(
            "Replace with the grid on the mirror now", icon_name="refresh",
            tip="Give the set-up picked in the list the Applied voltages grid "
                "as it is on the mirror now. Its name and its High power / "
                "Low power tick stay.\nClick once, then once more to confirm. "
                "Undo replace brings the old grid back.")
        self._replace_btn.clicked.connect(self._replace)
        edits.addWidget(self._replace_btn)
        self._undo_btn = U.button(
            "Undo replace",
            tip="Give the set-up picked in the list back the grid it had "
                "before its last replace.\nClick once, then once more to "
                "confirm.")
        self._undo_btn.clicked.connect(self._undo)
        edits.addWidget(self._undo_btn)
        self._rename_btn = U.button(
            "Rename", icon_name="pencil",
            tip="Give the set-up picked in the list the name typed in the box."
                "\nLow power and High power can be renamed too, and a deleted "
                "one's name can be given to another set-up.")
        self._rename_btn.clicked.connect(self._rename)
        edits.addWidget(self._rename_btn)
        self._restore_btn = U.button(
            "Restore built-in", icon_name="plus",
            tip="Put the built-in Low power and High power set-ups back on the "
                "list after they were deleted or renamed — the grids the "
                "program came with.")
        self._restore_btn.clicked.connect(self._restore)
        edits.addWidget(self._restore_btn)
        self._delete_btn = U.button(
            "Delete", icon_name="trash", danger=True,
            tip="Delete the set-ups picked in the list — Low power and High "
                "power too. Click once, then once more to confirm.\nRestore "
                "built-in brings Low power and High power back.")
        self._delete_btn.clicked.connect(self._delete)
        edits.addWidget(self._delete_btn)
        edits.addStretch(1)
        lay.addLayout(edits)
        for b in (self._save_btn, self._save_setup_btn, self._replace_btn,
                  self._undo_btn, self._delete_btn):
            self._texts[b] = b.text()

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
                if entry.get("replaces"):
                    when = f"built in, was \"{entry['replaces']}\""
                elif by == D.BUILT_IN_BY:
                    when = "built in"
                else:
                    when = f"{saved}  ({by})" if by else saved
                if entry.get("replaced"):
                    again = entry["replaced"].replace("T", " ")
                    who = entry.get("replaced_by", "")
                    when += (f", replaced {again}  ({who})" if who
                             else f", replaced {again}")
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
                    # The date and the "on the mirror now" mark sit in the
                    # middle of their columns, like every value in this
                    # program's tables; only the name reads from the left.
                    if col in (_C_SAVED, _C_ON):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self._show_twice(setups)

    def _show_twice(self, setups):
        # The ticks as shown — one clicked a moment ago counts already.
        shown = {n: {**e, "power": self._pending.get(n, D.tag_of(n, setups))}
                 for n, e in setups.items()}
        twice = D.ticked_twice(shown)
        lines = []
        for tag, words in ((D.TAG_HIGH, "High power"),
                           (D.TAG_LOW, "Low power")):
            names = twice.get(tag)
            if names:
                lines.append(
                    f"{len(names)} set-ups are ticked for {words}: "
                    + ", ".join(f"\"{n}\"" for n in names)
                    + " — the Overview accepts the mirror on any of them. "
                      "Untick the ones no longer in use.")
        text = "\n".join(lines)
        if self._twice.text() != text:
            self._twice.setText(text)
        self._twice.setVisible(bool(text))

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

    def _fresh_setup_colours(self):
        view = self._view
        return D.setup_grid(view.state) if view is not None else None

    def _setups(self):
        return self._view.setups if self._view is not None else {}

    def _enable(self):
        picked = self._picked_names()
        setups = self._setups()
        fresh = self._fresh_colours() is not None
        self._save_btn.setEnabled(fresh)
        self._save_setup_btn.setEnabled(
            self._fresh_setup_colours() is not None)
        one = picked[0] if len(picked) == 1 else None
        self._replace_btn.setEnabled(one is not None and fresh)
        self._undo_btn.setEnabled(
            one is not None
            and bool((setups.get(one) or {}).get("previous")))
        self._rename_btn.setEnabled(one is not None)
        # Low power and High power are deleted like any other since
        # 21.10.2026; this brings them back.
        self._restore_btn.setEnabled(
            self._view is not None and bool(D.missing_builtins(setups)))
        self._delete_btn.setEnabled(bool(picked))

    def _confirmed(self, button, what, text):
        """True on the second click of `button` for the same `what` within
        _ARM_MS; the first click only turns its text into `text`."""
        if self._armed == (button, what):
            self._disarm()
            return True
        self._disarm()
        self._armed = (button, what)
        button.setText(text)
        self._arm_timer.start()
        return False

    # ── the edits, done on the dm thread ───────────────────────────────────
    def _save(self):
        self._save_colours(self._fresh_colours(),
                           "There is no fresh reading of the grid to save.",
                           self._save_btn)

    def _save_setup(self):
        self._save_colours(
            self._fresh_setup_colours(),
            "The Set-up grid has not been read — it may be covered in the "
            "Remote Desktop window, or OPR1 runs an older version.",
            self._save_setup_btn)

    def _save_colours(self, colours, missing, button):
        name = D.tidy_name(self._name.text())
        if colours is None:
            self._note.setText(missing)
            return
        if not name:
            self._note.setText("Type a name for the set-up first.")
            return
        frozen = [list(c) for c in colours]
        existing = D.find_name(self._setups(), name)
        if existing is not None:
            # A name on the list: its grid is replaced, after a second click
            # (21.10.2026 — before, it was refused and a third set-up made).
            if not self._confirmed(button, existing,
                                   f"Click again to replace \"{existing}\""):
                self._note.setText(
                    f"\"{existing}\" is already on the list. Click again to "
                    f"replace its grid; its High power / Low power tick "
                    f"stays.")
                return
            self._send_replace(existing, frozen)
            return
        self._disarm()
        self._note.setText(f"Saving \"{name}\"…")
        self._win.engine().dm_edit(
            lambda setups: self._result(D.add_setup(setups, name, frozen),
                                        f"Saved \"{name}\"."))

    def _replace(self):
        picked = self._picked_names()
        colours = self._fresh_colours()
        if len(picked) != 1:
            return
        if colours is None:
            self._note.setText("There is no fresh reading of the grid to save.")
            return
        name = picked[0]
        if not self._confirmed(self._replace_btn, name,
                               f"Click again to replace \"{name}\""):
            return
        self._send_replace(name, [list(c) for c in colours])

    def _send_replace(self, name, colours):
        self._note.setText(f"Replacing the grid of \"{name}\"…")
        self._win.engine().dm_edit(
            lambda setups: self._result(
                D.replace_setup(setups, name, colours),
                f"The grid of \"{name}\" is replaced. Undo replace brings "
                f"the old one back."))

    def _undo(self):
        picked = self._picked_names()
        if len(picked) != 1:
            return
        name = picked[0]
        if not self._confirmed(self._undo_btn, name,
                               f"Click again to undo \"{name}\""):
            return
        self._note.setText(f"Bringing back the old grid of \"{name}\"…")
        self._win.engine().dm_edit(
            lambda setups: self._result(
                D.undo_replace(setups, name),
                f"\"{name}\" has its previous grid again."))

    def _restore(self):
        self._disarm()
        self._note.setText("Restoring the built-in set-ups…")
        self._win.engine().dm_edit(D.restore_builtins)

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
        picked = self._picked_names()
        if not picked:
            return
        if not self._confirmed(self._delete_btn, tuple(picked),
                               f"Click again to delete {len(picked)}"):
            return
        names = list(picked)
        built = [n for n in names if D.is_reference(n)]
        after = (" Restore built-in brings " + " and ".join(built) + " back."
                 if built else "")
        self._note.setText("Deleting…")
        self._win.engine().dm_edit(
            lambda setups: (D.delete_setups(setups, names),
                            "Deleted " + ", ".join(f"\"{n}\"" for n in names)
                            + "." + after))

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
        self._armed = None
        self._arm_timer.stop()
        for button, text in self._texts.items():
            if button.text() != text:
                button.setText(text)

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
