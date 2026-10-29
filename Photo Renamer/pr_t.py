"""
pr_t.py  --  Photo Renamer: the whole program, as one QWidget.

Open a folder, look through the pictures in it (sub-folders included), rename them
by double-clicking, cross out the ones that should go, mark them up in the panel on
the right (circle, rectangle, arrow, pen, text, crop, rotate), then Save. Recover
walks the pending changes and drops only the ones you pick.

Nothing on disk is touched until Save. Recover therefore only ever throws away
pending changes; once Save has run there is nothing left to undo -- except that an
edited picture's untouched original is copied into "Original photos" first.

Every colour in this file is stated. Windows here runs in dark mode, so anything
left to the theme comes out black on black.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QObject, QPoint,
                            QPointF, QRect, QRectF, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QFontMetricsF,
                           QIcon, QImage, QImageReader, QImageWriter,
                           QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QShortcut, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                               QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSplitter, QStyle,
                               QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

PICTURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff",
                    ".webp"}

# Folders that never hold pictures worth showing.
_SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}

# Save copies the untouched original of every edited picture in here, at the top
# of the opened folder, with the sub-folders mirrored inside. It is never listed
# in the grid itself.
ORIGINALS_DIR = "Original photos"

# The grid: always two tiles in a row, three once the pane is wide enough for
# three of MIN_TILE_W, and the tiles stretch so a row is always filled edge to
# edge -- no empty strip on the right.
MIN_COLUMNS = 2
MAX_COLUMNS = 3
MIN_TILE_W = 200
_CARD_INSET = 2            # gap between two cards
_CARD_PAD = 4              # inside the card, around the picture
_NAME_H = 17               # exactly one line for the name, and nothing else
_PIC_ASPECT = 1.1          # the picture box is slightly taller than wide

# Thumbnails are decoded into these sizes and cached per size, so dragging the
# splitter does not re-read the folder every few pixels and a wide pane still
# gets a sharp picture.
_THUMB_BUCKETS = (128, 256, 512)
_PREVIEW_SIDE = 1400       # how large a preview is decoded; the label scales it
_THUMB_CACHE_MAX = 4000
_PREVIEW_CACHE_MAX = 8

# Windows will not take these in a file name, and a name ending in a dot is
# refused as well.
_INVALID_NAME_CHARS = set('<>:"/\\|?*')


# ─────────────────────────────────────────────────────────────────────────────
#  Settings file
#
#  Same idiom the other programs here use: read through a bare try/except so a
#  damaged file is simply an empty one, and never raise on write.
# ─────────────────────────────────────────────────────────────────────────────
UI_STATE_PATH = (Path(os.environ.get("APPDATA", str(Path.home())))
                 / "ELI_PhotoRenamer" / "ui_state.json")


def load_ui_state() -> dict:
    try:
        with open(UI_STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_ui_state(state: dict) -> None:
    try:
        UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(UI_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _remember(key: str, value) -> None:
    state = load_ui_state()
    state[key] = value
    save_ui_state(state)


# ─────────────────────────────────────────────────────────────────────────────
#  Handing a file or a folder to Windows
#
#  The house pattern: the helper raises, the caller shows the error. os.startfile
#  is the one that copes with a UNC path.
# ─────────────────────────────────────────────────────────────────────────────
def open_with_windows(path: Path) -> None:
    """Open a file in whatever Windows uses for it, or a folder in Explorer."""
    if sys.platform.startswith("win"):
        os.startfile(str(path))                                  # noqa: S606
        return
    subprocess.Popen(["xdg-open", str(path)])


def reveal_in_explorer(path: Path) -> None:
    """Open the file's folder with the file itself highlighted.

    The path must stay ONE argument, comma included -- Explorer parses the tail
    of the command line itself, and a split argument list opens the wrong thing.
    """
    if sys.platform.startswith("win"):
        subprocess.Popen(f'explorer /select,"{path}"')            # noqa: S607
        return
    open_with_windows(path.parent)


# ─────────────────────────────────────────────────────────────────────────────
#  Look
# ─────────────────────────────────────────────────────────────────────────────
_BTN_QSS = (
    "QPushButton { padding: 5px 11px; border: 1px solid #b6b6b6; border-radius: 3px;"
    " background: #efefef; color: #111; }"
    "QPushButton:hover { background: #d9e8ff; }"
    "QPushButton:pressed { background: #b9d0f5; }"
    "QPushButton:disabled { background: #f4f4f4; color: #9a9a9a; border-color: #dcdcdc; }"
)
# The one deliberately dark button in the window, so white ink is right here.
_SAVE_QSS = (
    "QPushButton { padding: 5px 14px; border: 1px solid #245c27; border-radius: 3px;"
    " background: #2e7d32; color: #ffffff; font-weight: 700; }"
    "QPushButton:hover { background: #276b2b; }"
    "QPushButton:pressed { background: #1f5722; }"
    "QPushButton:disabled { background: #dfe4df; color: #8d938d; border-color: #cbd2cb; }"
)
_DANGER_QSS = (
    "QPushButton { padding: 5px 11px; border: 1px solid #c07070; border-radius: 3px;"
    " background: #fdecea; color: #8d1c12; }"
    "QPushButton:hover { background: #f8d3ce; }"
    "QPushButton:disabled { background: #f6f0f0; color: #bb9a97; border-color: #e3d2d0; }"
)
_TABLE_QSS = (
    "QTableWidget { background: #ffffff; color: #16202c; gridline-color: #dfe4ea;"
    " font-size: 12px; selection-background-color: #cfe0f7;"
    " selection-color: #10243c; }"
    "QHeaderView::section { background: #eef1f5; color: #16202c; font-weight: 600;"
    " border: 0px; border-right: 1px solid #dfe4ea;"
    " border-bottom: 1px solid #cfd6de; padding: 5px 7px; }"
    "QTableCornerButton::section { background: #eef1f5; border: 0px; }"
)
_LIST_QSS = (
    "QListWidget { background: #fbfbfb; border: 1px solid #c8ccd2; }"
    "QScrollBar:vertical { background: #f0f0f0; width: 13px; margin: 0; }"
    "QScrollBar::handle:vertical { background: #b9bec5; border-radius: 6px;"
    " min-height: 24px; }"
    "QScrollBar::handle:vertical:hover { background: #9aa1a9; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
)
_EDITOR_QSS = (
    "QLineEdit { background: #ffffff; color: #111; border: 1px solid #2f6fb5;"
    " border-radius: 2px; padding: 1px 3px; font-size: 12px;"
    " selection-background-color: #2f6fb5; selection-color: #ffffff; }"
)
# A QMenu left unstyled comes out dark here, whatever the application sheet says.
_MENU_QSS = (
    "QMenu { background: #ffffff; color: #16202c; border: 1px solid #c3c9d2;"
    " padding: 3px; }"
    "QMenu::item { padding: 4px 22px 4px 12px; }"
    "QMenu::item:selected { background: #cfe0f7; color: #10243c; }"
    "QMenu::item:disabled { color: #9aa3ad; }"
    "QMenu::separator { height: 1px; background: #dfe4ea; margin: 3px 6px; }"
)
_MSGBOX_QSS = ("QMessageBox { background: #f3f3f3; color: #111; }"
               "QLabel { color: #111; }" + _BTN_QSS)
_TABS_QSS = (
    "QTabWidget::pane { border: 1px solid #c8ccd2; background: #f3f3f3;"
    " top: -1px; }"
    "QTabBar::tab { background: #e2e5e9; color: #16202c; border: 1px solid #c8ccd2;"
    " border-bottom: 0px; padding: 5px 16px; margin-right: 2px;"
    " border-top-left-radius: 3px; border-top-right-radius: 3px; }"
    "QTabBar::tab:selected { background: #f3f3f3; font-weight: 700; }"
    "QTabBar::tab:hover:!selected { background: #d9e8ff; }"
)

_CHECK_PX = 15
_CHECK_QSS_CACHE = ""


def _check_mark_url() -> str:
    """Path to the white tick, painted on first use.

    A stylesheet that gives QCheckBox::indicator a size takes the indicator over
    from the style and then draws nothing unless it is also handed a background, a
    border and, for the ticked state, an image -- and it can only take that image
    from a file. So the tick is painted here, beside the settings file. Empty on
    failure, which leaves a plain solid blue square: still unmistakably on.
    """
    try:
        path = UI_STATE_PATH.parent / "check_mark.png"
        if not path.exists():
            n = _CHECK_PX * 2               # 2x, so it stays clean when scaled
            img = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor("#ffffff"))
            pen.setWidthF(n * 0.15)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            q.setPen(pen)
            q.drawPolyline(QPolygonF([
                QPointF(n * 0.21, n * 0.52), QPointF(n * 0.42, n * 0.73),
                QPointF(n * 0.79, n * 0.26)]))
            q.end()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not img.save(str(path), "PNG"):
                return ""
        return path.as_posix()
    except Exception:
        return ""


def _check_qss() -> str:
    """Every state of a tick box, stated. Built on first use, not at import: the
    tick is a file that has to be written first."""
    global _CHECK_QSS_CACHE
    if _CHECK_QSS_CACHE:
        return _CHECK_QSS_CACHE
    url = _check_mark_url()
    mark = f' image: url("{url}");' if url else ""
    _CHECK_QSS_CACHE = (
        "QCheckBox { color: #111; spacing: 6px; }"
        "QCheckBox:disabled { color: #8a8a8a; }"
        f"QCheckBox::indicator {{ width: {_CHECK_PX}px; height: {_CHECK_PX}px;"
        " border: 1px solid #7b8492; border-radius: 3px; background: #ffffff; }"
        "QCheckBox::indicator:hover { border-color: #2f6fb5; background: #eaf3ff; }"
        f"QCheckBox::indicator:checked {{ background: #2f6fb5;"
        f" border-color: #24557f;{mark} }}"
        "QCheckBox::indicator:checked:hover { background: #3a7ec6;"
        " border-color: #24557f; }"
        "QCheckBox::indicator:disabled { background: #f0f0f0; border-color: #cfcfcf; }"
    )
    return _CHECK_QSS_CACHE


class _ElidedLabel(QLabel):
    """A label that shortens its own text instead of pushing its neighbours out.

    A plain QLabel asks for as much width as its text needs, so one long folder
    name in the top bar is enough to shove the buttons off the edge of the
    window. This one keeps the full text, paints what fits, and reports a
    minimum width of zero.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:          # noqa: N802 (Qt naming)
        self._full = text
        super().setText(text)
        self.update()

    def minimumSizeHint(self) -> QSize:            # noqa: N802
        return QSize(0, QFontMetrics(self.font()).height())

    def paintEvent(self, event) -> None:           # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(self.palette().windowText().color()))
        fm = QFontMetrics(self.font())
        text = fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle,
                             self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft
                                    | Qt.AlignmentFlag.AlignVCenter), text)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  Button icons
#
#  Drawn here, not typed as characters. A Unicode glyph brings its own typeface
#  along -- colour emoji that ignore the stylesheet on one machine, hairline
#  outlines on the next -- and Qt's own disabled artwork fades a drawing to
#  nothing, so every mode is spelled out below.
#
#  Every recipe draws inside a 20x20 grid and the painter is scaled to whatever
#  size is asked for, so one recipe serves every monitor scaling.
# ─────────────────────────────────────────────────────────────────────────────
_ICON_BOX = 20.0
_ICON_PX = 18
_ICON_SIZES = (18, 27, 36)
_ICON_CACHE: dict = {}


def _ipen(c: QColor, w: float = 1.9) -> QPen:
    p = QPen(c)
    p.setWidthF(w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def _ico_folder(p: QPainter, c: QColor) -> None:
    p.setPen(_ipen(c, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(2.5, 6.0)
    path.lineTo(8.0, 6.0)
    path.lineTo(9.6, 7.8)
    path.lineTo(17.5, 7.8)
    path.lineTo(17.5, 16.0)
    path.lineTo(2.5, 16.0)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(2.5, 10.0), QPointF(17.5, 10.0))


def _ico_explorer(p: QPainter, c: QColor) -> None:
    # A folder with an arrow leaving it: hand this folder to Windows.
    p.setPen(_ipen(c, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(2.2, 7.4)
    path.lineTo(7.0, 7.4)
    path.lineTo(8.4, 9.0)
    path.lineTo(12.6, 9.0)
    path.lineTo(12.6, 17.0)
    path.lineTo(2.2, 17.0)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(11.2, 8.4), QPointF(17.4, 2.6))
    p.drawPolyline(QPolygonF([QPointF(13.2, 2.6), QPointF(17.8, 2.6),
                              QPointF(17.8, 7.0)]))


def _ico_picture(p: QPainter, c: QColor) -> None:
    # A photograph: frame, sun, hill. The hill is filled, an outline is mush.
    p.setPen(_ipen(c, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(2.4, 4.4, 15.2, 11.6), 1.6, 1.6)
    p.drawEllipse(QRectF(5.0, 6.4, 3.0, 3.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    hill = QPainterPath()
    hill.moveTo(3.6, 14.8)
    hill.lineTo(8.0, 9.8)
    hill.lineTo(11.2, 13.2)
    hill.lineTo(13.0, 11.4)
    hill.lineTo(16.4, 14.8)
    hill.closeSubpath()
    p.drawPath(hill)


def _ico_save(p: QPainter, c: QColor) -> None:
    # A tray with an arrow coming down into it: "write this out".
    p.setPen(_ipen(c, 1.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(10.0, 2.6), QPointF(10.0, 11.4))
    p.drawPolyline(QPolygonF([QPointF(6.4, 8.0), QPointF(10.0, 11.6),
                              QPointF(13.6, 8.0)]))
    p.drawPolyline(QPolygonF([QPointF(3.4, 13.0), QPointF(3.4, 16.8),
                              QPointF(16.6, 16.8), QPointF(16.6, 13.0)]))


def _ico_undo(p: QPainter, c: QColor) -> None:
    # An arrow curving back to the left.
    p.setPen(_ipen(c, 1.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(4.2, 9.0)
    path.arcTo(QRectF(4.2, 5.2, 11.6, 11.6), 160.0, -230.0)
    p.drawPath(path)
    p.drawPolyline(QPolygonF([QPointF(1.6, 6.0), QPointF(4.2, 9.4),
                              QPointF(7.6, 6.8)]))


def _ico_pointer(p: QPainter, c: QColor) -> None:
    # A mouse pointer: just look, draw nothing.
    p.setPen(_ipen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolygon(QPolygonF([QPointF(5.0, 2.6), QPointF(5.0, 16.0),
                             QPointF(8.4, 12.8), QPointF(10.8, 17.6),
                             QPointF(13.0, 16.6), QPointF(10.6, 11.8),
                             QPointF(15.0, 11.6)]))


def _ico_ellipse(p: QPainter, c: QColor) -> None:
    p.setPen(_ipen(c, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(2.6, 4.4, 14.8, 11.2))


def _ico_rect(p: QPainter, c: QColor) -> None:
    p.setPen(_ipen(c, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(3.0, 5.0, 14.0, 10.0))


def _ico_arrow(p: QPainter, c: QColor) -> None:
    p.setPen(_ipen(c, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(3.4, 16.6), QPointF(14.6, 5.4))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(17.2, 2.8), QPointF(15.8, 10.4),
                             QPointF(9.6, 4.2)]))


def _ico_pen(p: QPainter, c: QColor) -> None:
    # A pencil lying on a slant, and the scribble it leaves.
    p.setPen(_ipen(c, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolygon(QPolygonF([QPointF(13.4, 2.8), QPointF(17.2, 6.6),
                             QPointF(8.0, 15.8), QPointF(3.6, 16.4),
                             QPointF(4.2, 12.0)]))
    p.drawLine(QPointF(11.4, 4.8), QPointF(15.2, 8.6))


def _ico_text(p: QPainter, c: QColor) -> None:
    # A capital T, drawn rather than typed.
    p.setPen(_ipen(c, 2.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(4.0, 4.0), QPointF(16.0, 4.0))
    p.drawLine(QPointF(10.0, 4.0), QPointF(10.0, 16.6))
    p.drawLine(QPointF(7.4, 16.6), QPointF(12.6, 16.6))


def _ico_crop(p: QPainter, c: QColor) -> None:
    # The two crossing corner brackets of a crop tool.
    p.setPen(_ipen(c, 1.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(5.6, 2.0), QPointF(5.6, 14.4),
                              QPointF(18.0, 14.4)]))
    p.drawPolyline(QPolygonF([QPointF(2.0, 5.6), QPointF(14.4, 5.6),
                              QPointF(14.4, 18.0)]))


def _rotate_arrow(p: QPainter, c: QColor, clockwise: bool) -> None:
    # Drawn as written, the head sits at the arc's start and the arrow turns
    # anticlockwise; the clockwise one is its mirror image.
    p.setPen(_ipen(c, 1.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if clockwise:
        p.translate(_ICON_BOX, 0.0)
        p.scale(-1.0, 1.0)
    path = QPainterPath()
    path.arcMoveTo(QRectF(3.4, 4.0, 13.2, 13.2), 150.0)
    path.arcTo(QRectF(3.4, 4.0, 13.2, 13.2), 150.0, -270.0)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(1.6, 5.6), QPointF(8.2, 4.0),
                             QPointF(4.0, 10.6)]))


def _ico_rot_left(p: QPainter, c: QColor) -> None:
    _rotate_arrow(p, c, clockwise=False)


def _ico_rot_right(p: QPainter, c: QColor) -> None:
    _rotate_arrow(p, c, clockwise=True)


def _ico_reset(p: QPainter, c: QColor) -> None:
    # A photograph with a cross over it: throw every edit of it away.
    p.setPen(_ipen(c, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(2.0, 3.6, 13.0, 10.0), 1.4, 1.4)
    p.setPen(_ipen(c, 2.0))
    p.drawLine(QPointF(11.4, 10.8), QPointF(18.0, 17.4))
    p.drawLine(QPointF(18.0, 10.8), QPointF(11.4, 17.4))


def _line_width_recipe(width: float):
    def draw(p: QPainter, c: QColor) -> None:
        pen = QPen(c)
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(3.6, 10.0), QPointF(16.4, 10.0))
    return draw


_ICON_RECIPES = {
    "folder": _ico_folder,
    "explorer": _ico_explorer,
    "picture": _ico_picture,
    "save": _ico_save,
    "undo": _ico_undo,
    "view": _ico_pointer,
    "ellipse": _ico_ellipse,
    "rect": _ico_rect,
    "arrow": _ico_arrow,
    "pen": _ico_pen,
    "text": _ico_text,
    "crop": _ico_crop,
    "rot_left": _ico_rot_left,
    "rot_right": _ico_rot_right,
    "reset": _ico_reset,
    "width0": _line_width_recipe(1.4),
    "width1": _line_width_recipe(3.0),
    "width2": _line_width_recipe(5.4),
}


def _render_icon_pixmap(name: str, px: int, ink: QColor) -> QPixmap:
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(px / _ICON_BOX, px / _ICON_BOX)
    _ICON_RECIPES[name](p, ink)
    p.end()
    return pm


def action_icon(name: str, ink: str = "#1e2530") -> QIcon:
    """A button icon with every mode and size stated.

    Qt derives Disabled from Normal by fading it, which on a thin drawing leaves
    almost no ink at all, so the disabled artwork is supplied here too.
    """
    key = (name, ink)
    hit = _ICON_CACHE.get(key)
    if hit is not None:
        return hit
    icon = QIcon()
    normal = QColor(ink)
    disabled = QColor("#a0a8b2")
    for px in _ICON_SIZES:
        pm_n = _render_icon_pixmap(name, px, normal)
        pm_d = _render_icon_pixmap(name, px, disabled)
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
            icon.addPixmap(pm_n, mode, QIcon.State.Off)
            icon.addPixmap(pm_n, mode, QIcon.State.On)
        icon.addPixmap(pm_d, QIcon.Mode.Disabled, QIcon.State.Off)
        icon.addPixmap(pm_d, QIcon.Mode.Disabled, QIcon.State.On)
    _ICON_CACHE[key] = icon
    return icon


def _btn(text: str, tip: str = "", icon: str = "", kind: str = "plain") -> QPushButton:
    b = QPushButton(text)
    if kind == "save":
        b.setStyleSheet(_SAVE_QSS)
        ink = "#ffffff"
    elif kind == "danger":
        b.setStyleSheet(_DANGER_QSS)
        ink = "#8d1c12"
    else:
        b.setStyleSheet(_BTN_QSS)
        ink = "#1e2530"
    if tip:
        b.setToolTip(tip)
    if icon:
        b.setIcon(action_icon(icon, ink))
        b.setIconSize(QSize(_ICON_PX, _ICON_PX))
    b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return b


# The square icon-only buttons above the picture. A chosen tool keeps a blue
# ground and a thick blue rim, so which one is active is never in doubt.
_TOOL_BTN_PX = 25
_TOOL_ICON_PX = 16
_TOOL_QSS = (
    "QPushButton { padding: 0px; border: 1px solid #b6b6b6; border-radius: 3px;"
    " background: #efefef; color: #111; }"
    "QPushButton:hover { background: #d9e8ff; }"
    "QPushButton:pressed { background: #b9d0f5; }"
    "QPushButton:checked { background: #cfe0f7; border: 2px solid #2f6fb5; }"
    "QPushButton:disabled { background: #f4f4f4; border-color: #dcdcdc; }"
)


def _tool_btn(icon: QIcon, tip: str, checkable: bool = False) -> QPushButton:
    b = QPushButton()
    b.setStyleSheet(_TOOL_QSS)
    b.setIcon(icon)
    b.setIconSize(QSize(_TOOL_ICON_PX, _TOOL_ICON_PX))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setFixedSize(_TOOL_BTN_PX, _TOOL_BTN_PX)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


def swatch_icon(colour: str) -> QIcon:
    """A filled square of the colour with a dark rim, so white shows too."""
    key = ("swatch", colour)
    hit = _ICON_CACHE.get(key)
    if hit is not None:
        return hit
    icon = QIcon()
    for px in _ICON_SIZES:
        for mode, fill, rim in ((QIcon.Mode.Normal, colour, "#2a2f36"),
                                (QIcon.Mode.Disabled, "#e3e3e3", "#b8b8b8")):
            pm = QPixmap(px, px)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            q.scale(px / _ICON_BOX, px / _ICON_BOX)
            q.setPen(QPen(QColor(rim), 1.4))
            q.setBrush(QBrush(QColor(fill)))
            q.drawRoundedRect(QRectF(2.6, 2.6, 14.8, 14.8), 2.0, 2.0)
            q.end()
            modes = ((QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected)
                     if mode == QIcon.Mode.Normal else (mode,))
            for m in modes:
                icon.addPixmap(pm, m, QIcon.State.Off)
                icon.addPixmap(pm, m, QIcon.State.On)
    _ICON_CACHE[key] = icon
    return icon


# ─────────────────────────────────────────────────────────────────────────────
#  Reading a picture
# ─────────────────────────────────────────────────────────────────────────────
def bucket_for(px: int) -> int:
    """The decode size a picture area of `px` should be served from."""
    for b in _THUMB_BUCKETS:
        if b >= px:
            return b
    return _THUMB_BUCKETS[-1]


def load_scaled(path: Path, max_side: int) -> tuple[QImage, int, int]:
    """(image scaled to fit max_side, full width, full height).

    Two things matter here. The bytes are read with Python's own open().read(),
    which releases the GIL for the wait, and handed to QImageReader through a
    buffer -- QImageReader given a path holds the GIL for the whole read, which
    freezes the window on a slow or network folder. And setScaledSize is applied
    before the read, so a thumbnail is never decoded at full resolution.

    A null image comes back for anything that cannot be read; the caller draws a
    placeholder rather than treating it as an error.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return QImage(), 0, 0
    try:
        ba = QByteArray(data)
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.ReadOnly)
        r = QImageReader(buf)
        r.setAutoTransform(True)
        sz = r.size()
        full_w = sz.width() if sz.isValid() else 0
        full_h = sz.height() if sz.isValid() else 0
        if full_w > 0 and full_h > 0 and max_side > 0:
            scale = max(full_w, full_h) / float(max_side)
            if scale > 1.0:
                r.setScaledSize(QSize(max(1, int(full_w / scale)),
                                      max(1, int(full_h / scale))))
        img = r.read()
        if img.isNull():
            return QImage(), full_w, full_h
        # size() is the stored size; a phone photo turned upright by its EXIF
        # tag comes out the other way round. The edits are measured against the
        # upright picture, so the full size must be too.
        if (full_w and full_h and full_w != full_h
                and (img.width() > img.height()) != (full_w > full_h)):
            full_w, full_h = full_h, full_w
        return img, full_w or img.width(), full_h or img.height()
    except Exception:
        return QImage(), 0, 0


def _retry(op, tries: int = 3, wait_s: float = 0.15) -> str:
    """Run a file operation, retrying a moment later on failure.

    Returns "" when it worked, otherwise the reason, ready to be shown to the
    user.
    """
    reason = ""
    for attempt in range(tries):
        try:
            op()
            return ""
        except OSError as exc:
            reason = exc.strerror or str(exc)
            if attempt + 1 < tries:
                time.sleep(wait_s)
    return reason or "could not be done"


class _LoadSink(QObject):
    """One signal hub for the whole widget.

    Deliberately NOT one per worker: a signals object parented to the widget and
    created per job is never collected, which is a slow leak that only shows up
    after days of running.
    """
    done = Signal(str, int, QImage, int, int)   # path, max_side, image, w, h


class _LoadTask(QRunnable):
    def __init__(self, path: Path, max_side: int, sink: _LoadSink):
        super().__init__()
        self._path = path
        self._max_side = max_side
        self._sink = sink

    def run(self) -> None:
        img, w, h = load_scaled(self._path, self._max_side)
        try:
            self._sink.done.emit(str(self._path), self._max_side, img, w, h)
        except RuntimeError:
            pass        # the window closed while this job was running


# ─────────────────────────────────────────────────────────────────────────────
#  Edits
#
#  An edit is kept as a list of steps, never as changed pixels. Every step holds
#  its points in full-resolution pixels of the picture as it is at that step --
#  after the rotations and crops before it. render_edits() replays the steps on
#  any decode of the picture, so the preview, the grid tile and the file Save
#  writes all come out of the same code: what is on screen is what is saved.
# ─────────────────────────────────────────────────────────────────────────────
EDIT_COLOURS = ("#e02020", "#ffd400", "#20a040", "#1e6fe0", "#111111", "#ffffff")

# Line width and text height for the three thickness buttons, as a share of the
# picture's longer side -- so a "medium" line looks the same on a phone photo and
# on a camera frame, whatever the zoom on screen.
_LINE_SHARE = (0.003, 0.006, 0.012)
_TEXT_SHARE = (0.035, 0.05, 0.075)


@dataclass
class EditOp:
    kind: str                   # ellipse, rect, arrow, pen, text, rotate, crop
    pts: list = field(default_factory=list)   # [(x, y), ...] full-res pixels
    colour: str = "#e02020"
    width: float = 0.0          # line width, or text height, in full-res pixels
    text: str = ""
    turn: int = 0               # rotate: +1 clockwise, -1 anticlockwise


def _crop_box(op: EditOp, cur_w: int, cur_h: int) -> QRect:
    """The crop step's rectangle in whole pixels, kept inside the picture."""
    (x0, y0), (x1, y1) = op.pts[0], op.pts[-1]
    left = max(0, int(round(min(x0, x1))))
    top = max(0, int(round(min(y0, y1))))
    right = min(cur_w, int(round(max(x0, x1))))
    bottom = min(cur_h, int(round(max(y0, y1))))
    return QRect(left, top, max(0, right - left), max(0, bottom - top))


def frame_after(full_w: int, full_h: int, ops: list[EditOp]) -> tuple[int, int]:
    """Size in full-resolution pixels after every rotation and crop."""
    w, h = full_w, full_h
    for op in ops:
        if op.kind == "rotate" and op.turn % 2:
            w, h = h, w
        elif op.kind == "crop":
            box = _crop_box(op, w, h)
            if box.width() > 0 and box.height() > 0:
                w, h = box.width(), box.height()
    return w, h


def _halo_for(colour: str) -> QColor:
    """Black around a light colour, white around a dark one."""
    c = QColor(colour)
    return QColor("#000000") if c.lightnessF() > 0.55 else QColor("#ffffff")


def draw_op(p: QPainter, op: EditOp) -> None:
    """Draw one marking step. The painter is already in full-res pixels."""
    if not op.pts:
        return
    c = QColor(op.colour)
    pen = QPen(c)
    pen.setWidthF(max(0.5, op.width))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    a = QPointF(*op.pts[0])
    b = QPointF(*op.pts[-1])
    if op.kind == "ellipse":
        p.drawEllipse(QRectF(a, b).normalized())
    elif op.kind == "rect":
        p.drawRect(QRectF(a, b).normalized())
    elif op.kind == "arrow":
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        if length < 0.5:
            return
        ux, uy = dx / length, dy / length
        head = min(length * 0.6, max(op.width * 4.5, 6.0))
        half = head * 0.5
        base = QPointF(b.x() - ux * head, b.y() - uy * head)
        # The shaft stops inside the head, so its round end never pokes out of
        # the point.
        p.drawLine(a, QPointF(b.x() - ux * head * 0.6, b.y() - uy * head * 0.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c))
        p.drawPolygon(QPolygonF([b,
                                 QPointF(base.x() - uy * half, base.y() + ux * half),
                                 QPointF(base.x() + uy * half, base.y() - ux * half)]))
    elif op.kind == "pen":
        if len(op.pts) == 1:
            p.drawPoint(a)
        else:
            p.drawPolyline(QPolygonF([QPointF(*pt) for pt in op.pts]))
    elif op.kind == "text" and op.text:
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(max(1, int(round(op.width))))
        fm = QFontMetricsF(f)
        path = QPainterPath()
        path.addText(QPointF(a.x(), a.y() + fm.ascent()), f, op.text)
        # A thin halo in the opposite brightness keeps the words readable on a
        # busy photograph.
        halo = QPen(_halo_for(op.colour))
        halo.setWidthF(max(1.0, op.width * 0.12))
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, halo)
        p.fillPath(path, QBrush(c))


def render_edits(base: QImage, full_w: int, full_h: int,
                 ops: list[EditOp]) -> QImage:
    """Replay the edit steps on a decode of the picture, at whatever size.

    base is the picture decoded at any scale; full_w x full_h is its real size,
    the frame the steps' points are measured in.
    """
    if not ops or base.isNull() or full_w <= 0 or full_h <= 0:
        return base
    img = base.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    s = base.width() / float(full_w)
    cur_w, cur_h = full_w, full_h
    for op in ops:
        if op.kind == "rotate":
            if op.turn % 4:
                img = img.transformed(QTransform().rotate(90.0 * op.turn))
                if op.turn % 2:
                    cur_w, cur_h = cur_h, cur_w
        elif op.kind == "crop":
            box = _crop_box(op, cur_w, cur_h)
            if box.width() <= 0 or box.height() <= 0:
                continue
            r = QRect(int(round(box.left() * s)), int(round(box.top() * s)),
                      max(1, int(round(box.width() * s))),
                      max(1, int(round(box.height() * s)))) & img.rect()
            img = img.copy(r)
            cur_w, cur_h = box.width(), box.height()
        else:
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            p.scale(s, s)
            draw_op(p, op)
            p.end()
    return img


_WRITABLE: set[str] = set()


def can_write(suffix: str) -> bool:
    """Whether an edited picture can be written back in its own format.

    GIF is the usual no: Qt reads it but cannot write it.
    """
    if not _WRITABLE:
        _WRITABLE.update(bytes(f).decode("ascii", "ignore").lower()
                         for f in QImageWriter.supportedImageFormats())
    return suffix.lower().lstrip(".") in _WRITABLE


def encode_image(img: QImage, suffix: str) -> bytes | None:
    """The picture as file bytes in the format its extension names."""
    fmt = suffix.lower().lstrip(".")
    if fmt in ("jpg", "jpeg", "bmp"):
        img = img.convertToFormat(QImage.Format.Format_RGB32)
    else:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buf, QByteArray(fmt.encode("ascii")))
    if fmt in ("jpg", "jpeg", "webp"):
        writer.setQuality(95)
    ok = writer.write(img)
    buf.close()
    return bytes(ba.data()) if ok else None


# ─────────────────────────────────────────────────────────────────────────────
#  What we know about one file
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Item:
    path: Path                  # where the file is now, on disk
    rel_dir: str                # sub-folder below the opened folder, "" at the top
    orig_stem: str              # the name it has on disk, without the extension
    suffix: str                 # ".png" -- never editable
    size: int                   # bytes on disk
    stem: str = ""              # the pending name, without the extension
    deleted: bool = False       # crossed out, to be removed by Save
    conflict: str = ""          # why this name cannot be used, "" when it can
    edits: list = field(default_factory=list)   # pending EditOp steps
    edit_rev: int = 0           # bumped on every change to edits, for caches

    def __post_init__(self):
        if not self.stem:
            self.stem = self.orig_stem

    @property
    def renamed(self) -> bool:
        return self.stem != self.orig_stem

    @property
    def edited(self) -> bool:
        return bool(self.edits)

    @property
    def new_name(self) -> str:
        return self.stem + self.suffix

    @property
    def orig_name(self) -> str:
        return self.orig_stem + self.suffix


def _edited_line(it: Item) -> str:
    n = len(it.edits)
    return (f"edited - {n} step{'s' if n != 1 else ''}; Save keeps the original"
            f" in \"{ORIGINALS_DIR}\"")


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1048576:.1f} MB"


# ─────────────────────────────────────────────────────────────────────────────
#  The grid of tiles
# ─────────────────────────────────────────────────────────────────────────────
def _badge_rect(tile: QRect) -> QRect:
    """The cross in the top-right corner of a tile. One definition, used both by
    the painter and by the click test, so the two can never drift apart."""
    d = max(16, min(22, tile.width() // 5))
    return QRect(tile.right() - d - 4, tile.top() + 4, d, d)


def _draw_cross_badge(p: QPainter, r: QRect) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.setPen(QPen(QColor("#c07070"), 1.4))
    p.drawEllipse(QRectF(r).adjusted(1.0, 1.0, -1.0, -1.0))
    pen = QPen(QColor("#8d1c12"), max(1.6, r.width() * 0.1))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    inset = r.width() * 0.32
    c = QRectF(r).adjusted(inset, inset, -inset, -inset)
    p.drawLine(c.topLeft(), c.bottomRight())
    p.drawLine(c.topRight(), c.bottomLeft())
    p.restore()


def _draw_undo_badge(p: QPainter, r: QRect) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QBrush(QColor("#eaf7ea")))
    p.setPen(QPen(QColor("#6a9a6a"), 1.4))
    p.drawEllipse(QRectF(r).adjusted(1.0, 1.0, -1.0, -1.0))
    p.translate(r.topLeft())
    p.scale(r.width() / _ICON_BOX, r.height() / _ICON_BOX)
    _ico_undo(p, QColor("#1a5c1a"))
    p.restore()


def _draw_edited_badge(p: QPainter, r: QRect) -> None:
    """A pencil in a white disc: this picture has pending edits."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.setPen(QPen(QColor("#5b3b8c"), 1.4))
    p.drawEllipse(QRectF(r).adjusted(1.0, 1.0, -1.0, -1.0))
    p.translate(QPointF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.18))
    p.scale(r.width() * 0.64 / _ICON_BOX, r.height() * 0.64 / _ICON_BOX)
    _ico_pen(p, QColor("#5b3b8c"))
    p.restore()


class _TileDelegate(QStyledItemDelegate):
    """Paints a whole tile: card, picture, cross, name.

    Nothing is left to the default item painting. The name has four different
    looks (plain, renamed, crossed out, clashing) and the default painter would
    take its colour from the palette, which on this machine is dark.

    The name gets exactly one line, cut off at the end when it is too long -- the
    whole name is in the item's tooltip and in the panel on the right. Every
    pixel that is not that one line belongs to the picture.
    """

    def __init__(self, owner: "PhotoRenamerWidget"):
        super().__init__(owner)
        self._owner = owner

    # ---- geometry -----------------------------------------------------------
    def sizeHint(self, option, index) -> QSize:
        return self._owner.tile_size()

    @staticmethod
    def _pic_rect(tile: QRect, pic_h: int) -> QRect:
        return QRect(tile.left() + _CARD_PAD, tile.top() + _CARD_PAD,
                     tile.width() - 2 * _CARD_PAD, pic_h)

    @staticmethod
    def _name_rect(tile: QRect, pic_h: int) -> QRect:
        top = tile.top() + _CARD_PAD + pic_h + 2
        return QRect(tile.left() + 3, top, tile.width() - 6, _NAME_H)

    # ---- painting -----------------------------------------------------------
    def paint(self, p: QPainter, option, index) -> None:
        item = self._owner.item_at(index.row())
        if item is None:
            return
        tile = option.rect.adjusted(_CARD_INSET, _CARD_INSET,
                                    -_CARD_INSET, -_CARD_INSET)
        pic_h = self._owner.pic_height()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # The card. Order matters: a clash must win over everything, because it is
        # the one state that blocks Save.
        if item.conflict:
            bg, border, bw = QColor("#fff0ec"), QColor("#cc3300"), 2.0
        elif item.deleted:
            bg, border, bw = QColor("#ffe0e0"), QColor("#c07070"), 1.4
        elif selected:
            bg, border, bw = QColor("#eaf3ff"), QColor("#2f6fb5"), 2.0
        elif hovered:
            bg, border, bw = QColor("#f5f9ff"), QColor("#9fb6d4"), 1.4
        else:
            bg, border, bw = QColor("#ffffff"), QColor("#c8ccd2"), 1.4

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(border, bw))
        p.drawRoundedRect(QRectF(tile).adjusted(bw / 2, bw / 2,
                                                -bw / 2, -bw / 2), 3.0, 3.0)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # The picture, on its own dark ground so a light photograph has an edge.
        pic = self._pic_rect(tile, pic_h)
        p.fillRect(pic, QColor("#1e1e1e"))
        pm = self._owner.thumb_for(item, pic.width())
        if pm is not None and not pm.isNull():
            scaled = pm.scaled(pic.size(), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            x = pic.left() + (pic.width() - scaled.width()) // 2
            y = pic.top() + (pic.height() - scaled.height()) // 2
            if item.deleted:
                p.setOpacity(0.35)
            p.drawPixmap(x, y, scaled)
            p.setOpacity(1.0)

        # The name: one line, cut off at the end.
        f = QFont(option.font)
        f.setPointSizeF(8.5)
        if item.conflict:
            f.setBold(True)
            ink = QColor("#b3200a")
        elif item.deleted:
            f.setStrikeOut(True)
            ink = QColor("#111111")
        elif item.renamed:
            f.setBold(True)
            ink = QColor("#1a4f9c")
        else:
            ink = QColor("#111111")
        p.setFont(f)
        p.setPen(QPen(ink))
        name_r = self._name_rect(tile, pic_h)
        fm = QFontMetrics(f)
        p.drawText(name_r, int(Qt.AlignmentFlag.AlignHCenter
                               | Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(item.new_name, Qt.TextElideMode.ElideRight,
                                 name_r.width()))

        badge = _badge_rect(tile)
        if item.deleted:
            _draw_undo_badge(p, badge)
        else:
            _draw_cross_badge(p, badge)
            if item.edited:
                d = badge.width()
                _draw_edited_badge(p, QRect(pic.left() + 4, pic.bottom() - d - 3,
                                            d, d))
        p.restore()

    # ---- the inline name editor --------------------------------------------
    def createEditor(self, parent, option, index):
        ed = QLineEdit(parent)
        ed.setStyleSheet(_EDITOR_QSS)
        ed.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        return ed

    def setEditorData(self, editor, index) -> None:
        item = self._owner.item_at(index.row())
        if item is not None:
            # The extension is never handed to the editor, so it cannot be lost.
            editor.setText(item.stem)
            editor.selectAll()

    def setModelData(self, editor, model, index) -> None:
        self._owner.rename_item(index.row(), editor.text())

    def updateEditorGeometry(self, editor, option, index) -> None:
        tile = option.rect.adjusted(_CARD_INSET, _CARD_INSET,
                                    -_CARD_INSET, -_CARD_INSET)
        name_r = self._name_rect(tile, self._owner.pic_height())
        # A little taller than the line, so the text is not clipped by the box.
        editor.setGeometry(QRect(name_r.left(), name_r.top() - 2,
                                 name_r.width(), _NAME_H + 5))


class _ThumbList(QListWidget):
    """The grid. Adds the cross-click, the hand cursor over it, Delete as a
    shortcut for crossing out, and the right-click menu."""

    def __init__(self, owner: "PhotoRenamerWidget"):
        super().__init__(owner)
        self._owner = owner
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(0)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setStyleSheet(_LIST_QSS)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._owner.relayout_grid()

    def _badge_hit(self, pos: QPoint):
        idx = self.indexAt(pos)
        if not idx.isValid():
            return None
        tile = self.visualRect(idx).adjusted(_CARD_INSET, _CARD_INSET,
                                             -_CARD_INSET, -_CARD_INSET)
        if _badge_rect(tile).contains(pos):
            return idx.row()
        return None

    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        if self._badge_hit(pos) is not None:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            row = self._badge_hit(e.position().toPoint())
            if row is not None:
                self._owner.toggle_delete([row])
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        # A second click on the cross must not open the rename editor.
        if self._badge_hit(e.position().toPoint()) is not None:
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            rows = sorted(i.row() for i in self.selectedIndexes())
            if rows:
                self._owner.toggle_delete(rows)
                e.accept()
                return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e) -> None:
        idx = self.indexAt(e.pos())
        if not idx.isValid():
            return
        row = idx.row()
        item = self._owner.item_at(row)
        if item is None:
            return
        self.setCurrentRow(row)

        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        a_open = menu.addAction("Open picture")
        a_show = menu.addAction("Show in Explorer")
        menu.addSeparator()
        a_rename = menu.addAction("Rename")
        a_mark = menu.addAction("Put the file back" if item.deleted
                                else "Mark the file for deletion")
        menu.addSeparator()
        a_table = menu.addAction("Add to table")
        chosen = menu.exec(e.globalPos())
        if chosen is None:
            return
        if chosen is a_table:
            self._owner.add_selection_to_table(row)
        elif chosen is a_open:
            self._owner.open_picture(row)
        elif chosen is a_show:
            self._owner.show_in_explorer(row)
        elif chosen is a_rename:
            self.edit(idx)
        elif chosen is a_mark:
            self._owner.toggle_delete([row])


# ─────────────────────────────────────────────────────────────────────────────
#  The preview panel on the right
# ─────────────────────────────────────────────────────────────────────────────
class _TextEntry(QLineEdit):
    """The box a Text click opens on the picture. Enter or a click elsewhere
    keeps the words, Esc throws them away."""
    commit = Signal(str)
    cancel = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._done = False
        self.setStyleSheet("QLineEdit { background: #ffffff; color: #111;"
                           " border: 1px solid #2f6fb5; padding: 0px 3px; }")
        self.returnPressed.connect(self._finish)

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self.commit.emit(self.text())

    def keyPressEvent(self, e) -> None:            # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self._done = True
            self.cancel.emit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:            # noqa: N802
        super().focusOutEvent(e)
        self._finish()


class _EditCanvas(QWidget):
    """The picture in the preview panel, and the surface the tools draw on.

    It shows a picture that already has its edits burnt in (render_edits), and
    turns a mouse drag into one new EditOp, in full-resolution pixels of the
    picture as it now is. While the drag is going on, the shape is drawn on top
    with the same draw_op() that will draw it for good.
    """
    op_done = Signal(object)        # an EditOp
    escape = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self._img: QImage | None = None
        self._frame = (0, 0)        # full-res size of the picture shown
        self._message = ""
        self._scaled: QPixmap | None = None
        self._scaled_for: tuple = ()
        self.tool = "view"
        self.colour = EDIT_COLOURS[0]
        self.level = 1
        self.can_edit = False
        self._live: EditOp | None = None
        self._entry: _TextEntry | None = None

    # ---- what is shown ------------------------------------------------------
    def set_message(self, text: str) -> None:
        self._close_entry()
        self._img = None
        self._scaled = None
        self._live = None
        self._message = text
        self.update()

    def set_picture(self, img: QImage, frame_w: int, frame_h: int) -> None:
        self._img = img
        self._frame = (frame_w, frame_h)
        self._scaled = None
        self._message = ""
        self.update()

    def has_picture(self) -> bool:
        return self._img is not None and not self._img.isNull()

    def set_tool(self, tool: str) -> None:
        self._close_entry()
        self._live = None
        self.tool = tool
        if tool == "view":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    # ---- geometry -----------------------------------------------------------
    def view_rect(self) -> QRectF:
        """Where the picture sits: as large as fits, centred, 2 px clear."""
        fw, fh = self._frame
        area = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if fw <= 0 or fh <= 0 or area.width() <= 0 or area.height() <= 0:
            return QRectF()
        k = min(area.width() / fw, area.height() / fh)
        w, h = fw * k, fh * k
        return QRectF(area.left() + (area.width() - w) / 2,
                      area.top() + (area.height() - h) / 2, w, h)

    def _to_pic(self, pos: QPointF) -> tuple[float, float]:
        vr = self.view_rect()
        fw, fh = self._frame
        x = (pos.x() - vr.left()) * fw / vr.width()
        y = (pos.y() - vr.top()) * fh / vr.height()
        return (min(max(x, 0.0), float(fw)), min(max(y, 0.0), float(fh)))

    def _view_scale(self) -> float:
        vr = self.view_rect()
        return vr.width() / self._frame[0] if self._frame[0] else 1.0

    def _longer(self) -> float:
        return float(max(self._frame))

    # ---- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:           # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1e1e1e"))
        p.setPen(QPen(QColor("#444444"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if not self.has_picture():
            p.setPen(QPen(QColor("#cfcfcf")))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter),
                       self._message)
            p.end()
            return
        vr = self.view_rect()
        dpr = self.devicePixelRatioF()
        want = (int(vr.width() * dpr), int(vr.height() * dpr))
        if self._scaled is None or self._scaled_for != want:
            self._scaled = QPixmap.fromImage(self._img.scaled(
                max(1, want[0]), max(1, want[1]),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self._scaled.setDevicePixelRatio(dpr)
            self._scaled_for = want
        p.drawPixmap(vr.topLeft(), self._scaled)

        op = self._live
        if op is not None:
            k = self._view_scale()
            if op.kind == "crop" and len(op.pts) >= 2:
                box = _crop_box(op, *self._frame)
                r = QRectF(vr.left() + box.left() * k, vr.top() + box.top() * k,
                           box.width() * k, box.height() * k)
                dim = QColor(0, 0, 0, 130)
                p.fillRect(QRectF(vr.left(), vr.top(), vr.width(),
                                  r.top() - vr.top()), dim)
                p.fillRect(QRectF(vr.left(), r.bottom(), vr.width(),
                                  vr.bottom() - r.bottom()), dim)
                p.fillRect(QRectF(vr.left(), r.top(), r.left() - vr.left(),
                                  r.height()), dim)
                p.fillRect(QRectF(r.right(), r.top(), vr.right() - r.right(),
                                  r.height()), dim)
                pen = QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
            else:
                p.save()
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.translate(vr.topLeft())
                p.scale(k, k)
                draw_op(p, op)
                p.restore()
        p.end()

    def resizeEvent(self, e) -> None:              # noqa: N802
        super().resizeEvent(e)
        self._close_entry()

    # ---- the mouse ----------------------------------------------------------
    def mousePressEvent(self, e) -> None:          # noqa: N802
        if (e.button() != Qt.MouseButton.LeftButton or self.tool == "view"
                or not self.can_edit or not self.has_picture()):
            super().mousePressEvent(e)
            return
        pos = e.position()
        if not self.view_rect().contains(pos):
            return
        pt = self._to_pic(pos)
        if self.tool == "text":
            self._open_entry(pos, pt)
            return
        width = _LINE_SHARE[self.level] * self._longer()
        if self.tool == "crop":
            width = 0.0
        pts = [pt] if self.tool == "pen" else [pt, pt]
        self._live = EditOp(kind=self.tool, pts=pts, colour=self.colour,
                            width=width)
        self.update()

    def mouseMoveEvent(self, e) -> None:           # noqa: N802
        op = self._live
        if op is None:
            return
        pt = self._to_pic(e.position())
        if op.kind == "pen":
            last = op.pts[-1]
            k = self._view_scale()
            # A new point only once the mouse has really moved, so a slow hand
            # does not leave thousands of points behind.
            if math.hypot((pt[0] - last[0]) * k, (pt[1] - last[1]) * k) >= 1.5:
                op.pts.append(pt)
        else:
            op.pts[-1] = pt
        self.update()

    def mouseReleaseEvent(self, e) -> None:        # noqa: N802
        op = self._live
        if op is None or e.button() != Qt.MouseButton.LeftButton:
            return
        self._live = None
        self.update()
        k = self._view_scale()
        (x0, y0), (x1, y1) = op.pts[0], op.pts[-1]
        tiny = math.hypot((x1 - x0) * k, (y1 - y0) * k) < 4
        if op.kind != "pen" and tiny:
            return                  # a plain click is not a shape
        if op.kind == "crop":
            box = _crop_box(op, *self._frame)
            if box.width() < 2 or box.height() < 2:
                return
        self.op_done.emit(op)

    def keyPressEvent(self, e) -> None:            # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            if self._live is not None:
                self._live = None
                self.update()
            else:
                self.escape.emit()
            return
        super().keyPressEvent(e)

    # ---- the text box -------------------------------------------------------
    def _open_entry(self, pos: QPointF, pt: tuple[float, float]) -> None:
        self._close_entry()
        height = _TEXT_SHARE[self.level] * self._longer()
        view_px = int(min(48, max(11, height * self._view_scale())))
        entry = _TextEntry(self)
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(view_px)
        entry.setFont(f)
        entry.setFixedHeight(view_px + 8)
        entry.setFixedWidth(max(120, min(360, self.width() - int(pos.x()) - 4)))
        entry.move(int(pos.x()), int(pos.y()))
        colour = self.colour

        def done(text: str) -> None:
            self._close_entry()
            if text.strip():
                self.op_done.emit(EditOp(kind="text", pts=[pt], colour=colour,
                                         width=height, text=text.strip()))

        entry.commit.connect(done)
        entry.cancel.connect(self._close_entry)
        self._entry = entry
        entry.show()
        entry.setFocus()

    def _close_entry(self) -> None:
        entry = self._entry
        self._entry = None
        if entry is not None:
            entry._done = True
            entry.hide()
            entry.deleteLater()


class _PreviewPanel(QWidget):
    """The picture on the right, and the drawing tools that work on it.

    The tools are icon-only squares in one row, handed out as `toolbar` for the
    main widget to place in its top bar.
    """
    tool_changed = Signal(str)
    rotate = Signal(int)
    undo = Signal()
    reset = Signal()

    _TOOLS = (("view", "Just look - no drawing; the panel follows the mouse"),
              ("ellipse", "Circle - drag around what to mark"),
              ("rect", "Rectangle - drag around what to mark"),
              ("arrow", "Arrow - drag from the tail to the point"),
              ("pen", "Pen - draw freehand"),
              ("text", "Text - click where the words should start, type,"
                       " press Enter"),
              ("crop", "Crop - drag the part of the picture to keep"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # ---- the drawing tools: ONE row, in its own widget ----
        # The panel builds it but does not hold it: the main widget puts it in
        # the top bar beside Save, so it costs the picture no height at all.
        self.toolbar = QWidget()
        self.toolbar.setStyleSheet("QWidget { background: transparent; }")
        tools = QHBoxLayout(self.toolbar)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(3)
        style = tools
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_btns: dict[str, QPushButton] = {}
        for name, tip in self._TOOLS:
            b = _tool_btn(action_icon(name), tip, checkable=True)
            self._tool_group.addButton(b)
            self._tool_btns[name] = b
            b.clicked.connect(lambda _on=False, n=name: self.set_tool(n))
            tools.addWidget(b)
        tools.addSpacing(8)
        self._btn_rot_l = _tool_btn(action_icon("rot_left"),
                                    "Turn the picture a quarter to the left")
        self._btn_rot_l.clicked.connect(lambda: self.rotate.emit(-1))
        tools.addWidget(self._btn_rot_l)
        self._btn_rot_r = _tool_btn(action_icon("rot_right"),
                                    "Turn the picture a quarter to the right")
        self._btn_rot_r.clicked.connect(lambda: self.rotate.emit(1))
        tools.addWidget(self._btn_rot_r)
        tools.addSpacing(8)

        self._colour_group = QButtonGroup(self)
        self._colour_group.setExclusive(True)
        self._colour_btns: list[QPushButton] = []
        names = ("red", "yellow", "green", "blue", "black", "white")
        for colour, cname in zip(EDIT_COLOURS, names):
            b = _tool_btn(swatch_icon(colour), f"Draw in {cname}", checkable=True)
            self._colour_group.addButton(b)
            self._colour_btns.append(b)
            b.clicked.connect(lambda _on=False, c=colour: self._set_colour(c))
            style.addWidget(b)
        style.addSpacing(8)
        self._width_group = QButtonGroup(self)
        self._width_group.setExclusive(True)
        self._width_btns: list[QPushButton] = []
        for level, wname in enumerate(("Thin", "Medium", "Thick")):
            b = _tool_btn(action_icon(f"width{level}"),
                          f"{wname} lines and {wname.lower()} text",
                          checkable=True)
            self._width_group.addButton(b)
            self._width_btns.append(b)
            b.clicked.connect(lambda _on=False, lv=level: self._set_level(lv))
            style.addWidget(b)
        style.addSpacing(8)
        self._btn_undo = _tool_btn(action_icon("undo"),
                                   "Undo the last edit of this picture (Ctrl+Z)")
        self._btn_undo.clicked.connect(self.undo.emit)
        style.addWidget(self._btn_undo)
        self._btn_reset = _tool_btn(action_icon("reset"),
                                    "Throw away every edit of this picture")
        self._btn_reset.clicked.connect(self.reset.emit)
        style.addWidget(self._btn_reset)
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Fixed,
                                   QSizePolicy.Policy.Fixed)

        self.canvas = _EditCanvas()
        self.canvas.escape.connect(lambda: self.set_tool("view"))
        lay.addWidget(self.canvas, 1)

        self._name = _ElidedLabel("")
        self._name.setStyleSheet("QLabel { color: #111; font-size: 13px;"
                                 " font-weight: 700; background: transparent; }")
        lay.addWidget(self._name)

        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setStyleSheet("QLabel { color: #3d4650; font-size: 11px;"
                                 " background: transparent; }")
        lay.addWidget(self._info)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_open = _btn("Open picture",
                             "Open this picture in the Windows viewer", "picture")
        self.btn_reveal = _btn("Show in Explorer",
                               "Open this picture's folder with the file selected",
                               "explorer")
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_reveal)
        row.addStretch(1)
        lay.addLayout(row)

        self._placeholder = "Move the mouse over a picture"
        self.canvas.set_message(self._placeholder)
        self._tool_btns["view"].setChecked(True)
        self._colour_btns[0].setChecked(True)
        self._width_btns[1].setChecked(True)
        self.set_enabled(False)
        self.set_edit_state(False, False)

    # ---- tools --------------------------------------------------------------
    def tool(self) -> str:
        return self.canvas.tool

    def editing(self) -> bool:
        """A drawing tool is chosen: the panel stays on its picture."""
        return self.canvas.tool != "view"

    def set_tool(self, name: str) -> None:
        btn = self._tool_btns.get(name)
        if btn is None:
            return
        btn.setChecked(True)
        changed = name != self.canvas.tool
        self.canvas.set_tool(name)
        if changed:
            self.tool_changed.emit(name)

    def _set_colour(self, colour: str) -> None:
        self.canvas.colour = colour

    def _set_level(self, level: int) -> None:
        self.canvas.level = level

    def set_edit_state(self, can_edit: bool, has_edits: bool) -> None:
        """Tools only for a picture that is shown and can be written back."""
        for name, b in self._tool_btns.items():
            if name != "view":
                b.setEnabled(can_edit)
        for b in (self._btn_rot_l, self._btn_rot_r, *self._colour_btns,
                  *self._width_btns):
            b.setEnabled(can_edit)
        self._btn_undo.setEnabled(has_edits)
        self._btn_reset.setEnabled(has_edits)
        # The chosen tool stays chosen -- the next picture may take it again --
        # but the canvas will not draw on this one.
        self.canvas.can_edit = can_edit

    # ---- what is shown ------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        self.btn_open.setEnabled(on)
        self.btn_reveal.setEnabled(on)

    def clear_all(self) -> None:
        self.canvas.set_message(self._placeholder)
        self._name.setText("")
        self._info.setText("")
        self.set_enabled(False)
        self.set_edit_state(False, False)

    def set_texts(self, name: str, lines: list[str]) -> None:
        self._name.setText(name)
        self._name.setToolTip(name)
        self._info.setText("\n".join(lines))

    def set_loading(self) -> None:
        self.canvas.set_message("loading")

    def set_picture(self, img: QImage | None, frame_w: int, frame_h: int) -> None:
        if img is None or img.isNull() or frame_w <= 0 or frame_h <= 0:
            self.canvas.set_message("cannot be shown")
            return
        self.canvas.set_picture(img, frame_w, frame_h)


# ─────────────────────────────────────────────────────────────────────────────
#  The Save preview table and the Recover dialog
# ─────────────────────────────────────────────────────────────────────────────
def _fill_table(table: QTableWidget, headers: list[str],
                rows: list[tuple[list[str], str]]) -> None:
    """rows is (cells, tint) where tint is "" or "delete".

    Alternating row colours are off on purpose: a tinted row has to be the only
    thing standing out. Every tint here is light, so the ink stays dark.
    """
    table.setStyleSheet(_TABLE_QSS)
    table.setAlternatingRowColors(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, (cells, tint) in enumerate(rows):
        for c, text in enumerate(cells):
            cell = QTableWidgetItem(text)
            if tint == "delete":
                cell.setBackground(QBrush(QColor("#ffe0e0")))
            else:
                cell.setBackground(QBrush(QColor("#ffffff")))
            cell.setForeground(QBrush(QColor("#111111" if tint else "#16202c")))
            table.setItem(r, c, cell)
    table.resizeColumnsToContents()
    if len(headers) > 1:
        table.horizontalHeader().setStretchLastSection(True)


class _SavePreviewDialog(QDialog):
    """What Save is about to do -- shown before a single file is touched."""

    def __init__(self, rows: list[tuple[list[str], str]], n_rename: int,
                 n_delete: int, parent=None, n_edit: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Save - what will happen")
        self.setStyleSheet("QDialog { background: #ffffff; color: #111; }")
        self.resize(820, 520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QLabel(f"{n_rename} to rename, {n_edit} edited, "
                      f"{n_delete} to delete")
        head.setStyleSheet("QLabel { color: #16202c; font-size: 12px;"
                           " font-weight: 700; background: transparent; }")
        lay.addWidget(head)

        table = QTableWidget(0, 0)
        _fill_table(table, ["Action", "Sub-folder", "Old name", "New name"], rows)
        lay.addWidget(table, 1)

        if n_delete:
            warn = QLabel(f"{n_delete} file(s) will be deleted permanently.")
            warn.setWordWrap(True)
            warn.setStyleSheet("QLabel { background: #ffe0e0; color: #8d1c12;"
                               " border: 1px solid #c07070; border-radius: 3px;"
                               " padding: 6px 8px; font-weight: 700; }")
            lay.addWidget(warn)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = _btn("Cancel", "Change nothing")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        apply_b = _btn("Apply", "Carry out the changes above", "save", "save")
        apply_b.clicked.connect(self.accept)
        apply_b.setDefault(True)
        row.addWidget(apply_b)
        lay.addLayout(row)


class _RecoverDialog(QDialog):
    """Pick which pending changes to throw away.

    One tick box per change, so a single rename can be put back while the rest of
    the work stays.
    """

    def __init__(self, changes: list[tuple[int, str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recover - undo pending changes")
        self.setStyleSheet("QDialog { background: #f3f3f3; color: #111; }")
        self.resize(660, 520)
        self._boxes: list[tuple[int, QCheckBox]] = []
        self._kinds: list[str] = [kind for _row, kind, _text in changes]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QLabel("Tick what should go back to how it was. "
                      "Nothing on disk has been changed yet.")
        head.setWordWrap(True)
        head.setStyleSheet("QLabel { color: #16202c; font-size: 12px;"
                           " background: transparent; }")
        lay.addWidget(head)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.StyledPanel)
        area.setStyleSheet("QScrollArea { background: #ffffff;"
                           " border: 1px solid #c8ccd2; }")
        inner = QWidget()
        inner.setStyleSheet("QWidget { background: #ffffff; }")
        ilay = QVBoxLayout(inner)
        ilay.setContentsMargins(10, 8, 10, 8)
        ilay.setSpacing(4)
        qss = _check_qss()
        for row_idx, kind, text in changes:
            box = QCheckBox(text)
            if kind == "delete":
                box.setStyleSheet(qss + "QCheckBox { color: #8d1c12;"
                                        " font-weight: 600; }")
            else:
                box.setStyleSheet(qss)
            ilay.addWidget(box)
            self._boxes.append((row_idx, box))
        ilay.addStretch(1)
        area.setWidget(inner)
        lay.addWidget(area, 1)

        row = QHBoxLayout()
        all_b = _btn("Select all")
        all_b.clicked.connect(lambda: self._set_all(True))
        row.addWidget(all_b)
        none_b = _btn("Select none")
        none_b.clicked.connect(lambda: self._set_all(False))
        row.addWidget(none_b)
        row.addStretch(1)
        cancel = _btn("Cancel", "Keep every pending change")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        revert = _btn("Revert selected", "Throw away the ticked changes",
                      "undo", "danger")
        revert.clicked.connect(self.accept)
        row.addWidget(revert)
        lay.addLayout(row)

    def _set_all(self, on: bool) -> None:
        for _row, box in self._boxes:
            box.setChecked(on)

    def selected_rows(self) -> list[int]:
        return [row for row, box in self._boxes if box.isChecked()]

    def selected(self) -> list[tuple[int, str]]:
        """(row, kind) for every ticked change."""
        return [(row, kind) for (row, box), kind in zip(self._boxes, self._kinds)
                if box.isChecked()]


# ─────────────────────────────────────────────────────────────────────────────
#  The program
# ─────────────────────────────────────────────────────────────────────────────
class PhotoRenamerWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root: Path | None = None
        self._items: list[Item] = []
        self._disk_names: dict[str, set[str]] = {}   # rel_dir -> every name in it
        self._thumbs: dict[tuple[str, int], QPixmap] = {}
        # Tiles with their edits burnt in: (path, thumb width, edit_rev).
        self._edit_thumbs: dict[tuple[str, int, int], QPixmap] = {}
        # Kept as QImage, not QPixmap: the edits are painted onto a copy of it.
        self._previews: dict[str, QImage] = {}
        self._pending: set[tuple[str, int]] = set()
        self._nat: dict[str, tuple[int, int]] = {}   # path -> full pixel size
        self._preview_path: Path | None = None
        self._hover_row = -1

        # The tile size, recomputed from the pane's width. Sensible values until
        # the first resize arrives.
        self._tile_w = MIN_TILE_W // 2
        self._pic_h = int((MIN_TILE_W // 2 - 2 * _CARD_INSET - 2 * _CARD_PAD)
                          * _PIC_ASPECT)
        self._tile_h = self._pic_h + _NAME_H + 2 * _CARD_PAD + 2 * _CARD_INSET + 2
        self._relayout_depth = 0
        self._split_needs_default = True
        self._min_locked = False
        self._table = None          # the Table tab, made in _build

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(8, (os.cpu_count() or 4))))
        self._sink = _LoadSink(self)
        self._sink.done.connect(self._on_loaded)

        # A mouse dragged across the grid must not queue a full-size read per
        # tile it passes over; only where it comes to rest.
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(110)
        self._hover_timer.timeout.connect(self._load_hover_preview)

        self._build()
        state = load_ui_state()
        last = state.get("last_folder")
        if isinstance(last, str) and last and Path(last).is_dir():
            QTimer.singleShot(0, lambda: self.open_folder(Path(last)))

    # ---- building -----------------------------------------------------------
    def _build(self) -> None:
        # Two tabs: everything below is the Photos tab; the Table tab lays the
        # pictures out in a comparison table and saves it as one picture.
        top = QVBoxLayout(self)
        top.setContentsMargins(4, 4, 4, 4)
        top.setSpacing(0)
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TABS_QSS)
        self._tabs.setDocumentMode(False)
        top.addWidget(self._tabs)
        photos = QWidget()
        self._tabs.addTab(photos, action_icon("picture"), "Photos")

        outer = QVBoxLayout(photos)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # The top bar is three side-by-side blocks in a grid: the folder, then
        # the buttons and the drawing tools, then the counts. It lives in its own
        # widget so its minimum width can be read off and made the window's
        # minimum -- which is what stops a small window from clipping a button.
        bar_host = QWidget()
        bar_host.setStyleSheet("QWidget { background: transparent; }")
        bar = QGridLayout(bar_host)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setHorizontalSpacing(10)

        # ---- left: the folder ----
        left = QWidget()
        llay = QHBoxLayout(left)
        llay.setContentsMargins(0, 0, 0, 0)
        llay.setSpacing(8)
        self._btn_open = _btn("Open folder", "Pick the folder with the pictures",
                              "folder")
        self._btn_open.clicked.connect(self._pick_folder)
        llay.addWidget(self._btn_open)
        self._lbl_folder = _ElidedLabel("No folder open")
        self._lbl_folder.setStyleSheet("QLabel { color: #16202c; font-size: 12px;"
                                       " background: transparent; }")
        llay.addWidget(self._lbl_folder, 1)

        # ---- middle: what you do ----
        mid = QWidget()
        mlay = QHBoxLayout(mid)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.setSpacing(8)
        self._btn_explorer = _btn("Open in Explorer",
                                  "Open the folder that is loaded, in Explorer",
                                  "explorer")
        self._btn_explorer.clicked.connect(self._open_root_in_explorer)
        mlay.addWidget(self._btn_explorer)

        self._btn_recover = _btn("Recover", "Undo pending changes, one by one",
                                 "undo")
        self._btn_recover.clicked.connect(self._recover)
        mlay.addWidget(self._btn_recover)

        self._btn_save = _btn("Save", "Carry out the renames and the deletions",
                              "save", "save")
        self._btn_save.clicked.connect(self._save)
        mlay.addWidget(self._btn_save)

        self._chk_quick = QCheckBox("Save without asking")
        self._chk_quick.setStyleSheet(_check_qss())
        self._chk_quick.setToolTip("On: Save gets on with it.\n"
                                   "Off: Save first shows a table of what it is "
                                   "about to do.")
        self._chk_quick.setChecked(bool(load_ui_state()
                                        .get("save_without_asking", True)))
        self._chk_quick.toggled.connect(
            lambda on: _remember("save_without_asking", bool(on)))
        self._chk_quick.setSizePolicy(QSizePolicy.Policy.Fixed,
                                      QSizePolicy.Policy.Fixed)
        mlay.addWidget(self._chk_quick)
        # The drawing tools join this row once the preview panel exists.
        self._mid_lay = mlay

        # ---- right: the counts ----
        # Separate short labels rather than one rich-text line: a rich-text
        # label cannot be shortened, and it would shove the buttons sideways
        # every time a number changed.
        # Two short lines, not one long one: with the drawing tools in this bar
        # a single line of counts would push the window's minimum width past
        # what a screen offers.
        right = QWidget()
        rlay = QGridLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setHorizontalSpacing(10)
        rlay.setVerticalSpacing(0)
        self._st_count = self._status_label("#16202c")
        self._st_ren = self._status_label("#1a4f9c")
        self._st_edit = self._status_label("#5b3b8c")
        self._st_del = self._status_label("#8d1c12")
        self._st_bad = self._status_label("#b3200a", bold=True)
        right_edge = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        rlay.addWidget(self._st_count, 0, 0, right_edge)
        rlay.addWidget(self._st_ren, 0, 1, right_edge)
        rlay.addWidget(self._st_edit, 0, 2, right_edge)
        rlay.addWidget(self._st_del, 1, 0, right_edge)
        rlay.addWidget(self._st_bad, 1, 1, 1, 2, right_edge)

        bar.addWidget(left, 0, 0)
        bar.addWidget(mid, 0, 1)
        bar.addWidget(right, 0, 2)
        # The folder name takes the spare room; the buttons, the tools and the
        # counts keep their own width.
        bar.setColumnStretch(0, 1)
        bar.setColumnStretch(1, 0)
        bar.setColumnStretch(2, 0)
        self._bar_host = bar_host
        outer.addWidget(bar_host)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet("QSplitter::handle { background: #d5d8dc; width: 4px; }")
        split.setChildrenCollapsible(False)
        self._list = _ThumbList(self)
        self._list.setItemDelegate(_TileDelegate(self))
        self._list.itemEntered.connect(self._on_item_entered)
        self._list.currentItemChanged.connect(
            lambda cur, _prev: self._show_row(self._list.row(cur) if cur else -1))
        self._list.setMinimumWidth(240)
        split.addWidget(self._list)

        self._preview = _PreviewPanel()
        self._preview.setMinimumWidth(240)
        self._mid_lay.addSpacing(14)
        self._mid_lay.addWidget(self._preview.toolbar)
        self._preview.btn_open.clicked.connect(
            lambda: self.open_picture(self._list.currentRow()))
        self._preview.btn_reveal.clicked.connect(
            lambda: self.show_in_explorer(self._list.currentRow()))
        self._preview.tool_changed.connect(self._on_tool_changed)
        self._preview.canvas.op_done.connect(self.add_edit)
        self._preview.rotate.connect(
            lambda turn: self.add_edit(EditOp(kind="rotate", turn=turn)))
        self._preview.undo.connect(self.undo_edit)
        self._preview.reset.connect(self.reset_edits)
        split.addWidget(self._preview)

        undo_key = QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), self)
        undo_key.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo_key.activated.connect(self.undo_edit)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        state = load_ui_state()
        sizes = state.get("split_sizes")
        if (isinstance(sizes, list) and len(sizes) == 2
                and all(isinstance(v, int) and v > 40 for v in sizes)):
            split.setSizes(sizes)
            self._split_needs_default = False
        self._split = split
        outer.addWidget(split, 1)

        # Imported here, not at the top: tb_t builds on this module.
        from tb_t import TableTab
        self._table = TableTab(self)
        self._tabs.addTab(self._table, action_icon("table"), "Table")

        self._refresh_buttons()

    @staticmethod
    def _status_label(colour: str, bold: bool = False) -> QLabel:
        lbl = QLabel("")
        lbl.setStyleSheet(f"QLabel {{ color: {colour}; font-size: 12px;"
                          f" font-weight: {700 if bold else 400};"
                          " background: transparent; }")
        lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lbl.setVisible(False)
        return lbl

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._lock_minimum_width()

    def _lock_minimum_width(self) -> None:
        """Make the window at least as wide as the top bar really needs.

        Measured on the first show, not while building: a layout that has not
        been activated yet reports a minimum far smaller than the truth, and the
        window then let itself be dragged down to where the button labels were
        cut off ("Open fol", "Save wit").

        The counts on the right are hidden while they are zero, so they would not
        be counted. They are filled with their longest wording for the
        measurement and then put back.
        """
        if self._min_locked:
            return
        self._min_locked = True
        counters = (self._st_count, self._st_ren, self._st_edit, self._st_del,
                    self._st_bad)
        samples = ("9999 pictures", "999 renamed", "999 edited", "999 to delete",
                   "999 names clash")
        was = [(lbl.text(), lbl.isVisible()) for lbl in counters]
        for lbl, text in zip(counters, samples):
            lbl.setText(text)
            lbl.setVisible(True)
        lay = self._bar_host.layout()
        lay.activate()
        need = lay.totalMinimumSize().width()
        for lbl, (text, vis) in zip(counters, was):
            lbl.setText(text)
            lbl.setVisible(vis)
        self.setMinimumWidth(need + 24)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._split_needs_default and self.width() > 200:
            # The preview starts at half the window, as asked.
            half = max(200, (self._split.width() - 4) // 2)
            self._split.setSizes([half, half])
            self._split_needs_default = False

    # ---- the grid's geometry ------------------------------------------------
    def tile_size(self) -> QSize:
        return QSize(self._tile_w, self._tile_h)

    def pic_height(self) -> int:
        return self._pic_h

    def columns_for(self, viewport_w: int) -> int:
        """Always two in a row; three once three tiles of MIN_TILE_W fit."""
        return max(MIN_COLUMNS, min(MAX_COLUMNS, viewport_w // MIN_TILE_W))

    def grid_width(self) -> int:
        """The width the tiles are measured against.

        NOT the viewport's width. Changing the grid size makes the scroll bar
        appear or disappear, that resizes the viewport, and the new width comes
        straight back in here with a different answer -- which froze the window
        solid the first time it was tried. So the room for the scroll bar is
        reserved whether it is showing or not, and the number never depends on
        it.
        """
        frame = 2 * self._list.frameWidth()
        bar = self._list.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent, None, self._list)
        return max(0, self._list.width() - frame - max(13, bar))

    def relayout_grid(self) -> None:
        """Re-measure the tiles for the pane's current width."""
        if self._relayout_depth > 1:
            return
        vw = self.grid_width()
        if vw < 20:
            return
        cols = self.columns_for(vw)
        # Never wider than the pane allows: a tile held at MIN_TILE_W in a
        # narrow pane wraps the grid down to a single column.
        tile_w = max(40, vw // cols)
        pic_h = int((tile_w - 2 * _CARD_INSET - 2 * _CARD_PAD) * _PIC_ASPECT)
        tile_h = pic_h + _NAME_H + 2 * _CARD_PAD + 2 * _CARD_INSET + 2
        if (tile_w, tile_h) == (self._tile_w, self._tile_h):
            return
        self._tile_w, self._pic_h, self._tile_h = tile_w, pic_h, tile_h
        self._relayout_depth += 1
        try:
            self._list.setGridSize(QSize(tile_w, tile_h))
            self._list.doItemsLayout()
        finally:
            self._relayout_depth -= 1

    # ---- opening a folder ---------------------------------------------------
    def _pick_folder(self) -> None:
        start = str(self._root) if self._root else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Open picture folder", start)
        if path:
            self.open_folder(Path(path))

    def open_folder(self, folder: Path) -> None:
        if self._pending_changes() and not self._ask_discard():
            return
        self._root = folder
        self._scan()
        _remember("last_folder", str(folder))

    def _ask_discard(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("There are changes that have not been saved.\n"
                    "Open another folder and throw them away?")
        box.setStyleSheet(_MSGBOX_QSS)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _scan(self) -> None:
        self._items = []
        self._disk_names = {}
        self._thumbs.clear()
        self._edit_thumbs.clear()
        self._previews.clear()
        self._pending.clear()
        self._nat.clear()
        self._preview.clear_all()
        self._preview_path = None
        self._hover_row = -1
        root = self._root
        if root is None or not root.is_dir():
            self._list.clear()
            self._lbl_folder.setText("No folder open")
            self._refresh_buttons()
            return

        found: list[Item] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.startswith(".")]
            here = Path(dirpath)
            try:
                rel = here.relative_to(root).as_posix()
            except ValueError:
                continue
            rel = "" if rel == "." else rel
            if rel == "":
                # The kept originals are not pictures to work on.
                dirnames[:] = [d for d in dirnames
                               if d.lower() != ORIGINALS_DIR.lower()]
            self._disk_names[rel] = {n.lower() for n in filenames}
            for name in filenames:
                p = here / name
                if p.suffix.lower() not in PICTURE_SUFFIXES:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                found.append(Item(path=p, rel_dir=rel, orig_stem=p.stem,
                                  suffix=p.suffix, size=size))
        found.sort(key=lambda it: (it.rel_dir.lower(), it.orig_stem.lower()))
        self._items = found

        self._list.clear()
        for row, it in enumerate(self._items):
            li = QListWidgetItem(it.orig_name)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsEditable)
            self._list.addItem(li)
            self._set_tooltip(row)

        self._lbl_folder.setText(root.name or str(root))
        self._lbl_folder.setToolTip(str(root))
        self._recompute()
        self.relayout_grid()
        self._queue_visible_thumbs()

    def _set_tooltip(self, row: int) -> None:
        it = self.item_at(row)
        li = self._list.item(row)
        if it is None or li is None:
            return
        lines = [it.new_name]
        if it.renamed:
            lines.append(f"was: {it.orig_name}")
        if it.rel_dir:
            lines.append(f"sub-folder: {it.rel_dir}")
        nat = self._nat.get(str(it.path))
        lines.append(f"{nat[0]} x {nat[1]} px, {human_size(it.size)}" if nat
                     else human_size(it.size))
        if it.edited and not it.deleted:
            lines.append(_edited_line(it))
        if it.deleted:
            lines.append("crossed out - Save will delete this file")
        if it.conflict:
            lines.append(f"name cannot be used: {it.conflict}")
        li.setToolTip("\n".join(lines))

    # ---- thumbnails ---------------------------------------------------------
    def item_at(self, row: int) -> Item | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def thumb_for(self, item: Item, pic_w: int) -> QPixmap | None:
        """The best thumbnail available now, asking for the right one if needed.

        When the exact size is not in the cache but another size of the same
        picture is, that one is drawn scaled -- otherwise every drag of the
        splitter would blank the whole grid while it re-reads the folder.
        """
        key = str(item.path)
        want = bucket_for(max(1, pic_w))
        pm = self._thumbs.get((key, want))
        if pm is None:
            if (key, want) not in self._pending:
                self._pending.add((key, want))
                self._pool.start(_LoadTask(item.path, want, self._sink))
            for b in _THUMB_BUCKETS:
                pm = self._thumbs.get((key, b))
                if pm is not None:
                    break
        if pm is None or pm.isNull() or not item.edited or item.deleted:
            return pm
        return self._edited_thumb(item, pm)

    def _edited_thumb(self, item: Item, pm: QPixmap) -> QPixmap:
        """The tile with the picture's edits burnt in, cached per edit."""
        key = str(item.path)
        nat = self._nat.get(key)
        if not nat:
            return pm
        ck = (key, pm.width(), item.edit_rev)
        hit = self._edit_thumbs.get(ck)
        if hit is None:
            if len(self._edit_thumbs) > 400:
                self._edit_thumbs.clear()
            hit = QPixmap.fromImage(render_edits(pm.toImage(), nat[0], nat[1],
                                                 item.edits))
            self._edit_thumbs[ck] = hit
        return hit

    def _queue_visible_thumbs(self) -> None:
        """Ask for the first screenful straight away; the rest arrive as the
        delegate paints them."""
        pic_w = self._tile_w - 2 * _CARD_INSET - 2 * _CARD_PAD
        for row in range(min(len(self._items), 80)):
            self.thumb_for(self._items[row], pic_w)

    def _on_loaded(self, key: str, max_side: int, img: QImage,
                   w: int, h: int) -> None:
        if w > 0 and h > 0:
            self._nat[key] = (w, h)
        if max_side == _PREVIEW_SIDE:
            if len(self._previews) > _PREVIEW_CACHE_MAX:
                self._previews.clear()
            self._previews[key] = img
            if self._preview_path is not None and str(self._preview_path) == key:
                self._update_canvas()
                self._refresh_preview_texts()
            return
        pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        self._pending.discard((key, max_side))
        if len(self._thumbs) > _THUMB_CACHE_MAX:
            self._thumbs.clear()
        self._thumbs[(key, max_side)] = pm
        self._list.viewport().update()
        if self._table is not None:
            self._table.owner_thumb_arrived()

    # ---- the preview panel --------------------------------------------------
    def _on_item_entered(self, li: QListWidgetItem) -> None:
        self._hover_row = self._list.row(li)
        # While a drawing tool is chosen the panel stays on its picture; only a
        # click on another tile moves it.
        if self._preview.editing():
            return
        self._show_row(self._hover_row, defer_picture=True)

    def _show_row(self, row: int, defer_picture: bool = False) -> None:
        item = self.item_at(row)
        if item is None:
            return
        self._preview_path = item.path
        self._preview.set_enabled(True)
        self._refresh_preview_texts()

        key = str(item.path)
        if key in self._previews:
            self._update_canvas()
            return
        self._preview.set_edit_state(False, item.edited)
        self._preview.set_loading()
        if defer_picture:
            self._hover_timer.start()
        else:
            self._load_hover_preview()

    def _shown_row(self) -> int:
        """The row the preview panel is showing, -1 for none."""
        if self._preview_path is None:
            return -1
        for row, it in enumerate(self._items):
            if it.path == self._preview_path:
                return row
        return -1

    def _update_canvas(self) -> None:
        """Put the shown picture, with its edits, into the panel."""
        item = self.item_at(self._shown_row())
        if item is None:
            return
        key = str(item.path)
        base = self._previews.get(key)
        if base is None:
            return
        nat = self._nat.get(key)
        if base.isNull() or not nat:
            self._preview.set_picture(None, 0, 0)
            self._preview.set_edit_state(False, item.edited)
            return
        fw, fh = frame_after(nat[0], nat[1], item.edits)
        self._preview.set_picture(render_edits(base, nat[0], nat[1], item.edits),
                                  fw, fh)
        self._preview.set_edit_state(can_write(item.suffix) and not item.deleted,
                                     item.edited)

    def _refresh_preview_texts(self) -> None:
        item = self.item_at(self._shown_row())
        if item is None:
            return
        nat = self._nat.get(str(item.path))
        if nat and item.edited:
            fw, fh = frame_after(nat[0], nat[1], item.edits)
            size = f"{fw} x {fh} px (was {nat[0]} x {nat[1]})"
            lines = [f"{size}, {human_size(item.size)}"]
        else:
            lines = [f"{nat[0]} x {nat[1]} px, {human_size(item.size)}" if nat
                     else human_size(item.size)]
        if item.rel_dir:
            lines.append(f"sub-folder: {item.rel_dir}")
        if item.renamed:
            lines.append(f"was: {item.orig_name}")
        if item.edited and not item.deleted:
            lines.append(_edited_line(item))
        if not can_write(item.suffix):
            lines.append(f"{item.suffix} pictures cannot be saved after editing")
        if item.deleted:
            lines.append("crossed out - Save will delete this file")
        if item.conflict:
            lines.append(f"name cannot be used: {item.conflict}")
        self._preview.set_texts(item.new_name, lines)

    def _load_hover_preview(self) -> None:
        if self._preview_path is None:
            return
        self._pool.start(_LoadTask(self._preview_path, _PREVIEW_SIDE, self._sink))

    # ---- opening things -----------------------------------------------------
    def _complain(self, title: str, what: str, exc: Exception) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"{what}\n\n{type(exc).__name__}: {exc}")
        box.setStyleSheet(_MSGBOX_QSS)
        box.exec()

    def open_picture(self, row: int) -> None:
        item = self.item_at(row)
        if item is None:
            return
        try:
            open_with_windows(item.path)
        except Exception as exc:
            self._complain("Cannot open the picture", str(item.path), exc)

    def show_in_explorer(self, row: int) -> None:
        item = self.item_at(row)
        if item is None:
            return
        try:
            reveal_in_explorer(item.path)
        except Exception as exc:
            self._complain("Cannot open Explorer", str(item.path), exc)

    def _open_root_in_explorer(self) -> None:
        if self._root is None:
            return
        try:
            open_with_windows(self._root)
        except Exception as exc:
            self._complain("Cannot open the folder", str(self._root), exc)

    # ---- editing ------------------------------------------------------------
    def rename_item(self, row: int, text: str) -> None:
        item = self.item_at(row)
        if item is None:
            return
        new_stem = text.strip()
        if new_stem == item.stem:
            return
        item.stem = new_stem
        # The list item's own text is never painted -- the delegate does all of
        # it -- but keyboard type-ahead searches it, so keep it in step.
        li = self._list.item(row)
        if li is not None:
            li.setText(item.new_name)
        self._recompute()
        self._list.viewport().update()
        if row in (self._list.currentRow(), self._hover_row):
            self._refresh_preview_texts()

    def toggle_delete(self, rows: list[int]) -> None:
        items = [self._items[r] for r in rows if 0 <= r < len(self._items)]
        if not items:
            return
        # A mixed selection is turned all-on rather than flipped item by item,
        # which is what a single Delete press on a mixed block should mean.
        target = not all(it.deleted for it in items)
        for it in items:
            it.deleted = target
        self._recompute()
        self._list.viewport().update()
        if self._shown_row() in rows:
            self._update_canvas()
            self._refresh_preview_texts()

    # ---- picture edits ------------------------------------------------------
    def _on_tool_changed(self, tool: str) -> None:
        """Choosing a drawing tool pins the panel to the picture it shows -- and
        makes that the current tile, so a click elsewhere is what moves it."""
        if tool == "view":
            return
        row = self._shown_row()
        if row >= 0 and row != self._list.currentRow():
            self._list.setCurrentRow(row)

    def _edits_changed(self, row: int) -> None:
        it = self.item_at(row)
        if it is None:
            return
        it.edit_rev += 1
        self._recompute()
        self._list.viewport().update()
        if row == self._shown_row():
            self._update_canvas()
            self._refresh_preview_texts()

    def add_edit(self, op: EditOp) -> None:
        """One more step on the picture the panel shows."""
        row = self._shown_row()
        it = self.item_at(row)
        if it is None or it.deleted or not can_write(it.suffix):
            return
        it.edits.append(op)
        self._edits_changed(row)

    def undo_edit(self) -> None:
        row = self._shown_row()
        it = self.item_at(row)
        if it is None or not it.edits:
            return
        it.edits.pop()
        self._edits_changed(row)

    def reset_edits(self) -> None:
        row = self._shown_row()
        it = self.item_at(row)
        if it is None or not it.edits:
            return
        it.edits.clear()
        self._edits_changed(row)

    # ---- the pending state --------------------------------------------------
    def _pending_changes(self) -> bool:
        return any(it.deleted or it.renamed or it.edited for it in self._items)

    @staticmethod
    def _name_problem(stem: str) -> str:
        if not stem:
            return "the name is empty"
        if any(ch in _INVALID_NAME_CHARS for ch in stem):
            bad = "".join(sorted({ch for ch in stem
                                  if ch in _INVALID_NAME_CHARS}))
            return f"not allowed in a file name: {bad}"
        if any(ord(ch) < 32 for ch in stem):
            return "not allowed in a file name"
        if stem.endswith("."):
            return "a name cannot end with a dot"
        return ""

    def _recompute(self) -> None:
        """Decide, for every item, whether its pending name can be used."""
        for it in self._items:
            it.conflict = ""

        # Two items in the same folder heading for the same name.
        by_dir: dict[str, dict[str, list[Item]]] = {}
        for it in self._items:
            if it.deleted:
                continue
            problem = self._name_problem(it.stem)
            if problem:
                it.conflict = problem
                continue
            by_dir.setdefault(it.rel_dir, {}).setdefault(
                it.new_name.lower(), []).append(it)
        for names in by_dir.values():
            for same in names.values():
                if len(same) > 1:
                    for it in same:
                        it.conflict = "another picture here takes this name"

        # A name already held by a file we are not managing (a text file, say).
        # A name held by a managed picture is covered by the test above: either
        # that picture keeps the name -- and then both land in the same bucket --
        # or it moves away and the name is free.
        managed: dict[str, set[str]] = {}
        for it in self._items:
            managed.setdefault(it.rel_dir, set()).add(it.orig_name.lower())
        for it in self._items:
            if it.deleted or it.conflict or not it.renamed:
                continue
            here = self._disk_names.get(it.rel_dir, set())
            low = it.new_name.lower()
            if low in here and low not in managed.get(it.rel_dir, set()):
                it.conflict = "another file here already has this name"

        for row in range(len(self._items)):
            self._set_tooltip(row)
        self._refresh_buttons()
        if self._table is not None:
            self._table.owner_changed()

    def _refresh_buttons(self) -> None:
        n_all = len(self._items)
        n_ren = sum(1 for it in self._items if it.renamed and not it.deleted)
        n_edit = sum(1 for it in self._items if it.edited and not it.deleted)
        n_del = sum(1 for it in self._items if it.deleted)
        n_bad = sum(1 for it in self._items if it.conflict)

        self._st_count.setText(f"{n_all} pictures")
        self._st_count.setVisible(n_all > 0)
        self._st_ren.setText(f"{n_ren} renamed")
        self._st_ren.setVisible(n_ren > 0)
        self._st_edit.setText(f"{n_edit} edited")
        self._st_edit.setVisible(n_edit > 0)
        self._st_del.setText(f"{n_del} to delete")
        self._st_del.setVisible(n_del > 0)
        self._st_bad.setText(f"{n_bad} name clashes" if n_bad == 1
                             else f"{n_bad} names clash")
        self._st_bad.setVisible(n_bad > 0)

        pending = n_ren > 0 or n_del > 0 or n_edit > 0
        self._btn_save.setEnabled(pending and n_bad == 0)
        self._btn_recover.setEnabled(pending)
        self._btn_explorer.setEnabled(self._root is not None)
        self._btn_save.setToolTip(
            "Fix the names marked in red first" if n_bad
            else "Carry out the renames, the edits and the deletions.\n"
                 f"The original of every edited picture is kept in "
                 f"\"{ORIGINALS_DIR}\".")

    # ---- Recover ------------------------------------------------------------
    def _recover(self) -> None:
        changes: list[tuple[int, str, str]] = []
        for row, it in enumerate(self._items):
            where = f"   [{it.rel_dir}]" if it.rel_dir else ""
            if it.deleted:
                changes.append((row, "delete", f"delete:  {it.orig_name}{where}"))
                continue
            if it.renamed:
                changes.append((row, "rename",
                                f"rename:  {it.orig_name}   →   "
                                f"{it.new_name}{where}"))
            if it.edited:
                n = len(it.edits)
                changes.append((row, "edit",
                                f"edit:  {it.orig_name}   ({n} step"
                                f"{'s' if n != 1 else ''}){where}"))
        if not changes:
            return
        dlg = _RecoverDialog(changes, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for row, kind in dlg.selected():
            it = self.item_at(row)
            if it is None:
                continue
            if kind in ("delete", "rename"):
                it.deleted = False
                it.stem = it.orig_stem
                li = self._list.item(row)
                if li is not None:
                    li.setText(it.orig_name)
            if kind in ("delete", "edit") and it.edits:
                it.edits.clear()
                it.edit_rev += 1
        self._recompute()
        self._list.viewport().update()
        self._update_canvas()
        self._refresh_preview_texts()

    # ---- Save ---------------------------------------------------------------
    def _save(self) -> None:
        renames = [it for it in self._items if it.renamed and not it.deleted]
        edits = [it for it in self._items if it.edited and not it.deleted]
        deletes = [it for it in self._items if it.deleted]
        if not renames and not deletes and not edits:
            return

        if not self._chk_quick.isChecked():
            rows: list[tuple[list[str], str]] = []
            for it in edits:
                rows.append((["edit", it.rel_dir, it.orig_name,
                              f"original kept in {ORIGINALS_DIR}"], ""))
            for it in renames:
                rows.append((["rename", it.rel_dir, it.orig_name, it.new_name], ""))
            for it in deletes:
                rows.append((["delete", it.rel_dir, it.orig_name, ""], "delete"))
            dlg = _SavePreviewDialog(rows, len(renames), len(deletes), self,
                                     n_edit=len(edits))
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

        results = self._apply(renames, deletes, edits)
        fails = [(cells, err) for cells, err in results if err]
        # The Table tab holds pictures by path: point it at the new names.
        moved = {}
        for it in renames:
            new = it.path.parent / it.new_name
            if new.exists() and not it.path.exists():
                moved[str(it.path)] = str(new)
        self._scan()
        if self._table is not None:
            self._table.after_save(moved)
        # Nothing is said when it all worked; only a failure is worth a window.
        if fails:
            self._report_failures(fails, len(results))

    def _report_failures(self, fails, total: int) -> None:
        shown = fails[:12]
        lines = [f"{cells[2]} - {err}" for cells, err in shown]
        if len(fails) > len(shown):
            lines.append(f"and {len(fails) - len(shown)} more")
        box = QMessageBox(self)
        box.setWindowTitle("Some files could not be done")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"{len(fails)} of {total} could not be done:")
        box.setInformativeText("\n".join(lines))
        box.setStyleSheet(_MSGBOX_QSS)
        box.exec()

    def _originals_target(self, it: Item) -> Path:
        """Where the untouched original of an edited picture is copied.

        Never over an existing file: a picture edited again in a later session
        finds its first original already there, and gets "name (2)" beside it.
        """
        folder = self._root / ORIGINALS_DIR
        if it.rel_dir:
            folder = folder / Path(it.rel_dir)
        target = folder / it.orig_name
        n = 2
        while target.exists():
            target = folder / f"{it.orig_stem} ({n}){it.suffix}"
            n += 1
        return target

    def _write_edit(self, it: Item) -> str:
        """Keep the original, then write the edited picture over the file.

        Returns "" when it worked, otherwise the reason. If the original could
        not be kept, the picture is left exactly as it was.
        """
        img, w, h = load_scaled(it.path, 0)
        if img.isNull() or w <= 0 or h <= 0:
            return "the picture could not be read"
        data = encode_image(render_edits(img, w, h, it.edits), it.suffix)
        if data is None:
            return f"{it.suffix} pictures cannot be written"

        target = self._originals_target(it)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"no {ORIGINALS_DIR} folder: {exc.strerror or exc}"
        err = _retry(lambda: shutil.copy2(it.path, target))
        if err:
            return f"original could not be kept, picture left unchanged: {err}"

        tmp = it.path.parent / f".__pr_{os.getpid()}_edit{it.suffix}"
        err = _retry(lambda: tmp.write_bytes(data))
        if not err:
            err = _retry(lambda: os.replace(tmp, it.path))
        if err:
            # Leave nothing behind: no temporary file, and no second copy of an
            # original whose picture was never changed.
            for leftover in (tmp, target):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
        return err

    def _apply(self, renames: list[Item], deletes: list[Item],
               edits: list[Item] = ()) -> list[tuple[tuple[str, str, str, str], str]]:
        """Carry out the changes. Returns (action, sub-folder, old, new), error.

        Edits go first, while every file still has its old name; the original
        is copied into ORIGINALS_DIR under that old name.

        Renames go through a temporary name first. Without that pass, swapping two
        names -- a.png becomes b.png while b.png becomes a.png -- fails on the
        first step, because the target still exists.

        The thumbnail readers are stopped and waited out before anything is
        touched. Windows refuses to rename or delete a file that is open for
        reading, and a picture whose thumbnail is still being fetched is exactly
        that: measured here, a Save straight after opening a folder lost both the
        rename and the deletion, with nothing but "Access is denied" to show for
        it. Each operation is then retried a couple of times as well, which also
        covers a file an image viewer has open for a moment.
        """
        self._stop_loading()

        out: list[tuple[tuple[str, str, str, str], str]] = []
        staged: list[tuple[Item, Path]] = []

        for it in edits:
            if it.deleted or not it.edits:
                continue
            out.append((("edit", it.rel_dir, it.orig_name, ""),
                        self._write_edit(it)))

        for n, it in enumerate(renames):
            tmp = it.path.parent / f".__pr_{os.getpid()}_{n}{it.suffix}"
            err = _retry(lambda p=it.path, t=tmp: p.rename(t))
            if err:
                out.append((("rename", it.rel_dir, it.orig_name, it.new_name),
                            err))
            else:
                staged.append((it, tmp))

        for it, tmp in staged:
            target = it.path.parent / it.new_name
            err = _retry(lambda t=tmp, g=target: t.rename(g))
            if err:
                # Put it back under its old name, so a failure leaves the folder
                # exactly as it was rather than full of hidden temporary files.
                _retry(lambda t=tmp, p=it.path: t.rename(p))
            out.append((("rename", it.rel_dir, it.orig_name, it.new_name), err))

        for it in deletes:
            err = _retry(lambda p=it.path: p.unlink())
            out.append((("delete", it.rel_dir, it.orig_name, ""), err))
        return out

    def _stop_loading(self) -> None:
        """Drop every queued picture read and wait for the running ones."""
        try:
            self._hover_timer.stop()
            self._pool.clear()
            self._pool.waitForDone(4000)
        except Exception:
            pass
        self._pending.clear()

    # ---- shutting down ------------------------------------------------------
    def has_pending_changes(self) -> bool:
        return self._pending_changes()

    def shutdown(self) -> None:
        state = load_ui_state()
        if self._root is not None:
            state["last_folder"] = str(self._root)
        state["split_sizes"] = list(self._split.sizes())
        state["save_without_asking"] = bool(self._chk_quick.isChecked())
        save_ui_state(state)
        if self._table is not None:
            self._table.shutdown()
        self._stop_loading()

    # ---- what the Table tab asks for ----------------------------------------
    def table_items(self) -> list[Item]:
        """The pictures the Table tab lists: everything not crossed out."""
        return [it for it in self._items if not it.deleted]

    def root_folder(self) -> Path | None:
        return self._root

    def edits_for_path(self, path: str) -> tuple[list, int]:
        """(pending edit steps, edit revision) of a picture; ([], 0) for one
        that is not in the open folder."""
        for it in self._items:
            if str(it.path) == path:
                return (it.edits if not it.deleted else []), it.edit_rev
        return [], 0

    def selected_paths(self) -> list[str]:
        rows = sorted({i.row() for i in self._list.selectedIndexes()})
        return [str(self._items[r].path) for r in rows
                if 0 <= r < len(self._items) and not self._items[r].deleted]

    def add_selection_to_table(self, row: int) -> None:
        """The tile menu's "Add to table": the selected pictures, or the one
        clicked, into the table's next empty cells."""
        paths = self.selected_paths()
        it = self.item_at(row)
        if not paths and it is not None and not it.deleted:
            paths = [str(it.path)]
        if self._table is not None and paths:
            n = self._table.add_paths(paths)
            self._flash_status(f"{n} added to the table")

    def _flash_status(self, text: str) -> None:
        self._st_count.setText(text)
        self._st_count.setVisible(True)
        QTimer.singleShot(3000, self._refresh_buttons)
