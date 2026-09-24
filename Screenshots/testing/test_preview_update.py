"""The live preview window: tiles appear one by one, cycles are swapped inside
the SAME window, and stepping with the arrows stops the automatic following.

Needs a desktop session (it really opens a window). No clicking is simulated —
the same methods the buttons call are called directly.

Run:  python testing/test_preview_update.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk  # noqa: E402
import s  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def make_png(path: Path, shade: int):
    from PIL import Image
    Image.new("L", (64, 48), shade).save(path, "PNG")


def cells(win) -> int:
    return len(win._inner.winfo_children())


def main():
    tmp = Path(tempfile.mkdtemp(prefix="prevtest_"))
    base = 1_757_000_000_000_000_000
    files = []
    for i in range(5):
        p = tmp / f"PAM{i}_NF__frame_{base + i * 1_000_000_000}.png"
        make_png(p, 40 + i * 40)
        files.append(p)

    root = tk.Tk()
    root.withdraw()
    win = s.PreviewWindow(root, [], cycle_mode=True)
    win.update()

    print("live append")
    hist = [{"cycle": 1, "ts": "13:49:15", "files": []}]
    win.set_cycle_history(hist)
    win.show_cycle(1)
    check("an empty cycle draws no tiles", cells(win) == 0)
    for p in files[:3]:
        hist[0]["files"].append(p)
        win.append_path(p)
        win.update()
    check("each copied file adds one tile", cells(win) == 3)
    check("the window still shows cycle 1", win.shown_cycle == 1)

    print("cycle switching in place")
    hist.append({"cycle": 2, "ts": "13:49:20", "files": list(files[3:])})
    win.set_cycle_history(hist)
    same = win
    win.show_cycle(2)
    win.update()
    check("switching keeps the same window", win is same and win.winfo_exists())
    check("the grid now holds the other cycle's files", cells(win) == 2)
    check("the drop-down names the shown cycle", "Cycle 2" in win._cycle_var.get())

    print("following")
    check("following is on to start with", win.following)
    win._step_cycle(-1)
    win.update()
    check("an arrow step goes back a cycle", win.shown_cycle == 1)
    check("an arrow step stops the following", not win.following)
    check("cycle 1 is drawn again", cells(win) == 3)
    win._follow_var.set(True)
    win._on_follow_toggle()
    win.update()
    check("switching following back on jumps to the newest cycle",
          win.shown_cycle == 2)

    print("no leftover global bindings")
    check("the wheel is bound on the window, not application-wide",
          bool(win.bind("<MouseWheel>")) and not root.bind_all("<MouseWheel>"))

    win.destroy()
    root.destroy()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
