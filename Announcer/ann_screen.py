r"""Announcer — the screens: where they are, grabbing them, comparing them.

TWO COORDINATE SPACES, ONE BOUNDARY
-----------------------------------
Measured on this PC on 2026-09-16, with PySide6 6.11.1:

    screen        Qt logical geometry      dpr   Windows says
    \\.\DISPLAY1  0,0    1280x720          1.5   0,0    1920x1080
    Q27P3C        1920,-174 2560x1440      1.0   1920,-174 2560x1440
    Q27P4U        4480,-174 2560x1440      1.0   4480,-174 2560x1440

Read those three lines carefully, because everything here follows from them:

  * Qt 6 is per-monitor DPI aware out of the box (awareness level 2 — measured,
    not assumed), so its geometry is in LOGICAL pixels.
  * A screen's ORIGIN is the same number in both spaces. Its SIZE is not.
    The laptop is 1280 logical wide and 1920 physical wide, while the next
    screen starts at 1920 in both — so Qt's logical desktop has a 640 px HOLE
    in the middle of it. The union of `QScreen.geometry()` is therefore not a
    coordinate space and must never be treated as one: always pick a screen
    first, then work inside it.
  * `PIL.ImageGrab` is unconditionally physical: `grab(all_screens=True)` comes
    back 7040x1440 for a virtual desktop that starts at y = -174, whatever the
    process's DPI awareness. It takes ABSOLUTE screen coordinates and works the
    virtual-desktop offset out itself — do not subtract it by hand. Doing that
    once moved every shot 174 px down.
  * Windows' own `GetMonitorInfoW`, in this DPI-aware process, agrees with the
    derived physical rectangles exactly. So the ctypes block the tkinter version
    needed is gone: Qt's own numbers are enough.

THE RULE, then, in one line: **a watched rectangle is always physical pixels**,
and Qt's logical pixels exist only inside widgets. Conversion happens in
`to_physical` / `to_logical` and nowhere else.

That also means every rectangle the tkinter version saved still works. It was
physical too, by accident: `screeninfo.get_monitors()` calls
`SetProcessDpiAwareness(2)` inside itself, and the old program called it at
start-up, so it had become DPI-aware before it ever drew a selector.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QLabel, QWidget

DEFAULT_THRESHOLD = 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Where the screens are
# ─────────────────────────────────────────────────────────────────────────────

class ScreenInfo:
    """One screen in both spaces at once, so nothing has to guess which it has."""
    __slots__ = ("index", "name", "logical", "physical", "available", "dpr",
                 "primary")

    def __init__(self, index, screen):
        g = screen.geometry()
        self.index = index
        self.name = screen.name()
        self.dpr = float(screen.devicePixelRatio())
        self.logical = QRect(g)
        self.available = QRect(screen.availableGeometry())
        # Origin physical, size logical x dpr — verified against Windows'
        # own numbers for all three screens on this PC.
        self.physical = QRect(g.x(), g.y(),
                              round(g.width() * self.dpr),
                              round(g.height() * self.dpr))
        self.primary = screen is QGuiApplication.primaryScreen()

    @property
    def label(self):
        """What the operator sees in a dropdown."""
        p = self.physical
        star = " (main)" if self.primary else ""
        return f"Monitor {self.index + 1} — {p.width()}x{p.height()}{star}"

    def __repr__(self):
        return f"ScreenInfo({self.index}, {self.name!r}, {self.physical})"


def screens():
    """Every screen, asked for fresh.

    Asked fresh every time on purpose. A monitor can be unplugged, put to sleep
    or rearranged between two reads, and a remembered list would then place a
    window on a screen that is not there.
    """
    return [ScreenInfo(i, s) for i, s in enumerate(QGuiApplication.screens())]


def screen_for_physical(point, infos=None):
    """The screen a physical point is on, or None.

    None is a real answer: a remembered rectangle can point at a screen that has
    since been unplugged, and pretending otherwise is how a window ends up
    somewhere nobody can reach it.
    """
    x, y = (point.x(), point.y()) if hasattr(point, "x") else (point[0], point[1])
    for s in (infos if infos is not None else screens()):
        if s.physical.contains(int(x), int(y)):
            return s
    return None


def screen_for_region(region, infos=None):
    """The screen a watched rectangle sits on — the one it covers most of."""
    if not region:
        return None
    infos = infos if infos is not None else screens()
    rect = QRect(QPoint(int(region[0]), int(region[1])),
                 QPoint(int(region[2]), int(region[3])))
    best, best_area = None, 0
    for s in infos:
        cut = s.physical.intersected(rect)
        area = max(0, cut.width()) * max(0, cut.height())
        if area > best_area:
            best, best_area = s, area
    return best


def to_physical(logical_point, info):
    """A Qt logical point on one screen as a physical screen pixel."""
    ox, oy = info.logical.x(), info.logical.y()
    return QPoint(round(ox + (logical_point.x() - ox) * info.dpr),
                  round(oy + (logical_point.y() - oy) * info.dpr))


def to_logical(physical_point, info):
    """The way back, for drawing a stored rectangle on screen."""
    ox, oy = info.logical.x(), info.logical.y()
    return QPoint(round(ox + (physical_point.x() - ox) / info.dpr),
                  round(oy + (physical_point.y() - oy) / info.dpr))


def fit_rect(x, y, w, h, infos=None):
    """Pull a window rectangle onto a screen that exists. Logical pixels.

    Two behaviours worth keeping, neither of which Qt offers:

    * the screen chosen is the one the window already covers MOST of, so a
      window left on the second monitor stays on the second monitor;
    * the clamping order matters. `min` before `max` means a window LARGER than
      its screen is pinned to the top-left corner instead of being pushed off
      the opposite edge, where its title bar would be unreachable.
    """
    infos = infos if infos is not None else screens()
    if not infos:
        return x, y
    rect = QRect(int(x), int(y), max(1, int(w)), max(1, int(h)))
    best, best_area = None, -1
    for s in infos:
        cut = s.available.intersected(rect)
        area = max(0, cut.width()) * max(0, cut.height())
        if area > best_area:
            best, best_area = s, area
    if best_area <= 0:
        best = next((s for s in infos if s.primary), infos[0])
    area = best.available
    nx = min(max(int(x), area.x()), area.x() + area.width() - rect.width())
    ny = min(max(int(y), area.y()), area.y() + area.height() - rect.height())
    nx = max(nx, area.x())
    ny = max(ny, area.y())
    return nx, ny


def place_on_screen(widget, x=None, y=None):
    """Move a window to a remembered spot, pulled back onto a real screen.

    A remembered position is the top-left of the window's INSIDE, which is what
    Qt's `move()` sets and what `geometry()` reports — so unlike the tkinter
    version there is nothing to measure and correct here. The drift that used to
    walk a window off the screen a title bar at a time came from tk storing one
    and Windows wanting the other.
    """
    if x is None or y is None:
        return
    w = max(1, widget.width() or widget.sizeHint().width())
    h = max(1, widget.height() or widget.sizeHint().height())
    nx, ny = fit_rect(x, y, w, h)
    widget.move(nx, ny)


# ─────────────────────────────────────────────────────────────────────────────
# Grabbing and comparing
# ─────────────────────────────────────────────────────────────────────────────

def grab_rect(region):
    """A picture of one physical rectangle of the desktop: (image, error).

    `all_screens=True` is not optional — without it PIL photographs the primary
    monitor only, and every rectangle on another screen comes back wrong.

    Two things this cannot tell apart from a real change, both worth knowing:
    a LOCKED session photographs as the lock screen or as solid black, and a
    SLEEPING monitor photographs as black. Either one looks like the watched
    picture changed, which is one more reason the alarm fires once and waits.
    """
    if not region:
        return None, "no rectangle"
    try:
        from PIL import ImageGrab
        bbox = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
        if bbox[2] - bbox[0] < 1 or bbox[3] - bbox[1] < 1:
            return None, "rectangle has no area"
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        return img, None
    except Exception as exc:
        return None, f"screenshot failed: {exc}"


def grab_regions(regions):
    """Pictures of several rectangles out of ONE photograph of the desktop.

    Takes an iterable of `[x1, y1, x2, y2]` and answers a list of
    `(image, error)` in the same order, each one exactly the picture `grab_rect`
    would have given for that rectangle.

    Why this exists: asking for a small rectangle is not a small job. On Windows
    PIL photographs the WHOLE virtual desktop and crops afterwards, so one
    300x200 rectangle costs the same as everything on every screen — measured on
    this PC 2026-09-17, 269 ms and a 30 MB buffer per call at 7040x1440. The
    watcher asked for that once per watched area, twice a second: four areas did
    not fit in the half second they had, and churned 120 MB a second doing it.
    One photograph of the bounding box of all of them answers every one, and the
    bounding box is what PIL was going to photograph anyway.
    """
    regions = list(regions)
    if not regions:
        return []

    def drawn(r):
        return bool(r) and (int(r[2]) - int(r[0]) >= 1
                            and int(r[3]) - int(r[1]) >= 1)

    usable = [r for r in regions if drawn(r)]
    if not usable:
        return [grab_rect(r) for r in regions]
    x0 = min(int(r[0]) for r in usable)
    y0 = min(int(r[1]) for r in usable)
    big, err = grab_rect([x0, y0,
                          max(int(r[2]) for r in usable),
                          max(int(r[3]) for r in usable)])
    if err is not None:
        return [(None, err) for _ in regions]
    out = []
    for r in regions:
        if not r:
            out.append((None, "no rectangle"))
        elif not drawn(r):
            out.append((None, "rectangle has no area"))
        else:
            try:
                out.append((big.crop((int(r[0]) - x0, int(r[1]) - y0,
                                      int(r[2]) - x0, int(r[3]) - y0)), None))
            except Exception as exc:
                out.append((None, f"screenshot failed: {exc}"))
    return out


def picture_diff(now, reference):
    """How far two pictures are apart: the mean absolute deviation, 0-255.

    The average of the per-channel means, which is the number the operator sets
    the sensitivity against. Pure PIL, no numpy — one more dependency for a
    comparison of a few hundred pixels would not pay for itself.

    Returns None when the two cannot be compared at all; the caller decides what
    that means, and for a watched area it means the size changed, which is a
    trip and not a shrug.

    The size is checked HERE and not left to PIL. Measured with Pillow 12.2.0:
    `ImageChops.difference` on two different sizes raises nothing — it silently
    crops to the smaller one and hands back a perfectly plausible number. So a
    rectangle whose screen had been rescaled underneath it would have been
    compared against the wrong pixels and reported as calm, which is the one
    failure that looks exactly like success.
    """
    try:
        if now.size != reference.size:
            return None
        from PIL import ImageChops, ImageStat
        means = ImageStat.Stat(ImageChops.difference(now, reference)).mean
        return sum(means) / len(means)
    except Exception:
        return None


def pil_to_qpixmap(img):
    """A PIL image as a QPixmap, for a thumbnail or a preview."""
    try:
        rgb = img.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3,
                      QImage.Format.Format_RGB888)
        # copy(), because the QImage above borrows `data`, which is a temporary.
        return QPixmap.fromImage(qimg.copy())
    except Exception:
        return QPixmap()


def scaled_pixmap(img, max_w, max_h):
    """A thumbnail that keeps single pixels visible.

    Nearest-neighbour on purpose: a watched area is often one small number or a
    thin indicator lamp, and smoothing it away is exactly the detail the
    operator is trying to look at.
    """
    pm = pil_to_qpixmap(img)
    if pm.isNull():
        return pm
    if pm.width() <= max_w and pm.height() <= max_h:
        return pm
    return pm.scaled(int(max_w), int(max_h),
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.FastTransformation)


def framed_pixmap(img, max_w, max_h):
    """A thumbnail with its own edge drawn round it.

    The picture sits centred in a box bigger than itself, so where the picture
    stops and the box begins is invisible — a white dialog photographed onto a
    white label has no edge at all, and the operator cannot tell what was
    actually inside the rectangle they dragged out.

    So the edge is drawn, not left to the picture: one pale line hard against
    the picture and one black line outside it. Two lines rather than one because
    a single black line disappears into dark content and a single white one
    disappears into the label behind it; this pair shows up on either.
    """
    inner = scaled_pixmap(img, int(max_w) - 4, int(max_h) - 4)
    if inner.isNull():
        return inner
    out = QPixmap(inner.width() + 4, inner.height() + 4)
    out.fill(QColor("#f0f0f0"))
    p = QPainter(out)
    try:
        p.drawPixmap(2, 2, inner)
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawRect(0, 0, out.width() - 1, out.height() - 1)
    finally:
        p.end()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dragging a rectangle out on the screen
# ─────────────────────────────────────────────────────────────────────────────

class _SelectorOverlay(QWidget):
    """The dimming layer over ONE screen, with the rubber band drawn on it.

    One widget per screen and everything painted in a single `paintEvent`: the
    dim wash, the bright border, and the caption. The tkinter version needed two
    stacked windows for this, because Win32 per-window alpha multiplies
    everything in the window, so a border drawn on a 15 %-opaque overlay came out
    15 % opaque too and was invisible against a bright display. Qt paints real
    per-pixel alpha, so an opaque border on a translucent wash is just a border.
    """

    picked = Signal(object, object)      # (physical QRect, ScreenInfo)
    cancelled = Signal()

    _BORDER = 3

    def __init__(self, info, instruction):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self._info = info
        self._instruction = instruction
        self._origin = None
        self._band = QRect()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setGeometry(info.logical)

    # ── painting ────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        """Opens the painter and closes it whatever the body does.

        The body is separate and the `finally` is not decoration: an exception
        escaping a paintEvent leaves the QPainter active on the widget, Qt then
        prints `QBackingStore::endPaint() called with active painter` on every
        repaint, and the program dies on the next move with `QPaintDevice:
        Cannot destroy paint device that is being painted`. One broken overlay
        must cost a blank frame, not the program.
        """
        p = QPainter(self)
        try:
            self._paint_body(p)
        finally:
            p.end()

    def _paint_body(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # A wash dark enough to say "the program is waiting" and light enough
        # that the thing being framed is still readable underneath.
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if not self._band.isNull():
            # The inside of the band is left alone, so the rectangle being
            # chosen is shown at full brightness while it is chosen.
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self._band, QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor("#ff2d2d"), self._BORDER))
            p.drawRect(self._band.adjusted(0, 0, -1, -1))
            size = (f"{round(self._band.width() * self._info.dpr)}"
                    f" x {round(self._band.height() * self._info.dpr)} px")
            self._draw_caption(p, size, self._band.center().x(),
                               max(14, self._band.top() - 16))
        else:
            self._draw_caption(p, self._instruction, self.width() // 2,
                               int(self.height() * 0.08))

    def _draw_caption(self, p, text, cx, cy):
        """White ink on a dark plate — never ink alone over an unknown desktop."""
        font = QFont("Segoe UI", 12, QFont.Weight.DemiBold)
        p.setFont(font)
        rect = p.fontMetrics().boundingRect(text)
        rect.moveCenter(QPoint(int(cx), int(cy)))
        plate = rect.adjusted(-10, -6, 10, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20, 225))
        p.drawRoundedRect(plate, 4, 4)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    # ── the drag ────────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        # Take the keyboard as well as the mouse. Three overlays are shown and
        # only one of them can be focused; without this, Esc after a drag begun
        # on another screen goes to whichever overlay was activated first, and
        # the operator is left with no way out of a dimmed desktop.
        self.activateWindow()
        self.setFocus()
        self._origin = ev.position().toPoint()
        self._band = QRect(self._origin, self._origin)
        self.update()

    def mouseMoveEvent(self, ev):
        if self._origin is None:
            return
        # Clamped to the screen the drag started on. A watched rectangle is a
        # rectangle OF a screen, and a drag that crossed from a 150 % screen to
        # a 100 % one would have no single scale to convert with.
        pos = ev.position().toPoint()
        pos.setX(max(0, min(pos.x(), self.width() - 1)))
        pos.setY(max(0, min(pos.y(), self.height() - 1)))
        self._band = QRect(self._origin, pos).normalized()
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        band = self._band.normalized()
        self._origin = None
        self._band = QRect()
        # A stray click is not a rectangle. The old version used 5 px; the same
        # number here, in logical pixels, so a twitch selects nothing rather
        # than a rectangle nobody meant.
        if band.width() < 5 or band.height() < 5:
            self.cancelled.emit()
            return
        top_left = to_physical(self.mapToGlobal(band.topLeft()), self._info)
        bottom_right = to_physical(self.mapToGlobal(band.bottomRight()), self._info)
        self.picked.emit(QRect(top_left, bottom_right), self._info)

    def keyPressEvent(self, ev):
        # `isAutoRepeat` matters: a held Escape emitted `cancelled` thirty times
        # a second, and every one of them armed another "bring the windows back"
        # timer, so the dialog re-showed and re-activated itself in a burst.
        if ev.key() == Qt.Key.Key_Escape and not ev.isAutoRepeat():
            self.cancelled.emit()


# A shown overlay must never be reachable only through the caller's variable.
# `_SelectorOverlay` is a parentless QWidget, so Python owns its C++ object:
# dropping the last Python reference destroys a VISIBLE top-level window there
# and then, inside whatever was running, and Qt faults on the next event.
# `AreasTab`/`AreaDialog` kept the selector in one attribute that the next
# "Draw the area" press overwrote — two clicks on that button were enough. The
# selector now holds itself here for exactly as long as its overlays are up.
_LIVE_SELECTORS = set()

# And a second holding pen, for things on their way out. See `retire`.
_RETIRED = set()


def selection_in_progress():
    """True while a rectangle is being dragged out somewhere on the desktop."""
    return bool(_LIVE_SELECTORS)


def retire(obj):
    """`deleteLater()`, and keep the Python reference until Qt has really gone.

    This is the exact shape of the crash that was measured out of three Windows
    minidumps on 2026-09-17. All three faulted on the GUI thread inside
    `QCoreApplicationPrivate::sendPostedEvents`, and in one of them at the
    precise instruction that touches `pe.receiver->d_func()` — with the pointer
    reading `0x16`. Small integers and, in another dump, the bit pattern of the
    double 1.0: freed memory that had already been handed out again, not a null
    and not something uninitialised. Qt was delivering a queued event to a
    receiver that no longer existed.

    How a program gets into that state:

        ov.deleteLater()        # queues a DeferredDelete event FOR ov
        self._overlays = []     # drops the last Python reference to ov

    `hide()` and `deleteLater()` both POST events addressed to that widget. If
    the Python wrapper is collected on the next line, shiboken can free the C++
    object straight away — and the events already sitting in the queue are now
    addressed to freed memory. The fault arrives later, in the event loop,
    nowhere near the line that caused it, which is why it looked random.

    So: hand the object over here instead of calling `deleteLater` yourself.
    `destroyed` fires when Qt has actually destroyed it, and not before, which
    is the only moment at which letting go of the reference is safe.
    """
    if obj is None:
        return
    _RETIRED.add(obj)
    obj.destroyed.connect(lambda *_a, o=obj: _RETIRED.discard(o))
    obj.deleteLater()


class RegionSelector(QObject):
    """Drag a rectangle out anywhere on any screen.

    Every screen is covered, not just one, so there is no monitor to pick first
    — the operator simply drags where the thing is. The old version dimmed one
    chosen monitor and left the others bright, which meant choosing the monitor
    in a dropdown before you could even see it.

    `on_picked(region, screen)` gets a physical `[x1, y1, x2, y2]` and the
    `ScreenInfo` it came from. `on_done()` always runs, cancelled or not, which
    is what brings the hidden windows back.
    """

    # Both ways out are named. A single click anywhere already cancels — a drag
    # under five pixels is not a rectangle — and that is the way out that works
    # even when Esc is going to some other window.
    INSTRUCTION = "Drag out the area to watch  ·  Esc or a single click cancels"

    # However long the operator stares at it, a dimmed desktop with no window
    # anywhere is a dead end, so it gives itself up. Generous on purpose: this
    # is a safety net for "Esc went to the wrong window", not a time limit on
    # deciding where to drag.
    GIVE_UP_MS = 120_000

    def __init__(self, on_picked, on_done=None,
                 instruction=INSTRUCTION):
        super().__init__(None)
        self._on_picked = on_picked
        self._on_done = on_done
        self._closed = False
        self._overlays = []
        for info in screens():
            ov = _SelectorOverlay(info, instruction)
            ov.picked.connect(self._picked)
            ov.cancelled.connect(self._cancelled)
            self._overlays.append(ov)
        self._give_up = QTimer(self)
        self._give_up.setSingleShot(True)
        self._give_up.timeout.connect(self._cancelled)

    def show(self):
        """Cover every screen. Always ends in `on_done`, one way or another."""
        if self._closed:
            return
        if not self._overlays:
            # No screens at all. Without this the caller's windows stayed
            # hidden for ever, waiting for a signal nothing would ever emit.
            self._closed = True
            if self._on_done:
                QTimer.singleShot(0, self._on_done)
            return
        _LIVE_SELECTORS.add(self)
        for ov in self._overlays:
            ov.show()
            ov.raise_()
        self._overlays[0].activateWindow()
        self._overlays[0].setFocus()
        self._give_up.start(self.GIVE_UP_MS)

    def take_down(self):
        """Close the overlays and tell nobody. For an owner that is going away.

        The owner's `on_done` normally brings its windows back — which is
        meaningless once the owner itself has closed, and on a dialog that has
        already finished it reaches into a widget Qt is done with.
        """
        self._on_picked = None
        self._on_done = None
        self._close()

    def _picked(self, rect, info):
        if self._closed:
            return                      # a second overlay answering the same drag
        region = [rect.left(), rect.top(), rect.right(), rect.bottom()]
        self._close()
        if self._on_picked:
            self._on_picked(region, info)
        if self._on_done:
            self._on_done()

    def _cancelled(self):
        if self._closed:
            return
        self._close()
        if self._on_done:
            self._on_done()

    def _close(self):
        """Take the overlays down. Idempotent, and safe from inside an overlay.

        `retire` rather than `deleteLater` — read the note on it. `hide()` posts
        events addressed to the overlay, so dropping the last Python reference
        here is what left Qt delivering queued events into freed memory.
        """
        self._closed = True
        self._give_up.stop()
        for ov in self._overlays:
            ov.hide()
            retire(ov)
        self._overlays = []
        _LIVE_SELECTORS.discard(self)
        retire(self)


# ─────────────────────────────────────────────────────────────────────────────
# Which screen is which
# ─────────────────────────────────────────────────────────────────────────────

class MonitorNumbers:
    """Put a big number on every screen for a couple of seconds.

    The numbers in the program have to mean something on the desk, and no
    operator knows which of three identical panels Windows calls DISPLAY2.
    """

    SHOW_MS = 2500

    def __init__(self):
        self._labels = []

    def show(self):
        self.hide()
        for info in screens():
            lbl = QLabel(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
            p = info.physical
            lbl.setText(f"Monitor {info.index + 1}\n{p.width()} x {p.height()}"
                        + ("\n(main)" if info.primary else ""))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Both colours stated. This PC runs Windows in dark mode, so a label
            # that sets neither comes out black ink on a black plate.
            lbl.setStyleSheet("QLabel { background: #101010; color: #ffffff;"
                              " border: 2px solid #ffffff; border-radius: 8px;"
                              " padding: 24px 34px;"
                              " font-family: 'Segoe UI'; font-size: 34px;"
                              " font-weight: 700; }")
            lbl.adjustSize()
            g = info.logical
            lbl.move(g.x() + (g.width() - lbl.width()) // 2,
                     g.y() + (g.height() - lbl.height()) // 2)
            lbl.setWindowOpacity(0.9)
            lbl.show()
            lbl.raise_()
            self._labels.append(lbl)
        QTimer.singleShot(self.SHOW_MS, self.hide)

    def hide(self):
        # `retire`, not `deleteLater`: these are parentless top-level labels and
        # this is the same drop-the-last-reference-too-early shape that was
        # measured out of the crash dumps. See `retire`.
        for lbl in self._labels:
            lbl.hide()
            retire(lbl)
        self._labels = []
