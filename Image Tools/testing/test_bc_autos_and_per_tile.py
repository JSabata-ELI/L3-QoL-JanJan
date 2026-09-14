"""The three display rows (Contrast / Brightness / Gamma) and the per-tile settings.

Two things the operator reported, both checked here.

  1. "Auto contrast does the same thing as Auto brightness, and neither one does what
     its slider does." It was true: both boxes ran the same percentile stretch, so the
     two pictures were identical, and neither resembled dragging the slider. Each box is
     now its own slider set automatically —
        Auto contrast  = the gain that spreads this frame's window, black left alone
        Auto brightness = the offset that puts this frame's black at 0, nothing spread
     so the two differ, each reproduces exactly on its own slider at the number it
     reports, and the two together are the old full stretch.

  2. "Click an image and the sliders should show that image's settings." With several
     tiles on screen each one keeps its own; selecting a tile now reads them back into
     the panel, and a selection whose tiles disagree says so instead of showing one
     tile's number as everybody's.

Runs headless, no share and no archiver:

    python testing/test_bc_autos_and_per_tile.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLabel,
                               QPushButton, QSlider)
from PySide6.QtCore import Qt

import img_scale
import is_t


FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ── 1. the three rows ─────────────────────────────────────────────────────────
def dim_frame() -> np.ndarray:
    """A frame like the dim cameras really produce: everything in the bottom few
    percent of the 16-bit range, which is where the old 8-bit contrast slider
    posterised and where its gain ran out."""
    rng = np.random.default_rng(7)
    arr = rng.normal(2200.0, 400.0, (240, 320)).astype(np.float32)
    arr[100:110, 150:160] = 40000.0          # a few hot pixels
    return np.clip(arr, 0.0, 65535.0)


def test_rows() -> None:
    arr = dim_frame()
    fs = img_scale.FULL_SCALE_16

    print("Each Auto box does its own row's job")
    a_con, o_con = {}, {}
    con = img_scale.render_u8(arr, img_scale.AUTO_CONTRAST, fs, None, out=a_con)
    bri = img_scale.render_u8(arr, img_scale.AUTO_BRIGHT, fs, None, out=o_con)
    both = img_scale.render_u8(arr, img_scale.AUTO_CONTRAST | img_scale.AUTO_BRIGHT, fs,
                               None)
    plain = img_scale.render_u8(arr, img_scale.AUTO_NONE, fs, None)
    check(int(np.abs(con.astype(int) - bri.astype(int)).max()) > 50,
          f"Auto contrast and Auto brightness give different pictures "
          f"(median {int(np.median(con))} vs {int(np.median(bri))})")
    check(int(np.percentile(con, 99)) > 4 * int(np.percentile(plain, 99)),
          "Auto contrast spreads the frame out")
    check(int(np.median(bri)) < int(np.median(plain)),
          "Auto brightness only shifts — it pulls the background down to black")
    check(int(np.percentile(bri, 99)) - int(np.percentile(bri, 1))
          <= int(np.percentile(plain, 99)) - int(np.percentile(plain, 1)) + 1,
          "…and spreads nothing while doing it")

    print("Auto is the slider, set for you")
    man_c = img_scale.render_u8(arr, img_scale.AUTO_NONE, fs, None,
                                contrast=a_con["contrast"])
    man_b = img_scale.render_u8(arr, img_scale.AUTO_NONE, fs, None,
                                offset=o_con["offset"])
    check(a_con["contrast"] != 0 and abs(a_con["contrast"]) <= img_scale.CONTRAST_MAX,
          f"Auto contrast reports a value the slider can hold: {a_con['contrast']}")
    check(np.array_equal(man_c, con),
          "putting the Contrast slider on that number gives back Auto's picture")
    check(np.array_equal(man_b, bri),
          "putting the Brightness slider on Auto's offset gives back Auto's picture")

    print("Both boxes together are the old full stretch")
    stretch = img_scale.stretch_u8(arr, full_scale=fs)
    check(int(np.abs(both.astype(int) - stretch.astype(int)).max()) <= 2,
          "Auto contrast + Auto brightness == the percentile stretch")

    print("The contrast curve reaches as far as the frames need")
    need = 65535.0 / (float(np.percentile(arr, 99.5)) - float(np.percentile(arr, 0.5)))
    check(img_scale.contrast_gain(img_scale.CONTRAST_MAX) >= need,
          f"slider reaches {img_scale.contrast_gain(img_scale.CONTRAST_MAX):.0f}x, "
          f"this frame needs {need:.0f}x")
    check(abs(img_scale.contrast_gain(64) - 2.0) < 1e-6
          and abs(img_scale.contrast_gain(0) - 1.0) < 1e-9,
          "0 is untouched and +64 is exactly twice")
    # The old rational curve, kept here as the thing the numbers must not drift from.
    for c in (20, 50, 100, 127):
        old = (259.0 * (c + 127.0)) / (127.0 * (259.0 - c))
        check(abs(img_scale.contrast_gain(c) - old) / old < 0.02,
              f"Con +{c} still means what it meant ({img_scale.contrast_gain(c):.3f} "
              f"vs {old:.3f})")

    print("Auto gamma is the gamma slider set for you, the same way")
    ag = {}
    g_auto = img_scale.render_u8(arr, img_scale.AUTO_NONE, fs,
                                 img_scale.GAMMA_SLIDER_AUTO, out=ag)
    parked = img_scale.slider_from_gamma(float(ag["gamma"]))
    g_man = img_scale.render_u8(arr, img_scale.AUTO_NONE, fs, parked)
    check(img_scale.GAMMA_SLIDER_MIN <= parked <= img_scale.GAMMA_SLIDER_MAX,
          f"Auto gamma reports a value the slider can hold: {parked / 100:.2f}")
    check(int(np.abs(g_man.astype(int) - g_auto.astype(int)).max()) <= 1,
          "putting the Gamma slider on that number gives back Auto's picture")

    print("Gamma survives an Auto box instead of being switched off")
    g_on = img_scale.render_u8(arr, img_scale.AUTO_CONTRAST, fs, 50)   # gamma 0.50
    check(int(np.abs(g_on.astype(int) - con.astype(int)).max()) > 10,
          "gamma still bends the curve while Auto contrast is on")

    print("A frame with no signal at all does not blow up")
    flat = np.full((32, 32), 1234.0, dtype=np.float32)
    out = img_scale.render_u8(flat, img_scale.AUTO_CONTRAST | img_scale.AUTO_BRIGHT, fs,
                              None)
    check(out.shape == flat.shape, "flat frame renders")


# ── 2. per-tile settings ──────────────────────────────────────────────────────
class _Panel:
    """The display panel's widgets plus the real per-tile methods under test."""

    _is_multi_cam            = is_t.Viewer._is_multi_cam
    _is_view_only_palette    = is_t.Viewer._is_view_only_palette
    _bc_raw                  = is_t.Viewer._bc_raw
    _sync_bc_controls_enabled = is_t.Viewer._sync_bc_controls_enabled
    _sync_bc_value_labels    = is_t.Viewer._sync_bc_value_labels
    _set_gamma_label         = is_t.Viewer._set_gamma_label
    _disp_ui_snapshot        = is_t.Viewer._disp_ui_snapshot
    _disp_snapshot           = is_t.Viewer._disp_snapshot
    _cam_disp_reset          = is_t.Viewer._cam_disp_reset
    _cam_disp_get            = is_t.Viewer._cam_disp_get
    _disp_targets            = is_t.Viewer._disp_targets
    _bc_from_ui              = staticmethod(is_t.Viewer._bc_from_ui)
    _apply_disp_to_targets   = is_t.Viewer._apply_disp_to_targets
    _bc_row_touched          = is_t.Viewer._bc_row_touched
    _apply_bc_rows_to_targets = is_t.Viewer._apply_bc_rows_to_targets
    _DISP_ROWS               = is_t.Viewer._DISP_ROWS
    _disp_ui_of              = is_t.Viewer._disp_ui_of
    _disp_target_records     = is_t.Viewer._disp_target_records
    _disp_diff_flags         = is_t.Viewer._disp_diff_flags
    _refresh_disp_diff_marks = is_t.Viewer._refresh_disp_diff_marks
    _load_disp_from_targets  = is_t.Viewer._load_disp_from_targets

    def __init__(self, grid, cam_names):
        self._multi_grid = grid
        self._cam_names = list(cam_names)
        self._cam_items = [[] for _ in cam_names]
        self.gradient_cb = QComboBox()
        for name in is_t.GRADIENT_NAMES:
            self.gradient_cb.addItem(name)
        self.gradient_cb.setCurrentIndex(2)
        self.cb_bright = QCheckBox("Auto")
        self.cb_bright_auto = QCheckBox("Auto")
        self.cb_gamma_auto = QCheckBox("Auto")
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setRange(img_scale.CONTRAST_MIN, img_scale.CONTRAST_MAX)
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(img_scale.BRIGHTNESS_MIN,
                                        img_scale.BRIGHTNESS_MAX)
        self.gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self.gamma_slider.setRange(img_scale.GAMMA_SLIDER_MIN,
                                   img_scale.GAMMA_SLIDER_MAX)
        self.gamma_slider.setValue(img_scale.GAMMA_SLIDER_NEUTRAL)
        self.btn_contrast_reset = QPushButton("↺")
        self.btn_brightness_reset = QPushButton("↺")
        self.btn_gamma_reset = QPushButton("↺")
        self.lbl_contrast_name = QLabel("Con:")
        self.lbl_bright_name = QLabel("Bri:")
        self.lbl_gamma = QLabel("Gam:")
        self.lbl_contrast_val = is_t._bc_value_label("0", is_t._TT_CONTRAST)
        self.lbl_bright_val = is_t._bc_value_label("0", is_t._TT_BRIGHTNESS)
        self.lbl_gamma_val = is_t._bc_value_label("1.00", is_t._TT_GAMMA)
        self.lbl_disp_mixed = QLabel("")
        self.lbl_disp_mixed.setVisible(False)
        self._contrast_manual = 0
        self._brightness_manual = 0
        self._brightness_offset = 0
        self._gamma_manual = img_scale.GAMMA_SLIDER_NEUTRAL
        self._cam_disp = []

    # What the panel does when a control is moved, minus the reload: the value lands in
    # the manual backing store, its row is marked as touched, and the touched rows are
    # written into the targeted tiles.
    def set_contrast(self, v):
        self.contrast_slider.setValue(int(v))
        self._contrast_manual = int(v)
        self._bc_row_touched("contrast")
        self._apply_bc_rows_to_targets()

    def set_brightness(self, v):
        self.brightness_slider.setValue(int(v))
        self._brightness_manual = int(v)
        self._brightness_offset = int(v)
        self._bc_row_touched("bright")
        self._apply_bc_rows_to_targets()

    def set_gamma(self, v):
        self.gamma_slider.setValue(int(v))
        self._gamma_manual = int(v)
        self._bc_row_touched("gamma")
        self._apply_bc_rows_to_targets()

    def set_auto_contrast(self, on):
        self.cb_bright.setChecked(bool(on))
        self._bc_row_touched("contrast")
        self._apply_bc_rows_to_targets()

    def trio(self):
        """The three readouts as the operator reads them off the panel."""
        return (self.lbl_contrast_val.text(), self.lbl_bright_val.text(),
                self.lbl_gamma_val.text())


def test_per_tile() -> None:
    cam_names = ["C01-A-CAM1NF", "C02-B-CAM2NF", "C03-C-CAM3NF"]
    grid = is_t.MultiCameraGrid()
    grid.setup_cameras(cam_names)
    p = _Panel(grid, cam_names)
    p._cam_disp_reset(len(cam_names))
    p._refresh_disp_diff_marks()

    print("A setting made on one camera stays on that camera")
    grid._on_cam_clicked(0)
    p._load_disp_from_targets()
    p.set_contrast(0)
    p.set_brightness(50)
    check(p._cam_disp[0]["ui"]["bri"] == 50, "camera 1 took the 50")
    check(p._cam_disp[1]["ui"]["bri"] == 0, "camera 2 was not touched")

    print("Clicking another camera shows THAT camera's settings")
    grid._on_cam_clicked(0)          # deselect
    grid._on_cam_clicked(1)          # select camera 2
    p._load_disp_from_targets()
    check(p.trio() == ("0", "0", "1.00"),
          f"camera 2 reads 0 / 0 / 1.00, got {p.trio()}")
    grid._on_cam_clicked(1)
    grid._on_cam_clicked(0)          # back to camera 1
    p._load_disp_from_targets()
    check(p.trio() == ("0", "50", "1.00"),
          f"camera 1 reads its own 0 / 50 / 1.00 again, got {p.trio()}")
    check(p.brightness_slider.value() == 50, "the slider itself moved back to 50")

    print("Two cameras that disagree say so")
    grid._on_cam_clicked(1)          # cameras 1 and 2 selected, different brightness
    p._load_disp_from_targets()
    check(p.lbl_bright_val.text() == "≠", f"brightness reads ≠, got "
                                          f"{p.lbl_bright_val.text()!r}")
    check(p.lbl_contrast_val.text() == "0", "contrast agrees, so it still shows 0")
    check(p.lbl_disp_mixed.isVisible(), "the warning line is up")
    check("brightness" in p.lbl_disp_mixed.text().lower(),
          f"and names the row: {p.lbl_disp_mixed.text()!r}")

    print("Moving one row leaves the other rows where they were")
    p.set_contrast(20)
    check(p._cam_disp[0]["ui"]["con"] == 20 and p._cam_disp[1]["ui"]["con"] == 20,
          "both selected cameras took the contrast")
    check(p._cam_disp[0]["ui"]["bri"] == 50 and p._cam_disp[1]["ui"]["bri"] == 0,
          "their brightnesses are untouched — a contrast move is not a whole-panel copy")
    check(p.lbl_bright_val.text() == "≠", "so the brightness row still says they differ")
    p.set_contrast(0)

    print("Moving the control sets both the same")
    p.set_brightness(30)
    check(p._cam_disp[0]["ui"]["bri"] == 30 and p._cam_disp[1]["ui"]["bri"] == 30,
          "both selected cameras took the 30")
    check(p._cam_disp[2]["ui"]["bri"] == 0, "the unselected camera kept its own")
    check(not p.lbl_disp_mixed.isVisible(), "the warning is gone")
    check(p.lbl_bright_val.text() == "30",
          f"and the readout is a number again: {p.lbl_bright_val.text()!r}")

    print("An Auto box is part of what a camera remembers")
    grid._on_cam_clicked(1)          # camera 1 alone
    p.set_auto_contrast(True)
    check(p._cam_disp[0]["ui"]["auto_c"] and not p._cam_disp[1]["ui"]["auto_c"],
          "Auto contrast is on for camera 1 only")
    grid._on_cam_clicked(0)
    grid._on_cam_clicked(1)
    p._load_disp_from_targets()
    check(not p.cb_bright.isChecked(), "camera 2 comes up with Auto contrast off")
    check(p.contrast_slider.isEnabled(), "…so its Contrast slider is live")
    grid._on_cam_clicked(1)
    grid._on_cam_clicked(0)
    p._load_disp_from_targets()
    check(p.cb_bright.isChecked(), "camera 1 comes back with Auto contrast on")
    check(not p.contrast_slider.isEnabled(), "…and its slider greyed out again")

    print("Selecting a camera never writes to it")
    before = [dict(r) for r in p._cam_disp]
    grid._on_cam_clicked(0)
    grid._on_cam_clicked(2)
    p._load_disp_from_targets()
    check(all(a["ui"] == b["ui"] and a["gid"] == b["gid"]
              for a, b in zip(before, p._cam_disp)),
          "no tile changed when the selection did")

    print("Nothing selected means every camera")
    grid._on_cam_clicked(2)
    check(grid.selected_cam_indices() == [], "selection cleared")
    p._load_disp_from_targets()
    check(p.lbl_bright_val.text() == "≠",
          "with all three in scope and camera 3 still on 0, the row differs")


# ── 3. the preview layer paints the same thing ────────────────────────────────
class _ProxyStub:
    """Just enough of Viewer to run the real _proxy_render."""

    _proxy_render = is_t.Viewer._proxy_render
    _proxy_gamma = is_t.Viewer._proxy_gamma

    class _Cache:
        def get(self, key):
            return None

        def put(self, key, val):
            pass

    def __init__(self):
        self._proxy_render_cache = self._Cache()


def proxy_tuple(arr16: np.ndarray):
    """Encode a frame the way load_proxy_gray stores it for the preview layer."""
    lo = float(np.percentile(arr16, 0.1))
    hi = float(np.percentile(arr16, 99.9))
    mx = float(arr16.max())
    k = is_t.PROXY_KNEE_CODE
    f = arr16.astype(np.float32)
    u8 = np.clip((f - lo) / (hi - lo) * k, 0, k).astype(np.uint8)
    tail = f > hi
    if mx > hi and tail.any():
        u8[tail] = (k + 1 + np.clip((f[tail] - hi) / (mx - hi) * (254 - k),
                                    0, 254 - k)).astype(np.uint8)
    return u8, lo, hi, max(mx, hi), "C01-A-CAM1NF", 16


def pixmap_gray(pm) -> np.ndarray:
    img = pm.toImage().convertToFormat(is_t.QImage.Format.Format_Grayscale8)
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine())[:, :img.width()].copy()


def test_preview_matches() -> None:
    print("The preview layer paints what the full render paints")
    arr = dim_frame()
    arr16 = arr.astype(np.uint16)
    stub = _ProxyStub()
    packed = proxy_tuple(arr16)
    cases = [("nothing", 0, 0, 0, 0),
             ("Auto contrast", 1, 0, 0, 0),
             ("Auto brightness", 0, 1, 0, 0),
             ("both Autos", 1, 1, 0, 0),
             ("sliders by hand", 0, 0, 200, -4)]
    for name, brighten, auto_b, con, off in cases:
        bc = is_t._RenderBC(off, con, auto_b, img_scale.GAMMA_SLIDER_NEUTRAL)
        pm = stub._proxy_render(0, 1, packed, brighten, bc, 1)
        prev = pixmap_gray(pm).astype(int)
        full = img_scale.render_u8(
            arr16, img_scale.auto_mask(bool(brighten), bool(auto_b)),
            img_scale.FULL_SCALE_16, img_scale.GAMMA_SLIDER_NEUTRAL, con, off).astype(int)
        d = np.abs(prev - full)
        # The preview holds the frame in 240 codes, so a big gain multiplies its own
        # rounding: a handful of codes apart is the storage, a hundred would be a
        # different operation, which is what this is watching for.
        check(float(np.median(d)) <= 8 and int(np.percentile(d, 99)) <= 30,
              f"{name}: preview and full render agree "
              f"(median {float(np.median(d)):.1f}, p99 {int(np.percentile(d, 99))})")


def main() -> int:
    # The console here is cp1250; the mark this test asserts on is not in it.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    QApplication.instance() or QApplication(sys.argv)
    test_rows()
    print()
    test_preview_matches()
    print()
    test_per_tile()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
