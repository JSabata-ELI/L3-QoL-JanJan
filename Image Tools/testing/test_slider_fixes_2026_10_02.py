"""Checks for the Image Slider fixes of 2 Oct 2026.

    python testing/test_slider_fixes_2026_10_02.py

Offscreen — nothing appears on screen. Covers:

  1. Pointing analysis sorts the worker pool's results by time; since 05.10.2026
     the graph measures from the MEDIAN shot (it was the first shot before).
  2. Draw mode: arming a mode only selects the last mark on the view that has the
     keyboard, and there Delete really removes it. The Draw buttons never take the
     focus.
  3. The rotate knob's stalk does not paint over the cross's upper arm.
  4. While Overlay settings is open every mark carries its number tag.
  5. PV overlay: a value from an older shot gets a dot on its own row, and the
     panel is exactly as wide with the dot as without it.
  6. Pointing analysis shows a progress bar while it runs, hidden afterwards.
  7. Timeline & Range: frame arrows, Play, Stop and Speed share one row; the two pin
     buttons sit either side of Save Timestamp; no words on Play / Stop.
  8. Rebuilding the camera grid leaves no parentless (top-level) camera tiles behind.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_common as B                                             # noqa: E402

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_is_fix1002_")

m = B.load_slider()
from PySide6.QtCore import Qt, QRect, QPointF, QEvent              # noqa: E402
from PySide6.QtGui import QImage, QPainter, QColor, QKeyEvent       # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                 # noqa: E402

FAILS: "list[str]" = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILS.append(what)


app = QApplication.instance() or QApplication(sys.argv)


def make_viewer():
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v.resize(1400, 900)
    v.show()
    app.processEvents()
    return v


# ── 1. pointing from the median shot ─────────────────────────────────────────
print("1. pointing reference = median shot")
v = make_viewer()
v._pointing_queue = [{"cam_idx": 0, "cam_name": "CAM", "cam_key": "CAM",
                      "items": [], "n_sampled": 3}]
v._pointing_sets = []
v._pointing_skipped = []
v._pointing_m = 1.0
v._pointing_run_done = 0
v._start_next_pointing_cam = lambda: None          # stop after this one camera
# Out of time order, as the worker pool hands them back.
v._on_pointing_finished([(300, 14.0, -2.0, 100, 80),
                         (100, 10.0, 5.0, 100, 80),
                         (200, 12.0, 1.0, 100, 80)])
s = v._pointing_sets[-1]
check(list(s["ts_int"]) == [100, 200, 300], f"sorted by time ({list(s['ts_int'])})")
# Since 05.10.2026 the zero is the MEDIAN shot: the set keeps the offsets from the
# sensor centre and the graph measures from the median.
check(list(s["cx"]) == [10.0, 12.0, 14.0] and list(s["cy"]) == [5.0, 1.0, -2.0],
      f"the set keeps the measured offsets ({list(s['cx'])}, {list(s['cy'])})")
v.pointing_panel.plot(s["cx"], s["cy"], s["n"], ts_ns=s["ts"], ts_ns_int=s["ts_int"],
                      img_w=s["img_w"], img_h=s["img_h"], sensor_c=s["sensor_c"])
pp = v.pointing_panel
check(list(pp._raw_cx) == [-2.0, 0.0, 2.0] and list(pp._raw_cy) == [4.0, 0.0, -3.0],
      f"the median shot sits at 0,0 ({list(pp._raw_cx)}, {list(pp._raw_cy)})")
check("median" in pp._axis_label("X"), f"axis label says so ({pp._axis_label('X')!r})")

# ── 6. progress bar ──────────────────────────────────────────────────────────
print("6. pointing progress bar")
check(not v.pointing_progress.isVisible(), "hidden while nothing runs")
v._pointing_queue = [{"cam_name": "PAP1DF", "items": [None] * 10, "n_sampled": 10}]
v._pointing_total_cams = 1
v.pointing_progress.setRange(0, 10)
v.pointing_progress.setVisible(True)
v._pointing_run_done = 0
v._on_pointing_progress(4, 10)
check(v.pointing_progress.value() == 4, f"value follows progress ({v.pointing_progress.value()})")
check("%p" in v.pointing_progress.format(), f"shows a percentage ({v.pointing_progress.format()!r})")
v._pointing_sets = []
v._pointing_queue = []
v._finish_pointing_run()
check(not v.pointing_progress.isVisible(), "hidden again when the run ends")

# ── 7. timeline layout ───────────────────────────────────────────────────────
print("7. Timeline & Range rows")
for c in v.findChildren(QWidget):
    if hasattr(c, "set_expanded"):
        c.set_expanded(True)
app.processEvents()


def mid(w):
    return w.mapTo(v, w.rect().center()).y()


row = [v.btn_prev, v.btn_next, v.btn_play, v.btn_stop, v.speed_cb]
check(max(mid(w) for w in row) - min(mid(w) for w in row) <= 4,
      "arrows, Play, Stop and Speed share one row")
check(v.btn_play.text() == "" and v.btn_stop.text() == "", "Play / Stop carry no words")
check(not v.btn_play.icon().isNull() and not v.btn_stop.icon().isNull(),
      "Play / Stop carry painted icons")
ts = [v.btn_prev_ts, v.btn_next_ts, v.btn_save_ts]
check(max(mid(w) for w in ts) - min(mid(w) for w in ts) <= 4,
      "pin buttons share the Save Timestamp row")
gx = [w.mapTo(v, w.rect().topLeft()).x() for w in ts]
# 05.10.2026: the two pins side by side, then Save Timestamp.
check(gx[0] < gx[1] < gx[2], f"order: previous pin, next pin, Save ({gx})")

# ── 2. draw focus / delete ───────────────────────────────────────────────────
print("2. Draw: selection only where Delete reaches")
iv = v.img_view
check(v.btn_draw_cross.focusPolicy() == Qt.FocusPolicy.NoFocus,
      "Draw button does not take the focus")
iv.marks = m.new_marks()
iv.marks["cross"] = [(0.3, 0.3), (0.6, 0.6)]
iv.clearFocus()
app.processEvents()
iv.set_draw_mode("cross")
check(iv._sel is None, "no focus → nothing is shown as selected")
iv.set_draw_mode("")
v._overlay_targets = lambda: [iv]
v._toggle_draw_mode("cross")
app.processEvents()
check(iv.hasFocus(), "arming Draw hands the keyboard to the picture")
check(iv._sel == ("cross", 1), f"…and the last cross is selected there ({iv._sel})")
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
iv.keyPressEvent(ev)
check(len(iv.marks["cross"]) == 1, f"Delete removes it ({len(iv.marks['cross'])} left)")
v._toggle_draw_mode("cross")
del v._overlay_targets

# ── 3. rotate stalk vs the cross arm ─────────────────────────────────────────
print("3. rotate stalk leaves the arm alone")
style = dict(m.MARK_STYLE_DEFAULTS)
style["cross_outline"] = False
img = QImage(200, 200, QImage.Format.Format_ARGB32)
img.fill(QColor(0, 0, 0))
p = QPainter(img)
m.draw_marks(p, QRect(0, 0, 200, 200), {"cross": [(0.5, 0.5)], "circle": [], "square": []},
             style, draw_mode="cross", selected=("cross", 0), handle_radius=6)
p.end()
cs = int(style["cross_size"])
arm = [img.pixelColor(100, 100 - k) for k in range(3, cs - 1)]
greenish = sum(1 for c in arm if c.green() > 150 and c.red() < 120 and c.blue() < 120)
check(greenish == len(arm), f"upper arm stays the cross colour ({greenish}/{len(arm)} px)")
stalk = [img.pixelColor(100, 100 - k) for k in range(cs + 8, cs + 16)]
check(any(c.red() > 150 and c.green() > 150 and c.blue() > 150 for c in stalk)
      or any(c.lightness() < 40 for c in stalk), "the stalk is still drawn above the box")

# ── 4. number tags ───────────────────────────────────────────────────────────
print("4. number tags while Overlay settings is open")


def render(numbers):
    im = QImage(200, 200, QImage.Format.Format_ARGB32)
    im.fill(QColor(0, 0, 0))
    pp = QPainter(im)
    m.draw_marks(pp, QRect(0, 0, 200, 200),
                 {"cross": [(0.3, 0.5), (0.7, 0.5)], "circle": [], "square": []},
                 style, numbers=numbers)
    pp.end()
    return im


plain, tagged = render(False), render(True)
white = sum(1 for x in range(200) for y in range(200)
            if tagged.pixelColor(x, y).lightness() > 200
            and plain.pixelColor(x, y).lightness() < 100)
check(white > 40, f"tags painted beside the crosses ({white} white px)")
check(render(False) == plain, "no tags when the window is closed")

# ── 5. PV overlay dot ────────────────────────────────────────────────────────
print("5. PV overlay: one dot per value state, constant width")
host = QWidget()
host.resize(500, 300)
ov = m._PvOverlayPanel(host)
rows = [("SBW4", "12.95 J"), ("GDD", "-1240 fs2"), ("PTM1", "")]
ov.update_values(rows)
host.show()
app.processEvents()
w0, h0 = ov.width(), ov.height()
ov.update_values(rows, states={"GDD": m.PV_STATE_HELD, "PTM1": m.PV_STATE_LOADING})
app.processEvents()
check((ov.width(), ov.height()) == (w0, h0),
      f"same size with the dots ({ov.width()}x{ov.height()} vs {w0}x{h0})")
html = ov._content.text()
check(html.count(m.PV_DOT_COLORS[m.PV_STATE_HELD]) == 1, "exactly one orange dot")
check(html.count(m.PV_DOT_COLORS[m.PV_STATE_LOADING]) == 1, "exactly one red dot")
check(all(w not in html for w in ("older shot", "no data yet", "n/a", "ERR")),
      "no status words anywhere")
ov.update_values(rows, states={"SBW4": m.PV_STATE_NONE})
app.processEvents()
check(ov._content.text().count(m.PV_DOT_COLORS[m.PV_STATE_NONE]) == 1, "grey dot")
ov.update_values(rows)
app.processEvents()
check((ov.width(), ov.height()) == (w0, h0), "same size after the dots go")
check(m.pv_value_number_text("no data yet") == "" and m.pv_value_number_text("n/a")
      == "" and m.pv_value_number_text("12.95 J") == "12.95 J",
      "no-number words are not shown")

# ── 8. no orphan tiles ───────────────────────────────────────────────────────
print("8. grid rebuild leaves no top-level tiles")
grid = v._multi_grid
grid.setup_cameras(["CAM_A", "CAM_B", "CAM_C"])
app.processEvents()
grid.setup_cameras(["CAM_A", "CAM_D"])
grid._rebuild_grid()
app.processEvents()
from PySide6.QtCore import QCoreApplication                          # noqa: E402
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
app.processEvents()
tops = [w for w in app.topLevelWidgets()
        if isinstance(w, (m.CameraView, m._AutoLayoutContainer, m._FreeLayoutContainer))]
check(not tops, f"no parentless camera tiles or containers ({len(tops)})")
check(len(grid._cam_views) == 2 and all(cv.isVisibleTo(grid) for cv in grid._cam_views),
      "the live tiles are shown")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("all checks passed")
sys.exit(0)
