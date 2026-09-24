"""Announcer — tells you the moment something stops being true.

Run:  python main.py

One window, four tabs over one list of watched things:

    Watch    everything being watched, with its reading and its verdict
    Values   the archived numbers and their limits
    Areas    the rectangles of the screen that must keep looking the same
    Alarm    what the alarm looks and sounds like, and where it appears

This file owns only the things that belong to the whole program: the taskbar
identity, the look, the header row with the circle, the tabs, the settings file
and shutting down. Every verdict lives in `ann_watch` / `ann_core`; every tab
renders from there.
"""

import sys
from pathlib import Path

# The frozen build carries its libraries in _internal next to the exe. A user
# site-packages folder that happens to hold an older PySide6 or Pillow would
# otherwise win, and the program would fail in a way that looks like a bug in
# the program. First thing in the file, before any Qt import.
if getattr(sys, "frozen", False):
    _internal = Path(sys.executable).resolve().parent / "_internal"
    sys.path = [p for p in sys.path if "site-packages" not in p.lower()]
    if str(_internal) not in sys.path:
        sys.path.insert(0, str(_internal))

# A frozen windowed build has no stdout at all, and a cp1250 console raises on
# the arrows and degree signs this program prints. Neither may take it down.
if getattr(sys, "frozen", False) or True:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Before Qt is imported, so that a death inside Qt itself is still written down.
# An access violation is not a Python exception and leaves no traceback; without
# this the only record of a crash was the Windows event log. See `ann_log`.
import ann_log                                                  # noqa: E402

ann_log.install()

import re                                                       # noqa: E402

from PySide6.QtCore import Qt, QTimer                           # noqa: E402
from PySide6.QtGui import QIcon                                 # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout,  # noqa: E402
                               QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QVBoxLayout, QWidget)

import ann_core as C                                            # noqa: E402
import ann_presets as P                                         # noqa: E402
import ann_screen as S                                          # noqa: E402
import ann_ui as U                                              # noqa: E402
from ann_watch import WatchEngine                               # noqa: E402

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
APP_TITLE = f"Announcer {APP_VERSION}".strip()


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
    it, so every *stable* id tried here eventually picked up a bad cache entry
    and then drew the blank window placeholder for good: a fixed string, a hash
    of the icon, and a hash tagged with the build's file name each broke within
    days. Setting no id at all was no better -- Windows then keys the button on
    the exe path and caches the picture there instead (Diagnostic v1.3.1,
    measured 2026-09-17: the window icon, the exe's own icon resource and the
    shell's own file icon all correct, the taskbar button blank).

    An id Windows has never seen has no cache entry, so the button falls back
    to the window icon, which every program here sets itself -- measured on a
    fresh id on 2026-09-04 and again on 2026-09-17. A random suffix per launch
    makes every run a first-time id, which is why this is the one form that
    cannot go stale. Nothing here needs a stable identity: no program registers
    a shortcut, pins itself or sends Windows toasts. The one cost is pinning a
    *running* taskbar button -- that pin would carry this run's id and would
    not start the program again, so pin the exe instead.

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


class AnnouncerWindow(QMainWindow):
    """The one window: the header, the tabs, the settings and the engine."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        ico = _icon_file()
        if ico:
            self.setWindowIcon(QIcon(str(ico)))
        # Only a floor small enough to be out of the way: what the window can
        # really shrink to is decided by the button row at the top, which knows
        # its own smallest size. 980x640 was a guess and it stopped the window
        # being made any narrower than the table happened to be.
        self.setMinimumSize(640, 420)

        # ── the settings ───────────────────────────────────────────────────
        self._cfg, problem = C.load_config()
        was_migrated = isinstance(self._cfg.get("items"), list)
        self._items, notes = C.migrate(self._cfg)
        self._first_problem = problem
        self._first_notes = notes
        # A brand new installation starts with the standard values rather than
        # an empty table, so there is something to look at and edit.
        if not self._items:
            self._items = C.default_value_items()
            self._first_notes = ["Started with the standard machine values."]

        # The set of alarms being worked in. A preset that is no longer in the
        # file falls back to all of them — never to an empty screen, which is
        # indistinguishable from "everything was lost".
        self._preset = self._cfg.get("active_preset") or C.PRESET_ALL
        if self._preset not in (C.PRESET_ALL, C.PRESET_UNASSIGNED) \
                and self._preset not in C.alarm_presets(self._cfg):
            self._first_notes.append(
                f'The preset "{self._preset}" is gone — showing all alarms.')
            self._preset = C.PRESET_ALL

        # Saves are coalesced: the settings hold the reference pictures, so
        # rewriting them on every single click would be a lot of bytes for a
        # tick box. One second after the last change, and never on close — a
        # program is not reliably told it is quitting.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._save_now)

        # ── the engine ─────────────────────────────────────────────────────
        # The engine sees only the chosen preset. That is what makes a preset
        # mean something: the other alarms are not merely hidden, they are not
        # read from the archiver and cannot fire.
        self._engine = WatchEngine(self.active_items, self)
        self._engine.watching_changed.connect(self._on_watching_changed)
        self._engine.changed.connect(self._update_light)
        self._engine.fired.connect(self._on_fired)

        self._alarm = None          # made on first use
        self._hud = None
        # An alarm is up: the flash is on screen and the circle has to stay with
        # it. Watching has already stopped by then, and stopping normally puts
        # the tabbed window back — but not while an alarm is being looked at.
        self._alarm_up = False

        self._build_ui()
        self._monitors = S.MonitorNumbers()

        for line in self._first_notes:
            self._watch.log(line)
        if problem:
            self._watch.log(problem)

        # Write the migrated list out straight away, on a first run only.
        # Without this the settings file holds no record of what is being
        # watched until the operator happens to change something, the migration
        # notes are announced again at every start, and a row deleted before any
        # other edit comes back from the defaults.
        if not was_migrated:
            self.save_soon()

        self._update_light()

    # ── the window ─────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 0)
        root.setSpacing(6)
        root.addLayout(self._build_header())

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.setDocumentMode(True)
        root.addWidget(self._tabs, stretch=1)
        self.setCentralWidget(central)

        from wa_t import WatchTab
        self._watch = WatchTab(self._engine, on_edit=self._edited)
        self._tabs.addTab(self._watch, "Watch")

        # Each editing tab is loaded on its own and a failure degrades to a red
        # label rather than taking the program with it. The Watch tab is the one
        # that matters, and it is already up.
        self._values = self._add_tab("vl_t", "ValuesTab", "Values")
        self._areas = self._add_tab("ar_t", "AreasTab", "Areas")
        self._alarm_tab = self._add_tab("al_t", "AlarmTab", "Alarm")

    def _add_tab(self, module_name, class_name, title):
        try:
            mod = __import__(module_name)
            widget = getattr(mod, class_name)(self)
            self._tabs.addTab(widget, title)
            return widget
        except Exception as exc:
            import traceback
            err = QLabel(f"The {title} tab could not be built:\n\n{exc}")
            err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err.setWordWrap(True)
            err.setStyleSheet("QLabel { color: #8d1c12; background: #fdecea;"
                              " font-size: 13px; padding: 20px; }")
            self._tabs.addTab(err, f"{title} (error)")
            try:
                self._watch.log(f"The {title} tab could not be built: {exc}")
                self._watch.log(traceback.format_exc().replace("\n", " | "))
            except Exception:
                pass
            return None

    def _build_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        self._light = U.StatusLight(28)
        self._light.clicked.connect(self._toggle_watching)
        row.addWidget(self._light)

        # The one thing said about readings that are not arriving: a red
        # exclamation mark beside the circle, after five minutes of it. No
        # words, here or on the panel over his work — see `C.NO_DATA_S`.
        self._mark = U.AlertMark(32)      # bigger than the circle, on purpose
        self._mark.hide()
        row.addWidget(self._mark)

        self._btn_watch = U.button("Start watching", icon_name="play",
                                   primary=True,
                                   tip="Arm the alarm. The readings are taken "
                                       "either way; this decides whether a "
                                       "verdict raises anything.")
        self._btn_watch.clicked.connect(self._toggle_watching)
        row.addWidget(self._btn_watch)

        self._btn_reset = U.button("Reset", icon_name="refresh",
                                   tip="Forget that anything fired and arm "
                                       "again. If the thing is still wrong it "
                                       "fires straight away, which is the "
                                       "honest answer.")
        self._btn_reset.clicked.connect(self._reset)
        row.addWidget(self._btn_reset)

        self._btn_monitors = U.button("Identify monitors", icon_name="monitor",
                                      tip="Put a big number on each screen for "
                                          "a couple of seconds.")
        self._btn_monitors.clicked.connect(lambda: self._monitors.show())
        row.addWidget(self._btn_monitors)

        self._btn_test = U.button("Test the alarm", icon_name="bell",
                                  tip="Flash and sound the alarm now, without "
                                      "waiting for anything to go wrong.")
        self._btn_test.clicked.connect(self._test_alarm)
        row.addWidget(self._btn_test)

        row.addSpacing(12)
        row.addWidget(QLabel("Presets"))
        self._preset_box = QComboBox()
        self._preset_box.setMinimumWidth(170)
        self._preset_box.setToolTip(
            "The set of alarms to work in. Only the chosen set is listed and "
            "only it is watched.\nUnassigned holds everything that is in no "
            "preset.")
        P.fill_preset_combo(self._preset_box, self._cfg, self._preset)
        self._preset_box.currentIndexChanged.connect(self._preset_picked)
        row.addWidget(self._preset_box)

        self._btn_presets = U.button("Manage presets", icon_name="gear",
                                     tip="Make, rename and forget the sets of "
                                         "alarms.")
        self._btn_presets.clicked.connect(self._manage_presets)
        row.addWidget(self._btn_presets)

        row.addStretch(1)
        self._header_note = QLabel("")
        self._header_note.setStyleSheet(f"color: {U.QUIET};")
        row.addWidget(self._header_note)
        return row

    # ── the items ──────────────────────────────────────────────────────────
    def items(self):
        """Every item there is. New rows are appended to this very list."""
        return self._items

    def active_items(self):
        """Only the chosen preset — what is listed, and what is watched."""
        return C.items_in_preset(self._items, self._preset)

    def preset(self):
        return self._preset

    # ── the presets ────────────────────────────────────────────────────────
    def _preset_picked(self):
        chosen = self._preset_box.currentData()
        if chosen is None or chosen == self._preset:
            return
        self._preset = chosen
        self._cfg["active_preset"] = chosen
        # The engine keeps per-item state; the ones that just left the preset
        # are no longer read, so their state has to go with them.
        self._engine.drop_missing()
        self.refresh_tabs()
        self.save_soon()
        n = len(self.active_items())
        self.log(f"Preset \"{P.preset_word(chosen)}\" — "
                 f"{n} alarm{'' if n == 1 else 's'} listed and watched.")

    def _manage_presets(self):
        dlg = P.ManageDialog(self, self._cfg, self._items)
        dlg.exec()
        if not dlg.changed:
            return
        # The preset being worked in may have just been renamed or deleted.
        if self._preset not in (C.PRESET_ALL, C.PRESET_UNASSIGNED) \
                and self._preset not in C.alarm_presets(self._cfg):
            self._preset = C.PRESET_ALL
            self._cfg["active_preset"] = self._preset
        self._preset = P.fill_preset_combo(self._preset_box, self._cfg,
                                           self._preset)
        self._engine.drop_missing()
        self.refresh_tabs()
        self.save_soon()

    def refresh_presets(self):
        """A tab made or changed a preset: put the drop-down back in step."""
        self._preset = P.fill_preset_combo(self._preset_box, self._cfg,
                                           self._preset)

    def engine(self):
        return self._engine

    def config(self):
        return self._cfg

    def log(self, message, hint=None):
        self._watch.log(message, hint)

    def _edited(self, item=None):
        """Anything changed a watched item: repaint everything and save.

        `item` is set when the operator asked to edit that one, which the Watch
        tab does on a double-click; the Values or Areas tab is then brought up
        with it selected, because that is where it is edited.
        """
        self._engine.drop_missing()
        self.refresh_tabs()
        self.save_soon()
        if item is not None:
            self.edit_item(item)

    def edit_item(self, item):
        """Open the tab that owns this kind of item, with it selected."""
        tab = self._values if item.get("kind") == "value" else self._areas
        if tab is None:
            return
        self._tabs.setCurrentWidget(tab)
        try:
            tab.select_item(int(item["id"]))
        except Exception:
            pass

    def refresh_tabs(self):
        # The lamp and the floating panel list the same rows as the tabs do, so
        # they are put back in step here too — a preset change alters all three.
        self._update_light()
        for tab in (self._watch, self._values, self._areas, self._alarm_tab):
            if tab is None:
                continue
            try:
                tab.reload()
            except AttributeError:
                pass
            except Exception as exc:
                self.log(f"A tab could not be refreshed: {exc}")

    def save_soon(self):
        self._save_timer.start()

    def _save_now(self):
        self._cfg["items"] = self._items
        problem = C.save_config(self._cfg)
        if problem:
            self.log(problem)

    # ── watching ───────────────────────────────────────────────────────────
    def _toggle_watching(self):
        if self._engine.watching:
            self._engine.stop()
        else:
            armed = [it for it in self.active_items() if it.get("on")]
            if not armed:
                where = ("" if self._preset == C.PRESET_ALL
                         else f' in the preset "{P.preset_word(self._preset)}"')
                QMessageBox.warning(
                    self, "Nothing to watch",
                    f"Nothing is switched on{where}. Tick something in the On "
                    "column first, on the Watch tab or in the Values or Areas "
                    "list — or pick another preset in the header.")
                return
            self._engine.start()

    def _reset(self):
        self._engine.reset()
        self._stop_alarm()
        self._update_light()

    def _on_watching_changed(self, watching):
        self._btn_watch.setText("Stop watching" if watching else "Start watching")
        self._btn_watch.setIcon(U.icon("stop" if watching else "play", "#ffffff"))
        if watching:
            self.log("Watching.")
            self._show_hud()
        else:
            self.log("Stopped watching.")
            self._hide_hud()
        self._update_light()

    def _update_light(self):
        # The chosen preset, not every item there is: what is not in the preset
        # is not read at all, so it can neither have fired nor be "ready".
        listed = self.active_items()
        fired = any(self._engine.state_of(int(it["id"])).fired
                    for it in listed)
        if self._engine.watching:
            colour = "green"
        elif fired:
            colour = "red"
        elif any(it.get("on") for it in listed):
            colour = "orange"
        else:
            colour = "grey"
        self._light.set_colour(colour)
        self._light.setToolTip({
            "green": "Watching. Click to stop.",
            "red": "Something fired. Click to start watching again.",
            "orange": "Ready, but not watching. Click to start.",
            "grey": "Nothing is switched on.",
        }[colour])
        no_data = self._engine.no_data()
        self._mark.setVisible(no_data)
        if self._hud is not None and self._hud.isVisible():
            self._hud.set_colour(colour)
            self._hud.set_no_data(no_data)
            self._hud.set_badges(self._badges())

    _MAX_BADGES = 5

    def _badges(self):
        """The sentences that belong on the panel: readings out of range.

        NOTHING about readings that did not arrive. "not read yet", "could not
        be read", "not refreshed for 40 s" — none of those is a sentence any
        more; a failing channel is said once in the log and, after five
        minutes, by the exclamation mark beside the circle. Asked for on
        2026-09-21: the panel floats over the operator's real work and it was
        filling up with read failures, which tell him nothing he can act on.

        Worst first, and capped. The panel floats over the operator's real work,
        and with the lab idle every one of the fourteen machine values is
        legitimately out of range at once — a column of fourteen badges is a wall
        of red that says less than one line would. So the worst few get a badge
        each and the rest are counted.

        What a badge SAYS: the one that actually raised the alarm shows the
        operator's own sentence, because that is the instruction. Everything else
        shows its reading, because when eight things are wrong the numbers are
        what tell you what is going on.
        """
        rows = []
        # Only the chosen preset. `self._items` is everything ever set up, and
        # what the preset leaves out is never read, so it sat on the panel for
        # ever as "not read yet" — five badges for two watched rows.
        for it in self.active_items():
            if not it.get("on"):
                continue
            st = self._engine.state_of(int(it["id"]))
            # Only a real verdict on a real reading. "unknown" and "stale" are
            # both "we could not read it", and that is the mark's job.
            if st.state not in (C.STATE_WARN, C.STATE_TRIP):
                continue
            name = it.get("name") or "(unnamed)"
            if st.fired:
                text = f"{name} — {C.fire_sentence(it, st.text)}"
            else:
                text = f"{name} — {st.text}"
            rows.append((C.state_rank(st.state), 0 if st.fired else 1,
                         text, st.state))
        rows.sort(key=lambda r: (-r[0], r[1]))
        out = [(text, state) for _rank, _f, text, state in
               rows[:self._MAX_BADGES]]
        left = len(rows) - len(out)
        if left > 0:
            worst = rows[self._MAX_BADGES][3]
            out.append((f"and {left} more not in range", worst))
        return out

    # ── the alarm ──────────────────────────────────────────────────────────
    def _ensure_alarm(self):
        if self._alarm is None:
            try:
                from ann_alarm import AlarmWindow
                self._alarm = AlarmWindow(self._cfg, self.log, self)
                self._alarm.dismissed.connect(self._on_alarm_dismissed)
            except Exception as exc:
                self.log(f"The alarm window could not be built: {exc}")
        return self._alarm

    def _on_fired(self, item_id, sentence):
        item = self._engine.item_by_id(item_id)
        name = (item or {}).get("name") or "Something"
        self.log(f"ALARM — {name}: {sentence}")
        # One alarm at a time: watching stops, so nothing re-alarms twice a
        # second. Reset arms it again, and if the thing is still wrong it fires
        # straight away — which is the truth.
        #
        # The circle and its sentences stay on screen with the flash. While the
        # alarm is up the badge is the only place the operator's own words can
        # be read, so putting the tabbed window back over them would take the
        # message away at exactly the wrong moment.
        self._alarm_up = True
        self._engine.stop()
        alarm = self._ensure_alarm()
        if alarm is not None:
            alarm.raise_alarm(sentence)
        self._update_light()

    def _on_alarm_dismissed(self):
        """The flash was clicked away, or its time ran out."""
        if not self._alarm_up:
            return
        self._alarm_up = False
        self._hide_hud()
        self._update_light()

    def _stop_alarm(self):
        # Dismiss FIRST: dismissing emits its own signal, and that is what puts
        # the circle away and the window back. Clearing the flag before it would
        # make that handler decide there was nothing to put back.
        if self._alarm is not None:
            self._alarm.dismiss()
        self._alarm_up = False

    def _test_alarm(self):
        alarm = self._ensure_alarm()
        if alarm is not None:
            alarm.raise_alarm("This is a test. Click it or press Esc.")

    # ── the panel that stays on screen while watching ──────────────────────
    def ensure_hud(self):
        """The circle window, made on first use. None if it cannot be built."""
        if self._hud is None:
            try:
                from ann_alarm import HudWindow
                self._hud = HudWindow(self._cfg, self)
                self._hud.clicked.connect(self._on_hud_clicked)
            except Exception as exc:
                self.log(f"The circle could not be built: {exc}")
        return self._hud

    def _show_hud(self):
        if self.ensure_hud() is None:
            return
        self._hud.set_colour(self._light.colour())
        self._hud.show_panel()
        self._hud.set_no_data(self._engine.no_data())
        self._hud.set_badges(self._badges())
        # The tabbed window goes away, so only the circle is on screen and the
        # desktop behind it still takes clicks. That is how this program has
        # always worked while it is watching.
        self.hide()

    def _hide_hud(self, bring_window_back=True):
        if self._alarm_up:
            # Still flashing. The circle and the sentences belong on screen
            # with it; the window comes back once the alarm is dismissed.
            return
        if self._hud is not None:
            self._hud.hide_panel()
        # `bring_window_back` is False on the way out. Shutting down used to
        # come through here and re-SHOW the window it was in the middle of
        # closing.
        if bring_window_back and not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()

    def _on_hud_clicked(self):
        """The circle on screen was clicked.

        While an alarm is up the click means "I have seen it" — the same as
        clicking the flash. Otherwise it means "stop watching", and the window
        comes back.
        """
        if self._alarm_up:
            self._stop_alarm()
            return
        self._engine.stop()

    # ── shutting down ──────────────────────────────────────────────────────
    def closeEvent(self, ev):
        ann_log.note("the window was closed — shutting down")
        for step in (self._save_now, self._engine.shutdown,
                     lambda: self._monitors.hide(),
                     self._stop_alarm,
                     lambda: self._hide_hud(bring_window_back=False)):
            try:
                step()
            except Exception:
                pass
        super().closeEvent(ev)
        # And really end. `setQuitOnLastWindowClosed(False)` is there so that
        # hiding the window while watching does not quit — but it also meant
        # closing the window left the event loop running with nothing on
        # screen: an invisible copy holding the settings file, and a second one
        # the next time the program was started.
        QApplication.quit()


def main():
    # Qt's own warnings into the same file. "QBackingStore::endPaint() called
    # with active painter" and "Signal source has been deleted" are Qt naming
    # this exact class of bug, and they used to go nowhere.
    ann_log.install_qt_handler()

    aumid = _icon_app_id("ELIBeamlines.Announcer", _icon_file())
    if aumid:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        except Exception:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    # Hiding the main window while watching must not quit the program: the
    # circle left on screen is not a QWindow Qt counts.
    app.setQuitOnLastWindowClosed(False)
    ico = _icon_file()
    if ico:
        try:
            app.setWindowIcon(QIcon(str(ico)))
        except Exception:
            pass
    U.install_app_look(app)

    win = AnnouncerWindow()
    win.show()
    code = app.exec()
    sys.exit(code)


if __name__ == "__main__":
    main()
