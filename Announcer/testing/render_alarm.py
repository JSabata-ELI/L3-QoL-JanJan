"""Photograph the alarm in every colour behaviour, and the panel on top.

Run:  python testing/render_alarm.py
Writes: testing/alarm_<mode>_<cycle>.png and testing/hud.png

Rendered on the real Windows platform plugin. The alarm window is translucent
and shaped, so the pictures have real transparency in them — that is the point:
what is NOT painted is what the desktop shows through, and in "one shape" mode
it is also the part of the window that does not take clicks.

A dark chequer is drawn behind each one, so transparent and black can be told
apart in the picture.
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt        # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap              # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

app = QApplication.instance() or QApplication([])

import ann_alarm as AL                                           # noqa: E402
import ann_ui as U                                               # noqa: E402

U.install_app_look(app)


def settle(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def through_mask(widget, pm):
    """What the screen really shows: the painting, clipped by the window shape.

    `QWidget.grab()` paints the widget and ignores the window region, so a
    shaped window photographs as a full rectangle and the picture cannot tell
    you whether the shape is right. Windows clips by the mask, so the harness
    has to as well.
    """
    region = widget.mask()
    if region is None or region.isEmpty():
        return pm
    # `grab()` comes back in DEVICE pixels — on the 150 % screen a 300x380
    # window photographs as 450x570 — while the mask is in the widget's own
    # logical pixels. Clipping one with the other is how a perfectly good mask
    # photographs as a shrunken shape cut off in the corner.
    #
    # Resetting the ratio to 1 is the second half of the fix, and the easier
    # half to miss: a scaled pixmap KEEPS its device-pixel ratio, so drawing a
    # 300x380 pixmap that still claims 1.5 puts it on screen at 200x253.
    if pm.size() != widget.size():
        pm = pm.scaled(widget.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(1.0)
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setClipRegion(region)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def mask_check(widget):
    """Is the shape actually a shape, and is it the right way round?"""
    region = widget.mask()
    if region is None or region.isEmpty():
        return "whole rectangle (nothing masked)"
    w, h = widget.width(), widget.height()
    covered = sum(r.width() * r.height() for r in region)
    share = 100.0 * covered / max(1, w * h)
    corner_in = region.contains(QPoint(1, 1))
    middle_in = region.contains(QPoint(w // 2, h // 2))
    return (f"shaped, {share:.0f}% of the rectangle · "
            f"corner {'IN (wrong)' if corner_in else 'out (right)'} · "
            f"middle {'in' if middle_in else 'OUT (wrong)'}")


def on_chequer(pm):
    """Put the shot on a chequer, so transparent and black look different."""
    out = QPixmap(pm.size())
    out.fill(QColor("#303030"))
    p = QPainter(out)
    step = 12
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#4a4a4a"))
    for y in range(0, pm.height(), step):
        for x in range(0, pm.width(), step):
            if ((x // step) + (y // step)) % 2 == 0:
                p.drawRect(x, y, step, step)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


cfg = {"flash_color": "#ff2222", "flash_interval": 0.3, "flash_duration": 0.0,
       "image_file": "viper", "sound_enabled": False,
       "alarm_geometry": [80, 80, 300, 380]}

messages = []
alarm = AL.AlarmWindow(cfg, log=lambda m, *a: messages.append(m))

for mode in ("color", "image"):
    for cycle in AL.COLOR_CYCLES:
        cfg["flash_mode"] = mode
        cfg["color_cycle"] = cycle
        alarm.raise_alarm("render")
        settle(260)               # past the first blink and a few rainbow steps
        shot = on_chequer(through_mask(alarm, alarm.grab()))
        out = HERE / f"alarm_{mode}_{cycle}.png"
        shot.save(str(out))
        print(f"wrote {out.name:38} {mask_check(alarm)}")
        alarm.dismiss()
        settle(60)

# The dark half of a blink: in "one shape" mode nothing is painted, and the
# window must STILL be shaped, or the click that dismisses it would be lost.
cfg["flash_mode"] = "image"
cfg["color_cycle"] = "fixed"
cfg["flash_interval"] = 0.05
alarm.raise_alarm("render")
settle(30)
dark = None
for _ in range(20):
    settle(25)
    if not alarm._lit:
        dark = on_chequer(through_mask(alarm, alarm.grab()))
        break
if dark is not None:
    dark.save(str(HERE / "alarm_image_dark_half.png"))
    print(f"wrote alarm_image_dark_half.png        {mask_check(alarm)}"
          f"  <- must still be shaped, or the dismissing click is lost")
alarm.dismiss()

# ── the panel on top ────────────────────────────────────────────────────────
hud = AL.HudWindow(cfg)
hud.show_panel()
hud.set_colour("green")
hud.set_badges([("Back reflection — 3.4 mJ is over 2 mJ", "trip"),
                ("Chiller DA3 — 0.4 °C is over 0.3 °C", "warn"),
                ("Helium volume — not refreshed for 40 s", "stale")])
settle(400)
shot = on_chequer(through_mask(hud, hud.grab()))
shot.save(str(HERE / "hud.png"))
print(f"wrote hud.png                          {mask_check(hud)}"
      f"  <- shaped is what lets the desktop behind take the clicks")
badges = on_chequer(hud._badges.grab())
badges.save(str(HERE / "hud_badges.png"))
print("wrote hud_badges.png")
hud.hide_panel()

for m in messages:
    print(f"  log: {m}")
