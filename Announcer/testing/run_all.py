"""Run every test in this folder and report one line per file.

Run:  python testing/run_all.py

**NOTHING RUN FROM HERE MAY APPEAR ON THE OPERATOR'S SCREEN.** That is the
operator's own instruction, given twice on 18.9.2026, and it is the rule this
file enforces rather than merely hopes for: every test is started with
`QT_QPA_PLATFORM=offscreen` in its environment, which makes it physically
impossible for the Qt it loads to put a window anywhere. The tests ask for that
platform themselves with `setdefault`, so the value set here wins.

`test_screen.py` is the one exception, and it is listed as such below: it
measures where the real monitors are and what their scaling is, which the
offscreen plugin invents. It shows no widget at all — it only asks Qt about the
screens.

The render harnesses and the benches are NOT run from here: they exist to be
LOOKED at, so they do put windows on the screen, and one of them needs the
facility network. They are listed at the end and are for the operator to run
when he wants them, not for this file.
"""
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
TESTS = ["test_core.py", "test_cpva.py", "test_screen.py",
         "test_selector.py", "test_multi_select.py", "test_watch.py",
         "test_watch_table.py", "test_hud.py", "test_placing.py",
         "test_columns.py", "test_row_menu.py"]
BY_EYE = ["render_window.py", "render_alarm.py", "render_icons.py",
          "render_dialogs.py", "render_areas_columns.py",
          "bench_archiver.py",
          "bench_whole_chain.py"]
# The two long soaks that used to be run from here are gone, by the operator's
# instruction of 18.9.2026: they ran the program a hundred and fifty times over
# and took the screen for minutes at a time, and he does the trying-out himself.
# Nothing here may run for minutes or put a window on his screen.

# The only test that may load the real Windows platform plugin, and only
# because it measures the real monitors. It shows nothing.
NEEDS_REAL_SCREENS = {"test_screen.py"}

bad = 0
for name in TESTS:
    path = HERE / name
    if not path.exists():
        print(f"{name:20} MISSING")
        bad += 1
        continue
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = ("windows" if name in NEEDS_REAL_SCREENS
                              else "offscreen")
    out = subprocess.run([sys.executable, str(path)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         cwd=str(HERE.parent), env=env)
    tail = [ln for ln in (out.stdout or "").splitlines() if " passed" in ln]
    summary = tail[-1].strip() if tail else "no summary"
    fails = [ln.strip() for ln in (out.stdout or "").splitlines()
             if "FAIL" in ln or "ERROR" in ln]
    mark = "ok  " if out.returncode == 0 else "FAIL"
    print(f"{mark} {name:20} {summary}")
    for line in fails:
        print(f"       {line}")
    if out.returncode != 0:
        bad += 1

print("\nLook at these by eye — they draw windows or need the archiver:")
for name in BY_EYE:
    print(f"    python testing/{name}")
sys.exit(1 if bad else 0)
