"""A marked region never disappears on its own.

Reported: five regions marked on one day, a plain click on another day in the
calendar, all five gone. Two separate causes, both fixed:

* `_on_cal_clicked` discarded the regions of every day the click unmarked — and a
  plain click unmarks every other day. A day that carries a pick is now never
  unmarked.
* clicking a moment deleted every region on every day. Neither kind of pick
  deletes the other any more; the moment line says which is being searched.

Offscreen, no share and no archiver.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 1)
NEXT = date(2026, 9, 2)
CAM = ("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(day, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=tz).timestamp() * 1e9)


def add_region(dlg, day, h0, h1):
    dlg._regions.append({"id": dlg._region_seq, "t_start_ns": ns_at(day, h0),
                         "t_end_ns": ns_at(day, h1), "color": "#C62828",
                         "day": day})
    dlg._region_seq += 1


def main() -> int:
    m = load_finder()
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    series = [(ns_at(DAY, 8) + i * 60_000_000_000, 10.0 + (i % 7))
              for i in range(600)]
    series += [(ns_at(NEXT, 8) + i * 60_000_000_000, 11.0 + (i % 5))
               for i in range(600)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))

    dlg = m.PVRegionSearchDialog([CAM], [QDate(DAY.year, DAY.month, DAY.day)])
    B.wait_for(lambda: bool(dlg._series), timeout_s=10.0)

    print("=== a plain click on another day keeps what is picked ===")
    for h in (8, 9, 10, 11, 12):
        add_region(dlg, DAY, h, h + 1)
    dlg._rebuild_regions_ui()
    dlg._refresh_day_list()
    check("five regions marked", len(dlg._regions) == 5)

    # A PLAIN click (no modifier) on the next day — the gesture that lost them.
    dlg._on_cal_clicked(QDate(NEXT.year, NEXT.month, NEXT.day))
    check("all five regions are still there", len(dlg._regions) == 5,
          f"{len(dlg._regions)} left")
    check("the day carrying them stays marked", DAY in dlg._days,
          str(dlg._days))
    check("and the day just clicked is marked too", NEXT in dlg._days,
          str(dlg._days))
    check("the graph moves to the day clicked", dlg._focus_day() == NEXT,
          str(dlg._focus_day()))

    print("\n=== picks on a second day ADD to the set ===")
    B.wait_for(lambda: NEXT in dlg._series, timeout_s=10.0)
    add_region(dlg, NEXT, 9, 10)
    dlg._rebuild_regions_ui()
    dlg._refresh_day_list()
    cfg = dlg.get_config()
    check("both days are searched", sorted(cfg["days"]) == [DAY, NEXT],
          str(cfg["days"]))
    check("with five regions on the first and one on the second",
          len(cfg["regions"][DAY]) == 5 and len(cfg["regions"][NEXT]) == 1)
    check("the day list counts them per day",
          "5 regions" in dlg._day_list.item(0).text()
          and "1 region" in dlg._day_list.item(1).text(),
          repr([dlg._day_list.item(i).text() for i in range(2)]))
    check("and its tooltip says WHICH",
          dlg._day_list.item(0).toolTip().count("\n") == 4,
          repr(dlg._day_list.item(0).toolTip()))

    print("\n=== a picked moment deletes no region ===")
    dlg._set_moment_from_x(dlg._ns_to_x(ns_at(NEXT, 9, 30), NEXT))
    check("the moment is picked", len(dlg._moments) == 1, str(dlg._moments))
    check("and every region is still there", len(dlg._regions) == 6,
          f"{len(dlg._regions)}")
    check("the moment line says the regions are ignored",
          "ignored" in dlg._lbl_moment.text(), dlg._lbl_moment.text())
    check("the search runs the moments, not the regions",
          dlg.get_config().get("moments_ns") == dlg._moments)
    dlg._clear_moment()
    check("Clear brings the regions back into the search",
          dlg.get_config().get("moment_ns") is None
          and len(dlg.get_config()["regions"]) == 2)

    print("\n=== a day lets go only when its last pick does ===")
    for r in list(dlg._regions):
        if r["day"] == NEXT:
            dlg._delete_region(r["id"])
    check("the day stays marked as an ordinary day", NEXT in dlg._days,
          str(dlg._days))
    dlg._on_cal_clicked(QDate(DAY.year, DAY.month, DAY.day))
    check("and now a plain click can drop it", NEXT not in dlg._days,
          str(dlg._days))
    check("while the day that still carries regions stays", DAY in dlg._days)

    print("\n=== Clear all regions ===")
    dlg._clear_regions()
    check("the regions are gone", dlg._regions == [])
    check("the day is still marked", DAY in dlg._days, str(dlg._days))
    dlg.deleteLater()

    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
