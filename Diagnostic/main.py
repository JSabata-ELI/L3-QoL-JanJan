import atexit
import sys
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QPlainTextEdit, QProxyStyle, QStyle,
)
from PySide6.QtCore import Qt, QThread, Signal

# NOTE: pandas (via operation_history_logic) and matplotlib (via monitor_tab)
# are the heavy part of startup — on a cold OS file cache their import can take
# tens of seconds. They are imported lazily (operation_history_logic inside
# HistoryTab._run; monitor_tab inside main() after the splash is shown) so the
# window/splash paints before that cost is paid instead of after.

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent

# PID lock file: lets remote_launcher.py tell whether the app is already
# running (so a Webex "run diagnostic" command doesn't spawn a second copy).
#
# Written in TWO places. This one sits next to the program and is kept only so
# an older watcher still finds it; the one that counts is the shared status file
# in %APPDATA%\Diagnostic (alerting.run_status_path). Next to the program is not
# a usable meeting point any more: the app now starts either from here or from a
# built version under C:\Dev\dist\Diagnostic\vX.Y.Z, and each of those would keep
# its own lock — so a watcher started from one folder would never see a copy
# launched from the other, and would open a second one.
LOCK_FILE = APP_DIR / "diagnostic.lock"


def _acquire_lock():
    # Imported here, not at the top: this runs before the splash, and alerting
    # pulls in requests. Same lazy-import rule as pandas/matplotlib above —
    # nothing that only matters later may delay the first painted pixel.
    import alerting
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    # monitoring=False is the honest starting point: the window is coming up but
    # nothing is armed yet. monitor_tab flips it when the switch actually moves,
    # which is what remote_launcher waits for before it reports "running".
    alerting.write_run_status(pid=os.getpid(), exe=str(sys.executable),
                              monitoring=False)


def _release_lock():
    import alerting
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    alerting.clear_run_status()


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


class ToolTipDelayStyle(QProxyStyle):
    """Show tooltips after a 0.5 s hover (Qt's default is ~0.7 s)."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return 500
        return super().styleHint(hint, option, widget, returnData)


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
    # Capped like monitor_tab's log: this window is meant to stay open for
    # weeks, and an uncapped log grows for exactly as long.
    MAX_LINES = 20000

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setStyleSheet(LOG_STYLE)
        self.setMaximumBlockCount(self.MAX_LINES)

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
        self._btn_run.setToolTip(
            "Reads MasterOperations.parquet and compares each waveplate's recent "
            "operations against its 30-day baseline. Flags any that drift more "
            "than 3 % and writes a PNG plot per flagged waveplate into "
            "HistoryPlots. Runs in the background — watch the log below.")
        self._btn_stop = _btn("■  Stop", STOP_BUTTON_STYLE)
        self._btn_stop.setEnabled(False)
        self._btn_stop.setToolTip(
            "Ask the running analysis to stop after its current step. "
            "Enabled only while an analysis is in progress.")
        self._btn_open = _btn("Open HistoryPlots", SECONDARY_STYLE)
        self._btn_open.setToolTip(
            "Open the HistoryPlots folder in Explorer to view the PNG plots "
            "produced by the last analysis run.")
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

        # Imported here, not at module load: this pulls in pandas, which is a
        # large chunk of the cold-start import cost and is only needed once the
        # user actually runs an analysis.
        from operation_history_logic import OperationHistoryLogic
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
        self.setWindowTitle("Diagnostic")
        # Fallback size for when the window is un-maximized; on launch the
        # window opens maximized (see main()). Wide enough that the PV table
        # shows all columns without a horizontal scrollbar.
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1720, screen.width() - 40),
                    min(950, screen.height() - 60))

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.North)

        # Lazy import: constructing the monitor tab pulls in matplotlib. Done
        # here so main() can show the splash first (see main()).
        from monitor_tab import MonitorWidget
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

def _icon_file() -> Path | None:
    """icon.ico sits next to the exe. APP_DIR is __file__-based, which in a
    frozen build points into the bundle rather than the exe folder."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    cands.append(APP_DIR / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


def main():
    _acquire_lock()
    atexit.register(_release_lock)

    # Give the taskbar button its own identity instead of grouping under the
    # generic host process, and hand it our icon.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ELI.Diagnostic")
    except Exception:
        pass

    app = QApplication(sys.argv)
    _ico = _icon_file()
    if _ico:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(_ico)))
    app.setStyle(ToolTipDelayStyle("Fusion"))
    app.setStyleSheet(APP_STYLESHEET)

    # Cold-start feedback: building MainWindow imports matplotlib (and touches
    # a large compiled dependency tree that Windows may re-scan on a cold file
    # cache), which can take tens of seconds. Paint a splash first and force it
    # on screen with processEvents() so the user isn't staring at nothing while
    # that happens — only PySide6 is loaded at this point, so the splash is
    # near-instant.
    splash = QLabel("Loading Diagnostics…")
    splash.setAlignment(Qt.AlignCenter)
    splash.setWindowFlags(Qt.SplashScreen | Qt.WindowStaysOnTopHint)
    splash.setFixedSize(360, 120)
    splash.setStyleSheet(
        "background:#f3f3f3; color:#1565C0; font-size:16px; font-weight:600; "
        "border:1px solid #ccc;")
    splash.show()
    app.processEvents()

    window = MainWindow()
    if _ico:
        from PySide6.QtGui import QIcon
        window.setWindowIcon(QIcon(str(_ico)))
    window.showMaximized()
    splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
