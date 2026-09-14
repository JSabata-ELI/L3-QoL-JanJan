"""Render "Select PV channels" under main.py's own application stylesheet and look
at it.

Offscreen has no fonts and lies about text size, so run it for real:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_pv_config.py

The archiver is stubbed — no network. What to check on the picture:

  * every tick box is a box you can see: white ground, dark border, solid blue when
    ticked — both in the Presets block and in the Show column;
  * the formula is a ROW OF THE TABLE: "= I*0.749" in the PV column, its name in
    "Displayed name", its unit in "Unit", all three editable;
  * there is no "Own formulas" block under the tables any more;
  * nothing is white-on-white and no text spills out of its cell.

Writes pv_config.png and pv_config_bad_formula.png beside this file.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from bench_common import load_slider
from render_save_view_dialog import app_stylesheet

FAKE_CHANNELS = [
    "L3-PM03-023:Energy",
    "L3-SPFE-AOD03-002:Order2_RB",
    "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "L3-Compressor-Waveplate:Angle",
    "L3-Pulser-HighPowerEnable",
]


def main() -> int:
    m = load_slider()
    m.cpva.fetch_channels_cached = lambda *a, **k: list(FAKE_CHANNELS)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    custom = {"L3-SPFE-AOD03-002:Order2_RB": "L3-SPFE-AOD03-002:Order2_RB",
              "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy":
                  "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"}
    labels = {"L3-SPFE-AOD03-002:Order2_RB": "GDD",
              "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy": "SBW4",
              "Back_Ref": "Back_Ref"}
    units = {"L3-SPFE-AOD03-002:Order2_RB": "fs2",
             "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy": "J"}
    derived = [{"name": "compressed SBW4", "expr": "I*0.749", "unit": "J",
                "bindings": {"I": "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"}}]
    dlg = m.PvConfigDialog(["Back_Ref"] + list(custom), custom, derived, labels,
                           hidden=set(), units=units,
                           limits={"Back_Ref": (0.2, 1.4)})
    dlg.show()
    out = Path(__file__).parent
    shots: list = []

    def shoot():
        app.processEvents()
        dlg.grab().save(str(out / "pv_config.png"))
        shots.append("pv_config.png")
        # A formula that cannot be computed has to be visible as such.
        rows = [r for r in dlg._rows if r["ent"]["kind"] == "formula"]
        if rows:
            rows[0]["expr_edit"].setText("I*")
            dlg._sync_row_state()
            app.processEvents()
            dlg.grab().save(str(out / "pv_config_bad_formula.png"))
            shots.append("pv_config_bad_formula.png")
        # And with the search box in use: the result list is only on screen then.
        rows[0]["expr_edit"].setText("I*0.749")
        dlg._sync_row_state()
        dlg._search.setText("energy")
        dlg._on_search("energy")
        app.processEvents()
        dlg.grab().save(str(out / "pv_config_search.png"))
        shots.append("pv_config_search.png")
        # A brand-new formula row: it has to be a row you can obviously type in.
        dlg._search.clear()
        dlg._on_search("")
        dlg._add_derived_row()
        app.processEvents()
        dlg.grab().save(str(out / "pv_config_new_formula.png"))
        shots.append("pv_config_new_formula.png")
        print("on the picture :", dlg._title_on.text())
        print("read only      :", dlg._title_off.text())
        print("formulas       :", dlg.derived_defs())
        print("written        :", ", ".join(shots))
        app.quit()

    QTimer.singleShot(900, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
