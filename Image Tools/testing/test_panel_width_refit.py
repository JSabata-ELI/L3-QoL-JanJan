"""The side panel has to widen when its words turn out to need more room.

The width was worked out once, while the panel was still being built — before it
had a screen, a scaling factor or its final fonts. On a display at 125 % every
label came out wider than measured and the right-hand button of each pair lost
its last word, with no sideways scroll bar to reach it. The measurement is now
repeated once the panel is really on screen and whenever a group is opened, and
it may only widen, never narrow.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_panel_width_refit.py

Offscreen has no fonts and lies about text width, so this needs the real platform.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_wk_width_")

from PySide6.QtWidgets import QApplication                # noqa: E402

import wk_t                                               # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setStyle("Fusion")


def _panel():
    w = wk_t.WorkshopWidget()
    w.resize(1400, 900)
    w.show()
    _app.processEvents()
    return w


def test_nothing_is_cut_off_as_built():
    w = _panel()
    panel, scroll = w._panel, w._panel_scroll
    assert panel.width() >= panel.sizeHint().width(), (
        panel.width(), panel.sizeHint().width())
    assert scroll.minimumWidth() >= panel.sizeHint().width()
    w.close()


def _stretch_a_label(w):
    """Make one button's words far longer than they were when the panel was built.

    That is what a display at 125 % does to every label at once, and it is the
    thing the old, measured-once width could not survive."""
    btn = w._btn_slider                 # "Show in Image Slider"
    btn.setText("Show in Image Slider, the long way round about it")
    _app.processEvents()
    return btn


def test_a_longer_label_widens_the_panel():
    w = _panel()
    was = w._panel_scroll.minimumWidth()
    btn = _stretch_a_label(w)
    w._fit_panel_width()
    _app.processEvents()
    now = w._panel_scroll.minimumWidth()
    assert now > was, f"the panel stayed at {was} px with a far longer label"
    assert now >= w._panel.sizeHint().width()
    assert btn.width() >= btn.sizeHint().width(), "the label is still cut short"
    w.close()


def test_it_never_narrows_again():
    w = _panel()
    btn = _stretch_a_label(w)
    w._fit_panel_width()
    wide = w._panel_scroll.minimumWidth()
    #  Back to the short label: the picture area must not be yanked sideways.
    btn.setText("Show in Image Slider")
    _app.processEvents()
    w._fit_panel_width()
    assert w._panel_scroll.minimumWidth() == wide
    w.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all ok")
