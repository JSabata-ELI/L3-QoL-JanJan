"""Photograph the three condition editors, including the value editor with the
attached screen area switched on.

Writes announcer_editor_<kind>.png next to this file.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PIL import ImageGrab                      # noqa: E402
import a                                        # noqa: E402


def shoot(win, tag):
    win.update()
    win.update_idletasks()
    time.sleep(0.4)
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x - 10, y - 40, x + w + 10, y + h + 10),
                         all_screens=True)
    path = HERE / f"announcer_editor_{tag}.png"
    img.save(path)
    print(f"{tag}: {img.size[0]}x{img.size[1]} -> {path.name}")


def main():
    app = a.ScreenTracker()
    # The saved geometry can put the window on a monitor that is asleep, and a
    # grab of that one comes back black.
    app.update()
    app._apply_geometry(app, "820x620+80+60")
    app.update()

    for kind in ("screen", "pv", "hall"):
        app._edit_condition(new=kind)
        win = app._cond_editor
        app.update()
        if kind == "pv":
            shoot(win, "value_plain")
            # Switch the attached screen area on: it is the half that is new.
            for child in win.winfo_children()[0].winfo_children():
                if child.winfo_class() == "TFrame":
                    for box in child.winfo_children():
                        if box.winfo_class() == "TCheckbutton":
                            box.invoke()
            app.update()
            shoot(win, "value_with_area")
        else:
            shoot(win, kind)
        win.destroy()
        app.update()

    app.destroy()
    print("done")


if __name__ == "__main__":
    main()
