"""Laser settings — the window and every tab really build.

Five tabs are on the window and a sixth, Info / Log, is built and kept
working without being shown — `main.log()` writes into it. Both halves are
checked here: the tab bar has exactly five pages, and the hidden one still
takes a line.

    python testing/test_window_builds.py

Headless, silent and offline:

  * the `offscreen` platform, so nothing can reach a screen. Text metrics are
    wrong there, but nothing here measures text — the pictures are
    `render_tabs.py`'s job;
  * the settings file is pointed at a temporary folder, so the real one is
    neither read nor written;
  * the reading clock is never started, so not one request leaves this PC.

What it catches is the failure that costs the most: a typo in a tab that turns
into a red "the tab could not be built" page at the operator's first launch.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def main():
    from PySide6.QtWidgets import QApplication

    import ls_core as C
    import ls_engine
    import ov_t as ov

    tmp = tempfile.TemporaryDirectory()
    fake_cfg = Path(tmp.name) / "laser_settings.json"
    C.config_path = lambda: fake_cfg          # the real one is left alone
    # Nothing may leave this PC from a test. The clock is what would start it.
    ls_engine.PollEngine.start = lambda self: None

    app = QApplication.instance() or QApplication(sys.argv)
    import ls_ui as U
    U.install_app_look(app)

    import main as program
    win = program.LaserSettingsWindow()

    print("the window builds, with five tabs on it")
    check("the tabs", [win._tabs.tabText(i) for i in range(win._tabs.count())],
          ["Overview", "Limits", "DM settings", "DM writer", "Settings"])
    check("the Overview built", win._overview is not None, True)
    check("the Limits built", win._settings is not None, True)
    check("the DM settings built", win._dm_tab is not None, True)
    check("the DM writer built", win._dm_writer is not None, True)
    check("the Settings built", win._font is not None, True)
    print("the DM writer is off on a fresh install")
    check("off", win.dm_settings(), dict(C.DM_DEFAULTS))
    win._dm_writer._write.setChecked(True)
    check("the switch reaches the settings", win.dm_settings()["write"], True)
    check("and the engine is asked for it", win._dm_wanted(),
          (True, C.DM_DEFAULTS["title"], C.DM_DEFAULTS["every_s"]))
    win._dm_writer._every.setValue(2)
    check("Look every reaches the engine", win._dm_wanted()[2], 2)
    check("a bad value in the file is repaired",
          (C.dm_every("x"), C.dm_every(0), C.dm_every(99), C.dm_every(3.0)),
          (5, 1, 10, 3))
    win._dm_writer._every.setValue(C.DM_DEFAULTS["every_s"])
    win._dm_writer._write.setChecked(False)
    print("and Info / Log is built but is NOT one of them")
    check("it exists", win._info is not None, True)
    check("it is hidden", win._info.isVisible(), False)
    check("it is on no tab", any(win._tabs.tabText(i) == "Info / Log"
                                for i in range(win._tabs.count())), False)
    check("five squares", sorted(win._overview._squares), sorted(C.HALLS))
    print("a fresh install has the shot counter, on the Overview")
    check("the only rows are the counter and DM settings",
          [(C.is_shot_row(r), C.is_dm_row(r)) for r in win.rows()],
          [(True, False), (False, True)])
    # As the line above the list, not as a row of it (5.10.2026).
    check("not as a row of the list", len(win._overview._row_widgets), 0)
    check("but as the line above it",
          win._overview._shot_row is not None, True)
    win._overview.refresh()
    check("which reads Shot counter: 0", win._overview._shot_label.text(),
          "Shot counter: 0")
    check("with no Add shot counter button",
          hasattr(win._settings, "_btn_shots"), False)
    # The rest below was written for an empty list: the counter is taken off
    # here, in memory only, so those numbers stay what they were.
    win.rows().clear()
    win.settings_changed(rebuild=True)
    check("a fresh install watches no values", win.rows(), [])
    check("one channel per valve, the open switch, then SBW4's energy",
          win.channels(),
          [f"L3-PSS:SGV{n}_IN_OPEN_POSITION"
           for n in (501, 502, 503, 504, 505)] + list(C.FIRING_CHANNELS))

    print("a channel added on the Limits tab reaches the Overview")
    row = C.new_row(C.next_id(win.rows()), "TEST:PV", "Test value", "ns")
    C.limits_for(row, C.HALLS[0]).update({"min": 1.0, "max": 2.0})
    win.rows().append(row)
    win.settings_changed(rebuild=True)
    check("the Overview lists it", len(win._overview._row_widgets), 1)
    check("the Limits lists it", win._settings._table.rowCount(), 1)
    check("and there is no counting line under that table",
          hasattr(win._settings, "_foot"), False)
    print("an Overview row is a name, a square and a number — and no table")
    row_widget = win._overview._row_widgets[0]
    check("no table on the Overview", hasattr(win._overview, "_table"), False)
    check("it is named on the first tab", row_widget.texts()[0], "Test value")
    check("nothing was read, so the square claims nothing",
          row_widget.state(), C.STATE_UNKNOWN)
    look = win.look()
    check("the name column is at least its floor wide",
          row_widget._name.minimumWidth()
          >= ov.NAME_MIN_EM * look["size_px"], True)

    print("nothing is painted behind a row")
    check("its only background is transparent",
          row_widget.styleSheet().count("background:"),
          row_widget.styleSheet().count("background: transparent"))

    print("the look comes out of the settings file and reaches the row")
    # The fit OFF for all of this. With it on, the numbers in the document are
    # the ones the fit worked out from the window, not the ones written here —
    # that is the whole point of it, and it is checked in
    # `test_fit_to_window.py`. This block is the manual path.
    win.halls()["fit"] = False
    win.look().update(C.LOOK_DEFAULTS)
    win.halls()["band_px"] = C.HALLS_DEFAULTS["band_px"]
    win.halls()["word_px"] = C.HALLS_DEFAULTS["word_px"]
    win.settings_changed(rebuild=True)
    look = win.look()
    row_widget = win._overview._row_widgets[0]
    check("the defaults to begin with", look, dict(C.LOOK_DEFAULTS))
    check("and the row is that big", row_widget._name.font().pixelSize(),
          C.LOOK_DEFAULTS["size_px"])
    check("square too", row_widget._mark.side(), C.LOOK_DEFAULTS["mark_px"])
    look.update({"family": "Consolas", "size_px": 24, "bold": False,
                 "mark_px": 36})
    win.settings_changed(rebuild=True)
    row_widget = win._overview._row_widgets[0]
    check("a bigger size reaches the list",
          row_widget._name.font().pixelSize(), 24)
    check("and the square", row_widget._mark.side(), 36)
    # The typeface is checked on the STYLESHEET, not on `font().family()`: the
    # offscreen platform used here has no real fonts and reports whatever it
    # substituted. That the family reaches the font as well is checked by
    # rendering, in `render_tabs.py`, on the `windows` platform.
    check("and the typeface", 'font-family: "Consolas"'
          in row_widget.styleSheet(), True)
    check("and bold is off", row_widget._name.font().bold(), False)
    check("the Settings tab's boxes followed", win._font._size.value(), 24)
    win._font._defaults()
    check("and Back to the defaults puts them all back", win.look(),
          dict(C.LOOK_DEFAULTS))

    print("the five squares are arranged by hand, and can go back")
    halls = win.halls()
    check("automatic to begin with", halls["auto"], True)
    band = win._overview._band
    # The setting, or the squares' side if that is less — a band taller than
    # its squares is an empty strip, and that room is the list's.
    check("the band is the setting, never taller than its squares",
          band.height(),
          min(halls["band_px"], C.band_needed(halls, band.width(),
                                              halls["band_px"])))
    check("and the fit is still off from the block above", halls["fit"], False)
    check("and the squares are five even ones in it",
          len({round(s.width()) for s in band.squares.values()}), 1)
    halls["layout"] = {h: [0.0, 0.0, 0.18, 0.5] for h in C.HALLS}
    halls["auto"] = False
    halls["band_px"] = 220
    win.settings_changed(rebuild=True)
    band = win._overview._band
    check("a taller band reaches the Overview", band.height(), 220)
    check("and the hand-made rectangle too",
          band.squares["E3"].height(), 110)
    check("the board on the Settings tab shows the same",
          [round(v, 3) for v in win._font._board.tiles()[2]],
          [0.0, 0.0, 0.18, 0.5])
    win._font._arrange_again()
    check("Arrange them again goes back to automatic",
          win.halls()["auto"], True)
    check("and the squares are even again",
          len({round(s.width())
               for s in win._overview._band.squares.values()}), 1)
    halls = win.halls()
    halls["band_px"] = C.HALLS_DEFAULTS["band_px"]
    win.settings_changed(rebuild=True)

    print("the word inside the square can be switched off")
    check("on to begin with", win.halls()["show_word"], True)
    check("and the square has one", bool(
        win._overview._band.squares["Int"].word()), True)
    win._font._show_word.setChecked(False)
    check("the switch reaches the settings", win.halls()["show_word"], False)
    check("the size box went with it", win._font._word_px.isEnabled(), False)
    win._font._show_word.setChecked(True)

    print("a hand-made arrangement with a hall missing falls back")
    broken, problem = C.load_config(fake_cfg)
    broken = C.normalise({**broken, "halls": {"auto": False, "layout": {
        "Int": [0.0, 0.0, 0.2, 1.0]}}})
    check("it is not used", broken["halls"]["auto"], True)

    print("it is watched everywhere, with no limits but the one typed")
    check("one switch, and it is on", C.is_shown(row), True)
    for hall in C.HALLS[1:]:
        check(f"{hall} has no limits of its own",
              (C.limits_for(row, hall)["min"], C.limits_for(row, hall)["max"]),
              (None, None))

    print("unticking Show takes the row off the Overview at once")
    row["show"] = False
    win.settings_changed(rebuild=True)
    check("gone from the Overview", win._overview._rows_on_screen, [])
    check("no rows left", win._overview._row_widgets, [])
    check("and it says the list is empty rather than going blank",
          "tick Show" in win._overview._list.itemAt(0).widget().text(), True)
    # Under the "Not shown" line, which is a table row of its own.
    check("still on the Limits tab, under the Not shown line",
          win._settings._table_rows, [None, row])
    check("that line says so", win._settings._table.item(0, 0).text(),
          "Not shown")
    row["show"] = True
    win.settings_changed(rebuild=True)
    check("and back again", len(win._overview._row_widgets), 1)
    check("with its name", win._overview._row_widgets[0].texts()[0],
          "Test value")

    print("a formula row can be added and works its number out")
    calc = C.new_formula_row(C.next_id(win.rows()), "Three quarters",
                             "A * 0.749", "ns", {"A": int(row["id"])})
    win.rows().append(calc)
    win.settings_changed(rebuild=True)
    check("both rows on the Limits tab", win._settings._table.rowCount(), 2)
    import st_t
    check("the second row is B",
          win._settings._table.item(1, st_t._C_LETTER).text(), "B")
    check("and its box shows the formula",
          win._settings._table.item(1, st_t._C_PV).text(), "= A * 0.749")
    check("nothing is wrong with it", C.formula_problems(calc, win.rows()), [])
    check("a formula reads no channel of its own",
          [c for c in win.channels() if not c.startswith("L3-PSS")
           and c not in C.FIRING_CHANNELS],
          ["TEST:PV"])

    print("the hidden Info / Log page still keeps the log")
    check("the settings path was said", any("Settings:" in line for line in
                                            win._info._log.toPlainText()
                                            .splitlines()), True)
    win.log("a test line")
    check("a line lands in the log",
          "a test line" in win._info._log.toPlainText(), True)
    win._info.refresh()
    check("and it says what the program is doing",
          "channel" in win._info._state.text(), True)
    check("and when the last reading arrived",
          win._info._clock.text(), "nothing read yet")

    print("the settings are written where they were pointed")
    win._save_now()
    check("the file was written", fake_cfg.exists(), True)
    back, problem = C.load_config(fake_cfg)
    check("read back with no problem", problem, None)
    check("the channel survived", back["rows"][0]["pv"], "TEST:PV")
    check("its limit survived",
          C.limits_for(back["rows"][0], C.HALLS[0])["min"], 1.0)

    win.close()
    tmp.cleanup()

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
