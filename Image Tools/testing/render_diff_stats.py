"""The subtraction statistics: the numbers, and the per-camera histograms under them.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_diff_stats.py

The frames and their references are synthetic, but the numbers come out of the real
_apply_reference_diff, so what is drawn is what the INFO panel would show.

What to check on the picture (it is 275 px wide, the real INFO column):

  * the single-camera block says how many pixels differ, what share of the frame that
    is, the average and brightest difference, and how many pixels reach each level;
  * EVERY camera has its own histogram, captioned with its own name and numbers —
    not one histogram for the whole grid;
  * the histogram axis is numbered with more than just 0 and 255, the tallest bar is
    labelled with the count it stands for ("top"), and the red line marks the
    brightest pixel;
  * the difference itself is written INSIDE the picture — max (red), mean (blue) and
    min (purple) — and a narrow one drops min, then mean, rather than overlapping;
  * nothing is white-on-white and nothing spills out.

Writes diff_stats.png beside this file.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from bench_common import load_slider
from render_save_view_dialog import app_stylesheet

fails: list = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


def _frame(m, peak: float, smear: int):
    """A frame with one bright spot and a faint smear, and the stats against a blank
    reference."""
    import numpy as np
    from PySide6.QtGui import QImage
    h, w = 240, 320
    yy, xx = np.mgrid[0:h, 0:w]
    spot = np.exp(-(((yy - 120) ** 2 + (xx - 160) ** 2) / (2 * 18.0 ** 2)))
    cur = np.clip(spot * peak, 0, 255).astype(np.uint8)
    cur[40:44, 40:120] = smear
    ref = np.zeros((h, w), dtype=np.uint8)
    img = QImage(cur.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
    stats: dict = {}
    m._apply_reference_diff(img, ref.astype(np.float32), 0, 0, stats)
    return cur, stats


def main() -> int:
    m = load_slider()
    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    cur, stats = _frame(m, 210, 6)
    cur2, stats2 = _frame(m, 90, 3)

    print("the numbers")
    lit = int((cur > 0).sum())
    check(stats["bg"] == 0.0, f"background is the frame floor ({stats['bg']})")
    check(stats["above"] == int((cur > 1).sum()),
          f"pixels above it: {stats['above']} (frame has {lit} non-zero)")
    check(abs(stats["max"] - float(cur.max())) < 1.5,
          f"peak is the brightest pixel ({stats['max']} vs {cur.max()})")
    check(len(stats["hist"]) == m._DIFF_HIST_BINS,
          f"the histogram has {m._DIFF_HIST_BINS} buckets ({len(stats['hist'])})")
    check(sum(stats["hist"]) == stats["above"],
          "every lit pixel is in exactly one bucket")
    check(abs(stats["pct"] - 100.0 * stats["above"] / cur.size) < 1e-6,
          f"the share of the frame is right ({stats['pct']:.2f} %)")
    check(abs(stats["mean"] - float(cur[cur > 1].mean())) < 0.05,
          f"the mean difference is right ({stats['mean']:.2f})")
    check(abs(stats["min"] - float(cur[cur > 1].min())) < 0.05,
          f"the faintest counted pixel is right ({stats['min']:.0f} "
          f"vs {float(cur[cur > 1].min()):.0f})")
    check(stats["min"] <= stats["mean"] <= stats["max"],
          f"min <= mean <= max ({stats['min']:.0f} / {stats['mean']:.1f} / "
          f"{stats['max']:.0f})")

    print("the levels")
    levels = dict(stats["levels"])
    check(tuple(levels) == m._DIFF_LEVELS,
          f"one count per level {m._DIFF_LEVELS} ({tuple(levels)})")
    for lv, n in stats["levels"]:
        want = int((cur >= lv).sum())
        check(n == want, f"pixels at or above {lv}: {n} (frame has {want})")
    check(all(levels[a] >= levels[b]
              for a, b in zip(m._DIFF_LEVELS, m._DIFF_LEVELS[1:])),
          "the counts fall as the level rises")

    line = m.Viewer._fmt_diff_stats(stats)
    print("  line   :", line.replace("\n", " | "))
    print("  compact:", m.Viewer._fmt_diff_stats(stats, compact=True).replace("\n", " | "))
    check("differ" in line and "px" in line, "the line names what it counts")
    # The line is the pixel count and NOTHING else — the peak, the mean and the
    # per-level counts belong to the histogram and its tooltip now.
    check("peak" not in line and "mean" not in line and "≥" not in line,
          "nothing but the count is on the line")
    compact = m.Viewer._fmt_diff_stats(stats, compact=True)
    check(compact == m.Viewer._fmt_px(stats["above"]) + " px",
          f"the multi-camera line is the count alone ({compact!r})")
    check("≥ 50" in m.Viewer._fmt_diff_levels(stats),
          "the levels are still formatted, for the tooltip")

    # The wiring: the INFO panel's own line and histogram blocks, fed the way the
    # display paths feed them. Without this the numbers could be perfect and never
    # reach the panel.
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    key = ("diff-stats-test",)
    m._diff_stats_put(key, stats)
    v._ref_path = Path("reference.png")
    v.cb_subtract.blockSignals(True)
    v.cb_subtract.setChecked(True)
    v.cb_subtract.blockSignals(False)
    v._update_diff_stats(key)
    print("the INFO panel — one camera")
    check("differ" in v.lbl_diff_stats.text(),
          f"the line is on the panel ({v.lbl_diff_stats.text()[:40]!r}…)")
    check(v._diff_hist_box.isVisibleTo(v), "and so is the histogram")

    print("the INFO panel — two cameras")
    v._cam_names = ["PTM11WNF", "PCM4"]
    v._cam_ref_paths = [Path("r1.png"), Path("r2.png")]
    # (frame key, statistics) per camera — the frame key is what stops one frame's
    # measured numbers from being shown for the next one (see _pick_diff_stats).
    v._cam_diff_stats = {0: ("f0", stats), 1: ("f1", stats2)}
    v._cam_diff_final = {0: ("f0", stats), 1: ("f1", stats2)}
    v._flush_cam_diff_stats()
    shown = [b for b in v._diff_hist_blocks if b.isVisibleTo(v)]
    check(len(shown) == 2, f"two cameras → two histograms ({len(shown)})")
    check(shown[0].lbl.text().startswith("PTM11WNF")
          and shown[1].lbl.text().startswith("PCM4"),
          "each block is captioned with its own camera")
    check(shown[0].hist._hist != shown[1].hist._hist,
          "the two histograms hold different data")
    check(v._diff_hist_box.height() <= v._DIFF_BOX_MAX_H,
          f"the box stays within its cap ({v._diff_hist_box.height()} px)")

    # Unticking Subtraction leaves them EXACTLY where they are: with a reference set,
    # every frame is still measured against it, and the INFO panel sits above the
    # settings column — hiding the box moved the checkbox itself out from under the
    # cursor, so clicking on and off to compare was impossible (23.09.2026).
    v.cb_subtract.blockSignals(True)
    v.cb_subtract.setChecked(False)
    v.cb_subtract.blockSignals(False)
    v._flush_cam_diff_stats()
    shown_off = [b for b in v._diff_hist_blocks if b.isVisibleTo(v)]
    check(len(shown_off) == 2,
          f"the histograms stay put with Subtraction off ({len(shown_off)})")
    check(v._diff_hist_box.isVisibleTo(v), "and so does their box")

    # They go with the REFERENCE, which is what they describe.
    v._ref_path = None
    v._cam_ref_paths = [None, None]
    v._flush_cam_diff_stats()
    check(v.lbl_diff_stats.text() == "", "both go away once the reference does")
    check(not v._diff_hist_box.isVisibleTo(v),
          "the histograms are hidden, not left behind")

    # The picture: the panel as the operator sees it, single camera then two.
    box = QWidget()
    box.setFixedWidth(275)
    box.setStyleSheet("background: #f3f3f3; color: #111;")
    vl = QVBoxLayout(box)
    vl.setContentsMargins(6, 6, 6, 6)
    vl.setSpacing(4)
    lbl = QLabel(line)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(m._DIFF_TEXT_STYLE)
    vl.addWidget(lbl)
    hist = m._DiffHistogram()
    hist.set_data(stats["hist"], stats["max"], stats["mean"], stats["min"])
    vl.addWidget(hist)
    for nm, st in (("PTM11WNF", stats), ("PCM4", stats2)):
        b = m._DiffCamBlock(compact=True)
        b.set_block(f"{nm}: " + m.Viewer._fmt_diff_stats(st, compact=True),
                    st["hist"], st["max"], st["mean"], st["min"])
        vl.addWidget(b)
    # The SAME numbers while they are still only an estimate — the frame has so far
    # only been decoded smaller than it will end up. Amber line with "…", grey bars,
    # "not final" written on the picture. Both states are shot together so the
    # difference between them can be judged at a glance.
    prov_lbl = QLabel(m.Viewer._fmt_diff_stats(stats, provisional=True))
    prov_lbl.setWordWrap(True)
    prov_lbl.setStyleSheet(m._DIFF_TEXT_PROV_STYLE)
    vl.addWidget(prov_lbl)
    prov = m._DiffHistogram()
    prov.set_data(stats["hist"], stats["max"], stats["mean"], stats["min"],
                  provisional=True)
    vl.addWidget(prov)
    box.move(-4000, -4000)
    box.show()

    # The same histogram squeezed into a tile-sized column: the numbers must DROP,
    # never overlap each other or the "top" count on the left.
    narrow = QWidget()
    narrow.setFixedWidth(130)
    narrow.setStyleSheet("background: #f3f3f3; color: #111;")
    nl = QVBoxLayout(narrow)
    nl.setContentsMargins(4, 4, 4, 4)
    nh = m._DiffHistogram(compact=True)
    nh.set_data(stats["hist"], stats["max"], stats["mean"], stats["min"])
    nl.addWidget(nh)
    # The narrow one as an estimate too: here "not final" must be the thing that
    # survives, because a number nobody should write down is worse than no number.
    nh2 = m._DiffHistogram(compact=True)
    nh2.set_data(stats["hist"], stats["max"], stats["mean"], stats["min"],
                 provisional=True)
    nl.addWidget(nh2)
    narrow.move(-4000, -4000)
    narrow.show()

    out = Path(__file__).parent

    def shoot():
        app.processEvents()
        box.grab().save(str(out / "diff_stats.png"))
        narrow.grab().save(str(out / "diff_stats_narrow.png"))
        print("written: diff_stats.png, diff_stats_narrow.png")
        app.quit()

    QTimer.singleShot(700, shoot)
    app.exec()
    print()
    if fails:
        print(f"{len(fails)} FAILED")
        for f in fails:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
