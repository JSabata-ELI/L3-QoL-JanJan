r"""The mouse wheel changes a drop-down only after it was clicked or tabbed to.

Tk delivers a wheel notch to the widget under the pointer, and ttk's own class
binding then steps the value -- a read-only drop-down too, firing
<<ComboboxSelected>>. install_wheel_guard() replaces the class binding for
TCombobox and TSpinbox: a box without the keyboard focus lets the notch pass
and keeps its value; the focused box steps and stops the notch there.

The Tk root is withdrawn at once, so no window is ever shown and nothing takes
the keyboard focus. The notch is a Tk-internal `event generate` on the widget,
never real input on the desktop; "has the focus" is stood in for by replacing
the root's focus_get(), because a withdrawn window cannot own the focus.

Run: python -m pytest testing/test_wheel_guard.py  (from the Chiller Log folder)
"""
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cpt


@pytest.fixture(scope="module")
def root():
    # One Tk per process: a second tk.Tk() after destroy() fails to load ttk.
    r = tk.Tk()
    r.withdraw()
    cpt.install_wheel_guard(r)
    yield r
    r.destroy()


@pytest.fixture
def combo(root):
    cb = ttk.Combobox(root, values=["Line", "Bar", "Scatter"], state="readonly")
    cb.set("Bar")
    picked = []
    cb.bind("<<ComboboxSelected>>", lambda _e: picked.append(cb.get()))
    yield cb, picked
    root.__dict__.pop("focus_get", None)
    cb.destroy()


def _notch(widget, delta):
    widget.event_generate("<MouseWheel>", delta=delta, when="now")
    widget.update()    # <<ComboboxSelected>> is queued; the root stays withdrawn


def test_class_bindings_are_guarded(root):
    for cls in ("TCombobox", "TSpinbox"):
        script = root.bind_class(cls, "<MouseWheel>")
        assert script, cls
        assert "ttk::" not in script, f"{cls} still runs Tk's own wheel script"


def test_installing_twice_keeps_one_guard(root):
    before = root.bind_class("TCombobox", "<MouseWheel>")
    cpt.install_wheel_guard(root)
    assert root.bind_class("TCombobox", "<MouseWheel>") == before


def test_unfocused_combo_keeps_its_value(root, combo):
    cb, picked = combo
    root.focus_get = lambda: None
    for delta in (120, -120, 240, -360):
        _notch(cb, delta)
    assert cb.get() == "Bar"
    assert picked == []


def test_unfocused_combo_lets_the_notch_pass(root, combo):
    cb, _picked = combo
    root.focus_get = lambda: None
    seen = []
    root.bind_class("Guard-probe", "<MouseWheel>", lambda _e: seen.append(1))
    cb.bindtags(cb.bindtags() + ("Guard-probe",))
    _notch(cb, -120)
    assert seen == [1], "the guard stopped the notch for a box that was not clicked"


def test_focused_combo_steps_and_stops_the_notch(root, combo):
    cb, picked = combo
    root.focus_get = lambda: cb
    seen = []
    root.bind_class("Guard-probe", "<MouseWheel>", lambda _e: seen.append(1))
    cb.bindtags(cb.bindtags() + ("Guard-probe",))
    _notch(cb, -120)                      # wheel towards the user: next item
    assert cb.get() == "Scatter"
    assert picked == ["Scatter"]
    assert seen == [], "the notch went on to the window after the box used it"


def test_focused_spinbox_steps(root):
    sb = ttk.Spinbox(root, from_=0, to=10)
    sb.set(5)
    try:
        root.focus_get = lambda: None
        _notch(sb, 120)
        assert sb.get() == "5"
        root.focus_get = lambda: sb
        _notch(sb, 120)
        assert sb.get() != "5"
    finally:
        root.__dict__.pop("focus_get", None)
        sb.destroy()
