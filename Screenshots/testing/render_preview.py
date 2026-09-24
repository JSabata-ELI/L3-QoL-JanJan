"""Render the live preview window and save a picture of it, so the cycle bar
can be checked for legibility instead of guessed at.

Run:  python testing/render_preview.py
Writes testing/preview_cycle_bar.png
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk  # noqa: E402
import s  # noqa: E402

OUT = Path(__file__).resolve().parent / "preview_cycle_bar.png"


def make_png(path: Path, shade: int, colour: bool = False):
    from PIL import Image, ImageDraw
    if colour:
        img = Image.new("RGB", (320, 240), (shade, shade // 3, 20))
        d = ImageDraw.Draw(img)
        d.ellipse((110, 70, 210, 170), fill=(min(255, shade + 120), 200, 40))
    else:
        img = Image.new("L", (320, 240), shade)
        d = ImageDraw.Draw(img)
        d.ellipse((110, 70, 210, 170), fill=min(255, shade + 150))
    img.save(path, "PNG")


def main():
    s._set_dpi_awareness()
    tmp = Path(tempfile.mkdtemp(prefix="prevrender_"))
    base = 1_757_000_000_000_000_000
    cams = ["PAP1_NF", "PAP1_DF", "PAM5_NF", "PAM10_NF", "PAM9_NF", "PAM12_NF"]
    files = []
    for i, cam in enumerate(cams):
        p = tmp / f"{cam}__frame_{base + i * 1_000_000_000}.png"
        make_png(p, 30 + i * 30, colour=(i % 2 == 1))
        files.append(p)

    root = tk.Tk()
    root.withdraw()
    win = s.PreviewWindow(root, [], cycle_mode=True)
    hist = [
        {"cycle": 1, "ts": "13:49:15", "files": files},
        {"cycle": 2, "ts": "13:49:20", "files": files[:3]},
    ]
    win.set_cycle_history(hist)
    win.show_cycle(1)
    for i, p in enumerate(files):
        win.set_caption_extra(p, f"(−0.{i}s)")
    win.show_cycle(1)
    win.set_cycle_status("cycle 2 running…  spread 0.0s–1.8s")
    win._auto.set(True)          # Auto contrast on: the colour tiles must stay in colour
    win._gamma.set(1.6)
    win._redraw()
    win.attributes("-topmost", True)
    win.update_idletasks()
    win.update()
    win.after(600, root.quit)
    root.mainloop()

    from PIL import ImageGrab
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(OUT, "PNG")
    print(f"saved {OUT}  ({w}x{h})")
    win.destroy()
    root.destroy()


if __name__ == "__main__":
    main()
