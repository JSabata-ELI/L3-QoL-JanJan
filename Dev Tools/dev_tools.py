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


def main():
    from b_t import BuilderUI
    from cm_t import DeployGUI

    root = tk.Tk()
    if getattr(sys, "frozen", False):
        root.title(Path(sys.executable).stem)
    else:
        root.title("Dev Tools")
    try:
        root.iconbitmap(str(_app_dir() / "icon.ico"))
    except Exception:
        pass
    root.geometry("950x780")
    root.minsize(900, 600)

    try:
        ttk.Style(root).configure("Focused.TFrame")
    except Exception:
        pass

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, side="top")

    # Sdílený log widget — fyzicky žije v CM tabu, Builder do něj píše přes log_widget
    cm_tab = DeployGUI(nb)

    def _on_build_done(built_projects, build_summary=None):
        nb.select(cm_tab)
        cm_tab.auto_deploy(built_projects, build_summary=build_summary)

    builder_tab = BuilderUI(nb, on_build_done=_on_build_done, log_widget=cm_tab.log)
    builder_tab._cm_ref = cm_tab

    nb.add(builder_tab, text="  Builder  ")
    nb.add(cm_tab,      text="  Copy Manager  ")

    root.mainloop()


if __name__ == "__main__":
    main()