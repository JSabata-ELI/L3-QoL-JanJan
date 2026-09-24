"""The PV VALUES list must show every picked PV, whatever the other sections do.

Reported 16.09.2026: with four PVs picked, collapsing TIMELINE & RANGE left the PV
table showing two rows with no way to read the rest. Collapsing a section changes
nothing about how many PVs are picked, so the list may not lose rows over it.

The table is a QTableWidget, whose size policy is Expanding/Expanding with a tiny
minimumSizeHint — so inside the 275 px scroll panel it is the one widget the layout
can steal height from. `refresh()` sets only a MAXIMUM height, which caps growth and
does nothing to stop the squeeze.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_pv_table_height.py

Real windows platform on purpose: offscreen has no fonts and lies about row and
header heights, which are exactly what is being measured.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_pvh_"))
os.environ["APPDATA"] = str(_TMP)

import is_t                                                          # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

FAILURES: "list[str]" = []

PVS = ["PTM1", "PCM2", "PCM4", "PAP1"]


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def app_stylesheet() -> str:
    from render_display_rows import app_stylesheet as css
    return css()


def visible_rows(tbl) -> int:
    """How many rows fit in the viewport as it stands — what the operator can read."""
    h = tbl.viewport().height()
    return max(0, h // max(1, tbl.rowHeight(0) or tbl.ROW_H))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    v = is_t.Viewer()
    v.resize(1500, 950)
    # Off the visible desktop: the real windows platform is needed for honest row and
    # header heights, but nothing may flash on the screen while a test runs.
    v.move(-4000, -4000)
    v.show()
    app.processEvents()

    v._pv_enabled = list(PVS)
    v._pv_hidden = set()
    v._pv_values = {n: "12.345" for n in PVS}
    v._pv_rebuild_table()
    app.processEvents()

    tbl = v._pv_table
    print(f"window {v.width()}x{v.height()},  {len(PVS)} PVs picked")
    for key in ("source", "save", "timeline", "display", "pv", "overlays", "analysis"):
        v._sections[key].set_expanded(key in ("source", "save", "timeline", "pv"))
    app.processEvents()
    open_h = tbl.height()
    open_rows = visible_rows(tbl)
    print(f"  timeline OPEN      table {open_h:3d} px, {open_rows} of {len(PVS)} rows readable")

    v._sections["timeline"].set_expanded(False)
    app.processEvents()
    app.processEvents()
    shut_h = tbl.height()
    shut_rows = visible_rows(tbl)
    print(f"  timeline COLLAPSED table {shut_h:3d} px, {shut_rows} of {len(PVS)} rows readable")

    check("every picked PV is readable with the timeline open",
          open_rows >= len(PVS), f"{open_rows}/{len(PVS)}")
    check("collapsing TIMELINE & RANGE does not shrink the PV list",
          shut_rows >= len(PVS),
          f"{shut_rows}/{len(PVS)} rows, {shut_h} px (was {open_h} px)")

    # And the other way round: every other section shut must not squeeze it either.
    for key in ("source", "save", "display", "overlays", "analysis"):
        v._sections[key].set_expanded(False)
    app.processEvents()
    all_shut_rows = visible_rows(tbl)
    print(f"  everything else shut: {all_shut_rows} of {len(PVS)} rows readable "
          f"({tbl.height()} px)")
    check("PV list survives every other section being shut",
          all_shut_rows >= len(PVS), f"{all_shut_rows}/{len(PVS)}")

    # Many PVs, in a short window, with everything else shut — the worst case for the
    # squeeze. The list grows to hold them all and the PANEL scrolls; the table itself
    # never hides a row behind its own scrollbar.
    many = is_t.pv_all_names()[:7]
    v.resize(1500, 700)
    v._pv_enabled = list(many)
    v._pv_values = {n: "12.345" for n in many}
    v._pv_rebuild_table()
    for _ in range(3):
        app.processEvents()
    rows_seen = visible_rows(tbl)
    own_scroll = tbl.verticalScrollBar().maximum() > 0
    print(f"  {len(many)} PVs in a 700 px window: {rows_seen} readable "
          f"({tbl.height()} px), own scrollbar={own_scroll}")
    check("every PV is readable with more PVs and a short window",
          rows_seen >= len(many), f"{rows_seen}/{len(many)}")
    check("the list does not hide rows behind its own scrollbar", not own_scroll)

    out = Path(__file__).resolve().parent / "pv_table_collapsed.png"
    v._sections["pv"].parentWidget().grab().save(str(out))
    print(f"  wrote {out.name}")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
