"""Announcer — the Areas tab: the rectangles of the screen that must not change.

A watched area does not understand what it is looking at. It keeps a reference
picture, takes a new one twice a second, and compares them; if the average
difference is over the sensitivity number, that counts as a change. Which is
exactly what makes it work on anything — a status box, a warning lamp, a number,
a camera window.

Two things follow from that and are the whole of the advice:

  * draw the rectangle TIGHT around the thing that matters. A small change in a
    big rectangle averages away to nothing.
  * the reference is stored in the settings file — so a restart never quietly
    accepts a screen that is ALREADY in the bad state as normal. Drawing a
    rectangle puts this window out of the way first, because it would otherwise
    be standing in front of what is being drawn; retaking the picture from
    "Load" does not, since watching photographs the screen with this window on
    it as well.

The preview on the right is there so the sensitivity can be set by looking:
the rectangle as it is now, its reference beside it, and the live difference.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import ann_core as C
import ann_presets as P
import ann_screen as S
import ann_ui as U

# The order is the operator's: what the area IS first, where it is last. The
# monitor number is not a column of its own — one digit does not earn a column,
# so it is written into the Rectangle cell with the size and the corner.
_COLS = ["On", "Name", "Sensitivity", "Say when it fires", "Fires",
         "Rectangle", "Reference"]
(_C_ON, _C_NAME, _C_THR, _C_MSG, _C_FIRES, _C_RECT, _C_REF) = range(7)

_FIRES_WORDS = {"show": "shows only", "alarm": "raises the alarm"}

# How long the windows need to be out of the way before a picture is taken.
# Both numbers are the old program's, and both are load-bearing: without them
# the program photographs itself.
_HIDE_BEFORE_SELECT_MS = 150
_HIDE_BEFORE_GRAB_MS = 300


class AreaDialog(QDialog):
    """One watched area. Nothing is written until Save."""

    def __init__(self, parent, win, draft):
        super().__init__(parent)
        self.setWindowTitle("Watched screen area")
        self.setModal(True)
        self.setMinimumWidth(520)
        U.paint_dialog(self)
        self._win = win
        self.draft = dict(draft)
        self._selector = None
        self._problem = None
        # Every step of drawing an area is armed on a timer, and the operator
        # can close this editor inside any of those delays. `_finished` is what
        # every one of those callbacks checks before touching anything.
        self._finished = False
        # True from the moment this editor hides the main window until it has
        # put it back. `done` reads it: closing the editor mid-drag must not
        # leave the operator with no window anywhere on screen.
        self._hid_windows = False

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        r = 0

        self._name = QLineEdit(self.draft.get("name") or "")
        grid.addWidget(QLabel("Name"), r, 0)
        grid.addWidget(self._name, r, 1, 1, 2)
        r += 1

        self._msg = QLineEdit(self.draft.get("message") or "")
        self._msg.setPlaceholderText("what you want to read when it fires")
        grid.addWidget(QLabel("Say when it fires"), r, 0)
        grid.addWidget(self._msg, r, 1, 1, 2)
        r += 1

        self._where = QLabel("")
        self._where.setWordWrap(True)
        self._btn_draw = U.button("Draw the area", icon_name="area",
                                  tip="The screens dim; drag out the rectangle "
                                      "and let go. Esc cancels.")
        self._btn_draw.clicked.connect(self._draw)
        grid.addWidget(QLabel("Area"), r, 0)
        grid.addWidget(self._where, r, 1)
        grid.addWidget(self._btn_draw, r, 2)
        r += 1

        self._thumb = QLabel("no reference picture yet")
        self._thumb.setMinimumSize(320, 150)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setStyleSheet(
            "QLabel { background: #ffffff; color: #666;"
            " border: 1px solid #b6b6b6; }")
        self._btn_snap = U.button("Take the picture again", icon_name="camera",
                                  tip="Photograph the area now and keep that "
                                      "as the reference. Use it when the "
                                      "screen has legitimately changed.")
        self._btn_snap.clicked.connect(self._snap)
        grid.addWidget(QLabel("Reference"), r, 0)
        grid.addWidget(self._thumb, r, 1)
        grid.addWidget(self._btn_snap, r, 2, Qt.AlignmentFlag.AlignTop)
        r += 1

        # A fresh picture of a screen that has not changed looks exactly like
        # the old one, so the only proof the button did anything is the time it
        # says here. Without it "Take the picture again" reads as a dead button.
        self._ref_note = QLabel("")
        self._ref_note.setStyleSheet(f"color: {U.QUIET}; font-size: 11px;")
        grid.addWidget(self._ref_note, r, 1)
        r += 1

        self._thr = QDoubleSpinBox()
        self._thr.setRange(0.1, 50.0)
        self._thr.setSingleStep(0.5)
        self._thr.setDecimals(1)
        self._thr.setValue(float(self.draft.get("threshold")
                                 or C.DEFAULT_THRESHOLD))
        self._thr.setToolTip(
            "The average difference, 0 to 255, that counts as a change.\n"
            "Lower is more sensitive. Camera noise, anti-aliased text and the "
            "mouse pointer all count as difference, so the usual answer is a "
            "tight rectangle rather than a high number.")
        grid.addWidget(QLabel("Sensitivity"), r, 0)
        grid.addWidget(self._thr, r, 1)
        r += 1

        self._fires = QComboBox()
        for key in C.FIRES:
            self._fires.addItem(_FIRES_WORDS[key], key)
        self._fires.setCurrentIndex(
            max(0, list(C.FIRES).index(self.draft.get("fires") or "alarm")))
        grid.addWidget(QLabel("When it changes"), r, 0)
        grid.addWidget(self._fires, r, 1)
        r += 1

        # This is where the Group box was. Groups are gone — presets took the
        # job over, and these are the tick boxes that replaced them.
        self._presets = P.PresetBox(self, win.config(),
                                    self.draft.get("presets") or [])
        grid.addWidget(QLabel("Presets"), r, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self._presets, r, 1)
        r += 1

        self._on = QCheckBox("Watch this one")
        self._on.setStyleSheet(U.CHK_QSS)
        self._on.setChecked(bool(self.draft.get("on", True)))
        grid.addWidget(self._on, r, 1)
        r += 1

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Save")
        ok.setStyleSheet(U.PRIMARY_QSS)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(U.BTN_QSS)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._refresh_where()

    # ── drawing and snapping ────────────────────────────────────────────────
    def _refresh_where(self):
        region = self.draft.get("region")
        if not region:
            self._where.setText("not drawn yet")
        else:
            w = int(region[2]) - int(region[0])
            h = int(region[3]) - int(region[1])
            screen = self.draft.get("screen") or ""
            self._where.setText(
                f"Monitor {int(self.draft.get('monitor', 0)) + 1} · "
                f"{w}x{h} px at {int(region[0])},{int(region[1])}"
                + (f"\n{screen}" if screen else ""))
        img = C.decode_reference(self.draft.get("reference"))
        if img is None:
            # The pixmap is cleared FIRST. A QLabel shows one or the other, and
            # setting even an empty pixmap after the text throws the text away —
            # which left this box blank and saying nothing about why.
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("no reference picture yet — this area cannot "
                                "fire until one is taken")
        else:
            self._thumb.setText("")
            self._thumb.setPixmap(S.framed_pixmap(img, 320, 150))

    def _draw(self):
        """Dim the desktop and wait for a rectangle.

        This dialog is shown with `show()` and NOT with `exec()`, and that is
        the reason it may hide itself here at all: `QDialog.hide()` ends an
        `exec()` loop, which used to return Rejected the instant this button was
        pressed. The dialog came back, the rectangle was drawn, Save was pressed
        — and the caller had already given up on it, so every area the operator
        ever drew was thrown away. See `AreasTab._open`.
        """
        if self._busy():
            return
        self._set_busy(True)
        # Both windows out of the way first, or the program photographs itself.
        self._hid_windows = True
        self.hide()
        self._win.hide()
        QTimer.singleShot(_HIDE_BEFORE_SELECT_MS, self._open_selector)

    def done(self, code):
        """Closing this editor takes its half-drawn rectangle with it.

        Without this, closing the editor within the 150 ms before the overlays
        appear left a selector nobody owned: three dimmed screens with no way
        to report back, and — because one drag at a time is the rule — "Draw
        the area" refused to work at all until it timed out.
        """
        self._finished = True
        sel, self._selector = self._selector, None
        if sel is not None:
            sel.take_down()
        if self._hid_windows:
            # We hid it, so we put it back. Otherwise closing the editor in the
            # middle of a drag leaves no window on screen at all, which is the
            # dead end this whole path is supposed to have stopped having.
            self._hid_windows = False
            self._win.show()
            self._win.raise_()
            self._win.activateWindow()
        super().done(code)

    def _busy(self):
        """True while a rectangle is being dragged out, anywhere."""
        return self._selector is not None or S.selection_in_progress()

    def _set_busy(self, busy):
        self._btn_draw.setEnabled(not busy)
        self._btn_snap.setEnabled(not busy)

    def _open_selector(self):
        if self._finished:
            return                      # the editor closed inside the delay
        # Never overwrite a live selector: the overlays it holds are parentless
        # top-level windows, and dropping the old selector would destroy three
        # VISIBLE windows on the spot. Two quick presses of "Draw the area" did
        # exactly that, and Qt faulted on the next event.
        if self._selector is not None:
            return
        sel = S.RegionSelector(self._picked, self._selector_done)
        self._selector = sel
        sel.show()

    def _picked(self, region, info):
        if self._finished:
            return
        self.draft["region"] = region
        self.draft["monitor"] = info.index
        self.draft["screen"] = info.name
        # A new rectangle makes the old picture meaningless, so a new one is
        # taken straight away — that is what the drawing was for.
        QTimer.singleShot(_HIDE_BEFORE_GRAB_MS, self._grab_reference)

    def _selector_done(self):
        self._selector = None
        if self._finished:
            return
        # Long enough that the dimming overlay is really gone before anything
        # is photographed.
        QTimer.singleShot(_HIDE_BEFORE_GRAB_MS + 60, self._come_back)

    def _come_back(self):
        if self._finished:
            return
        self._set_busy(False)
        self._hid_windows = False
        self._win.show()
        self.show()
        self.raise_()
        self.activateWindow()
        self._refresh_where()
        # Held back until there is a window to put it on. A QMessageBox parented
        # to a hidden dialog runs its own event loop from behind nothing, and
        # the operator cannot see what it is asking.
        problem, self._problem = self._problem, None
        if problem:
            QMessageBox.warning(self, "Too big to store", problem)

    def _grab_reference(self):
        if self._finished:
            return
        img, err = S.grab_rect(self.draft.get("region"))
        if err is not None:
            self._win.log(f"The reference picture could not be taken: {err}")
            return
        text, problem = C.encode_reference(img)
        if problem:
            # A refusal, not a reset: whatever reference was there stays.
            self._win.log(problem)
            self._problem = problem
            return
        self.draft["reference"] = text
        self._refresh_where()
        self._ref_note.setText(
            f"picture taken at {time.strftime('%H:%M:%S')}")

    def _snap(self):
        if not self.draft.get("region"):
            QMessageBox.warning(self, "No area",
                                "Draw the area first.")
            return
        if self._busy():
            return
        self._set_busy(True)
        self._hid_windows = True
        self.hide()
        self._win.hide()
        QTimer.singleShot(_HIDE_BEFORE_GRAB_MS, self._snap_now)

    def _snap_now(self):
        if self._finished:
            return
        self._grab_reference()
        self._come_back()

    # ── saving ──────────────────────────────────────────────────────────────
    def _save(self):
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "No name",
                                "Give it a name, so you can tell it apart in "
                                "the list.")
            return
        if not self.draft.get("region"):
            QMessageBox.warning(self, "No area", "Draw the area first.")
            return
        d = self.draft
        d["name"] = name
        d["message"] = self._msg.text().strip()
        d["threshold"] = float(self._thr.value())
        d["fires"] = self._fires.currentData()
        d["presets"] = self._presets.chosen()
        d["on"] = self._on.isChecked()
        if not d.get("reference"):
            if QMessageBox.question(
                    self, "No reference picture",
                    "Without a reference picture this can never fire — there "
                    "is nothing to compare against.\n\nSave it anyway?"
            ) != QMessageBox.StandardButton.Yes:
                return
        self.accept()


class AreasTab(QWidget):

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._updating = False
        self._dlg = None
        # id → the clock time its reference picture was last retaken, this run.
        self._snapped = {}
        self._build()
        self.reload()
        # The preview has its OWN clock, and a slow one. It used to hang off
        # `engine().changed`, which fires up to four times a second — and each
        # refresh photographs the whole desktop (measured: 269 ms and 30 MB a
        # time, because PIL always grabs everything and crops afterwards). That
        # asked the window's own thread for more than a second of work per
        # second, on top of the same grabs on the worker.
        self._pv_clock = QTimer(self)
        self._pv_clock.setInterval(1000)
        self._pv_clock.timeout.connect(self._refresh_preview)
        self._pv_clock.start()

    # ── building ────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        row = QHBoxLayout()
        self._btn_add = U.button("Add area", icon_name="plus",
                                 tip="Drag out another rectangle to watch.")
        self._btn_add.clicked.connect(self._add)
        row.addWidget(self._btn_add)
        self._btn_edit = U.button("Edit", icon_name="pencil")
        self._btn_edit.clicked.connect(self._edit)
        row.addWidget(self._btn_edit)
        self._btn_snap = U.button("Take the picture again", icon_name="camera",
                                  tip="Photograph the selected area now and "
                                      "keep that as its reference.")
        self._btn_snap.clicked.connect(self._resnap)
        row.addWidget(self._btn_snap)
        # Remove IS a button, again — see the same note in `vl_t`. It is on
        # both the button row and the rows' right-click menu, and on either it
        # takes every row that is picked. "Assign to a preset" stays on the
        # menu alone.
        self._btn_remove = U.button("Remove", icon_name="trash", danger=True,
                                    tip="Take the picked areas off the list.")
        self._btn_remove.clicked.connect(self._remove)
        row.addWidget(self._btn_remove)
        row.addSpacing(16)

        row.addWidget(U.section_label("saved rectangles"))
        self._presets = QComboBox()
        self._presets.setMinimumWidth(150)
        self._presets.setToolTip(
            "Rectangles kept by name, from this version and every earlier one. "
            "Load puts one into the selected area.")
        row.addWidget(self._presets)
        self._btn_p_load = U.button("Load", icon_name="refresh",
                                    tip="Put this saved rectangle into the "
                                        "selected area and take a fresh "
                                        "reference picture.")
        self._btn_p_load.clicked.connect(self._preset_load)
        row.addWidget(self._btn_p_load)
        self._btn_p_save = U.button("Save", icon_name="save",
                                    tip="Keep the selected area's rectangle "
                                        "under a name.")
        self._btn_p_save.clicked.connect(self._preset_save)
        row.addWidget(self._btn_p_save)
        self._btn_p_del = U.button("Delete", icon_name="trash", danger=True,
                                   tip="Forget this saved rectangle. The "
                                       "watched areas are untouched.")
        self._btn_p_del.clicked.connect(self._preset_delete)
        row.addWidget(self._btn_p_del)
        row.addStretch(1)
        root.addLayout(row)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, stretch=1)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        hh = self._table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        hh.setMinimumSectionSize(24)
        # The sensitivity number sits in the middle of its column, so its
        # heading does too.
        self._table.horizontalHeaderItem(_C_THR).setTextAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        # Every column as wide as its widest cell, heading included — and every
        # one of them still draggable. See `U.ColumnFitter` for why neither of
        # Qt's automatic modes will do: one cannot be dragged and the other
        # ignores the contents. `fit()` is called at the end of every reload.
        self._fit = U.ColumnFitter(self._table)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(U.TABLE_QSS)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.doubleClicked.connect(lambda _i: self._edit())
        self._table.itemSelectionChanged.connect(self._on_selection)
        self._build_row_menu()
        split.addWidget(self._table)

        split.addWidget(self._build_preview())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([800, 420])

    def _build_row_menu(self):
        """Right-click a row: what can be done to the rows that are picked.

        Remove and "Assign to a preset" were buttons over the table. They are
        about a SET of rows and nothing else, so they belong on the rows — and
        the count in each label is what the operator checks before pressing it.
        """
        self._row_menu = U.RowMenu(self._table, [
            ("Edit", "pencil", self._edit),
            ("Take the picture again", "camera", self._resnap),
            None,
            (lambda n: "Assign to a preset" if n == 1
             else f"Assign these {n} to a preset", "gear", self._assign),
            (lambda n: "Remove" if n == 1 else f"Remove these {n}",
             "trash", self._remove),
        ])

    def _build_preview(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(U.section_label("the selected area, right now"))
        self._pv_now = self._picture_label("nothing selected")
        lay.addWidget(self._pv_now)
        lay.addWidget(U.section_label("its reference picture"))
        self._pv_ref = self._picture_label("")
        lay.addWidget(self._pv_ref)
        self._pv_state = QLabel("")
        self._pv_state.setWordWrap(True)
        self._pv_state.setMinimumHeight(46)
        lay.addWidget(self._pv_state)
        # See the same note in the editor: a retaken picture of an unchanged
        # screen is indistinguishable from the old one, so the time it happened
        # is the only visible proof.
        self._pv_note = QLabel("")
        self._pv_note.setWordWrap(True)
        self._pv_note.setStyleSheet(f"color: {U.QUIET}; font-size: 11px;")
        lay.addWidget(self._pv_note)
        lay.addStretch(1)
        return box

    def _picture_label(self, text):
        lbl = QLabel(text)
        lbl.setMinimumHeight(170)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Both colours stated: on this PC an unpainted label is black on black.
        lbl.setStyleSheet("QLabel { background: #ffffff; color: #666666;"
                          " border: 1px solid #b6b6b6; }")
        return lbl

    # ── the list ────────────────────────────────────────────────────────────
    def _areas(self):
        """The rows on screen: areas, in the preset being worked in.

        Every row number in this tab is an index into THIS list, so the filter
        belongs here and nowhere else.
        """
        return [it for it in self._win.active_items()
                if it.get("kind") == "area"]

    def reload(self):
        items = self._areas()
        self._updating = True
        try:
            self._table.clearSpans()
            self._table.setRowCount(len(items))
            for row, it in enumerate(items):
                self._fill_row(row, it)
            chosen = self._win.preset()
            U.empty_table_note(
                self._table,
                "No screen area is being watched yet — press Add area and drag "
                "out a rectangle."
                if chosen == C.PRESET_ALL else
                f'No screen area in the preset "{P.preset_word(chosen)}" — '
                f'press Add area, or pick another preset in the header.')
        finally:
            self._updating = False
        self._fit.fit()
        self._refresh_presets()
        self._on_selection()

    def _fill_row(self, row, item):
        for col in range(len(_COLS)):
            cell = self._table.item(row, col)
            if cell is None:
                cell = QTableWidgetItem("")
                self._table.setItem(row, col, cell)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if col == _C_ON:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            elif col in (_C_NAME, _C_MSG, _C_THR):
                flags |= Qt.ItemFlag.ItemIsEditable
            cell.setFlags(flags)
            cell.setForeground(QColor(U.INK))
            if col == _C_THR:
                cell.setBackground(QColor("#fffbe8"))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignHCenter
                                      | Qt.AlignmentFlag.AlignVCenter)
            else:
                cell.setData(Qt.ItemDataRole.BackgroundRole, None)

        region = item.get("region")
        self._table.item(row, _C_ON).setData(Qt.ItemDataRole.UserRole,
                                             int(item["id"]))
        self._table.item(row, _C_ON).setCheckState(
            Qt.CheckState.Checked if item.get("on") else Qt.CheckState.Unchecked)
        self._table.item(row, _C_NAME).setText(item.get("name") or "(unnamed)")
        if region:
            w = int(region[2]) - int(region[0])
            h = int(region[3]) - int(region[1])
            self._table.item(row, _C_RECT).setText(
                f"Monitor {int(item.get('monitor', 0)) + 1} · "
                f"{w}x{h} at {int(region[0])},{int(region[1])}")
        else:
            self._table.item(row, _C_RECT).setText("not drawn yet")
        self._table.item(row, _C_THR).setText(
            f"{float(item.get('threshold', C.DEFAULT_THRESHOLD)):.1f}")
        ref_cell = self._table.item(row, _C_REF)
        if item.get("reference"):
            ref_cell.setText("taken")
            ref_cell.setForeground(QColor("#155724"))
        else:
            # Said plainly, because without one the area can never fire.
            ref_cell.setText("NO PICTURE")
            ref_cell.setForeground(QColor("#8d1c12"))
        self._table.item(row, _C_FIRES).setText(
            _FIRES_WORDS.get(item.get("fires"), ""))
        self._table.item(row, _C_MSG).setText(item.get("message") or "")
        tip = C.item_summary(item) + "\n\nDouble-click the row to change it."
        for col in range(len(_COLS)):
            self._table.item(row, col).setToolTip(tip)

    def select_item(self, item_id):
        for row, it in enumerate(self._areas()):
            if int(it["id"]) == int(item_id):
                self._table.selectRow(row)
                self._table.scrollToItem(self._table.item(row, 0))
                return

    def _selected(self):
        """The one selected row, or None. For the things that need exactly one.

        Editing a row, photographing it again, and saving or loading a rectangle
        are all questions about a single area, so they stay one-at-a-time.
        Removing is not — see `_selected_many`.
        """
        rows = {i.row() for i in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        items = self._areas()
        row = rows.pop()
        return items[row] if 0 <= row < len(items) else None

    def _selected_many(self):
        """Every selected row, in the order they appear in the table."""
        items = self._areas()
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        return [items[r] for r in rows if 0 <= r < len(items)]

    def _on_selection(self):
        one = self._selected() is not None
        for b in (self._btn_edit, self._btn_snap,
                  self._btn_p_save, self._btn_p_load):
            b.setEnabled(one)
        # Remove is the one button here that is not a question about a single
        # row: several areas can be picked, so it takes all of them.
        self._btn_remove.setEnabled(bool(self._selected_many()))
        self._btn_p_del.setEnabled(self._presets.count() > 0)
        self._btn_p_load.setEnabled(one and self._presets.count() > 0)
        self._refresh_preview()

    # ── the preview ─────────────────────────────────────────────────────────
    def _refresh_preview(self):
        if not self.isVisible():
            return                      # nothing to draw for a hidden tab
        if S.selection_in_progress():
            return                      # the desktop is dimmed; do not photograph it
        item = self._selected()
        if item is None:
            # "Nothing selected" is a lie when several rows ARE selected, and
            # it is the moment the operator is most likely to think the program
            # has lost the selection.
            many = len(self._selected_many())
            self._pv_now.setPixmap(QPixmap())
            self._pv_now.setText(
                f"{many} areas selected — pick one to see it"
                if many > 1 else "nothing selected")
            self._pv_ref.setPixmap(QPixmap())
            self._pv_ref.setText("")
            self._pv_state.setText("")
            self._pv_state.setStyleSheet("")
            self._pv_note.setText("")
            return
        img, err = S.grab_rect(item.get("region"))
        if img is None:
            self._pv_now.setPixmap(QPixmap())
            self._pv_now.setText(err or "nothing to show")
        else:
            self._pv_now.setText("")
            self._pv_now.setPixmap(S.framed_pixmap(img, 400, 170))
        ref = C.decode_reference(item.get("reference"))
        if ref is None:
            self._pv_ref.setPixmap(QPixmap())
            self._pv_ref.setText("no reference picture — it cannot fire")
        else:
            self._pv_ref.setText("")
            self._pv_ref.setPixmap(S.framed_pixmap(ref, 400, 170))

        st = self._win.engine().state_of(int(item["id"]))
        ground, ink = U.STATE_COLORS.get(st.state, U.STATE_COLORS["unknown"])
        thr = float(item.get("threshold", C.DEFAULT_THRESHOLD))
        live = "—" if st.diff is None else f"{st.diff:.2f}"
        self._pv_state.setText(f"difference now {live} · sensitivity {thr:.1f}\n"
                               f"{st.text}")
        self._pv_state.setStyleSheet(
            f"QLabel {{ background: {ground}; color: {ink};"
            f" border: 1px solid {ink}; border-radius: 3px; padding: 4px 6px;"
            f" font-weight: 600; }}")
        when = self._snapped.get(int(item["id"]))
        self._pv_note.setText(
            f"reference picture taken at {when}" if when else "")

    # ── editing in the table ────────────────────────────────────────────────
    def _on_item_changed(self, cell):
        if self._updating:
            return
        row, col = cell.row(), cell.column()
        items = self._areas()
        if not (0 <= row < len(items)):
            return
        item = items[row]
        if col == _C_ON:
            item["on"] = cell.checkState() == Qt.CheckState.Checked
        elif col == _C_NAME:
            item["name"] = cell.text().strip() or "(unnamed)"
        elif col == _C_MSG:
            item["message"] = cell.text().strip()
        elif col == _C_THR:
            value = C.parse_level(cell.text())
            item["threshold"] = max(0.1, value) if value is not None \
                else C.DEFAULT_THRESHOLD
            self._updating = True
            cell.setText(f"{item['threshold']:.1f}")
            self._updating = False
        else:
            return
        self._win.save_soon()
        self._win.refresh_tabs()

    # ── the buttons ─────────────────────────────────────────────────────────
    def _add(self):
        self._open(None)

    def _edit(self):
        item = self._selected()
        if item is not None:
            self._open(item)

    def _open(self, item):
        """Open the editor. `item` is None for a new area, the row to change
        otherwise.

        Deliberately `show()` and a `finished` signal rather than `exec()`. The
        editor has to hide itself while the operator drags the rectangle out —
        otherwise the program photographs its own window — and hiding a dialog
        ENDS its `exec()` loop. So `exec()` returned "Cancel" the moment "Draw
        the area" was pressed, and everything done afterwards, the rectangle and
        the Save, went nowhere. Nothing was ever saved from this dialog.
        """
        if self._dlg is not None:
            # One editor at a time — but a button that does nothing at all is
            # indistinguishable from a broken program, so put the one that is
            # already open in front instead.
            self._dlg.show()
            self._dlg.raise_()
            self._dlg.activateWindow()
            return
        # A new area joins the preset being worked in, or it would vanish the
        # moment it was saved.
        chosen = self._win.preset()
        here = ([] if chosen in (C.PRESET_ALL, C.PRESET_UNASSIGNED)
                else [chosen])
        draft = item if item is not None else C.new_area_item(
            C.next_id(self._win.items()), "", presets=here)
        dlg = AreaDialog(self, self._win, draft)
        self._dlg = dlg
        dlg.finished.connect(lambda code, d=dlg, it=item:
                             self._dlg_finished(d, it, code))
        # A second way out of "an editor is open". `finished` is the normal one,
        # but if the dialog is ever destroyed without emitting it, this button
        # would refuse to open another one for the rest of the session.
        dlg.destroyed.connect(self._dlg_gone)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _dlg_gone(self, *_a):
        self._dlg = None

    def _dlg_finished(self, dlg, item, code):
        self._dlg = None
        if code == int(QDialog.DialogCode.Accepted):
            if item is None:
                self._win.items().append(dlg.draft)
                self._commit(dlg.draft)
            else:
                item.update(dlg.draft)
                self._win.engine().forget_reference(int(item["id"]))
                self._commit(item)
        # `retire`, not `deleteLater`: the dialog has posted events of its own
        # (it was just hidden) and the reference here is about to go. See the
        # note on `S.retire`.
        S.retire(dlg)

    def _resnap(self):
        item = self._selected()
        if item is None:
            return
        if not item.get("region"):
            QMessageBox.warning(self, "No area",
                                "This area has no rectangle yet — press Edit "
                                "and draw one.")
            return
        self._win.hide()
        QTimer.singleShot(_HIDE_BEFORE_GRAB_MS,
                          lambda: self._resnap_now(item))

    def _resnap_now(self, item, was_hidden=True):
        img, err = S.grab_rect(item.get("region"))
        if was_hidden:
            self._win.show()
            self._win.raise_()
        if err is not None:
            self._win.log(f"The reference picture could not be taken: {err}")
            return
        text, problem = C.encode_reference(img)
        if problem:
            self._win.log(problem)
            QMessageBox.warning(self, "Too big to store", problem)
            return
        item["reference"] = text
        self._snapped[int(item["id"])] = time.strftime("%H:%M:%S")
        self._win.engine().forget_reference(int(item["id"]))
        self._win.log(f'Took a new reference picture for '
                      f'"{item.get("name") or "an area"}".')
        self._commit(item)

    def _remove(self):
        doomed = self._selected_many()
        if not doomed:
            return
        if len(doomed) == 1:
            what = f'"{doomed[0].get("name") or "this area"}"'
            title, question = "Remove it", f'Take {what} off the list?'
        else:
            what = f"{len(doomed)} areas"
            title = "Remove them"
            question = (f"Take these {len(doomed)} off the list?\n\n"
                        + U.name_list(doomed, "an area"))
        if QMessageBox.question(
                self, title,
                f'{question}\n\nThe reference pictures go with them. The saved '
                f'rectangles are not touched.'
        ) != QMessageBox.StandardButton.Yes:
            return
        C.drop_items(self._win.items(), doomed)
        self._win.log(f"Removed {what}.")
        self._commit(None)

    def _assign(self):
        chosen = self._selected_many()
        if not chosen:
            return
        dlg = P.AssignDialog(self, self._win.config(), chosen)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dlg.apply()
        self._commit(None)

    # ── the saved rectangles ────────────────────────────────────────────────
    def _refresh_presets(self):
        keep = self._presets.currentText()
        self._presets.clear()
        names = C.preset_names(self._win.config())
        self._presets.addItems(names)
        if keep in names:
            self._presets.setCurrentText(keep)

    def _preset_save(self):
        item = self._selected()
        if item is None or not item.get("region"):
            QMessageBox.warning(self, "Nothing to save",
                                "Select an area that has a rectangle first.")
            return
        name, ok = QInputDialog.getText(
            self, "Save the rectangle",
            "A name for this rectangle:", text=item.get("name") or "")
        name = (name or "").strip()
        if not ok or not name:
            return
        cfg = self._win.config()
        if name in C.preset_names(cfg):
            if QMessageBox.question(
                    self, "Already there",
                    f'"{name}" is already saved. Replace its rectangle?'
            ) != QMessageBox.StandardButton.Yes:
                return
        problem = C.set_preset_region(cfg, name, item["region"])
        if problem:
            QMessageBox.warning(self, "Not that name", problem)
            return
        self._win.save_soon()
        self._refresh_presets()
        self._presets.setCurrentText(name)
        self._win.log(f'Saved the rectangle as "{name}".')

    def _preset_load(self):
        item = self._selected()
        name = self._presets.currentText()
        if item is None or not name:
            return
        region = C.preset_region(self._win.config(), name)
        if region is None:
            QMessageBox.warning(self, "Nothing in it",
                                f'"{name}" does not hold a rectangle.')
            return
        item["region"] = region
        screen = S.screen_for_region(region)
        if screen is not None:
            item["monitor"] = screen.index
            item["screen"] = screen.name
        else:
            self._win.log(f'"{name}" points at a place no screen covers any '
                          f'more — the area was loaded, but check it.')
        self._win.log(f'Loaded "{name}" into "{item.get("name")}". '
                      f'Taking a fresh reference picture.')
        # Straight away, with the window where it is. Hiding and coming back
        # looked like the program had fallen over and restarted itself, and it
        # bought nothing: while watching, the picture is taken with this window
        # on screen too, so a reference taken the same way is the one that
        # matches.
        self._resnap_now(item, was_hidden=False)

    def _preset_delete(self):
        name = self._presets.currentText()
        if not name:
            return
        if QMessageBox.question(
                self, "Forget it",
                f'Forget the saved rectangle "{name}"?\n\nThe watched areas '
                f'are not touched.') != QMessageBox.StandardButton.Yes:
            return
        self._win.config().pop(name, None)
        self._win.save_soon()
        self._refresh_presets()
        self._win.log(f'Forgot the saved rectangle "{name}".')

    def _commit(self, item):
        # A preset may have been made inside the editor or the Assign box, so
        # the header's drop-down is put back in step before anything reloads.
        self._win.refresh_presets()
        self._win.engine().drop_missing()
        self._win.save_soon()
        self._win.refresh_tabs()
        if item is not None:
            self.select_item(int(item["id"]))
