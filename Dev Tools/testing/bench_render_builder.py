"""Render the Builder tab to a PNG, without taking the keyboard focus.

Run from the Dev Tools folder:  python testing/bench_render_builder.py <out.png>

Fake projects with names of different lengths, three of them ticked out of
list order, so the build-order numbers left of the tick boxes can be checked
by eye: one column, nothing in the rows shifted. The window is shown with
SW_SHOWNOACTIVATE and captured with PrintWindow.
"""
import ctypes
import ctypes.wintypes as wt
import json
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import b_t  # noqa: E402
import cm_t  # noqa: E402
from PIL import Image  # noqa: E402

TMP = Path(tempfile.mkdtemp())
NAMES = ("Announcer", "CSS Logger", "Image Tools", "Screenshots",
         "Time Converter", "Git Work")


def sandbox():
    src, dist = TMP / "src", TMP / "dist"
    for name in NAMES:
        (src / name).mkdir(parents=True)
        (src / name / "main.py").write_text("print('hi')\n", encoding="utf-8")
    dist.mkdir()
    cfg = TMP / "config.json"
    cfg.write_text(json.dumps({"root_folder": str(src), "dist_root": str(dist)}),
                   encoding="utf-8")
    b_t._CONFIG_PATH = cfg
    cm_t._CONFIG_PATH = cfg
    b_t.SETTINGS_PATH = TMP / "builder_settings.json"
    b_t.USAGE_LOG = TMP / "build_usage.json"
    b_t.invalidate_versions_cache()


def grab(win, out):
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = u.GetAncestor(win.winfo_id(), 2)
    rect = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
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


def main(out: str):
    sandbox()
    root = tk.Tk()
    root.withdraw()
    ui = b_t.BuilderUI(root)
    ui.pack(fill="both", expand=True)
    for n in ("Screenshots", "Announcer", "Time Converter"):
        ui.project_checks[n].set(True)
        ui._on_project_check_clicked(next(p for p in ui.projects_sorted if p.name == n))

    root.geometry("1300x700+40+40")
    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)
    ctypes.windll.user32.ShowWindow(hwnd, 4)    # SW_SHOWNOACTIVATE
    for _ in range(20):
        root.update()
    root.after(300, root.quit)
    root.mainloop()
    grab(root, out)
    root.destroy()
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(TMP / "builder.png"))
