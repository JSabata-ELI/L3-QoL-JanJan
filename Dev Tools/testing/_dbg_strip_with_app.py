"""Launch one program, wait, then photograph the WHOLE primary taskbar strip.

Seeing the new button next to every other app's button is the decisive test:
if the neighbours all draw their icons and only ours draws the blank window
placeholder, the placeholder is real and not an artefact of the capture.

Run:  python _dbg_strip_with_app.py "<exe path>" <outdir> <seconds>
"""
import subprocess
import sys
import time
from pathlib import Path

import win32gui

sys.path.insert(0, str(Path(__file__).parent))
from audit_exe_identity import print_window  # noqa: E402

exe = Path(sys.argv[1])
out = Path(sys.argv[2])
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
out.mkdir(parents=True, exist_ok=True)

cmd = ([sys.executable, str(exe)] if exe.suffix.lower() == ".py"
       else [str(exe)])
proc = subprocess.Popen(cmd, cwd=str(exe.parent))
print("launched", proc.pid, exe.name)
try:
    time.sleep(wait)

    titles = []

    def title_cb(hwnd, _):
        import ctypes
        import ctypes.wintypes as wt
        got = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value == proc.pid and win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t:
                titles.append(t)
        return True

    win32gui.EnumWindows(title_cb, None)
    print("window titles:", titles)

    trays = []

    def cb(hwnd, _):
        if win32gui.GetClassName(hwnd) == "Shell_TrayWnd":
            trays.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    for hwnd in trays:
        got = print_window(hwnd)
        if got:
            img, _ = got
            path = out / f"strip_{exe.stem.replace(' ', '_')}.png"
            img.crop((0, 0, min(1250, img.width), img.height)).save(path)
            print("saved", path)
finally:
    subprocess.run(["taskkill", "/f", "/t", "/pid", str(proc.pid)],
                   capture_output=True)
    print("closed")
