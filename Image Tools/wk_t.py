# wk_t.py — Workshop tab for Image Tools
# Receives images from Image Finder / Image Slider / Shot Finder.
# Features: brightness/contrast, crop, zoom/pan, draw (brush/line/rect/text/eraser),
#           reference diff (pixel-by-pixel), undo/redo, save PNG/TIFF.

import os
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
from PIL import Image as _PilImg

from PySide6.QtCore import (
    Qt, QPoint, QPointF, QRect, QRectF, QSize, Signal, QObject,
)
from PySide6.QtGui import (
    QPixmap, QImage, QColor, QPainter, QPen, QBrush, QFont, QFontMetrics,
    QPainterPath, QCursor, QIcon, QKeyEvent,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QFileDialog,
    QMessageBox, QSizePolicy, QScrollArea, QFrame, QToolButton, QButtonGroup,
    QAbstractButton, QColorDialog, QSplitter, QToolBar,
    QStatusBar, QGroupBox,
)


# ─────────────────────────────────────────────────────────────────
#  Gradient / palette LUTs  (same definitions as in if_t.py)
# ─────────────────────────────────────────────────────────────────

def _wk_make_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]; t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                lut[i] = tuple(int(c0[k] + f * (c1[k] - c0[k])) for k in range(3))
                break
    return lut

def _wk_make_binary_lut():
    lut = np.zeros((256, 3), dtype=np.uint8)
    lut[128:] = 255
    return lut

def _wk_make_stepped_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            if t < stops[j + 1][0]:
                color = stops[j][1]
                break
        lut[i] = color
    return lut

WK_GRADIENTS: dict[str, "np.ndarray | None"] = {
    "Grayscale":       None,
    "Gradient":        _wk_make_lut([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Hot":             _wk_make_lut([(0,(0,0,0)),(0.33,(255,0,0)),(0.66,(255,255,0)),(1,(255,255,255))]),
    "Binary":          _wk_make_stepped_lut([(0,(0,0,0)),(0.17,(255,0,0)),(0.33,(255,165,0)),(0.5,(255,255,0)),(0.67,(0,255,0)),(0.83,(0,200,255)),(0.92,(0,0,255)),(1,(255,255,255))]),
    "Black and White": _wk_make_binary_lut(),
    "Viridis":         _wk_make_lut([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _wk_make_lut([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _wk_make_lut([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _wk_make_lut([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _wk_make_lut([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}

# ─────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────

def _np_to_qimage(arr: np.ndarray) -> QImage:
    """Convert HxW uint8 (grayscale) or HxWx3 uint8 (RGB) ndarray to QImage."""
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        return QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    else:
        h, w = arr.shape[:2]
        return QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def _qimage_to_np(img: QImage) -> np.ndarray:
    """Convert QImage to HxWx3 uint8 RGB ndarray."""
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3)).copy()
    return arr


def _arr_to_pil(arr: np.ndarray) -> _PilImg.Image:
    if arr.ndim == 2:
        return _PilImg.fromarray(arr, mode="L")
    return _PilImg.fromarray(arr, mode="RGB")


# ─────────────────────────────────────────────────────────────────
#  WorkshopSlot — holds the edit stack for one image slot
# ─────────────────────────────────────────────────────────────────

@dataclass
class _WorkshopSlot:
    label: str = ""
    source_arr: np.ndarray = field(default_factory=lambda: np.zeros((1, 1, 3), np.uint8))
    current_arr: np.ndarray = field(default_factory=lambda: np.zeros((1, 1, 3), np.uint8))
    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    UNDO_LIMIT = 30

    def push_undo(self):
        self.undo_stack.append(self.current_arr.copy())
        if len(self.undo_stack) > self.UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.current_arr.copy())
        self.current_arr = self.undo_stack.pop()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.current_arr.copy())
        self.current_arr = self.redo_stack.pop()
        return True

    def reset_to_source(self):
        self.push_undo()
        self.current_arr = self.source_arr.copy()


# ─────────────────────────────────────────────────────────────────
#  Bresenham line helper
# ─────────────────────────────────────────────────────────────────

def _bresenham(x0, y0, x1, y1):
    pts = []
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy; x0 += sx
        if e2 < dx:
            err += dx; y0 += sy
    return pts


# ─────────────────────────────────────────────────────────────────
#  Canvas widget — zoomed, pannable, draw-capable
# ─────────────────────────────────────────────────────────────────

class WorkshopCanvas(QWidget):
    image_changed = Signal()
    color_picked  = Signal(QColor)

    TOOL_NONE    = "none"
    TOOL_BRUSH   = "brush"
    TOOL_ERASER  = "eraser"
    TOOL_LINE    = "line"
    TOOL_RECT    = "rect"
    TOOL_TEXT    = "text"
    TOOL_CROP    = "crop"
    TOOL_EYEDROP = "eyedrop"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 200)

        self._slot: "_WorkshopSlot | None" = None
        self._qimage: "QImage | None" = None

        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._pan_last: "QPointF | None" = None
        self._pan_active = False

        self.tool = self.TOOL_NONE
        self.draw_color = QColor(255, 0, 0)
        self.brush_size = 6
        self.line_width = 2
        self._draw_last: "QPoint | None" = None
        self._shape_start: "QPoint | None" = None
        self._rubber_end: "QPoint | None" = None

        # Inline text editing state
        self._text_active = False       # currently typing
        self._text_pos: "QPoint | None" = None   # image-space anchor
        self._text_buf = ""             # current text buffer
        self._text_font_size = 16

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── Slot binding ──────────────────────────────────────────────

    def set_slot(self, slot: "_WorkshopSlot"):
        self._slot = slot
        self._rebuild_qimage()
        self.fit_to_view()

    def _rebuild_qimage(self):
        if self._slot is None:
            self._qimage = None
            self.update()
            return
        self._qimage = _np_to_qimage(self._slot.current_arr)
        self.update()

    def refresh(self):
        """Rebuild QImage from slot data; re-fit only if image size changed."""
        if self._slot is None:
            self._rebuild_qimage()
            return
        old_w = self._qimage.width()  if self._qimage else 0
        old_h = self._qimage.height() if self._qimage else 0
        self._rebuild_qimage()
        if self._qimage and (self._qimage.width() != old_w or self._qimage.height() != old_h):
            self.fit_to_view()

    # ── Coordinate helpers ────────────────────────────────────────

    def _img_rect(self) -> QRectF:
        if self._qimage is None:
            return QRectF()
        w = self._qimage.width() * self._zoom
        h = self._qimage.height() * self._zoom
        return QRectF(self._offset.x(), self._offset.y(), w, h)

    def _widget_to_img(self, pt: QPoint) -> QPoint:
        if self._qimage is None:
            return QPoint(0, 0)
        ix = int((pt.x() - self._offset.x()) / self._zoom)
        iy = int((pt.y() - self._offset.y()) / self._zoom)
        ix = max(0, min(self._qimage.width() - 1, ix))
        iy = max(0, min(self._qimage.height() - 1, iy))
        return QPoint(ix, iy)

    def _img_to_widget(self, pt: QPoint) -> QPointF:
        return QPointF(pt.x() * self._zoom + self._offset.x(),
                       pt.y() * self._zoom + self._offset.y())

    def fit_to_view(self):
        if self._qimage is None or self._qimage.isNull():
            return
        iw, ih = self._qimage.width(), self._qimage.height()
        ww, wh = self.width(), self.height()
        if ww <= 0 or wh <= 0:
            return
        self._zoom = min(ww / iw, wh / ih)
        self._offset = QPointF((ww - iw * self._zoom) / 2,
                               (wh - ih * self._zoom) / 2)
        self.update()

    def zoom_in(self):  self._zoom_at(self.rect().center(), 1.25)
    def zoom_out(self): self._zoom_at(self.rect().center(), 0.8)

    def zoom_reset(self):
        if self._qimage is None: return
        self._zoom = 1.0
        ww, wh = self.width(), self.height()
        iw, ih = self._qimage.width(), self._qimage.height()
        self._offset = QPointF((ww - iw) / 2, (wh - ih) / 2)
        self.update()

    def _zoom_at(self, center: QPoint, factor: float):
        old = self._zoom
        self._zoom = max(0.05, min(32.0, self._zoom * factor))
        cx, cy = center.x(), center.y()
        self._offset = QPointF(cx - (cx - self._offset.x()) * self._zoom / old,
                               cy - (cy - self._offset.y()) * self._zoom / old)
        self.update()

    # ── Paint ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#2b2b2b"))
        if self._qimage is None:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No image\n\nUse 'Send to Workshop' in Image Finder,\nImage Slider or Shot Finder.")
            return
        p.drawImage(self._img_rect(), self._qimage)

        # Rubber band preview (line / rect / crop)
        if self._rubber_end is not None and self._shape_start is not None:
            p.save()
            p.setPen(QPen(QColor(255, 255, 0), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            sp = self._img_to_widget(self._shape_start)
            ep = QPointF(self._rubber_end)
            if self.tool in (self.TOOL_RECT, self.TOOL_CROP):
                p.drawRect(QRectF(sp, ep).normalized())
            elif self.tool == self.TOOL_LINE:
                p.drawLine(sp, ep)
            p.restore()

        # Eraser cursor circle
        if self.tool == self.TOOL_ERASER:
            r_widget = max(2, int(self.brush_size * self._zoom))
            cursor_pos = self.mapFromGlobal(self.cursor().pos())
            p.save()
            p.setPen(QPen(QColor(255, 255, 0, 180), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cursor_pos, r_widget, r_widget)
            p.restore()

        # Inline text preview
        if self._text_active and self._text_pos is not None:
            wp = self._img_to_widget(self._text_pos)
            font_px = max(8, int(self._text_font_size * self._zoom))
            f = QFont("Arial", font_px)
            p.save()
            p.setFont(f)
            p.setPen(QPen(self.draw_color))
            display = self._text_buf + "|"
            p.drawText(int(wp.x()), int(wp.y()), display)
            p.restore()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._qimage is not None:
            self.fit_to_view()

    # ── Mouse ─────────────────────────────────────────────────────

    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self._zoom_at(e.position().toPoint(), factor)

    def mousePressEvent(self, e):
        if self._slot is None:
            return
        if e.button() == Qt.MouseButton.MiddleButton or (
            e.button() == Qt.MouseButton.LeftButton and
            e.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._pan_active = True
            self._pan_last = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        img_pt = self._widget_to_img(e.position().toPoint())
        if self.tool == self.TOOL_BRUSH:
            self._slot.push_undo()
            self._draw_last = img_pt
            self._paint_brush(img_pt, img_pt)
        elif self.tool == self.TOOL_ERASER:
            self._slot.push_undo()
            self._draw_last = img_pt
            self._paint_eraser(img_pt, img_pt)
        elif self.tool in (self.TOOL_LINE, self.TOOL_RECT, self.TOOL_CROP):
            self._shape_start = img_pt
            self._rubber_end = e.position().toPoint()
        elif self.tool == self.TOOL_TEXT:
            if self._text_active:
                self._commit_text()
            self._text_pos = img_pt
            self._text_buf = ""
            self._text_active = True
            self.setFocus()
            self.update()
        elif self.tool == self.TOOL_EYEDROP:
            self._pick_color(img_pt)

    def mouseMoveEvent(self, e):
        if self._pan_active and self._pan_last is not None:
            delta = e.position() - self._pan_last
            self._offset += delta
            self._pan_last = e.position()
            self.update()
            return
        if self._slot is None:
            return
        img_pt = self._widget_to_img(e.position().toPoint())
        if e.buttons() & Qt.MouseButton.LeftButton:
            if self.tool == self.TOOL_BRUSH and self._draw_last is not None:
                self._paint_brush(self._draw_last, img_pt)
                self._draw_last = img_pt
            elif self.tool == self.TOOL_ERASER and self._draw_last is not None:
                self._paint_eraser(self._draw_last, img_pt)
                self._draw_last = img_pt
            elif self.tool in (self.TOOL_LINE, self.TOOL_RECT, self.TOOL_CROP):
                self._rubber_end = e.position().toPoint()
                self.update()
        # Repaint to move eraser cursor circle
        if self.tool == self.TOOL_ERASER:
            self.update()

    def mouseReleaseEvent(self, e):
        if self._pan_active:
            self._pan_active = False
            self._pan_last = None
            # Restore correct cursor for current tool
            self.setCursor(Qt.CursorShape.OpenHandCursor
                           if self.tool == self.TOOL_NONE
                           else Qt.CursorShape.ArrowCursor)
            return
        if self._slot is None or e.button() != Qt.MouseButton.LeftButton:
            return
        img_pt = self._widget_to_img(e.position().toPoint())
        if self.tool == self.TOOL_LINE and self._shape_start is not None:
            self._slot.push_undo()
            self._paint_line(self._shape_start, img_pt)
            self._shape_start = None; self._rubber_end = None
        elif self.tool == self.TOOL_RECT and self._shape_start is not None:
            self._slot.push_undo()
            self._paint_rect(self._shape_start, img_pt)
            self._shape_start = None; self._rubber_end = None
        elif self.tool == self.TOOL_CROP and self._shape_start is not None:
            self._do_crop(self._shape_start, img_pt)
            self._shape_start = None; self._rubber_end = None
        self._draw_last = None

    def keyPressEvent(self, e):
        k = e.key()
        # While typing text inline, capture all printable keys
        if self._text_active:
            if k == Qt.Key.Key_Return or k == Qt.Key.Key_Enter:
                self._commit_text()
            elif k == Qt.Key.Key_Escape:
                self._text_active = False
                self._text_buf = ""
                self._text_pos = None
                self.update()
            elif k == Qt.Key.Key_Backspace:
                self._text_buf = self._text_buf[:-1]
                self.update()
            else:
                ch = e.text()
                if ch and ch.isprintable():
                    self._text_buf += ch
                    self.update()
            return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal): self.zoom_in()
        elif k == Qt.Key.Key_Minus: self.zoom_out()
        elif k == Qt.Key.Key_0: self.fit_to_view()
        elif k == Qt.Key.Key_1: self.zoom_reset()

    # ── Drawing operations ────────────────────────────────────────

    def _ensure_rgb(self):
        if self._slot.current_arr.ndim == 2:
            self._slot.current_arr = np.stack(
                [self._slot.current_arr] * 3, axis=2)

    def _color_arr(self):
        return [self.draw_color.red(), self.draw_color.green(),
                self.draw_color.blue()]

    def _paint_brush(self, p0: QPoint, p1: QPoint):
        self._ensure_rgb()
        arr = self._slot.current_arr
        color = self._color_arr()
        r = max(1, self.brush_size // 2)
        h, w = arr.shape[:2]
        for x, y in _bresenham(p0.x(), p0.y(), p1.x(), p1.y()):
            arr[max(0, y-r):min(h, y+r+1),
                max(0, x-r):min(w, x+r+1)] = color
        self._rebuild_qimage()
        self.image_changed.emit()

    def _paint_eraser(self, p0: QPoint, p1: QPoint):
        """Restore pixels to source_arr value under eraser."""
        self._ensure_rgb()
        arr = self._slot.current_arr
        src = self._slot.source_arr
        # Ensure source is also RGB and same size as current
        if src.ndim == 2:
            src = np.stack([src] * 3, axis=2)
        # If current was cropped smaller than source, clamp coordinates to current size
        h, w = arr.shape[:2]
        sh, sw = src.shape[:2]
        r = max(1, self.brush_size)
        for x, y in _bresenham(p0.x(), p0.y(), p1.x(), p1.y()):
            y0, y1 = max(0, y-r), min(h, y+r+1)
            x0, x1 = max(0, x-r), min(w, x+r+1)
            # Map current coords to source coords (1:1 if not cropped, else clamp)
            sy0, sy1 = min(y0, sh), min(y1, sh)
            sx0, sx1 = min(x0, sw), min(x1, sw)
            block_h = sy1 - sy0
            block_w = sx1 - sx0
            if block_h > 0 and block_w > 0:
                arr[y0:y0+block_h, x0:x0+block_w] = src[sy0:sy1, sx0:sx1]
        self._rebuild_qimage()
        self.image_changed.emit()

    def _paint_line(self, p0: QPoint, p1: QPoint):
        self._ensure_rgb()
        arr = self._slot.current_arr
        color = self._color_arr()
        lw = max(1, self.line_width)
        r = lw // 2
        h, w = arr.shape[:2]
        for x, y in _bresenham(p0.x(), p0.y(), p1.x(), p1.y()):
            arr[max(0, y-r):min(h, y+r+1),
                max(0, x-r):min(w, x+r+1)] = color
        self._rebuild_qimage()
        self.image_changed.emit()

    def _paint_rect(self, p0: QPoint, p1: QPoint):
        self._ensure_rgb()
        arr = self._slot.current_arr
        color = self._color_arr()
        lw = max(1, self.line_width)
        r = lw // 2
        x0, x1 = sorted([p0.x(), p1.x()])
        y0, y1 = sorted([p0.y(), p1.y()])
        h, w = arr.shape[:2]
        # top / bottom
        for y in range(max(0, y0-r), min(h, y0+r+1)):
            arr[y, max(0, x0):min(w, x1+1)] = color
        for y in range(max(0, y1-r), min(h, y1+r+1)):
            arr[y, max(0, x0):min(w, x1+1)] = color
        # left / right
        for x in range(max(0, x0-r), min(w, x0+r+1)):
            arr[max(0, y0):min(h, y1+1), x] = color
        for x in range(max(0, x1-r), min(w, x1+r+1)):
            arr[max(0, y0):min(h, y1+1), x] = color
        self._rebuild_qimage()
        self.image_changed.emit()

    def _commit_text(self):
        if not self._text_buf or self._text_pos is None or self._slot is None:
            self._text_active = False
            self._text_buf = ""
            self._text_pos = None
            self.update()
            return
        self._slot.push_undo()
        self._ensure_rgb()
        img = _np_to_qimage(self._slot.current_arr)
        p = QPainter(img)
        p.setFont(QFont("Arial", self._text_font_size))
        p.setPen(QPen(self.draw_color))
        p.drawText(self._text_pos.x(), self._text_pos.y(), self._text_buf)
        p.end()
        self._slot.current_arr = _qimage_to_np(img)
        self._text_active = False
        self._text_buf = ""
        self._text_pos = None
        self._rebuild_qimage()
        self.image_changed.emit()

    def _pick_color(self, pt: QPoint):
        arr = self._slot.current_arr
        if arr.ndim == 2:
            v = int(arr[pt.y(), pt.x()])
            col = QColor(v, v, v)
        else:
            r, g, b = int(arr[pt.y(), pt.x(), 0]), int(arr[pt.y(), pt.x(), 1]), int(arr[pt.y(), pt.x(), 2])
            col = QColor(r, g, b)
        self.draw_color = col
        self.color_picked.emit(col)

    def _do_crop(self, p0: QPoint, p1: QPoint):
        x0, x1 = sorted([p0.x(), p1.x()])
        y0, y1 = sorted([p0.y(), p1.y()])
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._slot.push_undo()
        self._slot.current_arr = self._slot.current_arr[y0:y1, x0:x1].copy()
        self.refresh()
        self.image_changed.emit()


# ─────────────────────────────────────────────────────────────────
#  WorkshopWidget — the full tab
# ─────────────────────────────────────────────────────────────────

_TOOL_BTN_STYLE = (
    "QToolButton { padding: 5px 10px; border: 1px solid #bbb; border-radius: 3px; "
    "background: #e8e8e8; font-size: 12px; }"
    "QToolButton:checked { background: #3a7ebf; color: #fff; border-color: #2060a0; }"
    "QToolButton:hover { background: #d0e4ff; }"
)
_BTN_STYLE = (
    "QPushButton { padding: 4px 10px; border: 1px solid #bbb; border-radius: 3px; background: #e8e8e8; }"
    "QPushButton:hover { background: #d0e4ff; }"
    "QPushButton:pressed { background: #b0c8ef; }"
)
_DANGER_STYLE = (
    "QPushButton { padding: 4px 10px; border: 1px solid #c55; border-radius: 3px; "
    "background: #fdecea; color: #900; }"
    "QPushButton:hover { background: #f8d0cc; }"
)


class WorkshopWidget(QWidget):
    """Workshop tab — receives images, edits them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: list[_WorkshopSlot] = []
        self._active: int = -1
        self._ref: int = -1
        self._bc_preview_active: bool = False
        self._build_ui()

    # ─────────────────────────────────────────────────── UI build ─

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Main area ─────────────────────────────────────────────
        main_row = QHBoxLayout()
        main_row.setSpacing(6)

        # ── LEFT panel ────────────────────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(200)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(2, 2, 4, 2)
        ll.setSpacing(4)

        # Image info at top
        self._info_lbl = QLabel("—")
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet(
            "font-size: 11px; color: #444; background: #e8e8e8; "
            "border: 1px solid #ccc; border-radius: 3px; padding: 3px;")
        ll.addWidget(self._info_lbl)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Images in Workshop"))
        self._slot_list = QComboBox()
        self._slot_list.setToolTip("Select active image")
        self._slot_list.currentIndexChanged.connect(self._on_slot_selected)
        ll.addWidget(self._slot_list)

        slot_btns = QHBoxLayout()
        btn_remove = QPushButton("Remove")
        btn_remove.setStyleSheet(_DANGER_STYLE)
        btn_remove.clicked.connect(self._remove_slot)
        btn_clear = QPushButton("Clear All")
        btn_clear.setStyleSheet(_DANGER_STYLE)
        btn_clear.clicked.connect(self._clear_all)
        slot_btns.addWidget(btn_remove)
        slot_btns.addWidget(btn_clear)
        ll.addLayout(slot_btns)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Tools"))

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._draw_color = QColor(255, 0, 0)

        def _tb(label, tooltip, tool):
            btn = QToolButton()
            btn.setText(label)
            btn.setToolTip(tooltip)
            btn.setCheckable(True)
            btn.setStyleSheet(_TOOL_BTN_STYLE)
            btn.clicked.connect(lambda _checked, t=tool: self._set_tool(t))
            self._tool_group.addButton(btn)
            return btn

        self._btn_tool_none   = _tb("Pan",       "Pan view (Ctrl+drag or middle mouse)",  WorkshopCanvas.TOOL_NONE)
        self._btn_tool_brush  = _tb("Brush",     "Paint with brush",                      WorkshopCanvas.TOOL_BRUSH)
        self._btn_tool_eraser = _tb("Eraser",    "Erase back to original",                WorkshopCanvas.TOOL_ERASER)
        self._btn_tool_line   = _tb("Line",      "Draw straight line",                    WorkshopCanvas.TOOL_LINE)
        self._btn_tool_rect   = _tb("Rect",      "Draw rectangle outline",                WorkshopCanvas.TOOL_RECT)
        self._btn_tool_text   = _tb("Text",      "Click on image, then type; Enter to confirm", WorkshopCanvas.TOOL_TEXT)
        self._btn_tool_crop   = _tb("Crop",      "Crop to selected region",               WorkshopCanvas.TOOL_CROP)
        self._btn_tool_eye    = _tb("Eyedrop",   "Click image to pick draw colour",       WorkshopCanvas.TOOL_EYEDROP)
        self._btn_tool_none.setChecked(True)

        tool_grid = QGridLayout()
        tool_grid.setSpacing(2)
        tool_buttons = [self._btn_tool_none, self._btn_tool_brush,
                        self._btn_tool_eraser, self._btn_tool_line,
                        self._btn_tool_rect, self._btn_tool_text,
                        self._btn_tool_crop, self._btn_tool_eye]
        for i, btn in enumerate(tool_buttons):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tool_grid.addWidget(btn, i // 2, i % 2)
        ll.addLayout(tool_grid)

        # Colour swatch + size row
        color_row = QHBoxLayout(); color_row.setSpacing(4)
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(28, 28)
        self._color_btn.setToolTip("Draw colour (click to change)")
        self._color_btn.clicked.connect(self._pick_draw_color)
        self._update_color_btn()
        color_row.addWidget(self._color_btn)
        color_row.addWidget(QLabel("Brush:"))
        self._brush_sb = QSpinBox()
        self._brush_sb.setRange(1, 200); self._brush_sb.setValue(6)
        self._brush_sb.setFixedWidth(48)
        self._brush_sb.valueChanged.connect(self._on_brush_size)
        color_row.addWidget(self._brush_sb)
        color_row.addWidget(QLabel("Line:"))
        self._line_sb = QSpinBox()
        self._line_sb.setRange(1, 50); self._line_sb.setValue(2)
        self._line_sb.setFixedWidth(40)
        self._line_sb.valueChanged.connect(self._on_line_width)
        color_row.addWidget(self._line_sb)
        ll.addLayout(color_row)

        # Text font size
        txt_row = QHBoxLayout(); txt_row.setSpacing(4)
        txt_row.addWidget(QLabel("Text size:"))
        self._text_size_sb = QSpinBox()
        self._text_size_sb.setRange(6, 120); self._text_size_sb.setValue(16)
        self._text_size_sb.setFixedWidth(52)
        self._text_size_sb.valueChanged.connect(lambda v: setattr(self._canvas, '_text_font_size', v))
        txt_row.addWidget(self._text_size_sb)
        txt_row.addStretch(1)
        ll.addLayout(txt_row)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Undo / History"))
        hist_row = QHBoxLayout()
        btn_undo = QPushButton("↩ Undo")
        btn_undo.setStyleSheet(_BTN_STYLE)
        btn_undo.setShortcut("Ctrl+Z")
        btn_undo.clicked.connect(self._undo)
        btn_reset = QPushButton("↺ Reset")
        btn_reset.setStyleSheet(_DANGER_STYLE)
        btn_reset.setToolTip("Reset to original image (undoable)")
        btn_reset.clicked.connect(self._reset_to_source)
        hist_row.addWidget(btn_undo)
        hist_row.addWidget(btn_reset)
        ll.addLayout(hist_row)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Brightness / Contrast"))

        ll.addWidget(QLabel("Brightness:"))
        self._bright_sl = QSlider(Qt.Orientation.Horizontal)
        self._bright_sl.setRange(-255, 255); self._bright_sl.setValue(0)
        self._bright_sl.valueChanged.connect(self._on_bc_changed)
        ll.addWidget(self._bright_sl)

        ll.addWidget(QLabel("Contrast:"))
        self._contrast_sl = QSlider(Qt.Orientation.Horizontal)
        self._contrast_sl.setRange(-127, 127); self._contrast_sl.setValue(0)
        self._contrast_sl.valueChanged.connect(self._on_bc_changed)
        ll.addWidget(self._contrast_sl)

        self._bc_lbl = QLabel("")
        self._bc_lbl.setStyleSheet("font-size: 10px; color: #666; font-style: italic;")
        ll.addWidget(self._bc_lbl)

        bc_row = QHBoxLayout()
        btn_apply_bc = QPushButton("Apply")
        btn_apply_bc.setStyleSheet(_BTN_STYLE)
        btn_apply_bc.setToolTip("Bake brightness/contrast into the image (undoable)")
        btn_apply_bc.clicked.connect(self._apply_bright_contrast)
        btn_auto_bc = QPushButton("Auto")
        btn_auto_bc.setStyleSheet(_BTN_STYLE)
        btn_auto_bc.setToolTip("Auto stretch histogram to full range (undoable)")
        btn_auto_bc.clicked.connect(self._auto_bright_contrast)
        btn_reset_bc = QPushButton("Reset")
        btn_reset_bc.setStyleSheet(_BTN_STYLE)
        btn_reset_bc.clicked.connect(self._reset_bc_sliders)
        bc_row.addWidget(btn_apply_bc)
        bc_row.addWidget(btn_auto_bc)
        bc_row.addWidget(btn_reset_bc)
        ll.addLayout(bc_row)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Palette"))
        self._palette_cb = QComboBox()
        for name in WK_GRADIENTS:
            self._palette_cb.addItem(name)
        self._palette_cb.currentTextChanged.connect(self._on_palette_changed)
        ll.addWidget(self._palette_cb)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Reference Subtraction"))

        ll.addWidget(QLabel("Reference:"))
        self._ref_combo = QComboBox()
        self._ref_combo.addItem("— none —")
        self._ref_combo.currentIndexChanged.connect(self._on_ref_changed)
        ll.addWidget(self._ref_combo)

        btn_load_ref = QPushButton("Load ref from file…")
        btn_load_ref.setStyleSheet(_BTN_STYLE)
        btn_load_ref.clicked.connect(self._load_ref_from_file)
        ll.addWidget(btn_load_ref)

        btn_sub = QPushButton("Subtract  (A − B ≥ 0)")
        btn_sub.setStyleSheet(_BTN_STYLE)
        btn_sub.clicked.connect(self._subtract_ref)
        btn_abs = QPushButton("Abs diff  |A − B|")
        btn_abs.setStyleSheet(_BTN_STYLE)
        btn_abs.clicked.connect(self._abs_diff_ref)
        ll.addWidget(btn_sub)
        ll.addWidget(btn_abs)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Zoom"))
        zoom_row = QHBoxLayout()
        for label, slot in [("🔍+", lambda: self._canvas.zoom_in()),
                             ("🔍−", lambda: self._canvas.zoom_out()),
                             ("Fit",  lambda: self._canvas.fit_to_view()),
                             ("1:1",  lambda: self._canvas.zoom_reset())]:
            b = QPushButton(label)
            b.setStyleSheet(_BTN_STYLE)
            b.clicked.connect(slot)
            zoom_row.addWidget(b)
        ll.addLayout(zoom_row)

        ll.addWidget(_separator())
        ll.addWidget(_group_label("Save"))
        save_row = QHBoxLayout()
        btn_save_png  = QPushButton("Save PNG…")
        btn_save_png.setStyleSheet(_BTN_STYLE)
        btn_save_png.clicked.connect(lambda: self._save("png"))
        btn_save_tiff = QPushButton("Save TIFF…")
        btn_save_tiff.setStyleSheet(_BTN_STYLE)
        btn_save_tiff.clicked.connect(lambda: self._save("tiff"))
        save_row.addWidget(btn_save_png)
        save_row.addWidget(btn_save_tiff)
        ll.addLayout(save_row)

        ll.addStretch(1)
        left_scroll.setWidget(left)

        # Canvas
        self._canvas = WorkshopCanvas()
        self._canvas.image_changed.connect(self._on_canvas_changed)
        self._canvas.color_picked.connect(self._on_color_picked)

        main_row.addWidget(left_scroll)
        main_row.addWidget(self._canvas, 1)
        root.addLayout(main_row, 1)

        # Status bar
        self._status_lbl = QLabel(
            "No images. Use '➤ Workshop' in Image Finder, Image Slider or Shot Finder.")
        self._status_lbl.setStyleSheet("font-size: 11px; color: #555; padding: 2px 4px;")
        root.addWidget(self._status_lbl)

        self._update_slot_list()
        self._set_tool(WorkshopCanvas.TOOL_NONE)

    # ─────────────────────────────────────────────── Public API ───

    def receive_image(self, arr: np.ndarray, label: str = ""):
        """Called by IF/IS/SF — push image into Workshop."""
        if arr.ndim == 2:
            rgb = np.stack([arr, arr, arr], axis=2).copy()
        elif arr.shape[2] == 4:
            rgb = arr[:, :, :3].copy()
        else:
            rgb = arr.copy()
        rgb = rgb.astype(np.uint8)
        slot = _WorkshopSlot(label=label, source_arr=rgb, current_arr=rgb.copy())
        self._slots.append(slot)
        self._update_slot_list()
        self._activate_slot(len(self._slots) - 1)
        self._status_lbl.setText(
            f"Received: {label}  ({rgb.shape[1]}×{rgb.shape[0]})")

    # ─────────────────────────────────────────────────── Slots ────

    def _update_slot_list(self):
        self._slot_list.blockSignals(True)
        self._ref_combo.blockSignals(True)
        cur_a = self._slot_list.currentIndex()
        self._slot_list.clear()
        self._ref_combo.clear()
        self._ref_combo.addItem("— none —")
        for i, s in enumerate(self._slots):
            short = s.label[:40] + ("…" if len(s.label) > 40 else "")
            self._slot_list.addItem(f"{i+1}. {short}")
            self._ref_combo.addItem(f"{i+1}. {short}")
        if 0 <= cur_a < len(self._slots):
            self._slot_list.setCurrentIndex(cur_a)
        elif self._slots:
            self._slot_list.setCurrentIndex(len(self._slots) - 1)
        self._slot_list.blockSignals(False)
        self._ref_combo.blockSignals(False)

    def _on_slot_selected(self, idx: int):
        if 0 <= idx < len(self._slots):
            self._activate_slot(idx)

    def _activate_slot(self, idx: int):
        self._active = idx
        self._slot_list.blockSignals(True)
        self._slot_list.setCurrentIndex(idx)
        self._slot_list.blockSignals(False)
        self._canvas.set_slot(self._slots[idx])
        self._reset_bc_sliders()
        self._update_info()

    def _on_ref_changed(self, idx: int):
        self._ref = idx - 1

    def _remove_slot(self):
        if not self._slots or self._active < 0:
            return
        self._slots.pop(self._active)
        self._active = max(0, self._active - 1)
        self._ref = -1
        self._update_slot_list()
        if self._slots:
            self._activate_slot(self._active)
        else:
            self._canvas._slot = None
            self._canvas._qimage = None
            self._canvas.update()
            self._info_lbl.setText("—")

    def _clear_all(self):
        if not self._slots:
            return
        r = QMessageBox.question(self, "Clear all",
            "Remove all images from Workshop?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        self._slots.clear()
        self._active = -1; self._ref = -1
        self._update_slot_list()
        self._canvas._slot = None
        self._canvas._qimage = None
        self._canvas.update()
        self._info_lbl.setText("—")
        self._status_lbl.setText("Workshop cleared.")

    # ─────────────────────────────────────────────────── Tools ────

    def _set_tool(self, tool: str):
        self._canvas.tool = tool
        cursors = {
            WorkshopCanvas.TOOL_NONE:    Qt.CursorShape.OpenHandCursor,
            WorkshopCanvas.TOOL_BRUSH:   Qt.CursorShape.CrossCursor,
            WorkshopCanvas.TOOL_ERASER:  Qt.CursorShape.BlankCursor,
            WorkshopCanvas.TOOL_LINE:    Qt.CursorShape.CrossCursor,
            WorkshopCanvas.TOOL_RECT:    Qt.CursorShape.CrossCursor,
            WorkshopCanvas.TOOL_TEXT:    Qt.CursorShape.IBeamCursor,
            WorkshopCanvas.TOOL_CROP:    Qt.CursorShape.CrossCursor,
            WorkshopCanvas.TOOL_EYEDROP: Qt.CursorShape.PointingHandCursor,
        }
        self._canvas.setCursor(QCursor(cursors.get(tool, Qt.CursorShape.ArrowCursor)))

    def _pick_draw_color(self):
        col = QColorDialog.getColor(self._draw_color, self, "Pick draw colour")
        if col.isValid():
            self._draw_color = col
            self._canvas.draw_color = col
            self._update_color_btn()

    def _update_color_btn(self):
        self._color_btn.setStyleSheet(
            f"background: {self._draw_color.name()}; "
            "border: 2px solid #888; border-radius: 3px;")

    def _on_color_picked(self, col: QColor):
        """Canvas picked a colour from the image — update swatch + draw colour."""
        self._draw_color = col
        self._canvas.draw_color = col
        self._update_color_btn()
        self._status_lbl.setText(
            f"Picked colour: {col.name()}  R {col.red()}  G {col.green()}  B {col.blue()}"
            "  —  now set as draw colour")

    def _on_brush_size(self, v: int):
        self._canvas.brush_size = v

    def _on_line_width(self, v: int):
        self._canvas.line_width = v

    # ─────────────────────────────────────────────── Undo/Redo ────

    def _undo(self):
        if self._active < 0:
            return
        if self._slots[self._active].undo():
            self._canvas.refresh()
            self._update_info()
            self._status_lbl.setText("Undone.")

    def _redo(self):
        if self._active < 0:
            return
        if self._slots[self._active].redo():
            self._canvas.refresh()
            self._update_info()
            self._status_lbl.setText("Redone.")

    def _reset_to_source(self):
        if self._active < 0:
            return
        self._slots[self._active].reset_to_source()
        self._canvas.refresh()
        self._reset_bc_sliders()
        self._update_info()
        self._status_lbl.setText("Reset to original.")

    # ──────────────────────────────────────── Brightness/Contrast ─

    def _on_bc_changed(self):
        b = self._bright_sl.value()
        c = self._contrast_sl.value()
        if b == 0 and c == 0:
            self._bc_lbl.setText("")
            # Restore original if nothing changed
            if self._active >= 0 and hasattr(self, '_bc_preview_active') and self._bc_preview_active:
                self._canvas.refresh()
                self._bc_preview_active = False
            return
        self._bc_lbl.setText(f"B {b:+d}  C {c:+d}  (click Apply to bake)")
        # Live preview — apply to a temporary copy without touching the slot
        if self._active < 0:
            return
        slot = self._slots[self._active]
        arr = slot.current_arr.astype(np.int32)
        factor = (259.0 * (c + 127)) / (127.0 * (259.0 - c)) if c != 0 else 1.0
        preview = np.clip(factor * (arr - 128) + 128 + b, 0, 255).astype(np.uint8)
        qimg = _np_to_qimage(preview)
        self._canvas._qimage = qimg
        self._canvas.update()
        self._bc_preview_active = True

    def _reset_bc_sliders(self):
        self._bright_sl.blockSignals(True)
        self._contrast_sl.blockSignals(True)
        self._bright_sl.setValue(0)
        self._contrast_sl.setValue(0)
        self._bright_sl.blockSignals(False)
        self._contrast_sl.blockSignals(False)
        self._bc_lbl.setText("")
        self._bc_preview_active = False
        if self._active >= 0:
            self._canvas.refresh()

    def _apply_bright_contrast(self):
        if self._active < 0:
            return
        b = self._bright_sl.value()
        c = self._contrast_sl.value()
        if b == 0 and c == 0:
            return
        slot = self._slots[self._active]
        slot.push_undo()
        arr = slot.current_arr.astype(np.int32)
        factor = (259.0 * (c + 127)) / (127.0 * (259.0 - c)) if c != 0 else 1.0
        arr = np.clip(factor * (arr - 128) + 128 + b, 0, 255).astype(np.uint8)
        slot.current_arr = arr
        self._canvas.refresh()
        self._reset_bc_sliders()
        self._update_info()
        self._status_lbl.setText(f"Applied: brightness {b:+d}, contrast {c:+d}")

    def _auto_bright_contrast(self):
        """Histogram stretch: remap so p2 → 0 and p98 → 255 (undoable)."""
        if self._active < 0:
            return
        slot = self._slots[self._active]
        slot.push_undo()
        arr = slot.current_arr.astype(np.float32)
        lo = float(np.percentile(arr, 2))
        hi = float(np.percentile(arr, 98))
        if hi <= lo:
            self._status_lbl.setText("Auto B/C: image is flat, nothing to stretch.")
            return
        arr = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
        slot.current_arr = arr
        self._reset_bc_sliders()
        self._canvas.refresh()
        self._update_info()
        self._status_lbl.setText(f"Auto B/C applied: mapped [{lo:.0f}…{hi:.0f}] → [0…255]")

    def _on_palette_changed(self, name: str):
        """Apply the selected palette LUT to the current slot (undoable)."""
        if self._active < 0:
            return
        slot = self._slots[self._active]
        slot.push_undo()
        lut = WK_GRADIENTS.get(name)
        # Convert to grayscale first, then apply LUT
        src = slot.current_arr
        if src.ndim == 3:
            gray = (0.299 * src[:, :, 0] + 0.587 * src[:, :, 1] +
                    0.114 * src[:, :, 2]).astype(np.uint8)
        else:
            gray = src.astype(np.uint8)
        if lut is None:
            rgb = np.stack([gray, gray, gray], axis=2)
        else:
            rgb = lut[gray].astype(np.uint8)
        slot.current_arr = rgb
        self._canvas.refresh()
        self._update_info()
        self._status_lbl.setText(f"Palette applied: {name}")

    # ──────────────────────────────────────── Reference diff ──────

    def _load_ref_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load reference image", "",
            "Images (*.png *.tif *.tiff *.jpg *.bmp)")
        if not path:
            return
        try:
            pil = _PilImg.open(path).convert("RGB")
            arr = np.array(pil, dtype=np.uint8)
            slot = _WorkshopSlot(
                label=f"[REF] {Path(path).name}",
                source_arr=arr, current_arr=arr.copy())
            self._slots.append(slot)
            self._update_slot_list()
            self._status_lbl.setText(f"Reference loaded: {Path(path).name}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not load:\n{e}")

    def _subtract_ref(self):  self._do_diff(absolute=False)
    def _abs_diff_ref(self):  self._do_diff(absolute=True)

    def _do_diff(self, absolute: bool):
        if self._active < 0:
            QMessageBox.information(self, "No image", "No active image."); return
        if self._ref < 0:
            QMessageBox.information(self, "No reference",
                "Select a reference image first."); return
        if self._ref == self._active:
            QMessageBox.information(self, "Same image",
                "Active and reference are the same."); return
        a = self._slots[self._active].current_arr.astype(np.int32)
        r = self._slots[self._ref].current_arr.astype(np.int32)
        h = min(a.shape[0], r.shape[0])
        w = min(a.shape[1], r.shape[1])
        result = (np.abs(a[:h, :w] - r[:h, :w]) if absolute
                  else np.clip(a[:h, :w] - r[:h, :w], 0, 255)).astype(np.uint8)
        slot = self._slots[self._active]
        slot.push_undo()
        slot.current_arr = result
        self._canvas.refresh()
        self._update_info()
        op = "abs diff" if absolute else "subtract"
        self._status_lbl.setText(
            f"{op}: {slot.label}  −  {self._slots[self._ref].label}")

    # ─────────────────────────────────────────────────── Save ─────

    def _save(self, fmt: str = "png"):
        if self._active < 0:
            QMessageBox.information(self, "No image", "No image to save."); return
        slot = self._slots[self._active]
        safe = "".join(c if c.isalnum() or c in " _-." else "_" for c in slot.label)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", f"{safe}.{fmt}",
            f"{'PNG' if fmt=='png' else 'TIFF'} (*.{fmt})")
        if not path:
            return
        try:
            _arr_to_pil(slot.current_arr).save(path)
            self._status_lbl.setText(f"Saved: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Save error", f"Could not save:\n{e}")

    # ─────────────────────────────────────────────────── Info ─────

    def _update_info(self):
        if self._active < 0 or not self._slots:
            self._info_lbl.setText("—"); return
        slot = self._slots[self._active]
        arr = slot.current_arr
        h, w = arr.shape[:2]
        mode = "RGB" if arr.ndim == 3 else "Gray"
        self._info_lbl.setText(
            f"<b>{slot.label[:35]}</b><br>"
            f"{w} × {h} px  |  {mode}<br>"
            f"Undo steps: {len(slot.undo_stack)}")

    def _on_canvas_changed(self):
        self._update_info()


# ─────────────────────────────────────────────────────────────────
#  Small UI helpers
# ─────────────────────────────────────────────────────────────────

def _group_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: 600; font-size: 11px; color: #333; margin-top: 2px;")
    return lbl


def _separator() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: #ccc;")
    return f
