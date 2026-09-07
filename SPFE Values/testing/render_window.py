"""
Open the window with made-up data and save a picture of it.

For checking how it looks, which is the only way to check it: an offscreen Qt
has no fonts and lies about text size, so this insists on the real Windows
platform plugin.

Everything lands in a temporary folder -- APPDATA is redirected, so the real
log on this PC is untouched, and the share is never contacted.

Run:  python testing/render_window.py [out.png] [--size=1000x640]
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
_TMP = tempfile.mkdtemp(prefix="spfe_render_")
os.environ["APPDATA"] = _TMP

import spfe_store as store                                    # noqa: E402
from spfe_store import (                                      # noqa: E402
    SLOT_EVENING, SLOT_MORNING, SLOT_NOW, Record, TZ_PRAGUE,
)

# The real scratch share must never see made-up data. Point the candidates at a
# folder inside the temporary directory instead, so the sharing half of the
# program is still exercised end to end.
_FAKE_SHARE = Path(_TMP) / "share"
_FAKE_SHARE.mkdir(parents=True, exist_ok=True)
store.SHARE_CANDIDATES = (str(_FAKE_SHARE),)


def seed():
    """Three weeks of plausible mornings and evenings, plus one bad number
    today so the amber cell can be looked at."""
    fields = store.load_fields()
    rows = []
    today = date.today()
    start = today - timedelta(days=21)
    i = 0
    d = start
    while d < today:
        if d.weekday() < 5:
            rows.append(Record(
                when=datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ_PRAGUE),
                slot=SLOT_MORNING,
                values={
                    "pumplaser_dac": 24500,
                    "humidity": 41.0 + (i % 4) * 0.3,
                    "osc_power": 634 + i % 5,
                    "osc_bandwidth": 91 + i % 3,
                    "ba1loop4_1": -367 + i % 6,
                    "ba1loop4_2": 605 + i % 4,
                    "dazz1": 15,
                    "dazz2": 60,
                    "ba2loop2_1": 136 + i % 5,
                    "ba2loop2_2": -276 + i % 7,
                    "ba2loop2_3": 2500 + i * 3,
                    "ba2loop4_1": 1200 + i % 9,
                    "ba2loop4_2": -900 + i % 6,
                    "ba2loop4_3": 10000 + i * 5,
                }))
            rows.append(Record(
                when=datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ_PRAGUE),
                slot=SLOT_EVENING,
                values={
                    "pumplaser_dac": 24500,
                    "humidity": 43.0 + (i % 3) * 0.2,
                    "osc_power": 628 + i % 4,
                    "osc_bandwidth": 90 + i % 3,
                    "ba1loop4_1": -370 + i % 5,
                    "ba1loop4_2": 601 + i % 4,
                }))
            i += 1
        d += timedelta(days=1)

    # Today: an ordinary morning, one number badly off, and a typed note.
    rows.append(Record(
        when=datetime(today.year, today.month, today.day, 9, 0, tzinfo=TZ_PRAGUE),
        slot=SLOT_MORNING,
        values={
            "pumplaser_dac": 24500,
            "humidity": 41.6,
            "osc_power": 212,                 # this is the one that must go amber
            "osc_bandwidth": 92,
            "ba1loop4_1": -367,
            "ba1loop4_2": 605,
            "dazz1": 15,
            "dazz2": 60,
            "ba2loop2_1": 136,
            "ba2loop2_2": -276,
            "ba2loop2_3": 2500,
            "ba2loop4_1": 1200,
            "ba2loop4_2": -900,
            "ba2loop4_3": 10000,
            "spider_e5": -917,
            "spider_orig": 333,
            "gdd_baseline": 22710,
            "gdd_electrons": 24300,
            "gdd_plasma": 23700,
            "energy": 11.4,
            "notes": ("We had to hard reset the oscillator in the morning as we "
                      "did not achieve good values. We moved the Ti:Sa to -2 and "
                      "made movements on the green."),
        }))
    rows.append(Record(
        when=datetime(today.year, today.month, today.day, 14, 32, tzinfo=TZ_PRAGUE),
        slot=SLOT_NOW,
        values={"pumplaser_dac": 24500, "humidity": 42.1,
                "osc_power": 631, "osc_bandwidth": 92,
                "ba1loop4_1": -365, "ba1loop4_2": 607}))

    store.append_csv(store.local_dir() / store.CSV_NAME, rows, fields.columns)

    # A campaign, so the day band can be looked at with something in it. Two
    # weeks back, so today is showing a CARRIED name -- the grey case. And a
    # LONG one, sixty characters, because that is the length that used to be
    # cut off after a third of it.
    store.write_json_map(
        store.local_dir() / store.CAMPAIGNS_NAME, "days",
        {(today - timedelta(days=14)).isoformat():
            {"name": "77 Borghesi / E5 ELI70157 Spadova / electrons and plasma",
             "set_at": "2026-09-01 08:00:00", "by": "render"}})

    # References, chosen so that today's morning shows one of each: a number
    # well inside its range, one within 5 % of an edge (yellow), and one
    # outside it altogether (red, white text, black outline). Those three
    # cells are the whole point of the picture.
    store.write_json_map(
        store.local_dir() / store.REFERENCES_NAME, "limits", {
            "pumplaser_dac": {"min": 24000, "max": 25000},   # 24500: comfortable
            "humidity":      {"min": 30, "max": 42},         # 41.6: nearly out
            "osc_power":     {"min": 600, "max": 660},       # 212: out
            "ba2loop2_3":    {"min": 2000, "max": 2520},     # 2500: nearly out
            "ba2loop4_3":    {"min": 8000, "max": 9500},     # 10000: out
            "gdd_baseline":  {"min": 22000, "max": 23000},   # 22710: comfortable
        })
    print(f"seeded {len(rows)} moments into {store.local_dir()}")


def main():
    seed()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from spfe_t import SPFEValuesWidget

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else HERE / "_window.png")

    # --size 1000x640 checks the smallest window the program allows: that is
    # where a paired button loses its label and a group box overflows.
    size = (1400, 880)
    for a in sys.argv[1:]:
        if a.startswith("--size="):
            wide, _, high = a.split("=", 1)[1].partition("x")
            size = (int(wide), int(high))

    app = QApplication(sys.argv)
    w = SPFEValuesWidget()
    w.resize(*size)
    w.show()

    shots = []
    state = {"waited": 0}

    def grab(widget, name: str):
        app.processEvents()
        path = out.with_name(out.stem + name + out.suffix)
        widget.grab().save(str(path))
        print(f"saved {path}")
        shots.append(path)

    def finish():
        # Wait for the start-up job, so the buttons are shot in their enabled
        # state rather than greyed out mid-run.
        if w._working and state["waited"] < 40:
            state["waited"] += 1
            QTimer.singleShot(500, finish)
            return

        # The page itself. There is no tab bar any more: the two other pictures
        # are the windows the toolbar opens.
        grab(w, "")

        w._open_view_day()
        app.processEvents()
        grab(w._day_window, "_viewday")
        w._day_window.hide()

        w._open_references()
        app.processEvents()
        grab(w._refs_window, "_references")
        w._refs_window.hide()

        w._open_log()
        app.processEvents()
        grab(w._log_window, "_log")
        w._log_window.hide()

        w.shutdown()
        app.quit()

    QTimer.singleShot(1200, finish)
    app.exec()
    return 0 if shots else 1


if __name__ == "__main__":
    sys.exit(main())
