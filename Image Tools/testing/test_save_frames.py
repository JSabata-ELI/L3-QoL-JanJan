"""Save As saves the frames that are ON THE WALL — every one of them.

The fault: `save_primary_files_as` never looked at the wall. It called
`_collect_primary_files_async`, which re-picks ONE frame per picked camera folder
out of the hour in the Time window. Six cameras showing five picked moments each —
thirty frames on screen — wrote six files, and not even those six.

Pinned here:

  * `_frames_on_the_wall` finds every frame across every tab, once each (the
    per-camera tabs and Day-by-day hold the SAME frames), and leaves out a tile
    with nothing behind it;
  * the count that lands on disk is the count on the wall, not the camera count;
  * a folder for each camera, and the frames inside it;
  * one PDF with a page per frame; one picture per camera; one picture with every
    frame on it, four across;
  * a grayscale 16-bit frame comes out as a VISIBLE 8-bit picture on the tab's own
    absolute scale — PIL's own 16-bit→RGB clips at 255, which is why a grayscale
    save with a PV bar used to come out white;
  * the PV bar is drawn by the program's one bar drawer, and shows up as extra
    height under the frame;
  * with nothing on the wall the old per-camera collect is still the fallback;
  * the dialog's answers round-trip, and "A folder for each camera" is greyed out
    for the single-file modes, where it means nothing.

Offscreen, no share and no archiver.
"""
import shutil
import sys
import tempfile
import types
from datetime import date, datetime, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []

DAY_A = date(2026, 9, 3)
DAY_B = date(2026, 9, 4)
CAMS = ("C03-035-PTM11WNF", "C03-039-PAM10NF", "C03-040-PFM13NF")
FRAME_W, FRAME_H = 60, 40
# The frames must sit in an archive-shaped folder and carry MaxValue, or img_scale
# has no camera and no bracket to work with and collapses to the plain 65535
# fallback — the render checks below are about the 12-bit sensor range.
#
# And the STORED number is not the count: the archiver stretches each frame's own
# bracket up to fill the 16-bit container, so a 12-bit frame's 4095 counts are
# stored as 65535. A stored 32768 therefore means half the sensor range, which is
# what makes the render come out mid-grey.
SENSOR_MAX = 4095
PEAK = 32768


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(day: date, hour: int, minute: int = 0, second: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(day.year, day.month, day.day, hour, minute, second,
                        tzinfo=tz).timestamp() * 1e9)


def write_frame(path: Path, peak: int = PEAK) -> Path:
    """A 16-bit frame in the shape the archive stores: 12-bit counts in an I;16 PNG
    with the `MaxValue` tEXt chunk the archiver writes."""
    import numpy as np
    from PIL import Image as PilImage, PngImagePlugin
    arr = np.zeros((FRAME_H, FRAME_W), dtype=np.uint16)
    arr[8:32, 12:48] = peak
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("MaxValue", str(SENSOR_MAX))
    PilImage.fromarray(arr).save(str(path), pnginfo=meta)
    return path


class FakeWall:
    """Stands in for a `_DayWall` — the save path only ever asks it for its cells."""

    def __init__(self, cells):
        self._cells = list(cells)

    def cells(self):
        return list(self._cells)


def build_wall_frames(root: Path) -> "tuple[list, list]":
    """Three cameras × two picked moments, on two different days.

    Six frames on screen. The old code wrote three, one per camera folder — which
    is the whole point of the count checks below.
    """
    picks = [(1, DAY_A, ns_at(DAY_A, 9, 48, 58)),
             (2, DAY_B, ns_at(DAY_B, 13, 8, 41))]
    cells = []
    paths = []
    for pick, day, ts in picks:
        for cam in CAMS:
            folder = f"{cam}-_-IMG"
            p = write_frame(root / folder / f"{folder}_-_{ts}.png")
            paths.append(p)
            cells.append({"path": p, "cam": cam, "cam_folder": folder, "day": day,
                          "ts_ns": ts, "pick": pick, "region": None,
                          "status": "found"})
    return cells, paths


def make_stub(m, cells, cam_tabs=True):
    """A stub carrying only what the save path reads, with the REAL methods bound.

    Nothing here re-implements the code under test: every method checked is the
    shipping one, taken off `ImageFinderWidget` and bound to this namespace. That is
    the only way the test cannot drift from the app.
    """
    walls = []
    if cam_tabs:
        # The tabs as they really are: one wall per camera, plus the Day-by-day wall
        # holding the same frames again. The union must come out deduplicated.
        for cam in CAMS:
            walls.append(FakeWall([c for c in cells if c["cam"] == cam]))
    walls.append(FakeWall(cells))

    logged: list = []
    stub = types.SimpleNamespace(
        _all_walls=lambda: list(walls),
        _wall=walls[-1] if walls else None,
        _log=lambda msg: logged.append(str(msg)),
        _log_safe=lambda msg: logged.append(str(msg)),
        _gradient_cb=None,
        _logged=logged,
    )
    for name in ("_frames_on_the_wall", "_save_caption", "_save_display_pil",
                 "_save_pv_bar_onto", "_save_frames_each", "_save_frames_pdf",
                 "_save_frames_sheet", "_save_frames_per_camera",
                 "_save_sheet_image", "_save_sheet_heading",
                 "_save_frames_per_row", "_save_stamp_caption",
                 "_apply_gradient_to_image"):
        setattr(stub, name, getattr(m.ImageFinderWidget, name).__get__(stub))
    stub._SHEET_COLS = m.ImageFinderWidget._SHEET_COLS
    stub._SHEET_COLS_MAX = m.ImageFinderWidget._SHEET_COLS_MAX
    stub._SHEET_CELL_MAX = m.ImageFinderWidget._SHEET_CELL_MAX
    return stub


def render_opts(grad="Grayscale", pv_bar=False, energy=None):
    """The snapshot `_save_frames_with_options` takes off the widgets.

    `gamma` is a slider integer, never None — `_bc_args` always returns one, and
    `_apply_gradient_to_image` reads the widgets back when handed a None, which is
    exactly what a worker thread must not do."""
    import img_scale
    return {"grad_name": grad, "auto": False,
            "gamma": img_scale.GAMMA_SLIDER_NEUTRAL, "contrast": 0,
            "offset": 0, "sel_cols": ["sbw4"], "pv_bar": pv_bar,
            "energy": energy or {}}


# ── the checks ────────────────────────────────────────────────────────────────
def check_collect(m, cells):
    stub = make_stub(m, cells)
    frames = stub._frames_on_the_wall()
    check("every frame on the wall is collected once",
          len(frames) == 6, f"{len(frames)} (expected 6)")
    check("no frame is collected twice",
          len({str(f['path']) for f in frames}) == 6)
    check("the camera count is NOT the frame count",
          len(frames) != len(CAMS), f"{len(frames)} frames, {len(CAMS)} cameras")
    check("each frame keeps its camera, day and pick",
          all(f["cam"] in CAMS and f["day"] in (DAY_A, DAY_B)
              and f["pick"] in (1, 2) for f in frames))
    check("collected in reading order (camera, then day)",
          [f["cam"] for f in frames] == sorted(f["cam"] for f in frames))

    # A camera that had nothing near the moment: a tile with no file behind it.
    blank = list(cells) + [{"path": None, "cam": "C03-099-NONE", "day": DAY_A,
                            "ts_ns": None, "pick": 1, "status": "no_frame"}]
    got = make_stub(m, blank)._frames_on_the_wall()
    check("a tile with no frame is left out",
          len(got) == 6 and all(f["path"] is not None for f in got),
          f"{len(got)}")


def check_each(m, cells, tmp):
    stub = make_stub(m, cells)
    frames = stub._frames_on_the_wall()

    flat = tmp / "flat"
    msg = stub._save_frames_each(frames, flat, False, render_opts())
    files = sorted(p for p in flat.rglob("*") if p.is_file())
    check("a file for each frame, all six written",
          len(files) == 6, f"{len(files)}: {[p.name for p in files]}")
    check("the message says six", "6 of 6" in msg, msg.splitlines()[0])
    check("an untouched grayscale frame is copied whole",
          all(p.stat().st_size > 0 for p in files))

    subs = tmp / "subs"
    stub._save_frames_each(frames, subs, True, render_opts())
    dirs = sorted(d.name for d in subs.iterdir() if d.is_dir())
    check("a folder for each camera",
          dirs == sorted(CAMS), f"{dirs}")
    check("two frames in each camera's folder",
          all(len(list((subs / c).glob('*.png'))) == 2 for c in CAMS),
          str({c: len(list((subs / c).glob('*.png'))) for c in CAMS}))

    # Two frames of one camera on two days must not collide on one name.
    names = {p.name for p in (subs / CAMS[0]).glob("*.png")}
    check("the two days give two different file names", len(names) == 2, str(names))


def check_render(m, cells, tmp):
    """A grayscale 16-bit frame must come out VISIBLE, not clipped white."""
    from PIL import Image as PilImage
    import numpy as np
    stub = make_stub(m, cells)
    src = cells[0]["path"]

    img = stub._save_display_pil(src, render_opts())
    arr = np.array(img)
    check("grayscale render is 8-bit RGB", img.mode == "RGB" and arr.dtype == np.uint8,
          f"{img.mode} {arr.dtype}")
    lit = int(arr[..., 0].max())
    dark = int(arr[..., 0].min())
    check("the lit part is mid-grey, not blown white",
          0 < lit < 250, f"peak {lit}")
    check("the dark part stayed dark", dark < 20, f"floor {dark}")
    check("half the sensor range renders mid-grey",
          100 < lit < 160, f"peak {lit} (expect ~128)")

    got = stub._save_display_pil(src, render_opts(grad="Gradient"))
    a2 = np.array(got)
    check("a palette gives a coloured picture",
          got.mode == "RGB" and (a2[..., 0] != a2[..., 2]).any())


def check_pv_bar(m, cells, tmp):
    stub = make_stub(m, cells)
    src = cells[0]["path"]
    row = m._EnergyRow(datetime.now(timezone.utc), {"sbw4": "12.34"})
    r = render_opts(pv_bar=True, energy={str(src): (row, None, None)})

    plain = stub._save_display_pil(src, render_opts())
    barred = stub._save_pv_bar_onto(stub._save_display_pil(src, r), src, r)
    check("the PV bar adds height under the frame",
          barred.height > plain.height and barred.width == plain.width,
          f"{plain.size} → {barred.size}")

    out = tmp / "bar"
    stub._save_frames_each(stub._frames_on_the_wall(), out, False, r)
    files = sorted(out.glob("*.png"))
    check("all six are saved with the bar", len(files) == 6, f"{len(files)}")
    if files:
        from PIL import Image as PilImage
        with PilImage.open(files[0]) as im:
            check("a saved barred frame is taller than the frame",
                  im.height > FRAME_H, f"{im.size}")

    off = stub._save_pv_bar_onto(plain, src, render_opts())
    check("no bar asked for, no bar drawn", off.height == plain.height)


def check_sheet(m, cells, tmp):
    stub = make_stub(m, cells)
    frames = stub._frames_on_the_wall()
    sheet = stub._save_sheet_image(frames, render_opts(), "heading")

    # Three cameras × two moments each divides evenly, so the row break lands on
    # the camera: two across, three rows, each row one camera's moments in order.
    imgs = [(f, None) for f in frames]
    check("a row is one camera when the frames divide evenly",
          stub._save_frames_per_row(imgs) == 2,
          f"{stub._save_frames_per_row(imgs)} across")
    check("...and the sheet really came out two across",
          sheet.width < FRAME_W * 3, f"{sheet.size}")
    check("three rows, so it is taller than it is wide",
          sheet.height > sheet.width, f"{sheet.size}")

    # An uneven set has no camera boundary to wrap on, so the plain grid stands.
    uneven = [(f, None) for f in frames[:5]]
    check("an uneven set falls back to the four-across grid",
          stub._save_frames_per_row(uneven) == 0,
          f"{stub._save_frames_per_row(uneven)}")
    one_cam = [(f, None) for f in frames if f["cam"] == CAMS[0]]
    check("one camera is not split into rows of one",
          stub._save_frames_per_row(one_cam) == 0)
    wide = [({"cam": c}, None) for c in CAMS for _ in range(9)]
    check("more moments than fit across falls back too",
          stub._save_frames_per_row(wide) == 0,
          f"{stub._save_frames_per_row(wide)}")

    out = tmp / "sheet.png"
    msg = stub._save_frames_sheet(frames, out, render_opts())
    check("one picture written", out.exists() and str(out) in msg)

    percam = tmp / "percam"
    msg = stub._save_frames_per_camera(frames, percam, render_opts())
    files = sorted(p.stem for p in percam.glob("*.png"))
    check("one picture for each camera",
          len(files) == len(CAMS), f"{files}")
    check("each is named after its camera",
          all(any(c.replace('-', '_') in f or c in f for f in files) for c in CAMS),
          f"{files}")


def check_pdf(m, cells, tmp):
    stub = make_stub(m, cells)
    frames = stub._frames_on_the_wall()
    out = tmp / "frames.pdf"
    msg = stub._save_frames_pdf(frames, out, render_opts())
    check("a PDF is written", out.exists() and out.stat().st_size > 1000,
          f"{out.stat().st_size if out.exists() else 0} bytes")
    check("a page for each frame", "6 page(s)" in msg, msg.splitlines()[0])
    raw = out.read_bytes()
    check("the PDF really holds six pages",
          raw.count(b"/Type /Page\n") >= 6 or raw.count(b"/Type/Page") >= 6
          or raw.count(b"/Page") >= 6, f"{raw.count(b'/Page')} /Page tokens")


def check_caption(m, cells):
    stub = make_stub(m, cells)
    cap = stub._save_caption(cells[0])
    check("the caption leads with the pick number", cap.startswith("1)"), cap)
    check("the caption names the camera", CAMS[0] in cap, cap)
    check("the caption carries the frame's own time", "09:48:58" in cap, cap)
    no_pick = dict(cells[0]); no_pick["pick"] = None
    check("no pick number when only one moment was picked",
          not stub._save_caption(no_pick).startswith("1)"),
          stub._save_caption(no_pick))


def check_fallback(m, tmp):
    """Nothing on the wall — the per-camera collect is still what runs."""
    called = {}
    stub = types.SimpleNamespace(
        _all_walls=lambda: [],
        _wall=None,
        _log=lambda msg: None,
        _collect_primary_files_async=lambda cb: called.setdefault("cb", cb),
        _save_frames_with_options=lambda frames: called.setdefault("frames", frames),
        primary_files=None,
    )
    for name in ("_frames_on_the_wall", "save_primary_files_as"):
        setattr(stub, name, getattr(m.ImageFinderWidget, name).__get__(stub))
    stub.save_primary_files_as()
    check("with an empty wall it falls back to the camera collect",
          "cb" in called and "frames" not in called)

    # ...and what the collect hands back still reaches the same dialog.
    p = write_frame(tmp / "fallback" / CAMS[0] / f"{CAMS[0]}_-_{ns_at(DAY_A, 9)}.png")
    called["cb"]([p])
    got = called.get("frames") or []
    check("the collected frame goes through the same save path",
          len(got) == 1 and got[0]["path"] == p, f"{got}")

    # A wall with frames must NOT go near the collect.
    cells, _ = build_wall_frames(tmp / "nofallback")
    called2 = {}
    stub2 = make_stub(m, cells)
    stub2._collect_primary_files_async = lambda cb: called2.setdefault("cb", cb)
    stub2._save_frames_with_options = lambda frames: called2.setdefault("frames", frames)
    stub2.primary_files = None
    stub2.save_primary_files_as = m.ImageFinderWidget.save_primary_files_as.__get__(stub2)
    stub2.save_primary_files_as()
    check("with frames on the wall the camera collect is never called",
          "cb" not in called2 and len(called2.get("frames") or []) == 6,
          f"{len(called2.get('frames') or [])}")


def check_dialog(m):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    dlg = m._SaveFramesDialog(30, 6, True)
    labels = [w.text() for w in dlg.findChildren(m.QLabel)]
    check("it says how many frames and how many cameras",
          any("30 frame" in t and "6 camera" in t for t in labels), str(labels[:1]))

    o = dlg.result_options()
    check("a file for each frame is the default",
          o["mode"] == "each", o["mode"])
    check("the PV switch comes in from the panel", o["pv_bar"] is True)
    check("a folder per camera is on by default with several cameras",
          o["subfolders"] is True)

    dlg._modes["pdf"].setChecked(True)
    o = dlg.result_options()
    check("picking one PDF changes the mode", o["mode"] == "pdf", o["mode"])
    check("a folder per camera is dropped for a single file",
          o["subfolders"] is False)
    check("...and the box is greyed out, not silently ignored",
          not dlg._cb_subfolders.isEnabled())

    dlg._modes["each"].setChecked(True)
    check("going back re-enables it", dlg._cb_subfolders.isEnabled())

    dlg._cb_pv.setChecked(False)
    check("the PV switch can be turned off in the dialog",
          dlg.result_options()["pv_bar"] is False)

    one = m._SaveFramesDialog(1, 1, False)
    check("one camera does not ask for a folder each",
          one.result_options()["subfolders"] is False)
    labels = [w.text() for w in one.findChildren(m.QLabel)]
    check("singular wording for one frame",
          any("1 frame from 1 camera" in t for t in labels), str(labels[:1]))


def main():
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    tmp = Path(tempfile.mkdtemp(prefix="save_frames_"))
    try:
        cells, _paths = build_wall_frames(tmp / "archive")
        print("\nWhat the wall is holding")
        check_collect(m, cells)
        print("\nA file for each frame")
        check_each(m, cells, tmp)
        print("\nHow a frame is rendered")
        check_render(m, cells, tmp)
        print("\nThe PV bar")
        check_pv_bar(m, cells, tmp)
        print("\nOne picture")
        check_sheet(m, cells, tmp)
        print("\nOne PDF")
        check_pdf(m, cells, tmp)
        print("\nThe caption")
        check_caption(m, cells)
        print("\nNothing on the wall")
        check_fallback(m, tmp)
        print("\nThe dialog")
        check_dialog(m)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
