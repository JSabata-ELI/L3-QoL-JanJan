"""Laser settings — "Fit everything to the window" really fills the window.

    python testing/test_fit_to_window.py

Headless, silent, offline and nothing on screen: the `offscreen` platform, the
window given `WA_DontShowOnScreen` as well, the settings file pointed at a
temporary folder and the reading clock never started.

What it pins is the complaint of 1.10.2026, in three parts:

  * "Height of the band" must BE the band on screen. It said 400 over a band
    253 px tall, because the first attempt kept a scale factor in the drawing
    code and never wrote the answer down;
  * no strip of nothing between the tab bar and the line. The band is exactly
    as tall as its squares, so there is nothing to centre them in;
  * the squares and the list fill the window — "bez nějakých větších mezer" —
    whatever size the window is, and the window may now be any size at all.

And the half that is easy to get wrong in the other direction: a number the
operator TYPED must never be quietly replaced by one the fit worked out.

ABSOLUTE PIXEL SIZES ARE NEVER ASSERTED HERE. The `offscreen` platform's text
metrics are not Windows', but they are self-consistent — the solver and this
file ask the same `QFontMetrics` — so every check is a relation between numbers
and not a number anybody measured once.
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


def at_most(what, got, limit):
    if got > limit:
        FAILED.append(f"{what}: got {got!r}, wanted at most {limit!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted at most {limit!r}")
    else:
        print(f"  ok    {what} ({got} <= {limit})")


def at_least(what, got, limit):
    if got < limit:
        FAILED.append(f"{what}: got {got!r}, wanted at least {limit!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted at least {limit!r}")
    else:
        print(f"  ok    {what} ({got} >= {limit})")


# ─────────────────────────────────────────────────────────────────────────────
# One real window, never shown
# ─────────────────────────────────────────────────────────────────────────────
def build():
    """The real program, with the real Settings tab wired to the real Overview.

    The real window and not a stand-in, because half of what is checked here is
    the handover between the two tabs: the fit writes the document and the
    Settings tab's boxes have to follow without that looking like somebody
    typing.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import ls_core as C
    import ls_engine

    tmp = tempfile.TemporaryDirectory()
    C.config_path = lambda: Path(tmp.name) / "laser_settings.json"
    ls_engine.PollEngine.start = lambda self: None

    # Before the first QWidget, or Qt aborts the process outright — with no
    # Python traceback, because `qFatal` is not an exception.
    app = QApplication.instance() or QApplication(sys.argv)
    import ls_ui as U
    U.install_app_look(app)

    import main as program
    win = program.LaserSettingsWindow()
    # Belt and braces over the offscreen platform: the window manager is never
    # shown this window at all.
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    return win, tmp


def resize(win, w, h):
    from PySide6.QtWidgets import QApplication
    win.resize(w, h)
    QApplication.processEvents()
    # The fit is coalesced to the next turn of the event loop, so a second
    # round is what actually runs it. A third, because placing the band can
    # move the list.
    QApplication.processEvents()
    QApplication.processEvents()
    return win._overview


def add_rows(win, names):
    import ls_core as C
    rows = win.rows()
    del rows[:]
    for i, name in enumerate(names, start=1):
        row = C.new_row(i, f"TEST:PV{i}", name, "ns")
        for hall in C.HALLS:
            C.limits_for(row, hall).update({"min": 0.0, "max": 100.0})
        rows.append(row)
    win.settings_changed(rebuild=True)


def row_height(tab):
    """What one row really takes, measured off the widget on screen."""
    if not tab._row_widgets:
        return 0
    w = tab._row_widgets[0]
    return w.sizeHint().height() + tab._list.spacing()


# ─────────────────────────────────────────────────────────────────────────────
# The tests
# ─────────────────────────────────────────────────────────────────────────────
def test_the_band_is_what_the_box_says(win):
    print("the band on screen IS the number in the box")
    import ls_core as C
    add_rows(win, ["Front end delay", "Amplifier 1 voltage", "GDD"])
    for w, h in ((1180, 720), (1920, 1080), (806, 470), (420, 300)):
        tab = resize(win, w, h)
        halls = C.halls_of(win._cfg)
        check(f"{w}x{h}: the band is its setting",
              tab._band.height(), halls["band_px"])
        check(f"{w}x{h}: and the Settings box says the same",
              win._font._band.value(), halls["band_px"])


def test_no_strip_above_or_below_the_squares(win):
    print("no strip of nothing between the tab bar and the line")
    for w, h in ((1180, 720), (1920, 1080), (420, 300)):
        tab = resize(win, w, h)
        square = tab._band.squares["Int"]
        check(f"{w}x{h}: the square starts at the band's top", square.y(), 0)
        check(f"{w}x{h}: and ends at its bottom",
              square.height(), tab._band.height())
        check(f"{w}x{h}: and it is square", square.width(), square.height())
        check(f"{w}x{h}: all five the same size",
              len({(s.width(), s.height())
                   for s in tab._band.squares.values()}), 1)


def test_the_verdict_square_is_one_and_a_half_times_the_text(win):
    print("the verdict square is one and a half times the text")
    import ls_core as C
    for w, h in ((1180, 720), (1920, 1080), (806, 470)):
        tab = resize(win, w, h)
        look = C.look_of(win._cfg)
        check(f"{w}x{h}: worked out from the text",
              look["mark_px"], round(look["size_px"] * C.FIT_MARK_RATIO))
        check(f"{w}x{h}: and that reached the row",
              tab._row_widgets[0]._mark.side(), look["mark_px"])


def test_it_fills_the_height(win):
    """Nothing overflows, and room left over is room nothing could have used.

    "Bez nějakých větších mezer" cannot be asserted as "the slack is small",
    and it took a measurement to see why. With three rows on a wide window the
    text stops growing because a ROW IS AS WIDE AS THE WINDOW, and the band
    stops growing because a square has to stay square — so two hundred pixels
    can be left at the bottom and not one of them could have been used by
    anything. That is the honest consequence of "čtverečky zůstanou čtverce".

    So the assertion is the real rule: either the slack is under a row, or
    everything that could have taken it is already at a limit.
    """
    print("the height is filled, or what is left could not have been used")
    import ls_core as C
    from PySide6.QtGui import QFontMetrics
    for w, h in ((1180, 720), (1920, 1080), (806, 470), (420, 300)):
        tab = resize(win, w, h)
        n = len(tab._row_widgets)
        look = C.look_of(win._cfg)
        size = int(look["size_px"])
        fm = QFontMetrics(tab._base_font(look, size))
        row_h = max(fm.height(), int(look["mark_px"]))
        avail = tab.height() - tab._overhead(n)
        slack = avail - tab._band.height() - n * row_h
        at_least(f"{w}x{h}: nothing overflows", slack, 0)
        if slack <= row_h:
            print(f"  ok    {w}x{h}: and it is filled to within a row")
            continue
        list_w = tab._list_width()
        _row_h, row_w = tab._row_measures(look, list_w)
        at_most(f"{w}x{h}: the band is already as wide as a square can be",
                tab._band.height(),
                int(C.band_side_cap(tab._band_width())) + 1)
        at_most(f"{w}x{h}: and one more text size would not fit the width",
                list_w + 1, row_w(size + 1))


def test_nothing_runs_off_the_side(win):
    print("no row is wider than the window")
    for w, h in ((1180, 720), (1920, 1080), (420, 300)):
        tab = resize(win, w, h)
        widest = 0
        for row in tab._row_widgets:
            widest = max(widest, row._name.width() + 3 * 12
                         + row._mark.side()
                         + row._value.sizeHint().width()
                         + row._unit.sizeHint().width())
        at_most(f"{w}x{h}: the widest row fits", widest,
                tab._scroll.viewport().width())
        check(f"{w}x{h}: and there is no sideways scrollbar",
              tab._scroll.horizontalScrollBar().isVisible(), False)


def test_a_bigger_window_means_bigger_everything(win):
    print("a bigger window really does mean bigger everything")
    import ls_core as C
    resize(win, 806, 470)
    small = (C.look_of(win._cfg)["size_px"], C.halls_of(win._cfg)["band_px"])
    resize(win, 1920, 1080)
    big = (C.look_of(win._cfg)["size_px"], C.halls_of(win._cfg)["band_px"])
    at_least("the text grew", big[0], small[0] + 1)
    at_least("the squares grew", big[1], small[1] + 1)
    resize(win, 420, 300)
    tiny = (C.look_of(win._cfg)["size_px"], C.halls_of(win._cfg)["band_px"])
    at_most("and a tiny window shrinks them again", tiny[0], small[0])
    at_least("but never past the floor", tiny[0], C.LOOK_LIMITS["size_px"][0])
    at_least("nor the band past its own", tiny[1], 1)


def test_nothing_watched_gives_the_squares_the_window(win):
    print("nothing watched: the squares take the window, the text is left be")
    import ls_core as C
    resize(win, 1180, 720)
    before = int(C.look_of(win._cfg)["size_px"])
    for row in win.rows():
        row["show"] = False
    win.settings_changed(rebuild=True)
    tab = resize(win, 1180, 720)
    check("no rows on screen", len(tab._row_widgets), 0)
    at_least("the band took the room", tab._band.height(), 200)
    # No row to measure means no information about how big a row should be.
    # Parking the ceiling here because "there is room" would ambush him the
    # moment he ticked Show again.
    check("and the text size was left alone",
          int(C.look_of(win._cfg)["size_px"]), before)
    for row in win.rows():
        row["show"] = True
    win.settings_changed(rebuild=True)


def test_fit_off_uses_the_numbers_verbatim(win):
    print("with the fit off, the numbers are used exactly as they are written")
    import ls_core as C
    resize(win, 1180, 720)
    halls = C.halls_of(win._cfg)
    look = C.look_of(win._cfg)
    halls["fit"] = False
    look["size_px"], look["mark_px"] = 22, 40
    halls["band_px"], halls["word_px"] = 180, 14
    win.settings_changed(rebuild=True)
    tab = resize(win, 1180, 720)
    check("the band is what was written", tab._band.height(), 180)
    check("the text too", tab._row_widgets[0]._name.font().pixelSize(), 22)
    check("and the square", tab._row_widgets[0]._mark.side(), 40)
    check("the fit did not switch itself back on",
          C.halls_of(win._cfg)["fit"], False)


def test_typing_switches_the_fit_off(win):
    print("typing a size switches the fit off and that number stands")
    import ls_core as C
    halls = C.halls_of(win._cfg)
    halls["fit"] = True
    win._font.reload()
    win.settings_changed(rebuild=True)
    resize(win, 1180, 720)
    check("the fit is on", C.halls_of(win._cfg)["fit"], True)
    win._font._size.setValue(26)
    check("typing switched it off", C.halls_of(win._cfg)["fit"], False)
    check("and the box followed", win._font._fit.isChecked(), False)
    check("the number is his", C.look_of(win._cfg)["size_px"], 26)
    resize(win, 900, 600)
    check("and a resize does not take it away",
          C.look_of(win._cfg)["size_px"], 26)


def test_parking_does_not_switch_the_fit_off(win):
    print("the fit parking its own numbers is not somebody typing")
    import ls_core as C
    halls = C.halls_of(win._cfg)
    halls["fit"] = True
    win._font.reload()
    win.settings_changed(rebuild=True)
    for w, h in ((1180, 720), (1500, 900), (700, 420), (1920, 1080)):
        resize(win, w, h)
        check(f"{w}x{h}: still on", C.halls_of(win._cfg)["fit"], True)


def test_switching_the_fit_off_gives_his_numbers_back(win):
    print("switching the fit off puts the operator's own numbers back")
    import ls_core as C
    resize(win, 1180, 720)
    # His numbers, typed with the fit off.
    win._font._fit.setChecked(False)
    win._font._size.setValue(21)
    win._font._mark.setValue(33)
    win._font._band.setValue(170)
    win._font._word_px.setValue(13)
    mine = (21, 33, 170, 13)
    # The fit on: it overrules all four.
    win._font._fit.setChecked(True)
    resize(win, 1920, 1080)
    look, halls = C.look_of(win._cfg), C.halls_of(win._cfg)
    now = (look["size_px"], look["mark_px"], halls["band_px"], halls["word_px"])
    check("the fit really did change them", now != mine, True)
    # And off again.
    win._font._fit.setChecked(False)
    look, halls = C.look_of(win._cfg), C.halls_of(win._cfg)
    check("every one of the four came back",
          (look["size_px"], look["mark_px"],
           halls["band_px"], halls["word_px"]), mine)
    check("and so did the boxes",
          (win._font._size.value(), win._font._mark.value(),
           win._font._band.value(), win._font._word_px.value()), mine)

    print("and they survive the settings file")
    win._save_now()
    back, problem = C.load_config(C.config_path())
    check("read back with no problem", problem, None)
    check("the remembered numbers are in the file",
          tuple(C.halls_of(back)["manual"][k] for k in C.MANUAL_KEYS), mine)


def test_it_settles(win):
    print("the fit settles: solving twice on one window changes nothing")
    import ls_core as C
    halls = C.halls_of(win._cfg)
    halls["fit"] = True
    win._font.reload()
    tab = resize(win, 1180, 720)
    first = (C.look_of(win._cfg)["size_px"], C.look_of(win._cfg)["mark_px"],
             halls["band_px"], halls["word_px"], tab._band.height())
    for _ in range(5):
        tab._fit_to_window(force=True)
    again = (C.look_of(win._cfg)["size_px"], C.look_of(win._cfg)["mark_px"],
             C.halls_of(win._cfg)["band_px"], C.halls_of(win._cfg)["word_px"],
             tab._band.height())
    check("five more solves, the same answer", again, first)

    print("and a burst of resizes costs one solve")
    from PySide6.QtWidgets import QApplication
    count = [0]
    real = tab._fit_to_window

    def counted(force=False):
        count[0] += 1
        return real(force=force)

    tab._fit_to_window = counted
    tab._fit_timer.timeout.disconnect()
    tab._fit_timer.timeout.connect(tab._fit_to_window)
    for width in range(1100, 1160, 6):
        win.resize(width, 700)
    QApplication.processEvents()
    QApplication.processEvents()
    at_most("ten resizes, one solve", count[0], 1)


def test_every_name_and_number_is_shown_in_full(win):
    """The complaint of 1.10.2026: "SBW4 Compressed" came out "SBW4 Compres…".

    So the name column takes what the longest name really needs and the TEXT
    SIZE gives way — "udělej to tak, aby vždy byly vidět slova a hodnoty".
    """
    print("every name and every number is shown whole, never cut")
    import ls_core as C
    halls = C.halls_of(win._cfg)
    halls["fit"] = True
    win._font.reload()
    names = ["GDD", "TOD", "SBW4 Compressed", "Compressor pressure gauge"]
    for w, h in ((1180, 720), (806, 470), (420, 300)):
        add_rows(win, names)
        tab = resize(win, w, h)
        for row, name in zip(tab._row_widgets, names):
            check(f"{w}x{h}: {name!r} is whole", row._name.text(),
                  name + ":")
        for row in tab._row_widgets:
            check(f"{w}x{h}: and so is {row.texts()[1]!r}",
                  f"{row._value.text()} {row._unit.text()}".strip(),
                  row.texts()[1])
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(row._value.font())
            at_most(f"{w}x{h}: {row._value.text()!r} is not clipped",
                    fm.horizontalAdvance(row._value.text())
                    + row._value.indent(), row._value.width())
            at_most(f"{w}x{h}: {row._unit.text()!r} is not clipped",
                    fm.horizontalAdvance(row._unit.text())
                    + row._unit.indent(), row._unit.width())


def test_a_monstrous_name_is_elided_as_a_last_resort(win):
    print("a name no size of text could fit is cut, and says so in its tooltip")
    import ls_core as C
    add_rows(win, ["GDD", "TOD", "x" * 400])
    tab = resize(win, 1180, 720)
    # The text really does go all the way to its floor first. That is the
    # contract — the name wins and the text gives way — and only when the floor
    # is not enough either does the name get cut.
    check("the text went to its floor", int(C.look_of(win._cfg)["size_px"]),
          C.LOOK_LIMITS["size_px"][0])
    row = tab._row_widgets[2]
    check("the name on screen is cut", row._name.text() != "x" * 400, True)
    check("and the whole of it is in the tooltip",
          "x" * 400 in row.toolTip(), True)
    add_rows(win, ["GDD", "TOD", "SBW4 Compressed"])


def test_the_list_is_centred(win):
    print("the list sits in the middle of the window, as one block")
    import ls_core as C
    halls = C.halls_of(win._cfg)
    halls["fit"] = True
    win._font.reload()
    add_rows(win, ["GDD", "TOD", "SBW4 Compressed"])
    for w, h in ((1180, 720), (1920, 1080), (806, 470)):
        tab = resize(win, w, h)
        viewport = tab._scroll.viewport().width()
        widths = {r.width() for r in tab._row_widgets}
        check(f"{w}x{h}: every row is the same width", len(widths), 1)
        lefts = {r.x() for r in tab._row_widgets}
        check(f"{w}x{h}: and they all start in the same place", len(lefts), 1)
        row = tab._row_widgets[0]
        at_most(f"{w}x{h}: a row is no wider than the window", row.width(),
                viewport)
        # Centred to within a pixel: an odd number of leftover pixels cannot be
        # split in two.
        left = row.x()
        right = viewport - (row.x() + row.width())
        at_most(f"{w}x{h}: the room each side is the same", abs(left - right), 1)
        at_least(f"{w}x{h}: and there is room on the left at all", left, 0)


def test_the_energy_is_to_two_places_and_the_unit_is_lifted(win):
    print("an energy reads to hundredths, and fs^2 is written fs²")
    import ls_core as C
    check("a joule to two places", C.value_text(19.9294, "J"), "19.93 J")
    check("and it keeps them when they are zeros",
          C.value_text(20.0, "J"), "20.00 J")
    check("millijoules too", C.value_text(1.23456, "mJ"), "1.23 mJ")
    check("the unit decides, whatever the case",
          C.value_text(19.9294, "j"), "19.93 j")
    print("and every other unit keeps the shortest form that loses nothing")
    check("a delay", C.value_text(12.0, "ns"), "12 ns")
    check("a pressure", C.value_text(0.0012, "mbar"), "0.0012 mbar")
    print("the caret becomes a superscript, on screen only")
    check("squared", C.unit_text("fs^2"), "fs²")
    check("cubed", C.unit_text("fs^3"), "fs³")
    check("and a negative power", C.unit_text("cm^-1"), "cm⁻¹")
    check("two digits", C.unit_text("m^10"), "m¹⁰")
    check("in a reading", C.value_text(24900.0, "fs^2"), "24900 fs²")
    check("nothing to lift is left alone", C.unit_text("mbar"), "mbar")
    check("and a caret with no number is left as it was",
          C.unit_text("a^x"), "a^x")
    check("one typed as a superscript already stays that way",
          C.unit_text("fs²"), "fs²")

    print("the Limits tab's Unit box still shows what was typed into it")
    import ls_core as CC
    rows = win.rows()
    del rows[:]
    row = CC.new_row(1, "TEST:PV1", "GDD", "fs^2")
    for hall in CC.HALLS:
        CC.limits_for(row, hall).update({"min": 0.0, "max": 1e9})
    rows.append(row)
    win.settings_changed(rebuild=True)
    table = win._settings._table
    import st_t
    check("the cell is the raw text", table.item(0, st_t._C_UNIT).text(),
          "fs^2")
    check("and the settings file keeps it", win.rows()[0]["unit"], "fs^2")
    # A canned reading straight into the engine's own store — nothing leaves
    # this PC, and the Overview reads it the way it reads a real one.
    class _Reading:
        def __init__(self, value):
            self.value = value
            self.error = None

        @property
        def has_value(self):
            return True

    import time
    win._engine.readings()["TEST:PV1"] = _Reading(24900.0)
    win._engine.last_ok()["TEST:PV1"] = time.time()
    resize(win, 1180, 720)
    # A resize works the SIZES out; it is `refresh()` that reads the numbers.
    win._overview.refresh()
    shown = win._overview._row_widgets[0].texts()[1]
    check("while the Overview lifts it", shown, "24900 fs²")
    check("and the caret is nowhere on screen", "^" in shown, False)


def test_a_hand_made_arrangement_keeps_its_shape(win):
    print("a hand-made arrangement grows with the window, in one piece")
    import ls_core as C
    halls = C.halls_of(win._cfg)
    halls["fit"] = True
    halls["auto"] = False
    halls["layout"] = {h: [0.04 * i, 0.0, 0.18, 0.5]
                       for i, h in enumerate(C.HALLS)}
    halls["band_frac"] = 0.4
    win._font.reload()
    win.settings_changed(rebuild=True)
    for w, h in ((1180, 720), (1920, 1080)):
        tab = resize(win, w, h)
        band = tab._band
        check(f"{w}x{h}: the band is still its own setting",
              band.height(), C.halls_of(win._cfg)["band_px"])
        check(f"{w}x{h}: and the tile is still half of it",
              abs(band.squares["E3"].height() - band.height() * 0.5) <= 1,
              True)
    small = resize(win, 806, 470)._band.height()
    big = resize(win, 1920, 1080)._band.height()
    at_least("a bigger window gives the arrangement more room", big, small + 1)
    halls = C.halls_of(win._cfg)
    halls["auto"] = True
    halls["layout"] = {}
    win.settings_changed(rebuild=True)


def test_fit_off_a_tall_band_gives_its_room_to_the_list(win):
    """1.10.2026: Fit off, band 500 px, window made taller — the strip round
    the squares grew and the energies stayed hidden. The band is never placed
    taller than its squares now; the rest is the list's."""
    print("fit off, band set too tall: the squares fill it, the list gets more")
    import ls_core as C
    add_rows(win, ["GDD", "TOD", "SBW4 Compressed", "Front end delay"])
    halls = C.halls_of(win._cfg)
    look = C.look_of(win._cfg)
    halls["fit"] = False
    halls["auto"], halls["layout"] = True, {}
    halls["band_px"] = 500
    look["size_px"], look["mark_px"] = 20, 30
    win.settings_changed(rebuild=True)
    for w, h in ((1550, 520), (1550, 900)):
        tab = resize(win, w, h)
        square = tab._band.squares["Int"]
        check(f"{w}x{h}: no strip above the squares", square.y(), 0)
        at_most(f"{w}x{h}: the band is no taller than a square",
                tab._band.height(), square.height() + 1)
        # + 60, not + 100, since the Scanning line above the list
        # (12.10.2026) takes its own height out of the same room.
        at_least(f"{w}x{h}: and the list got more than its minimum",
                 tab._scroll.height(), tab.LIST_MIN + 60)
    small = resize(win, 1550, 520)._scroll.height()
    big = resize(win, 1550, 900)._scroll.height()
    at_least("a taller window is a taller list, not a taller gap",
             big, small + 300)
    halls["fit"] = True
    win.settings_changed(rebuild=True)


def test_the_board_is_the_real_size(win):
    print("the board on the Settings tab draws the squares at the real size")
    tab = resize(win, 1550, 900)
    board = win._font._board
    bw, bh = tab.band_shape()
    board.set_band_px((bw, bh))
    want = board.heightForWidth(bw + 2)
    at_most("a board as wide as the band is as tall as it",
            abs(want - (bh + 2)), 2)
    board.resize(bw + 2, want)
    real = tab._band.squares["Int"].width()
    model = board._tile_rect(0).width()
    at_most("and its squares are the Overview's size", abs(model - real), 3)


def test_the_maximise_size_is_kept(win):
    print("the Maximise size: repaired, saved, read back")
    import ls_core as C
    check("off by default", C.window_max_of({})["on"], False)
    bad = C.window_max_of({"window_max": {"on": 1, "w": "x", "h": 99999}})
    check("rubbish repaired", (bad["on"], bad["w"], bad["h"]),
          (True, C.WINDOW_MAX_DEFAULTS["w"], C.WINDOW_MAX_LIMITS[1]))
    check("never bigger than the screen",
          C.max_rect(0, 0, 3000, 500, 1920, 1040), (0, 0, 1920, 500))
    check("the remembered place is kept",
          C.max_rect(300, 120, 1300, 650, 1920, 1040), (300, 120, 1300, 650))
    check("and slid back in when it would hang over the edge",
          C.max_rect(1000, 900, 1300, 650, 1920, 1040), (620, 390, 1300, 650))
    font = win._font
    font._max_on.setChecked(True)
    font._max_w.setValue(1300)
    font._max_h.setValue(650)
    font._max_x.setValue(300)
    font._max_y.setValue(120)
    wm = win.window_max()
    check("the boxes reach the document",
          (wm["on"], wm["w"], wm["h"], wm["x"], wm["y"]),
          (True, 1300, 650, 300, 120))
    win._save_now()
    back, problem = C.load_config(C.config_path())
    check("read back with no problem", problem, None)
    wm = C.window_max_of(back)
    check("and it is in the file",
          (wm["on"], wm["w"], wm["h"], wm["x"], wm["y"]),
          (True, 1300, 650, 300, 120))
    font._max_on.setChecked(False)


def test_rows_read_name_value_square(win):
    """`GDD: -12000 fs²  [tick]` — names end on one line, then a space and a
    minus slot, numbers start on one line, squares in one column straight
    after the longest number (2.10.2026)."""
    print("a row is name, number, square; names right, numbers left")
    add_rows(win, ["GDD", "SBW4 energy"])
    tab = resize(win, 1180, 720)
    a, b = tab._row_widgets
    check("the name comes first", a._name.x() < a._value.x(), True)
    check("then the number, then the square",
          a._value.x() < a._mark.x(), True)
    check("names end on one line",
          a.mapTo(tab, a._name.geometry().topRight()).x(),
          b.mapTo(tab, b._name.geometry().topRight()).x())
    check("numbers end on one line",
          a.mapTo(tab, a._value.geometry().topRight()).x(),
          b.mapTo(tab, b._value.geometry().topRight()).x())
    check("squares in one column",
          a.mapTo(tab, a._mark.pos()).x(), b.mapTo(tab, b._mark.pos()).x())
    from PySide6.QtCore import Qt
    check("the name is flush right",
          bool(a._name.alignment() & Qt.AlignmentFlag.AlignRight), True)
    check("the number is flush left",
          bool(a._value.alignment() & Qt.AlignmentFlag.AlignLeft), True)
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(a._value.font())
    a.set_value("12 ns")
    b.set_value("-12 ns")
    check("a number without a minus keeps the minus slot",
          a._value.indent(), fm.horizontalAdvance("-"))
    check("a negative number fills it", b._value.indent(), 0)
    name_end = a.mapTo(tab, a._name.geometry().topRight()).x() + 1
    check("one space between the colon and the number",
          a.mapTo(tab, a._value.pos()).x() - name_end,
          fm.horizontalAdvance(" "))

    print("the units stand under each other (GDD / TOD / SBW4 Compressed)")
    add_rows(win, ["GDD", "TOD", "SBW4 Compressed"])
    tab = resize(win, 1180, 720)
    rows = tab._row_widgets
    for row, text in zip(rows, ("23800 fs²", "-88000 fs³", "19.81 J")):
        row.set_value(text)
    tab._fit_columns()
    tab._list.activate()
    for row in rows:
        row.layout().activate()
    xs = [r.mapTo(tab, r._unit.pos()).x() for r in rows]
    check("the units start on one line", len(set(xs)), 1)
    xs = [r.mapTo(tab, r._value.pos()).x() for r in rows]
    check("the numbers start on one line", len(set(xs)), 1)
    xs = [r.mapTo(tab, r._mark.pos()).x() for r in rows]
    check("the squares in one column", len(set(xs)), 1)
    check("the number is split from its unit",
          (rows[1]._value.text(), rows[1]._unit.text()), ("-88000", "fs³"))


def test_one_square_beside_the_list(win):
    """One square, named after the hall in force, with the list beside it."""
    print("the one-square mode")
    import ls_core as C
    add_rows(win, ["Front end delay", "Amplifier 1 voltage", "GDD"])
    halls = C.halls_of(win._cfg)
    halls["single"] = True
    halls["fit"] = True
    win.settings_changed(rebuild=True)
    for w, h in ((1180, 720), (806, 470), (420, 300)):
        tab = resize(win, w, h)
        sq = tab._single
        check(f"{w}x{h}: the one square is there", sq.isVisible(), True)
        check(f"{w}x{h}: the five are not", tab._band.isVisible(), False)
        check(f"{w}x{h}: it is square", sq.width(), sq.height())
        check(f"{w}x{h}: its side is the box", sq.width(), halls["band_px"])
        at_most(f"{w}x{h}: the list is right of it",
                sq.x() + sq.width(), tab._scroll.x())
        at_most(f"{w}x{h}: and inside the window",
                tab._scroll.x() + tab._scroll.width(), tab.width())
        for row in tab._row_widgets:
            check(f"{w}x{h}: {row.texts()[0]!r} is whole",
                  row._name.text(), row.texts()[0] + ":")
        check(f"{w}x{h}: no row is wider than the list",
              max(r.width() for r in tab._row_widgets)
              <= tab._scroll.viewport().width(), True)
    # Nothing can be read in this test, so the hall is the fallback, Int —
    # whose square is called L3.
    check("it names the hall in force", tab._single.label(), "L3")
    check("the Settings tab calls the box the square's size",
          win._font._band_label.text(), "Size of the square")
    check("and the board is switched off", win._font._board.isHidden(), True)
    halls["single"] = False
    win.settings_changed(rebuild=True)
    tab = resize(win, 1180, 720)
    check("back to five: the band is there", tab._band.isVisible(), True)
    check("and the one square is not", tab._single.isVisible(), False)
    check("and the board is back", win._font._board.isHidden(), False)


def main():
    win, tmp = build()
    for test in (test_the_band_is_what_the_box_says,
                 test_no_strip_above_or_below_the_squares,
                 test_the_verdict_square_is_one_and_a_half_times_the_text,
                 test_it_fills_the_height,
                 test_nothing_runs_off_the_side,
                 test_a_bigger_window_means_bigger_everything,
                 test_nothing_watched_gives_the_squares_the_window,
                 test_fit_off_uses_the_numbers_verbatim,
                 test_typing_switches_the_fit_off,
                 test_parking_does_not_switch_the_fit_off,
                 test_switching_the_fit_off_gives_his_numbers_back,
                 test_it_settles,
                 test_every_name_and_number_is_shown_in_full,
                 test_a_monstrous_name_is_elided_as_a_last_resort,
                 test_the_list_is_centred,
                 test_the_energy_is_to_two_places_and_the_unit_is_lifted,
                 test_a_hand_made_arrangement_keeps_its_shape,
                 test_fit_off_a_tall_band_gives_its_room_to_the_list,
                 test_the_board_is_the_real_size,
                 test_the_maximise_size_is_kept,
                 test_rows_read_name_value_square,
                 test_one_square_beside_the_list):
        test(win)
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
