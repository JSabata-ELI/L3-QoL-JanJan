"""A formula is a ROW OF THE PICKED-PV TABLE now — there is no separate formula
block under the dialog any more.

What this pins down:

  * "+ Add formula" produces a row you can actually type in. The row used to be
    dropped from the tables until it had a name, which with the old block below was
    harmless and here would mean the button did nothing visible.
  * The three boxes on that row ARE the formula: the expression in the PV column, the
    name in "Displayed name", the unit in "Unit". What comes back out of
    derived_defs() / hidden_names() is what was typed into them.
  * Renaming a formula carries its alarm limits with it — they are keyed by name, and
    left behind they would be a threshold the operator set and then silently lost.
  * The record behind a row is plain text, not widgets. Every rebuild throws the row
    away, so a record made of its boxes would be a formula that dies with its table.
  * The recipe presets ("Compressed SBW4") still add their source PV, their formula,
    and take the source off the picture.

    QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 python test_pv_dialog_formula_in_table.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bench_common import load_slider

FAKE_CHANNELS = [
    "L3-PM03-023:Energy",
    "L3-SPFE-AOD03-002:Order2_RB",
    "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "L3-Compressor-Waveplate:Angle",
]

fails: list = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


def row_of(dlg, name: str):
    for r in dlg._rows:
        if r["ent"]["name"] == name:
            return r
    return None


def formula_rows(dlg):
    return [r for r in dlg._rows if r["ent"]["kind"] == "formula"]


def main() -> int:
    m = load_slider()
    m.cpva.fetch_channels_cached = lambda *a, **k: list(FAKE_CHANNELS)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    print("a new formula is a row you can type in")
    dlg = m.PvConfigDialog(["Back_Ref"], {}, [], {}, hidden=set(), units={}, limits={})
    dlg._add_derived_row()
    rows = formula_rows(dlg)
    check(len(rows) == 1, "+ Add formula puts exactly one formula row in the table")
    r = rows[0]
    check(r["expr_edit"] is not None, "the PV column of that row is a box, not a label")
    check(r["name_edit"] is not None, "so is Displayed name")
    check(r["unit_edit"] is not None, "and Unit is open for a formula")

    print("what is typed into the row is what comes back out")
    r["name_edit"].setText("compressed SBW4")
    r["expr_edit"].setText("F*0.749")
    r["unit_edit"].setText("J")
    defs = dlg.derived_defs()
    check(len(defs) == 1, "one formula stored")
    check(defs[0]["name"] == "compressed SBW4", f"name kept ({defs[0]['name']!r})")
    check(defs[0]["expr"] == "F*0.749", f"expression kept ({defs[0]['expr']!r})")
    check(defs[0]["unit"] == "J", f"unit kept ({defs[0]['unit']!r})")
    check("compressed SBW4" in dlg.selected_names(), "the formula is in the list")
    # The letter F is Back_Ref in the preset order, so the binding must name it.
    check(defs[0]["bindings"].get("F") == "Back_Ref",
          f"the letter is bound to the PV it meant ({defs[0]['bindings']})")

    print("the row survives a rebuild of the tables")
    dlg._rebuild_picked_tables()
    r2 = formula_rows(dlg)[0]
    check(r2["expr_edit"].text() == "F*0.749", "the expression is still in the box")
    check(r2["name_edit"].text() == "compressed SBW4", "and so is the name")
    check(r2["unit_edit"].text() == "J", "and the unit")

    print("a limit follows a rename")
    r2["min_edit"].setText("5")
    dlg._sync_row_state()
    check(dlg.limits().get("compressed SBW4") == (5.0, None),
          f"limit set on the formula ({dlg.limits()})")
    r2["name_edit"].setText("SBW4 compressed")
    dlg._sync_row_state()
    check(dlg.limits().get("SBW4 compressed") == (5.0, None),
          f"limit moved with the name ({dlg.limits()})")
    check("compressed SBW4" not in dlg.limits(), "and nothing was left behind")

    print("Show is still the eye, and it moves the row between the two tables")
    r3 = formula_rows(dlg)[0]
    r3["chk"].setChecked(False)
    dlg._rebuild_picked_tables()
    check(dlg.hidden_names() == ["SBW4 compressed"],
          f"the formula is read but not on the picture ({dlg.hidden_names()})")
    check(len(dlg.derived_defs()) == 1, "unticking it did not delete it")

    print("an empty formula row is ignored, not an error")
    dlg2 = m.PvConfigDialog(["Back_Ref"], {}, [], {}, hidden=set(), units={}, limits={})
    dlg2._add_derived_row()
    dlg2._sync_row_state()
    check(dlg2.derived_defs() == [], "an untouched new row stores nothing")
    check("" not in dlg2.selected_names(), "and does not appear as a nameless PV")

    print("a formula with an expression but no name is refused, not dropped")
    r = formula_rows(dlg2)[0]
    r["expr_edit"].setText("F*2")
    dlg2._sync_row_state()
    # isHidden, not isVisible: nothing is "visible" while the dialog has never been
    # shown, and what is being tested is the flag the row sets on itself.
    check(not r["warn"].isHidden(), "the row is marked ⚠")

    print("✕ removes the formula")
    dlg3 = m.PvConfigDialog(["Back_Ref"], {}, [
        {"name": "ratio", "expr": "F/F", "unit": "", "bindings": {"F": "Back_Ref"}}],
        {}, hidden=set(), units={}, limits={})
    check(len(formula_rows(dlg3)) == 1, "a saved formula comes back as a row")
    dlg3._remove_derived_row(dlg3._derived_rows[0])
    check(dlg3.derived_defs() == [], "and ✕ takes it out for good")

    print("a recipe preset still builds its own formula row")
    dlg4 = m.PvConfigDialog([], {}, [], {}, hidden=set(), units={}, limits={})
    recipe = next(iter(m.PV_PRESET_RECIPES))
    src = str(m.PV_PRESET_RECIPES[recipe].get("source") or "")
    dlg4._on_preset_toggled(recipe, True)
    defs4 = dlg4.derived_defs()
    check(len(defs4) == 1 and defs4[0]["name"] == recipe,
          f"{recipe} came in as a formula ({defs4})")
    check(src in dlg4.selected_names(), f"its source {src} is read")
    if m.PV_PRESET_RECIPES[recipe].get("hide_source"):
        check(src in dlg4.hidden_names(), f"and {src} is off the picture")
    dlg4._on_preset_toggled(recipe, False)
    check(dlg4.derived_defs() == [], "unticking it removes the formula")
    check(src not in dlg4.hidden_names(), f"and gives {src} its eye back")

    print()
    if fails:
        print(f"{len(fails)} FAILED:")
        for f in fails:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
