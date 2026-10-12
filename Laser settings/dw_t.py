"""Laser settings — the DM writer tab: this PC reads the mirror for everybody.

Meant for OPR1, the PC with the Remote Desktop window to the deformable
mirror's PC open. Switched on here, this PC photographs that window every 5 s,
finds the Set-up grid of Mirror Control Window in it and writes what it saw to
the scratch share, where the DM settings tab on every PC reads it (`ls_dm`).

Nothing is done TO the Remote Desktop window: it is never activated, moved,
resized or clicked, and nothing is typed into it. PrintWindow draws it into
memory, about 20 ms of work every 5 s. The operator keeps using it as before.

A minimised window cannot be photographed — Windows does not draw it — so
while it is minimised the file says "unknown" and carries no colours.

The switch is kept per PC, in this PC's own settings file: switching it on
here does not switch it on anywhere else.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

import ls_core as C
import ls_dm as D
import ls_ui as U
from dm_t import DmGrid


class DmWriterTab(QWidget):
    """The switch, the window's title, and what this PC saw last."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._loading = False
        self._view = None
        self._pic_said = None
        self._build()
        self.reload()

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
        page.setObjectName("dwPage")
        page.setStyleSheet(f"#dwPage {{ background: {U.PAPER}; }}")
        root = QVBoxLayout(page)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        scroll.setWidget(page)
        outer.addWidget(scroll)

        box = QGroupBox("Reading the deformable mirror on this PC")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)
        self._write = QCheckBox("Read the Set-up grid from the Remote Desktop "
                                "window and write it to the scratch share")
        self._write.setStyleSheet(U.chk_qss())
        self._write.setToolTip(
            "Tick this only on the PC that has the Remote Desktop window to the "
            "mirror's PC open (OPR1).\nEvery few seconds (Look every) the "
            "window is photographed in "
            "memory — never activated, moved or clicked — and what the Set-up "
            "grid shows is written for the DM settings tab on every PC.\n"
            "While the window is minimised it cannot be read, and \"unknown\" "
            "is written.")
        self._write.toggled.connect(self._typed)
        lay.addWidget(self._write)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("The window's title contains"))
        self._title = QLineEdit()
        self._title.setFixedWidth(220)
        self._title.setToolTip(
            "The Remote Desktop window is found by this text in its title — "
            "the mirror PC's address,\nas in \"192.168.1.107 - Remote Desktop "
            "Connection\".")
        self._title.editingFinished.connect(self._typed)
        row.addWidget(self._title)
        row.addSpacing(24)
        row.addWidget(QLabel("Look every"))
        self._every = QSpinBox()
        self._every.setRange(C.DM_EVERY_MIN_S, C.DM_EVERY_MAX_S)
        self._every.setSuffix(" s")
        self._every.setFixedWidth(90)
        self._every.setToolTip(
            "How often this PC photographs the window and writes what it "
            "read.\nEvery other PC reads the file just as often, so a change "
            "of set-up shows everywhere within this time.")
        self._every.valueChanged.connect(self._typed)
        row.addWidget(self._every)
        row.addStretch(1)
        lay.addLayout(row)
        root.addWidget(box)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(40)
        root.addWidget(self._status)

        # What this PC photographs, for when the grid is plainly on the screen
        # and still not found (9.10.2026). Saved by itself the first time the
        # grid is missing; this saves the next look on demand.
        pic_row = QHBoxLayout()
        pic_row.setSpacing(8)
        self._pic_btn = U.button(
            "Save a picture of the window", icon_name="save",
            tip="Save what this PC photographs of the Remote Desktop window, "
                "as a PNG next to the state file on the scratch share.\nDone "
                "by itself the first time the Set-up grid is not found.")
        self._pic_btn.clicked.connect(self._save_picture)
        pic_row.addWidget(self._pic_btn)
        self._pic_note = QLabel("")
        self._pic_note.setWordWrap(True)
        self._pic_note.setStyleSheet("color: #16202c; font-size: 12px;")
        self._pic_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        pic_row.addWidget(self._pic_note, 1)
        root.addLayout(pic_row)

        grids = QHBoxLayout()
        grids.addStretch(1)
        self._grid = DmGrid("What this PC read last")
        self._grid.setMaximumWidth(340)
        grids.addWidget(self._grid, 2)
        grids.addStretch(1)
        root.addLayout(grids)

        where = QLabel(f"Written to: {D.share_folder_text()}, file "
                       f"{D.STATE_NAME}")
        where.setWordWrap(True)
        where.setStyleSheet(f"color: {U.QUIET}; font-size: 11px;")
        where.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(where)
        root.addStretch(1)

    # ── the settings ───────────────────────────────────────────────────────
    def reload(self):
        dm = self._win.dm_settings()
        self._loading = True
        try:
            self._write.setChecked(bool(dm["write"]))
            self._title.setText(dm["title"])
            self._every.setValue(int(dm["every_s"]))
        finally:
            self._loading = False
        self.refresh()

    def _typed(self, *_a):
        if self._loading:
            return
        dm = self._win.dm_settings()
        dm["write"] = bool(self._write.isChecked())
        dm["every_s"] = C.dm_every(self._every.value())
        title = self._title.text().strip()
        if title:
            dm["title"] = title
        else:
            self._loading = True
            try:
                self._title.setText(dm["title"])
            finally:
                self._loading = False
        self._win.dm_changed()
        self.refresh()

    # ── what this PC saw ───────────────────────────────────────────────────
    def refresh(self):
        dm = self._win.dm_settings()
        engine = self._win.engine()
        view = engine.dm() if engine is not None else None
        local = view.local if view is not None else None
        if not dm["write"]:
            text, kind = ("Off — this PC does not read the mirror.", "off")
            colours = None
        elif local is None:
            text, kind = ("Switched on — the first look is on its way.",
                          "unknown")
            colours = None
        else:
            said = D.STATUS_TEXT.get(local.status, "")
            if local.status != D.STATUS_OK and local.problem:
                said = local.problem
            text = f"Last look at {D.clock_text(local.at)}: {said}"
            if local.status == D.STATUS_OK and local.how == D.HOW_SCREEN:
                text += " Copied off the screen, so keep the window uncovered."
            if local.took_ms is not None:
                text += f" (took {local.took_ms:.0f} ms)"
            kind = "ok" if local.status == D.STATUS_OK else "warn"
            if view.problem:
                text += f"\n{view.problem}"
                kind = "trip"
            colours = local.colours
        ground, ink = U.STATE_COLORS[kind]
        style = (f"QLabel {{ background: {ground}; color: {ink};"
                 f" border: 1px solid {ink}; border-radius: 4px;"
                 f" font-size: 13px; padding: 8px; }}")
        if self._status.styleSheet() != style:
            self._status.setStyleSheet(style)
        if self._status.text() != text:
            self._status.setText(text)
        self._grid.set_grid(colours)
        self._pic_btn.setEnabled(bool(dm["write"]) and engine is not None)
        # Only a NEW sentence replaces the box: "Saving…" stays until the
        # save it waits for has said how it went.
        note = view.picture_note if view is not None else None
        if note and note != self._pic_said:
            self._pic_said = note
            self._pic_note.setText(note)

    def _save_picture(self):
        engine = self._win.engine()
        if engine is None:
            return
        engine.dm_save_picture()
        self._pic_note.setText("Saving the next look…")
