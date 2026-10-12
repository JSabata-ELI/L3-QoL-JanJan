"""Render the Launcher window to a PNG, without taking the keyboard focus.

Run from the Launcher folder:  python testing/bench_render.py <out.png>

Fake programs, one flagged as updated and one misplaced file, so the amber
program button and the amber Clean button can be checked by eye. The window
is shown with SW_SHOWNOACTIVATE and captured with PrintWindow, so it is read
even when other windows cover it.
"""
import ctypes
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import l  # noqa: E402
from PIL import Image  # noqa: E402

TMP = Path(tempfile.mkdtemp())
l.CONFIG_PATH = TMP / "config.json"
l.Launcher._auto_select_root = lambda self: None
l.Launcher._schedule_version_poll = lambda self: None

_tk_init = l.tk.Tk.__init__


def _hidden_init(self, *a, **kw):
    _tk_init(self, *a, **kw)
    self.withdraw()


l.tk.Tk.__init__ = _hidden_init


def fake_programs() -> dict:
    icon = Path(l.__file__).resolve().parent / "icon.ico"
    progs = {}
    for name in ("Image Tools", "Screenshots", "CSS Logger", "Announcer",
                 "Time Converter"):
        d = TMP / name
        d.mkdir(parents=True)
        exe = d / f"{name} v1.0.0.exe"
        exe.write_text("x")
        progs[name] = {
            "exe_path": exe, "readme_path": exe, "readme_full_path": exe,
            "label": name, "program_dir": d, "icon_path": icon,
            "archive_versions": [{"exe_path": exe, "py_path": None,
                                  "has_internal": False, "label": "v1.0.0"}],
        }
    return progs


def main(out: str):
    app = l.Launcher()
    progs = fake_programs()
    app._apply_programs(progs, "Test", TMP, app._scan_gen,
                        tidy=([(TMP / "x v1.0.0.exe", None)] * 2, []))
    app._update_available.add("Screenshots")
    app._set_update_style("Screenshots")

    app.geometry("+40+40")
    app.update_idletasks()
    u = ctypes.windll.user32
    hwnd = u.GetAncestor(app.winfo_id(), 2)
    u.ShowWindow(hwnd, 4)                       # SW_SHOWNOACTIVATE
    for _ in range(20):
        app.update()
    app.after(300, app.quit)
    app.mainloop()

    import ctypes.wintypes as wt
    rect = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    g = ctypes.windll.gdi32
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)                 # PW_RENDERFULLCONTENT
    buf = ctypes.create_string_buffer(w * h * 4)
    bmi = (ctypes.c_uint32 * 10)(40, w, -h, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    g.GetDIBits(mdc, bmp, 0, h, buf, bmi, 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    app.destroy()
    print("saved", out, w, h)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(TMP / "launcher.png"))
