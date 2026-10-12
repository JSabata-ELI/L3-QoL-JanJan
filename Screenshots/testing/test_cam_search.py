"""Main-window camera search: checkbuttons reused, category order kept,
monitor lookup quick, auto-run settings fixed at Start. Windows withdrawn.

Run:  python testing/test_cam_search.py
"""
import sys
import time

import hidden_tk  # noqa: F401  (before `import s`)
import s

FAILED = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


app = s.App()
app.update()
order0 = [str(w) for w in app.cam_canvas.winfo_children()[0].pack_slaves()]
cbs0 = dict(app._cam_cbs)

t0 = time.perf_counter()
for q in ("S", "SA", "SAM", "SAM1", "SAM", "SA", "S", ""):
    app.cam_search_var.set(q)
    app.update_idletasks()
dt = (time.perf_counter() - t0) * 1000
check("typing reuses the same checkbuttons", dict(app._cam_cbs) == cbs0)
app.cam_search_var.set("SCG")
app.update_idletasks()
app.cam_search_var.set("")
app.update_idletasks()
order1 = [str(w) for w in app.cam_canvas.winfo_children()[0].pack_slaves()]
check("categories keep their order after a search", order1 == order0)
print(f"   8 keystrokes: {dt:.0f} ms")

t0 = time.perf_counter()
mons = s.monitors_for_cameras(list(app.camera_vars)[:40])
check("monitor lookup for 40 cameras is quick", (time.perf_counter() - t0) < 1.0,
      f"{(time.perf_counter() - t0) * 1000:.0f} ms")

app._auto_cycles_var.set("")  # garbage in the box
check("an empty Max cycles box reads as no limit", app._auto_max_cycles() == 0)
app._auto_copy_active = True
app._auto_copy_params = {"interval_s": 7, "max_cycles": 3}
app._auto_interval_var.set(60)
check("a running run keeps its interval", app._auto_interval_s() == 7)
check("a running run keeps its cycle count", app._auto_max_cycles() == 3)
app._auto_copy_active = False
app.destroy()
print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    sys.exit(1)
print("all ok")
