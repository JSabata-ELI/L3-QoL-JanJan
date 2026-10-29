"""SBW4 is ONE row in "Select PV channels", whichever of its two archiver names is
picked. Reading already asks both (HAPLS first, L3 when HAPLS holds nothing), so a
second row for the other name only read the same numbers twice.

What this pins down:

  * A saved registry holding the HAPLS name as an added PV is folded into the SBW4
    preset: the added PV is gone, its formula is re-bound to SBW4, its name moves over.
  * A saved selection with both names comes back as one SBW4, shown if either was.
  * Picking the HAPLS name in the search adds the SBW4 preset, not a second row, and
    the search does not offer the HAPLS name as a channel of its own.

    QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 python test_pv_sbw4_one_row.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bench_common import load_slider

HAPLS = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"
FAKE_CHANNELS = ["L3-SBW4-PM311:Energy", HAPLS, "L3-SPFE-AOD03-002:Order2_RB"]

fails: list = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


def main() -> int:
    m = load_slider()
    m.cpva.fetch_channels_cached = lambda *a, **k: list(FAKE_CHANNELS)
    m.pv_registry_save = lambda: None        # never touch the real registry file
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    l3 = m.PV_CHANNEL_MAP["SBW4"]
    check(m.pv_preset_for_channel(HAPLS) == "SBW4", "the HAPLS name is the SBW4 preset")
    check(m.pv_preset_for_channel(l3) == "SBW4", "so is the L3 name")
    check(m.pv_preset_for_channel("L3-SPFE-AOD03-002:Order2_RB") is None,
          "an unrelated channel is no preset")

    print("the saved registry from the screenshot")
    folded = m.pv_registry_from_dict({
        "pv_custom": {"L3-SPFE-AOD03-002:Order2_RB": "L3-SPFE-AOD03-002:Order2_RB",
                      HAPLS: HAPLS},
        "pv_derived": [{"name": "compressed SBW4", "expr": "I*0.749", "unit": "",
                        "bindings": {"I": HAPLS}}],
        "pv_labels": {HAPLS: "SBW4 hapls", "L3-SPFE-AOD03-002:Order2_RB": "GDD"},
        "pv_limits": {HAPLS: {"min": 0.5, "max": None}},
    })
    check(folded == {HAPLS: "SBW4"}, f"one name folded: {folded}")
    check(HAPLS not in m.PV_CUSTOM_CHANNELS, "the HAPLS name is no added PV any more")
    check("L3-SPFE-AOD03-002:Order2_RB" in m.PV_CUSTOM_CHANNELS, "GDD stays")
    d = m.pv_derived_def("compressed SBW4")
    check(d is not None and d["bindings"] == {"I": "SBW4"},
          f"the formula now reads SBW4: {d and d['bindings']}")
    check(m.PV_LABELS.get("SBW4") == "SBW4 hapls", "its name moved to the preset")
    check(m.PV_LIMITS.get("SBW4") == (0.5, None), "so did its alarm limit")

    print("a saved selection with both names")
    hidden = {"SBW4", HAPLS}
    sel = m.pv_fold_selection(["SBW4", "L3-SPFE-AOD03-002:Order2_RB", HAPLS], hidden)
    check(sel == ["SBW4", "L3-SPFE-AOD03-002:Order2_RB"], f"one SBW4: {sel}")
    check("SBW4" in hidden and HAPLS not in hidden, "both hidden -> SBW4 hidden")
    hidden = {"SBW4"}
    m.pv_fold_selection(["SBW4", HAPLS], hidden)
    check("SBW4" not in hidden, "either shown -> SBW4 shown")

    print("the dialog")
    dlg = m.PvConfigDialog([HAPLS], {HAPLS: HAPLS}, [], {}, hidden=set(),
                           units={}, limits={})
    check(dlg._preset_in.get("SBW4") and not dlg._custom,
          "handed the HAPLS name, it shows the SBW4 preset and no added PV")
    dlg._on_channels_loaded(list(FAKE_CHANNELS))
    keys = [k for _d, k in dlg._suggestions()]
    check(HAPLS not in keys, "the search does not offer the HAPLS name on its own")
    disp = next(d for d, k in dlg._suggestions() if k == l3)
    check(HAPLS in disp, f"the SBW4 suggestion names both: {disp}")

    dlg2 = m.PvConfigDialog([], {}, [], {}, hidden=set(), units={}, limits={})
    dlg2._add_channel(HAPLS)
    check(dlg2._preset_in.get("SBW4") and not dlg2._custom,
          "picking the HAPLS name adds the SBW4 preset, not a second row")
    dlg2._add_channel(l3)
    check(sum(1 for e in dlg2._picked_entries() if e["kind"] != "shot") == 1,
          "picking the L3 name after it still leaves one row")
    check(dlg2.selected_names() == ["SBW4"], f"{dlg2.selected_names()}")

    dlg.deleteLater()
    dlg2.deleteLater()
    app.processEvents()
    print("\nFAILED: " + "; ".join(fails) if fails else "\nall ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
