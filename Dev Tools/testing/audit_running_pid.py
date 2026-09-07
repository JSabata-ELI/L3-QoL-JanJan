"""Measure the identity of a program that is ALREADY running, by process id.

Same measurements as audit_exe_identity.py (window text, icon slots, taskbar
button name + a crop of its pixels), but it attaches to a live process and
never closes it -- for a program the user has open, or one whose exe lives
somewhere else than the share.

Run:  python audit_running_pid.py <pid> <outdir> [tag]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
from pathlib import Path

import win32gui

sys.path.insert(0, str(Path(__file__).parent))
from audit_exe_identity import (crop_button, icon_to_png,  # noqa: E402
                                taskbar_buttons, window_icons)

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

pid = int(sys.argv[1])
outdir = Path(sys.argv[2])
outdir.mkdir(parents=True, exist_ok=True)
tag = sys.argv[3] if len(sys.argv) > 3 else f"pid{pid}"

wins = []


def cb(hwnd, _):
    if win32gui.GetParent(hwnd):
        return True
    got = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
    if got.value != pid:
        return True
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    wins.append((hwnd, win32gui.GetWindowText(hwnd),
                 (r - l) * (b - t), bool(win32gui.IsWindowVisible(hwnd)),
                 bool(user32.IsIconic(hwnd))))
    return True


win32gui.EnumWindows(cb, None)
wins.sort(key=lambda w: -w[2])
rec = {"pid": pid, "windows": [{"title": w[1], "area": w[2], "visible": w[3],
                                "minimized": w[4]} for w in wins]}

if wins:
    hwnd, title = wins[0][0], wins[0][1]
    rec["window_title"] = title
    icons = window_icons(hwnd)
    rec["icon_handles"] = {k: hex(v) for k, v in icons.items()}
    src = icons.get("big") or icons.get("small") or icons.get("class")
    if icon_to_png(src, outdir / f"{tag}_window.png"):
        rec["png_window_icon"] = f"{tag}_window.png"

    for name, r, tray in taskbar_buttons():
        base = title.split("  ")[0].strip()
        if name and base and base.lower() in name.lower():
            rec["taskbar_button_name"] = name
            if crop_button(tray, r, outdir / f"{tag}_taskbar.png"):
                rec["png_taskbar"] = f"{tag}_taskbar.png"
            break

print(json.dumps(rec, indent=2, ensure_ascii=False))
