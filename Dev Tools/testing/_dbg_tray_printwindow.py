"""Capture the taskbar window itself (it is auto-hidden on this desk, so a
screen BitBlt of its rectangle only gets wallpaper)."""
import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

import win32con
import win32gui
import win32ui
from PIL import Image

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def shot_window(hwnd, path, flags):
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    print(win32gui.GetClassName(hwnd), (l, t, r, b), "flags", flags)
    if w <= 0 or h <= 0:
        return None
    dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    ok = user32.PrintWindow(wt.HWND(hwnd), mem.GetSafeHdc(), flags)
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    img.save(path)
    win32gui.ReleaseDC(hwnd, dc)
    print("   PrintWindow ->", ok, img.size)
    return (l, t)


def cb(hwnd, _):
    cls = win32gui.GetClassName(hwnd)
    if cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        for flags in (0, 2):
            shot_window(hwnd, out / f"tray_{cls}_{hwnd}_f{flags}.png", flags)
    return True


win32gui.EnumWindows(cb, None)
