# dev_tools.py
# Thin connector — imports BuilderUI from b_t.py and DeployGUI from cm_t.py.

import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _icon_app_id(prefix, ico_path):
    """A taskbar identity that is new on every launch.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so every *stable* id tried here eventually picked up a bad cache entry
    and then drew the blank window placeholder for good: a fixed string, a hash
    of the icon, and a hash tagged with the build's file name each broke within
    days. Setting no id at all was no better -- Windows then keys the button on
    the exe path and caches the picture there instead (Diagnostic v1.3.1,
    measured 2026-09-17: the window icon, the exe's own icon resource and the
    shell's own file icon all correct, the taskbar button blank).

    An id Windows has never seen has no cache entry, so the button falls back
    to the window icon, which every program here sets itself -- measured on a
    fresh id on 2026-09-04 and again on 2026-09-17. A random suffix per launch
    makes every run a first-time id, which is why this is the one form that
    cannot go stale. Nothing here needs a stable identity: no program registers
    a shortcut, pins itself or sends Windows toasts. The one cost is pinning a
    *running* taskbar button -- that pin would carry this run's id and would
    not start the program again, so pin the exe instead.

    Returns None when there is no icon at all; the caller then sets no id and
    the button keeps taking the exe's own picture.
    """
    import os.path
    if not ico_path or not os.path.exists(str(ico_path)):
        return None
    import uuid
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def set_app_icon(win, ico_path: str, app_id: str | None = None) -> None:
    """Give the window (title bar) AND the Windows taskbar button our icon.

    tkinter's iconbitmap only fixes the title bar / WM_BIG icon; the Windows 11
    taskbar reads the *small* icon slots (ICON_SMALL/SMALL2) and the window-class
    icon (GCLP_HICONSM), which Tk otherwise leaves as its default feather logo.
    We force every slot from icon.ico via Win32 before the window is first shown.
    """
    import ctypes
    _aumid = _icon_app_id(app_id, ico_path) if app_id else None
    if _aumid:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass
    try:
        win.iconbitmap(default=ico_path)
    except Exception:
        pass
    try:
        u = ctypes.windll.user32
        hwnd = u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        big = u.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)  # LR_LOADFROMFILE|LR_DEFAULTSIZE
        sm  = u.LoadImageW(None, ico_path, 1, 16, 16, 0x10)       # LR_LOADFROMFILE
        for which, h in ((1, big), (0, sm), (2, sm)):            # ICON_BIG, ICON_SMALL, ICON_SMALL2
            if h:
                u.SendMessageW(hwnd, 0x0080, which, h)           # WM_SETICON
        set_cls = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
        if big:
            set_cls(hwnd, -14, big)   # GCLP_HICON
        if sm:
            set_cls(hwnd, -34, sm)    # GCLP_HICONSM
    except Exception:
        pass


def main():
    from b_t import BuilderUI
    from cm_t import DeployGUI

    root = tk.Tk()
    root.title("Dev Tools")
    set_app_icon(root, str(_app_dir() / "icon.ico"), "ELI.DevTools")
    root.geometry("1180x820")
    root.minsize(1060, 620)
    try:
        root.state("zoomed")          # start maximized
    except Exception:
        root.attributes("-zoomed", True)

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, side="top")

    # Sdílený log widget — fyzicky žije v CM tabu, Builder do něj píše přes log_widget
    cm_tab = DeployGUI(nb)

    def _on_build_done(built_projects, build_summary=None, build_info=None):
        nb.select(cm_tab)
        cm_tab.auto_deploy(built_projects, build_summary=build_summary, build_info=build_info)

    builder_tab = BuilderUI(nb, on_build_done=_on_build_done, log_widget=cm_tab.log)
    builder_tab._cm_ref = cm_tab
    cm_tab._builder_ref = builder_tab

    nb.add(builder_tab, text="  Builder  ")
    nb.add(cm_tab,      text="  Copy Manager  ")

    root.mainloop()


if __name__ == "__main__":
    main()