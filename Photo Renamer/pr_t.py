"""
pr_t.py  --  Photo Renamer: the whole program, as one QWidget.

Open a folder, look through the pictures in it (sub-folders included), rename them
by double-clicking, cross out the ones that should go, then Save. Recover walks the
pending changes and drops only the ones you pick.

Nothing on disk is touched until Save. Recover therefore only ever throws away
pending changes; once Save has run there is nothing left to undo.

Every colour in this file is stated. Windows here runs in dark mode, so anything
left to the theme comes out black on black.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QObject, QPoint, QPointF, QRect,
                            QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon, QImage,
                           QImageReader, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QStyle,
                               QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

PICTURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff",
                    ".webp"}

# Folders that never hold pictures worth showing.
_SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}

# The grid: at most six tiles in a row, fewer when they will not fit, and the
# tiles stretch so a row is always filled edge to edge -- no empty strip on the
# right. MIN_TILE_W is picked so that half of the default window gives six.
MAX_COLUMNS = 6
MIN_TILE_W = 100
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


_ICON_RECIPES = {
    "folder": _ico_folder,
    "explorer": _ico_explorer,
    "picture": _ico_picture,
    "save": _ico_save,
    "undo": _ico_undo,
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

    def __post_init__(self):
        if not self.stem:
            self.stem = self.orig_stem

    @property
    def renamed(self) -> bool:
        return self.stem != self.orig_stem

    @property
    def new_name(self) -> str:
        return self.stem + self.suffix

    @property
    def orig_name(self) -> str:
        return self.orig_stem + self.suffix


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
        chosen = menu.exec(e.globalPos())
        if chosen is None:
            return
        if chosen is a_open:
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
class _PreviewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._pic = QLabel()
        self._pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pic.setStyleSheet("QLabel { background: #1e1e1e;"
                                " border: 1px solid #444; color: #cfcfcf; }")
        self._pic.setMinimumHeight(200)
        self._pic.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Expanding)
        lay.addWidget(self._pic, 1)

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

        self._full: QPixmap | None = None
        self._placeholder = "Move the mouse over a picture"
        self._pic.setText(self._placeholder)
        self.set_enabled(False)

    def set_enabled(self, on: bool) -> None:
        self.btn_open.setEnabled(on)
        self.btn_reveal.setEnabled(on)

    def clear_all(self) -> None:
        self._full = None
        self._pic.setPixmap(QPixmap())
        self._pic.setText(self._placeholder)
        self._name.setText("")
        self._info.setText("")
        self.set_enabled(False)

    def set_texts(self, name: str, lines: list[str]) -> None:
        self._name.setText(name)
        self._name.setToolTip(name)
        self._info.setText("\n".join(lines))

    def set_loading(self) -> None:
        self._full = None
        self._pic.setPixmap(QPixmap())
        self._pic.setText("loading")

    def set_pixmap(self, pm: QPixmap | None) -> None:
        self._full = pm
        if pm is None or pm.isNull():
            self._pic.setPixmap(QPixmap())
            self._pic.setText("cannot be shown")
            return
        self._pic.setText("")
        self._rescale()

    def _rescale(self) -> None:
        if self._full is None or self._full.isNull():
            return
        area = self._pic.size()
        if area.width() < 8 or area.height() < 8:
            return
        self._pic.setPixmap(self._full.scaled(
            area - QSize(4, 4), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._rescale()


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
                 n_delete: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save - what will happen")
        self.setStyleSheet("QDialog { background: #ffffff; color: #111; }")
        self.resize(820, 520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QLabel(f"{n_rename} to rename, {n_delete} to delete")
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
        self._previews: dict[str, QPixmap] = {}
        self._pending: set[tuple[str, int]] = set()
        self._nat: dict[str, tuple[int, int]] = {}   # path -> full pixel size
        self._preview_path: Path | None = None
        self._hover_row = -1

        # The tile size, recomputed from the pane's width. Sensible values until
        # the first resize arrives.
        self._tile_w = MIN_TILE_W
        self._pic_h = int((MIN_TILE_W - 2 * _CARD_INSET - 2 * _CARD_PAD)
                          * _PIC_ASPECT)
        self._tile_h = self._pic_h + _NAME_H + 2 * _CARD_PAD + 2 * _CARD_INSET + 2
        self._relayout_depth = 0
        self._split_needs_default = True
        self._min_locked = False

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # The top bar is three side-by-side blocks in a grid, with the outer two
        # sharing the spare room equally: that is what puts the buttons in the
        # middle instead of hard against the right edge. It lives in its own
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

        # ---- right: the counts ----
        # Separate short labels rather than one rich-text line: a rich-text
        # label cannot be shortened, and it would shove the buttons sideways
        # every time a number changed.
        right = QWidget()
        rlay = QHBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(10)
        rlay.addStretch(1)
        self._st_count = self._status_label("#16202c")
        self._st_ren = self._status_label("#1a4f9c")
        self._st_del = self._status_label("#8d1c12")
        self._st_bad = self._status_label("#b3200a", bold=True)
        for lbl in (self._st_count, self._st_ren, self._st_del, self._st_bad):
            rlay.addWidget(lbl)

        bar.addWidget(left, 0, 0)
        bar.addWidget(mid, 0, 1)
        bar.addWidget(right, 0, 2)
        bar.setColumnStretch(0, 1)
        bar.setColumnStretch(1, 0)
        bar.setColumnStretch(2, 1)
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
        self._preview.btn_open.clicked.connect(
            lambda: self.open_picture(self._list.currentRow()))
        self._preview.btn_reveal.clicked.connect(
            lambda: self.show_in_explorer(self._list.currentRow()))
        split.addWidget(self._preview)
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
        counters = (self._st_count, self._st_ren, self._st_del, self._st_bad)
        samples = ("9999 pictures", "999 renamed", "999 to delete",
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
        """At most six in a row, fewer when they will not fit."""
        return max(1, min(MAX_COLUMNS, viewport_w // MIN_TILE_W))

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
        tile_w = max(MIN_TILE_W, vw // cols)
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
        if pm is not None:
            return pm
        if (key, want) not in self._pending:
            self._pending.add((key, want))
            self._pool.start(_LoadTask(item.path, want, self._sink))
        for b in _THUMB_BUCKETS:
            other = self._thumbs.get((key, b))
            if other is not None:
                return other
        return None

    def _queue_visible_thumbs(self) -> None:
        """Ask for the first screenful straight away; the rest arrive as the
        delegate paints them."""
        pic_w = self._tile_w - 2 * _CARD_INSET - 2 * _CARD_PAD
        for row in range(min(len(self._items), 80)):
            self.thumb_for(self._items[row], pic_w)

    def _on_loaded(self, key: str, max_side: int, img: QImage,
                   w: int, h: int) -> None:
        pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        if w > 0 and h > 0:
            self._nat[key] = (w, h)
        if max_side == _PREVIEW_SIDE:
            if len(self._previews) > _PREVIEW_CACHE_MAX:
                self._previews.clear()
            self._previews[key] = pm
            if self._preview_path is not None and str(self._preview_path) == key:
                self._preview.set_pixmap(pm)
                self._refresh_preview_texts()
            return
        self._pending.discard((key, max_side))
        if len(self._thumbs) > _THUMB_CACHE_MAX:
            self._thumbs.clear()
        self._thumbs[(key, max_side)] = pm
        self._list.viewport().update()

    # ---- the preview panel --------------------------------------------------
    def _on_item_entered(self, li: QListWidgetItem) -> None:
        self._hover_row = self._list.row(li)
        self._show_row(self._hover_row, defer_picture=True)

    def _show_row(self, row: int, defer_picture: bool = False) -> None:
        item = self.item_at(row)
        if item is None:
            return
        self._preview_path = item.path
        self._preview.set_enabled(True)
        self._refresh_preview_texts()

        key = str(item.path)
        cached = self._previews.get(key)
        if cached is not None:
            self._preview.set_pixmap(cached)
            return
        self._preview.set_loading()
        if defer_picture:
            self._hover_timer.start()
        else:
            self._load_hover_preview()

    def _refresh_preview_texts(self) -> None:
        if self._preview_path is None:
            return
        item = next((it for it in self._items if it.path == self._preview_path),
                    None)
        if item is None:
            return
        nat = self._nat.get(str(item.path))
        lines = [f"{nat[0]} x {nat[1]} px, {human_size(item.size)}" if nat
                 else human_size(item.size)]
        if item.rel_dir:
            lines.append(f"sub-folder: {item.rel_dir}")
        if item.renamed:
            lines.append(f"was: {item.orig_name}")
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
        if self._list.currentRow() in rows:
            self._refresh_preview_texts()

    # ---- the pending state --------------------------------------------------
    def _pending_changes(self) -> bool:
        return any(it.deleted or it.renamed for it in self._items)

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

    def _refresh_buttons(self) -> None:
        n_all = len(self._items)
        n_ren = sum(1 for it in self._items if it.renamed and not it.deleted)
        n_del = sum(1 for it in self._items if it.deleted)
        n_bad = sum(1 for it in self._items if it.conflict)

        self._st_count.setText(f"{n_all} pictures")
        self._st_count.setVisible(n_all > 0)
        self._st_ren.setText(f"{n_ren} renamed")
        self._st_ren.setVisible(n_ren > 0)
        self._st_del.setText(f"{n_del} to delete")
        self._st_del.setVisible(n_del > 0)
        self._st_bad.setText(f"{n_bad} name clashes" if n_bad == 1
                             else f"{n_bad} names clash")
        self._st_bad.setVisible(n_bad > 0)

        pending = n_ren > 0 or n_del > 0
        self._btn_save.setEnabled(pending and n_bad == 0)
        self._btn_recover.setEnabled(pending)
        self._btn_explorer.setEnabled(self._root is not None)
        self._btn_save.setToolTip("Fix the names marked in red first" if n_bad
                                  else "Carry out the renames and the deletions")

    # ---- Recover ------------------------------------------------------------
    def _recover(self) -> None:
        changes: list[tuple[int, str, str]] = []
        for row, it in enumerate(self._items):
            where = f"   [{it.rel_dir}]" if it.rel_dir else ""
            if it.deleted:
                changes.append((row, "delete", f"delete:  {it.orig_name}{where}"))
            elif it.renamed:
                changes.append((row, "rename",
                                f"rename:  {it.orig_name}   →   "
                                f"{it.new_name}{where}"))
        if not changes:
            return
        dlg = _RecoverDialog(changes, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for row in dlg.selected_rows():
            it = self.item_at(row)
            if it is None:
                continue
            it.deleted = False
            it.stem = it.orig_stem
            li = self._list.item(row)
            if li is not None:
                li.setText(it.orig_name)
        self._recompute()
        self._list.viewport().update()
        self._refresh_preview_texts()

    # ---- Save ---------------------------------------------------------------
    def _save(self) -> None:
        renames = [it for it in self._items if it.renamed and not it.deleted]
        deletes = [it for it in self._items if it.deleted]
        if not renames and not deletes:
            return

        if not self._chk_quick.isChecked():
            rows: list[tuple[list[str], str]] = []
            for it in renames:
                rows.append((["rename", it.rel_dir, it.orig_name, it.new_name], ""))
            for it in deletes:
                rows.append((["delete", it.rel_dir, it.orig_name, ""], "delete"))
            dlg = _SavePreviewDialog(rows, len(renames), len(deletes), self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

        results = self._apply(renames, deletes)
        fails = [(cells, err) for cells, err in results if err]
        self._scan()
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

    def _apply(self, renames: list[Item],
               deletes: list[Item]) -> list[tuple[tuple[str, str, str, str], str]]:
        """Carry out the changes. Returns (action, sub-folder, old, new), error.

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
        self._stop_loading()
