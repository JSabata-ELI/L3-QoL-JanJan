"""Every program's taskbar identity must be new on every launch.

Windows caches the taskbar picture per AppUserModelID and never re-reads it, so
a stable identity eventually draws the blank window placeholder and can never
be talked out of it again. The rule (INFRASTRUCTURE.md, "What is duplicated
deliberately") is therefore: `_icon_app_id()` returns a fresh id each call, and
None when the program has no icon at all.

This test lifts each copy of the function out of its file with `ast` and runs
it, so no program is imported and nothing is launched.

Every listed file must also define `_nudge_taskbar_icon(hwnd)`: Explorer reads
a taskbar button's picture only once, and taking the window icon away and back
a few times after start is what makes it read again (measured 15.10.2026). In
the Tk programs `set_app_icon()` must call it, must fall back to the icon.ico
bundled inside the exe (sys._MEIPASS), and must wait with after() for the
window's first map instead of forcing it with update_idletasks(). The nudge results are printed
separately for the Tk and the Qt programs.

Run:  python test_icon_app_id.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Every file that carries a copy. Keeping the list explicit is the point: a new
# program is added here, and a program that loses its copy fails loudly.
FILES = [
    "Announcer/main.py",
    "Calibrations/cal.py",
    "Chiller Log/cpt.py",
    "CSS Logger/main.py",
    "Dev Tools/dev_tools.py",
    "Diagnostic/main.py",
    "Git Work/git_work.py",
    "Image Tools/main.py",
    "Laser settings/main.py",
    "Launcher/l.py",
    "Photo Renamer/main.py",
    "Pulser Monitor/main.py",
    "Screenshots/main.py",
    "Shift planner/sp.py",
    "SPFE Values/main.py",
    "Time Converter/tc.py",
]


def load_helper(path: Path):
    """Return (callable, takes_arguments) for this file's _icon_app_id."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_icon_app_id":
            src = ast.get_source_segment(path.read_text(encoding="utf-8"), node)
            ns = {"_APP_ID_PREFIX": "ELI.Test", "_icon_file": lambda: path}
            exec(src, ns)  # noqa: S102 - the source under test is our own
            return ns["_icon_app_id"], bool(node.args.args)
    raise AssertionError(f"{path.name} has no _icon_app_id()")


def _toolkit(tree: ast.Module) -> str:
    """"Qt" when the file imports PySide6, else "Tk" when it imports tkinter."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    if "PySide6" in names:
        return "Qt"
    return "Tk" if "tkinter" in names else "?"


def check_nudge(path: Path) -> tuple[str, list[str]]:
    """Return (toolkit, problems) for this file's taskbar-icon nudge."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    kit = _toolkit(tree)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    problems = []
    nudge = funcs.get("_nudge_taskbar_icon")
    if nudge is None:
        problems.append("no _nudge_taskbar_icon()")
    elif [a.arg for a in nudge.args.args] != ["hwnd"]:
        problems.append("_nudge_taskbar_icon() does not take (hwnd)")
    if kit == "Tk":
        sai = funcs.get("set_app_icon")
        if sai is None:
            problems.append("no set_app_icon()")
        else:
            called = {c.func.id for c in ast.walk(sai)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            if "_nudge_taskbar_icon" not in called:
                problems.append("set_app_icon() does not call _nudge_taskbar_icon()")
            src = ast.get_source_segment(path.read_text(encoding="utf-8"), sai)
            body = src + "".join(
                ast.get_source_segment(path.read_text(encoding="utf-8"), funcs[n]) or ""
                for n in called if n in funcs)
            if "_MEIPASS" not in body:
                problems.append("set_app_icon() has no sys._MEIPASS icon fallback")
            if "wm_frame" not in src:
                problems.append("set_app_icon() does not take the outer window (wm_frame)")
            # The slots wait for the first map (win.after); forcing the outer
            # window with update_idletasks() showed an empty window early.
            methods = {c.func.attr for c in ast.walk(sai)
                       if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
            if "after" not in methods:
                problems.append("set_app_icon() does not defer the slots with after()")
            if "update_idletasks" in methods:
                problems.append("set_app_icon() calls update_idletasks() - maps the window early")
    return kit, problems


def main() -> int:
    failures = []
    for rel in FILES:
        path = ROOT / rel
        try:
            fn, takes_args = load_helper(path)
        except (AssertionError, SyntaxError) as e:
            failures.append(f"{rel}: {e}")
            continue

        call = (lambda: fn("ELI.Test", path)) if takes_args else (lambda: fn())
        a, b = call(), call()

        if a is None or b is None:
            failures.append(f"{rel}: no id for an icon that exists")
        elif a == b:
            failures.append(f"{rel}: same id twice ({a}) - it will go stale")
        elif not a.startswith("ELI.Test."):
            failures.append(f"{rel}: id does not keep the prefix ({a})")

        if takes_args:
            # No icon, and a path that does not exist, must both give no id:
            # an identity with no window icon behind it has nothing to draw.
            if fn("ELI.Test", None) is not None:
                failures.append(f"{rel}: id handed out with no icon")
            if fn("ELI.Test", path.parent / "no_such_icon.ico") is not None:
                failures.append(f"{rel}: id handed out for a missing icon file")

        if not failures or not failures[-1].startswith(rel):
            print(f"{rel:38s} ok  {a}")

    print()
    for f in failures:
        print("FAIL", f)
    print(f"{len(FILES) - len({f.split(':')[0] for f in failures})}"
          f"/{len(FILES)} files ok")

    # The taskbar-icon nudge, reported per toolkit.
    nudge_fail = {"Tk": [], "Qt": [], "?": []}
    nudge_count = {"Tk": 0, "Qt": 0, "?": 0}
    for rel in FILES:
        path = ROOT / rel
        try:
            kit, problems = check_nudge(path)
        except (OSError, SyntaxError) as e:
            kit, problems = "?", [str(e)]
        nudge_count[kit] += 1
        nudge_fail[kit].extend(f"{rel}: {p}" for p in problems)
    for kit in ("Tk", "Qt", "?"):
        if not nudge_count[kit]:
            continue
        print()
        print(f"Taskbar-icon nudge, {kit} programs:")
        for f in nudge_fail[kit]:
            print("FAIL", f)
        bad = len({f.split(':')[0] for f in nudge_fail[kit]})
        print(f"{nudge_count[kit] - bad}/{nudge_count[kit]} files ok")

    any_nudge_fail = any(nudge_fail.values())
    return 1 if failures or any_nudge_fail else 0


if __name__ == "__main__":
    sys.exit(main())
