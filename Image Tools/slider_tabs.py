"""Layout tabs for the Image Slider.

Each tab is a complete, independent Image Slider (one `Viewer` from is_t.py) with
its own cameras, arrangement, time window and live mode — e.g. one tab on the
"ghost tile" cameras and another on the "laser running" set.

Only the tab on screen works. A hidden tab is suspended (Viewer.suspend): its
timers, preview sweep, decoding and PV reads stop, while everything already loaded
stays in memory. In live mode its folder watchers keep noting new file names (they
sleep in the OS until a file appears), so coming back is instant: resume() jumps to
the newest frames and one catch-up poll covers the rest. The timeline is never
re-scanned on a tab switch.

Tabs are built lazily — a tab restored at startup becomes a Viewer only when it is
first opened — and remembered in %APPDATA%/ELI_ImageTools/slider_tabs.json: name,
cameras, time window (or "live") and the moment on screen.

Frames or moments sent from another tab (Image Finder, Shot Finder, Workshop)
always open in a NEW layout tab, so the arrangements already set up are untouched.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QLineEdit, QTabBar, QToolButton,
                               QVBoxLayout, QWidget)

TABS_PATH = (Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools"
             / "slider_tabs.json")

# Hidden tabs that may keep their decoded preview frames (up to 2 GB each, see
# PROXY_RAM_BUDGET_MB in is_t.py). The most recently used ones keep theirs; an
# older hidden tab frees it and rebuilds it when it is opened again.
PREVIEW_KEEP_HIDDEN = 1

AUTOSAVE_MS = 15_000

# The automatic tab names before "Panel N" — renamed when the tabs are restored.
_OLD_AUTO_NAME = re.compile(r"^Tab ?\d+$")

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

    QTabWidget's corner widget cannot do this — it sits at the far edge, and with
    the app stylesheet it was drawn over the first tab. The methods mirror the
    QTabWidget ones SliderTabs uses."""

    def __init__(self, parent=None):
        from PySide6.QtWidgets import QHBoxLayout, QStackedWidget
        super().__init__(parent)
        self._bar = _Bar(self)
        self._bar.setDrawBase(False)
        self._bar.setExpanding(False)
        # Never wider than its tabs (keeps "+" right after the last one); narrower
        # when there are too many, which brings up the scroll arrows.
        from PySide6.QtWidgets import QSizePolicy
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
        # The bar's size hint changes with every tab, close button, name and with the
        # bold current tab, and QTabBar does not always tell the layout — a stale hint
        # left a gap before the "+".
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
    """One layout tab. Holds its Viewer once built, or the saved state until then."""

    def __init__(self, state: "dict | None" = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.viewer = None
        self.state = state          # restored state, handed over when the Viewer is built
        self.started = False        # first show has happened (state loaded / dialog opened)


class SliderTabs(QWidget):
    """The Image Slider tab of the main window: a tab bar of layout tabs.

    Other tabs talk to this object as if it were the Slider: open_moment,
    open_folder_path and receive_external_folder open a new layout tab;
    cancel_scan and stop reach every tab."""

    def __init__(self, viewer_cls, parent=None):
        super().__init__(parent)
        self._viewer_cls = viewer_cls
        self._refs: dict = {}            # attributes main.py wires into every Viewer
        self._outer_visible = False      # is the Image Slider tab itself on screen
        self._mru: list = []             # pages, most recently shown first
        self._last_saved = ""
        self._loading = True

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
        self._btn_new.setToolTip("New layout tab — its own cameras, arrangement and "
                                 "time window")
        self._btn_new.clicked.connect(self._on_new_clicked)
        corner_lay = QVBoxLayout(self._tabs.corner)
        corner_lay.setContentsMargins(0, 0, 0, 0)
        corner_lay.addWidget(self._btn_new)

        bar.tabBarDoubleClicked.connect(self._start_rename)
        bar.tabMoved.connect(lambda *_: self.save())
        self._tabs.currentChanged.connect(self._on_current_changed)
        self._rename_edit: "QLineEdit | None" = None

        for st in self._load():
            name = st.get("name") or ""
            # "Tab 1" / "Tab1" were the automatic names before "Panel N"; a
            # name the user typed is kept.
            if not name or _OLD_AUTO_NAME.match(name):
                name = self._next_name()
            self._add_page(name, st)
        if self._tabs.count() == 0:
            self._add_page(self._next_name(), None)
        cur = self._saved_current
        if 0 <= cur < self._tabs.count():
            self._tabs.setCurrentIndex(cur)
        self._loading = False

        self._autosave = QTimer(self)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.save)
        self._autosave.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.save)

    # ── wiring from main.py ──────────────────────────────────────────────────
    def inject(self, **refs):
        """Attributes every Viewer needs (other tabs, the main tab widget)."""
        self._refs.update(refs)
        for page in self._pages():
            if page.viewer is not None:
                self._apply_refs(page.viewer)

    def _apply_refs(self, viewer):
        for k, v in self._refs.items():
            setattr(viewer, k, v)
        viewer._layout_tabs = self

    def set_outer_visible(self, visible: bool):
        """The main window switched to / away from the Image Slider tab."""
        self._outer_visible = bool(visible)
        self._sync_chrome()
        if visible:
            self._ensure_started(self._current_page())
        self._apply_activity()

    # ── pages ────────────────────────────────────────────────────────────────
    def _pages(self) -> list:
        return [self._tabs.widget(i) for i in range(self._tabs.count())]

    def _current_page(self) -> "_TabPage | None":
        w = self._tabs.currentWidget()
        return w if isinstance(w, _TabPage) else None

    def _next_name(self) -> str:
        used = {self._tabs.tabText(i) for i in range(self._tabs.count())}
        n = 1
        while f"Panel {n}" in used:
            n += 1
        return f"Panel {n}"

    def _add_page(self, name: str, state: "dict | None") -> _TabPage:
        page = _TabPage(state)
        idx = self._tabs.addTab(page, name)
        self._tabs.setTabToolTip(idx, "Double-click to rename")
        btn = QToolButton()
        btn.setIcon(self._close_icon)
        btn.setIconSize(QSize(12, 12))
        btn.setFixedSize(18, 18)
        btn.setStyleSheet(_BTN_QSS)
        btn.setToolTip("Close this layout tab")
        btn.clicked.connect(lambda _=False, p=page: self._close_page(p))
        self._tabs.tabBar().setTabButton(idx, QTabBar.ButtonPosition.RightSide, btn)
        self._sync_close_buttons()
        self._tabs.tabBar().updateGeometry()
        return page

    def _sync_close_buttons(self):
        """The last tab cannot be closed — there must always be a Slider."""
        only = self._tabs.count() <= 1
        bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            b = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if b is not None:
                b.setVisible(not only)
        bar.updateGeometry()

    def _build_viewer(self, page: _TabPage):
        if page.viewer is not None:
            return page.viewer
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            v = self._viewer_cls()
        finally:
            QApplication.restoreOverrideCursor()
        v.setWindowTitle("")
        self._apply_refs(v)
        page.layout().addWidget(v)
        page.viewer = v
        if page.state:
            v.set_pending_tab_state(page.state)
        page.state = None
        return v

    def _ensure_started(self, page: "_TabPage | None"):
        """First time a tab is on screen: build its Viewer and load what it had —
        or, for a tab with nothing to load, open the Time window dialog as the
        Slider always did on its first activation."""
        if page is None or page.started:
            return
        page.started = True
        v = self._build_viewer(page)

        def _go():
            try:
                # A handoff from another tab can open a new tab in between; then this
                # one is hidden again and must not pop a dialog or scan out of sight.
                if self._current_page() is not page or not self._outer_visible:
                    page.started = False
                    return
                if not v.start_from_tab_state():
                    v.auto_start_online()
            except RuntimeError:
                pass          # tab closed before the queued start ran
        QTimer.singleShot(0, _go)

    def new_tab(self, name: "str | None" = None, start: bool = False):
        """Create a tab, make it current and return its Viewer. `start` opens the
        Time window dialog in it; handoffs pass False and load their own data."""
        page = self._add_page(name or self._next_name(), None)
        v = self._build_viewer(page)
        page.started = True
        self._tabs.setCurrentWidget(page)
        if start:
            QTimer.singleShot(0, v.auto_start_online)
        self.save()
        return v

    def _on_new_clicked(self):
        self.new_tab(start=True)

    def _close_page(self, page: _TabPage):
        if self._tabs.count() <= 1:
            return
        idx = self._tabs.indexOf(page)
        if idx < 0:
            return
        v = page.viewer
        if v is not None:
            try:
                v._pending_online_mode = False
                v._stop_online_mode()
                v.cancel_scan()
                v.stop()
                v._proxy_cancel(drop=True)
            except Exception:
                pass
        if page in self._mru:
            self._mru.remove(page)
        self._tabs.removeTab(idx)
        self._sync_close_buttons()
        page.deleteLater()
        self.save()

    # ── switching ────────────────────────────────────────────────────────────
    def _on_current_changed(self, _idx: int):
        if self._rename_edit is not None:
            self._finish_rename()
        if self._loading:
            return
        if self._outer_visible:
            self._ensure_started(self._current_page())
        self._apply_activity()
        self.save()

    def _apply_activity(self):
        """Exactly one Viewer runs: the current tab's, and only while the Image
        Slider is the main window's tab on screen. The rest are suspended."""
        cur = self._current_page()
        if cur is not None:
            if cur in self._mru:
                self._mru.remove(cur)
            self._mru.insert(0, cur)
        for page in self._pages():
            v = page.viewer
            if v is None:
                continue
            if page is cur and self._outer_visible:
                v.resume()
            else:
                v.suspend()
        kept = 0
        for page in self._mru:
            if page is cur or page.viewer is None:
                continue
            if kept < PREVIEW_KEEP_HIDDEN:
                kept += 1
            else:
                page.viewer.drop_preview()

    def set_chrome_visible(self, show: bool):
        """Watcher mode (Ctrl+F11) hides the tab bar so the cameras fill the screen."""
        self._chrome_wanted = bool(show)
        self._sync_chrome()

    def _sync_chrome(self):
        """The layout tab bar shows only while the Image Slider is the main tab on
        screen and watcher mode is off."""
        self._tabs.chrome.setVisible(self._outer_visible
                                     and getattr(self, "_chrome_wanted", True))

    def move_chrome_into(self, host_layout):
        """Put the tab bar + "+" into the main window's tab row (main.py), right
        after the main tabs. Shown only while this tab is on screen. Added to the
        new layout first, so it is never a parentless window for a moment."""
        chrome = self._tabs.chrome
        self._tabs.layout().removeWidget(chrome)
        host_layout.addWidget(chrome)
        self._sync_chrome()

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
        self._saved_current = int(d.get("current") or 0)
        return [t for t in (d.get("tabs") or []) if isinstance(t, dict)]

    def _page_state(self, page: _TabPage) -> dict:
        if page.viewer is not None:
            try:
                return page.viewer.export_tab_state()
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

    # ── the Slider's public API, as other tabs call it ───────────────────────
    def active(self):
        """The Viewer of the current tab (built if it was not yet)."""
        page = self._current_page()
        return self._build_viewer(page) if page is not None else None

    def _active_cameras(self) -> list:
        page = self._current_page()
        if page is None:
            return []
        if page.viewer is not None:
            return list(page.viewer.last_pick_cam_names or [])
        return list((page.state or {}).get("cameras") or [])

    def _drop_tab_of(self, viewer):
        for page in self._pages():
            if page.viewer is viewer:
                self._close_page(page)
                return

    def open_moment(self, ts_ns: int, cam_names=None, pad_minutes=None) -> bool:
        cams = [c for c in (cam_names or []) if c] or self._active_cameras()
        if not cams:
            return False
        when = datetime.fromtimestamp(int(ts_ns) / 1e9).strftime("%d.%m %H:%M:%S")
        v = self.new_tab(f"Moment {when}")
        ok = v.open_moment(ts_ns, cams, pad_minutes)
        if not ok:
            self._drop_tab_of(v)
        return ok

    def receive_external_folder(self, folder, energy_map=None, discrete: bool = True,
                                cam_name=None) -> bool:
        folder = Path(folder)
        if not folder.is_dir():
            return False
        v = self.new_tab(cam_name or folder.name)
        ok = v.receive_external_folder(folder, energy_map=energy_map,
                                       discrete=discrete, cam_name=cam_name)
        if not ok:
            self._drop_tab_of(v)
        return ok

    def open_folder_path(self, folder):
        folder = Path(folder)
        v = self.new_tab(folder.name)
        v.open_folder_path(folder)

    def cancel_scan(self):
        for page in self._pages():
            if page.viewer is not None:
                page.viewer.cancel_scan()

    def stop(self):
        for page in self._pages():
            if page.viewer is not None:
                page.viewer.stop()
