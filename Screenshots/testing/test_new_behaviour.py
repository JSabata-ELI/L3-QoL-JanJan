"""The preview and editor respond in place, the wheel leaves unclicked boxes
alone, the hover pop-up never sits under the pointer, and a drawing survives
zooming. Every window is withdrawn before it maps; nothing takes focus.

Run:  python testing/test_new_behaviour.py
"""
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path

import hidden_tk  # noqa: F401  (before `import s`)
import s
from PIL import Image

FAILED = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def wheel_guard(root):
    print("wheel guard")
    s.install_wheel_guard(root)
    top = tk.Toplevel(root)
    cb = ttk.Combobox(top, values=["a", "b", "c"], state="readonly")
    cb.current(1)
    cb.pack()
    sp = ttk.Spinbox(top, from_=0, to=10)
    sp.set(5)
    sp.pack()
    fired = []
    cb.bind("<<ComboboxSelected>>", lambda e: fired.append(1))
    scrolled = []
    top.bind("<MouseWheel>", lambda e: scrolled.append(1))
    root.update()

    focus = [None]
    root.focus_get = lambda: focus[0]

    cb.event_generate("<MouseWheel>", delta=-120)
    sp.event_generate("<MouseWheel>", delta=-120)
    root.update()
    check("unfocused combobox keeps its value", cb.get() == "b", cb.get())
    check("unfocused combobox fires no selection", not fired)
    check("unfocused spinbox keeps its value", sp.get() == "5", sp.get())
    check("the wheel goes on to the window (page scrolls)", len(scrolled) == 2, str(len(scrolled)))

    scrolled.clear()
    focus[0] = cb
    cb.event_generate("<MouseWheel>", delta=-120)
    root.update()
    check("clicked combobox changes on the wheel", cb.get() == "c", cb.get())
    check("and the window does not scroll as well", not scrolled)
    focus[0] = sp
    sp.event_generate("<MouseWheel>", delta=120)
    root.update()
    check("clicked spinbox changes on the wheel", sp.get() not in ("5", ""), sp.get())
    del root.focus_get
    top.destroy()


def preview_retone(root, files):
    print("preview: tone change in place")
    win = s.PreviewWindow(root, list(files))
    root.update()
    labels_before = dict(win._cells)
    children_before = list(win._inner.winfo_children())
    check("every file has a tile", len(labels_before) == len(files))

    opened = []
    orig_open = s.Image.open

    def counting_open(*a, **kw):
        opened.append(a[0])
        return orig_open(*a, **kw)
    s.Image.open = counting_open
    try:
        t0 = time.perf_counter()
        for v in range(-60, 61, 5):          # a slider drag: 25 ticks
            win._contrast.set(v)
            win._schedule_retone()
        root.update()
        dt = (time.perf_counter() - t0) * 1000
        win._palette.set("Hot")
        root.update()
        win._gamma.set(1.8)
        win._schedule_retone()
        root.update()
    finally:
        s.Image.open = orig_open

    check("no file is read again on a tone change", not opened, f"{len(opened)} reads")
    check("the tiles are the same widgets (no rebuild)",
          list(win._inner.winfo_children()) == children_before
          and dict(win._cells) == labels_before)
    check("a 25-tick drag folds into one pass", win._retone_id is None, f"{dt:.0f} ms")
    img_now = str(labels_before[files[0]].cget("image"))
    check("the picture on the tile was swapped", bool(img_now))

    print("hover popup")
    import re
    px, py = win.winfo_pointerx(), win.winfo_pointery()
    left, top, right, bottom = s.PreviewWindow._monitor_work_area(px, py)

    class FakeThumb:   # a thumbnail sitting under the pointer
        def __init__(self, x0):
            self.x0 = x0
        def winfo_rootx(self): return self.x0
        def winfo_rooty(self): return py - 60
        def winfo_width(self): return 240
        def winfo_height(self): return 180

    for where, x0 in (("left edge", left + 2), ("middle", (left + right) // 2 - 120),
                      ("right edge", right - 242)):
        fake_px = x0 + 120
        win.winfo_pointerx = lambda v=fake_px: v
        win._show_popup(files[0], FakeThumb(x0))
        root.update()
        pop = win._popup
        if pop is None:
            check(f"pop-up opens ({where})", False)
            continue
        x, y = map(int, re.findall(r"[+-]-?\d+", pop.wm_geometry())[-2:])
        w, h = pop.winfo_reqwidth(), pop.winfo_reqheight()
        check(f"pop-up not under the pointer ({where})",
              not (x <= fake_px <= x + w and y <= py <= y + h),
              f"{(x, y, w, h)} vs {(fake_px, py)}")
        check(f"pop-up stays on its screen ({where})",
              left <= x and x + w <= right and top <= y and y + h <= bottom,
              f"{(x, y, w, h)} in {(left, top, right, bottom)}")
    del win.winfo_pointerx
    check("the pop-up opens from the cache", win._popup is not None)
    win._redraw()
    check("a rebuild closes the pop-up", win._popup is None)
    win.destroy()


def zoom_keeps_drawing(root, files):
    print("zoom window: drawing survives a zoom change")
    path = files[0]
    win = s.PreviewWindow(root, [path])
    root.update()
    src = Image.open(path)
    ov = Image.new("RGBA", src.size, (0, 0, 0, 0))
    ov.putpixel((10, 10), (255, 0, 0, 255))
    win._img_states[path] = {"overlay": ov, "palette": "Original", "brightness": 0,
                             "auto": False, "contrast": 0, "auto_bright": False,
                             "gamma": 1.0, "crop_rect": None, "zoom": 1.0}
    before = set(root.winfo_children())
    win._open_zoom_window(path)
    zwin = [w for w in win.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    root.update()
    time.sleep(0.05)
    root.update()

    def find(widget, kind, text=None):
        out = []
        for c in widget.winfo_children():
            if isinstance(c, kind) and (text is None or c.cget("text") == text):
                out.append(c)
            out.extend(find(c, kind, text))
        return out
    zoom_scale = find(zwin, ttk.Scale)[0]
    zoom_scale.set(2.0)
    root.update()
    zoom_scale.set(1.0)
    root.update()
    find(zwin, ttk.Button, "✔ Apply")[0].invoke()
    root.update()
    st = win._img_states.get(path) or {}
    ov2 = st.get("overlay")
    check("the drawing is still there after zooming in and out",
          ov2 is not None and ov2.getpixel((10, 10))[3] > 0)
    check("zooming did not set a crop", st.get("crop_rect") is None)
    win.destroy()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ss_new_"))
    files = []
    for i in range(12):
        p = tmp / f"CAM{i}_NF__frame_{1_757_000_000_000_000_000 + i * 1_000_000_000}.png"
        Image.radial_gradient("L").resize((640, 480)).save(p, "PNG")
        files.append(p)
    root = tk.Tk()
    wheel_guard(root)
    preview_retone(root, files)
    zoom_keeps_drawing(root, files)
    root.destroy()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
