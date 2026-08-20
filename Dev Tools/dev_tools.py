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


def set_app_icon(win, ico_path: str, app_id: str | None = None) -> None:
    """Give the window (title bar) AND the Windows taskbar button our icon.

    tkinter's iconbitmap only fixes the title bar / WM_BIG icon; the Windows 11
    taskbar reads the *small* icon slots (ICON_SMALL/SMALL2) and the window-class
    icon (GCLP_HICONSM), which Tk otherwise leaves as its default feather logo.
    We force every slot from icon.ico via Win32 before the window is first shown.
    """
    import ctypes
    if app_id:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
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