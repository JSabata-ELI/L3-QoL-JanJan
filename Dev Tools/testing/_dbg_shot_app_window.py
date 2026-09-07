"""Launch a program and photograph its own window (not the taskbar).

Run:  python _dbg_shot_app_window.py "<exe or .py>" <outdir> <seconds>
"""
import subprocess
import sys
import time
from pathlib import Path

import win32gui

sys.path.insert(0, str(Path(__file__).parent))
from audit_exe_identity import print_window  # noqa: E402

target = Path(sys.argv[1])
out = Path(sys.argv[2])
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
out.mkdir(parents=True, exist_ok=True)

cmd = ([sys.executable, str(target)] if target.suffix.lower() == ".py"
       else [str(target)])
proc = subprocess.Popen(cmd, cwd=str(target.parent))
print("launched", proc.pid)
try:
    time.sleep(wait)
    import ctypes
    import ctypes.wintypes as wt
    found = []

    def cb(hwnd, _):
        got = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value == proc.pid and win32gui.IsWindowVisible(hwnd):
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            found.append((hwnd, win32gui.GetWindowText(hwnd),
                          (r - l) * (b - t)))
        return True

    win32gui.EnumWindows(cb, None)
    found.sort(key=lambda w: -w[2])
    for hwnd, title, _ in found[:2]:
        got = print_window(hwnd)
        if got:
            img, _ = got
            safe = "".join(c if c.isalnum() else "_" for c in title) or "win"
            path = out / f"win_{safe}.png"
            img.save(path)
            print("saved", path, img.size)
finally:
    subprocess.run(["taskkill", "/f", "/t", "/pid", str(proc.pid)],
                   capture_output=True)
    print("closed")
