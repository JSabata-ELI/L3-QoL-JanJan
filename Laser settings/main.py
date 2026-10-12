"""Laser settings — is the machine set right for the hall we are shooting into?

Run:  python main.py

One window, three tabs over one list of channels:

    Overview   the five halls as big squares, and every watched channel with a
               tick or a cross beside it
    Limits     one hall at a time: its gate valve channel, and the min/max that
               count as right for it
    DM settings  which saved set-up the deformable mirror is on (`dm_t`)
    DM writer    the switch that makes this PC the one that reads the mirror
                 off the Remote Desktop window — OPR1 (`dw_t`, `ls_dm`)
    Settings   where the five squares sit and how big they are, and how the
               first tab's list is written

There is a fourth tab in the code, Info / Log, and it is NOT on the window. It
is built and kept alive — `log()` writes every line into it and it is still the
one place the whole state is spelled out in sentences — but it is never added to
the tab bar. Asked for on 1.10.2026: "info / log smaž, nebo minimálně
zneviditelni; v kódu to můžeš nechat, ale tahle záložka tam už nebude". To put
it back, hand `_add_tab` the three names the way the others are handed them.

This file owns only what belongs to the whole program: the taskbar identity, the
look, the tabs, the settings file, the reading clock and shutting down. What a
reading MEANS lives in `ls_core`; the tabs only draw it.
"""

import sys
from pathlib import Path

# The frozen build carries its libraries in _internal next to the exe. A user
# site-packages folder that happens to hold an older PySide6 would otherwise
# win, and the program would fail in a way that looks like a bug in the program.
# First thing in the file, before any Qt import.
if getattr(sys, "frozen", False):
    _internal = Path(sys.executable).resolve().parent / "_internal"
    sys.path = [p for p in sys.path if "site-packages" not in p.lower()]
    if str(_internal) not in sys.path:
        sys.path.insert(0, str(_internal))

# A frozen windowed build has no stdout at all, and a cp1250 console raises on
# the arrows and ellipses this program prints. Neither may take it down.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Before Qt is imported, so that a death inside Qt itself is still written down.
# An access violation is not a Python exception and leaves no traceback.
import ls_log                                                   # noqa: E402

ls_log.install()

import re                                                       # noqa: E402
import time                                                     # noqa: E402

from PySide6.QtCore import Qt, QTimer                           # noqa: E402
from PySide6.QtGui import QIcon                                 # noqa: E402
from PySide6.QtWidgets import (QApplication, QLabel,  # noqa: E402
                               QMainWindow, QTabWidget, QVBoxLayout,
                               QWidget)

import ls_core as C                                             # noqa: E402
import ls_ui as U                                               # noqa: E402
from ls_engine import PollEngine                                # noqa: E402

_VER_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)


def _detect_version():
    """The version out of the running file's own name, as the siblings do."""
    try:
        name = (Path(sys.executable).name if getattr(sys, "frozen", False)
                else Path(__file__).name)
        m = _VER_RE.search(name)
        if m:
            return f"v{m.group(1)}.{m.group(2)}.{m.group(3)}"
    except Exception:
        pass
    return ""


APP_VERSION = _detect_version()
APP_TITLE = f"Laser settings {APP_VERSION}".strip()


def _icon_file():
    """icon.ico next to the exe when frozen, next to this file otherwise."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    else:
        cands.append(Path(__file__).resolve().parent / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


def _icon_app_id(prefix, ico_path):
    """A taskbar identity that is new on every launch.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so every *stable* id tried in this family of programs eventually picked
    up a bad cache entry and then drew the blank window placeholder for good: a
    fixed string, a hash of the icon, and a hash tagged with the build's file
    name each broke within days. Setting no id at all is no better — Windows
    then keys the button on the exe path and caches the picture there instead.

    An id Windows has never seen has no cache entry, so the button falls back to
    the window icon, which every program here sets itself. A random suffix per
    launch makes every run a first-time id, which is why this is the one form
    that cannot go stale. Nothing here needs a stable identity: no program
    registers a shortcut, pins itself or sends Windows toasts. The one cost is
    pinning a *running* taskbar button — that pin would carry this run's id and
    would not start the program again, so pin the exe instead.

    Returns None when there is no icon at all; the caller then sets no id and
    the button keeps taking the exe's own picture.
    """
    import os.path
    if not ico_path or not os.path.exists(str(ico_path)):
        return None
    import uuid
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


# Note: do NOT add a WM_SETICON / SetClassLongPtr "force taskbar icon" helper
# here. Measured on Win11 with the window icon and the window-class icon set to
# two different pictures, the taskbar draws the WINDOW icon, so setWindowIcon is
# already enough. That helper is only needed for the Tk programs.


class LaserSettingsWindow(QMainWindow):
    """The one window: the tabs, the settings file and the reading clock."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        ico = _icon_file()
        if ico:
            self.setWindowIcon(QIcon(str(ico)))
        # NO MINIMUM SIZE. Asked for on 1.10.2026 — "úplně bez minima" — and
        # 820 x 560 was what stood in the way. Qt still honours whatever the
        # tabs themselves demand, so this one line is not the whole job: the
        # band on the Overview had to stop being a `setFixedHeight`
        # (`ov_t._HallBand`), the Limits tab had to go into a scroller, and the
        # two widest boxes on it had to stop claiming a minimum width. Put a
        # minimum back here and all of that is wasted.
        self.setMinimumSize(0, 0)

        self._cfg, problem = C.load_config()
        self._first_problem = problem

        # Everything said before the Info / Log tab exists. The settings path
        # and a bad settings file are both reported in here, and the tab is
        # built afterwards — without this they would be said to nobody.
        self._said = []

        # Saves are coalesced: typing a limit changes a cell on every keystroke
        # and the whole document would be rewritten each time. One second after
        # the last change — and never on close, because a program is not
        # reliably told it is quitting.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._save_now)
        # Re-maximise once a burst of edits in the Maximise boxes has settled,
        # not once per wheel notch — each one restores and maximises the whole
        # window, which is a flash. See `window_max_changed`.
        self._remax_timer = QTimer(self)
        self._remax_timer.setSingleShot(True)
        self._remax_timer.setInterval(400)
        self._remax_timer.timeout.connect(self._remaximise)

        # The shot counter: one for the whole window, so the Overview and the
        # Limits tab show the same number. Fed in `_on_pass`.
        self._shots = C.ShotCounter(C.shots_of(self._cfg)["reset_s"])

        self._engine = PollEngine(self.channels, self,
                                  want_screen=self._screen_wanted,
                                  want_dm=self._dm_wanted)
        self._engine.updated.connect(self._on_pass)
        self._engine.logged.connect(self.log)

        self._build_ui()
        self._restore_geometry()

        if problem:
            self.log(problem)
        self.log(f"Settings: {C.config_path()}")
        self._engine.start()

    # ── the window ─────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.setDocumentMode(True)
        root.addWidget(self._tabs, stretch=1)

        # No grey line along the bottom any more. What it said is on the
        # Info / Log tab, which is the last one, the way the sibling programs
        # keep their Log tab last.
        self.setCentralWidget(central)

        # The Info / Log page is built FIRST and never shown. `log()` writes
        # into it and the other tabs' failures are reported through `log()`, so
        # it has to exist before any of them is built or the first thing said
        # would be said to nobody.
        self._info = self._add_tab("in_t", "InfoTab", "Info / Log", shown=False)
        self._overview = self._add_tab("ov_t", "OverviewTab", "Overview")
        self._settings = self._add_tab("st_t", "SettingsTab", "Limits")
        # The deformable mirror: which set-up it is on (every PC), and the
        # switch that makes this PC the one that reads it (OPR1). See `ls_dm`.
        self._dm_tab = self._add_tab("dm_t", "DmTab", "DM settings")
        self._dm_writer = self._add_tab("dw_t", "DmWriterTab", "DM writer")
        self._font = self._add_tab("ft_t", "FontTab", "Settings")
        if self._info is not None:
            for line in self._said:
                self._info.append_line(line)
            self._said = []

    def _add_tab(self, module_name, class_name, title, shown=True):
        """Each tab on its own: a failure degrades to a red page, not a crash.

        `shown=False` builds the page and keeps it working without putting it on
        the tab bar. It is HIDDEN explicitly: a QWidget whose parent is the main
        window but which is in no layout would otherwise paint itself over the
        top left corner of the window at its own size.
        """
        try:
            mod = __import__(module_name)
            widget = getattr(mod, class_name)(self)
            if shown:
                self._tabs.addTab(widget, title)
            else:
                widget.setParent(self)
                widget.hide()
            return widget
        except Exception as exc:
            import traceback
            ls_log.note(f"{title} tab failed: {traceback.format_exc()}")
            if not shown:
                return None
            err = QLabel(f"The {title} tab could not be built:\n\n{exc}")
            err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err.setWordWrap(True)
            err.setStyleSheet("QLabel { color: #8d1c12; background: #fdecea;"
                              " font-size: 13px; padding: 20px; }")
            self._tabs.addTab(err, f"{title} (error)")
            return None

    def _restore_geometry(self):
        geom = self._cfg.get("window_geometry")
        if isinstance(geom, list) and len(geom) == 4:
            try:
                self.setGeometry(*[int(v) for v in geom])
            except Exception:
                pass

    # ── what the tabs ask ──────────────────────────────────────────────────
    def rows(self):
        """The one list of watched channels. Rows are appended to this list."""
        return self._cfg["rows"]

    def shooting(self):
        """{hall: its gate valve} — the two position channels and the rule.

        Handed out as the live dictionary: `ls_core.gate_for` fills a hall in
        as it is first touched, and the Settings tab edits the entries in
        place.
        """
        return self._cfg["shooting"]

    def look(self):
        """How the first tab's list is drawn: the live dictionary.

        Handed out the way `shooting()` is — the Settings tab edits it in place
        and the Overview reads it on every reload. `ls_core.look_of` fills it in
        and repairs it, so a hand-edited settings file cannot make the list
        unreadable.
        """
        return C.look_of(self._cfg)

    def halls(self):
        """The band of five squares: its height, the word in them, the layout.

        The live dictionary again, for the same reason `look()` is: the Settings
        tab's board writes the rectangles into it as they are dragged and the
        Overview places the squares from it on the next reload.
        """
        return C.halls_of(self._cfg)

    def scanning(self):
        """The Overview's Scanning box: every row with a scan limit is judged
        against its scan pair while it is ticked (`ls_core.limits_in_force`)."""
        return bool(self._cfg.get("scanning", False))

    def set_scanning(self, on):
        """Tick or untick Scanning: repaint every verdict now, and save."""
        on = bool(on)
        if on != self.scanning():
            self._cfg["scanning"] = on
            self.settings_changed()

    def shots(self):
        """The shot counter (`ls_core.ShotCounter`), shared by every tab."""
        return self._shots

    def shot_settings(self):
        """The counter's settings: the live dictionary, `ls_core.shots_of`."""
        return C.shots_of(self._cfg)

    def shot_reset_changed(self):
        """The Limits tab changed the reset time: use it now, and save."""
        self._shots.reset_s = float(C.shots_of(self._cfg)["reset_s"])
        self.save_soon()
        self._on_pass()

    def shot_look_changed(self):
        """The Limits tab changed the counter line's size or Bold: restyle the
        Overview in place. Never the Limits tab — the box being typed into lives
        in its table, and a rebuild would delete it under the operator."""
        self.look_changed()

    def look_changed(self):
        """The LOOK changed (Settings tab boxes, the counter line's size): the
        Overview repaints its existing rows. Nothing is rebuilt and no other tab
        is touched — the Limits table has nothing that depends on the look."""
        self.save_soon()
        if self._overview is not None:
            try:
                self._overview.restyle()
            except Exception as exc:
                self.log(f"The Overview could not be refreshed: {exc}")

    def halls_arranged(self):
        """A square was dragged on the Settings board: move the Overview's
        squares, nothing else. Called on every mouse move of a drag."""
        self.save_soon()
        if self._overview is not None:
            try:
                self._overview.rearrange()
            except Exception as exc:
                self.log(f"The Overview could not be refreshed: {exc}")

    def rows_changed(self):
        """A row was shown or hidden: the Overview's list and the Limits table
        are built again — the two places whose ROWS depend on it — in one
        paint each. The Settings and Info tabs do not."""
        self.save_soon()
        for tab in (self._overview, self._settings):
            if tab is None:
                continue
            tab.setUpdatesEnabled(False)
            try:
                tab.reload()
            except Exception as exc:
                self.log(f"A tab could not be refreshed: {exc}")
            finally:
                tab.setUpdatesEnabled(True)

    def window_max(self):
        """What Maximise does: the live dictionary, `ls_core.window_max_of`."""
        return C.window_max_of(self._cfg)

    def dm_settings(self):
        """Whether this PC reads the mirror: the live dictionary, `dm_of`."""
        return C.dm_of(self._cfg)

    def dm_changed(self):
        """The DM writer tab changed it: save, and look at once."""
        self.save_soon()
        self._engine.dm_edit(lambda setups: (None, None))

    def _dm_wanted(self):
        dm = C.dm_of(self._cfg)
        return dm["write"], dm["title"], dm["every_s"]

    def window_max_changed(self):
        """The Settings tab changed it: save, and re-maximise if maximised now,
        so the new size is seen without another click — once the edits settle,
        never per wheel notch or keystroke."""
        self.save_soon()
        if self.isMaximized():
            self._remax_timer.start()

    def _remaximise(self):
        if self.isMaximized():
            self.showNormal()
            self.showMaximized()

    # ── Maximise to the size the operator set ──────────────────────────────
    # Windows asks a window how big it may be maximised (WM_GETMINMAXINFO)
    # every time Maximise is clicked. Answered here, the window really IS
    # maximised — the title bar shows the restore button and Restore goes back
    # to the size it had — it is just smaller than the screen, at the place
    # remembered with it (`x`, `y` from the free area's top left corner).
    #
    # Windows works in physical pixels and the setting is in Qt's, so it is
    # multiplied by the screen's scale. A maximised window's invisible resize
    # border hangs off the screen (that is what the default position's negative
    # x and y are), so it is added back on — the setting is the window you SEE.
    def nativeEvent(self, event_type, message):
        try:
            if event_type == b"windows_generic_MSG" and self._apply_max(message):
                return True, 0
        except Exception as exc:
            # A Qt override must not raise: it would end the program somewhere
            # nobody could trace it. Normal maximise is what happens instead.
            ls_log.note(f"the Maximise size could not be applied: {exc}")
        return super().nativeEvent(event_type, message)

    def _apply_max(self, message):
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x0024:               # WM_GETMINMAXINFO
            return False
        wm = C.window_max_of(self._cfg)
        if not wm["on"]:
            return False

        class _MINMAXINFO(ctypes.Structure):
            _fields_ = [("ptReserved", wintypes.POINT),
                        ("ptMaxSize", wintypes.POINT),
                        ("ptMaxPosition", wintypes.POINT),
                        ("ptMinTrackSize", wintypes.POINT),
                        ("ptMaxTrackSize", wintypes.POINT)]

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        info = _MINMAXINFO.from_address(msg.lParam)
        # The border that hangs off a maximised window, from Windows' own
        # default position for it.
        bx = max(0, -int(info.ptMaxPosition.x))
        by = max(0, -int(info.ptMaxPosition.y))
        work_w, work_h = int(info.ptMaxSize.x), int(info.ptMaxSize.y)
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        mon = user32.MonitorFromWindow(msg.hWnd, 2)     # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            work_w = mi.rcWork.right - mi.rcWork.left + 2 * bx
            work_h = mi.rcWork.bottom - mi.rcWork.top + 2 * by
        # The scale of the monitor the window is on, from Windows itself: Qt's
        # own ratio can still be the previous screen's in the middle of a move.
        try:
            dpr = user32.GetDpiForWindow(msg.hWnd) / 96.0
        except Exception:
            dpr = 0.0
        if dpr <= 0:
            dpr = float(self.devicePixelRatioF() or 1.0)
        x, y, w, h = C.max_rect(int(round(wm["x"] * dpr)),
                                int(round(wm["y"] * dpr)),
                                int(round(wm["w"] * dpr)) + 2 * bx,
                                int(round(wm["h"] * dpr)) + 2 * by,
                                work_w, work_h)
        info.ptMaxSize.x, info.ptMaxSize.y = w, h
        # Windows' default position IS the work area's top left corner, border
        # hanging off; the remembered place is counted from there.
        info.ptMaxPosition.x = int(info.ptMaxPosition.x) + x
        info.ptMaxPosition.y = int(info.ptMaxPosition.y) + y
        info.ptMaxTrackSize.x = max(int(info.ptMaxTrackSize.x), w)
        info.ptMaxTrackSize.y = max(int(info.ptMaxTrackSize.y), h)
        return True

    def active_hall(self):
        return self._cfg.get("active_hall") or C.HALLS[0]

    def set_active_hall(self, hall):
        if hall in C.HALLS:
            self._cfg["active_hall"] = hall
            self.save_soon()

    def engine(self):
        return self._engine

    def channels(self):
        """Every channel one pass has to read: the gate valves, then the rows.

        The valves first on purpose. With a long list and a slow archiver the
        deadline can cut a pass short, and the five squares are what the program
        is for — they must be the last thing to go missing.

        One channel per valve, the open switch. What the last pass managed is
        handed in so that a hall whose open switch cannot be read adds its
        closed switch to the next pass and drops it again as soon as the open
        one comes back — see `ls_core.gate_channels`.

        A formula row reads nothing: its `pv` is empty and the rows it works
        from are on the list in their own right. `is_formula_row`, not
        `is_formula` — a formula whose box is still empty must not be asked for
        either, and the shape that made this matter is in `ls_core`.
        """
        out = []
        readings = self._engine.readings()
        last_ok = self._engine.last_ok()
        now = time.time()
        for hall in C.HALLS:
            out += C.gate_channels(self._cfg["shooting"], hall,
                                   readings=readings, last_ok=last_ok,
                                   now_s=now)
        # SBW4's energy under both its names, straight after the valves: it is
        # what says whether anything is being fired (`ls_core.firing`), so it
        # is as much a part of the squares as the valves are.
        out += list(C.FIRING_CHANNELS)
        # The SAST1 shutter, only while the DM settings line is on the
        # Overview: it is what says low power is being shot (`dm_judge`).
        if any(C.is_dm_row(r) and C.is_shown(r) for r in self._cfg["rows"]):
            out.append(C.DM_SHUTTER_CHANNEL)
        out += [r.get("pv", "") for r in self._cfg["rows"]
                if not C.is_formula_row(r) and not C.is_builtin_row(r)]
        # Once each: a row may watch one of the energy names as well.
        return list(dict.fromkeys(out))

    def _screen_wanted(self):
        """Look at the L3BT MSS window only while a valve's channels are silent."""
        return C.screen_wanted(self._cfg["shooting"], self._engine.readings(),
                               self._engine.last_ok(), time.time())

    def has_channels(self):
        return any((c or "").strip() for c in self.channels())

    def settings_changed(self, rebuild=False):
        """Something was edited: repaint, and write the file shortly.

        `rebuild` when the LIST changed rather than a value in it — a row added
        or removed means both tables have to be built again, not just refilled.
        """
        self.save_soon()
        if rebuild:
            for tab in (self._overview, self._settings, self._info,
                        self._font):
                if tab is None:
                    continue
                try:
                    tab.reload()
                except Exception as exc:
                    self.log(f"A tab could not be refreshed: {exc}")
        else:
            self._on_pass()

    def fit_parked(self):
        """The fit has worked the sizes out: put them in the Settings boxes.

        Called by the Overview, and it does ONE thing — it does not rebuild and
        it does not save. A `settings_changed(rebuild=True)` from here would
        call the Overview's own `reload()`, which runs the fit again, which
        calls this again: the loop that made the first attempt at this
        unworkable. And the parked numbers are not worth saving — they are
        worked out from the window every time the window changes, and writing
        them on every pixel of a drag is how a freak size gets persisted.
        """
        tab = getattr(self, "_font", None)
        if tab is None:
            return
        try:
            tab.park()
        except Exception as exc:
            ls_log.note(f"the fit could not reach the Settings tab: {exc}")

    def log(self, message, hint=None):          # noqa: ARG002 — see below
        """One line on the Info / Log tab, and one in the crash log.

        The tab holds everything this run has said, in order; the file holds
        every run, which is where a failure that scrolled past is read back
        from. `hint` is taken for the sake of the callers that pass one — a line
        in a list has nothing to hang a tooltip on.
        """
        text = str(message)
        info = getattr(self, "_info", None)
        if info is not None:
            try:
                info.append_line(text)
            except Exception:
                pass
        else:
            self._said.append(text)
        ls_log.note(text)

    # ── the clock ──────────────────────────────────────────────────────────
    def _on_pass(self):
        # The counter first, so every tab paints the same number. Counting
        # twice on the same readings is harmless: a sample is new only once.
        try:
            self._shots.update(self._engine.readings(),
                               C.shot_channels(self._cfg["rows"]),
                               time.monotonic())
        except Exception as exc:
            ls_log.note(f"the shot counter failed: {exc}")
        for tab in (self._overview, self._settings, self._info, self._font,
                    self._dm_tab, self._dm_writer):
            if tab is None:
                continue
            try:
                tab.refresh()
            except Exception as exc:
                ls_log.note(f"refresh failed: {exc}")

    # ── the settings file ──────────────────────────────────────────────────
    def save_soon(self):
        self._save_timer.start()

    def _save_now(self):
        # The size it goes back to, not the maximised one: saving the maximised
        # rectangle made the next start open as a big NORMAL window.
        geom = (self.normalGeometry() if self.isMaximized()
                else self.geometry())
        self._cfg["window_geometry"] = [geom.x(), geom.y(),
                                        geom.width(), geom.height()]
        problem = C.save_config(self._cfg)
        if problem:
            self.log(problem)

    # ── shutting down ──────────────────────────────────────────────────────
    def closeEvent(self, ev):
        ls_log.note("the window was closed — shutting down")
        ls_log.note(self._engine.stats_line())
        for step in (self._save_now, self._engine.shutdown):
            try:
                step()
            except Exception:
                pass
        super().closeEvent(ev)


def main():
    # Qt's own warnings into the same file. "QBackingStore::endPaint() called
    # with active painter" and "Signal source has been deleted" are Qt naming
    # exactly the class of bug that kills these programs, and they used to go
    # nowhere.
    ls_log.install_qt_handler()

    aumid = _icon_app_id("ELIBeamlines.LaserSettings", _icon_file())
    if aumid:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        except Exception:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    ico = _icon_file()
    if ico:
        try:
            app.setWindowIcon(QIcon(str(ico)))
        except Exception:
            pass
    U.install_app_look(app)

    win = LaserSettingsWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
