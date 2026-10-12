"""Laser settings — both ways of photographing a window, on a real one.

    python testing/test_dm_live_grab.py ["text in a window title"]

Takes a window already open on this PC (default: one with "Visual Studio Code"
in its title), photographs it the way the DM writer does — PrintWindow first,
then its rectangle copied off the screen — and says what each picture holds.
Nothing is opened, activated or clicked. With the Remote Desktop window to the
mirror open, pass "192.168.1.107" and the grid should be found one way or the
other. The pictures go to the temp folder as live_grab_*.png — they show the
whole window, so they are kept out of the project.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ls_dm as D                                               # noqa: E402
import ls_screen as S                                           # noqa: E402


def _any_window(needle):
    """A visible, not minimised window whose title contains `needle`, of any
    class — `find_rdp_window` skips browsers and editors on purpose."""
    import ctypes
    api = S._win32()
    user32 = api["user32"]
    found = []

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if needle.lower() in buf.value.lower():
            found.append(hwnd)
        return True

    user32.EnumWindows(api["enum_proc"](cb), 0)
    return found[0] if found else None


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Visual Studio Code"
    hwnd, minimised = D.find_rdp_window(title)
    if not hwnd:
        hwnd, minimised = _any_window(title), False
    if not hwnd:
        print(f"no window with \"{title}\" in its title")
        return 1
    print(f"window {hwnd:#x}, minimised: {minimised}")
    for how, grab in ((D.HOW_WINDOW, S.grab_window),
                      (D.HOW_SCREEN, S.grab_screen_of)):
        t0 = time.perf_counter()
        pic = grab(hwnd)
        took = (time.perf_counter() - t0) * 1000
        read = D.read_picture(pic, None, time.time())
        print(f"{how}: {pic.width} x {pic.height} in {took:.0f} ms — "
              f"{read.status}: {read.problem or 'grid read'}")
        out = Path(tempfile.gettempdir()) / f"live_grab_{how}.png"
        out.write_bytes(D.png_bytes(pic))
        print(f"        wrote {out}")
    read = D.read_window(title)
    print(f"read_window: {read.status}, how={read.how}, "
          f"{read.took_ms:.0f} ms — {read.problem or 'grid read'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
