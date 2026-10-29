"""Laser settings — the DM's Applied voltages grid (the LEFT one) read off a
picture of the remote desktop.

    python testing/test_dm_grid.py

`dm_0727.png` and `dm_0731.png` are the remote PC's screen on 8.10.2026 at 7:27
and 7:31: SID4 on the left, Mirror Control Window on the right, the mirror on
two different set-ups: 7:27 is High power, 7:31 is Low power — the two built
into `ls_dm.REFERENCE_SETUPS`. `dm_0731.png` was saved
with a white strip under it, which is kept: a picture bigger than the screen is
one more thing the search must not care about. Pillow is needed here only to
open and change the pictures; the program does not use it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PIL import Image                                           # noqa: E402

import ls_dm as D                                               # noqa: E402
import ls_screen as S                                           # noqa: E402

FAILED = []

# The Applied voltages grid's cells (the LEFT grid, read since 12.10.2026) on
# both screenshots, frame left out. The Set-up grid is SET_UP_SPOT.
SPOT = D.Spot(1301, 322, 1498, 519)
SET_UP_SPOT = D.Spot(1613, 322, 1810, 519)

# The top row of each.
TOP_ROW = {
    "dm_0731.png": [(200, 200, 255), (200, 200, 255), (255, 230, 230),
                    (240, 240, 255), (255, 255, 255), (255, 210, 210)],
    "dm_0727.png": [(200, 200, 255), (205, 205, 255), (255, 181, 181),
                    (230, 230, 255), (255, 255, 255), (255, 245, 245)],
}


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def picture(img):
    """A PIL image as the BGRA bytes `grab_window` hands over."""
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    return S.Picture(rgba.width, rgba.height, bgra.tobytes())


def screen(name):
    return Image.open(HERE / name).convert("RGB")


def read(img, hint=None):
    return D.read_picture(picture(img), hint, now_s=1000.0)


def test_both_screenshots():
    print("both screenshots: the left-hand grid, its edges and its colours")
    for name, top in TOP_ROW.items():
        pic = picture(screen(name))
        bars = D.find_bars(pic)
        check(f"{name}: two scales of the two grids, SID4's is not one",
              len(bars), 2)
        spot = D.grid_right_of(pic, bars[0]) if bars else None
        check(f"{name}: the Applied voltages grid's edges", spot, SPOT)
        check(f"{name}: Set-up is never taken",
              D.grid_right_of(pic, bars[-1]) if bars else None, None)
        check(f"{name}: (Set-up is still found looking left)",
              D.grid_left_of(pic, bars[-1]) if bars else None, SET_UP_SPOT)
        got = D.read_picture(pic, now_s=1000.0)
        check(f"{name}: read", got.status, D.STATUS_OK)
        check(f"{name}: top row", (got.colours or [])[:6], top)


def test_the_two_set_ups_are_told_apart():
    print("the two set-ups are different, and each matches itself")
    a = read(screen("dm_0727.png")).colours
    b = read(screen("dm_0731.png")).colours
    d = D.colour_difference(a, b)
    print(f"        they differ by up to {d} levels in "
          f"{len(D.cells_differing(a, b))} electrodes")
    check("far more apart than the tolerance", d > 3 * D.MATCH_TOL, True)
    setups = {"Low": {"colours": a}, "High": {"colours": b}}
    check("7:27 is Low", D.match(a, setups).name, "Low")
    check("7:31 is High", D.match(b, setups).name, "High")
    check("the same picture again is identical",
          D.colour_difference(a, read(screen("dm_0727.png")).colours), 0)


def test_a_scaled_window():
    print("the Remote Desktop window made smaller or bigger reads the same")
    img = screen("dm_0727.png")
    want = read(img).colours
    setups = {"Low": {"colours": want}}
    for factor in (0.5, 0.75, 1.25):
        for how, kind in ((Image.NEAREST, "sharp"), (Image.BILINEAR, "soft")):
            small = img.resize((round(img.width * factor),
                                round(img.height * factor)), how)
            got = read(small)
            check(f"{factor:.2f}x {kind}: read", got.status, D.STATUS_OK)
            check(f"{factor:.2f}x {kind}: still Low",
                  D.match(got.colours, setups).name, "Low")


def test_the_window_frame_and_covering_windows():
    print("a window title bar around it, and other windows over the rest")
    img = screen("dm_0727.png")
    want = read(img).colours
    framed = Image.new("RGB", (img.width + 16, img.height + 40), (240, 240, 240))
    framed.paste(img, (8, 32))
    check("inside a window frame", read(framed).colours, want)
    covered = img.copy()
    covered.paste((236, 236, 236), (0, 0, 1240, 1080))         # over SID4
    covered.paste((236, 236, 236), (1505, 0, 1920, 1080))      # over Set-up
    covered.paste((30, 30, 30), (1240, 560, 1505, 900))        # under the grid
    check("everything but the grid covered", read(covered).colours, want)
    hidden = img.copy()
    hidden.paste((236, 236, 236), (1240, 380, 1505, 700))      # over the grid
    check("the grid itself covered: no grid, Set-up is never read instead",
          read(hidden).status, D.STATUS_NO_GRID)


def test_the_hint_is_used_and_then_dropped():
    print("where the scale was is tried first, and a stale place is not trusted")
    img = screen("dm_0727.png")
    first = read(img)
    check("the scale is remembered", first.bar is not None, True)
    check("read again from there", read(img, first.bar).colours, first.colours)
    moved = Image.new("RGB", img.size, (0, 0, 0))
    moved.paste(img.crop((300, 0, 1920, 1080)), (0, 60))
    got = read(moved, first.bar)
    check("the window moved: found again", got.colours, first.colours)


def test_nothing_to_read():
    print("a blank or black window is no grid, never a set-up")
    for colour in ((255, 255, 255), (0, 0, 0)):
        got = read(Image.new("RGB", (1280, 800), colour))
        check(f"{colour}", (got.status, got.colours), (D.STATUS_NO_GRID, None))

    print("and the sentence says what the picture did hold")
    black = read(Image.new("RGB", (1280, 800), (0, 0, 0))).problem
    check("black", "almost all black" in black and "1280 × 800" in black,
          True)
    white = read(Image.new("RGB", (1280, 800), (255, 255, 255))).problem
    check("no scale", "no red-white-blue colour scale" in white, True)
    img = screen("dm_0727.png")
    img.paste((0, 0, 0), (SPOT.x0, SPOT.y0, SPOT.x1 + 1, SPOT.y1 + 1))
    hidden = read(img).problem
    check("scales but no grid", "found, but no 6 × 6 grid" in hidden, True)


def test_through_remote_desktop():
    print("OPR1's own picture, 9.10.2026: Remote Desktop's lossy colours")
    pic = picture(screen("dm_opr1_rdp.png"))
    check("both scales found, the blended edge column notwithstanding",
          len(D.find_bars(pic)), 2)
    got = D.read_picture(pic, None, now_s=1000.0)
    check("read", got.status, D.STATUS_OK)
    m = D.match(got.colours, D.with_references({}))
    # The mirror was on high power then (the user, 9.10.2026).
    check("it is High power", m.name, D.POWER_HIGH)
    check("a few levels off, well inside the tolerance",
          m.difference <= D.MATCH_TOL // 2, True)
    low = D.with_references({})[D.POWER_LOW]["colours"]
    check("and still far from Low power",
          D.colour_difference(got.colours, low) > 3 * D.MATCH_TOL, True)


def test_the_scale_edge_column():
    print("OPR1's own picture, 27.10.2026: the scale's edge column passes for "
          "a cell")
    pic = picture(screen("dm_opr1_edge.png"))
    bars = D.find_bars(pic)
    check("both scales found", len(bars), 2)
    # The scale is measured one pixel short: the column after it is still
    # scale colour, (255, 23, 20), on every row but the one it is measured on.
    check("the Applied voltages grid's edges, not the scale's edge column",
          D.grid_right_of(pic, bars[0]) if bars else None,
          D.Spot(1291, 347, 1488, 544))
    got = D.read_picture(pic, None, now_s=1000.0)
    check("read", got.status, D.STATUS_OK)
    check("the Set-up grid read too", got.setup_colours is not None, True)


def test_the_picture_is_saved():
    import io
    import tempfile
    print("the picture of the window, saved as PNG on the share")
    img = screen("dm_0731.png")
    pic = picture(img)
    back = Image.open(io.BytesIO(D.png_bytes(pic))).convert("RGB")
    check("PNG reads back pixel for pixel", back.tobytes(), img.tobytes())
    with tempfile.TemporaryDirectory() as tmp:
        share = D.Share(roots=[tmp])
        local = D.DmRead(D.STATUS_NO_GRID, None, 1000.0, picture=pic)
        view = D.exchange(share, local, [], save_picture=False)
        files = sorted(p.name for p in (Path(tmp) / D.SHARE_SUBDIR).iterdir())
        check("not asked: no picture", files, [D.STATE_NAME])
        check("the view does not hold the picture", view.local.picture, None)
        view = D.exchange(share, local, [], save_picture=True)
        saved = Path(tmp) / D.SHARE_SUBDIR / D.picture_name()
        check("asked: saved", saved.exists(), True)
        check("and said where", str(saved) in (view.picture_note or ""), True)
        D.exchange(share, local, [], save_picture=True)
        files = sorted(p.name for p in (Path(tmp) / D.SHARE_SUBDIR).iterdir())
        check("one file, overwritten", files,
              sorted([D.STATE_NAME, D.picture_name()]))
        both = local._replace(screen_picture=pic)
        view = D.exchange(share, both, [], save_picture=True)
        files = sorted(p.name for p in (Path(tmp) / D.SHARE_SUBDIR).iterdir())
        check("the screen copy beside it", files,
              sorted([D.STATE_NAME, D.picture_name(),
                      D.picture_name(D.HOW_SCREEN)]))
        check("the view holds neither", (view.local.picture,
                                         view.local.screen_picture),
              (None, None))


def test_one_electrode_changed():
    print("one electrode re-coloured: not the saved set-up, and which one says")
    img = screen("dm_0727.png")
    saved = read(img).colours
    cw = (SPOT.x1 - SPOT.x0 + 1) / 6
    ch = (SPOT.y1 - SPOT.y0 + 1) / 6
    row, col = 3, 4                                     # electrode 22
    x0 = int(SPOT.x0 + col * cw) + 2
    y0 = int(SPOT.y0 + row * ch) + 2
    img.paste((255, 120, 120), (x0, y0, int(x0 + cw) - 4, int(y0 + ch) - 4))
    got = read(img).colours
    m = D.match(got, {"Low": {"colours": saved}})
    check("no match", m.name, None)
    check("closest is still named", m.closest, "Low")
    check("electrode 22 is the one", m.differing, [22])


def test_speed():
    print("a full search of a 1920 x 1080 picture is cheap")
    pic = picture(screen("dm_0727.png"))
    t0 = time.perf_counter()
    first = D.read_picture(pic, now_s=1000.0)
    full = time.perf_counter() - t0
    t0 = time.perf_counter()
    D.read_picture(pic, first.bar, now_s=1000.0)
    hinted = time.perf_counter() - t0
    print(f"        full search {full * 1000:.0f} ms, from the hint "
          f"{hinted * 1000:.0f} ms")
    check("full search under 150 ms", full < 0.15, True)
    check("from the hint under 30 ms", hinted < 0.03, True)


def test_the_set_up_grid_is_read_too():
    print("the right-hand Set-up grid: read for saving, never judged")
    for name, want in (("dm_0727.png", D.POWER_HIGH),
                       ("dm_0731.png", D.POWER_LOW),
                       ("dm_opr1_rdp.png", D.POWER_HIGH)):
        got = read(screen(name))
        check(f"{name}: Set-up grid read", got.setup_colours is not None, True)
        check(f"{name}: it is the same set-up, a few levels off",
              D.colour_difference(got.colours, got.setup_colours)
              <= D.MATCH_TOL // 2, True)
        check(f"{name}: and is recognised as {want}",
              D.match(got.setup_colours, D.with_references({})).name, want)
        hinted = read(screen(name), got.bar)
        check(f"{name}: read from the hint as well",
              hinted.setup_colours, got.setup_colours)
    img = screen("dm_0727.png")
    covered = img.copy()
    covered.paste((236, 236, 236), (1505, 0, 1920, 1080))      # over Set-up
    got = read(covered)
    check("Set-up covered: the Applied grid still read, Set-up is None",
          (got.status, got.setup_colours), (D.STATUS_OK, None))

    print("a set-up saved off the right grid is recognised on the left one")
    setup = read(screen("dm_0727.png")).setup_colours
    applied = read(screen("dm_0727.png")).colours
    saved, why = D.add_setup({}, "Prepared", setup, now_s=1000.0)
    check("saved", why, None)
    check("the applied grid matches it", D.match(applied, saved).name,
          "Prepared")

    print("the state file carries it; a fresh one hands it out")
    doc = D.state_document(read(screen("dm_0727.png")), {})
    check("in the file", doc["setup_colours"], [list(c) for c in setup])
    doc["at"] = 1000.0
    check("fresh: handed out", D.setup_grid(doc, 1004.0),
          [list(c) for c in setup])
    check("stale: not", D.setup_grid(doc, 1000.0 + D.STALE_AFTER_S + 1), None)
    check("an older writer's file: none",
          D.setup_grid({k: v for k, v in doc.items()
                        if k != "setup_colours"}, 1004.0), None)
    check("unknown status: none",
          D.setup_grid({**doc, "status": D.STATUS_MINIMISED}, 1004.0), None)


def test_what_the_tab_says():
    print("the DM settings tab's verdict")
    colours = [list(c) for c in read(screen("dm_0727.png")).colours]
    other = [list(c) for c in read(screen("dm_0731.png")).colours]
    setups = {"Low power": {"colours": colours}}
    now = 10_000.0
    ok = {"at": now - 4, "host": "OPR1", "status": D.STATUS_OK,
          "colours": colours}
    check("a saved set-up", D.verdict(ok, setups, now)[:2],
          (D.VERDICT_MATCH, "Low power"))
    check("another one", D.verdict({**ok, "colours": other}, setups, now).kind,
          D.VERDICT_OTHER)
    check("minimised: unknown, no colours",
          D.verdict({**ok, "status": D.STATUS_MINIMISED, "colours": None},
                    setups, now)[::3],
          (D.VERDICT_UNKNOWN, None))
    check("old: stale, no colours",
          D.verdict({**ok, "at": now - 120}, setups, now)[::3],
          (D.VERDICT_STALE, None))
    check("no file", D.verdict(None, setups, now).kind, D.VERDICT_NONE)
    check("nothing saved yet",
          D.verdict(ok, {}, now).detail.startswith("No set-up"), True)


def test_saving_and_editing_set_ups():
    print("saving, renaming and deleting set-ups")
    colours = read(screen("dm_0727.png")).colours
    other = read(screen("dm_0731.png")).colours
    setups, why = D.add_setup({}, "Morning", colours, now_s=1000.0)
    check("saved", (sorted(setups), why), (["Morning"], None))
    check("same name refused", D.add_setup(setups, "Morning", other)[0], None)
    check("same colours refused", D.add_setup(setups, "Copy", colours)[0], None)
    check("no name refused", D.add_setup(setups, "  ", other)[0], None)
    setups, _ = D.add_setup(setups, "Evening", other)
    renamed, _ = D.rename_setup(setups, "Evening", "Late")
    check("renamed, order kept", list(renamed), ["Morning", "Late"])
    check("rename onto another refused",
          D.rename_setup(setups, "Evening", "Morning")[0], None)
    check("deleted", list(D.delete_setups(renamed, ["Late"])), ["Morning"])
    back = D.clean_setups(D.setups_document(renamed))
    check("survives the file", back["Late"]["colours"],
          [list(c) for c in other])
    check("a broken entry is dropped",
          D.clean_setups({"setups": {"x": {"colours": [1, 2]}}}), {})


def test_built_in_references():
    print("Low power and High power are built in")
    low = read(screen("dm_0731.png")).colours
    high = read(screen("dm_0727.png")).colours
    plain = D.with_references({})
    check("7:31 is Low power", D.match(low, plain).name, D.POWER_LOW)
    check("7:27 is High power", D.match(high, plain).name, D.POWER_HIGH)
    changed = [list(c) for c in low]
    changed[7] = [255, 0, 0]
    check("anything else is neither", D.match(changed, plain).name, None)
    refs = D.with_references({"Mine": {"colours": changed},
                              "Low power": {"power": D.TAG_LOW}})
    check("both first, a tick-only file entry keeps the built-in grid",
          (list(refs)[:2], D.match(refs["Low power"]["colours"], plain).name),
          ([D.POWER_LOW, D.POWER_HIGH], D.POWER_LOW))
    check("their colours cannot be saved again",
          D.add_setup(refs, "Copy", low)[0], None)
    # Deleted (21.10.2026): written as removed, and it stays off the list.
    deleted = D.delete_setups(refs, ["High power"])
    on_file = D.for_file(deleted)
    check("deleted, written as removed", on_file["High power"],
          {"removed": True})
    gone = D.with_references(D.clean_setups(D.setups_document(on_file)))
    check("and stays off the list", list(gone), ["Low power", "Mine"])
    # Renamed (12.10.2026): the built-in colours under the new name, kept in
    # the file with "replaces", and the built-in name gone from the list.
    renamed, why = D.rename_setup(refs, "Low power", "Mine low")
    check("can be renamed", why, None)
    on_file = D.for_file(renamed)
    back = D.with_references(D.clean_setups(D.setups_document(on_file)))
    check("the new name is on the list, the old one is not",
          ("Mine low" in back, "Low power" in back), (True, False))
    check("it still knows the low-power grid",
          D.match(low, back).name, "Mine low")
    check("and keeps its Low power tick", D.tag_of("Mine low", back),
          D.TAG_LOW)
    check("a built-in name still carried cannot be taken",
          D.rename_setup(back, "Mine", "high POWER")[0], None)
    check("the freed one can (21.10.2026), in its own spelling",
          "Low power" in D.rename_setup(back, "Mine", "low power")[0], True)
    check("deleting the renamed one does not bring it back",
          "Low power" in D.with_references(D.clean_setups(
              D.setups_document(D.for_file(D.delete_setups(
                  back, ["Mine low"]))))), False)
    view = D.DmView({"at": 1000.0, "host": "OPR1", "status": D.STATUS_OK,
                     "colours": [list(c) for c in high]}, refs, None, None,
                    None, 1000.0)
    check("the Overview's words", D.mirror_now(view, 1004.0)[:2],
          ("High power", D.TAG_HIGH))
    check("stale: no tag",
          D.mirror_now(view, 1100.0)[:2], ("No fresh reading", None))
    check("nothing yet", D.mirror_now(None, 1000.0)[:2],
          ("Not read yet", None))
    other = D.DmView({**view.state, "colours": changed}, refs, None, None,
                     None, 1000.0)
    check("an own set-up never ticked: ticked for neither",
          D.mirror_now(other, 1004.0)[:2], ("Mine", D.TAG_NONE))


def test_ticks():
    print("High power / Low power ticks, any number of each")
    low = read(screen("dm_0731.png")).colours
    high = read(screen("dm_0727.png")).colours
    high2 = [list(c) for c in high]
    high2[7] = [255, 0, 0]
    refs = D.with_references({})
    check("built-in ones start ticked as named",
          (D.tag_of("Low power", refs), D.tag_of("High power", refs)),
          (D.TAG_LOW, D.TAG_HIGH))
    setups, _ = D.add_setup(refs, "High power 2", high2, now_s=1000.0)
    check("a new one is ticked for neither",
          D.tag_of("High power 2", setups), D.TAG_NONE)
    setups, _ = D.set_tag(setups, "High power 2", D.TAG_HIGH)
    setups, _ = D.set_tag(setups, "High power", D.TAG_NONE)
    check("ticked", (D.tag_of("High power 2", setups),
                     D.tag_of("High power", setups)),
          (D.TAG_HIGH, D.TAG_NONE))
    on_file = D.for_file(setups)
    check("the file keeps own set-ups and only a built-in's changed tick",
          (sorted(on_file), on_file["High power"]),
          (["High power", "High power 2"], {"power": D.TAG_NONE}))
    back = D.with_references(D.clean_setups(D.setups_document(on_file)))
    check("both ticks survive the file",
          (D.tag_of("High power 2", back), D.tag_of("High power", back),
           D.tag_of("Low power", back)),
          (D.TAG_HIGH, D.TAG_NONE, D.TAG_LOW))
    check("the built-in colours stay built in",
          back["High power"]["colours"], [list(c) for c in high])
    renamed, _ = D.rename_setup(back, "High power 2", "HP alt")
    check("a rename keeps the tick", D.tag_of("HP alt", renamed), D.TAG_HIGH)
    check("an unknown name refused",
          D.set_tag(back, "Gone", D.TAG_LOW)[0], None)

    def now_on(colours, setups):
        view = D.DmView({"at": 1000.0, "host": "OPR1", "status": D.STATUS_OK,
                         "colours": [list(c) for c in colours]}, setups, None,
                        None, None, 1000.0)
        return D.mirror_now(view, 1004.0)[:2]

    check("High power 2 on the mirror", now_on(high2, back),
          ("High power 2", D.TAG_HIGH))
    check("High power unticked", now_on(high, back),
          ("High power", D.TAG_NONE))
    check("Low power still Low", now_on(low, back), ("Low power", D.TAG_LOW))


def _through_file(setups):
    """What every PC reads back after `setups` was written."""
    return D.with_references(D.clean_setups(D.setups_document(
        D.for_file(setups))))


def test_replace_delete_restore():
    print("replacing, undoing, deleting and restoring (21.10.2026)")
    low = read(screen("dm_0731.png")).colours
    high = read(screen("dm_0727.png")).colours
    new_low = [list(c) for c in low]
    new_low[7] = [255, 0, 0]
    refs = D.with_references({})

    print("  the crew saves a new Low power under the same name")
    check("a plain save under that name is refused, any case",
          D.add_setup(refs, " low  POWER", new_low)[0], None)
    replaced, why = D.replace_setup(refs, "low power", new_low, now_s=1000.0)
    check("replace works on the built-in one", why, None)
    back = _through_file(replaced)
    check("every PC reads the new grid",
          D.match(new_low, back).name, "Low power")
    check("the old grid is no saved set-up any more",
          D.match(low, back).name, None)
    check("the tick stays", D.tag_of("Low power", back), D.TAG_LOW)
    check("still first on the list", list(back), ["Low power", "High power"])
    check("who and when are kept",
          (bool(back["Low power"]["replaced"]),
           bool(back["Low power"]["replaced_by"])), (True, True))
    check("the same grid again is refused",
          D.replace_setup(back, "Low power", new_low)[0], None)
    check("High power's grid is refused",
          D.replace_setup(back, "Low power", high)[0], None)

    print("  undo")
    undone, why = D.undo_replace(back, "Low power")
    check("undo works", why, None)
    again = _through_file(undone)
    check("the old grid is back", D.match(low, again).name, "Low power")
    check("it is the built-in one again, only a tick in the file",
          D.for_file(again), {})
    check("nothing more to undo",
          D.undo_replace(again, "Low power")[0], None)

    print("  delete and restore")
    gone = _through_file(D.delete_setups(again, ["Low power", "High power"]))
    check("both gone", list(gone), [])
    check("both missing", D.missing_builtins(gone),
          ["Low power", "High power"])
    restored, note = D.restore_builtins(gone)
    check("restored", (list(restored), note),
          (["Low power", "High power"],
           "Built-in Low power and High power restored."))
    check("ticked as named again",
          (D.tag_of("Low power", restored), D.tag_of("High power", restored)),
          (D.TAG_LOW, D.TAG_HIGH))
    check("nothing to restore now", D.restore_builtins(restored)[0], None)
    saved, _ = D.add_setup(gone, "Low power", new_low)
    check("a deleted name saved anew is ticked as named",
          D.tag_of("Low power", _through_file(saved)), D.TAG_LOW)
    renamed, _ = D.rename_setup(refs, "High power", "HP old")
    check("a renamed built-in grid is not restored twice",
          D.restore_builtins(_through_file(renamed)),
          (None, "The built-in High power grid is saved, as \"HP old\"."))

    print("  two ticked for the same power")
    extra, _ = D.add_setup(refs, "Low power 2", new_low)
    check("one each: nothing to say", D.ticked_twice(extra), {})
    extra, _ = D.set_tag(extra, "Low power 2", D.TAG_LOW)
    check("two for Low power", D.ticked_twice(extra),
          {D.TAG_LOW: ["Low power", "Low power 2"]})

    print("  a file of the old version still loads")
    old = {"version": 1, "setups": {
        "Mine": {"colours": new_low, "saved": "x", "by": "PC"},
        "High power": {"power": D.TAG_NONE}}}
    loaded = D.with_references(D.clean_setups(old))
    check("as before", (list(loaded), D.tag_of("High power", loaded)),
          (["Low power", "High power", "Mine"], D.TAG_NONE))


def main():
    for test in (test_both_screenshots,
                 test_the_two_set_ups_are_told_apart,
                 test_a_scaled_window,
                 test_the_window_frame_and_covering_windows,
                 test_the_hint_is_used_and_then_dropped,
                 test_nothing_to_read,
                 test_through_remote_desktop,
                 test_the_scale_edge_column,
                 test_the_picture_is_saved,
                 test_one_electrode_changed,
                 test_speed,
                 test_the_set_up_grid_is_read_too,
                 test_what_the_tab_says,
                 test_saving_and_editing_set_ups,
                 test_built_in_references,
                 test_ticks,
                 test_replace_delete_restore):
        test()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for line in FAILED:
            print("  " + line)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
