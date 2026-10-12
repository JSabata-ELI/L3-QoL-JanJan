"""Setting tabs for the CSS Logger.

Each tab is a complete, independent CSS Logger (one `CSSLoggerWidget` from
main.py) with its own channels, preset, conditions, time window and Live — e.g.
one tab on the Operation preset, one on a hand-picked set of channels, one on a
preset with the ramping channels added.

Only the tab on screen draws. A tab that is not on screen is suspended
(CSSLoggerWidget.suspend): in Live it keeps reading the archive, but only every
HIDDEN_LIVE_POLL_MS and without redrawing anything; resume() catches it up to
"now" at once and draws what it collected. Every tab follows the same rule while
the Spectra tab is the one on screen.

Tabs saved at the last close come back in the background: the tab on screen is
built at once, the others one by one a few seconds apart, already suspended — so
switching to one later finds it loaded and up to date. They are remembered in
%APPDATA%/CSS Logger/logger_tabs.json (per user, not on the share): name,
channels, preset, conditions and the window (or "live" with its span).

The tab bar sits in the main window's tab row, after "CSS Logger" and "Spectra"
(the same arrangement as the Image Slider's layout tabs in Image Tools).
"""
import json
import os
from pathlib import Path

from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLineEdit, QSizePolicy,
                               QStackedWidget, QTabBar, QToolButton, QVBoxLayout,
                               QWidget)

TABS_PATH = Path(os.environ.get("CSS_LOGGER_TABS_FILE") or
                 Path(os.environ.get("APPDATA", Path.home())) / "CSS Logger"
                 / "logger_tabs.json")

AUTOSAVE_MS = 15_000
# Restored tabs that are not on screen are built this long after the start, and
# this far apart: each one runs its own first load, and all of them at once
# would compete with the tab the user is looking at.
BACKGROUND_FIRST_MS = 4_000
BACKGROUND_NEXT_MS = 3_000

_TAB_QSS = """
QTabBar::tab {
    background: #e4e4e4; color: #333;
    padding: 3px 6px 3px 12px; border: 1px solid #c4c4c4;
    border-bottom: none; border-radius: 3px 3px 0 0;
    margin-right: 2px; min-width: 70px; font-size: 11px;
}
QTabBar::tab:selected { background: #ffffff; color: #111; font-weight: 600; }
QTabBar::tab:hover    { background: #d8e8ff; color: #111; }
"""

_BTN_QSS = """
QToolButton { background: transparent; border: 1px solid transparent;
              border-radius: 3px; padding: 0px; }
QToolButton:hover   { background: #d0d8e4; border-color: #9aa6b6; }
QToolButton:pressed { background: #b8c4d4; }
"""

_PLUS_QSS = """
QToolButton { background: #ffffff; border: 1px solid #9aa6b6;
              border-radius: 3px; padding: 0px; margin: 1px 4px; }
QToolButton:hover   { background: #d8e8ff; }
QToolButton:pressed { background: #b8c4d4; }
"""


def _drawn_icon(kind: str, side: int = 14) -> QIcon:
    """Dark ink '+' or '×', painted (not a font glyph), sharp on high-DPI screens.
    The same pixmap is registered for every icon mode so no style can pale it."""
    scale = 3
    pm = QPixmap(side * scale, side * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#2b2b2b"))
    pen.setWidthF(2.0 * scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    s = side * scale
    m = 3.5 * scale
    if kind == "plus":
        p.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
        p.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
    else:
        p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
        p.drawLine(QPointF(s - m, m), QPointF(m, s - m))
    p.end()
    pm.setDevicePixelRatio(scale)
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected,
                 QIcon.Mode.Disabled):
        icon.addPixmap(pm, mode)
    return icon


class _Bar(QTabBar):
    """QTabBar that tells its layout when its size changed. Plain QTabBar keeps the
    pre-stylesheet size hint after polishing, which left a gap before the "+"."""

    def tabLayoutChange(self):
        super().tabLayoutChange()
        self.updateGeometry()

    def changeEvent(self, ev):
        super().changeEvent(ev)
        self.updateGeometry()


class _TabArea(QWidget):
    """A tab bar with the "+" button right after the last tab, over a page stack.
    (QTabWidget's corner widget sits at the far edge instead.)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bar = _Bar(self)
        self._bar.setDrawBase(False)
        self._bar.setExpanding(False)
        self._bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._stack = QStackedWidget(self)
        self._pages: list = []                       # in tab order
        self.corner = QWidget(self)                  # holds the "+" button
        self.chrome = QWidget(self)
        row = QHBoxLayout(self.chrome)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._bar)
        row.addWidget(self.corner)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.chrome)
        lay.addWidget(self._stack, 1)
        self.currentChanged = self._bar.currentChanged
        self._bar.currentChanged.connect(self._sync_stack)
        self._bar.tabMoved.connect(self._on_moved)

    def _sync_stack(self, idx: int):
        if 0 <= idx < len(self._pages):
            self._stack.setCurrentWidget(self._pages[idx])
        self._bar.updateGeometry()

    def _on_moved(self, frm: int, to: int):
        self._pages.insert(to, self._pages.pop(frm))

    def tabBar(self) -> QTabBar:
        return self._bar

    def count(self) -> int:
        return len(self._pages)

    def widget(self, i: int):
        return self._pages[i] if 0 <= i < len(self._pages) else None

    def indexOf(self, w) -> int:
        try:
            return self._pages.index(w)
        except ValueError:
            return -1

    def addTab(self, page, name: str) -> int:
        self._stack.addWidget(page)
        self._pages.append(page)
        idx = self._bar.addTab(name)
        self._sync_stack(self._bar.currentIndex())
        return idx

    def removeTab(self, i: int):
        page = self._pages.pop(i)
        self._bar.removeTab(i)
        self._stack.removeWidget(page)
        self._sync_stack(self._bar.currentIndex())

    def tabText(self, i: int) -> str:
        return self._bar.tabText(i)

    def setTabText(self, i: int, t: str):
        self._bar.setTabText(i, t)
        self._bar.updateGeometry()

    def setTabToolTip(self, i: int, t: str):
        self._bar.setTabToolTip(i, t)

    def currentIndex(self) -> int:
        return self._bar.currentIndex()

    def setCurrentIndex(self, i: int):
        self._bar.setCurrentIndex(i)

    def currentWidget(self):
        return self.widget(self._bar.currentIndex())

    def setCurrentWidget(self, w):
        i = self.indexOf(w)
        if i >= 0:
            self._bar.setCurrentIndex(i)


class _TabPage(QWidget):
    """One setting tab: its CSSLoggerWidget once built, or the saved state."""

    def __init__(self, state: "dict | None" = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.logger = None
        self.state = state


class LoggerTabs(QWidget):
    """The "CSS Logger" tab of the main window: a row of setting tabs.

    ``factory(tab_state)`` builds one CSSLoggerWidget; ``tab_state`` is None for
    the classic start (the config file's channels, the Operation preset, Live).
    """

    def __init__(self, factory, parent=None):
        super().__init__(parent)
        self._factory = factory
        self._outer_visible = True
        self._loading = True
        self._last_saved = ""
        self._rename_edit = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._tabs = _TabArea(self)
        bar = self._tabs.tabBar()
        bar.setMovable(True)
        bar.setUsesScrollButtons(True)
        bar.setStyleSheet(_TAB_QSS)
        lay.addWidget(self._tabs)

        self._close_icon = _drawn_icon("close", 12)
        self._btn_new = QToolButton(self._tabs.corner)
        self._btn_new.setIcon(_drawn_icon("plus", 14))
        self._btn_new.setIconSize(QSize(14, 14))
        self._btn_new.setFixedSize(30, 22)
        self._btn_new.setStyleSheet(_PLUS_QSS)
        self._btn_new.setToolTip("New setting tab — starts as a copy of the tab "
                                 "on screen; change its channels, conditions or "
                                 "time window as you like")
        self._btn_new.clicked.connect(self._on_new_clicked)
        corner_lay = QVBoxLayout(self._tabs.corner)
        corner_lay.setContentsMargins(0, 0, 0, 0)
        corner_lay.addWidget(self._btn_new)

        bar.tabBarDoubleClicked.connect(self._start_rename)
        bar.tabMoved.connect(lambda *_: self.save())
        self._tabs.currentChanged.connect(self._on_current_changed)

        saved = self._load()
        for st in saved:
            self._add_page(st.get("name") or self._next_name(), st)
        if self._tabs.count() == 0:
            self._add_page("", None)          # named after its preset once built
        cur = self._saved_current if 0 <= self._saved_current < self._tabs.count() else 0
        self._tabs.setCurrentIndex(cur)
        self._build(self._current_page())
        if not self._tabs.tabText(cur):
            self._tabs.setTabText(cur, self._default_name(self._current_page()))
        self._loading = False
        self._apply_activity()

        # The rest come up in the background, one at a time.
        QTimer.singleShot(BACKGROUND_FIRST_MS, self._build_next_in_background)

        self._autosave = QTimer(self)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.save)
        self._autosave.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.save)

    # ── pages ────────────────────────────────────────────────────────────────
    def _pages(self) -> list:
        return [self._tabs.widget(i) for i in range(self._tabs.count())]

    def _current_page(self) -> "_TabPage | None":
        w = self._tabs.currentWidget()
        return w if isinstance(w, _TabPage) else None

    def loggers(self) -> list:
        """Every CSSLoggerWidget built so far, in tab order."""
        return [p.logger for p in self._pages() if p.logger is not None]

    def active(self):
        """The CSSLoggerWidget on screen (built if it was not yet)."""
        page = self._current_page()
        return self._build(page) if page is not None else None

    def first(self):
        """The first tab's CSSLoggerWidget: it owns the config file, as the one
        and only CSS Logger did before there were tabs."""
        pages = self._pages()
        return self._build(pages[0]) if pages else None

    def _next_name(self) -> str:
        used = {self._tabs.tabText(i) for i in range(self._tabs.count())}
        n = 1
        while f"Tab {n}" in used:
            n += 1
        return f"Tab {n}"

    def _default_name(self, page) -> str:
        """A first tab is named after its preset (e.g. "Operation")."""
        lg = page.logger if page is not None else None
        name = ""
        try:
            name = lg._preset_combo.currentText().strip() if lg is not None else ""
        except Exception:
            name = ""
        return name or self._next_name()

    def _add_page(self, name: str, state: "dict | None") -> _TabPage:
        page = _TabPage(state)
        idx = self._tabs.addTab(page, name)
        self._tabs.setTabToolTip(idx, "Double-click to rename")
        btn = QToolButton()
        btn.setIcon(self._close_icon)
        btn.setIconSize(QSize(12, 12))
        btn.setFixedSize(18, 18)
        btn.setStyleSheet(_BTN_QSS)
        btn.setToolTip("Close this setting tab")
        btn.clicked.connect(lambda _=False, p=page: self._close_page(p))
        self._tabs.tabBar().setTabButton(idx, QTabBar.ButtonPosition.RightSide, btn)
        self._sync_close_buttons()
        self._tabs.tabBar().updateGeometry()
        return page

    def _sync_close_buttons(self):
        """The last tab cannot be closed — there is always a CSS Logger."""
        only = self._tabs.count() <= 1
        bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            b = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if b is not None:
                b.setVisible(not only)
        bar.updateGeometry()

    def _build(self, page: "_TabPage | None", hidden: bool = False):
        """The page's CSSLoggerWidget, built on first need. One built for a tab
        that is not on screen is suspended before its first load starts."""
        if page is None:
            return None
        if page.logger is not None:
            return page.logger
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            lg = self._factory(page.state)
        finally:
            QApplication.restoreOverrideCursor()
        if hidden:
            lg.suspend()
        page.layout().addWidget(lg)
        page.logger = lg
        page.state = None
        return lg

    def _build_next_in_background(self):
        """Build the next restored tab that is not on screen yet (suspended),
        so it loads and keeps up while the user looks at another one."""
        for page in self._pages():
            if page.logger is None:
                self._build(page, hidden=page is not self._current_page()
                            or not self._outer_visible)
                QTimer.singleShot(BACKGROUND_NEXT_MS, self._build_next_in_background)
                return

    def new_tab(self, state: "dict | None" = None, name: "str | None" = None):
        """Create a tab from ``state`` (None: a copy of the tab on screen), make
        it current and return its CSSLoggerWidget."""
        if state is None:
            cur = self.active()
            state = cur.export_tab_state() if cur is not None else None
        page = self._add_page(name or self._next_name(), state)
        lg = self._build(page)
        self._tabs.setCurrentWidget(page)
        self.save()
        return lg

    def _on_new_clicked(self):
        self.new_tab()

    def _close_page(self, page: _TabPage):
        if self._tabs.count() <= 1:
            return
        idx = self._tabs.indexOf(page)
        if idx < 0:
            return
        lg = page.logger
        if lg is not None:
            self._stop_logger(lg)
        self._tabs.removeTab(idx)
        self._sync_close_buttons()
        page.deleteLater()
        self.save()

    @staticmethod
    def _stop_logger(lg):
        """Stop everything a tab has running: Live, a load, a zoom read."""
        try:
            lg._stop_live("Tab closed.")
        except Exception:
            pass
        try:
            lg._load_epoch += 1
            lg._live_epoch += 1
            lg._export_epoch = getattr(lg, "_export_epoch", 0) + 1
        except Exception:
            pass

    def shutdown(self):
        """The window is closing: stop every tab."""
        for lg in self.loggers():
            self._stop_logger(lg)

    # ── switching ────────────────────────────────────────────────────────────
    def _on_current_changed(self, _idx: int):
        if self._rename_edit is not None:
            self._finish_rename()
        if self._loading:
            return
        self._build(self._current_page())
        self._apply_activity()
        self.save()

    def set_outer_visible(self, visible: bool):
        """The main window switched to / away from the CSS Logger tab."""
        self._outer_visible = bool(visible)
        self._apply_activity()

    def _apply_activity(self):
        """Exactly one tab draws: the current one, while the CSS Logger is the
        main window's tab on screen. The others are suspended."""
        cur = self._current_page()
        for page in self._pages():
            lg = page.logger
            if lg is None:
                continue
            if page is cur and self._outer_visible:
                lg.resume()
            else:
                lg.suspend()

    def move_chrome_into(self, host_layout):
        """Put the tab bar + "+" into the main window's tab row. Added to the new
        layout first, so it is never a parentless window for a moment."""
        chrome = self._tabs.chrome
        self._tabs.layout().removeWidget(chrome)
        host_layout.addWidget(chrome)

    # ── renaming ─────────────────────────────────────────────────────────────
    def _start_rename(self, idx: int):
        if idx < 0:
            return
        bar = self._tabs.tabBar()
        ed = QLineEdit(bar)
        ed.setText(self._tabs.tabText(idx))
        ed.setStyleSheet("QLineEdit { background: #ffffff; color: #111; "
                         "border: 1px solid #5a7ab0; font-size: 11px; padding: 0 3px; }")
        ed.setGeometry(bar.tabRect(idx).adjusted(2, 2, -22, -1))
        ed.selectAll()
        ed.show()
        ed.setFocus()
        ed._tab_page = self._tabs.widget(idx)
        ed.editingFinished.connect(self._finish_rename)
        self._rename_edit = ed

    def _finish_rename(self):
        ed = self._rename_edit
        if ed is None:
            return
        self._rename_edit = None
        name = ed.text().strip()
        idx = self._tabs.indexOf(ed._tab_page)
        if name and idx >= 0:
            self._tabs.setTabText(idx, name)
        ed.hide()
        ed.deleteLater()
        self.save()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> list:
        self._saved_current = 0
        try:
            d = json.loads(TABS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(d, dict):
            return []
        try:
            self._saved_current = int(d.get("current") or 0)
        except (TypeError, ValueError):
            self._saved_current = 0
        return [t for t in (d.get("tabs") or []) if isinstance(t, dict)]

    def _page_state(self, page: _TabPage) -> dict:
        if page.logger is not None:
            try:
                return page.logger.export_tab_state()
            except Exception:
                return {}
        return dict(page.state or {})

    def save(self):
        if self._loading:
            return
        tabs = []
        for i, page in enumerate(self._pages()):
            st = self._page_state(page)
            st["name"] = self._tabs.tabText(i)
            tabs.append(st)
        text = json.dumps({"current": self._tabs.currentIndex(), "tabs": tabs},
                          indent=2, ensure_ascii=False)
        if text == self._last_saved:
            return
        try:
            TABS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = TABS_PATH.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, TABS_PATH)
            self._last_saved = text
        except Exception:
            pass
