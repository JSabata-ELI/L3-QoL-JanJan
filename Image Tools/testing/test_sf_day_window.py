"""The Shot Finder's day window, and the search's time estimate.

What is pinned here, and why each of them was wrong before:

  * **The time window opens on the shift**, 07:00-21:00 (the house default, see
    daypicker.DEFAULT_FROM_HOUR). It used to open on the whole calendar day, so every
    search read hours in which nothing is ever shot.
  * **The day's curve holds its value.** A PV is what the archive last wrote until
    something new is written, so the line is flat and then steps (`steps-post`, the
    same as the PV Search window). Sloping from one sample to the next draws values
    that were never measured — a long gap came out as a diagonal across the graph.
  * **The last column is the frame's OWN time**, in Prague time and in the same shape
    as the Prague Time column, so the two can be read against each other. It used to
    print the whole file name, camera and all, which is why the column took half the
    window.
  * **Every listed shot is named without being asked.** The column is filled by a
    background pass that starts at the row the operator is looking at and wraps round;
    a row the share has already answered "no frame" for is never asked twice; and a
    pass belonging to a day that has been closed writes nothing.
  * **The time left is only shown when the job has measured it.** The old bar drew one
    straight line through two halves of very different speed and printed a number that
    jumped from minutes to seconds and back.

Nothing here touches the share or the archiver: the frame resolver and the preview
loader are both replaced, so the test says nothing about whether this machine can see
the facility. The measuring of the day durations itself lives inside the search worker
and is exercised by a real search, not here.

    python testing/test_sf_day_window.py
"""
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication          # noqa: E402

import sf_t                                          # noqa: E402

FAILURES: "list[str]" = []

DAY = date(2026, 9, 2)
CAM = "C02-101-SHG-NF"
COL = "sbw4"
N_ROWS = 10


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def rows(n=N_ROWS):
    """n shots, a quarter of an hour apart, with real unix-ns stamps."""
    out = []
    for i in range(n):
        dt = datetime(DAY.year, DAY.month, DAY.day, 8 + i // 4, 15 * (i % 4))
        ns = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        out.append({"_dt": dt, "_ns": ns, COL: f"{10.0 + 0.03 * i:.3f}"})
    return out


def result(rr):
    return {"day": DAY, "cam": CAM, "status": "ok",
            "best_row": rr[3], "rows_in_tol": rr,
            "col": COL, "actual": 10.09, "diff": 0.09, "target_csv": 10.0,
            "hour_folder": None, "per_col": {COL: rr},
            "search_cols": [COL], "extra_cols": [],
            "criteria_csv": [{"col": COL, "target_csv": 10.0, "tol_ui": 1.0}],
            "col_meta": {COL: {"source": "api", "status": "ok"}},
            "img_path": None, "folder_path": None, "display_vals": {}, "reason": ""}


def frame_path(ns: int) -> Path:
    """A file named the way the archive names them, so its own stamp can be read back
    out of the name — which is what the Frame time cell prints."""
    return Path(f"//share/2026/9/2/6/{CAM}/{CAM}-_-IMG_-_{ns}.png")


app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")
app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                  "QLabel { background: transparent; }")

w = sf_t.ShotFinderWidget()

# ── the share and the frame reader, replaced ──────────────────────────────────
# Every frame is answered from the row's own timestamp, five milliseconds late, so a
# real reading of the share never happens and the printed time is predictable. ASKED
# records the order the rows were asked in; SKIP is answered with "there is no frame".
ASKED: "list[int]" = []
SKIP: "set[int]" = set()
NS_TO_ROW: dict = {}


def fake_find(dr, cam, dt_obj, ts_ns, hour_cache, scan_cache=None):
    row_idx = NS_TO_ROW.get(int(ts_ns or 0), -1)
    ASKED.append(row_idx)
    if row_idx in SKIP:
        return None
    return frame_path(int(ts_ns) + 5_000_000)


w._find_image_for_shot = fake_find
w._load_and_show_preview = lambda *a, **k: None
w.resize(1400, 800)
w.show()
app.processEvents()

# ── 1. the default time window ────────────────────────────────────────────────
print("\nTime window")
check("opens on the shift, not the calendar day", w._tw_times == (7, 0, 21, 0),
      f"got {w._tw_times}")
wins = w._tw_windows
span_h = (wins[0][1] - wins[0][0]) / 3.6e12 if wins else 0
check("14 hours wide", abs(span_h - 14.0) < 1e-6, f"got {span_h:.3f} h")
check("one window for the one picked day", len(wins) == 1, f"got {len(wins)}")

# ── open the day ──────────────────────────────────────────────────────────────
rr = rows()
NS_TO_ROW = {int(r["_ns"]): i for i, r in enumerate(rr)}
w._rebuild_result_tabs([CAM])
w._on_day_result(result(rr))
app.processEvents()
w._table.selectRow(0)
w._on_table_double_clicked(w._table.model().index(0, 0))
for _ in range(6):
    app.processEvents()

# ── 2. the columns ────────────────────────────────────────────────────────────
print("\nColumns")
tbl = w._day_table
heads = [tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount())]
check("last column is the frame's time", heads[-1] == "Frame time", f"got {heads}")
check("the camera name is nowhere in the columns",
      not any(CAM in h for h in heads), f"got {heads}")

# ── 3. what one cell says ─────────────────────────────────────────────────────
print("\nOne cell")
ns0 = int(rr[0]["_ns"]) + 5_000_000
p0 = frame_path(ns0)
w._day_set_image_cell(0, p0)
cell = tbl.item(0, w._day_img_col)
want = sf_t._ns_to_prague(ns0).strftime("%H:%M:%S.%f")[:-3]
check("prints the frame's own Prague time", cell.text() == want,
      f"got {cell.text()!r}, wanted {want!r}")
check("shaped like the Prague Time column",
      len(cell.text()) == len(tbl.item(0, 0).text()),
      f"{cell.text()!r} vs {tbl.item(0, 0).text()!r}")
check("the whole path is still remembered",
      cell.data(sf_t.Qt.ItemDataRole.UserRole) == str(p0))
check("and shown in the bubble", str(p0) in cell.toolTip())
w._day_set_image_cell(0, None, "no frame")
check("a shot with no frame is remembered as asked-and-empty",
      tbl.item(0, w._day_img_col).data(sf_t.Qt.ItemDataRole.UserRole) == "",
      f"got {tbl.item(0, w._day_img_col).data(sf_t.Qt.ItemDataRole.UserRole)!r}")

# ── 4. the background fill ────────────────────────────────────────────────────
print("\nFilling the column by itself")


def clear_cells():
    for r in range(tbl.rowCount()):
        it = tbl.item(r, w._day_img_col)
        it.setText("")
        it.setData(sf_t.Qt.ItemDataRole.UserRole, None)


def wait_for_fill(rows_wanted: int, timeout_s: float = 5.0) -> int:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        app.processEvents()
        filled = sum(1 for r in range(tbl.rowCount())
                     if tbl.item(r, w._day_img_col).data(
                         sf_t.Qt.ItemDataRole.UserRole) is not None)
        if filled >= rows_wanted:
            return filled
    return sum(1 for r in range(tbl.rowCount())
               if tbl.item(r, w._day_img_col).data(
                   sf_t.Qt.ItemDataRole.UserRole) is not None)

clear_cells()
ASKED.clear()
tbl.selectRow(6)
app.processEvents()
clear_cells()                      # the selection fills its own row first
ASKED.clear()
w._start_day_prefill(w._day_dr, w._day_fill_gen)
filled = wait_for_fill(N_ROWS)
check("every shot is named without being clicked", filled == N_ROWS,
      f"got {filled}/{N_ROWS}")
check("starts where the operator is looking and wraps round",
      ASKED[:N_ROWS] == list(range(6, N_ROWS)) + list(range(0, 6)),
      f"got {ASKED[:N_ROWS]}")
check("the times are the frames' own",
      tbl.item(2, w._day_img_col).text()
      == sf_t._ns_to_prague(int(rr[2]["_ns"]) + 5_000_000).strftime(
          "%H:%M:%S.%f")[:-3],
      f"got {tbl.item(2, w._day_img_col).text()!r}")

clear_cells()
ASKED.clear()
w._day_set_image_cell(4, None, "no frame")     # already asked, and there is none
w._start_day_prefill(w._day_dr, w._day_fill_gen)
wait_for_fill(N_ROWS)
check("a shot already known to have no frame is not asked about again",
      4 not in ASKED, f"asked {ASKED}")

clear_cells()
ASKED.clear()
w._start_day_prefill(w._day_dr, w._day_fill_gen - 1)   # a day already closed
for _ in range(8):
    app.processEvents()
check("a pass belonging to a closed day does nothing", ASKED == [],
      f"asked {ASKED}")

# ── 5. the curve holds its value ──────────────────────────────────────────────
print("\nThe day's curve")
short = w._col_short(COL)
curves = [ln for ln in w._day_ax.get_lines() if ln.get_label() == short]
check("the searched PV is drawn", len(curves) == 1, f"got {len(curves)}")
if curves:
    check("held until the next sample, never sloped towards it",
          curves[0].get_drawstyle().endswith("post"),
          f"got {curves[0].get_drawstyle()!r}")
dots = [ln for ln in w._day_ax.get_lines() if ln.get_label() == "in range"]
check("the shots in range are still dots, not a line", bool(dots) and
      dots[0].get_linestyle() in ("None", "none", ""),
      f"got {dots[0].get_linestyle()!r}" if dots else "no dots")

# ── 5b. the time labels stand upright ─────────────────────────────────────────
print("\nThe time labels")
rots = sorted({round(t.get_rotation()) for t in w._day_ax.get_xticklabels()})
check("upright, never at an angle", rots in ([0], []), f"got {rots}")
w._day_canvas.resize(900, 300)
w._retick_day_xaxis()
wide = len(w._day_ax.get_xticks())
w._day_canvas.resize(320, 300)
w._retick_day_xaxis()
narrow = len(w._day_ax.get_xticks())
check("fewer labels when the curve is dragged narrow, not overlapping ones",
      narrow < wide, f"{wide} labels at 900 px, {narrow} at 320 px")
check("the axes fill the pane instead of sitting in a white frame",
      w._day_fig.get_layout_engine() is not None,
      f"layout engine {type(w._day_fig.get_layout_engine()).__name__}")

# ── 5c. the slider under the curve ────────────────────────────────────────────
print("\nThe shot slider")
sl = w._day_slider
check("one step per listed shot", (sl.minimum(), sl.maximum()) == (0, N_ROWS - 1),
      f"got {sl.minimum()}..{sl.maximum()}")
check("the wheel walks the shots without a click first",
      bool(sl.property("wheelAlways")))
moved = []
for want in (3, 7, 0, N_ROWS - 1):
    sl.setValue(want)
    app.processEvents()
    moved.append(tbl.currentRow())
check("moving it picks that shot in the list", moved == [3, 7, 0, N_ROWS - 1],
      f"got {moved}")
back = []
for want in (5, 2):
    tbl.selectRow(want)
    app.processEvents()
    back.append(sl.value())
check("and a row picked in the list moves the handle", back == [5, 2],
      f"got {back}")
w._select_day_shot_by_ns(int(rr[8]["_ns"]))
app.processEvents()
check("a click on the curve moves both", (tbl.currentRow(), sl.value()) == (8, 8),
      f"got row {tbl.currentRow()}, handle {sl.value()}")

# ── 5d. a frame is read once, then it is in memory ────────────────────────────
print("\nFrames kept in memory")
RENDERED: "list[str]" = []


def fake_render(path, *a, **k):
    from PySide6.QtGui import QImage
    RENDERED.append(str(path))
    img = QImage(8, 8, QImage.Format.Format_Grayscale8)
    img.fill(0)
    return img, "note", {"contrast": 0}


real_loader = w._load_and_show_preview
w._load_and_show_preview = sf_t.ShotFinderWidget._load_and_show_preview.__get__(w)
w._render_preview_frame = fake_render
w._preview_cache.clear()
path3 = str(frame_path(int(rr[3]["_ns"]) + 5_000_000))
args = ("", False, None, 0, 0)
w._load_and_show_preview(Path(path3), "", w._preview_gen, *args)
first = len(RENDERED)
w._load_and_show_preview(Path(path3), "", w._preview_gen, *args)
check("the same frame is read off the share once", len(RENDERED) == first == 1,
      f"read {len(RENDERED)} time(s)")
check("and it is handed back with its own scale note",
      w._preview_cache.get(w._preview_key(path3, args))[1] == "note")
w._load_and_show_preview(Path(path3), "", w._preview_gen, "Iron", False, None, 0, 0)
check("a changed display setting is a different frame, not a stale one",
      len(RENDERED) == 2, f"read {len(RENDERED)} time(s)")

# Reading ahead: only rows whose frame is already named, and while the handle is
# being dragged only the ones AHEAD of it.
w._start_day_prefill(w._day_dr, w._day_fill_gen)   # names every frame again
wait_for_fill(N_ROWS)
tbl.selectRow(4)


def settle():
    """Let every read already under way finish, so what follows is measured on its
    own — the naming of the column queues reads of its own as the names land."""
    for _ in range(40):
        app.processEvents()
        w._preview_pool.waitForDone(3000)
    w._day_prefetch_timer.stop()


settle()
RENDERED.clear()
w._preview_cache.clear()
w._day_scrubbing = True
w._day_step_dir = 1
w._run_preview_prefetch()
w._preview_pool.waitForDone(3000)
ahead = sorted({NS_TO_ROW.get(int(sf_t._ts_from_stem(Path(p)) or 0) - 5_000_000, -1)
                for p in RENDERED})
check("while dragging, the shots ahead of the handle are read first",
      len(ahead) >= 4 and min(ahead) > 4, f"read rows {ahead}")
settle()
RENDERED.clear()
w._preview_cache.clear()
w._day_scrubbing = False
w._run_preview_prefetch()
w._preview_pool.waitForDone(3000)
both = sorted({NS_TO_ROW.get(int(sf_t._ts_from_stem(Path(p)) or 0) - 5_000_000, -1)
               for p in RENDERED})
check("standing still, the shots on both sides are read",
      bool([r for r in both if r < 4]) and bool([r for r in both if r > 4]),
      f"read rows {both}")
w._load_and_show_preview = real_loader

# ── 6. the time left is only said when it is known ────────────────────────────
print("\nThe time estimate")
w._prog_take("search", 10, "starting…")
w._on_progress(3.0)
check("nothing is promised before the job has measured anything",
      "left" not in w._prog.format(), f"got {w._prog.format()!r}")
w._on_eta(150.0)
check("minutes once it has", "~3 min left" in w._prog.format(),
      f"got {w._prog.format()!r}")
w._on_eta(20.0)
check("seconds when there are few", "~20 s left" in w._prog.format(),
      f"got {w._prog.format()!r}")
w._on_eta(-1.0)
check("and it goes quiet again when the job stops knowing",
      "left" not in w._prog.format(), f"got {w._prog.format()!r}")
w._prog_release("search")
w._prog_take("cameras", 24, "scanning…")
w._on_progress(6.0)
check("the camera scan, which never measures, says nothing",
      "left" not in w._prog.format() and "6/24" in w._prog.format(),
      f"got {w._prog.format()!r}")

print()
if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print(f"  · {f}")
    sys.exit(1)
print("all good")
