"""Step through frames with Subtraction on and watch the panel — end to end.

This is the reported complaint itself, run in the real viewer: with a reference set and
Subtraction on, "the maximum jumps around and something amplifies itself". Five frames
of the same camera, all three Auto boxes ticked, stepped one at a time:

  * the render parameters (contrast, brightness offset, gamma) must be the SAME on
    every frame after the first — otherwise each frame comes out a different
    brightness and two of them cannot be compared by eye;
  * the maximum reported for one frame must not change once it has settled — it used
    to be measured off the small render first and the native one after;
  * a frame shown with Subtraction on must be rendered NATIVE, not shrunk;
  * and the numbers must actually change from frame to frame, because the frames do —
    a test that froze everything would pass on a dead panel.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_subtraction_steady.py

The window is moved off the visible desktop before it is shown.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_steady_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                   # noqa: E402
from PIL import Image, PngImagePlugin                                # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

import img_scale                                                     # noqa: E402
import is_t                                                          # noqa: E402

FAILURES: "list[str]" = []
CAM_DIR = _TMP / "C03-081-STEADYCAM-_-IMG"

# Peaks in counts, frame by frame: a beam that brightens and dims. The differences
# against frame 0 are therefore all different, which is the point.
PEAKS = (800, 1400, 2600, 1100, 3300)


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def app_stylesheet() -> str:
    from render_display_rows import app_stylesheet as css
    return css()


def write_frame(i: int, peak_counts: int) -> "is_t.Item":
    CAM_DIR.mkdir(parents=True, exist_ok=True)
    h, w = 120, 160
    yy, xx = np.mgrid[0:h, 0:w]
    counts = np.clip(
        35.0 + (peak_counts - 35.0)
        * np.exp(-(((xx - 80 - i) ** 2 + (yy - 60) ** 2) / (2 * (12.0 + i) ** 2))),
        0, 4095)
    peak = int(counts.max())
    bits = img_scale.bits_from_max_value(peak)
    stored = np.clip(np.rint(counts * img_scale.SCALE_FACTORS[bits]), 0, 65535)
    ts = 1_700_000_000_000_000_000 + i * 1_000_000_000
    path = CAM_DIR / f"STEADYCAM_{ts}.png"
    meta = PngImagePlugin.PngInfo()
    meta.add_text("MaxValue", str(peak))
    Image.frombytes("I;16", (w, h),
                    stored.astype("<u2").tobytes()).save(path, pnginfo=meta)
    return is_t.Item(path, ts)


def settle(app, v, seconds: float = 3.0):
    """Run the event loop until the display pipeline has nothing left in flight."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if not v._inflight and v._display_load_key is None:
            # One more pass so the completion handlers get to run.
            app.processEvents()
            if not v._inflight and v._display_load_key is None:
                return
        time.sleep(0.01)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    items = [write_frame(i, pk) for i, pk in enumerate(PEAKS)]

    v = is_t.Viewer()
    v.resize(1400, 900)
    v.move(-4000, -4000)
    v.show()
    app.processEvents()

    v.items = items
    v._cam_names = ["C03-081-STEADYCAM"]
    v.current_idx = 0
    v.btn_set_ref.setEnabled(True)
    v._update_ref_buttons()

    # All three Auto boxes on — the state the complaint was made in.
    v.cb_bright.setChecked(True)          # Auto contrast
    v.cb_bright_auto.setChecked(True)     # Auto brightness
    v.cb_gamma_auto.setChecked(True)      # Auto gamma
    v.cb_subtract.setChecked(True)
    app.processEvents()

    v._set_reference_frame()              # reference = frame 0
    settle(app, v)
    check("a reference is set", v._has_reference())

    seen = []
    for idx in range(1, len(items)):
        v.current_idx = idx
        v._display_exact_index(idx, items[idx].ts_ns, update_slider=False)
        settle(app, v)
        bc = v._bc()
        line = v.lbl_diff_stats.text()
        st = v._diff_last_final[1] if v._diff_last_final else None
        seen.append({
            "idx": idx,
            "bc": (bc.contrast, bc.offset, bc.auto, bc.gamma),
            "brighten": v._render_brighten(),
            "max": float(st["max"]) if st else None,
            "above": int(st["above"]) if st else None,
            "side": int(st.get("side", -1)) if st else None,
            "line": line,
        })
        print(f"  frame {idx}: contrast {bc.contrast:>4} offset {bc.offset:>4} "
              f"gamma {bc.gamma:>4} auto {bc.auto}  |  "
              f"max {seen[-1]['max']}  {seen[-1]['above']} px  "
              f"side {seen[-1]['side']}  |  {line!r}")

    check("every frame produced numbers",
          all(s["max"] is not None for s in seen),
          str([s["max"] for s in seen]))

    params = {(s["brighten"],) + s["bc"] for s in seen}
    check("every frame is adjusted by the SAME contrast, brightness and gamma",
          len(params) == 1, f"{len(params)} different sets: {sorted(params)}")
    check("...and none of them is still an Auto sentinel",
          all(s["bc"][2] == 0 and s["brighten"] == 0
              and s["bc"][3] != img_scale.GAMMA_SLIDER_AUTO for s in seen),
          str(sorted(params)))
    # The first frame shown after Set ref is the REFERENCE, whose difference is
    # identically zero — Auto measured on it lands on "do nothing" and freezing that
    # would leave every later frame unadjusted. The run has to wait for a frame that
    # actually differs.
    check("the frozen values are a real measurement, not 'do nothing'",
          v._sub_auto_hold is not None
          and (v._sub_auto_hold.get("contrast") or v._sub_auto_hold.get("offset")),
          str(v._sub_auto_hold))

    check("every frame is measured at full resolution",
          all(s["side"] == is_t.FULL_RES_SIDE for s in seen),
          str([s["side"] for s in seen]))
    check("no line is left saying the numbers are not final",
          not any("…" in s["line"] for s in seen),
          str([s["line"] for s in seen]))

    # The panel is alive: the frames really do differ from the reference by different
    # amounts, so the numbers must move.
    maxima = [s["max"] for s in seen]
    check("the numbers still follow the pictures",
          len(set(maxima)) > 1, str(maxima))

    # Showing the same frame again must give back the very same numbers.
    v.current_idx = 2
    v._display_exact_index(2, items[2].ts_ns, update_slider=False)
    settle(app, v)
    again = float(v._diff_last_final[1]["max"])
    was = next(s["max"] for s in seen if s["idx"] == 2)
    check("coming back to a frame reports the same maximum it did before",
          again == was, f"{again} vs {was}")

    test_toggling_subtraction_moves_nothing(v, app, items)


def test_toggling_subtraction_moves_nothing(v, app, items):
    """Reported 23.09.2026: unticking Subtraction made the histograms vanish, and since
    the INFO panel sits ABOVE the settings column the whole column — the checkbox
    included — jumped up under the cursor, so clicking on and off to compare was
    impossible. The numbers now belong to the REFERENCE, not to the checkbox."""
    print("\nclicking Subtraction on and off")

    def snap():
        return {
            "box": v._diff_hist_box.isVisible(),
            "h": v._diff_hist_box.height(),
            "cb_y": v.cb_subtract.mapTo(v, v.cb_subtract.rect().topLeft()).y(),
            "line": v.lbl_diff_stats.text(),
            "max": (float(v._diff_last_final[1]["max"])
                    if v._diff_last_final else None),
        }

    on = snap()
    print(f"  Subtraction ON : histogram {on['h']} px, checkbox at y={on['cb_y']}, "
          f"max {on['max']}")
    v.cb_subtract.setChecked(False)
    settle(app, v)
    off = snap()
    print(f"  Subtraction OFF: histogram {off['h']} px, checkbox at y={off['cb_y']}, "
          f"max {off['max']}")

    check("the histogram is still there with Subtraction off", off["box"],
          str(off))
    check("nothing under it moved a pixel", off["cb_y"] == on["cb_y"],
          f"checkbox y {on['cb_y']} -> {off['cb_y']}")
    check("the histogram kept its height", off["h"] == on["h"],
          f"{on['h']} -> {off['h']}")
    check("and the numbers are the same difference they were",
          off["max"] == on["max"], f"{on['max']} -> {off['max']}")

    # Step a frame with Subtraction OFF: the numbers must FOLLOW it, not sit still.
    v.current_idx = 4
    v._display_exact_index(4, items[4].ts_ns, update_slider=False)
    settle(app, v)
    moved = snap()
    print(f"  stepped a frame with it off: max {moved['max']}, {moved['line']!r}")
    check("with Subtraction off the numbers still follow the frames",
          moved["max"] is not None and moved["max"] != off["max"],
          f"{off['max']} -> {moved['max']}")
    check("...and they are not marked as an estimate",
          "…" not in moved["line"], moved["line"])

    v.cb_subtract.setChecked(True)
    settle(app, v)
    back = snap()
    check("switching it back on moves nothing either",
          back["cb_y"] == on["cb_y"], f"{on['cb_y']} -> {back['cb_y']}")
    check("and reports the same numbers for the frame on screen",
          back["max"] == moved["max"], f"{moved['max']} -> {back['max']}")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
