"""The 12.10.2026 changes: the calendar's quick times, the RAMP / WAVEPLATE
group ("Keep steps every" defaults to 1000, applies at once, old empty
"master_multiple" values do not override it, presets carry the dead-shot rule)
and the PV Time Plot's conditions in their own window.

Headless, nothing is written to disk:

    python testing/test_ramp_and_quick_times.py
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton  # noqa: E402

import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def main() -> int:
    print("test_ramp_and_quick_times")
    for fn in ("save_config", "save_condition_presets", "save_custom_pvs", "save_presets"):
        setattr(css, fn, lambda *a, **k: None)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)

    # 1. Quick times under the calendars.
    now = datetime.now()
    d = css.TimeWindowDialog(None, now - timedelta(hours=1), now)
    for which, extra in (("from", []), ("to", ["Live"])):
        texts = [b.text() for b in d._sides[which]["abs_btns"]]
        check(f"{which}: quick times", texts == ["Now", "00:00", "07:00", "13:00", "17:00"] + extra,
              str(texts))
    d._sides["from"]["tabs"].setCurrentIndex(0)
    btn = next(b for b in d._sides["from"]["abs_btns"] if b.text() == "13:00")
    btn.click()
    t = d._sides["from"]["time"].time()
    check("13:00 sets the clock", (t.hour(), t.minute(), t.second()) == (13, 0, 0))
    d.close()

    # 2. Saved step values: new key always wins, an empty old key is no choice.
    check("master_step wins", css._saved_step({"master_step": "", "master_multiple": "5"}) == "")
    check("old non-empty value is kept", css._saved_step({"master_multiple": "5"}) == "5")
    check("old empty value is no choice", css._saved_step({"master_multiple": ""}) is None)
    check("nothing saved", css._saved_step({}) is None)

    w = css.CSSLoggerWidget()
    w._request_reload = lambda *a, **k: None
    check("Keep steps every starts at 1000", w._master_multiple_edit.text() == "1000",
          repr(w._master_multiple_edit.text()))

    # 3. It applies at once: a fake set of rows, the master on and off steps.
    pv = w._get_master_pv()
    rows = [(i, {pv: (v, 0), "E": (1.0, 0)}) for i, v in enumerate([1000, 1500, 2000, 2003, 2600])]
    w._table_rows_unfiltered = rows
    w._master_multiple_edit.setText("1000")
    w._last_step_txt = None
    w._on_step_edited()
    check("1000 keeps the rows on whole steps", len(w._table_rows) == 3, str(len(w._table_rows)))
    w._master_multiple_edit.setText("")
    w._on_step_edited()
    check("empty keeps every row", len(w._table_rows) == 5, str(len(w._table_rows)))

    # 4. Presets and saved tabs carry the whole group, dead-shot rule included.
    w._master_multiple_edit.setText("500")
    w._chk_dead_shot.setChecked(False)
    w._dead_energy_spin.setValue(0.2)
    p = w._current_preset_payload("X")
    check("preset stores the step", p.get("master_step") == "500", str(p))
    check("preset stores the dead-shot rule",
          p.get("dead_shot_skip") is False and abs(p.get("dead_shot_energy_max") - 0.2) < 1e-9)
    w2 = css.CSSLoggerWidget()
    w2._request_reload = lambda *a, **k: None
    w2._apply_ramp_settings(p)
    check("loading it restores the step", w2._master_multiple_edit.text() == "500")
    check("and the dead-shot tick", not w2._chk_dead_shot.isChecked())
    st = w.export_tab_state()
    check("a saved tab stores the step", st.get("master_step") == "500", str(st.get("master_step")))
    w2._apply_ramp_settings({"master_multiple": ""})
    check("an old empty preset leaves the step alone", w2._master_multiple_edit.text() == "500")

    # 5. PV Time Plot: the conditions sit behind a button, in their own window.
    check("PV Time has a Conditions button",
          isinstance(getattr(w, "_btn_pv_time_cond", None), QPushButton))
    check("the rows are not on the tab any more",
          not w._tab_pv_time.isAncestorOf(w._pv_time_cond_editor))
    check("they are in the window",
          w._pv_time_cond_dialog.isAncestorOf(w._pv_time_cond_editor))
    ed = w._pv_time_cond_editor
    ed.clear()
    ed.add_row({"pv": "E", "min": 0.5, "max": None, "enabled": True}, announce=False)
    ed.changed.emit()
    check("button counts the rows", w._btn_pv_time_cond.text() == "Conditions (1)",
          w._btn_pv_time_cond.text())
    w._chk_pv_time_cond.setChecked(False)
    check("button says when they are off", w._btn_pv_time_cond.text() == "Conditions (off)",
          w._btn_pv_time_cond.text())

    w.close(); w2.close()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
