# cal.py
import sys
import csv
import re
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QComboBox, QLabel, QFileDialog, QLineEdit, QGroupBox,
    QDialog, QTextEdit, QCalendarWidget
)

try:
    import xlwt  # for .xls export (legacy)
except Exception:
    xlwt = None


DEFAULT_INFO_TEXT = """\
Calibration tool (Python replica of Calibrations2.xlsx logic)

- Device selects block (PAP1/PTM1/PCM2/PCM4).
- Default waveplate rows are generated per device.
- From/To selects waveplate range.
- New cal data are computed from linear fit QE vs Device in the selected range:
    QE = slope * Device + intercept
- Convert? / Use Int? switches match the Excel Settings logic.
- int_multiplicator is shown in micro units (×10^6).
- Save supports CSV always, and .xls if xlwt is installed.
"""

def to_float(s: str):
    s = (s or "").strip().replace(",", ".")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def linreg_slope_intercept(x, y):
    """Least squares: y = a*x + b. Returns (a,b) or (None,None)."""
    if len(x) < 2:
        return (None, None)
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if abs(sxx) < 1e-300:
        return (None, None)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    a = sxy / sxx
    b = my - a * mx
    return (a, b)


def micro_to_base(v_micro: float | None):
    return None if v_micro is None else v_micro * 1e-6


def _normalize_seps(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[./_\\\s]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s


def parse_date_any(s: str):
    """
    Accept:
      dd-mm-yyyy, mm-dd-yyyy, yyyy-mm-dd, dd-mm-yy, mm-dd-yy
      plus separators . _ / \ and whitespace.
    Returns datetime.date or None.
    """
    s = _normalize_seps(s)
    if not s:
        return None

    fmts = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d-%m-%y",
        "%m-%d-%y",
    ]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass

    # Heuristic: if 3 parts and last part has 4 digits -> assume dd-mm-yyyy unless first part > 12 then dd-mm
    m = re.fullmatch(r"(\d{1,4})-(\d{1,2})-(\d{1,4})", s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        try:
            ai, bi, ci = int(a), int(b), int(c)
        except ValueError:
            return None

        # yyyy-mm-dd
        if len(a) == 4 and 1 <= bi <= 12 and 1 <= ci <= 31:
            try:
                return datetime(ai, bi, ci).date()
            except ValueError:
                return None

        # dd-mm-yyyy vs mm-dd-yyyy
        if len(c) == 4 and 1 <= bi <= 12:
            # if first part > 12 -> dd
            if ai > 12:
                dd, mm, yyyy = ai, bi, ci
            else:
                # prefer dd-mm-yyyy by default
                dd, mm, yyyy = ai, bi, ci
                # but if second part > 12 then swap
                if bi > 12:
                    dd, mm = bi, ai
            try:
                return datetime(yyyy, mm, dd).date()
            except ValueError:
                return None

    return None


def format_date(d) -> str:
    # Keep a single canonical display format (fast to read)
    return d.strftime("%d.%m.%Y")


class CalendarDialog(QDialog):
    def __init__(self, parent=None, initial_date=None):
        super().__init__(parent)
        self.setWindowTitle("Select date")
        self.resize(360, 300)

        lay = QVBoxLayout(self)
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(True)

        if initial_date is not None:
            qd = QDate(initial_date.year, initial_date.month, initial_date.day)
            self.cal.setSelectedDate(qd)

        lay.addWidget(self.cal)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)

    def selected_date(self):
        qd = self.cal.selectedDate()
        return datetime(qd.year(), qd.month(), qd.day()).date()


class InfoDialog(QDialog):
    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle("Info")
        self.resize(760, 450)

        root = QVBoxLayout(self)

        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text or "")
        root.addWidget(box)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)


class CalibrationTable(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Calibrations")
        self.resize(1320, 760)

        # Columns:
        # 0 Waveplate, 1 QE95, 2 <Device>, 3 Cal Factor (read-only), 4 Note
        self.COL_WAVEPLATE = 0
        self.COL_QE95 = 1
        self.COL_DEVICEVAL = 2
        self.COL_CAL = 3
        self.COL_NOTE = 4

        self._updating = False
        self.dirty = False
        self.info_text = DEFAULT_INFO_TEXT

        # Default profiles per device
        self.DEFAULT_PROFILES = {
            "PAP1": dict(start=0, end=1_400_000, step=50_000, from_=200_000, to=1_000_000),
            "PTM1": dict(start=0, end=60_000, step=5_000, from_=0, to=50_000),
            "PCM2": dict(start=200_000, end=800_000, step=50_000, from_=200_000, to=800_000),
            "PCM4": dict(start=200_000, end=800_000, step=50_000, from_=200_000, to=800_000),
        }

        # Timings list (replica of what was in Settings sheet)
        self.TIMINGS = ["Off-A SS", "Off-A", "Off-B SS", "Off-B"]

        # Highlight brushes (soft)
        self.brush_range_row = QBrush(QColor(120, 170, 255, 35))
        self.brush_active_col = QBrush(QColor(120, 170, 255, 22))
        self.brush_active_row = QBrush(QColor(120, 170, 255, 18))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Top area ---
        top = QHBoxLayout()

        left_box = QWidget()
        left_grid = QGridLayout(left_box)
        left_grid.setContentsMargins(0, 0, 0, 0)
        left_grid.setHorizontalSpacing(8)
        left_grid.setVerticalSpacing(6)

        # Make the "input columns" expand, labels stay compact
        left_grid.setColumnStretch(1, 2)
        left_grid.setColumnStretch(3, 1)
        left_grid.setColumnStretch(5, 1)
        left_grid.setColumnStretch(7, 2)

        def mk_lbl(text: str):
            lab = QLabel(text)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lab

        # Row 0: Device + Convert + Use Int (single row, no extra row above)
        left_grid.addWidget(mk_lbl("Device:"), 0, 0)
        self.device_combo = QComboBox()
        self.device_combo.addItems(["PAP1", "PTM1", "PCM2", "PCM4"])
        self.device_combo.setFixedWidth(150)
        left_grid.addWidget(self.device_combo, 0, 1)

        left_grid.addWidget(mk_lbl("Convert?"), 0, 2)
        self.convert_combo = QComboBox()
        self.convert_combo.addItems(["YES", "NO"])
        self.convert_combo.setFixedWidth(90)
        left_grid.addWidget(self.convert_combo, 0, 3)

        left_grid.addWidget(mk_lbl("Use Int?"), 0, 4)
        self.useint_combo = QComboBox()
        self.useint_combo.addItems(["YES", "NO"])
        self.useint_combo.setFixedWidth(90)
        left_grid.addWidget(self.useint_combo, 0, 5)

        # Row 1: Date + calendar button + Timing
        left_grid.addWidget(mk_lbl("Date:"), 1, 0)
        self.date_combo = QComboBox()
        self.date_combo.setEditable(True)
        self.date_combo.setInsertPolicy(QComboBox.NoInsert)
        self.date_combo.setMinimumWidth(160)
        left_grid.addWidget(self.date_combo, 1, 1)

        self.btn_date_cal = QPushButton("📅")
        self.btn_date_cal.setFixedSize(34, 26)
        self.btn_date_cal.setToolTip("Pick from calendar")
        left_grid.addWidget(self.btn_date_cal, 1, 2)

        left_grid.addWidget(mk_lbl("Timing:"), 1, 4)
        self.timing_combo = QComboBox()
        self.timing_combo.addItems(self.TIMINGS)
        self.timing_combo.setMinimumWidth(160)
        left_grid.addWidget(self.timing_combo, 1, 5, 1, 3)

        # Row 2: From / To (no "row:" labels)
        left_grid.addWidget(mk_lbl("From:"), 2, 0)
        self.from_combo = QComboBox()
        self.from_combo.setEditable(True)
        self.from_combo.setInsertPolicy(QComboBox.NoInsert)
        self.from_combo.setMinimumWidth(160)
        left_grid.addWidget(self.from_combo, 2, 1)

        left_grid.addWidget(mk_lbl("To:"), 2, 2)
        self.to_combo = QComboBox()
        self.to_combo.setEditable(True)
        self.to_combo.setInsertPolicy(QComboBox.NoInsert)
        self.to_combo.setMinimumWidth(160)
        left_grid.addWidget(self.to_combo, 2, 3)

        # Row 3: Average cal factor
        left_grid.addWidget(mk_lbl("Avg cal factor:"), 3, 0)
        self.avg_cal_factor = QLineEdit()
        self.avg_cal_factor.setReadOnly(True)
        self.avg_cal_factor.setPlaceholderText("—")
        left_grid.addWidget(self.avg_cal_factor, 3, 1, 1, 7)

        top.addWidget(left_box)
        top.addSpacing(16)

        # --- Mini calibration table ---
        self.cal_group = QGroupBox("Calibration")
        g = QGridLayout(self.cal_group)
        g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(6)

        g.addWidget(QLabel(""), 0, 0)
        lbl_old = QLabel("Old cal data")
        lbl_new = QLabel("New cal data")
        lbl_old.setAlignment(Qt.AlignCenter)
        lbl_new.setAlignment(Qt.AlignCenter)
        g.addWidget(lbl_old, 0, 1)
        g.addWidget(lbl_new, 0, 2)

        g.addWidget(QLabel("multiplicator"), 1, 0)
        g.addWidget(QLabel("offset"), 2, 0)
        g.addWidget(QLabel("int_multiplicator (×10⁶)"), 3, 0)

        self.mult1 = QLineEdit()
        self.off1 = QLineEdit()
        self.intmult1_micro = QLineEdit()

        self.mult2 = QLineEdit()
        self.off2 = QLineEdit()
        self.intmult2_micro = QLineEdit()
        for w in (self.mult2, self.off2, self.intmult2_micro):
            w.setReadOnly(True)
            w.setEnabled(False)

        g.addWidget(self.mult1, 1, 1)
        g.addWidget(self.mult2, 1, 2)
        g.addWidget(self.off1, 2, 1)
        g.addWidget(self.off2, 2, 2)
        g.addWidget(self.intmult1_micro, 3, 1)
        g.addWidget(self.intmult2_micro, 3, 2)

        top.addWidget(self.cal_group)
        top.addStretch(1)

        self.btn_info = QPushButton("Info")
        top.addWidget(self.btn_info)

        self.btn_save = QPushButton("Save")
        top.addWidget(self.btn_save)

        root.addLayout(top)

        # --- Table ---
        self.table = QTableWidget(0, 5)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(True)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.table.setStyleSheet("""
QTableWidget::item:selected {
    background: transparent;
    border: 2px solid rgba(120, 170, 255, 200);
    color: black;
}
QTableWidget::item:selected:!active {
    background: transparent;
    border: 2px solid rgba(120, 170, 255, 140);
    color: black;
}
""")

        # --- Table toolbar (+ / -) above table ---
        tbl_toolbar = QHBoxLayout()
        tbl_toolbar.setContentsMargins(0, 0, 0, 0)
        tbl_toolbar.setSpacing(6)

        self.btn_add = QPushButton("+")
        self.btn_remove = QPushButton("-")
        self.btn_add.setFixedSize(28, 26)
        self.btn_remove.setFixedSize(28, 26)
        self.btn_add.setToolTip("Insert row above selection (or append)")
        self.btn_remove.setToolTip("Delete selected row (or last)")

        tbl_toolbar.addWidget(self.btn_add)
        tbl_toolbar.addWidget(self.btn_remove)
        tbl_toolbar.addStretch(1)

        root.addLayout(tbl_toolbar)
        root.addWidget(self.table)

        # Signals
        self.btn_info.clicked.connect(self.open_info_dialog)
        self.btn_save.clicked.connect(self.save_to_file)
        self.btn_add.clicked.connect(self.add_row)
        self.btn_remove.clicked.connect(self.remove_row)

        self._last_device_index = self.device_combo.currentIndex()
        self.device_combo.currentIndexChanged.connect(self.on_device_change_requested)

        self.table.itemChanged.connect(self.on_item_changed)
        self.table.currentCellChanged.connect(lambda *_: self.apply_highlight())

        self.from_combo.currentTextChanged.connect(self.update_from_to_rows)
        self.to_combo.currentTextChanged.connect(self.update_from_to_rows)
        self.from_combo.lineEdit().editingFinished.connect(self.update_from_to_rows)
        self.to_combo.lineEdit().editingFinished.connect(self.update_from_to_rows)

        self.convert_combo.currentTextChanged.connect(self.on_mode_changed)
        self.useint_combo.currentTextChanged.connect(self.on_mode_changed)

        self.timing_combo.currentTextChanged.connect(self.mark_dirty)

        self.date_combo.currentTextChanged.connect(self.mark_dirty)
        self.date_combo.lineEdit().editingFinished.connect(self.on_date_edited)
        self.btn_date_cal.clicked.connect(self.open_calendar)

        self.mult1.editingFinished.connect(self.on_oldcal_edited)
        self.off1.editingFinished.connect(self.on_oldcal_edited)
        self.intmult1_micro.editingFinished.connect(self.on_oldcal_edited)

        # Init date dropdown with last 7 days
        self._fill_last_7_days()

        # Init defaults
        self.on_device_changed(self.device_combo.currentText())
        self.dirty = False

    # ---------- Date handling ----------
    def _fill_last_7_days(self):
        today = datetime.now().date()
        vals = [format_date(today - timedelta(days=i)) for i in range(0, 7)]
        self._updating = True
        try:
            self.date_combo.clear()
            self.date_combo.addItems(vals)
            self.date_combo.setCurrentText(vals[0])
        finally:
            self._updating = False

    def on_date_edited(self):
        if self._updating:
            return
        raw = self.date_combo.currentText()
        d = parse_date_any(raw)
        if d is None:
            # Keep user text, but warn once (no popup spam)
            QMessageBox.warning(
                self,
                "Invalid date",
                "Could not parse the date.\n\nAccepted examples:\n  25.02.2026\n  25-02-2026\n  02-25-2026\n  2026-02-25"
            )
            return

        self._updating = True
        try:
            self.date_combo.setCurrentText(format_date(d))
        finally:
            self._updating = False

        self.mark_dirty()

    def open_calendar(self):
        cur = parse_date_any(self.date_combo.currentText()) or datetime.now().date()
        dlg = CalendarDialog(self, cur)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.selected_date()
            self._updating = True
            try:
                self.date_combo.setCurrentText(format_date(d))
            finally:
                self._updating = False
            self.mark_dirty()

    # ---------- Dirty ----------
    def mark_dirty(self):
        if not self._updating:
            self.dirty = True

    def on_oldcal_edited(self):
        self.mark_dirty()
        self.recompute_new_calibration()

    def on_mode_changed(self):
        self.mark_dirty()
        self.recompute_new_calibration()

    # ---------- Info ----------
    def open_info_dialog(self):
        InfoDialog(self, self.info_text).exec()

    # ---------- Confirm device change ----------
    def on_device_change_requested(self, new_index: int):
        if new_index == self._last_device_index:
            return

        if self.dirty:
            reply = QMessageBox.question(
                self,
                "Change device?",
                "You have unsaved changes. Changing the device will reset the table.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                self.device_combo.blockSignals(True)
                try:
                    self.device_combo.setCurrentIndex(self._last_device_index)
                finally:
                    self.device_combo.blockSignals(False)
                return

        self._last_device_index = new_index
        self.on_device_changed(self.device_combo.currentText())
        self.dirty = False

    # ---------- Table helpers ----------
    def _refresh_row_numbers(self):
        for r in range(self.table.rowCount()):
            self.table.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))

    def _set_cell_text(self, row: int, col: int, text: str, read_only=False):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem("")
            self.table.setItem(row, col, item)
        item.setText(text)
        if read_only:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)

    def _get_cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _set_headers_for_device(self, device_name: str):
        qe_unit = "[mJ]" if device_name == "PAP1" else "[J]"
        dev_unit = "[mJ]" if device_name == "PAP1" else "[J]"
        self.table.setHorizontalHeaderLabels(
            ["Waveplate", f"QE95 {qe_unit}", f"{device_name} {dev_unit}", "Cal Factor", "Note"]
        )

    # ---------- Default table ----------
    def _generate_default_table(self, device_name: str):
        prof = self.DEFAULT_PROFILES.get(device_name)
        if not prof:
            self.table.setRowCount(0)
            self.populate_from_to_choices_from_table()
            return

        start = int(prof["start"])
        end = int(prof["end"])
        step = int(prof["step"])
        waveplates = list(range(start, end + 1, step))

        self._updating = True
        try:
            self.table.setRowCount(len(waveplates))
            self._refresh_row_numbers()
            for i, wv in enumerate(waveplates):
                self._set_cell_text(i, self.COL_WAVEPLATE, str(int(wv)), read_only=True)
                self._set_cell_text(i, self.COL_QE95, "")
                self._set_cell_text(i, self.COL_DEVICEVAL, "")
                self._set_cell_text(i, self.COL_CAL, "", read_only=True)
                self._set_cell_text(i, self.COL_NOTE, "")
        finally:
            self._updating = False

        self.populate_from_to_choices_from_table()
        self._updating = True
        try:
            self.from_combo.setCurrentText(str(int(prof["from_"])))
            self.to_combo.setCurrentText(str(int(prof["to"])))
        finally:
            self._updating = False

    # ---------- Device change ----------
    def on_device_changed(self, device_name: str):
        self._set_headers_for_device(device_name)
        self._generate_default_table(device_name)

        self._updating = True
        try:
            self.convert_combo.setCurrentText("YES")
            self.useint_combo.setCurrentText("NO")
        finally:
            self._updating = False

        self.update_from_to_rows()
        self.recompute_all_cal_factors()
        self.recompute_new_calibration()
        self.apply_highlight()

        self.dirty = False

    def populate_from_to_choices_from_table(self):
        vals = []
        for r in range(self.table.rowCount()):
            w = self._get_cell_text(r, self.COL_WAVEPLATE)
            if w != "":
                vals.append(w)

        def fill_combo(combo: QComboBox, default_text: str):
            combo.blockSignals(True)
            try:
                combo.clear()
                for v in vals:
                    combo.addItem(str(v))
                combo.setCurrentText(default_text if vals else "")
            finally:
                combo.blockSignals(False)

        if vals:
            fill_combo(self.from_combo, str(vals[0]))
            fill_combo(self.to_combo, str(vals[-1]))
        else:
            fill_combo(self.from_combo, "")
            fill_combo(self.to_combo, "")

    # ---------- From/To mapping ----------
    def find_row_by_waveplate(self, waveplate_value: int):
        target = str(waveplate_value)
        for r in range(self.table.rowCount()):
            if self._get_cell_text(r, self.COL_WAVEPLATE) == target:
                return r
        return None

    def update_from_to_rows(self):
        # row labels removed -> just recompute + highlight
        self.update_average_cal_factor()
        self.apply_highlight()

    # ---------- Average cal factor ----------
    def update_average_cal_factor(self):
        f = to_float(self.from_combo.currentText())
        t = to_float(self.to_combo.currentText())
        f_row = self.find_row_by_waveplate(int(f)) if f is not None else None
        t_row = self.find_row_by_waveplate(int(t)) if t is not None else None

        if f_row is None or t_row is None:
            self.avg_cal_factor.setText("")
            self.avg_cal_factor.setPlaceholderText("—")
            self.recompute_new_calibration()
            return

        r1, r2 = sorted((f_row, t_row))
        vals = []
        for r in range(r1, r2 + 1):
            v = to_float(self._get_cell_text(r, self.COL_CAL))
            if v is not None:
                vals.append(v)

        if not vals:
            self.avg_cal_factor.setText("")
            self.avg_cal_factor.setPlaceholderText("—")
            self.recompute_new_calibration()
            return

        avg = sum(vals) / len(vals)
        self.avg_cal_factor.setText(f"{avg:.10g}")
        self.recompute_new_calibration()

    # ---------- Cal factor ----------
    def cal_factor_formula(self, qe95: float, device_val: float):
        if qe95 is None or device_val is None:
            return None
        if abs(qe95) < 1e-300 or abs(device_val) < 1e-300:
            return None
        return qe95 / device_val

    def recompute_cal_factor_for_row(self, row: int):
        qe = to_float(self._get_cell_text(row, self.COL_QE95))
        dv = to_float(self._get_cell_text(row, self.COL_DEVICEVAL))

        val = self.cal_factor_formula(qe, dv)
        if val is None:
            self._set_cell_text(row, self.COL_CAL, "", read_only=True)
            return

        self._set_cell_text(row, self.COL_CAL, f"{val:.10g}", read_only=True)

    def recompute_all_cal_factors(self):
        self._updating = True
        try:
            for r in range(self.table.rowCount()):
                self.recompute_cal_factor_for_row(r)
        finally:
            self._updating = False
        self.update_average_cal_factor()

    def on_item_changed(self, item: QTableWidgetItem):
        if self._updating:
            return

        r, c = item.row(), item.column()

        if c == self.COL_CAL:
            self._updating = True
            try:
                self.recompute_cal_factor_for_row(r)
            finally:
                self._updating = False
            self.update_average_cal_factor()
            return

        if c in (self.COL_QE95, self.COL_DEVICEVAL, self.COL_NOTE):
            self.mark_dirty()

        if c in (self.COL_QE95, self.COL_DEVICEVAL):
            self._updating = True
            try:
                self.recompute_cal_factor_for_row(r)
            finally:
                self._updating = False
            self.update_average_cal_factor()

    # ---------- Fit points in range ----------
    def _collect_points_in_range(self):
        f = to_float(self.from_combo.currentText())
        t = to_float(self.to_combo.currentText())
        f_row = self.find_row_by_waveplate(int(f)) if f is not None else None
        t_row = self.find_row_by_waveplate(int(t)) if t is not None else None
        if f_row is None or t_row is None:
            return [], []
        r1, r2 = sorted((f_row, t_row))

        xs, ys = [], []
        for r in range(r1, r2 + 1):
            qe = to_float(self._get_cell_text(r, self.COL_QE95))
            dv = to_float(self._get_cell_text(r, self.COL_DEVICEVAL))
            if qe is None or dv is None:
                continue
            if abs(qe) < 1e-300 or abs(dv) < 1e-300:
                continue
            xs.append(dv)   # x = device
            ys.append(qe)   # y = QE
        return xs, ys

    # ---------- New cal data (Excel-replica logic) ----------
    def recompute_new_calibration(self):
        device = self.device_combo.currentText()
        convert_yes = (self.convert_combo.currentText().strip().upper() == "YES")
        useint_yes = (self.useint_combo.currentText().strip().upper() == "YES")

        m_old = to_float(self.mult1.text())
        o_old = to_float(self.off1.text())
        i_old_micro = to_float(self.intmult1_micro.text())
        i_old = micro_to_base(i_old_micro) if i_old_micro is not None else None

        xs, ys = self._collect_points_in_range()
        slope, intercept = linreg_slope_intercept(xs, ys)

        def set_ro(line: QLineEdit, val):
            line.setEnabled(True)
            line.setReadOnly(True)
            line.setText("" if val is None else f"{val:.10g}")
            line.setEnabled(False)

        def set_int_ro_micro(line: QLineEdit, val_base):
            line.setEnabled(True)
            line.setReadOnly(True)
            line.setText("" if val_base is None else f"{(val_base*1e6):.10g}")
            line.setEnabled(False)

        if slope is None or intercept is None or m_old is None or o_old is None or i_old is None:
            set_ro(self.mult2, None)
            set_ro(self.off2, None)
            set_int_ro_micro(self.intmult2_micro, None)
            return

        # exponent differs by device
        p = 1 if device == "PAP1" else 2

        M_mult = slope * m_old
        M_off = slope * o_old + intercept
        M_int = 1.0

        J_mult = 1.0
        J_off = o_old + (intercept / (i_old * (slope ** p)))
        J_int = slope * i_old

        if (not convert_yes) and (not useint_yes):
            new_mult, new_off, new_int = M_mult, M_off, M_int
        elif (not convert_yes) and useint_yes:
            new_mult, new_off, new_int = J_mult, J_off, J_int
        elif convert_yes and (not useint_yes):
            if abs(M_mult) < 1e-300:
                new_mult, new_off, new_int = None, None, None
            else:
                new_mult = 1.0
                new_off = M_off / M_mult
                new_int = M_mult
        else:
            new_mult = J_mult * J_int
            new_off = J_off * J_int
            new_int = 1.0

        set_ro(self.mult2, new_mult)
        set_ro(self.off2, new_off)
        set_int_ro_micro(self.intmult2_micro, new_int)

    # ---------- Highlighting ----------
    def _clear_backgrounds(self):
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
                if it is not None:
                    it.setBackground(QBrush())

    def apply_highlight(self):
        self._updating = True
        try:
            self._clear_backgrounds()

            f = to_float(self.from_combo.currentText())
            t = to_float(self.to_combo.currentText())
            f_row = self.find_row_by_waveplate(int(f)) if f is not None else None
            t_row = self.find_row_by_waveplate(int(t)) if t is not None else None

            if f_row is not None and t_row is not None:
                r1, r2 = sorted((f_row, t_row))
                for r in range(r1, r2 + 1):
                    for c in range(self.table.columnCount()):
                        it = self.table.item(r, c)
                        if it is None:
                            it = QTableWidgetItem("")
                            self.table.setItem(r, c, it)
                        it.setBackground(self.brush_range_row)

            cr = self.table.currentRow()
            cc = self.table.currentColumn()

            if cr >= 0:
                for c in range(self.table.columnCount()):
                    it = self.table.item(cr, c)
                    if it is None:
                        it = QTableWidgetItem("")
                        self.table.setItem(cr, c, it)
                    it.setBackground(self.brush_active_row)

            if cc >= 0:
                for r in range(self.table.rowCount()):
                    it = self.table.item(r, cc)
                    if it is None:
                        it = QTableWidgetItem("")
                        self.table.setItem(r, cc, it)
                    it.setBackground(self.brush_active_col)
        finally:
            self._updating = False

    # ---------- Row management ----------
    def add_row(self):
        selected = self.table.selectionModel().selectedIndexes()
        insert_at = selected[0].row() if selected else self.table.rowCount()

        self.table.insertRow(insert_at)
        self._refresh_row_numbers()

        self._updating = True
        try:
            self._set_cell_text(insert_at, self.COL_WAVEPLATE, "", read_only=True)
            self._set_cell_text(insert_at, self.COL_QE95, "")
            self._set_cell_text(insert_at, self.COL_DEVICEVAL, "")
            self._set_cell_text(insert_at, self.COL_CAL, "", read_only=True)
            self._set_cell_text(insert_at, self.COL_NOTE, "")
        finally:
            self._updating = False

        self.mark_dirty()
        self.update_average_cal_factor()
        self.apply_highlight()

    def remove_row(self):
        row_count = self.table.rowCount()
        if row_count == 0:
            return

        selected = self.table.selectionModel().selectedIndexes()
        r = selected[0].row() if selected else row_count - 1

        if row_count <= 1:
            reply = QMessageBox.question(
                self, "Delete last row?",
                "The table has only one row. Do you really want to delete it?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        self.table.removeRow(r)
        self._refresh_row_numbers()
        self.mark_dirty()
        self.update_from_to_rows()
        self.apply_highlight()

    # ---------- Saving ----------
    def _collect_table_data(self):
        headers = []
        for c in range(self.table.columnCount()):
            h = self.table.horizontalHeaderItem(c).text() if self.table.horizontalHeaderItem(c) else ""
            headers.append(h)

        rows = []
        for r in range(self.table.rowCount()):
            row_vals = [self._get_cell_text(r, c) for c in range(self.table.columnCount())]
            rows.append(row_vals)

        while rows and all(v == "" for v in rows[-1]):
            rows.pop()

        return headers, rows

    def save_to_file(self):
        headers, rows = self._collect_table_data()
        device = self.device_combo.currentText()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        default_name = f"calibration_{device}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save calibration",
            default_name,
            "Excel 97-2003 (*.xls);;CSV (*.csv)"
        )
        if not path:
            return

        if path.lower().endswith(".csv"):
            self._save_csv(path, device, ts, headers, rows)
            QMessageBox.information(self, "Saved", f"Saved to:\n{path}")
            self.dirty = False
            return

        if not path.lower().endswith(".xls"):
            path += ".xls"

        if xlwt is None:
            QMessageBox.warning(
                self,
                "xlwt missing",
                "Saving to .xls requires 'xlwt'.\n\nInstall:\n  py -m pip install xlwt\n\nSaving CSV instead."
            )
            csv_path = path[:-4] + ".csv"
            self._save_csv(csv_path, device, ts, headers, rows)
            QMessageBox.information(self, "Saved", f"Saved to:\n{csv_path}")
            self.dirty = False
            return

        self._save_xls(path, device, ts, headers, rows)
        QMessageBox.information(self, "Saved", f"Saved to:\n{path}")
        self.dirty = False

    def _save_csv(self, path, device, ts, headers, rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Device", device])
            w.writerow(["Saved", ts])
            w.writerow(["Date", self.date_combo.currentText().strip()])
            w.writerow(["Timing", self.timing_combo.currentText().strip()])
            w.writerow(["Convert?", self.convert_combo.currentText().strip()])
            w.writerow(["Use Int?", self.useint_combo.currentText().strip()])
            w.writerow(["From", self.from_combo.currentText().strip()])
            w.writerow(["To", self.to_combo.currentText().strip()])
            w.writerow(["Average cal factor", self.avg_cal_factor.text().strip()])
            w.writerow([])

            w.writerow(["Calibration", "Old", "New"])
            w.writerow(["multiplicator", self.mult1.text().strip(), self.mult2.text().strip()])
            w.writerow(["offset", self.off1.text().strip(), self.off2.text().strip()])
            w.writerow(["int_multiplicator (×10^6)", self.intmult1_micro.text().strip(), self.intmult2_micro.text().strip()])
            w.writerow([])

            w.writerow(headers)
            for row in rows:
                w.writerow(row)

    def _save_xls(self, path, device, ts, headers, rows):
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Calibration")

        bold = xlwt.easyxf("font: bold on;")
        r = 0

        def write_kv(k, v):
            nonlocal r
            ws.write(r, 0, k, bold)
            ws.write(r, 1, v)
            r += 1

        write_kv("Device", device)
        write_kv("Saved", ts)
        write_kv("Date", self.date_combo.currentText().strip())
        write_kv("Timing", self.timing_combo.currentText().strip())
        write_kv("Convert?", self.convert_combo.currentText().strip())
        write_kv("Use Int?", self.useint_combo.currentText().strip())
        write_kv("From", self.from_combo.currentText().strip())
        write_kv("To", self.to_combo.currentText().strip())
        write_kv("Average cal factor", self.avg_cal_factor.text().strip())

        r += 1
        ws.write(r, 0, "Calibration", bold)
        r += 1
        ws.write(r, 1, "Old", bold)
        ws.write(r, 2, "New", bold)
        r += 1

        ws.write(r, 0, "multiplicator", bold)
        ws.write(r, 1, self.mult1.text().strip())
        ws.write(r, 2, self.mult2.text().strip())
        r += 1

        ws.write(r, 0, "offset", bold)
        ws.write(r, 1, self.off1.text().strip())
        ws.write(r, 2, self.off2.text().strip())
        r += 1

        ws.write(r, 0, "int_multiplicator (×10^6)", bold)
        ws.write(r, 1, self.intmult1_micro.text().strip())
        ws.write(r, 2, self.intmult2_micro.text().strip())
        r += 2

        for c, h in enumerate(headers):
            ws.write(r, c, h, bold)
        r += 1

        for row in rows:
            for c, v in enumerate(row):
                ws.write(r, c, v)
            r += 1

        wb.save(path)


def main():
    app = QApplication(sys.argv)
    win = CalibrationTable()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()