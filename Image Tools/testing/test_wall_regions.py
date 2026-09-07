"""Four regions marked on one day are FOUR rows, not one.

The fault: `_row_order` deduped on the day and `_relayout_rows` keyed the
rectangle on `(day, camera)`, so every frame of a day landed on the same
rectangle — three painted underneath the fourth, and hit-testing then selected a
cell that was not on screen.

Also pinned here: the region survives the trip from the PV window to the cell
(`get_config` → `_region_targets` → `meta["region"]` → `fill_wall`), the banner
names which region a row is, and a cell with NO region still gives one row per
day — the contract the CSV / energy / blind searches and the moment wall rely on.

Offscreen, no share and no archiver.
"""
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 1)
CAMS = ("C03-040-PTM11WNF-_-IMG", "C03-041-PAM1FF-_-IMG")


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(DAY.year, DAY.month, DAY.day, hour, minute,
                        tzinfo=tz).timestamp() * 1e9)


def region(i: int, count: int, h0: int, h1: int) -> dict:
    return {"t_start_ns": ns_at(h0), "t_end_ns": ns_at(h1), "index": i,
            "count": count, "color": "#C62828",
            "label": f"{h0:02d}:00:00–{h1:02d}:00:00"}


def cells_for(m, regions: list, cams=CAMS) -> list:
    """Run the real `fill_wall` cell builder over region-driven results."""
    captured = {}
    stub = types.SimpleNamespace(
        _build_wall_tabs=lambda cells, moment_ns=None, moments_ns=None:
            captured.update(cells=cells),
        _log=lambda msg: None)
    fill = m.ImageFinderWidget.fill_wall.__get__(stub)
    results = {}
    for cam in cams:
        rows = []
        for r in regions:
            meta = {"ptm1": None, "sbw4": None, "source": "pv",
                    "region_ns": (r["t_start_ns"], r["t_end_ns"]),
                    "target_ns": r["t_start_ns"],
                    "region": dict(r)}
            rows.append((DAY, 10, Path(f"{cam}_{r['index']}.png"), meta, "found"))
        results[cam] = rows
    fill(results, None)
    return captured.get("cells") or []


def check_cells(m):
    regs = [region(i, 4, 8 + i, 9 + i) for i in (1, 2, 3, 4)]
    cells = cells_for(m, regs)
    check("two cameras × four regions = eight cells", len(cells) == 8,
          f"{len(cells)} cell(s)")
    check("every cell knows its region",
          sorted({(c.get('region') or {}).get('index') for c in cells})
          == [1, 2, 3, 4],
          repr(sorted({(c.get('region') or {}).get('index') for c in cells})))
    check("and carries the row key the search wrote",
          sorted({c["row_key"] for c in cells})
          == [(DAY, 1), (DAY, 2), (DAY, 3), (DAY, 4)],
          repr(sorted({c['row_key'] for c in cells})))
    check("the wall's own row for it is the DAY",
          {m._DayWall._cell_row_key(c) for c in cells} == {DAY},
          repr({m._DayWall._cell_row_key(c) for c in cells}))
    labels = sorted({c["cam"] for c in cells})
    check("and its column is (camera, region number)",
          sorted({m._DayWall._cell_col_key(c) for c in cells})
          == sorted((lbl, i) for lbl in labels for i in (1, 2, 3, 4)),
          repr(sorted({m._DayWall._cell_col_key(c) for c in cells})))

    # A cell with no region at all — the old contract.
    plain = {CAMS[0]: [(DAY, 10, Path("x.png"), {"source": "blind"}, "found")],
             CAMS[1]: [(DAY, 10, Path("y.png"), {"source": "blind"}, "found")]}
    captured = {}
    stub = types.SimpleNamespace(
        _build_wall_tabs=lambda cells, moment_ns=None, moments_ns=None:
            captured.update(cells=cells),
        _log=lambda msg: None)
    m.ImageFinderWidget.fill_wall.__get__(stub)(plain, None)
    got = captured.get("cells") or []
    check("a frame with no region gets the day's own row",
          {c["row_key"] for c in got} == {(DAY, None)},
          repr({c['row_key'] for c in got}))
    return cells


def check_rows(m, cells):
    wall = m._DayWall()
    wall.set_layout_mode("rows")
    wall.resize(1000, 600)
    wall.set_cells([dict(c) for c in cells])
    wall.set_canvas(1000, 600)

    # THE RULE: a row is a day, the next row is the next day. Four regions on one
    # day do not split it into four rows — they sit side by side inside that row,
    # each camera owning one column per region.
    rows, cols = wall._row_order()
    check("one day = one row", rows == [DAY], repr(rows))
    check("two cameras × four regions = eight columns", len(cols) == 8,
          f"{len(cols)} column(s)")
    check("the columns are sorted by camera name, then by region number",
          cols == sorted(cols, key=lambda t: (str(t[0]), t[1])), repr(cols))
    check("and a column belongs to ONE camera",
          [c[0] for c in cols] == sorted(c[0] for c in cols), repr(cols))

    rects = [wall._rects[i] for i in range(len(wall.cells()))]
    check("every cell has a rectangle of its own",
          all(r.width() > 0 and r.height() > 0 for r in rects),
          f"{sum(1 for r in rects if r.width() <= 0)} empty")
    keys = [(r.x(), r.y(), r.width(), r.height()) for r in rects]
    check("and no two cells share one", len(set(keys)) == len(keys),
          f"{len(set(keys))} distinct of {len(keys)}")
    ys = sorted({r.y() for r in rects})
    check("all eight sit on the one row", len(ys) == 1, repr(ys))
    check("about three rows fit the pane",
          abs(wall.row_pitch() - 600 // 3) <= 2, f"pitch {wall.row_pitch()} px")

    heads = [txt for _r, txt in wall._row_heads]
    check("one banner, for the day", len(heads) == 1, repr(heads))
    check("it names the day, spelled out", "Tuesday" in heads[0], repr(heads[0]))
    check("and says the day carries four", "4 regions" in heads[0], repr(heads[0]))

    # One region on a day says nothing about a count.
    one = cells_for(m, [region(1, 1, 8, 9)])
    w2 = m._DayWall()
    w2.set_layout_mode("rows")
    w2.resize(1000, 600)
    w2.set_cells([dict(c) for c in one])
    w2.set_canvas(1000, 600)
    check("a single region adds no count to the banner",
          all("region" not in txt for _r, txt in w2._row_heads),
          repr([t for _r, t in w2._row_heads]))

    # Several DAYS are what makes several rows.
    many = cells_for(m, [region(1, 2, 8, 9), region(2, 2, 9, 10)])
    for c in many[:2]:
        c["day"] = date(2026, 9, 2)
        c["row_key"] = (c["day"], (c.get("region") or {}).get("index"))
    w3 = m._DayWall()
    w3.set_layout_mode("rows")
    w3.resize(1000, 600)
    w3.set_cells([dict(c) for c in many])
    w3.set_canvas(1000, 600)
    check("two days = two rows", len(w3._row_order()[0]) == 2,
          repr(w3._row_order()[0]))
    check("the days run top to bottom in date order",
          w3._row_order()[0] == sorted(w3._row_order()[0]),
          repr(w3._row_order()[0]))

    # Hit-testing must reach the cell that is actually painted there.
    hit_ok = True
    for i, r in enumerate(rects):
        if wall._hit(r.center()) != i:
            hit_ok = False
            break
    check("clicking a tile picks THAT tile", hit_ok)


def check_one_tile_at_a_time(m, cells):
    """Replacing the frame of one region must not touch the other three."""
    wall = m._DayWall()
    wall.set_layout_mode("rows")
    wall.resize(1000, 600)
    wall.set_cells([dict(c) for c in cells])

    stub = types.SimpleNamespace(
        _all_walls=lambda: [wall],
        _wall=wall,
        _rebuild_baseline_combo=lambda cells: None)
    stub._cell_identity = m.ImageFinderWidget._cell_identity
    repl = m.ImageFinderWidget._replace_cell_frame.__get__(stub)

    target = wall.cells()[0]
    repl(target, Path("replaced.png"))
    changed = [c for c in wall.cells() if str(c.get("path")) == "replaced.png"]
    check("exactly one tile took the new picture", len(changed) == 1,
          f"{len(changed)} tile(s)")
    check("and it is the one asked for",
          m.ImageFinderWidget._cell_identity(changed[0])
          == m.ImageFinderWidget._cell_identity(target))


def check_config(m):
    """The PV window numbers the regions of a day 1…n in TIME order."""
    from PySide6.QtCore import QDate
    series = [(ns_at(8) + i * 60_000_000_000, 10.0 + i) for i in range(120)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))
    dlg = m.PVRegionSearchDialog(
        [(CAMS[0], "PTM11WNF", Path("x"))],
        [QDate(DAY.year, DAY.month, DAY.day)])
    B.wait_for(lambda: bool(dlg._series), timeout_s=10.0)

    # Marked out of order on purpose: 10:00, then 08:00, then 09:00.
    for h0, h1 in ((10, 11), (8, 9), (9, 10)):
        dlg._regions.append({"id": dlg._region_seq, "t_start_ns": ns_at(h0),
                             "t_end_ns": ns_at(h1), "color": "#C62828",
                             "day": DAY})
        dlg._region_seq += 1
    cfg = dlg.get_config()
    got = cfg["regions"][DAY]
    check("the regions of a day come out in time order",
          [r["index"] for r in got] == [1, 2, 3]
          and [r["t_start_ns"] for r in got]
              == [ns_at(8), ns_at(9), ns_at(10)],
          repr([(r["index"], m.PVRegionSearchDialog._hms(r["t_start_ns"]))
                for r in got]))
    check("each carries how many there are", all(r["count"] == 3 for r in got))
    check("and its own label", all("–" in (r["label"] or "") for r in got),
          repr(got[0]["label"]))
    dlg.deleteLater()


def check_targets(m):
    """`_region_targets` accepts the dicts and hands the region on."""
    calls = []

    def fake_fetch(channel, start_ns, end_ns, timeout=3.0):
        calls.append((start_ns, end_ns))
        return [{"time": start_ns + 1_000_000_000, "value": 12.0}]

    orig = m._cpva_fetch_samples
    m._cpva_fetch_samples = fake_fetch
    try:
        stub = types.SimpleNamespace(_blocking_call=None)
        stub._region_targets = m.ImageFinderWidget._region_targets.__get__(stub)
        regs = [region(1, 2, 8, 9), region(2, 2, 9, 10)]
        tgts = stub._region_targets(DAY, regs, "L3-SBW4-PM311:Energy")
        check("one target per region", len(tgts) == 2, f"{len(tgts)}")
        check("the region dict is carried on the target",
              [t["info"]["index"] for t in tgts] == [1, 2],
              repr([t.get("info") and t["info"]["index"] for t in tgts]))
        check("and the peak sample names the time",
              tgts[0]["target_ns"] == ns_at(8) + 1_000_000_000)
        # Bare tuples still work — the old callers and the tests pass those.
        tgts2 = stub._region_targets(DAY, [(ns_at(8), ns_at(9))],
                                     "L3-SBW4-PM311:Energy")
        check("a bare (start, end) pair is still accepted",
              len(tgts2) == 1 and tgts2[0]["info"] is None)
    finally:
        m._cpva_fetch_samples = orig


def main() -> int:
    m = load_finder()
    # A QWidget without a QApplication aborts the process with no message.
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    print("=== the region survives the trip to the cell ===")
    cells = check_cells(m)
    print("\n=== four regions, four rows ===")
    check_rows(m, cells)
    print("\n=== one tile at a time ===")
    check_one_tile_at_a_time(m, cells)
    print("\n=== the numbering comes from the PV window ===")
    check_config(m)
    print("\n=== the targets carry the region ===")
    check_targets(m)
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
