import sys
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QPlainTextEdit, QTextEdit, QSplitter, QTreeView,
    QTableView, QHeaderView, QFileSystemModel, QScrollArea, QFrame,
    QComboBox, QCheckBox, QLineEdit, QDateEdit, QInputDialog, QMessageBox,
    QSizePolicy, QAbstractItemView,
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QAbstractTableModel, QDate, QModelIndex,
)
from PySide6.QtGui import QFont, QPalette, QColor

from builder_logic import RepositoryBuilderLogic
from rampdiag_logic import RampingDiagnostixLogic
from operation_history_logic import OperationHistoryLogic
from detect_suspicious_logic import run_detection
from pulser_monitor_tab import PulserMonitorWidget

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent
DATA_REPOSITORY = APP_DIR / "DataRepository"
CONFIG_FILE = APP_DIR / "builder_config.json"
STATE_FILE = APP_DIR / "builder_state.json"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

BUTTON_STYLE = """
QPushButton {
    background: #3a7bd5;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
}
QPushButton:hover { background: #2f6bbf; }
QPushButton:disabled { background: #555; color: #888; }
"""

STOP_BUTTON_STYLE = """
QPushButton {
    background: #c0392b;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
}
QPushButton:hover { background: #a93226; }
QPushButton:disabled { background: #555; color: #888; }
"""

SECONDARY_STYLE = """
QPushButton {
    background: #444;
    color: #ddd;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover { background: #555; }
"""

LOG_STYLE = """
QPlainTextEdit {
    background: #1e1e1e;
    color: #d4d4d4;
    font-family: Consolas, monospace;
    font-size: 11px;
}
"""


def _btn(label, style=BUTTON_STYLE):
    b = QPushButton(label)
    b.setStyleSheet(style)
    return b


class WorkerThread(QThread):
    log = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self._stop = False

    def stop(self):
        self._stop = True

    def is_stopped(self):
        return self._stop

    def run(self):
        try:
            self._fn()
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class LogWidget(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setStyleSheet(LOG_STYLE)

    def append_line(self, text):
        self.appendPlainText(text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


# ---------------------------------------------------------------------------
# Pandas model (for Browser + Suspicious table)
# ---------------------------------------------------------------------------

class PandasModel(QAbstractTableModel):

    def __init__(self, df=None):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()

    def rowCount(self, parent=QModelIndex()):
        return len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return len(self._df.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.EditRole):
            value = self._df.iloc[index.row(), index.column()]
            if pd.isna(value):
                return ""
            col = self._df.columns[index.column()]
            if col == "timestamp":
                try:
                    dt = pd.to_datetime(value, unit="ns")
                    return dt.strftime("%Y-%b-%d %H:%M:%S.%f")[:-3]
                except Exception:
                    pass
            return str(value)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole:
            return False
        col = self._df.columns[index.column()]
        row = index.row()
        try:
            dtype = self._df[col].dtype
            if pd.api.types.is_integer_dtype(dtype):
                value = int(value)
            elif pd.api.types.is_float_dtype(dtype):
                value = float(value)
            self._df.at[row, col] = value
        except Exception:
            self._df.at[row, col] = value
        self.dataChanged.emit(index, index)
        return True

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return str(self._df.columns[section])
        return str(section)

    def update_dataframe(self, df):
        self.beginResetModel()
        self._df = df
        self.endResetModel()

    @property
    def df(self):
        return self._df


# ===========================================================================
# Tab 1 — Builder
# ===========================================================================

class BuilderTab(QWidget):

    def __init__(self):
        super().__init__()
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Status row
        status_row = QHBoxLayout()
        self._lbl_state = QLabel("State: unknown")
        self._lbl_state.setStyleSheet("color: #aaa;")
        status_row.addWidget(self._lbl_state)
        status_row.addStretch()
        self._btn_run = _btn("▶  Run Builder")
        self._btn_stop = _btn("■  Stop", STOP_BUTTON_STYLE)
        self._btn_stop.setEnabled(False)
        self._btn_open = _btn("Open DataRepository", SECONDARY_STYLE)
        status_row.addWidget(self._btn_run)
        status_row.addWidget(self._btn_stop)
        status_row.addWidget(self._btn_open)
        layout.addLayout(status_row)

        # Config display
        cfg_frame = QFrame()
        cfg_frame.setStyleSheet("QFrame { background:#2a2a2a; border-radius:4px; padding:4px; }")
        cfg_layout = QVBoxLayout(cfg_frame)
        cfg_layout.setContentsMargins(8, 6, 8, 6)
        self._lbl_config = QLabel("Config: loading...")
        self._lbl_config.setStyleSheet("color:#ccc; font-size:11px;")
        self._lbl_config.setWordWrap(True)
        cfg_layout.addWidget(self._lbl_config)
        layout.addWidget(cfg_frame)

        # Log
        self._log = LogWidget()
        layout.addWidget(self._log)

        self._btn_run.clicked.connect(self._run)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_open.clicked.connect(self._open_repo)

        self._refresh_status()

    def _load_config(self):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    def _refresh_status(self):
        try:
            config = self._load_config()
            pvs = ", ".join(config.get("pvs", {}).keys())
            start = config.get("start_date", "?")
            self._lbl_config.setText(
                f"PVs: {pvs}\n"
                f"Start date: {start}   |   Master multiple: {config.get('master_multiple','?')}"
            )
        except Exception as e:
            self._lbl_config.setText(f"Config error: {e}")

        # State
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    state = json.load(f)
                last = state.get("last_processed_day", "none")
            else:
                last = "none"
            self._lbl_state.setText(f"Last processed day: {last}")
        except Exception:
            self._lbl_state.setText("State: error reading")

    def _run(self):
        self._log.clear()
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)

        try:
            config = self._load_config()
        except Exception as e:
            self._log.append_line(f"Cannot load config: {e}")
            self._btn_run.setEnabled(True)
            self._btn_stop.setEnabled(False)
            return

        builder = RepositoryBuilderLogic(config, STATE_FILE, DATA_REPOSITORY)

        def work():
            builder.run(
                log_fn=self._log.append_line,
                stop_flag=self._worker.is_stopped,
            )

        self._worker = WorkerThread(work)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(lambda e: self._log.append_line(f"ERROR: {e}"))
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.stop()
        self._btn_stop.setEnabled(False)

    def _on_done(self):
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._refresh_status()

    def _open_repo(self):
        os.startfile(str(DATA_REPOSITORY))


# ===========================================================================
# Tab 2 — Browser
# ===========================================================================

class BrowserTab(QWidget):

    def __init__(self):
        super().__init__()
        self._current_file = None
        self._df = pd.DataFrame()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()
        self._btn_add_row = _btn("+ Row", SECONDARY_STYLE)
        self._btn_del_row = _btn("− Row", SECONDARY_STYLE)
        self._btn_add_col = _btn("+ Column", SECONDARY_STYLE)
        self._btn_del_col = _btn("− Column", SECONDARY_STYLE)
        self._btn_save = _btn("Save")
        self._btn_fill = _btn("Fill Selected", SECONDARY_STYLE)
        for b in [self._btn_add_row, self._btn_del_row, self._btn_add_col,
                  self._btn_del_col, self._btn_fill]:
            toolbar.addWidget(b)
        toolbar.addStretch()
        toolbar.addWidget(self._btn_save)
        layout.addLayout(toolbar)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)

        # Tree
        repo_path = DATA_REPOSITORY
        repo_path.mkdir(exist_ok=True)
        self._tree_model = QFileSystemModel()
        self._tree_model.setRootPath(str(repo_path))
        self._tree_model.setNameFilters(["*.parquet"])
        self._tree_model.setNameFilterDisables(False)

        self._tree = QTreeView()
        self._tree.setModel(self._tree_model)
        self._tree.setRootIndex(self._tree_model.index(str(repo_path)))
        for i in range(1, 4):
            self._tree.hideColumn(i)
        self._tree.setMinimumWidth(220)
        splitter.addWidget(self._tree)

        # Table
        self._table = QTableView()
        self._table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._model = PandasModel()
        self._table.setModel(self._model)
        splitter.addWidget(self._table)
        splitter.setSizes([240, 1160])

        layout.addWidget(splitter)

        self._tree.clicked.connect(self._load_file)
        self._btn_add_row.clicked.connect(self._add_row)
        self._btn_del_row.clicked.connect(self._del_row)
        self._btn_add_col.clicked.connect(self._add_col)
        self._btn_del_col.clicked.connect(self._del_col)
        self._btn_save.clicked.connect(self._save)
        self._btn_fill.clicked.connect(self._fill_selected)

    def _load_file(self, index):
        file_path = Path(self._tree_model.filePath(index))
        if not file_path.is_file() or file_path.suffix.lower() != ".parquet":
            return
        try:
            self._df = pd.read_parquet(file_path)
            self._current_file = file_path
            self._model.update_dataframe(self._df)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def load_file_path(self, file_path: Path):
        try:
            self._df = pd.read_parquet(file_path)
            self._current_file = file_path
            self._model.update_dataframe(self._df)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def select_timestamp(self, timestamp_ns: int):
        if "timestamp" not in self._df.columns:
            return
        matches = self._df.index[self._df["timestamp"] == timestamp_ns].tolist()
        if not matches:
            diff = (self._df["timestamp"] - timestamp_ns).abs()
            row = int(diff.idxmin())
        else:
            row = int(matches[0])
        index = self._model.index(row, 0)
        self._table.selectRow(row)
        self._table.scrollTo(index)

    def _add_row(self):
        if self._df.empty and len(self._df.columns) == 0:
            return
        self._df.loc[len(self._df)] = [None] * len(self._df.columns)
        self._model.update_dataframe(self._df)

    def _del_row(self):
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return
        rows = sorted([idx.row() for idx in indexes], reverse=True)
        for row in rows:
            self._df.drop(index=row, inplace=True)
        self._df.reset_index(drop=True, inplace=True)
        self._model.update_dataframe(self._df)

    def _add_col(self):
        if self._current_file is None:
            return
        name, ok = QInputDialog.getText(self, "Add Column", "Column name:")
        if ok and name:
            self._df[name] = None
            self._model.update_dataframe(self._df)

    def _del_col(self):
        if self._current_file is None or not list(self._df.columns):
            return
        name, ok = QInputDialog.getItem(self, "Delete Column", "Column:",
                                         list(self._df.columns), 0, False)
        if ok:
            self._df.drop(columns=[name], inplace=True)
            self._model.update_dataframe(self._df)

    def _fill_selected(self):
        indexes = self._table.selectedIndexes()
        if not indexes:
            return
        text, ok = QInputDialog.getText(self, "Fill Selected Cells", "New value:")
        if not ok:
            return
        value = self._parse_value(text)
        for idx in indexes:
            self._df.at[idx.row(), self._df.columns[idx.column()]] = value
        self._model.update_dataframe(self._df)

    def _parse_value(self, text):
        text = text.strip()
        if text.lower() in ("nan", "none", "null", ""):
            return np.nan
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        return text

    def _save(self):
        if self._current_file is None:
            return
        try:
            self._df.to_parquet(self._current_file, index=False)
            QMessageBox.information(self, "Saved", "File saved successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


# ===========================================================================
# Tab 3 — Suspicious rows
# ===========================================================================

class SuspiciousTab(QWidget):

    def __init__(self):
        super().__init__()
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._btn_run = _btn("▶  Run Detection")
        self._lbl_result = QLabel("Press Run to scan DataRepository")
        self._lbl_result.setStyleSheet("color: #aaa;")
        top.addWidget(self._btn_run)
        top.addWidget(self._lbl_result)
        top.addStretch()
        layout.addLayout(top)

        # Results table
        self._table = QTableView()
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._suspicious_df = pd.DataFrame(columns=["file", "row", "timestamp", "reason"])
        self._model = PandasModel(self._suspicious_df)
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

        # Log
        log_label = QLabel("Log:")
        log_label.setStyleSheet("color:#aaa;")
        layout.addWidget(log_label)
        self._log = LogWidget()
        self._log.setMaximumHeight(120)
        layout.addWidget(self._log)

        self._btn_run.clicked.connect(self._run)

    def _run(self):
        self._log.clear()
        self._btn_run.setEnabled(False)
        self._lbl_result.setText("Running...")

        def work():
            results = run_detection(DATA_REPOSITORY, log_fn=self._log.append_line)
            self._show_results(results)

        self._worker = WorkerThread(work)
        self._worker.finished.connect(lambda: self._btn_run.setEnabled(True))
        self._worker.error.connect(lambda e: self._log.append_line(f"ERROR: {e}"))
        self._worker.start()

    def _show_results(self, results):
        if not results:
            self._lbl_result.setText("No suspicious rows found.")
            df = pd.DataFrame(columns=["file", "row", "timestamp", "reason"])
        else:
            rows = []
            for row_idx, timestamp, fname, reason in results:
                try:
                    ts_str = pd.to_datetime(timestamp, unit="ns").strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts_str = str(timestamp)
                rows.append({"file": fname, "row": row_idx, "timestamp": ts_str, "reason": reason})
            df = pd.DataFrame(rows)
            self._lbl_result.setText(f"{len(results)} suspicious rows found.")

        self._model.update_dataframe(df)


# ===========================================================================
# Tab 4 — Diagnostix
# ===========================================================================

class DiagnostixTab(QWidget):

    def __init__(self):
        super().__init__()
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._btn_run = _btn("▶  Run Diagnostix")
        self._btn_stop = _btn("■  Stop", STOP_BUTTON_STYLE)
        self._btn_stop.setEnabled(False)
        self._btn_open_plots = _btn("Open Plots", SECONDARY_STYLE)
        self._btn_open_ops = _btn("Open Operations", SECONDARY_STYLE)
        for b in [self._btn_run, self._btn_stop, self._btn_open_plots, self._btn_open_ops]:
            top.addWidget(b)
        top.addStretch()
        layout.addLayout(top)

        info = QLabel(
            "Processes DataRepository → Features / Segments / Operations / Plots.\n"
            "Skips days already processed."
        )
        info.setStyleSheet("color:#888; font-size:11px;")
        layout.addWidget(info)

        self._log = LogWidget()
        layout.addWidget(self._log)

        self._btn_run.clicked.connect(self._run)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_open_plots.clicked.connect(
            lambda: os.startfile(str(APP_DIR / "Plots")))
        self._btn_open_ops.clicked.connect(
            lambda: os.startfile(str(APP_DIR / "Operations")))

    def _run(self):
        self._log.clear()
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)

        diag = RampingDiagnostixLogic(DATA_REPOSITORY, APP_DIR)

        def work():
            diag.run(
                log_fn=self._log.append_line,
                stop_flag=self._worker.is_stopped,
            )

        self._worker = WorkerThread(work)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(lambda e: self._log.append_line(f"ERROR: {e}"))
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.stop()
        self._btn_stop.setEnabled(False)

    def _on_done(self):
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)


# ===========================================================================
# Tab 5 — Filter Plot
# ===========================================================================

AVAILABLE_COLUMNS = [
    "waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4", "green",
    "sbw4_green", "green_ptm1", "ptm1_pap1", "pcm2_green", "sbw4_ptm1",
]


class ConditionRow(QWidget):

    def __init__(self, parent_layout, on_remove):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        row.addWidget(self.enabled)

        self.variable = QComboBox()
        self.variable.addItems(AVAILABLE_COLUMNS)
        self.variable.setCurrentText("green")
        row.addWidget(self.variable)

        lbl_t = QLabel("target")
        lbl_t.setStyleSheet("color:#aaa;")
        row.addWidget(lbl_t)

        self.target = QLineEdit("75")
        self.target.setFixedWidth(80)
        row.addWidget(self.target)

        lbl_tol = QLabel("tol %")
        lbl_tol.setStyleSheet("color:#aaa;")
        row.addWidget(lbl_tol)

        self.tolerance = QLineEdit("5")
        self.tolerance.setFixedWidth(60)
        row.addWidget(self.tolerance)

        btn_rm = _btn("✕", SECONDARY_STYLE)
        btn_rm.setFixedWidth(30)
        btn_rm.clicked.connect(on_remove)
        row.addWidget(btn_rm)
        row.addStretch()

    def get_condition(self):
        if not self.enabled.isChecked():
            return None
        try:
            return {
                "variable": self.variable.currentText(),
                "target": float(self.target.text()),
                "tolerance_percent": float(self.tolerance.text()),
            }
        except ValueError:
            return None


class FilterPlotTab(QWidget):

    def __init__(self):
        super().__init__()
        self._df_cache = None
        self._worker = None
        self._condition_rows = []
        self._init_ui()

    def _init_ui(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(8)

        # --- Left panel (controls) ---
        left = QWidget()
        left.setFixedWidth(340)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Y variable + plot mode
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Y:"))
        self._y_var = QComboBox()
        self._y_var.addItems(AVAILABLE_COLUMNS)
        self._y_var.setCurrentText("sbw4")
        row1.addWidget(self._y_var, 1)
        left_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Mode:"))
        self._plot_mode = QComboBox()
        self._plot_mode.addItems(["Daily distribution", "Raw shots"])
        row2.addWidget(self._plot_mode, 1)
        left_layout.addLayout(row2)

        # Date range
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("From:"))
        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(QDate(2026, 1, 1))
        row3.addWidget(self._from_date)
        left_layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("To:     "))
        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(QDate.currentDate())
        row4.addWidget(self._to_date)
        left_layout.addLayout(row4)

        # Conditions header
        cond_header = QLabel("Conditions (variable, target, tolerance %):")
        cond_header.setStyleSheet("color:#aaa; font-size:11px;")
        left_layout.addWidget(cond_header)

        # Scrollable conditions area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(240)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #444; }")
        self._cond_container = QWidget()
        self._cond_layout = QVBoxLayout(self._cond_container)
        self._cond_layout.setContentsMargins(4, 4, 4, 4)
        self._cond_layout.setSpacing(2)
        self._cond_layout.addStretch()
        scroll.setWidget(self._cond_container)
        left_layout.addWidget(scroll)

        # Condition buttons
        cond_btns = QHBoxLayout()
        btn_add_cond = _btn("+ Condition", SECONDARY_STYLE)
        btn_add_cond.clicked.connect(self._add_condition)
        cond_btns.addWidget(btn_add_cond)
        cond_btns.addStretch()
        left_layout.addLayout(cond_btns)

        # Plot button
        btn_row = QHBoxLayout()
        self._btn_plot = _btn("▶  Plot")
        self._btn_load = _btn("Reload data", SECONDARY_STYLE)
        btn_row.addWidget(self._btn_plot)
        btn_row.addWidget(self._btn_load)
        left_layout.addLayout(btn_row)

        # Stats
        stats_label = QLabel("Statistics:")
        stats_label.setStyleSheet("color:#aaa; font-size:11px; margin-top:6px;")
        left_layout.addWidget(stats_label)
        self._stats = QPlainTextEdit()
        self._stats.setReadOnly(True)
        self._stats.setStyleSheet(LOG_STYLE)
        self._stats.setMaximumHeight(140)
        left_layout.addWidget(self._stats)

        left_layout.addStretch()
        main.addWidget(left)

        # --- Right panel (matplotlib) ---
        self._fig = Figure(figsize=(10, 6))
        self._canvas = FigureCanvasQTAgg(self._fig)
        main.addWidget(self._canvas, 1)

        # Connect
        self._btn_plot.clicked.connect(self._do_plot)
        self._btn_load.clicked.connect(self._reload_data)

        # Default condition
        self._add_condition()

    def _add_condition(self):
        row = ConditionRow(self._cond_layout, lambda: self._remove_condition(row))
        self._condition_rows.append(row)
        # Insert before the stretch (last item)
        count = self._cond_layout.count()
        self._cond_layout.insertWidget(count - 1, row)

    def _remove_condition(self, row):
        if row in self._condition_rows:
            self._condition_rows.remove(row)
        self._cond_layout.removeWidget(row)
        row.deleteLater()

    def _reload_data(self):
        self._df_cache = None
        self._stats.setPlainText("Data cleared. Press Plot to reload.")

    def _load_data(self):
        files = sorted(DATA_REPOSITORY.glob("*/*.parquet"))
        tables = []
        for file in files:
            try:
                df = pd.read_parquet(file)
                df = self._add_shot_features(df)
                tables.append(df)
            except Exception:
                pass
        if not tables:
            raise RuntimeError(f"No data found in {DATA_REPOSITORY}")
        return pd.concat(tables, ignore_index=True)

    def _add_shot_features(self, df):
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ns", errors="coerce")
        for col in ["waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "pcm2" in df.columns and "pcm4" in df.columns:
            df["green"] = df["pcm2"] + df["pcm4"]

        def sdiv(a, b):
            import numpy as _np
            return _np.where((b != 0) & _np.isfinite(b), a / b, _np.nan)

        if all(c in df.columns for c in ["sbw4", "pcm2", "pcm4"]):
            df["sbw4_green"] = sdiv(df["sbw4"], df["green"])
        if all(c in df.columns for c in ["pcm2", "pcm4", "ptm1"]):
            df["green_ptm1"] = sdiv(df["green"], df["ptm1"])
        if all(c in df.columns for c in ["ptm1", "pap1"]):
            df["ptm1_pap1"] = sdiv(df["ptm1"], df["pap1"])
        if all(c in df.columns for c in ["pcm2", "pcm4"]):
            df["pcm2_green"] = sdiv(df["pcm2"], df["green"])
        if all(c in df.columns for c in ["sbw4", "ptm1"]):
            df["sbw4_ptm1"] = sdiv(df["sbw4"], df["ptm1"])
        return df

    def _apply_conditions(self, df, conditions):
        filtered = df.copy()
        ranges = []
        for cond in conditions:
            var = cond["variable"]
            target = cond["target"]
            tol = cond["tolerance_percent"]
            filtered[var] = pd.to_numeric(filtered[var], errors="coerce")
            lo = target * (1 - tol / 100)
            hi = target * (1 + tol / 100)
            filtered = filtered[filtered[var].between(lo, hi, inclusive="both")]
            ranges.append((var, lo, hi))
        return filtered, ranges

    def _do_plot(self):
        self._btn_plot.setEnabled(False)
        self._stats.setPlainText("Loading data...")

        def work():
            if self._df_cache is None:
                self._df_cache = self._load_data()

            conditions = [r.get_condition() for r in self._condition_rows]
            conditions = [c for c in conditions if c is not None]

            df = self._df_cache.copy()

            from_dt = pd.Timestamp(self._from_date.date().toPython())
            to_dt = pd.Timestamp(self._to_date.date().toPython()) + pd.Timedelta(days=1)
            df = df[(df["timestamp"] >= from_dt) & (df["timestamp"] <= to_dt)]

            y_col = self._y_var.currentText()
            filtered, ranges = self._apply_conditions(df, conditions)
            filtered = filtered.dropna(subset=["timestamp", y_col])

            condition_text = ", ".join(
                f"{var}={lo:.3g}–{hi:.3g}" for var, lo, hi in ranges
            )

            self._fig.clear()
            ax = self._fig.add_subplot(111)

            if self._plot_mode.currentText() == "Raw shots":
                filtered = filtered.sort_values("timestamp")
                ax.scatter(filtered["timestamp"], filtered[y_col], s=20)
                ax.set_xlabel("Time")
                ax.set_ylabel(y_col)
            else:
                filtered["day"] = filtered["timestamp"].dt.floor("D")
                days = sorted(filtered["day"].unique())
                data_by_day = []
                valid_days = []
                for day in days:
                    vals = filtered.loc[filtered["day"] == day, y_col].dropna().values
                    if len(vals) >= 2:
                        data_by_day.append(vals)
                        valid_days.append(day)

                if data_by_day:
                    positions = list(range(len(valid_days)))
                    ax.violinplot(data_by_day, positions=positions,
                                  showmedians=True, showextrema=True, widths=0.7)
                    ax.set_xticks(positions)
                    ax.set_xticklabels(
                        [pd.to_datetime(d).strftime("%Y-%m-%d") for d in valid_days],
                        rotation=45, ha="right"
                    )
                    ax.set_xlabel("Date")
                    ax.set_ylabel(y_col)
                else:
                    ax.text(0.5, 0.5, "No data", ha="center", va="center",
                            transform=ax.transAxes)

            ax.set_title(f"{y_col} | {condition_text}" if condition_text else y_col)
            ax.grid(True)
            self._fig.tight_layout()

            # Stats (computed in thread, displayed in main thread via on_done)
            vals = filtered[y_col]
            self._pending_stats = (
                f"Points: {len(vals)}\n"
                f"Median: {vals.median():.4f}\n"
                f"Std:    {vals.std():.4f}\n"
                f"Min:    {vals.min():.4f}\n"
                f"Max:    {vals.max():.4f}"
            )

        def on_done():
            # canvas.draw() must run in main thread
            self._canvas.draw()
            self._stats.setPlainText(getattr(self, "_pending_stats", ""))
            self._btn_plot.setEnabled(True)

        def on_error(e):
            self._stats.setPlainText(f"Error: {e}")
            self._btn_plot.setEnabled(True)

        self._worker = WorkerThread(work)
        self._worker.finished.connect(on_done)
        self._worker.error.connect(on_error)
        self._worker.start()


# ===========================================================================
# Tab 6 — History
# ===========================================================================

class HistoryTab(QWidget):

    def __init__(self):
        super().__init__()
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._btn_run = _btn("▶  Run History Analysis")
        self._btn_stop = _btn("■  Stop", STOP_BUTTON_STYLE)
        self._btn_stop.setEnabled(False)
        self._btn_open = _btn("Open HistoryPlots", SECONDARY_STYLE)
        for b in [self._btn_run, self._btn_stop, self._btn_open]:
            top.addWidget(b)
        top.addStretch()
        layout.addLayout(top)

        info = QLabel(
            "Analyzes MasterOperations.parquet (created by Diagnostix tab).\n"
            "Detects >3% deviations from 30-day baseline per waveplate. Saves PNG plots."
        )
        info.setStyleSheet("color:#888; font-size:11px;")
        layout.addWidget(info)

        self._log = LogWidget()
        layout.addWidget(self._log)

        self._btn_run.clicked.connect(self._run)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_open.clicked.connect(
            lambda: os.startfile(str(APP_DIR / "HistoryPlots")))

    def _run(self):
        self._log.clear()
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)

        history = OperationHistoryLogic(APP_DIR)

        def work():
            history.run(
                log_fn=self._log.append_line,
                stop_flag=self._worker.is_stopped,
            )

        self._worker = WorkerThread(work)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(lambda e: self._log.append_line(f"ERROR: {e}"))
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.stop()
        self._btn_stop.setEnabled(False)

    def _on_done(self):
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)


# ===========================================================================
# Main window
# ===========================================================================

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diagnostika")
        self.resize(1400, 900)

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.North)
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #444; }
            QTabBar::tab {
                background: #2a2a2a;
                color: #ccc;
                padding: 8px 18px;
                border: 1px solid #444;
                border-bottom: none;
            }
            QTabBar::tab:selected { background: #3a3a3a; color: white; }
            QTabBar::tab:hover { background: #333; }
        """)

        tabs.addTab(BuilderTab(), "Builder")
        tabs.addTab(BrowserTab(), "Browser")
        tabs.addTab(SuspiciousTab(), "Suspicious")
        tabs.addTab(DiagnostixTab(), "Diagnostix")
        tabs.addTab(FilterPlotTab(), "Filter Plot")
        tabs.addTab(HistoryTab(), "History")
        tabs.addTab(PulserMonitorWidget(), "Pulser Monitor")

        self.setCentralWidget(tabs)


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(40, 40, 40))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(55, 55, 55))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.Highlight, QColor(58, 123, 213))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
