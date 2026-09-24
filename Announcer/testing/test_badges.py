"""The floating panel says the same rows as the Watch tab, and no others.

Run:  python testing/test_badges.py

Photographed by the operator on 2026-09-18: the preset "Operations" holds two
alarms, the Watch tab listed those two — and the panel over his work showed
five badges plus "and 2 more not in range", all of them from alarms belonging
to other presets and reading "not read yet". They were never read, because the
engine only reads the chosen preset; the panel was simply walking the whole
list. The lamp in the corner had the same fault.

No window is put on the screen: the real window is built with
`WA_DontShowOnScreen`, the same way `test_multi_select.py` does it.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import Qt                                  # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_core as C                                           # noqa: E402

# The operator's own settings must never be in reach of a test.
_tmp = Path(tempfile.mkdtemp(prefix="announcer_badges_"))
C.config_path = lambda: _tmp / "presets.json"

from main import AnnouncerWindow                               # noqa: E402

_win = AnnouncerWindow()
_win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
_win.show()
_app.processEvents()


def _two_presets():
    """Two alarms in "Operations", four more that belong to another preset."""
    items = _win.items()
    items[:] = []
    for i in range(2):
        it = C.new_value_item(i + 1, f"in {i + 1}", pv=f"PV:{i + 1}")
        it["presets"] = ["Operations"]
        it["on"] = True
        items.append(it)
    for i in range(4):
        it = C.new_value_item(10 + i, f"out {i + 1}", pv=f"PV:X{i + 1}")
        it["presets"] = ["Night"]
        it["on"] = True
        items.append(it)
    _win._preset = "Operations"
    _win.refresh_tabs()
    _app.processEvents()
    return items


def test_only_the_chosen_preset_gets_a_badge():
    _two_presets()
    said = [text for text, _state in _win._badges()]
    assert len(said) <= 2, f"the panel listed rows it does not watch: {said}"
    for text in said:
        assert text.startswith("in "), f'"{text}" is not in the preset'


def test_the_panel_says_as_much_as_the_watch_tab_lists():
    _two_presets()
    listed = len(_win.active_items())
    assert len(_win._badges()) <= listed, \
        "more badges than there are rows in the Watch tab"


def test_nothing_in_the_preset_means_no_badges():
    items = _two_presets()
    for it in items:
        it["presets"] = ["Night"]
    _win.refresh_tabs()
    _app.processEvents()
    assert _win._badges() == [], \
        "an empty preset still put sentences over his work"


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:                               # noqa: BLE001
            bad += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
