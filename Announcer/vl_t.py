"""Announcer — the Values tab: the archived numbers and their limits.

The four limits are edited IN THE TABLE, by clicking the cell and typing. That
is what "easily change a limit" means, and it is the one thing this program is
asked to make easy. Everything else about a value — its channel, how the window
is reduced, whether it raises the alarm — is in the editor behind Add and Edit,
because those are decisions, not numbers to nudge.

An empty limit box means that limit is OFF. It never means zero: a zero limit on
an energy fires the moment the laser runs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import ann_core as C
import ann_cpva as A
import ann_presets as P
import ann_ui as U

_COLS = ["On", "Name", "Say when it fires", "Lo Lo", "Lo", "Hi", "Hi Hi",
         "Unit", "Channel", "Fires", "How read"]
(_C_ON, _C_NAME, _C_MSG, _C_LOLO, _C_LO, _C_HI, _C_HIHI, _C_UNIT, _C_PV,
 _C_FIRES, _C_READ) = range(11)

_LIMIT_COLS = {_C_LOLO: "lo_lo", _C_LO: "lo", _C_HI: "hi", _C_HIHI: "hi_hi"}
_TEXT_COLS = {_C_UNIT: "unit", _C_NAME: "name", _C_MSG: "message"}
# The tick box and the four limits sit in the middle of their own narrow
# columns, heading included — a right-aligned number under a left-aligned
# heading looked like it belonged to the column next door.
_CENTER_COLS = {_C_ON, _C_LOLO, _C_LO, _C_HI, _C_HIHI}

_READ_WORDS = {"peak": "worst in the window",
               "mean": "average of the newest few",
               "last": "newest value, however old"}
_FIRES_WORDS = {"show": "shows only", "alarm": "raises the alarm"}


# ─────────────────────────────────────────────────────────────────────────────
# Finding a channel by name
# ─────────────────────────────────────────────────────────────────────────────

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
        self._count.setText("Asking the archiver for the channel list…")
        try:
            self._names = A.fetch_channels(timeout=4.0)
        except Exception as exc:
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


# ─────────────────────────────────────────────────────────────────────────────
# The editor
# ─────────────────────────────────────────────────────────────────────────────

class ValueDialog(QDialog):
    """One watched value, start to finish. Nothing is written until Save."""

    def __init__(self, parent, draft, cfg):
        super().__init__(parent)
        self.setWindowTitle("Watched value")
        self.setModal(True)
        self.setMinimumWidth(620)
        U.paint_dialog(self)
        self.draft = dict(draft)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        r = 0

        self._name = QLineEdit(self.draft.get("name") or "")
        grid.addWidget(QLabel("Name"), r, 0)
        grid.addWidget(self._name, r, 1, 1, 3)
        r += 1

        self._msg = QLineEdit(self.draft.get("message") or "")
        self._msg.setPlaceholderText("what you want to read when it fires")
        grid.addWidget(QLabel("Say when it fires"), r, 0)
        grid.addWidget(self._msg, r, 1, 1, 3)
        r += 1

        self._pv = QLineEdit(self.draft.get("pv") or "")
        find = U.button("Find", icon_name="eye",
                        tip="Search the archiver's channel list.")
        find.clicked.connect(lambda: self._find_into(self._pv))
        grid.addWidget(QLabel("Channel"), r, 0)
        grid.addWidget(self._pv, r, 1, 1, 2)
        grid.addWidget(find, r, 3)
        r += 1

        self._diff_on = QCheckBox("Watch the difference between two channels")
        self._diff_on.setStyleSheet(U.CHK_QSS)
        self._diff_on.setToolTip(
            "For a chiller: the temperature minus its setpoint. The second "
            "channel is held forward, so a setpoint written once a week still "
            "counts.")
        self._diff_on.setChecked(bool(self.draft.get("minus")))
        self._diff_on.toggled.connect(self._toggle_diff)
        grid.addWidget(self._diff_on, r, 1, 1, 3)
        r += 1

        self._minus = QLineEdit(self.draft.get("minus") or "")
        self._minus_lbl = QLabel("minus")
        find2 = U.button("Find", icon_name="eye")
        find2.clicked.connect(lambda: self._find_into(self._minus))
        self._minus_find = find2
        grid.addWidget(self._minus_lbl, r, 0)
        grid.addWidget(self._minus, r, 1, 1, 2)
        grid.addWidget(find2, r, 3)
        r += 1

        self._read_out = QLabel("")
        self._read_out.setWordWrap(True)
        self._read_out.setStyleSheet(f"color: {U.QUIET};")
        read_now = U.button("Read now", icon_name="camera",
                            tip="Read this channel from the archiver and show "
                                "what it holds for the last minute.")
        read_now.clicked.connect(self._read_now)
        row = QHBoxLayout()
        row.addWidget(read_now)
        row.addWidget(self._read_out, stretch=1)
        grid.addLayout(row, r, 1, 1, 3)
        r += 1

        grid.addWidget(U.hsep(), r, 0, 1, 4)
        r += 1

        # ── the limits ─────────────────────────────────────────────────────
        grid.addWidget(QLabel("Limits"), r, 0)
        lim = QHBoxLayout()
        self._limits = {}
        for key, caption, tip in (
                ("lo_lo", "Lo Lo", "below this it fires"),
                ("lo", "Lo", "below this it only warns"),
                ("hi", "Hi", "above this it only warns"),
                ("hi_hi", "Hi Hi", "above this it fires")):
            box = QVBoxLayout()
            cap = QLabel(caption)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setStyleSheet("font-size: 10px; font-weight: 700; color: #444;")
            edit = QLineEdit(C.level_text(self.draft.get(key)))
            edit.setFixedWidth(84)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.setPlaceholderText("off")
            edit.setToolTip(f"{tip}. Leave it empty to switch this limit off.")
            self._limits[key] = edit
            box.addWidget(cap)
            box.addWidget(edit)
            lim.addLayout(box)
        self._unit = QLineEdit(self.draft.get("unit") or "")
        self._unit.setFixedWidth(70)
        ubox = QVBoxLayout()
        ucap = QLabel("Unit")
        ucap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ucap.setStyleSheet("font-size: 10px; font-weight: 700; color: #444;")
        ubox.addWidget(ucap)
        ubox.addWidget(self._unit)
        lim.addLayout(ubox)
        lim.addStretch(1)
        grid.addLayout(lim, r, 1, 1, 3)
        r += 1

        note = QLabel("An empty box switches that limit off. It is not zero.")
        note.setStyleSheet(f"color: {U.QUIET}; font-size: 11px;")
        grid.addWidget(note, r, 1, 1, 3)
        r += 1

        grid.addWidget(U.hsep(), r, 0, 1, 4)
        r += 1

        # ── how it is read ─────────────────────────────────────────────────
        self._read = QComboBox()
        for mode in C.READ_MODES:
            self._read.addItem(_READ_WORDS[mode], mode)
        self._read.setCurrentIndex(
            max(0, list(C.READ_MODES).index(self.draft.get("read") or "peak")))
        self._read.setToolTip(
            "worst in the window — one shot over the limit is the whole event\n"
            "average of the newest few — steadier; a single chiller sample "
            "crosses its limit constantly\n"
            "newest value, however old — for a channel written only when it "
            "changes: a setpoint, a state, a valve")
        self._read.currentIndexChanged.connect(self._toggle_window)
        grid.addWidget(QLabel("How it is read"), r, 0)
        grid.addWidget(self._read, r, 1, 1, 2)
        r += 1

        self._window = QDoubleSpinBox()
        self._window.setRange(1.0, 600.0)
        self._window.setDecimals(0)
        self._window.setSuffix(" s")
        self._window.setValue(float(self.draft.get("window_s")
                                    or C.DEFAULT_WINDOW_S))
        self._window_lbl = QLabel("Window")
        grid.addWidget(self._window_lbl, r, 0)
        grid.addWidget(self._window, r, 1)
        r += 1

        self._fires = QComboBox()
        for key in C.FIRES:
            self._fires.addItem(_FIRES_WORDS[key], key)
        self._fires.setCurrentIndex(
            max(0, list(C.FIRES).index(self.draft.get("fires") or "alarm")))
        self._fires.setToolTip(
            "shows only — the row and the badge go red, nothing flashes\n"
            "raises the alarm — that, plus the flashing window and the sound")
        grid.addWidget(QLabel("When it stops holding"), r, 0)
        grid.addWidget(self._fires, r, 1, 1, 2)
        r += 1

        self._presets = P.PresetBox(self, cfg, self.draft.get("presets") or [])
        grid.addWidget(QLabel("Presets"), r, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self._presets, r, 1, 1, 3)
        r += 1

        self._on = QCheckBox("Watch this one")
        self._on.setStyleSheet(U.CHK_QSS)
        self._on.setChecked(bool(self.draft.get("on", True)))
        grid.addWidget(self._on, r, 1, 1, 3)
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

        self._toggle_diff(self._diff_on.isChecked())
        self._toggle_window()

    def _toggle_diff(self, on):
        for w in (self._minus, self._minus_lbl, self._minus_find):
            w.setVisible(on)

    def _toggle_window(self):
        # "newest value" and "average" have no window to set, so the box is not
        # offered — a control that changes nothing reads as a broken control.
        mode = self._read.currentData()
        show = mode == "peak"
        self._window.setVisible(show)
        self._window_lbl.setVisible(show)

    def _find_into(self, edit):
        picker = ChannelPicker(self, edit.text().strip())
        if picker.exec() == QDialog.DialogCode.Accepted and picker.chosen:
            edit.setText(picker.chosen)

    def _read_now(self):
        pv = self._pv.text().strip()
        if not pv:
            self._read_out.setText("Type a channel name first.")
            return
        self._read_out.setText("reading…")
        item = dict(self.draft)
        item["pv"] = pv
        item["minus"] = (self._minus.text().strip()
                         if self._diff_on.isChecked() else None)
        item["read"] = self._read.currentData()
        item["window_s"] = float(self._window.value())
        # Synchronous on purpose: one read, four seconds at the very worst, and
        # the operator pressed a button and is waiting for its answer.
        reading = A.read_value(item, readable_error=C.readable_pv_error)
        if not reading.ok:
            self._read_out.setText(reading.error)
            self._read_out.setToolTip(reading.hint or "")
        elif reading.value is None:
            self._read_out.setText("The archiver holds nothing for this window. "
                                   "That is not the same as a failure — the "
                                   "channel may simply not have been written.")
        else:
            unit = self._unit.text().strip()
            self._read_out.setText(
                f"now {reading.value:.6g}{(' ' + unit) if unit else ''}  "
                f"(from {reading.samples} sample"
                f"{'s' if reading.samples != 1 else ''})")

    def _save(self):
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "No name",
                                "Give it a name, so you can tell it apart in "
                                "the list.")
            return
        pv = self._pv.text().strip()
        if not pv:
            QMessageBox.warning(self, "No channel",
                                "Type the channel name, or press Find.")
            return
        d = self.draft
        d["name"] = name
        d["message"] = self._msg.text().strip()
        d["pv"] = pv
        d["minus"] = (self._minus.text().strip()
                      if self._diff_on.isChecked() else None) or None
        d["unit"] = self._unit.text().strip()
        for key, edit in self._limits.items():
            d[key] = C.parse_level(edit.text())
        d["read"] = self._read.currentData()
        d["window_s"] = float(self._window.value())
        d["fires"] = self._fires.currentData()
        d["presets"] = self._presets.chosen()
        d["on"] = self._on.isChecked()
        if all(d.get(k) is None for k in ("lo_lo", "lo", "hi", "hi_hi")):
            if QMessageBox.question(
                    self, "No limit set",
                    "With no limit at all this can never say anything is "
                    "wrong — it will only show the number.\n\nSave it anyway?"
            ) != QMessageBox.StandardButton.Yes:
                return
        elif d["fires"] == "alarm" and d["lo_lo"] is None and d["hi_hi"] is None:
            if QMessageBox.question(
                    self, "Only a warning limit",
                    "It is set to raise the alarm, but only Lo and Hi are "
                    "filled in, and those only warn. Lo Lo or Hi Hi is what "
                    "fires.\n\nSave it anyway?"
            ) != QMessageBox.StandardButton.Yes:
                return
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# The tab
# ─────────────────────────────────────────────────────────────────────────────

class ValuesTab(QWidget):

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._updating = False
        self._shown_ids = []
        self._build()
        self.reload()

    # ── building ────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        row = QHBoxLayout()
        self._btn_add = U.button("Add value", icon_name="plus",
                                 tip="Watch another archived number.")
        self._btn_add.clicked.connect(self._add)
        row.addWidget(self._btn_add)
        self._btn_edit = U.button("Edit", icon_name="pencil")
        self._btn_edit.clicked.connect(self._edit)
        row.addWidget(self._btn_edit)
        self._btn_dup = U.button("Duplicate", icon_name="copy",
                                 tip="A copy of this row, ready to be pointed "
                                     "at another channel.")
        self._btn_dup.clicked.connect(self._duplicate)
        row.addWidget(self._btn_dup)
        # Remove IS a button, again. It was moved onto the rows' right-click
        # menu and the operator went looking for the button — "the Remove
        # button does not delete anything" (2026-09-21). It is on both: the
        # button here works on every row that is picked, exactly as the menu
        # entry does. "Assign to a preset" stays on the menu alone.
        self._btn_remove = U.button("Remove", icon_name="trash", danger=True,
                                    tip="Take the picked rows off the list.")
        self._btn_remove.clicked.connect(self._remove)
        row.addWidget(self._btn_remove)
        row.addSpacing(16)
        self._btn_standard = U.button("Restore the standard values",
                                      icon_name="refresh",
                                      tip="Put the helium, the alpha voltage "
                                          "and the twelve chiller rows back, "
                                          "if any of them have been removed.")
        self._btn_standard.clicked.connect(self._restore_standard)
        row.addWidget(self._btn_standard)
        row.addStretch(1)
        hint = QLabel("Click a limit and type. An empty box switches that "
                      "limit off.")
        hint.setStyleSheet(f"color: {U.QUIET};")
        row.addWidget(hint)
        root.addLayout(row)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        hh = self._table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        for col in _CENTER_COLS:
            head = self._table.horizontalHeaderItem(col)
            if head is not None:
                head.setTextAlignment(Qt.AlignmentFlag.AlignHCenter
                                      | Qt.AlignmentFlag.AlignVCenter)
        # The tick box does not follow the cell's alignment — see the delegate.
        self._table.setItemDelegateForColumn(_C_ON, U.CenteredCheckDelegate(
            self._table))
        # Every column as wide as its widest cell, heading included — and every
        # one of them still draggable. The hand-set widths that used to be here
        # were guesses: "Lo Lo" and the other three limits were pinned at 66 px
        # whatever the numbers in them, and the channel column could not be
        # dragged at all because it was on `ResizeToContents`. See
        # `U.ColumnFitter`. `fit()` runs at the end of every reload.
        self._fit = U.ColumnFitter(self._table)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(U.TABLE_QSS)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        self._build_row_menu()
        root.addWidget(self._table, stretch=1)

    def _build_row_menu(self):
        """Right-click a row: what can be done to the rows that are picked.

        Both of these were buttons over the table. They are about a SET of rows
        and nothing else, so they belong on the rows — and the count in each
        label is what the operator checks before pressing it.
        """
        self._row_menu = U.RowMenu(self._table, [
            ("Edit", "pencil", self._edit),
            ("Duplicate", "copy", self._duplicate),
            None,
            (lambda n: "Assign to a preset" if n == 1
             else f"Assign these {n} to a preset", "gear", self._assign),
            (lambda n: "Remove" if n == 1 else f"Remove these {n}",
             "trash", self._remove),
        ])

    # ── the list ────────────────────────────────────────────────────────────
    def _values(self):
        """The rows on screen: values, in the preset being worked in.

        Every row number in this tab is an index into THIS list, so the filter
        belongs here and nowhere else.
        """
        return [it for it in self._win.active_items()
                if it.get("kind") == "value"]

    def reload(self):
        items = self._values()
        self._updating = True
        try:
            self._table.clearSpans()
            self._table.setRowCount(len(items))
            for row, it in enumerate(items):
                self._fill_row(row, it)
            chosen = self._win.preset()
            U.empty_table_note(
                self._table,
                "No value is being watched — press Add value, or "
                '"Restore the standard values" for the machine\'s own.'
                if chosen == C.PRESET_ALL else
                f'No value in the preset "{P.preset_word(chosen)}" — press '
                f'Add value, or pick another preset in the header.')
            self._shown_ids = [int(it["id"]) for it in items]
        finally:
            self._updating = False
        self._fit.fit()
        self._sync_buttons()

    def _fill_row(self, row, item):
        for col in range(len(_COLS)):
            cell = self._table.item(row, col)
            if cell is None:
                cell = QTableWidgetItem("")
                self._table.setItem(row, col, cell)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if col == _C_ON:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            elif col in _LIMIT_COLS or col in _TEXT_COLS:
                flags |= Qt.ItemFlag.ItemIsEditable
            cell.setFlags(flags)
            # Both colours stated on every cell, so nothing is left to the
            # theme on a machine running Windows in dark mode.
            cell.setForeground(QColor(U.INK))
            if col in _CENTER_COLS:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignHCenter
                                      | Qt.AlignmentFlag.AlignVCenter)
            if col in _LIMIT_COLS:
                # The editable numbers get a faint tint, so it is visible which
                # cells can be typed into without having to click and find out.
                cell.setBackground(QColor("#fffbe8"))
            else:
                cell.setData(Qt.ItemDataRole.BackgroundRole, None)

        self._table.item(row, _C_ON).setData(Qt.ItemDataRole.UserRole,
                                             int(item["id"]))
        self._table.item(row, _C_ON).setCheckState(
            Qt.CheckState.Checked if item.get("on") else Qt.CheckState.Unchecked)
        self._table.item(row, _C_NAME).setText(item.get("name") or "(unnamed)")
        self._table.item(row, _C_PV).setText(C.channel_label(item))
        for col, key in _LIMIT_COLS.items():
            self._table.item(row, col).setText(C.level_text(item.get(key)))
        self._table.item(row, _C_UNIT).setText(item.get("unit") or "")
        self._table.item(row, _C_READ).setText(
            _READ_WORDS.get(item.get("read"), item.get("read") or "")
            + (f" ({item.get('window_s'):g} s)"
               if item.get("read") == "peak" and item.get("window_s") else ""))
        self._table.item(row, _C_FIRES).setText(
            _FIRES_WORDS.get(item.get("fires"), ""))
        self._table.item(row, _C_MSG).setText(item.get("message") or "")

        tip = C.item_summary(item) + "\n\nDouble-click the row to change it."
        for col in range(len(_COLS)):
            self._table.item(row, col).setToolTip(tip)

    def select_item(self, item_id):
        for row, it in enumerate(self._values()):
            if int(it["id"]) == int(item_id):
                self._table.selectRow(row)
                self._table.scrollToItem(self._table.item(row, 0))
                return

    def _selected(self):
        """The one selected row, or None. Editing and duplicating need exactly
        one; removing does not — see `_selected_many`."""
        rows = {i.row() for i in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        items = self._values()
        row = rows.pop()
        return items[row] if 0 <= row < len(items) else None

    def _selected_many(self):
        """Every selected row, in the order they appear in the table."""
        items = self._values()
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        return [items[r] for r in rows if 0 <= r < len(items)]

    def _sync_buttons(self):
        # Edit and Duplicate are questions about ONE row. Remove is not: if
        # several rows can be picked, Remove has to take all of them, so it is
        # live for one row or for twenty.
        one = self._selected() is not None
        for b in (self._btn_edit, self._btn_dup):
            b.setEnabled(one)
        self._btn_remove.setEnabled(bool(self._selected_many()))

    # ── editing in the table ────────────────────────────────────────────────
    def _on_item_changed(self, cell):
        if self._updating:
            return
        row, col = cell.row(), cell.column()
        items = self._values()
        if not (0 <= row < len(items)):
            return
        item = items[row]
        if col == _C_ON:
            item["on"] = cell.checkState() == Qt.CheckState.Checked
        elif col in _LIMIT_COLS:
            item[_LIMIT_COLS[col]] = C.parse_level(cell.text())
            # Written back, so a typed "1,5" shows as 1.5 and unreadable text
            # shows as empty rather than staying on screen as if it counted.
            self._updating = True
            cell.setText(C.level_text(item[_LIMIT_COLS[col]]))
            self._updating = False
        elif col in _TEXT_COLS:
            item[_TEXT_COLS[col]] = cell.text().strip()
        else:
            return
        self._win.save_soon()
        self._win.refresh_tabs()

    def _on_double_click(self, index):
        # A limit cell opens for typing; anything else opens the editor.
        if index.column() in _LIMIT_COLS or index.column() in _TEXT_COLS:
            return
        self._edit()

    # ── the buttons ─────────────────────────────────────────────────────────
    def _here(self):
        """The preset a new row joins: the one being worked in.

        Added while a preset is chosen, a row that did not join it would
        disappear the moment it was saved.
        """
        chosen = self._win.preset()
        return [] if chosen in (C.PRESET_ALL, C.PRESET_UNASSIGNED) else [chosen]

    def _add(self):
        draft = C.new_value_item(C.next_id(self._win.items()), "",
                                 C.DEFAULT_PV, presets=self._here())
        dlg = ValueDialog(self, draft, self._win.config())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._win.items().append(dlg.draft)
            self._commit(dlg.draft)

    def _edit(self):
        item = self._selected()
        if item is None:
            return
        dlg = ValueDialog(self, item, self._win.config())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            item.update(dlg.draft)
            self._commit(item)

    def _duplicate(self):
        item = self._selected()
        if item is None:
            return
        copy = dict(item)
        copy["id"] = C.next_id(self._win.items())
        copy["name"] = f"{item.get('name') or 'value'} (copy)"
        dlg = ValueDialog(self, copy, self._win.config())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._win.items().append(dlg.draft)
            self._commit(dlg.draft)

    def _assign(self):
        chosen = self._selected_many()
        if not chosen:
            return
        dlg = P.AssignDialog(self, self._win.config(), chosen)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dlg.apply()
        self._commit(None)

    def _remove(self):
        doomed = self._selected_many()
        if not doomed:
            return
        if len(doomed) == 1:
            what = f'"{doomed[0].get("name") or "this value"}"'
            title, question = "Remove it", f'Take {what} off the list?'
        else:
            what = f"{len(doomed)} values"
            title = "Remove them"
            question = (f"Take these {len(doomed)} off the list?\n\n"
                        + U.name_list(doomed, "a value"))
        if QMessageBox.question(
                self, title,
                f'{question}\n\nThey are gone for good. The standard machine '
                f'values can be put back with "Restore the standard values".'
        ) != QMessageBox.StandardButton.Yes:
            return
        C.drop_items(self._win.items(), doomed)
        self._win.log(f"Removed {what}.")
        self._commit(None)

    def _restore_standard(self):
        """Put back any of the fourteen that are missing, by channel and role.

        Matched on the channel PAIR rather than on the name, so a row that has
        been renamed is still recognised and not added a second time.
        """
        # Every value there is, not just the ones on screen: a standard row
        # sitting in another preset is not missing, and adding it again would
        # watch the same channel twice.
        have = {(it.get("pv"), it.get("minus")) for it in self._win.items()
                if it.get("kind") == "value"}
        added = 0
        for std in C.default_value_items(C.next_id(self._win.items())):
            if (std["pv"], std["minus"]) in have:
                continue
            std["id"] = C.next_id(self._win.items())
            self._win.items().append(std)
            added += 1
        if added:
            self._win.log(f"Put back {added} standard value"
                          f"{'s' if added != 1 else ''}.")
            self._commit(None)
        else:
            QMessageBox.information(self, "Nothing missing",
                                    "All the standard machine values are "
                                    "already on the list.")

    def _commit(self, item):
        # A preset may have been made inside the editor or the Assign box, so
        # the header's drop-down is put back in step before anything reloads.
        self._win.refresh_presets()
        self._win.engine().drop_missing()
        self._win.save_soon()
        self._win.refresh_tabs()
        if item is not None:
            self.select_item(int(item["id"]))
