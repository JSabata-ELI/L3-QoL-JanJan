import sys
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QPlainTextEdit,
)
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPalette, QColor

from operation_history_logic import OperationHistoryLogic
from monitor_tab import MonitorWidget

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent

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

        self._monitor_tab = MonitorWidget()
        tabs.addTab(self._monitor_tab, "PV Monitor")
        tabs.addTab(HistoryTab(), "History")

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
