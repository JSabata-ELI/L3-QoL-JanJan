"""Laser settings — the DM settings line on the Overview, judged by SBW4.

    python testing/test_dm_overview.py

The rule (`ls_core.dm_judge`): a fresh SBW4 shot over 0.05 J means the mirror
must be on High power, one at or under it means Low power; a contradiction is a
cross and the halls in force blink. Then the same on the real window, built
offscreen, with the engine's readings and its last DM view put in by hand —
the clock is never started, so nothing leaves this PC.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import ls_core as C                                             # noqa: E402
import ls_dm as D                                               # noqa: E402

FAILED = []
SBW4 = C.FIRING_CHANNELS[0]


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


class Energy:
    """An SBW4 reading as the engine hands it over."""

    def __init__(self, value, stamp):
        self.value = value
        self.error = None
        self.newest_ns = stamp


def test_the_rule():
    print("the rule: Low / High power against a fresh SBW4 shot")
    low, high, neither = D.TAG_LOW, D.TAG_HIGH, D.TAG_NONE
    check("ls_core and ls_dm use the same words",
          (C.DM_TAG_HIGH, C.DM_TAG_LOW), (high, low))
    cases = [
        ("Low, no fresh shot", low, None, C.STATE_OK),
        ("High, no fresh shot", high, None, C.STATE_OK),
        ("Low, 0.02 J", low, (0.02, 3.0), C.STATE_OK),
        ("Low, 0.05 J exactly", low, (0.05, 3.0), C.STATE_OK),
        ("Low, 0.3 J", low, (0.3, 3.0), C.STATE_BAD),
        ("High, 0.3 J", high, (0.3, 3.0), C.STATE_OK),
        ("High, 0.02 J", high, (0.02, 3.0), C.STATE_BAD),
        ("ticked for neither, no shot", neither, None, C.STATE_UNKNOWN),
        ("ticked for neither, 0.3 J", neither, (0.3, 3.0), C.STATE_BAD),
        ("ticked for neither, 0.02 J", neither, (0.02, 3.0), C.STATE_BAD),
        ("mirror not known", None, (0.3, 3.0), C.STATE_UNKNOWN),
    ]
    for what, power, energy, want in cases:
        check(what, C.dm_judge(power, energy)[0], want)

    print("SPST3 open with no shot over 0.05 J: low power is being shot")
    cases = [
        ("High, shutter open, no shot", high, None, True, C.STATE_BAD),
        ("Low, shutter open, no shot", low, None, True, C.STATE_OK),
        ("High, shutter open, 0.3 J", high, (0.3, 3.0), True, C.STATE_OK),
        ("Low, shutter open, 0.3 J", low, (0.3, 3.0), True, C.STATE_BAD),
        ("High, shutter open, 0.02 J", high, (0.02, 3.0), True, C.STATE_BAD),
        ("High, shutter closed, no shot", high, None, False, C.STATE_OK),
        ("High, shutter unknown, no shot", high, None, None, C.STATE_OK),
        ("neither, shutter open", neither, None, True, C.STATE_BAD),
        ("mirror not known, shutter open", None, None, True,
         C.STATE_UNKNOWN),
    ]
    for what, power, energy, shutter, want in cases:
        check(what, C.dm_judge(power, energy, "x", shutter)[0], want)

    print("the SAST1 shutter's state: 1 = open")
    reading = Energy(1.0, 1)
    check("1 is open", C.shutter_open({C.DM_SHUTTER_CHANNEL: reading},
                                      {C.DM_SHUTTER_CHANNEL: 100.0}, 101.0),
          True)
    reading.value = 0.0
    check("0 is closed", C.shutter_open({C.DM_SHUTTER_CHANNEL: reading},
                                        {C.DM_SHUTTER_CHANNEL: 100.0}, 101.0),
          False)
    check("not read for a minute: not known",
          C.shutter_open({C.DM_SHUTTER_CHANNEL: reading},
                         {C.DM_SHUTTER_CHANNEL: 100.0}, 200.0), None)
    check("never read: not known", C.shutter_open({}, {}, 101.0), None)
    cases = []
    for what, power, energy, want in cases:
        check(what, C.dm_judge(power, energy)[0], want)

    print("a fresh shot is FiringWatch's: new, and under 15 s old")
    watch = C.FiringWatch()
    readings = {SBW4: Energy(0.3, 1)}
    last_ok = {SBW4: 1000.0}
    watch.update(readings, last_ok, 1000.0, 50.0)
    check("the sample there at start-up is not fresh",
          watch.fresh_energy(50.0), None)
    readings[SBW4] = Energy(0.02, 2)
    watch.update(readings, last_ok, 1000.0, 60.0)
    check("a new one is", watch.fresh_energy(62.0), (0.02, 2.0))
    check("and 15 s later it is not", watch.fresh_energy(75.0), None)


def test_the_overview():
    from PySide6.QtWidgets import QApplication

    import ls_engine
    tmp = tempfile.TemporaryDirectory()
    C.config_path = lambda: Path(tmp.name) / "laser_settings.json"
    ls_engine.PollEngine.start = lambda self: None
    app = QApplication.instance() or QApplication(sys.argv)
    import ls_ui as U
    U.install_app_look(app)
    from test_dm_grid import read, screen
    import main as program

    win = program.LaserSettingsWindow()
    ov = win._overview
    engine = win._engine

    print("DM settings is on the list, unticked: not on the Overview")
    dm_row = next(r for r in win.rows() if C.is_dm_row(r))
    check("unticked", C.is_shown(dm_row), False)
    check("no row on the Overview", len(ov._row_widgets), 0)

    print("ticked: one row, reading the mirror")
    dm_row["show"] = True
    win.rows_changed()
    check("one row", len(ov._row_widgets), 1)

    now = [1000.0]
    clock = [100.0]
    C_time = ov.__class__.refresh.__globals__["time"]

    class Clock:
        @staticmethod
        def time():
            return now[0]

        @staticmethod
        def monotonic():
            return clock[0]

    ov.__class__.refresh.__globals__["time"] = Clock
    readings = {}
    last_ok = {}
    engine.readings = lambda: readings
    engine.last_ok = lambda: last_ok
    high = read(screen("dm_0727.png")).colours
    low = read(screen("dm_0731.png")).colours

    def mirror(colours):
        engine._dm = D.DmView({"at": now[0] - 2, "host": "OPR1",
                               "status": D.STATUS_OK,
                               "colours": [list(c) for c in colours]},
                              D.with_references({}), None, None, None, now[0])

    def shot(value, stamp):
        readings[SBW4] = Energy(value, stamp)
        last_ok[SBW4] = now[0]

    def row():
        w = ov._row_widgets[0]
        return w.texts()[1].replace(" ", " "), w.state()

    def blinking():
        return sorted(h for h, s in ov._squares.items() if s.state() == "fault")

    try:
        mirror(high)
        shot(0.3, 1)                    # there at start-up: not fresh
        ov.refresh()
        check("High power, tick", row(), ("High power", C.STATE_OK))
        check("nothing blinks", blinking(), [])

        print("a fresh 0.02 J shot on High power: cross, the hall blinks")
        clock[0] += 5
        shot(0.02, 2)
        ov.refresh()
        check("cross", row(), ("High power", C.STATE_BAD))
        check("Int blinks, though the laser is not firing", blinking(),
              ["Int"])

        print("15 s without a new shot: a tick again")
        clock[0] += 16
        ov.refresh()
        check("tick", row(), ("High power", C.STATE_OK))
        check("nothing blinks", blinking(), [])

        print("SAST1 opens, no SBW4 shot: low power on High power, cross")
        check("the shutter is read once the line is shown",
              C.DM_SHUTTER_CHANNEL in win.channels(), True)
        readings[C.DM_SHUTTER_CHANNEL] = Energy(1.0, 5)
        last_ok[C.DM_SHUTTER_CHANNEL] = now[0]
        ov.refresh()
        check("cross", row(), ("High power", C.STATE_BAD))
        check("Int blinks", blinking(), ["Int"])
        readings[C.DM_SHUTTER_CHANNEL] = Energy(0.0, 6)
        ov.refresh()
        check("closed again: tick", row(), ("High power", C.STATE_OK))

        print("Low power and a fresh 0.3 J shot: cross")
        mirror(low)
        clock[0] += 1
        shot(0.3, 3)
        ov.refresh()
        check("cross", row(), ("Low power", C.STATE_BAD))
        check("Int blinks", blinking(), ["Int"])

        print("Alarm unticked: a grey dash, nothing blinks")
        dm_row["alarm"] = False
        ov.refresh()
        check("dash", row(), ("Low power", C.STATE_UNKNOWN))
        check("nothing blinks", blinking(), [])
        dm_row["alarm"] = True

        print("the mirror not read: Unknown, no verdict")
        engine._dm = D.DmView({"at": now[0] - 2, "host": "OPR1",
                               "status": D.STATUS_MINIMISED,
                               "colours": None},
                              D.with_references({}), None, None, None, now[0])
        ov.refresh()
        check("Unknown", row(), ("Unknown", C.STATE_UNKNOWN))
        check("nothing blinks", blinking(), [])

        print("the Limits tab shows the set-up in Reading now")
        now[0] = C_time.time()          # that tab reads the real clock
        mirror(high)
        win._settings.refresh()
        st = win._settings
        t = next(i for i, r in enumerate(st._table_rows)
                 if r is not None and C.is_dm_row(r))
        import st_t
        check("High power", st._table.item(t, st_t._C_NOW).text(),
              "High power")
        check("Min not editable",
              bool(st._table.item(t, st_t._C_MIN).flags()
                   & st_t.Qt.ItemFlag.ItemIsEditable), False)
    finally:
        ov.__class__.refresh.__globals__["time"] = C_time
        win.deleteLater()
        app.processEvents()
        tmp.cleanup()


def main():
    test_the_rule()
    test_the_overview()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for line in FAILED:
            print("  " + line)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
