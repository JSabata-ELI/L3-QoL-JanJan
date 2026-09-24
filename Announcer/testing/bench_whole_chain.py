"""The whole chain, for real: watching → a live value over its limit → the alarm.

Run:  python testing/bench_whole_chain.py
Writes: testing/chain_hud.png, testing/chain_alarm.png

This is the one check the unit tests cannot make: that starting to watch really
hides the window and leaves the circle, that a genuine archiver reading really
trips, that the alarm window really appears, and that dismissing it really puts
everything back.

It reads the real archiver. The settings are redirected to a throw-away copy, so
the operator's own presets.json is never touched, and the sound is switched off
so a bench does not make a noise in the lab.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEventLoop, QTimer                    # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

app = QApplication.instance() or QApplication([])

import ann_core as C                                             # noqa: E402
import ann_ui as U                                               # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="announcer_bench_"))
_copy = _tmp / "presets.json"
_real = C.program_dir() / C.CONFIG_NAME
if _real.exists():
    _copy.write_bytes(_real.read_bytes())
C.config_path = lambda: _copy

U.install_app_look(app)
import main                                                      # noqa: E402

app.setQuitOnLastWindowClosed(False)


def settle(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def step(text):
    print(f"\n── {text}")


win = main.AnnouncerWindow()
win.resize(1200, 800)
win.show()

# No noise from a bench, and a short flash so the bench is not left flashing.
cfg = win.config()
cfg["sound_enabled"] = False
cfg["flash_duration"] = 0.0
cfg["flash_mode"] = "image"
cfg["color_cycle"] = "rainbow_wave"
cfg["alarm_geometry"] = [120, 120, 280, 360]

step("reading the standard values from the archiver")
settle(2500)
live = [(it, win.engine().state_of(int(it["id"])))
        for it in win.items() if it.get("kind") == "value"]
good = [(it, st) for it, st in live if st.value is not None]
print(f"   {len(good)} of {len(live)} values came back with a number")
if not good:
    print("   NOTHING could be read — is this PC on the facility network?")
    sys.exit(1)
for it, st in good[:3]:
    print(f"   {it['name']}: {st.value:.4g} {it.get('unit', '')}  ({st.state})")

step("making one of them impossible, so it must trip")
victim, vstate = good[0]
victim["hi_hi"] = float(vstate.value) - abs(float(vstate.value)) * 0.5 - 1.0
victim["fires"] = "alarm"
victim["message"] = f"{victim['name']} is over the limit — this is the bench"
print(f"   {victim['name']}: Hi Hi set to {victim['hi_hi']:.4g}, "
      f"and it is reading {vstate.value:.4g}")

step("starting to watch")
win._toggle_watching()
settle(300)
print(f"   the main window is {'hidden' if not win.isVisible() else 'STILL VISIBLE'}"
      f"  (it should be hidden — only the circle is meant to be on screen)")
hud = win._hud
print(f"   the circle is {'on screen' if hud is not None and hud.isVisible() else 'MISSING'}")

settle(2000)

if hud is not None and hud.isVisible():
    hud.grab().save(str(HERE / "chain_hud.png"))
    print(f"   wrote chain_hud.png")

step("what happened")
fired = [it for it in win.items()
         if win.engine().state_of(int(it["id"])).fired]
for it in fired:
    st = win.engine().state_of(int(it["id"]))
    print(f"   FIRED: {it['name']} — {st.text}")
if not fired:
    print("   NOTHING fired — that is a failure of this bench")
print(f"   watching is now {'on' if win.engine().watching else 'off'}"
      f"  (it should be OFF — the alarm is one-shot)")
alarm = win._alarm
if alarm is not None and alarm.isVisible():
    print("   the alarm window is on screen")
    alarm.grab().save(str(HERE / "chain_alarm.png"))
    print("   wrote chain_alarm.png")
else:
    print("   the alarm window is NOT on screen — that is a failure")

step("dismissing it, the way a click does")
if alarm is not None:
    alarm.dismiss()
settle(200)
win._reset()
settle(200)
print(f"   the alarm window is "
      f"{'gone' if alarm is None or not alarm.isVisible() else 'STILL THERE'}")

step("stopping")
win._engine.stop()
settle(400)
print(f"   the main window is {'back' if win.isVisible() else 'STILL HIDDEN'}")
print(f"   the circle is "
      f"{'gone' if hud is None or not hud.isVisible() else 'STILL THERE'}")
print("\n" + win.engine().stats_line())
win.close()
