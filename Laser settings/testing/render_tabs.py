"""Laser settings — grab every tab as a picture, without showing anything.

    python testing/render_tabs.py

Nothing appears on screen: the widgets are given `WA_DontShowOnScreen`, so Qt
lays them out and paints them into their own buffer and the window manager
never sees them. The `windows` platform plugin is used deliberately — the
`offscreen` one on this PC has no usable font and renders every letter as an
empty box, which makes a screenshot useless for checking that text fits.

Nothing here talks to the archiver. The readings are made up, so the pictures
are reproducible and the program is not "started" in any real sense.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The real plugin, for real fonts. Nothing is shown; see the docstring.
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from PySide6.QtCore import Qt                                   # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

import ls_core as C                                             # noqa: E402
import ls_ui as U                                               # noqa: E402


class FakeReading:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    @property
    def has_value(self):
        return self.error is None and self.value is not None


class FakeEngine:
    """Answers the two questions the tabs ask, from a canned table."""

    def __init__(self, readings, last_ok):
        self._readings = readings
        self._last_ok = last_ok

    def readings(self):
        return self._readings

    def last_ok(self):
        return self._last_ok

    def screen(self):
        return None

    def last_ok_for(self, channel):
        return self._last_ok.get(channel)

    def reading(self, channel):
        return self._readings.get(channel)

    def forget(self, channel=None):
        pass


class FakeWindow:
    def __init__(self, cfg, engine):
        self._cfg = cfg
        self._engine = engine
        self.messages = []
        # A counter that has counted twelve shots, so the Shots row has a
        # number in the picture.
        self._shots = C.ShotCounter(C.shots_of(cfg)["reset_s"])
        self._shots.shown = 12

    def shots(self):
        return self._shots

    def shot_settings(self):
        return C.shots_of(self._cfg)

    def shot_reset_changed(self):
        pass

    def rows(self):
        return self._cfg["rows"]

    def scanning(self):
        return bool(self._cfg.get("scanning", False))

    def set_scanning(self, on):
        self._cfg["scanning"] = bool(on)

    def shooting(self):
        return self._cfg["shooting"]

    def active_hall(self):
        return self._cfg["active_hall"]

    def set_active_hall(self, hall):
        self._cfg["active_hall"] = hall

    def engine(self):
        return self._engine

    def look(self):
        return C.look_of(self._cfg)

    def halls(self):
        return C.halls_of(self._cfg)

    def width(self):
        """The band's aspect is worked out from the window's width.

        A fixed number, so the pictures are reproducible — there is no real
        window here and the Settings tab's board asks for one.
        """
        return 1180

    def has_channels(self):
        return True

    def settings_changed(self, rebuild=False):
        pass

    def look_changed(self):
        pass

    def halls_arranged(self):
        pass

    def rows_changed(self):
        pass

    def log(self, message, hint=None):
        self.messages.append(str(message))


def scene():
    """One made-up machine: shooting into E2, one channel out of range."""
    import time
    now = time.time()
    cfg = C.default_config()
    cfg["active_hall"] = "E2"

    spec = [
        ("L3-FE-PULSER:Delay", "Front end delay", "ns", 12.0, (10, 20)),
        ("L3-AMP-PS01:Voltage", "Amplifier 1 voltage", "kV", 23.4, (20, 25)),
        ("L3-AMP-PS02:Voltage", "Amplifier 2 voltage", "kV", 31.8, (20, 25)),
        ("L3-COMP-VAC:Pressure", "Compressor pressure", "mbar", 0.0012,
         (None, 0.005)),
        ("L3-UTIL-CHL03-001:Temp", "Chiller 3 water", "°C", 18.6, (17, 20)),
        ("L3-SHUTTER-MAIN:State", "Main shutter", "", 1.0, (1, 1)),
        # A minus sign, so the picture shows it standing in front of the
        # digits of a right-aligned number.
        ("L3-DAZZLER:GDD", "GDD", "fs^2", -12000.0, (-13000, -11000)),
    ]
    readings, last_ok = {}, {}
    for i, (pv, name, unit, value, (lo, hi)) in enumerate(spec, start=1):
        row = C.new_row(i, pv, name, unit)
        for hall in C.HALLS:
            entry = C.limits_for(row, hall)
            entry["min"] = None if lo is None else float(lo)
            entry["max"] = None if hi is None else float(hi)
        cfg["rows"].append(row)
        readings[pv] = FakeReading(value=value)
        last_ok[pv] = now

    # One channel nobody can read, so the grey dash is in the picture too.
    dead = C.new_row(99, "L3-GONE:Nothing", "Spare diagnostic", "")
    for hall in C.HALLS:
        C.limits_for(dead, hall).update({"min": 0.0, "max": 1.0})
    cfg["rows"].append(dead)
    readings["L3-GONE:Nothing"] = FakeReading(error="Channel not found (HTTP 404)")

    # A formula that works out, and one that cannot, so both states of the box
    # are in the picture.
    good = C.new_formula_row(101, "Compressed energy", "B * 0.749", "J",
                             {"B": 2})
    for hall in C.HALLS:
        C.limits_for(good, hall).update({"min": 10.0, "max": 20.0})
    cfg["rows"].append(good)
    bad = C.new_formula_row(102, "Half of a missing row", "Z / 2", "", {})
    cfg["rows"].append(bad)

    # The shot counter, so its row is in the pictures of both tabs.
    cfg["rows"].append(C.new_shot_row(104))

    # One row with Show off, so the UNTICKED tick box is in the picture too —
    # it has to be as visible as a ticked one.
    unwatched = C.new_row(103, "L3-SPARE:Thing", "Not watched at all", "")
    unwatched["show"] = False
    for hall in C.HALLS:
        C.limits_for(unwatched, hall).update({"min": 0.0, "max": 1.0})
    cfg["rows"].append(unwatched)
    readings["L3-SPARE:Thing"] = FakeReading(value=9.0)
    last_ok["L3-SPARE:Thing"] = now

    # The laser is firing — SBW4 over 0.1 J — or no square could be green or
    # blink at all (`ls_core.firing`).
    for pv in C.FIRING_CHANNELS:
        readings[pv] = FakeReading(value=0.52)
        last_ok[pv] = now

    # One hall in each state the squares can be in: E2 shot into (and with a
    # channel out of range, so it blinks), Int open-but-idle, E3 idle, E4 with
    # a dead open switch answered by its fallback, E5 unreadable altogether.
    for hall in C.HALLS:
        op = C.gate_for(cfg["shooting"], hall)["open"]
        cl = C.closed_channel(hall)
        if hall == "E5":
            readings[op] = FakeReading(error="Archiver unreachable")
            readings[cl] = FakeReading(error="Archiver unreachable")
            continue
        if hall == "E4":                    # the open switch is down
            readings[op] = FakeReading(error="Archiver unreachable")
            readings[cl] = FakeReading(value=1.0)
            last_ok[cl] = now
            continue
        # open, and E2 shoots when open; Int shoots when CLOSED, so it is idle
        readings[op] = FakeReading(value=1.0 if hall in ("Int", "E2") else 0.0)
        last_ok[op] = now
    return cfg, FakeEngine(readings, last_ok)


def scene_today():
    """The machine as it really is: SGV502 to SGV505 publish nothing.

    Int's valve is open, so on the plain rule nobody is shooting anywhere and
    every row would wear a grey dash. `ls_core.halls_in_force` puts Int's limits
    in force instead and the square goes WHITE — this is the picture to check
    that against.
    """
    import time
    now = time.time()
    cfg, engine = scene()
    readings, last_ok = engine.readings(), engine.last_ok()
    for hall in C.HALLS:
        op = C.gate_for(cfg["shooting"], hall)["open"]
        cl = C.closed_channel(hall)
        if hall == "Int":
            readings[op] = FakeReading(value=1.0)       # open: NOT shooting
            last_ok[op] = now
            continue
        for pv in (op, cl):
            readings[pv] = FakeReading(error="Channel not found (HTTP 404)")
            last_ok.pop(pv, None)
    return cfg, engine


def _say_fit(cfg, tab, label):
    """Print what the fit worked out, beside the picture it worked it out for.

    The four numbers are the ones the Settings tab's boxes show, so printing
    them here is how "Height of the band is what is really on screen" is
    checked by eye at the same moment the picture is looked at.
    """
    look, halls = C.look_of(cfg), C.halls_of(cfg)
    band = tab._band
    square = band.squares["Int"]
    print(f"      fit {label}:  text {look['size_px']}  "
          f"square {look['mark_px']}  band {halls['band_px']} "
          f"(really {band.height()})  hall {square.width()}x{square.height()} "
          f"at y={square.y()}  word {halls['word_px']}")


def grab(widget, path, size=(1180, 720)):
    """Lay the widget out, paint it, save it — all without a visible window."""
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()                       # never reaches the screen; see above
    QApplication.processEvents()
    QApplication.processEvents()
    widget.grab().save(str(path), "PNG")
    widget.hide()
    print(f"wrote {path.name}  ({widget.width()}x{widget.height()})")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    U.install_app_look(app)
    cfg, engine = scene()
    win = FakeWindow(cfg, engine)

    from in_t import InfoTab
    from ov_t import OverviewTab
    from st_t import SettingsTab

    overview = OverviewTab(win)
    # Not firing yet (the energy archived at start is never a shot), so the
    # rows that read are ORANGE — idle — and the unreadable one is grey.
    grab(overview, HERE / "overview_idle.png")
    overview._firing.update = lambda *_a: (True, "SBW4 energy 0.520 J.")
    overview.refresh()
    grab(overview, HERE / "overview.png")

    # The same tab with a hall in fault, frozen on the dark half of the blink,
    # so the picture shows what the blink actually looks like.
    for square in overview._squares.values():
        square.set_blink(False)
    grab(overview, HERE / "overview_blink_off.png")

    settings = SettingsTab(win)
    grab(settings, HERE / "settings.png")
    # And squeezed. The window has no minimum size any more, so this tab is in
    # a scroller: at 420 px wide it has to SCROLL rather than clip its buttons
    # and its valve box off the edge.
    grab(settings, HERE / "limits_tiny.png", size=(420, 300))
    grab(settings, HERE / "settings.png")

    # Today's machine: only Int can answer, so Int's limits are in force. Its
    # valve SGV501 is open and no other valve can be read, so the square says
    # L3, "waiting for hall".
    cfg2, engine2 = scene_today()
    win2 = FakeWindow(cfg2, engine2)
    cfg2["active_hall"] = "Int"
    watching = OverviewTab(win2)
    grab(watching, HERE / "overview_watching_int.png")

    # The same again with nothing out of range, which is the WHITE square: the
    # one state that only exists because of the fallback.
    engine2.readings()["L3-AMP-PS02:Voltage"] = FakeReading(value=23.9)
    watching.refresh()
    grab(watching, HERE / "overview_watching_int_ok.png")
    settings_int = SettingsTab(win2)
    grab(settings_int, HERE / "settings_watching_int.png")

    # The fit at both ends. The window has no minimum size any more, so these
    # two are the pictures that matter: on a big one the squares and the list
    # have to GROW into the room with no strip of nothing between the tab bar
    # and the line, and on a tiny one everything has to be on its floor and
    # still legible. Both are the fit doing the work — `fit` is on by default.
    grab(watching, HERE / "overview_big.png", size=(1920, 1080))
    _say_fit(cfg2, watching, "big   1920x1080")
    grab(watching, HERE / "overview_tiny.png", size=(420, 300))
    _say_fit(cfg2, watching, "tiny    420x300")
    grab(watching, HERE / "overview_smallest.png", size=(806, 470))
    _say_fit(cfg2, watching, "small   806x470")

    # The one-square mode: one square named after the hall in force, the list
    # beside it. E2 with a channel out of range (the red half of the blink),
    # and today's machine, where it is Int.
    cfg3, engine3 = scene()
    C.halls_of(cfg3)["single"] = True
    single = OverviewTab(FakeWindow(cfg3, engine3))
    grab(single, HERE / "overview_single.png")
    grab(single, HERE / "overview_single_big.png", size=(1920, 1080))
    grab(single, HERE / "overview_single_tiny.png", size=(420, 300))
    C.halls_of(cfg2)["single"] = True
    watching.reload()
    grab(watching, HERE / "overview_single_int.png")
    C.halls_of(cfg2)["single"] = False
    watching.reload()

    # The Info / Log tab, with a few lines in it so the log is not an empty box
    # in the picture.
    info = InfoTab(win2)
    for line in ("Settings: C:\\Users\\someone\\AppData\\Roaming\\"
                 "LaserSettings\\laser_settings.json",
                 "Amplifier 2 voltage in Int: the minimum (30) is above the "
                 "maximum (25), so nothing can ever be in range.",
                 "Spare diagnostic: Channel not found (HTTP 404)"):
        info.append_line(line)
    info.refresh()
    grab(info, HERE / "info.png")

    # The Settings tab: the board the squares are arranged on, and the live
    # example of the list. Rendered on the real `windows` plugin, which is the
    # only way to see that the typeface, the size and the square really reached
    # the row.
    from ft_t import FontTab
    font_tab = FontTab(win)
    grab(font_tab, HERE / "font.png")
    # With the one-square mode on: the board is switched off and the band's
    # box is the square's size.
    C.halls_of(cfg)["single"] = True
    font_tab.reload()
    grab(font_tab, HERE / "font_single.png")
    C.halls_of(cfg)["single"] = False
    font_tab.reload()

    # The same tab with a hand-made arrangement, so the picture shows what a
    # dragged board looks like: two big squares over three small ones.
    halls = C.halls_of(cfg)
    halls["auto"] = False
    halls["layout"] = {
        "Int": [0.02, 0.05, 0.22, 0.90],
        "E2":  [0.26, 0.05, 0.22, 0.90],
        "E3":  [0.52, 0.05, 0.15, 0.42],
        "E4":  [0.69, 0.05, 0.15, 0.42],
        "E5":  [0.52, 0.53, 0.32, 0.42],
    }
    font_tab.reload()
    grab(font_tab, HERE / "settings_tab_arranged.png")
    arranged = OverviewTab(win)
    grab(arranged, HERE / "overview_arranged.png")

    # And the same arrangement with the word switched off, which is the other
    # half of what the switch does.
    halls["show_word"] = False
    arranged.reload()
    grab(arranged, HERE / "overview_arranged_no_word.png")
    halls["show_word"] = True

    # A GDD scan: Scanning ticked on GDD with a scan range that holds its
    # reading, so E2 says "SHOOTING + SCANNING" — and the blink's red half
    # says "OUT OF RANGE + SCANNING" while another row is out of range.
    cfg4, engine4 = scene()
    gdd = next(r for r in cfg4["rows"] if r.get("name") == "GDD")
    gdd.update({"scan_min": -20000.0, "scan_max": 0.0})
    cfg4["scanning"] = True                 # the Overview's one Scanning box
    scan_win = FakeWindow(cfg4, engine4)
    scanning = OverviewTab(scan_win)
    # The energy already archived at start is never a shot (`FiringWatch`),
    # so a made-up machine never fires on its own: say it does.
    scanning._firing.update = lambda *_a: (True, "SBW4 energy 0.520 J.")
    scanning.refresh()
    grab(scanning, HERE / "overview_scanning_fault.png")
    engine4.readings()["L3-AMP-PS02:Voltage"] = FakeReading(value=23.9)
    scanning.refresh()
    grab(scanning, HERE / "overview_scanning.png")
    scan_limits = SettingsTab(scan_win)
    grab(scan_limits, HERE / "limits_scanning.png", size=(1500, 720))
    C.halls_of(cfg4)["single"] = True
    scanning.reload()
    grab(scanning, HERE / "overview_single_scanning.png")

    # The DM settings line (8.10.2026): the mirror on High power, read off the
    # 7:31 screenshot. First with no fresh shot (a tick), then with a fresh
    # 0.02 J one — low power being shot on High power — a cross and E2 blinks.
    import ls_dm as D
    from test_dm_grid import read, screen
    cfg5, engine5 = scene()
    dm_row = next((r for r in cfg5["rows"] if C.is_dm_row(r)), None)
    if dm_row is None:
        dm_row = C.new_dm_row(105)
        cfg5["rows"].append(dm_row)
    dm_row["show"] = True
    high = read(screen("dm_0731.png")).colours
    import time
    view = D.DmView({"at": time.time(), "host": "OPR1",
                     "status": D.STATUS_OK,
                     "colours": [list(c) for c in high]},
                    D.with_references({}), None, None, None, time.time())
    engine5.dm = lambda: view
    dm_tab = OverviewTab(FakeWindow(cfg5, engine5))
    dm_tab._firing.update = lambda *_a: (True, "SBW4 energy 0.520 J.")
    dm_tab.refresh()
    grab(dm_tab, HERE / "overview_dm_ok.png")
    dm_tab._firing.fresh_energy = lambda _m: (0.02, 3.0)
    dm_tab.refresh()
    grab(dm_tab, HERE / "overview_dm_fault.png")
    dm_limits = SettingsTab(FakeWindow(cfg5, engine5))
    grab(dm_limits, HERE / "limits_dm.png", size=(1500, 720))

    # Every timer stopped BEFORE anything is deleted. An Overview owns two —
    # the blink and the coalesced fit — and a fit that fires while its tab is
    # on the deferred-delete queue runs over a half-dismantled widget. That is
    # an access violation, not a Python exception, so it comes out as the whole
    # script dying with no traceback after the last picture was written.
    tabs = (overview, settings, watching, settings_int, info, font_tab,
            arranged, single, scanning, scan_limits, dm_tab, dm_limits)
    for widget in tabs:
        for timer in (getattr(widget, "_fit_timer", None),
                      getattr(widget, "_blink", None)):
            if timer is not None:
                timer.stop()
    QApplication.processEvents()
    for widget in tabs:
        widget.deleteLater()
    QApplication.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
