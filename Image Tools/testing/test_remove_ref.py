"""Remove ref must undo Set ref completely — badge, numbers, buttons and the render.

Until now a subtraction reference could only be dropped by starting a new scan. The
button added beside Set ref takes it away deliberately, and this checks that nothing is
left behind: no green "Ref: …" badge claiming a reference that is gone, no difference
numbers under the picture, and the next frame drawn plain again.

The scope rule is the one Set ref already has (see the memory note "a setting hits the
cameras selected WHEN you move the control"): with several cameras it clears the ones
selected at the moment of the click.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_remove_ref.py

The window is moved off the visible desktop before it is shown — the real windows
platform is needed for honest widget sizes, but nothing may flash on the screen.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_remref_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                   # noqa: E402
from PIL import Image, PngImagePlugin                                # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                  # noqa: E402

import img_scale                                                     # noqa: E402
import is_t                                                          # noqa: E402

FAILURES: "list[str]" = []

OFFSCREEN = (-4000, -4000)      # far outside any monitor


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def app_stylesheet() -> str:
    from render_display_rows import app_stylesheet as css
    return css()


def write_frame(folder: Path, ts_ns: int, peak_counts: int) -> Path:
    """One archive-shaped 16-bit frame (see test_subtraction_stats.write_frame)."""
    folder.mkdir(parents=True, exist_ok=True)
    h, w = 48, 64
    yy, xx = np.mgrid[0:h, 0:w]
    counts = np.clip(30.0 + (peak_counts - 30.0)
                     * np.exp(-(((xx - 32) ** 2 + (yy - 24) ** 2) / (2 * 8.0 ** 2))),
                     0, 4095)
    peak = int(counts.max())
    bits = img_scale.bits_from_max_value(peak)
    stored = np.clip(np.rint(counts * img_scale.SCALE_FACTORS[bits]), 0, 65535)
    path = folder / f"CAM_{ts_ns}.png"
    meta = PngImagePlugin.PngInfo()
    meta.add_text("MaxValue", str(peak))
    Image.frombytes("I;16", (w, h),
                    stored.astype("<u2").tobytes()).save(path, pnginfo=meta)
    return path


def make_items(folder: Path, peaks) -> list:
    out = []
    for i, pk in enumerate(peaks):
        ts = 1_700_000_000_000_000_000 + i * 500_000_000
        out.append(is_t.Item(write_frame(folder, ts, pk), ts))
    return out


# ── one camera ───────────────────────────────────────────────────────────────────
def test_single_camera(v, app):
    items = make_items(_TMP / "C03-081-ONECAM-_-IMG", [900, 2600, 1800])
    v.items = items
    v._cam_names = ["C03-081-ONECAM"]
    v.current_idx = 1
    v.btn_set_ref.setEnabled(True)
    v._update_ref_buttons()
    check("Remove ref is dead while there is no reference",
          not v.btn_remove_ref.isEnabled())

    v.cb_subtract.setChecked(True)
    app.processEvents()
    v._set_reference_frame()
    app.processEvents()
    check("Set ref really set one", v._has_reference())
    check("Remove ref wakes up once there is one", v.btn_remove_ref.isEnabled())
    check("the picture wears the green reference badge",
          bool(v.img_view.cam_ref_text), repr(v.img_view.cam_ref_text))
    ref_line = v.lbl_ref_status.text()
    print(f"  reference line: {ref_line!r}")

    v._sub_auto_hold = {"contrast": 9}
    v._diff_last_final = ("frame", {"max": 5.0, "above": 3, "side": 0})

    v._remove_reference_frame()
    app.processEvents()
    check("the reference is gone", not v._has_reference())
    check("the reference array cache is dropped", v._ref_scaled == {})
    check("the green badge is off the picture",
          not v.img_view.cam_ref_text, repr(v.img_view.cam_ref_text))
    check("the difference numbers are gone", v.lbl_diff_stats.text() == "",
          repr(v.lbl_diff_stats.text()))
    check("the histogram is gone", not v._diff_hist_box.isVisible())
    check("the frozen Auto values are released", v._sub_auto_hold is None)
    check("the remembered measured numbers are released",
          v._diff_last_final is None)
    check("Remove ref greys itself out again", not v.btn_remove_ref.isEnabled())
    check("Set ref stays live", v.btn_set_ref.isEnabled())
    check("Subtraction is left exactly as the user set it",
          v.cb_subtract.isChecked())
    print(f"  reference line now: {v.lbl_ref_status.text()!r}")
    check("with Subtraction still on the panel says the reference is missing",
          "no reference" in v.lbl_ref_status.text().lower(),
          repr(v.lbl_ref_status.text()))
    check("the next render asks for no reference at all",
          v._ref_arr_for(is_t.FULL_RES_SIDE) is None)


# ── several cameras ──────────────────────────────────────────────────────────────
def test_multi_camera(v, app):
    names = ["C03-081-CAMA", "C03-082-CAMB", "C03-083-CAMC"]
    folders = [_TMP / f"{n}-_-IMG" for n in names]
    items = [make_items(f, [900, 2600]) for f in folders]
    # The viewer's own setup, not a hand-built stand-in: it is what allocates every
    # per-camera array the redraw touches.
    v._setup_multi_cam(list(names), folders)
    v._multi_grid.setup_cameras(names)
    v._cam_items = items
    v._cam_ts = [[it.ts_ns for it in c] for c in items]
    v._cam_current_idx = [1] * len(names)
    v._cam_disp_reset(len(names))
    app.processEvents()
    check("the viewer is in several-camera mode", v._is_multi_cam())

    # A reference on all three, set the way Set ref sets one.
    v._cam_ref_images = [True] * len(names)
    v._cam_ref_paths = [v._cam_items[i][1].path for i in range(len(names))]
    for i in range(len(names)):
        v._multi_grid.set_cam_ref_status(i, f"Ref: cam {i}")
        v._cam_sub_hold[i] = {"contrast": 5}
        v._cam_diff_stats[i] = ("f", {"max": 9.0, "above": 4, "side": 0})
        v._cam_diff_final[i] = ("f", {"max": 9.0, "above": 4, "side": 0})
    v.cb_subtract.setChecked(True)
    v._update_ref_buttons()
    app.processEvents()
    check("all three tiles wear the badge",
          all(_badge(v, i) for i in range(len(names))),
          str([_badge(v, i) for i in range(len(names))]))

    # Pick two of them — the rule under test is the SCOPE, so the selection set is
    # driven directly rather than through the grid's click gesture.
    v._multi_grid._selected_set = {0, 2}
    print(f"  selected cameras: {v._multi_grid.selected_cam_indices()}")

    v._remove_reference_frame()
    app.processEvents()
    check("the selected cameras lost their reference",
          v._cam_ref_paths[0] is None and v._cam_ref_paths[2] is None)
    check("the camera that was NOT selected kept its own",
          v._cam_ref_paths[1] is not None)
    check("their green badges are gone, the third one's stays",
          not _badge(v, 0) and not _badge(v, 2) and bool(_badge(v, 1)),
          str([_badge(v, i) for i in range(len(names))]))
    check("their frozen Auto values are released, the third one's kept",
          0 not in v._cam_sub_hold and 2 not in v._cam_sub_hold
          and 1 in v._cam_sub_hold)
    check("their numbers are released too",
          0 not in v._cam_diff_final and 2 not in v._cam_diff_final
          and 1 in v._cam_diff_final)
    check("a reference still exists, so Remove ref stays live",
          v._has_reference() and v.btn_remove_ref.isEnabled())
    check("a cleared camera renders with no reference",
          v._cam_ref_arr_for(0, 400) is None)
    check("...and the one that kept its reference still has one",
          v._cam_ref_arr_for(1, 400) is not None)

    # And the last one.
    v._multi_grid._selected_set = {1}
    v._remove_reference_frame()
    app.processEvents()
    check("removing the last one leaves none", not v._has_reference())
    check("Remove ref greys itself out", not v.btn_remove_ref.isEnabled())
    check("no tile is left claiming a reference", not any(v._cam_ref_paths))
    check("no tile is left wearing a badge",
          not any(_badge(v, i) for i in range(len(names))))
    check("with Subtraction still on the panel says the reference is missing",
          "no reference" in v.lbl_ref_status.text().lower(),
          repr(v.lbl_ref_status.text()))


def _badge(v, cam_i: int) -> str:
    """The green 'Ref: …' strip on one tile, as the operator sees it."""
    cv = v._multi_grid._cam_views[cam_i]
    return cv._ref_lbl.text() if cv._ref_lbl.isVisible() else ""


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    v = is_t.Viewer()
    v.resize(1500, 950)
    v.move(*OFFSCREEN)
    v.show()
    app.processEvents()

    print("one camera")
    test_single_camera(v, app)

    print("\nseveral cameras")
    test_multi_camera(v, app)

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
