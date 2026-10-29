"""Launch the real program and look at its icon: title bar and taskbar button.

The window opens WITHOUT taking the keyboard focus (SW_SHOWNOACTIVATE plus
the program's SSQ_NO_ACTIVATE hook), is photographed and closed again. The
pictures go to testing/audit_*.png beside the icon.ico rendering, using Dev
Tools' audit helpers (UI Automation finds the taskbar button, PrintWindow
draws it even when the taskbar is auto-hidden).

Run:  python testing/audit_icon_launch.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM = HERE.parent
sys.path.insert(0, str(PROGRAM.parent / "Dev Tools" / "testing"))
import audit_exe_identity as A   # noqa: E402

import win32con   # noqa: E402
import win32gui   # noqa: E402

user32 = ctypes.windll.user32


def windows_of_pid(pid):
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetParent(hwnd):
            return True
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            found.append((hwnd, win32gui.GetWindowText(hwnd)))
        return True
    win32gui.EnumWindows(cb, None)
    return found


si = subprocess.STARTUPINFO()
si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
si.wShowWindow = 4   # SW_SHOWNOACTIVATE
env = dict(os.environ, SSQ_NO_ACTIVATE="1")
proc = subprocess.Popen([sys.executable, str(PROGRAM / "main.py")],
                        cwd=str(PROGRAM), env=env, startupinfo=si,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ok = True
try:
    wins = []
    t_end = time.time() + 25
    while time.time() < t_end and not wins:
        wins = [w for w in windows_of_pid(proc.pid) if w[1].startswith("Screenshots")]
        time.sleep(0.4)
    if not wins:
        print("FAIL no window")
        ok = False
    else:
        time.sleep(3)
        hwnd, title = wins[0]
        print("title:", title)
        if user32.GetForegroundWindow() == hwnd:
            print("FAIL the window took the foreground (keyboard focus)")
            ok = False
        icons = A.window_icons(hwnd)
        print("icon handles:", {k: hex(v) for k, v in icons.items()})
        A.icon_to_png(icons.get("big") or icons.get("small"), HERE / "audit_window_icon.png")
        A.ico_file_to_png(PROGRAM / "icon.ico", HERE / "audit_icon_file.png")
        if not (icons.get("big") or icons.get("small")):
            print("FAIL the window has no icon")
            ok = False
        hit = None
        buttons = []
        for _ in range(6):   # the button may still be appearing
            try:
                buttons = A.taskbar_buttons()
            except Exception as e:   # UI Automation answers COM errors now and then
                print("taskbar lookup failed:", e)
                time.sleep(1)
                continue
            # Run from source the button is named after the process image
            # ("Python - 1 running window"), not after the window title.
            hit = next(((n, r, t) for n, r, t in buttons
                        if n and (n.lower().startswith("screenshots")
                                  or n.lower().startswith("python - "))), None)
            if hit:
                break
            time.sleep(1)
        if not hit:
            print("taskbar buttons seen:", [n.encode("ascii", "replace").decode()
                                            for n, _r, _t in buttons])
        if hit:
            print("taskbar button:", hit[0])
            A.crop_button(hit[2], hit[1], HERE / "audit_taskbar.png")
        else:
            print("FAIL no taskbar button found")
            ok = False
finally:
    for hwnd, _t in windows_of_pid(proc.pid):
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
    try:
        proc.wait(8)
    except Exception:
        proc.kill()
print("all passed" if ok else "FAILED")
sys.exit(0 if ok else 1)
