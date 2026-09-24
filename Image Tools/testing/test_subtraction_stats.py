"""Subtraction must report the SAME difference whatever the display controls say.

Reported: "with a reference set and Subtraction on, the maximum jumps around and
something amplifies itself". Three separate causes, one test each:

  1. GAMMA WAS INSIDE THE DIFFERENCE. The current frame was rendered through the gamma
     control before subtracting, while the reference (_load_raw_arr) is always decoded
     linear — so |current - reference| compared two differently bent curves, and with
     Auto gamma the curve came off each frame's own median, i.e. a different scale on
     every frame. The difference is now taken linear and gamma is a display step on the
     finished difference, like contrast and brightness already were.

  2. AUTO BRIGHTNESS / CONTRAST MEASURED EVERY DIFFERENCE FRAME. Each frame came out a
     different brightness. Auto still runs after the difference, but its values are
     frozen for the run (Viewer._apply_sub_hold).

  3. THE NUMBERS CAME FROM WHATEVER SIZE WAS DECODED. One frame is drawn small first and
     full-size a moment later; shrinking averages neighbouring pixels, so the maximum
     dropped. Only a full-size render may replace the numbers now, and an estimate is
     marked (Viewer._pick_diff_stats).

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_subtraction_stats.py

No window is ever shown: the render is a plain function and the panel rules are tested
on the methods themselves.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_sub_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                   # noqa: E402
from PIL import Image, PngImagePlugin                                # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage                    # noqa: E402

import img_scale                                                      # noqa: E402
import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []

# A camera folder name, so img_scale.camera_from_path finds a camera and the frame is
# put on the sensor's absolute scale exactly as a real archive frame is.
CAM_DIR = _TMP / "C03-081-TESTCAM-_-IMG"


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def write_frame(name: str, counts: np.ndarray) -> Path:
    """One frame written the way the archiver writes them.

    `counts` are the camera's own 12-bit numbers. The archiver brackets each frame to
    the next power of two above its peak and stretches it over the full 16 bits, then
    records the peak IN COUNTS as the MaxValue tag — which is what puts the frame back
    on the sensor's absolute scale at decode (img_scale.display_full_scale). Writing a
    raw count array instead produced a picture 30x too dark to test anything on."""
    CAM_DIR.mkdir(parents=True, exist_ok=True)
    path = CAM_DIR / name
    peak = int(counts.max())
    bits = img_scale.bits_from_max_value(peak)
    stored = np.clip(np.rint(counts.astype(np.float64)
                             * img_scale.SCALE_FACTORS[bits]), 0, 65535)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("MaxValue", str(peak))
    # frombytes, not fromarray(..., mode=): Pillow 13 drops the mode argument, and
    # little-endian u2 is exactly what "I;16" reads.
    h, w = stored.shape
    Image.frombytes("I;16", (w, h),
                    stored.astype("<u2").tobytes()).save(path, pnginfo=meta)
    return path


def make_pair():
    """A reference frame and a 'current' frame that differ in a known way — counts, as
    the camera reads them: a dark floor with a beam spot, and the spot brighter and
    wider on the current frame."""
    h, w = 96, 128
    yy, xx = np.mgrid[0:h, 0:w]
    ref = np.full((h, w), 40.0)
    ref += 900.0 * np.exp(-(((xx - 60) ** 2 + (yy - 44) ** 2) / (2 * 9.0 ** 2)))
    cur = np.full((h, w), 40.0)
    cur += 2600.0 * np.exp(-(((xx - 64) ** 2 + (yy - 46) ** 2) / (2 * 13.0 ** 2)))
    return (np.clip(ref, 0, 4095).astype(np.uint16),
            np.clip(cur, 0, 4095).astype(np.uint16))


def ref_array(path: Path, max_side: int) -> np.ndarray:
    """The subtraction reference exactly as the Viewer decodes it (_load_raw_arr)."""
    img = is_t.load_image_scaled(path, max_side, False, gradient_id=0,
                                 brightness_offset=0)
    if img.format() != QImage.Format.Format_Grayscale8:
        img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine())[:, :img.width()].copy().astype(np.float32)


def render(cur_path, ref_arr, **kw) -> dict:
    """One subtraction render; returns the statistics it produced."""
    stats: dict = {}
    is_t.load_image_scaled(cur_path, kw.pop("max_side", 0), kw.pop("brighten", False),
                           gradient_id=kw.pop("gradient_id", 1),
                           brightness_offset=kw.pop("offset", 0),
                           ref_image=ref_arr,
                           sub_threshold=kw.pop("sub_threshold", 0),
                           contrast=kw.pop("contrast", 0),
                           auto_bright=kw.pop("auto_bright", 0),
                           sub_offset=kw.pop("sub_offset", 0),
                           stats_out=stats,
                           gamma=kw.pop("gamma", img_scale.GAMMA_SLIDER_NEUTRAL))
    assert not kw, f"unused render arguments: {kw}"
    return stats


def numbers(st: dict) -> tuple:
    return (round(float(st.get("max", 0)), 3), round(float(st.get("mean", 0)), 3),
            round(float(st.get("min", 0)), 3), int(st.get("above", 0)),
            tuple(st.get("hist") or ()))


# ── 1. gamma may not touch the numbers ───────────────────────────────────────────
def test_gamma_does_not_move_the_numbers(ref_path, cur_path):
    ra = ref_array(ref_path, 0)
    base = render(cur_path, ra, gamma=img_scale.GAMMA_SLIDER_NEUTRAL)
    dark = render(cur_path, ra, gamma=50)                    # gamma 0.50
    auto = render(cur_path, ra, gamma=img_scale.GAMMA_SLIDER_AUTO)
    print(f"  gamma 1.00: max {base['max']:.0f}  mean {base['mean']:.2f}  "
          f"{base['above']} px")
    print(f"  gamma 0.50: max {dark['max']:.0f}  mean {dark['mean']:.2f}  "
          f"{dark['above']} px")
    print(f"  gamma AUTO: max {auto['max']:.0f}  mean {auto['mean']:.2f}  "
          f"{auto['above']} px")
    check("gamma 0.50 reports the same difference as gamma 1.00",
          numbers(dark) == numbers(base),
          f"max {dark['max']:.0f} vs {base['max']:.0f}")
    check("Auto gamma reports the same difference as gamma 1.00",
          numbers(auto) == numbers(base),
          f"max {auto['max']:.0f} vs {base['max']:.0f}")
    check("the difference is not empty (the test would pass on nothing otherwise)",
          base["above"] > 100 and base["max"] > 5,
          f"{base['above']} px, max {base['max']:.0f}")


def test_gamma_still_changes_the_picture(ref_path, cur_path):
    """It must still DO something — it moved from before the difference to after it,
    it was not dropped."""
    ra = ref_array(ref_path, 0)
    def pixels(gamma):
        img = is_t.load_image_scaled(cur_path, 0, False, gradient_id=1,
                                     ref_image=ra, gamma=gamma)
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(img.sizeInBytes())
        return np.frombuffer(ptr, dtype=np.uint8).reshape(
            img.height(), img.bytesPerLine())[:, :img.width()].copy()
    flat = pixels(img_scale.GAMMA_SLIDER_NEUTRAL)
    bent = pixels(50)
    check("gamma still brightens the difference on screen",
          float(bent.mean()) > float(flat.mean()) + 1.0,
          f"mean code {flat.mean():.1f} -> {bent.mean():.1f}")


# ── 2. auto brightness/contrast are frozen for the run ───────────────────────────
def test_auto_is_held_across_frames():
    """Two frames whose differences have different brightness must be adjusted by the
    SAME numbers once the run has frozen them."""
    first = {"contrast": 31, "offset": -7, "gamma": 100}
    br, bc = is_t.Viewer._apply_sub_hold(
        1, is_t._RenderBC(0, 0, 1, img_scale.GAMMA_SLIDER_AUTO), first)
    check("a held run renders with no Auto left in the parameters",
          br == 0 and bc.auto == 0, f"brighten={br}, auto={bc.auto}")
    check("the held contrast, offset and gamma are the ones that were measured",
          (bc.contrast, bc.offset, bc.gamma) == (31, -7, 100),
          str((bc.contrast, bc.offset, bc.gamma)))
    br2, bc2 = is_t.Viewer._apply_sub_hold(
        1, is_t._RenderBC(0, 0, 1, img_scale.GAMMA_SLIDER_AUTO), first)
    check("the same hold gives the same render parameters every frame",
          (br2, bc2) == (br, bc))
    # Nothing held: the controls go through untouched.
    br3, bc3 = is_t.Viewer._apply_sub_hold(1, is_t._RenderBC(0, 0, 1, 0), None)
    check("with nothing held the controls are untouched",
          br3 == 1 and bc3.auto == 1 and bc3.gamma == 0)


def test_hold_is_all_or_nothing(ref_path):
    """A half-filled hold would leave the other row measuring itself on every frame."""
    is_t._auto_bc_put(ref_path, {"contrast": 12, "gamma": 0.8})
    both = is_t.Viewer._sub_hold_from_render(ref_path, True, True, False)
    check("a hold is refused while one ticked box has not landed", both is None)
    one = is_t.Viewer._sub_hold_from_render(ref_path, True, False, False)
    check("a hold of the one box that is on is taken", one == {"contrast": 12},
          str(one))
    none = is_t.Viewer._sub_hold_from_render(ref_path, False, False, False)
    check("no Auto box on means nothing to hold", none is None)


# ── 3. the numbers come only from a full-size render ─────────────────────────────
class _PanelRules:
    """Just the two methods the INFO panel decides with."""
    _stats_are_final = staticmethod(is_t.Viewer._stats_are_final)
    _frame_of_key = staticmethod(is_t.Viewer._frame_of_key)
    _pick_diff_stats = is_t.Viewer._pick_diff_stats


def test_small_render_never_replaces_the_measured_numbers():
    p = _PanelRules()
    small = {"max": 41.0, "above": 900, "side": 400}
    full = {"max": 96.0, "above": 1310, "side": is_t.FULL_RES_SIDE}

    st, prov = p._pick_diff_stats(small, "frameA", None)
    check("with nothing measured yet the estimate is shown, and marked",
          st is small and prov is True)

    st, prov = p._pick_diff_stats(full, "frameA", None)
    check("a full-size render is shown unmarked", st is full and prov is False)

    st, prov = p._pick_diff_stats(small, "frameA", ("frameA", full))
    check("a later small render of the SAME frame keeps the measured numbers",
          st is full and prov is False, f"max {st['max']:.0f}")

    st, prov = p._pick_diff_stats(small, "frameB", ("frameA", full))
    check("another frame's measured numbers are never borrowed",
          st is small and prov is True, f"max {st['max']:.0f}")

    st, prov = p._pick_diff_stats(None, "frameB", ("frameA", full))
    check("nothing to show for this frame stays nothing", st is None)

    old = {"max": 12.0, "above": 3}      # written before "side" existed
    check("statistics with no size recorded are taken at face value",
          p._pick_diff_stats(old, "frameA", None)[1] is False)


def test_the_estimate_says_so_on_the_line():
    st = {"above": 1234, "bg": 0.0}
    plain = is_t.Viewer._fmt_diff_stats(st)
    marked = is_t.Viewer._fmt_diff_stats(st, provisional=True)
    print(f"  measured: {plain!r}")
    print(f"  estimate: {marked!r}")
    check("a measured line carries no mark", "…" not in plain, plain)
    check("an estimate is marked on the line itself", marked.endswith(" …"), marked)
    check("both still print the pixel count", "1 234" in plain and "1 234" in marked)


def test_the_decode_size_reaches_the_statistics(ref_path, cur_path):
    """The size is what the whole rule is built on, so it has to arrive."""
    ra_small = ref_array(ref_path, 64)
    sig = is_t.LoaderSignals()
    for side in (0, 64):
        ra = ref_array(ref_path, side)
        task = is_t.LoadTask(0, 1, 0, cur_path, side, False, 1, sig,
                             is_t._RENDER_BC_NONE, ra, 0, 0, key=("frame", side))
        task.run()
        st = is_t._diff_stats_get(("frame", side))
        check(f"the statistics of a max_side={side} render record that size",
              st is not None and st.get("side") == side,
              str(st.get("side") if st else None))
    small = is_t._diff_stats_get(("frame", 64))
    full = is_t._diff_stats_get(("frame", 0))
    print(f"  full size : max {full['max']:.0f}, {full['above']} px")
    print(f"  shrunken  : max {small['max']:.0f}, {small['above']} px")
    check("a shrunken render really does report a different maximum "
          "(this is the reported bug)",
          small["max"] != full["max"] or small["above"] != full["above"],
          f"{small['max']:.0f} vs {full['max']:.0f}")
    check("only the full-size one counts as final",
          is_t.Viewer._stats_are_final(full) and not is_t.Viewer._stats_are_final(small))
    del ra_small


# ── 4. the numbers are the same with Subtraction OFF ─────────────────────────────
def test_measuring_without_subtracting(ref_path, cur_path):
    """With a reference set, the frame is MEASURED against it whether or not the
    difference is what is on screen — that is what lets the histogram stay put while
    Subtraction is off, instead of vanishing and dragging the whole settings column
    (and the checkbox) 110 px up under the cursor."""
    ra = ref_array(ref_path, 0)

    on = {}
    img_on = is_t.load_image_scaled(cur_path, 0, False, gradient_id=1, ref_image=ra,
                                    stats_out=on, subtract=True)
    off = {}
    img_off = is_t.load_image_scaled(cur_path, 0, False, gradient_id=1, ref_image=ra,
                                     stats_out=off, subtract=False)
    plain = is_t.load_image_scaled(cur_path, 0, False, gradient_id=1)

    print(f"  Subtraction ON : max {on['max']:.0f}  mean {on['mean']:.2f}  "
          f"{on['above']} px")
    print(f"  Subtraction OFF: max {off['max']:.0f}  mean {off['mean']:.2f}  "
          f"{off['above']} px")
    check("the numbers are IDENTICAL with Subtraction off",
          numbers(off) == numbers(on),
          f"max {off['max']:.0f} vs {on['max']:.0f}")

    def px(img):
        i = img.convertToFormat(QImage.Format.Format_Grayscale8)
        ptr = i.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(i.sizeInBytes())
        return np.frombuffer(ptr, dtype=np.uint8).reshape(
            i.height(), i.bytesPerLine())[:, :i.width()].copy()

    check("with Subtraction off the PICTURE is the ordinary frame, not the difference",
          np.array_equal(px(img_off), px(plain)))
    check("...and with it on the picture IS the difference",
          not np.array_equal(px(img_on), px(plain)))

    # And the display controls still do not touch it.
    off_g = {}
    is_t.load_image_scaled(cur_path, 0, True, gradient_id=1, brightness_offset=20,
                           ref_image=ra, auto_bright=1, contrast=40, stats_out=off_g,
                           subtract=False, gamma=50)
    check("measuring is untouched by gamma, contrast, brightness and Auto",
          numbers(off_g) == numbers(on),
          f"max {off_g['max']:.0f} vs {on['max']:.0f}")


def test_the_numbers_survive_the_checkbox():
    """The measure key deliberately does NOT carry the checkbox, so the numbers of a
    frame are found again after the box is toggled — the panel does not go blank and
    nothing moves."""
    class _V:
        _sub_params = lambda self, ref: (3, 7)
        _ref_render = is_t.Viewer._ref_render
        class cb_subtract:
            state = True
            @classmethod
            def isChecked(cls):
                return cls.state
    v = _V()
    ref = np.zeros((4, 4), dtype=np.float32)
    on = v._ref_render("frameA", ref)
    _V.cb_subtract.state = False
    off = v._ref_render("frameA", ref)
    print(f"  measure key ON : {on.mkey}")
    print(f"  measure key OFF: {off.mkey}")
    check("the numbers of a frame live under one key whatever the checkbox says",
          on.mkey == off.mkey)
    check("the PICTURE key still changes, because the picture does",
          on.key_parts != off.key_parts,
          f"{on.key_parts} vs {off.key_parts}")
    check("with Subtraction off the picture key is the one it had with no reference",
          off.key_parts == (None, 0, 0), str(off.key_parts))
    check("the frame is the first field of a measure key, as _frame_of_key expects",
          is_t.Viewer._frame_of_key(on.mkey) == "frameA")


# ── 5. the same frame twice is still exactly zero ────────────────────────────────
def test_identical_frame_subtracts_to_zero(ref_path):
    ra = ref_array(ref_path, 0)
    st = render(ref_path, ra)
    check("a frame subtracted from itself differs nowhere",
          int(st["above"]) == 0 and float(st["max"]) == 0.0,
          f"{st['above']} px, max {st['max']:.0f}")
    st_g = render(ref_path, ra, gamma=50)
    check("...with gamma set too", int(st_g["above"]) == 0 and float(st_g["max"]) == 0.0,
          f"{st_g['above']} px, max {st_g['max']:.0f}")


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    ref16, cur16 = make_pair()
    ref_path = write_frame("TESTCAM_1700000000000000000.png", ref16)
    cur_path = write_frame("TESTCAM_1700000000500000000.png", cur16)

    print("gamma is outside the difference")
    test_gamma_does_not_move_the_numbers(ref_path, cur_path)
    test_gamma_still_changes_the_picture(ref_path, cur_path)

    print("\nAuto is measured once and held")
    test_auto_is_held_across_frames()
    test_hold_is_all_or_nothing(ref_path)

    print("\nthe numbers come from the full-size render")
    test_small_render_never_replaces_the_measured_numbers()
    test_the_estimate_says_so_on_the_line()
    test_the_decode_size_reaches_the_statistics(ref_path, cur_path)

    print("\nthe numbers survive Subtraction being switched off")
    test_measuring_without_subtracting(ref_path, cur_path)
    test_the_numbers_survive_the_checkbox()

    print("\nthe same frame twice")
    test_identical_frame_subtracts_to_zero(ref_path)

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    del app
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
