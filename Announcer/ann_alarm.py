"""Announcer — the alarm: the flashing window, and the panel that stays on top.

THE FLASH
---------
A borderless, always-on-top window that either fills itself with a colour or
shows one shape — a scorpion, a viper — painted in that colour. Four colour
behaviours:

    fixed             blink in the picked colour
    rainbow_blink     blink, each flash a clearly different hue
    rainbow_spectrum  blue-to-red laid across the width, drifting, blinking
    rainbow_wave      rainbow rings running out of the centre, never dark

Qt draws real per-pixel alpha, so three things the tkinter version needed are
gone: the near-black colour key every pixel had to be painted in, the rule that
the key must never equal the flash colour, and the window-opacity trick used to
blink. In particular `FLASH_OFF_ALPHA = 0.02` is not ported. It existed only
because tk could not blank the picture without also losing the click that
dismisses the alarm; here the shape IS the window (`setMask`), so the dark half
of a blink simply paints nothing and the click target cannot be lost.

What IS kept is the lookup-table way of drawing the moving rainbows. The grey
map that says which colour each pixel gets is built once per size and wrapped in
an 8-bit indexed QImage; a frame is then nothing but a new 256-entry colour
table. A `QLinearGradient` would be fewer lines but a different picture —
different ring spacing and visible banding between its handful of stops.

THE PANEL ON TOP
----------------
While watching, the tabbed window disappears and only the circle is on screen,
click-through around it, which is how this program has always worked. Two
windows, not one: the circle is a fixed size and gets a real window shape
(`setMask` with an ellipse, which the Windows plugin turns into `SetWindowRgn`,
so the desktop behind it genuinely takes the clicks), and the sentences sit in a
separate plain rectangle beside it. They have to be separate — masking a strip
whose height changes with the number of sentences is the case where a mask and a
layout fight each other, and that fight is already on record in a sibling
program.
"""

from __future__ import annotations

import colorsys
import math
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBitmap, QColor, QFont, QImage, QPainter, QPen,
                           QPixmap, QRegion)
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

import ann_core as C
import ann_screen as S
import ann_sound as Snd
import ann_ui as U


def owner_preset(owner):
    """Which preset the header is on — the one whose placement applies.

    Asked of the main window rather than kept here: the operator can change the
    preset between one alarm and the next, and both of these windows outlive
    that. Anything that is not a main window (a render script, a test) gets All.
    """
    try:
        return owner.preset()
    except Exception:                                          # noqa: BLE001
        return C.PRESET_ALL

# ── the rainbows ─────────────────────────────────────────────────────────────
GRADIENT_INTERVAL_MS = 60       # redraw of a moving rainbow, ~16 frames a second
GRADIENT_STEP = 3               # colour-wheel steps (of 256) per redraw
WAVE_BANDS = 3                  # rainbow rings between the centre and the edge
SPECTRUM_SPAN = 0.67            # share of the wheel laid across the width
BLUE_HUE_IDX = 171              # blue on the 0-255 wheel, the spectrum's left end
RAINBOW_BLINK_STEP_DEG = 47     # hue jump per blink — a clearly different colour

DEFAULT_COLOR = "#ff2222"
DEFAULT_INTERVAL_S = 0.3
COLOR_CYCLES = C.COLOR_CYCLES
FLASH_MODES = ("color", "image")

_TICK_MS = 20                   # the clock everything else is derived from

_hue_wheel = None


def hue_wheel():
    """The colour wheel as 256 fully saturated RGB triples, built once."""
    global _hue_wheel
    if _hue_wheel is None:
        _hue_wheel = [tuple(round(c * 255) for c in
                            colorsys.hsv_to_rgb(i / 256.0, 1.0, 1.0))
                      for i in range(256)]
    return _hue_wheel


def images_dir():
    """The alarm pictures, next to the exe once it is built.

    A build without this folder can only flash a plain colour, which is why the
    folder is named in build_config.json even though the builder now finds it
    by itself.
    """
    import sys
    base = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    return base / "images"


def image_names():
    out = []
    try:
        for ext in (".png", ".gif", ".jpg", ".jpeg"):
            out += sorted(p.stem for p in images_dir().glob(f"*{ext}"))
    except Exception:
        pass
    return out


def image_path(stem):
    if not stem:
        return None
    for ext in (".png", ".gif", ".jpg", ".jpeg"):
        p = images_dir() / f"{stem}{ext}"
        if p.exists():
            return p
    return None


# ── settings, with their defaults in one place ───────────────────────────────

def read_settings(cfg):
    """Everything the alarm needs, with every default stated.

    An older settings file simply picks up the defaults for what it lacks. Two
    retired values are translated on the way, so an operator who chose them once
    is not left with nothing: the flash mode "alternate" became "image" when the
    picture learned to blink on its own, and the cycle "rainbow_smooth" became
    "rainbow_wave", which is the no-blink one.
    """
    mode = cfg.get("flash_mode", "color")
    if mode == "alternate":
        mode = "image"
    if mode not in FLASH_MODES:
        mode = "color"
    cycle = cfg.get("color_cycle", "fixed")
    if cycle == "rainbow_smooth":
        cycle = "rainbow_wave"
    if cycle not in COLOR_CYCLES:
        cycle = "fixed"
    interval = cfg.get("flash_interval", DEFAULT_INTERVAL_S)
    if not isinstance(interval, (int, float)) or not 0 <= interval <= 3.0:
        interval = DEFAULT_INTERVAL_S
    duration = cfg.get("flash_duration", 0.0)
    if not isinstance(duration, (int, float)) or not 0 <= duration <= 600:
        duration = 0.0
    leadin = cfg.get("sound_leadin", Snd.LEADIN_MS)
    if not isinstance(leadin, (int, float)) or not 0 <= leadin <= 5000:
        leadin = Snd.LEADIN_MS
    return {
        "mode": mode,
        "cycle": cycle,
        "colour": cfg.get("flash_color") or DEFAULT_COLOR,
        "interval": float(interval),
        "duration": float(duration),          # 0 = until it is dismissed
        "image": cfg.get("image_file") or "",
        # Silent unless it is asked for: a new installation must not start
        # making a noise at somebody who has not chosen a sound yet.
        "sound_on": bool(cfg.get("sound_enabled", False)),
        "sound": cfg.get("sound_file") or "beep",
        "freq": int(cfg.get("sound_freq") or 1500),
        "sound_ms": int(cfg.get("sound_duration") or 500),
        "leadin": int(leadin),
        "device": cfg.get("sound_device") or Snd.DEFAULT_DEVICE,
    }


# ─────────────────────────────────────────────────────────────────────────────
# The flashing window
# ─────────────────────────────────────────────────────────────────────────────

# How wide the grabbable border is while the window is being placed, in pixels.
# The handles are PAINTED to this same number, so what is drawn is exactly what
# can be caught with the mouse.
_PLACE_EDGE = 8


class AlarmWindow(QWidget):
    """Where the alarm flashes. One window, reused; never destroyed."""

    dismissed = Signal()
    # Esc pressed on the window while it was being placed. The little box that
    # asks "keep this place?" is a separate window and cannot see that key, so
    # it is told; otherwise Esc left the box on screen with nothing under it.
    placing_cancelled = Signal()

    def __init__(self, cfg, log=None, owner=None):
        # No parent, deliberately: a Qt.Tool window WITH a parent is hidden
        # whenever the parent hides, and hiding the main window is the whole
        # point of watching.
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self._cfg = cfg
        self._log = log or (lambda *_a, **_k: None)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Announcer — alarm")

        self._s = read_settings(cfg)
        self._running = False
        self._started = 0.0
        self._lit = True
        self._phase = 0.0
        self._blink_hue = 0.0
        self._placing = False

        self._grey_key = None
        self._indexed = None         # the cached coordinate map, as a QImage
        self._mask_key = None
        # The picture's shape, cached per size. Reading and resampling a PNG is
        # not work for a paintEvent, and while the window is being positioned
        # the paintEvent runs on every mouse move.
        self._sil_key = None
        self._sil = (None, None)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

        self.hide()

    # ── raising and dismissing ──────────────────────────────────────────────
    def raise_alarm(self, sentence=""):
        """Start flashing, wherever the alarm window has been placed."""
        self._s = read_settings(self._cfg)
        self._sentence = sentence
        self._apply_geometry()

        if self._s["mode"] == "image" and image_path(self._s["image"]) is None:
            # Said out loud and then carried on in colour: an alarm that does
            # not appear because a file is missing is the worst of both.
            self._log("No alarm picture is selected, or the file is missing — "
                      "flashing a plain colour instead.")
            self._s["mode"] = "color"

        self._running = True
        self._started = time.monotonic()
        self._lit = True
        self._phase = 0.0
        self._blink_hue = 0.0
        self._refresh_mask()
        self.show()
        self.raise_()
        self.activateWindow()
        self.update()
        self._timer.start()
        if self._s["sound_on"]:
            Snd.play(self._s["sound"], freq=self._s["freq"],
                     duration_ms=self._s["sound_ms"],
                     leadin_ms=self._s["leadin"], device=self._s["device"],
                     report=None)

    def dismiss(self):
        if not self._running and not self.isVisible():
            return
        self._running = False
        self._timer.stop()
        self.hide()
        self.dismissed.emit()

    # ── where it appears ────────────────────────────────────────────────────
    def _apply_geometry(self):
        """The remembered place, or the middle of the main screen.

        Kept as a plain [x, y, w, h] in the settings. The tkinter version stored
        a geometry string and had to measure and correct where the window really
        landed, because tk remembered the inside corner while Windows positioned
        the outside; Qt has no such gap.
        """
        geom = C.placement(self._cfg, owner_preset(self._owner), "alarm")
        if geom:
            w = max(40, int(geom[2]))
            h = max(40, int(geom[3]))
            x, y = S.fit_rect(int(geom[0]), int(geom[1]), w, h)
            self.setGeometry(x, y, w, h)
            return
        infos = S.screens()
        info = next((i for i in infos if i.primary), infos[0]) if infos else None
        w, h = 420, 300
        if info is not None:
            a = info.available
            self.setGeometry(a.x() + (a.width() - w) // 2,
                             a.y() + (a.height() - h) // 2, w, h)
        else:
            self.resize(w, h)

    def save_geometry(self):
        g = self.geometry()
        C.set_placement(self._cfg, owner_preset(self._owner), "alarm",
                        [g.x(), g.y(), g.width(), g.height()])

    # ── the picture as a window shape ───────────────────────────────────────
    def _silhouette(self, size):
        """(QPixmap of the shape, QBitmap mask) for the current picture.

        The alpha is hard-thresholded at 128. A resampled edge carries partial
        alpha, and a window shape is binary either way, so thresholding is what
        makes the visible edge and the clickable edge the same edge.
        """
        path = image_path(self._s["image"])
        if path is None or size.width() < 1 or size.height() < 1:
            return None, None
        key = (str(path), size.width(), size.height())
        if key == self._sil_key:
            return self._sil
        self._sil_key = key
        self._sil = (None, None)
        try:
            from PIL import Image
            img = Image.open(path).convert("RGBA")
            img = img.resize((size.width(), size.height()), Image.LANCZOS)
            alpha = img.getchannel("A").point(
                lambda a: 255 if a >= 128 else 0)
            shape = Image.new("RGBA", img.size, (255, 255, 255, 0))
            shape.putalpha(alpha)
            data = shape.tobytes("raw", "RGBA")
            qimg = QImage(data, shape.width, shape.height, shape.width * 4,
                          QImage.Format.Format_RGBA8888).copy()
            pm = QPixmap.fromImage(qimg)
            self._sil = (pm, pm.mask())
            return self._sil
        except Exception as exc:
            # Said once per size, not once per frame: this is reachable from a
            # paintEvent that runs fifty times a second.
            self._log(f"The alarm picture could not be read: {exc}")
            return None, None

    def _refresh_mask(self):
        """Give the window the shape of the picture, or no shape at all."""
        key = (self._s["mode"], self._s["image"], self.width(), self.height(),
               self._placing)
        if key == self._mask_key:
            return
        self._mask_key = key
        if self._placing or self._s["mode"] != "image":
            self.clearMask()
            return
        _pm, mask = self._silhouette(self.size())
        if mask is None or mask.isNull():
            self.clearMask()
            return
        self.setMask(QRegion(mask))

    def resizeEvent(self, ev):
        # A mask is not recomputed for us, and the coordinate map is per size.
        self._mask_key = None
        self._grey_key = None
        # Guarded because this reads and resamples a PNG. An exception raised
        # inside a Python override does not stop at the boundary: PySide6 leaves
        # it set, later overrides fail with `SystemError`, and the process dies
        # with an access violation in the next garbage collection — measured
        # 2026-09-17. A window without its shape is a far smaller problem.
        try:
            self._refresh_mask()
        except Exception as exc:
            self._log(f"The alarm window could not be shaped: {exc}")
        super().resizeEvent(ev)

    # ── the clock ───────────────────────────────────────────────────────────
    def _tick(self):
        if not self._running:
            return
        elapsed = time.monotonic() - self._started
        if self._s["duration"] > 0 and elapsed > self._s["duration"]:
            self.dismiss()
            return
        cycle = self._s["cycle"]
        interval = max(0.0, self._s["interval"])
        moving = cycle in ("rainbow_wave", "rainbow_spectrum")

        # An interval of 0 means "do not blink at all", and the wave never goes
        # dark by design — its travelling colours are the alarm.
        if interval <= 0 or cycle == "rainbow_wave":
            lit = True
        else:
            lit = int(elapsed / interval) % 2 == 0
        changed = lit != self._lit
        self._lit = lit

        if moving:
            # One control drives both the blink and the travel, so a faster
            # blink also means faster colours.
            speed = max(0.25, min(4.0, DEFAULT_INTERVAL_S / interval)) \
                if interval > 0 else 1.0
            self._phase += GRADIENT_STEP * speed * (_TICK_MS / GRADIENT_INTERVAL_MS)
            changed = True
        elif cycle == "rainbow_blink" and lit and changed:
            self._blink_hue = (self._blink_hue + RAINBOW_BLINK_STEP_DEG) % 360.0
            changed = True

        if changed:
            self.update()

    # ── drawing ─────────────────────────────────────────────────────────────
    def _colour_now(self):
        if self._s["cycle"] == "rainbow_blink":
            r, g, b = colorsys.hsv_to_rgb(self._blink_hue / 360.0, 1.0, 1.0)
            return QColor(round(r * 255), round(g * 255), round(b * 255))
        return QColor(self._s["colour"] or DEFAULT_COLOR)

    def _coord_map(self, kind, w, h):
        """The grey map that says which colour of the rainbow a pixel gets.

        "wave" grows from 0 in the centre to 255 at the edge, so the colours come
        out as rings; "spectrum" from 0 on the left to 255 on the right, so they
        come out as bands. Cached per size — every frame of a moving rainbow
        reuses it, and building it is the only expensive part.
        """
        key = (kind, w, h)
        if key == self._grey_key:
            return self._indexed        # None here means "tried, cannot"
        # Guarded, and `copy()`ed, for two separate reasons. Guarded because
        # this is reached from a paintEvent with a QPainter already open on the
        # window: an exception escaping there leaves the painter active, and the
        # program then dies on the next move with "Cannot destroy paint device
        # that is being painted". `copy()` because a QImage built on a Python
        # `bytes` only BORROWS it — the previous map's image pointed into a
        # buffer that was being replaced on the line above.
        try:
            from PIL import Image
            src = (Image.radial_gradient("L") if kind == "wave"
                   else Image.linear_gradient("L").transpose(Image.ROTATE_90))
            grey = src.resize((w, h), Image.BILINEAR).tobytes()
            self._indexed = QImage(grey, w, h, w,
                                   QImage.Format.Format_Indexed8).copy()
            self._grey_key = key
        except Exception as exc:
            # The key is remembered on failure too, so this is said once per
            # size and not fifty times a second.
            self._indexed = None
            self._grey_key = key
            self._log(f"The rainbow could not be built: {exc}")
        return self._indexed

    def _gradient_image(self, kind, w, h):
        """One frame: the cached map with a freshly rotated colour table."""
        img = self._coord_map(kind, w, h)
        if img is None:
            return None
        wheel = hue_wheel()
        if kind == "wave":
            start, span, travel = 0.0, WAVE_BANDS * 256.0, -self._phase
        else:
            start, span, travel = BLUE_HUE_IDX, -SPECTRUM_SPAN * 256.0, self._phase
        table = []
        for v in range(256):
            r, g, b = wheel[int(start + v * span / 255.0 + travel) % 256]
            table.append(0xFF000000 | (r << 16) | (g << 8) | b)
        img.setColorTable(table)
        return img

    def paintEvent(self, _ev):
        """Opens the painter and closes it whatever the body does.

        The `finally` is load-bearing. An exception escaping a paintEvent leaves
        the QPainter active on the widget; Qt then prints
        `QBackingStore::endPaint() called with active painter` on every repaint
        and the program dies on the next move or resize with `QPaintDevice:
        Cannot destroy paint device that is being painted`. This window repaints
        fifty times a second for as long as the alarm is up.
        """
        p = QPainter(self)
        try:
            self._paint_body(p)
        finally:
            p.end()

    def _paint_body(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        if self._placing:
            # Being positioned: a tint you can see straight through, a ghost of
            # the picture, and a drawn frame with grips.
            #
            # The tint stays faint on purpose. The window is lined up ON TOP of
            # something that is already on the monitor — the picture that is
            # meant to blink — so covering that up defeats the whole exercise.
            # What makes the rectangle findable is the frame, not the shading.
            p.fillRect(rect, QColor(30, 30, 30, 45))
            pm, _m = self._silhouette(self.size())
            if pm is not None:
                p.setOpacity(0.35)
                p.drawPixmap(0, 0, pm)
                p.setOpacity(1.0)
            self._paint_place_frame(p, rect)
            return

        if not self._running:
            return

        cycle = self._s["cycle"]
        image_mode = self._s["mode"] == "image"

        if not self._lit:
            # The dark half of a blink. In image mode nothing is painted at all
            # — the window still has the shape of the picture, so the click that
            # dismisses the alarm cannot be lost. In colour mode the fill is
            # darkened instead, which is the pulse the operator knows.
            if not image_mode:
                col = self._colour_now()
                p.fillRect(rect, QColor(round(col.red() * 0.25),
                                        round(col.green() * 0.25),
                                        round(col.blue() * 0.25)))
            return

        if cycle in ("rainbow_wave", "rainbow_spectrum"):
            img = self._gradient_image("wave" if cycle == "rainbow_wave"
                                       else "spectrum",
                                       max(1, rect.width()), max(1, rect.height()))
            if img is not None:
                p.drawImage(0, 0, img)
            else:
                p.fillRect(rect, self._colour_now())
        else:
            p.fillRect(rect, self._colour_now())

    def _paint_place_frame(self, p, rect):
        """The edge and the eight grips, while the window is being placed.

        Both colours are stated here and neither is left to the theme. This
        window sits on whatever the operator happens to have on screen, so a
        single-colour grip is an invisible grip half the time: every line is
        drawn white first and dark on top, which reads on a black wallpaper and
        on a white one alike.
        """
        d = _PLACE_EDGE
        edge = rect.adjusted(1, 1, -2, -2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 235), 3))
        p.drawRect(edge)
        p.setPen(QPen(QColor(20, 20, 20, 235), 1))
        p.drawRect(edge)

        xs = (rect.left(), rect.center().x() - d // 2, rect.right() - d + 1)
        ys = (rect.top(), rect.center().y() - d // 2, rect.bottom() - d + 1)
        p.setBrush(QColor(255, 255, 255, 245))
        p.setPen(QPen(QColor(20, 20, 20), 1))
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                if i == 1 and j == 1:
                    continue        # the middle is for dragging, not for sizing
                p.drawRect(int(x), int(y), d - 1, d - 1)

    # ── being dragged and sized ─────────────────────────────────────────────
    def _edges_at(self, pos):
        """Which edges of the window a point is on, `_PLACE_EDGE` px wide.

        Empty when the point is in the middle, which is the part that moves the
        window rather than sizing it. Exactly the grips `_paint_place_frame`
        draws, so the picture and the behaviour cannot drift apart.
        """
        d = _PLACE_EDGE
        edges = Qt.Edge(0)
        if pos.x() < d:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - d:
            edges |= Qt.Edge.RightEdge
        if pos.y() < d:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - d:
            edges |= Qt.Edge.BottomEdge
        return edges

    _EDGE_CURSORS = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
    }

    def mousePressEvent(self, ev):
        if self._placing:
            # No title bar means Windows gives us neither a move nor a size, so
            # both are asked for here. Windows is asked to do the dragging
            # rather than the position being computed from mouse moves: the
            # pointer then stays glued to the spot it grabbed, and the window
            # keeps up with the mouse instead of trailing it.
            handle = self.windowHandle()
            if handle is None:
                return
            edges = self._edges_at(ev.position().toPoint())
            if edges:
                handle.startSystemResize(edges)
            else:
                handle.startSystemMove()
            return
        self.dismiss()

    def mouseMoveEvent(self, ev):
        """While being placed, the pointer says what the spot under it does."""
        if not self._placing:
            return
        try:
            shape = self._EDGE_CURSORS.get(
                self._edges_at(ev.position().toPoint()),
                Qt.CursorShape.SizeAllCursor)
            if self.cursor().shape() != shape:
                self.setCursor(shape)
        except Exception:
            # An exception escaping a Qt override poisons the process (see
            # `resizeEvent`), and this one runs on every mouse move.
            pass

    def keyPressEvent(self, ev):
        # Esc is the guaranteed way out. A frameless window may not hold the
        # keyboard focus, so the alarm is also dismissed by a click anywhere on
        # it — and in image mode "on it" means on the shape, which is the only
        # part of the window that exists.
        if ev.key() == Qt.Key.Key_Escape:
            if self._placing:
                self.stop_placing()
                self.placing_cancelled.emit()
            else:
                self.dismiss()

    # ── being placed ────────────────────────────────────────────────────────
    def start_placing(self):
        """Show the window as a draggable rectangle so it can be positioned."""
        self._s = read_settings(self._cfg)
        self._running = False
        self._timer.stop()
        self._placing = True
        self._apply_geometry()
        self._mask_key = None
        self._refresh_mask()
        self.setWindowOpacity(0.70)
        # Small enough to be dragged into a corner, never small enough to lose.
        # Sized to nothing, a frameless window has no edge left to grab and the
        # only way back would be "Fit to the picture".
        self.setMinimumSize(QSize(3 * _PLACE_EDGE, 3 * _PLACE_EDGE))
        # The pointer has to change over the edges without a button held down,
        # and a widget is not told about those moves unless it asks.
        self.setMouseTracking(True)
        self.show()
        self.raise_()
        # Windows only obeys a move or a size request from the window in front.
        # Without this the first drag did nothing at all, and the rectangle had
        # to be clicked once before it would move.
        self.activateWindow()
        self.update()

    def stop_placing(self, keep=False):
        if keep:
            self.save_geometry()
        self._placing = False
        self.setWindowOpacity(1.0)
        self.setMouseTracking(False)
        self.unsetCursor()
        self.setMinimumSize(QSize(0, 0))
        self._mask_key = None
        self.hide()

    def fit_to_picture(self):
        """Make the window exactly the picture's own size, corner kept."""
        path = image_path(self._s["image"])
        if path is None:
            return False
        try:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            return False
        g = self.geometry()
        x, y = S.fit_rect(g.x(), g.y(), w, h)
        self.setGeometry(x, y, w, h)
        self.update()
        return True


class PlaceAlarmDialog(QDialog):
    """The little box that sits beside the alarm window while it is positioned."""

    def __init__(self, parent, alarm):
        super().__init__(parent)
        self._alarm = alarm
        self.setWindowTitle("Where the alarm appears")
        self.setModal(False)
        U.paint_dialog(self)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Drag the middle of the framed rectangle to where the alarm should "
            "appear.\nDrag one of the eight grips on its edge to size it. The "
            "picture is shown\nfaintly inside, so you can line it up on "
            "something. Esc leaves it as it was."))
        row = QHBoxLayout()
        fit = U.button("Fit to the picture", icon_name="camera",
                       tip="Make the window exactly as big as the picture "
                           "file, so it is not stretched.")
        fit.clicked.connect(self._fit)
        row.addWidget(fit)
        row.addStretch(1)
        lay.addLayout(row)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Keep this place")
        ok.setStyleSheet(U.PRIMARY_QSS)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(U.BTN_QSS)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _fit(self):
        if not self._alarm.fit_to_picture():
            QLabel()        # nothing to fit to; the button says why in its tip


class PlaceCircleDialog(QDialog):
    """The little box that sits beside the circle while it is positioned."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Where the circle sits")
        self.setModal(False)
        U.paint_dialog(self)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Drag the circle to where it should sit while the program is\n"
            "watching. The space around it stays click-through, so it cannot\n"
            "get in the way of anything behind it."))
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Keep this place")
        ok.setStyleSheet(U.PRIMARY_QSS)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(U.BTN_QSS)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


# ─────────────────────────────────────────────────────────────────────────────
# The panel that stays on screen while watching
# ─────────────────────────────────────────────────────────────────────────────

def badge_spot(circle, w, h, area, gap=6):
    """Where the strip of sentences goes: (x, y). Pure, so it can be tested.

    The circle's home is the TOP RIGHT corner of the screen, where there is no
    room to its right — so simply clamping the strip onto the screen slid it
    straight over the circle and hid the one thing that is always meant to be
    visible. That is what the operator photographed on 2026-09-17.

    So each side is tried in turn, and a side only counts if the strip fits on
    the screen there AND leaves the circle alone. If none does — a strip wider
    than the screen, or a circle dragged into a corner — it goes underneath,
    which is the one direction that cannot overlap the circle whatever the
    strip's width.

    `area` is the screen's usable rectangle, or None when there is no screen to
    speak of, in which case only the overlap rule applies.
    """
    for cx, cy in ((circle.right() + gap, circle.y()),
                   (circle.x() - gap - w, circle.y()),
                   (circle.x(), circle.bottom() + gap),
                   (circle.x(), circle.y() - gap - h)):
        spot = QRect(cx, cy, w, h)
        if area is not None and not area.contains(spot):
            continue
        if spot.intersects(circle):
            continue
        return cx, cy

    x, y = circle.x(), circle.bottom() + gap
    if area is not None:
        x = min(max(x, area.x()), max(area.x(), area.x() + area.width() - w))
        y = min(max(y, area.y()), max(area.y(), area.y() + area.height() - h))
        # Clamping vertically can have pushed it back over the circle. Below is
        # the answer even if that hangs off the bottom: a sentence half off the
        # screen can still be read, a sentence under the circle cannot, and the
        # circle is the control.
        if QRect(x, y, w, h).intersects(circle):
            y = circle.bottom() + gap
    return x, y


class _BadgeStrip(QWidget):
    """The sentences, in a plain rectangle beside the circle.

    A rectangle needs no mask: it hit-tests correctly by itself. That is the
    reason this is not part of the circle's window — a mask around a strip whose
    height changes with the number of sentences is exactly where a mask and a
    layout start fighting each other.
    """

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(3)
        self._labels = []
        self._said = None            # the sentences last put on screen

    def set_badges(self, badges):
        """badges = [(text, state)], worst first. Empty hides the strip.

        Does nothing at all when the sentences have not changed. This is driven
        by the engine's 2 Hz signal, and with the lab idle every machine value
        is legitimately out of range — so it used to re-parse a stylesheet for
        every badge twice a second and call `raise_()` on a topmost window with
        it, about ninety thousand times in a couple of hours.
        """
        badges = list(badges)
        if badges == getattr(self, "_said", None):
            return
        self._said = list(badges)
        while len(self._labels) < len(badges):
            lbl = QLabel(self)
            lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            lbl.setWordWrap(False)
            self._labels.append(lbl)
            self._lay.addWidget(lbl)
        for i, lbl in enumerate(self._labels):
            if i < len(badges):
                text, state = badges[i]
                ground, ink = U.STATE_COLORS.get(state,
                                                 U.STATE_COLORS["unknown"])
                if state == C.STATE_TRIP:
                    ground, ink = "#cc2200", "#ffffff"
                lbl.setText(f"  {text}  ")
                lbl.setStyleSheet(
                    f"QLabel {{ background: {ground}; color: {ink};"
                    f" border: 1px solid {ink}; border-radius: 3px;"
                    f" padding: 3px 6px; }}")
                lbl.show()
            else:
                lbl.hide()
        if badges:
            self.adjustSize()
            # Only when it is not already up: `show()` + `raise_()` on a
            # topmost window is a SetWindowPos every time, and it was being
            # asked for twice a second with nothing to change.
            if not self.isVisible():
                self.show()
            self.raise_()
        else:
            self.hide()


class _NoDataMark(QWidget):
    """The red exclamation mark on the desktop, beside the circle.

    Its own window, for the same reason the sentences are their own window: the
    circle's window is masked down to the circle, and the desktop behind
    everything else has to keep taking the clicks. The mask here is the narrow
    strip the mark is drawn in, so the operator can still click his own work on
    either side of it.
    """

    _D = 40            # taller than the circle, so it cannot be missed

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._mark = U.AlertMark(self._D, self)
        self._mark.move(0, 0)
        self._mark.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self._mark.width(), self._mark.height())
        self.setToolTip(self._mark.toolTip())
        strip = max(4, round(self.width() * 0.6))
        self.setMask(QRegion(round((self.width() - strip) / 2), 0,
                             strip, self.height()))


class HudWindow(QWidget):
    """The circle, on top of everything, click-through around it."""

    clicked = Signal()

    _D = 30          # the circle's diameter
    _NORMAL_TIP = ("Watching. Click to stop and bring the window back.\n"
                   "Drag it to move it.")
    _PLACING_TIP = ('Drag me to where I should sit, then press '
                    '"Keep this place".')

    def __init__(self, cfg, owner=None):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self._cfg = cfg
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self._D + 6, self._D + 6)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self._NORMAL_TIP)
        self._light = U.StatusLight(self._D, self)
        self._light.move(0, 0)
        # The circle inside must not eat the drag or the click; it is drawing,
        # not a control.
        self._light.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._press = None
        self._placing = False
        self._placing_from = None
        self._badges = _BadgeStrip()
        self._no_data = None        # the exclamation mark, made on first use
        self._apply_mask()

    def _apply_mask(self):
        """Only the circle is window at all — the rest reaches the desktop.

        `setMask` is the mechanism that genuinely makes the corners
        click-through on Windows: the Qt plugin turns it into `SetWindowRgn`, so
        the pixels outside are neither painted nor hit-tested. A translucent
        background alone is not enough — Windows would still hit-test the whole
        rectangle and swallow every click that landed in a corner.

        The ellipse is a pixel wider than the drawn circle, so the antialiased
        edge is not clipped away.
        """
        self.setMask(QRegion(1, 1, self._D + 4, self._D + 4,
                             QRegion.RegionType.Ellipse))

    def set_colour(self, name):
        self._light.set_colour(name)

    def set_badges(self, badges):
        self._badges.set_badges(badges)
        self._place_badges()

    def set_no_data(self, on):
        """Put the red exclamation mark beside the circle, or take it away.

        Nothing is written next to it. It says one thing — the readings have
        stopped arriving — and the log holds the details.
        """
        if not on:
            if self._no_data is not None:
                self._no_data.hide()
            self._place_badges()
            return
        if self._no_data is None:
            self._no_data = _NoDataMark()
        self._place_mark()
        if not self._no_data.isVisible():
            self._no_data.show()
        self._no_data.raise_()
        self._place_badges()

    def _mark_visible(self):
        return self._no_data is not None and self._no_data.isVisible()

    def _place_mark(self):
        """Beside the circle, on whichever side has room for it.

        The same rule as the sentences, for the same reason: the circle's home
        is the top right corner, where there is nothing to the right of it.
        """
        if self._no_data is None:
            return
        infos = S.screens()
        info = S.screen_for_region(
            [self.x(), self.y(), self.x() + self.width(),
             self.y() + self.height()], infos)
        if info is None:
            info = next((i for i in infos if i.primary), infos[0]) if infos \
                else None
        x, y = badge_spot(self.geometry(), self._no_data.width(),
                          self._no_data.height(),
                          info.available if info is not None else None,
                          self._GAP)
        self._no_data.move(x, y)

    def set_message(self, text):
        self.set_badges([(text, C.STATE_TRIP)] if text else [])

    _GAP = 6

    def _place_badges(self):
        if not self._badges.isVisible():
            return
        infos = S.screens()
        info = S.screen_for_region(
            [self.x(), self.y(), self.x() + self.width(),
             self.y() + self.height()], infos)
        if info is None:
            info = next((i for i in infos if i.primary), infos[0]) if infos \
                else None
        # The sentences keep off the exclamation mark as well as off the
        # circle: both are things that must never be covered up, so they are
        # treated as one block to avoid.
        keep_off = self.geometry()
        if self._mark_visible():
            keep_off = keep_off.united(self._no_data.geometry())
        x, y = badge_spot(keep_off,
                          max(1, self._badges.width()),
                          max(1, self._badges.height()),
                          info.available if info is not None else None,
                          self._GAP)
        self._badges.move(x, y)

    # ── showing ─────────────────────────────────────────────────────────────
    def show_panel(self):
        pos = C.placement(self._cfg, owner_preset(self._owner), "hud")
        if pos:
            x, y = S.fit_rect(int(pos[0]), int(pos[1]),
                              self.width(), self.height())
        else:
            infos = S.screens()
            info = next((i for i in infos if i.primary), infos[0]) if infos \
                else None
            a = info.available if info is not None else QRect(0, 0, 800, 600)
            x, y = a.x() + a.width() - self.width() - 24, a.y() + 24
        self.move(x, y)
        self._apply_mask()
        self.show()
        self.raise_()
        self._place_badges()

    def hide_panel(self):
        self.remember_position()
        self._badges.hide()
        self._hide_mark()
        self.hide()

    def _hide_mark(self):
        if self._no_data is not None:
            self._no_data.hide()

    def remember_position(self):
        C.set_placement(self._cfg, owner_preset(self._owner), "hud",
                        [self.x(), self.y()])

    # ── being placed ───────────────────────────────────────────────────────
    def start_placing(self):
        """Show the circle on its own so it can be dragged to a new home.

        The same idea as placing the alarm window: the thing being positioned is
        the real thing, not a stand-in. The sentences are left out of it — they
        move with the circle and there may be none at the time.
        """
        self._placing = True
        self._placing_from = [self.x(), self.y()]
        self._badges.hide()
        self._hide_mark()
        self.set_colour("green")
        self.show_panel()
        self.setToolTip(self._PLACING_TIP)
        # Windows only obeys a move request from the window in front, so the
        # circle is put in front. Without this the first drag did nothing.
        self.activateWindow()

    def stop_placing(self, keep=False):
        if keep:
            self.remember_position()
        elif self._placing_from:
            x, y = S.fit_rect(self._placing_from[0], self._placing_from[1],
                              self.width(), self.height())
            self.move(x, y)
        self._placing = False
        self._placing_from = None
        self.setToolTip(self._NORMAL_TIP)
        self._badges.hide()
        self._hide_mark()
        self.hide()

    # ── clicking versus dragging ────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.globalPosition().toPoint()

    def mouseMoveEvent(self, ev):
        if self._press is None:
            return
        moved = (ev.globalPosition().toPoint() - self._press).manhattanLength()
        if moved > 4:
            # Windows gives a frameless window no move of its own, and asking
            # Windows to do it beats moving it by hand: the pointer stays glued
            # to the spot it grabbed.
            self._press = None
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            QTimer.singleShot(0, self._place_mark)
            QTimer.singleShot(0, self._place_badges)

    def mouseReleaseEvent(self, ev):
        if self._press is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._press = None
            if self._placing:
                # While it is being placed a click is a click, not an order.
                # `clicked` stops watching and brings the window back, which is
                # not what a press on something you are dragging should do.
                return
            self.clicked.emit()

    def moveEvent(self, ev):
        # Guarded for the same reason as `AlarmWindow.resizeEvent`: this
        # re-enumerates the screens and restyles the badges, it fires on every
        # step of a drag, and an exception escaping a Qt override poisons the
        # whole process rather than this one move.
        try:
            if self._mark_visible():
                self._place_mark()
            self._place_badges()
        except Exception:
            pass
        super().moveEvent(ev)
