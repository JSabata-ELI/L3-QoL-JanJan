"""The Workshop's text-size sample, and what "Save with overlay" actually writes.

Two things are checked, each one a complaint that was actually made:

  * **Text has a sample.** Line and Brush have always had a little picture beside them;
    Text had only a number, and 16 versus 40 reads the same on a spin box while coming
    out more than twice as tall on the picture. The sample must exist in the strip, sit
    beside the size box, draw its letters in the drawing colour, grow when the number
    grows, always stay inside its box, and be at TRUE size for the sizes that fit —
    with the amber dashed frame appearing only once it has to shrink them.

  * **The overlay switch decides the file.** Ticked, the saved picture must carry the
    drawing and the numbers the measuring tools wrote by themselves. Unticked, the file
    must be the picture and nothing else — pixel for pixel the same as the view with no
    drawing on it. The choice must also survive a restart.

Rendering is done with the real windows platform: offscreen has no fonts, so every text
measurement here would be meaningless.

Not shipped: the builder keeps test_* out of the bundle.

Usage:

    python testing/test_text_sample_and_overlay.py
    python testing/test_text_sample_and_overlay.py --shot out.png   # save screenshots

Exit code is 1 when a claim fails, so it can gate a build.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A scratch APPDATA, so the test never touches the real Workshop settings.
_TMP = Path(tempfile.mkdtemp(prefix="eli_wk_text_test_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                              # noqa: E402
import wk_t                                                     # noqa: E402
from PySide6.QtCore import QPointF, Qt                           # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage    # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu                 # noqa: E402


FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  -- " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


def _ink_box(widget, bg=(255, 255, 255)):
    """The rectangle the drawn letters occupy in a rendered widget, and their colour.

    Anything that is neither the white plate nor the pale frame counts as ink, so the
    measurement is of the letters themselves."""
    pm = widget.grab()
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGB888)
    x0 = y0 = 10 ** 6
    x1 = y1 = -1
    strongest = None
    best = -1
    # The frame runs along the border; the letters are drawn inside it.
    for y in range(2, img.height() - 2):
        for x in range(2, img.width() - 2):
            c = img.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            if r > 240 and g > 240 and b > 240:
                continue                      # the plate
            if abs(r - g) < 24 and abs(g - b) < 24:
                continue                      # grey frame / antialiasing of it
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x), max(y1, y)
            score = r - (g + b) / 2
            if score > best:
                best, strongest = score, (r, g, b)
    if x1 < 0:
        return None, None
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1), strongest


# ── 1. The text sample ──────────────────────────────────────────────────────
def test_text_sample(shot: "Path | None"):
    print("Text has a sample")
    prev = wk_t._TextPreview("sample")
    prev.resize(prev.sizeHint())
    prev.show()
    QApplication.processEvents()

    # It draws SOMETHING, in the drawing colour, not on a white-on-white plate.
    prev.set_text_style(14, QColor(200, 0, 0))
    QApplication.processEvents()
    box, col = _ink_box(prev)
    check(box is not None, "the letters are actually drawn")
    check(col is not None and col[0] > 120 and col[1] < 110 and col[2] < 110,
          "drawn in the drawing colour", f"strongest ink {col}")

    # Bigger number, bigger letters -- and never outside the box. This is the claim
    # the first attempt failed: everything from 16 up filled the box and came out
    # identical, so the sample said nothing about the sizes people actually use.
    sizes = (6, 8, 12, 16, 20, 28, 60, 120, 200)
    heights = []
    for size in sizes:
        prev.set_text_style(size, QColor(200, 0, 0))
        QApplication.processEvents()
        box, _ = _ink_box(prev)
        if box is None:
            check(False, f"letters drawn at {size}")
            heights.append(0)
            continue
        x, y, w, h = box
        heights.append(h)
        inside = (x >= 1 and y >= 1 and x + w <= prev.width() - 1
                  and y + h <= prev.height() - 1)
        check(inside, f"{size} stays inside the box",
              f"ink {box} in {prev.width()}x{prev.height()}")
    grows = all(b >= a for a, b in zip(heights, heights[1:]))
    check(grows, "a bigger number never draws smaller letters", f"heights {heights}")
    strictly = all(b > a for a, b in zip(heights, heights[1:]))
    check(strictly, "every step up is visible in the sample", f"heights {heights}")

    # True size over the range labels really use, and the amber dashed frame only
    # once the letters had to be shrunk.
    for size in (8, 16, 20):
        prev.set_text_style(size, QColor(200, 0, 0))
        r, k, squeezed = prev.fit()
        check(not squeezed and abs(k - 1.0) < 1e-9, f"{size} is drawn at true size",
              f"shrunk to {k:.3f}")
        QApplication.processEvents()
        box, _ = _ink_box(prev)
        check(box is not None and abs(box[3] - r.height()) <= 2,
              f"the letters really are {size} pt tall",
              f"ink {box[3] if box else '-'} px, measured {r.height():.0f} px")

    prev.set_text_style(200, QColor(200, 0, 0))
    _r, k, squeezed = prev.fit()
    check(squeezed and k < 0.5, "200 is marked as shrunk", f"shrunk to {k:.2f}")

    if shot is not None:
        prev.set_text_style(16, QColor(200, 0, 0))
        QApplication.processEvents()
        prev.grab().save(str(shot.with_name(shot.stem + "_sample_16.png")))
        prev.set_text_style(120, QColor(200, 0, 0))
        QApplication.processEvents()
        prev.grab().save(str(shot.with_name(shot.stem + "_sample_120.png")))
    prev.hide()

    # The big true-size sample must have room for the biggest size, not shrink it.
    pop = wk_t._TextZoomPopup(200)
    ink200 = wk_t._text_ink(200)
    check(pop.height() >= ink200.height() + 18,
          "the big sample is tall enough for 200 pt",
          f"{pop.height()} px window, {ink200.height():.0f} px letters")
    check(pop.width() >= ink200.width(), "and wide enough for it",
          f"{pop.width()} px window, {ink200.width():.0f} px letters")


# ── 2. The strip wiring ─────────────────────────────────────────────────────
def _panel():
    w = wk_t.WorkshopWidget()
    w.resize(1400, 800)
    w.show()
    QApplication.processEvents()
    return w


def test_strip_wiring(panel, shot: "Path | None"):
    print("The sample is wired to the size box")
    check(hasattr(panel, "_text_prev"), "the strip has a text sample")
    check(hasattr(panel, "_text_zoom"), "the strip has a big text sample")

    panel._text_sb.setValue(48)
    QApplication.processEvents()
    check(panel._text_prev._size == 48, "typing a size reaches the sample",
          f"sample at {panel._text_prev._size}")
    check(panel._canvas.font_size == 48, "and reaches the canvas")
    check(panel._text_zoom.isVisible(), "the big sample shows itself on a change")

    # A colour change must repaint the sample but not pop the big one up again.
    panel._text_zoom.hide()
    panel._on_color_picked(QColor(0, 120, 255))
    QApplication.processEvents()
    check(panel._text_prev._color == QColor(0, 120, 255),
          "the sample follows the drawing colour")
    check(not panel._text_zoom.isVisible(),
          "a colour change does not pop the big sample up")

    if shot is not None:
        panel._text_zoom.hide()
        QApplication.processEvents()
        panel._build_tool_strip  # keep the name obvious in a traceback
        panel.grab().save(str(shot.with_name(shot.stem + "_strip.png")))


# ── 3. Save with overlay ────────────────────────────────────────────────────
def test_overlay(panel):
    print("The overlay switch decides the file")
    arr = np.zeros((80, 120, 3), dtype=np.uint8)
    arr[:, :, :] = 40
    panel.receive_image(arr, "test")
    QApplication.processEvents()
    slot = panel._slot()
    check(slot is not None, "the test image is open")
    if slot is None:
        return

    a = wk_t._Annot(wk_t.A_RULER, [[10.0, 10.0], [100.0, 60.0]], "#ff0000", 3)
    a.label = "103.0 px"          # what a measuring tool writes by itself
    panel._canvas.add_annot(a)
    txt = wk_t._Annot(wk_t.A_TEXT, [[15.0, 70.0]], "#ff0000", 2)
    txt.text = "hello"
    txt.font_size = 20
    panel._canvas.add_annot(txt)
    QApplication.processEvents()
    check(len(slot.annots) >= 2, "the drawing is on the picture",
          f"{len(slot.annots)} items")

    plain_want = wk_t._np_to_qimage(wk_t.render_view(slot.base, slot.view)).convertToFormat(
        QImage.Format.Format_RGB888)

    panel._cb_burn.setChecked(False)
    QApplication.processEvents()
    off = panel._render_for_save(slot)
    check(off is not None and off == plain_want,
          "unticked writes the picture and nothing else")

    panel._cb_burn.setChecked(True)
    QApplication.processEvents()
    on = panel._render_for_save(slot)
    check(on is not None and on != plain_want, "ticked writes the drawing in too")

    # And it is the drawing that made the difference, in the right places: the ruler
    # line, and the caption the tool wrote.
    if on is not None and off is not None:
        diff = 0
        for y in range(on.height()):
            for x in range(on.width()):
                if on.pixel(x, y) != off.pixel(x, y):
                    diff += 1
        check(diff > 200, "the drawing covers a real part of the picture",
              f"{diff} pixels differ")

    # The switch has to be reachable where the saving is done as well: with the Save
    # section rolled up, the left-hand panel gives no sign the choice exists.
    seen = {}

    class _ListingMenu(QMenu):
        """A menu that writes down what it was given instead of popping up."""

        def exec(self, *_a, **_k):
            seen["actions"] = [(a.text(), a.isCheckable(), a.isChecked())
                               for a in self.actions()]
            return None

    real_menu = wk_t.QMenu
    wk_t.QMenu = _ListingMenu
    try:
        panel._cb_burn.setChecked(True)
        panel._canvas_menu(panel.mapToGlobal(panel.rect().center()), QPointF(5, 5))
    finally:
        wk_t.QMenu = real_menu
    entry = [a for a in seen.get("actions", []) if a[0] == "Save with overlay"]
    check(len(entry) == 1, "the right-click menu carries the switch too",
          f"{len(entry)} entries")
    check(bool(entry) and entry[0][1] and entry[0][2],
          "and shows it ticked when it is ticked", f"{entry}")

    # The choice must survive a restart.
    panel._cb_burn.setChecked(False)
    QApplication.processEvents()
    state = json.loads(Path(wk_t._UI_STATE_PATH).read_text(encoding="utf-8"))
    check(state.get("save_overlay") is False, "the choice is written down",
          f"save_overlay = {state.get('save_overlay')!r}")
    panel._cb_burn.setChecked(True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=Path, default=None)
    args = ap.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    # The same light look main.py gives the real window. Without it this PC's dark
    # mode shows through and every screenshot lies about what the operator sees.
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                      "QLabel { background: transparent; }"
                      "QPushButton { padding: 5px 8px; }"
                      "QComboBox { padding: 3px 6px; }")
    test_text_sample(args.shot)
    panel = _panel()
    test_strip_wiring(panel, args.shot)
    test_overlay(panel)
    panel.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} claim(s) failed:")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("All claims hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
