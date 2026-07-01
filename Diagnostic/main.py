import sys
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QPlainTextEdit,
)
from PySide6.QtCore import QThread, Signal

from operation_history_logic import OperationHistoryLogic
from monitor_tab import MonitorWidget

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

APP_STYLESHEET = """
    QWidget      { background: #f3f3f3; color: #111; }
    QLabel       { background: transparent; }
    QPushButton  { padding: 5px 8px; }
    QComboBox    { padding: 3px 6px; }
    QProgressBar { background: #fff; }
    QTabWidget::pane { border: 1px solid #ccc; }
    QTabBar::tab {
        background: #e8e8e8; color: #444;
        padding: 6px 18px; border: 1px solid #ccc;
        border-bottom: none; border-radius: 3px 3px 0 0;
        margin-right: 2px;
    }
    QTabBar::tab:selected { background: #f3f3f3; color: #111; font-weight: 600; }
    QTabBar::tab:hover    { background: #d8e8ff; }
    QTableView { background: #ffffff; gridline-color: #ddd; }
    QHeaderView::section {
        background: #e8e8e8; color: #333;
        padding: 4px; border: 1px solid #ccc;
    }
    QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget {
        background: #ffffff; color: #111; border: 1px solid #bbb;
    }
    QTableView::indicator, QCheckBox::indicator {
        width: 16px; height: 16px;
        border: 1px solid #888; border-radius: 3px; background: #fff;
    }
    QTableView::indicator:checked, QCheckBox::indicator:checked {
        background: #1565C0; border-color: #1565C0;
    }
    QToolTip {
        background: #ffffcc; color: #111;
        border: 1px solid #aaa; padding: 4px;
    }
"""

BUTTON_STYLE = """
QPushButton {
    background: #1565C0;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
}
QPushButton:hover { background: #0D47A1; }
QPushButton:disabled { background: #bbb; color: #888; }
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
QPushButton:disabled { background: #bbb; color: #888; }
"""

SECONDARY_STYLE = """
QPushButton {
    background: #e8e8e8;
    color: #111;
    border: 1px solid #bbb;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover { background: #d8e8ff; }
"""

LOG_STYLE = """
QPlainTextEdit {
    background: #ffffff;
    color: #222;
    font-family: Consolas, monospace;
    font-size: 11px;
    border: 1px solid #ccc;
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


# ===========================================================================
# History
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
            "Analyzes MasterOperations.parquet.\n"
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

        self._monitor_tab = MonitorWidget()
        tabs.addTab(self._monitor_tab, "PV Monitor")
        tabs.addTab(HistoryTab(), "History")

        # Log stays the last tab — add any new tabs above this line.
        tabs.addTab(self._monitor_tab.log, "Log")

        self.setCentralWidget(tabs)

    def closeEvent(self, event):
        try:
            self._monitor_tab.shutdown()
        except Exception:
            pass
        super().closeEvent(event)


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
