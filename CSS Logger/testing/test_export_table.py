"""
Table export / carry-forward test for the CSS Logger.

Covers the four things the export and the table both have to get right:

  * a channel written at its own pace (a rate, a timing word, a beam fate)
    never adds a row of its own — only the chosen shot channels give the table
    its moments;
  * a channel that did not change inside the window still has a value on every
    shot: the last value from BEFORE the window is held forward, exactly like
    the graph draws it (this is also what made custom PVs empty for an archived
    window);
  * a word value (a beam fate) survives the whole way into the file;
  * the table shows the channels that are on the graph — no empty column for a
    channel that is switched off or has nothing in this window.

Runs headless with the archiver mocked — no network needed.

Run:  python test_export_table.py      (prints PASS/FAIL, exits non-zero)
"""
import csv
import os
import pathlib
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import cpva_core                                             # noqa: E402
import main as app_main                                      # noqa: E402
from PySide6.QtCore import Qt                                # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

_NS = 1_000_000_000
PV_SHOT  = "L3-TEST-SHOT:Energy"       # ~3 samples/s — a real shot diagnostic
PV_SLOW  = "L3-TEST-SLOW:Energy"       # never changes inside the window
PV_RATE  = "L3-TIMING-TIMING:SysRate"  # its own cadence, says nothing about shots
PV_FATE  = "L3-TEST-BEAM:BeamFate"     # a word value

_fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        _fails.append(name)


# ── a fake archive ─────────────────────────────────────────────────────────
# Every channel has its own cadence, anchored to absolute time so the same
# sample comes back whatever slice it is asked for. Each request also returns
# the sample just before its own start, the way the real archiver does — that is
# what the export's seam guard exists for.

_CADENCE_S = {PV_SHOT: 1.0 / 3.0, PV_RATE: 7.0, PV_FATE: 13.0}
_FATES = ["ON TARGET", "DUMPED; SAFE", "BLOCKED"]


def _fake_samples(channel, a_ns, b_ns, timeout=None, count=None, **kw):
    a, b = int(a_ns), int(b_ns)
    if channel == PV_SLOW:
        return []                       # nothing at all inside any window
    step = _CADENCE_S.get(channel)
    if step is None:
        return []
    step_ns = int(step * _NS)
    k0 = a // step_ns - 1               # one sample before the start
    k1 = b // step_ns
    out = []
    for k in range(k0, k1 + 1):
        ts = k * step_ns
        if ts >= b:
            break
        if channel == PV_FATE:
            val = _FATES[k % len(_FATES)]
        elif channel == PV_RATE:
            val = 3.3
        else:
            val = 20.0 + (k % 11) * 0.25
        out.append({"time": ts, "value": val, "metaData": {"units": ""}})
    return out


def _fake_last_before(channel, before_ns, timeout=None, cancel_fn=None):
    if channel == PV_SLOW:
        return {"time": int(before_ns) - 5 * 86400 * _NS, "value": 12.5,
                "metaData": {"units": "J"}}
    got = _fake_samples(channel, int(before_ns) - 60 * _NS, int(before_ns))
    return got[-1] if got else None


def _fake_last_before_many(channels, before_ns, timeout=None, max_workers=8,
                           progress_fn=None, cancel_fn=None):
    out = {}
    for ch in channels:
        s = _fake_last_before(ch, before_ns)
        if s:
            out[ch] = s
    return out


def _install_mock():
    cpva_core.cpva_fetch_samples = _fake_samples
    cpva_core.cpva_fetch_samples_chunked = _fake_samples
    cpva_core.cpva_fetch_last_before = _fake_last_before
    cpva_core.cpva_fetch_last_before_many = _fake_last_before_many
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        if hasattr(app_main, fn):
            setattr(app_main, fn, lambda *a, **k: None)
    QMessageBox.question    = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning     = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.critical    = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)


def _new_widget():
    app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    return app_main.CSSLoggerWidget()


def _pump_until(cond, timeout_s=60.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    _app.processEvents()
    return cond()


# ── the pure pieces ────────────────────────────────────────────────────────

def test_slices():
    day = 86400 * _NS
    per_day = day // app_main._EXPORT_SLICE_NS
    sl = app_main._export_slices(0, 3 * day)
    check("a 3-day window is cut into slices", len(sl) == 3 * per_day, str(len(sl)))
    check("slices are contiguous",
          all(sl[i][1] == sl[i + 1][0] for i in range(len(sl) - 1)))
    check("the last slice includes the window end", sl[-1][1] > 3 * day)
    check("an empty window is no slices", app_main._export_slices(5, 5) == [])
    check("a slice is small enough to skip the reachability probe",
          app_main._EXPORT_SLICE_NS <= 4 * 3600 * _NS)
    year = app_main._export_slices(0, 365 * day)
    check("a year is covered", len(year) == 365 * per_day, str(len(year)))
    check("a window shorter than a slice is one slice",
          len(app_main._export_slices(0, 60 * _NS)) == 1)


def test_shot_grid_ignores_the_pace_channels():
    gap = app_main.SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
    lo, hi = 0, 10 * _NS
    samples = {
        PV_SHOT: [(int(i * _NS / 3), 20.0 + i) for i in range(30)],
        PV_RATE: [(int(i * 1.37 * _NS), 3.3) for i in range(7)],
        PV_FATE: [(int(i * 2.5 * _NS), "ON TARGET") for i in range(4)],
        PV_SLOW: [],
    }
    chans = [PV_SHOT, PV_SLOW, PV_RATE, PV_FATE]
    held = {PV_SLOW: 12.5}
    rows, held = app_main._export_slice_rows(
        samples, [PV_SHOT], chans, held, gap, lo, hi)
    check("one row per shot, none from the pace channels",
          len(rows) == 30, f"{len(rows)} rows")
    check("the shot channel keeps its own value",
          rows[5][1][PV_SHOT] == 25.0, str(rows[5][1][PV_SHOT]))
    check("a channel with no sample in the window is held from before it",
          all(r[1][PV_SLOW] == 12.5 for r in rows))
    check("a word value is paired onto every shot",
          all(isinstance(r[1][PV_FATE], str) and r[1][PV_FATE] for r in rows))
    check("the pace channel is carried, not resampled",
          rows[-1][1][PV_RATE] == 3.3)

    # Ticking the rate channel as a shot channel is the user's call and must
    # really add its moments.
    rows2, _ = app_main._export_slice_rows(
        samples, [PV_SHOT, PV_RATE], chans, {PV_SLOW: 12.5}, gap, lo, hi)
    check("a ticked pace channel does add rows", len(rows2) > len(rows),
          f"{len(rows2)} vs {len(rows)}")


def test_held_value_crosses_a_slice():
    gap = app_main.SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
    chans = [PV_SHOT, PV_SLOW]
    s1 = {PV_SHOT: [(1 * _NS, 5.0)], PV_SLOW: [(1 * _NS, 99.0)]}
    _rows, held = app_main._export_slice_rows(
        s1, [PV_SHOT], chans, {}, gap, 0, 2 * _NS)
    s2 = {PV_SHOT: [(3 * _NS, 6.0)], PV_SLOW: []}
    rows2, _held = app_main._export_slice_rows(
        s2, [PV_SHOT], chans, held, gap, 2 * _NS, 4 * _NS)
    check("the held value carries into the next slice",
          rows2 and rows2[0][1][PV_SLOW] == 99.0,
          str(rows2[0][1] if rows2 else None))


def test_cell_format():
    f = app_main._export_fmt
    check("a word value goes out as it is", f("DUMPED; SAFE", False) == "DUMPED; SAFE")
    check("nothing becomes an empty cell", f(None, False) == "")
    check("full precision, not 6 digits", f(1.2345678901, False) == "1.23456789",
          f(1.2345678901, False))
    check("decimal comma on request", f(1.5, True) == "1,5", f(1.5, True))
    check("a rounding artefact is not exported", f(0.1 + 0.2, False) == "0.3",
          f(0.1 + 0.2, False))


# ── the widget ─────────────────────────────────────────────────────────────

def _load(w, pvs, dt_from, dt_to):
    w._pv_list.clear()
    for pv in pvs:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    if w._master_multiple_edit is not None:
        w._master_multiple_edit.setText("")
    w._master_pv_edit.setText("")
    w._conditions = []
    w._dt_from, w._dt_to = dt_from, dt_to
    w._on_load_clicked(silent=True)
    ok = _pump_until(lambda: not w._load_in_flight)
    assert ok, "the load never finished"


def test_table_holds_and_customs_follow():
    _install_mock()
    w = _new_widget()
    t1 = datetime(2026, 9, 4, 12, 0, 0)
    w._custom_pvs = [{"name": "Sum", "expr": "A+B",
                      "bindings": {"A": PV_SHOT, "B": PV_SLOW}}]
    w._sync_pv_list_customs()
    _load(w, [PV_SHOT, PV_SLOW, PV_RATE, PV_FATE], t1 - timedelta(minutes=10), t1)

    rows = w._table_rows
    check("the window produced rows", bool(rows), f"{len(rows)} rows")
    first = rows[0][1]
    check("the slow channel has a value in the FIRST row",
          isinstance(first.get(PV_SLOW, (None,))[0], (int, float)),
          str(first.get(PV_SLOW)))
    check("the custom channel is computed in the first row",
          isinstance(first.get("Sum", (None,))[0], (int, float)),
          str(first.get("Sum")))
    n_empty = sum(1 for _ts, r in rows
                  if not isinstance(r.get("Sum", (None,))[0], (int, float)))
    check("the custom channel is filled in every row", n_empty == 0,
          f"{n_empty} empty of {len(rows)}")
    check("the custom channel is held from before the window too",
          isinstance(w._pre_window_vals.get("Sum", (None, None))[1], (int, float)),
          str(w._pre_window_vals.get("Sum")))
    check("the word channel reached the table",
          isinstance(first.get(PV_FATE, (None,))[0], str), str(first.get(PV_FATE)))
    w.close()


def test_table_columns_match_the_graph():
    _install_mock()
    w = _new_widget()
    t1 = datetime(2026, 9, 4, 12, 0, 0)
    w._custom_pvs = []
    w._sync_pv_list_customs()
    _load(w, [PV_SHOT, PV_SLOW, PV_RATE], t1 - timedelta(minutes=5), t1)
    cols = w._table_pvs()
    check("every loaded channel is a column", set(cols) == {PV_SHOT, PV_SLOW, PV_RATE},
          str(cols))
    w._pv_settings.setdefault(PV_RATE, {})["show"] = False
    cols = w._table_pvs()
    check("a channel switched off is not a column", PV_RATE not in cols, str(cols))
    header = [w._table_widget.horizontalHeaderItem(i).text()
              for i in range(w._table_widget.columnCount())]
    w._populate_table()
    header = [w._table_widget.horizontalHeaderItem(i).text()
              for i in range(w._table_widget.columnCount())]
    check("the table header follows", len(header) == len(cols) + 1, str(header))
    w.close()


def test_export_writes_every_shot():
    _install_mock()
    w = _new_widget()
    t1 = datetime(2026, 9, 4, 12, 0, 0)
    w._custom_pvs = [{"name": "Sum", "expr": "A+B",
                      "bindings": {"A": PV_SHOT, "B": PV_SLOW}}]
    w._sync_pv_list_customs()
    _load(w, [PV_SHOT, PV_SLOW, PV_RATE, PV_FATE], t1 - timedelta(minutes=2), t1)

    path = str(pathlib.Path(tempfile.gettempdir()) / "cssl_export_test.csv")
    cfg = {"channels": [PV_SHOT, PV_SLOW, PV_RATE, PV_FATE, "Sum"],
           "basis": [PV_SHOT], "source": "archive",
           "conditions": False, "comma": False}
    w._export_from_archive(path, cfg)
    ok = _pump_until(lambda: not w._export_prog.isVisible(), 120)
    check("the export finished", ok)

    with open(path, newline="", encoding="utf-8-sig") as f:
        first = f.readline()
        rd = list(csv.reader(f, delimiter=";"))
    check("Excel separator line", first.strip() == "sep=;", first.strip())
    head, body = rd[0], rd[1:]
    check("one column per chosen channel", len(head) == 6, str(head))
    # Two minutes of a 3 Hz channel.
    check("about every shot is written", 340 <= len(body) <= 380, f"{len(body)} rows")
    check("every row is complete", all(len(r) == 6 for r in body))
    i_slow = head.index(app_main.shorten_pv_name(PV_SLOW))
    i_fate = head.index(app_main.shorten_pv_name(PV_FATE))
    i_sum  = head.index("Sum")
    check("the held channel is filled in every row",
          all(r[i_slow] not in ("", None) for r in body), body[0][i_slow])
    check("the word value is in every row",
          all(r[i_fate] in _FATES for r in body), body[0][i_fate])
    check("a word value with a semicolon stays one cell",
          any(r[i_fate] == "DUMPED; SAFE" for r in body))
    check("the custom channel is exported",
          all(r[i_sum] not in ("", None) for r in body), body[0][i_sum])
    ts = [r[0] for r in body]
    check("timestamps are unique and rising", len(set(ts)) == len(ts))
    w.close()


def test_export_multiple_slices_has_no_seam_duplicates():
    _install_mock()
    w = _new_widget()
    # A whole day, so every slice seam inside it is exercised. Only the shot
    # channel is read, to keep the run short.
    t1 = datetime(2026, 9, 4, 0, 0, 0)
    w._custom_pvs = []
    w._sync_pv_list_customs()
    _load(w, [PV_SHOT], t1 - timedelta(minutes=1), t1)
    w._dt_from, w._dt_to = t1 - timedelta(days=1), t1
    path = str(pathlib.Path(tempfile.gettempdir()) / "cssl_export_seam.csv")
    cfg = {"channels": [PV_SHOT], "basis": [PV_SHOT], "source": "archive",
           "conditions": False, "comma": False}
    w._export_from_archive(path, cfg)
    ok = _pump_until(lambda: not w._export_prog.isVisible(), 600)
    check("the multi-slice export finished", ok)
    with open(path, newline="", encoding="utf-8-sig") as f:
        f.readline()
        rd = list(csv.reader(f, delimiter=";"))
    body = rd[1:]
    ts = [r[0] for r in body]
    check("no duplicate row at a slice seam", len(set(ts)) == len(ts),
          f"{len(ts) - len(set(ts))} duplicates")
    # A day of a 3 Hz channel, to the second.
    check("a whole day of shots is there", len(body) > 250_000,
          f"{len(body)} rows")
    w.close()


if __name__ == "__main__":
    test_slices()
    test_shot_grid_ignores_the_pace_channels()
    test_held_value_crosses_a_slice()
    test_cell_format()
    test_table_holds_and_customs_follow()
    test_table_columns_match_the_graph()
    test_export_writes_every_shot()
    test_export_multiple_slices_has_no_seam_duplicates()
    print()
    if _fails:
        print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
        sys.exit(1)
    print("all checks passed")
