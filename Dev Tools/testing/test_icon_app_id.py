"""Every program's taskbar identity must be new on every launch.

Windows caches the taskbar picture per AppUserModelID and never re-reads it, so
a stable identity eventually draws the blank window placeholder and can never
be talked out of it again. The rule (INFRASTRUCTURE.md, "What is duplicated
deliberately") is therefore: `_icon_app_id()` returns a fresh id each call, and
None when the program has no icon at all.

This test lifts each copy of the function out of its file with `ast` and runs
it, so no program is imported and nothing is launched.

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
    "Dev Tools/cm_t.py",
    "Dev Tools/dev_tools.py",
    "Diagnostic/main.py",
    "Git Work/git_work.py",
    "Image Tools/main.py",
    "Launcher/l.py",
    "Photo Renamer/main.py",
    "Pulser Monitor/pulser_monitor.py",
    "Screenshots/s.py",
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
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
