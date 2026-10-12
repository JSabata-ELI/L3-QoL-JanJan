"""Import first in a test: every Tk window is withdrawn before it maps, so a
test never shows a window or takes the keyboard focus.

    import hidden_tk  # noqa: F401  (before `import s`)

Also puts the program folder on sys.path, so `import s` works when the test
is run as `python testing/<name>.py` from the program folder.
"""
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_orig_tl_init = tk.Toplevel.__init__
_orig_tk_init = tk.Tk.__init__


def _tl_init(self, *a, **kw):
    _orig_tl_init(self, *a, **kw)
    self.withdraw()


def _tk_init(self, *a, **kw):
    _orig_tk_init(self, *a, **kw)
    self.withdraw()


tk.Toplevel.__init__ = _tl_init
tk.Tk.__init__ = _tk_init


def run_hidden(test_path: str) -> None:
    """Run another test script under the same patch (for the older tests that
    open real windows): python testing/hidden_tk.py testing/test_x.py"""
    import runpy
    test = Path(test_path).resolve()
    sys.argv = [str(test)]
    runpy.run_path(str(test), run_name="__main__")


if __name__ == "__main__":
    run_hidden(sys.argv[1])
