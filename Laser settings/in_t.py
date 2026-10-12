"""Laser settings — the Info / Log page. BUILT, WORKING, AND NOT ON A TAB.

It is still created on every launch and `main.log()` still writes every line
into it — `main._add_tab(..., shown=False)` simply never adds it to the tab bar.
Asked for on 1.10.2026: "info / log smaž, nebo minimálně zneviditelni,.. v kodu
to můžeš nechat, ale tahle záložka tam už nebude". Nothing in here was changed
for that, so putting the tab back is one argument in `main._build_ui`.

What is read back from instead is the log FILE, which has always had the same
lines. The live sentence this page's top line carries — whose limits are in
force — is on the first tab, in the squares and their tooltips.

The Overview carried a sentence along its bottom edge and the window carried
another under both tabs: which valves cannot be read, whose limits are in force,
how many channels are watched, when the last reading arrived, where the settings
file is. Asked for on 30.9.2026: take them off the working tabs and put them
here.

Two halves:

    the state now    the same two sentences, rewritten every pass
    the log          every line the program has said, newest at the bottom

The log is the same one that goes to `%LOCALAPPDATA%\\Laser settings\\
laser_settings_crash.log` — `main.log()` writes to both, so a failure that
scrolled past is read back out of the file, and the last thing that happened is
on screen without opening anything.
"""

from __future__ import annotations

import datetime
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                               QVBoxLayout, QWidget)

import ls_core as C
import ls_log
import ls_ui as U

# The house log panel: white paper, dark ink, one fixed-width typeface, and a
# hard cap on the lines Qt keeps. Copied from Diagnostic's `LogWidget`, which is
# where this shape comes from.
MAX_LINES = 5000

_LOG_QSS = (
    "QPlainTextEdit { background: #ffffff; color: #222222;"
    " border: 1px solid #b0b0b0; border-radius: 3px; padding: 3px 4px; }"
) + U.SCROLLBAR_QSS

_PATH_QSS = f"color: {U.QUIET}; font-size: 11px;"


class InfoTab(QWidget):
    """What the program is doing, where its files are, and what it has said."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._state_text = None
        self._clock_text = None
        self._build()

    # ── building ───────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # No heading over these two: the sentence says what it is.
        self._state = QLabel("")
        self._state.setWordWrap(True)
        self._state.setStyleSheet("color: #16202c; font-size: 13px;")
        root.addWidget(self._state)
        self._clock = QLabel("")
        self._clock.setStyleSheet(f"color: {U.QUIET}; font-size: 12px;")
        root.addWidget(self._clock)

        root.addWidget(U.hsep())

        # The two paths. Selectable, because the reason anybody reads them is to
        # paste one somewhere, and a path that cannot be copied is a path that
        # gets typed out by hand and mistyped.
        paths = QVBoxLayout()
        paths.setSpacing(2)
        for text in (f"Settings: {C.config_path()}",
                     f"Log: {ls_log.log_path()}"):
            line = QLabel(text)
            line.setStyleSheet(_PATH_QSS)
            line.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            line.setCursor(Qt.CursorShape.IBeamCursor)
            paths.addWidget(line)
        root.addLayout(paths)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(U.section_label("Log"))
        tools.addStretch(1)
        folder = U.button("Open the log folder", icon_name="eye",
                          tip="Open the folder the log file is written to.")
        folder.clicked.connect(self._open_folder)
        tools.addWidget(folder)
        clear = U.button("Clear", icon_name="trash",
                         tip="Empty the list on screen. The file on disk keeps "
                             "everything.")
        clear.clicked.connect(self._clear)
        tools.addWidget(clear)
        root.addLayout(tools)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(_LOG_QSS)
        self._log.setFont(U.mono_font(9))
        self._log.setMaximumBlockCount(MAX_LINES)
        self._log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._log, stretch=1)

    # ── the log ────────────────────────────────────────────────────────────
    def append_line(self, text):
        """One line, stamped, at the bottom — and the view follows it down."""
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.appendPlainText(f"[{stamp}]  {text}")
        bar = self._log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear(self):
        self._log.clear()
        self.append_line("the list on screen was emptied — the file kept "
                         "everything")

    def _open_folder(self):
        """The house pattern: the helper raises, the caller says what failed."""
        try:
            os.startfile(str(ls_log.log_path().parent))
        except Exception as exc:
            self._win.log(f"The log folder could not be opened: {exc}")

    # ── the two sentences ──────────────────────────────────────────────────
    def reload(self):
        self.refresh()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh()

    def refresh(self):
        # Not on the tab bar: nobody sees these two sentences while the window
        # is up, so they are not worked out once a second for nothing.
        if self.window().isVisible() and not self.isVisible():
            return
        engine = self._win.engine()
        readings = engine.readings()
        last_ok = engine.last_ok()
        now = time.time()
        state = C.status_line(self._win.rows(), self._win.shooting(),
                             readings, last_ok, now, engine.screen(),
                             getattr(self._win, "scanning", bool)())
        if state != self._state_text:
            self._state_text = state
            self._state.setText(state)
        clock = C.read_clock_text(last_ok, now)
        if clock != self._clock_text:
            self._clock_text = clock
            self._clock.setText(clock)
