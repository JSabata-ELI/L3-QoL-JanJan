"""Pressure ceiling and the graph's right-hand Y axis.

1. The global sensor-error ceiling is split: pressure PVs use
   "Pressure reject above", everything else "Temperature reject above".
   A single ceiling of 80 threw away a vented chamber's ~800 Torr as
   "bad data".
2. The right-hand (twin) axes: "Autoscale this Y axis" must work even when
   a poll redrew the graph while the menu was open, Refresh must clear the
   right-hand fixed ranges too, and a toolbar zoom must stay put on both
   sides across polls.

Offscreen Qt, nothing is shown:
    python testing/test_pressure_ceiling_and_axes.py
"""
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication                        # noqa: E402

import monitor_tab as mt                                          # noqa: E402

NS_MIN = 60 * 10**9
OK = True


def check(name, cond):
    global OK
    OK = OK and bool(cond)
    print(("  ok   " if cond else "  FAIL ") + name)


class _Win:
    """What GraphPanel and _valid_range reach for on their owner."""

    _valid_range = mt.MonitorWidget._valid_range
    _pv_units_of = mt.MonitorWidget._pv_units_of
    _global_valid_range = mt.MonitorWidget._global_valid_range

    def __init__(self):
        self.pvs = []
        self.runtime = {}
        self.settings = dict(mt.DEFAULT_SETTINGS)
        self.settings["graph_window_minutes"] = 60

    def extend_backfill(self):
        pass


def _add_pv(win, name, units, values, t0_ns):
    pv = mt.PVConfig(name=name, display_name=name, units=units)
    t = t0_ns + np.arange(len(values), dtype=np.int64) * NS_MIN
    hist = mt._SampleHistory(span_ns=24 * 3600 * 10**9, cap=mt.HISTORY_HARD_CAP)
    hist.append_samples(t, np.asarray(values, dtype=np.float64))
    win.pvs.append(pv)
    win.runtime[name] = mt.PVRuntime(
        current_value=float(values[-1]), current_units=units,
        last_update_ns=int(t[-1]), history=hist)
    return pv


def _row(win, name):
    return next(r for r in win.settings["valid_ranges"] if r["name"] == name)


def test_ceilings():
    print("ceilings")
    win = _Win()
    win.settings["valid_max_default"] = 80.0
    win.settings["valid_max_pressure_default"] = None
    check("the table is built from the old fields on upgrade",
          mt._seed_valid_ranges(win.settings))
    check("... once", not mt._seed_valid_ranges(win.settings))
    t0 = 1_790_000_000 * 10**9
    temp = _add_pv(win, "T", "DegC", [20.0, 21.0], t0)
    torr = _add_pv(win, "P", "Torr", [800.0, 809.0], t0)
    mbar = _add_pv(win, "M", "mbar", [1.0, 2.0], t0)
    flow = _add_pv(win, "F", "l/min", [5.0, 6.0], t0)
    psi = _add_pv(win, "He", "PSI", [2000.0, 2100.0], t0)
    check("temperature keeps the 80 ceiling", win._valid_range(temp)[1] == 80.0)
    check("Torr has no ceiling by default", win._valid_range(torr)[1] is None)
    check("mbar counts as pressure", win._valid_range(mbar)[1] is None)
    check("PSI counts as pressure", win._valid_range(psi)[1] is None)
    check("flow falls to 'Everything else'",
          win._global_valid_range(flow)[2] == "Everything else"
          and win._valid_range(flow)[1] == 80.0)
    check("809 Torr is not out of range",
          not mt._out_of_range(809.0, *win._valid_range(torr)))
    _row(win, "Pressure")["max"] = 1000.0
    check("pressure ceiling is used once set",
          win._valid_range(torr)[1] == 1000.0)
    torr.valid_max = 900.0
    check("a PV's own ceiling still wins", win._valid_range(torr)[1] == 900.0)
    # The archiver's unit counts even when the PV's own field is empty.
    torr.valid_max = None
    torr.units = ""
    check("unit taken from the archiver",
          win._valid_range(torr)[1] == 1000.0)
    # A row naming a group beats the unit row, wherever it sits.
    psi.group = "Helium"
    win.settings["valid_ranges"].append(
        {"name": "Helium", "groups": ["helium"], "units": [],
         "min": None, "max": 5000.0})
    check("a group row wins over the unit row",
          win._global_valid_range(psi) == (None, 5000.0, "Helium"))
    check("other pressures keep the unit row",
          win._global_valid_range(mbar)[2] == "Pressure")
    psi.group, psi.subgroup = "Utilities", "Helium"
    check("a subgroup name matches too",
          win._global_valid_range(psi)[2] == "Helium")
    # A brand-new kind of PV gets its own row.
    hum = _add_pv(win, "H", "%RH", [40.0, 41.0], t0)
    win.settings["valid_ranges"].append(
        {"name": "Humidity", "groups": [], "units": ["%RH", "%"],
         "min": 0.0, "max": 100.0})
    check("a new unit row applies", win._valid_range(hum) == (0.0, 100.0))


def test_settings_table():
    print("settings table")
    w = mt.ValidRangesWidget()
    rows = [{"name": "Pressure", "groups": [], "units": ["torr", "psi"],
             "min": 0.0001, "max": 3500.0},
            {"name": "Helium", "groups": ["Helium"], "units": [],
             "min": None, "max": 5000.0}]
    w.set_rows(rows)
    back, problems = w.rows()
    check("rows read back unchanged", back == rows and not problems)
    w._add_row({"name": "Bad", "groups": [], "units": [], "min": 5.0,
                "max": 1.0})
    w._add_row({"name": "Text", "groups": ["x"], "units": [], "min": None,
                "max": None})
    w.table.item(3, 4).setText("lots")
    back, problems = w.rows()
    check("a row with no group or unit is refused",
          any("Bad" in p and "applies to nothing" in p for p in problems))
    check("a limit that is not a number is refused",
          any("Text" in p and "numbers" in p for p in problems))
    check("the good rows are still read", len(back) == 2)


def _panel():
    win = _Win()
    t0 = 1_790_000_000 * 10**9
    _add_pv(win, "T", "DegC", list(np.linspace(10, 20, 50)), t0)
    _add_pv(win, "P", "Torr", list(np.linspace(0, 800, 50)), t0)
    panel = mt.GraphPanel(win)
    panel.refresh_combo()
    panel.combo.setCurrentIndex(0)          # All PVs
    panel.resize(1200, 500)
    panel.redraw()
    panel.canvas.draw()
    return win, panel


def test_right_axis():
    print("right-hand axis")
    win, panel = _panel()
    check("one right-hand axis", len(panel._extra_axes) == 1)
    auto = panel._extra_axes[0].get_ylim()
    check("autoscaled right axis reaches 800", auto[1] >= 800)

    # The menu captures its key, then a poll redraws before the click.
    key = panel._axis_key(panel._extra_axes[0])
    panel._extra_yaxis[key] = (0.0, 80.0)
    panel.redraw()
    check("fixed range applied", panel._extra_axes[0].get_ylim() == (0.0, 80.0))
    stale_key = key
    panel.redraw()                           # the poll during the open menu
    panel._clear_yrange(stale_key)
    check("Autoscale after a redraw restores the data range",
          panel._extra_axes[0].get_ylim()[1] >= 800)

    # Refresh clears both sides.
    panel.set_yaxis(0, 5)
    panel._extra_yaxis[0] = (0.0, 80.0)
    panel.redraw()
    panel.reset_view()
    check("Refresh clears the right-hand fixed range",
          panel._extra_axes[0].get_ylim()[1] >= 800)
    check("Refresh clears the left fixed range",
          panel.ax.get_ylim()[1] >= 20)

    # A zoom pins both sides across a poll with new samples.
    panel.ax.set_ylim(12, 14)
    panel._extra_axes[0].set_ylim(100, 200)
    panel._capture_user_view()
    rt = win.runtime["P"]
    rt.history.append_samples(
        np.array([int(rt.last_update_ns) + NS_MIN], dtype=np.int64),
        np.array([810.0]))
    rt.last_update_ns += NS_MIN
    panel.refresh_data()
    check("left zoom survives a poll", panel.ax.get_ylim() == (12, 14))
    check("right zoom survives a poll",
          panel._extra_axes[0].get_ylim() == (100, 200))
    panel.redraw()
    check("right zoom survives a full redraw",
          panel._extra_axes[0].get_ylim() == (100, 200))

    # Autoscale on the right axis while zoomed: right side autoscales,
    # left keeps its zoom.
    panel._clear_yrange(0)
    check("Autoscale while zoomed frees the right axis",
          panel._extra_axes[0].get_ylim()[1] >= 800)
    check("... and keeps the left zoom", panel.ax.get_ylim() == (12, 14))

    # Home / Reset zoom unpins both.
    panel._capture_user_view()
    panel.toolbar.home()
    check("Home drops the zoom", panel._user_view is None)
    check("Home autoscales the right axis",
          panel._extra_axes[0].get_ylim()[1] >= 800)

    # Set X range keeps the right-hand zoom that is already pinned.
    panel._extra_axes[0].set_ylim(1, 2)
    panel._capture_user_view()
    extra_before = dict(panel._user_view[2])
    lo, hi = panel.ax.get_xlim()
    panel._user_view = ((lo, hi), panel.ax.get_ylim(), panel._user_view[2])
    panel.redraw()
    check("pinned right zoom kept", panel._user_view[2] == extra_before)

    # Back / Forward / Pan / Zoom handlers do not raise.
    try:
        panel.toolbar.push_current()
        panel.toolbar.back()
        panel.toolbar.forward()
        panel.toolbar.pan()
        panel.toolbar.pan()
        panel.toolbar.zoom()
        panel.toolbar.zoom()
        panel.reset_zoom()
        raised = None
    except Exception as e:                   # noqa: BLE001
        raised = e
    check(f"toolbar handlers run ({raised!r})", raised is None)


def main() -> int:
    QApplication.instance() or QApplication([])
    test_ceilings()
    test_settings_table()
    test_right_axis()
    print("ALL OK" if OK else "FAILED")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
