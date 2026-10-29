"""
tb_t.py  --  Photo Renamer: the Table tab.

Lays pictures out in a table -- column headings on top, row headings on the
left, a corner label, a title above and notes below -- and saves the result as
ONE picture, or copies it to the clipboard.

Nothing of a photograph is thrown away. A column is as wide as its widest
picture and a row as tall as its tallest one, in the pictures' own pixels, so
two 1920 x 1080 frames side by side make a 3840 x 1080 picture area. A smaller
picture is centred in its cell and enlarged to fill it (its shape kept), never
shrunk. Headings, lines, title and notes are added around that area, and every
size in them follows the size of the cells, so a 4K table and a small one look
alike.

The on-screen preview is drawn by the same render_table() as the saved file,
only scaled down -- what is shown is what is saved.

Every colour here is stated; Windows here runs in dark mode.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from PySide6.QtCore import (QMarginsF, QMimeData, QPoint, QPointF, QRect,
                            QRectF, QSize, QSizeF, Qt, QThreadPool, QTimer,
                            QUrl)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QFontMetricsF,
                           QGuiApplication, QImage, QPageLayout, QPageSize,
                           QPainter, QPdfWriter, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QSizePolicy,
                               QSpinBox, QSplitter, QStyle,
                               QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

import pr_t
from pr_t import (_LIST_QSS, _MENU_QSS, _MSGBOX_QSS, PICTURE_SUFFIXES,
                  _btn, _check_qss, _ipen, _LoadSink, _LoadTask, _tool_btn,
                  action_icon, encode_image, frame_after, load_scaled,
                  render_edits)

# The settings file is reached through pr_t.load_ui_state / pr_t._remember
# every time, never imported by name, so a test that swaps them out in pr_t
# swaps them out here too.

MAX_ROWS = 40
MAX_COLS = 40

_HEAD_BG = "#d9d9d9"          # the heading band, as in the hand-made tables
_LINE = "#404040"             # the lines between cells
_INK = "#111111"

_PREVIEW_DECODE = 900         # how large a picture is decoded for the preview
_PREVIEW_MAX = 2200           # longest side of the preview image
_THUMB_PX = 200               # the pictures in the table editor

# QImage cannot be wider or taller than this, and a picture over ~1.4 GB of
# pixels is more than a 32-bit allocation will reliably give.
_MAX_SIDE = 32767
_MAX_BYTES = 1_400_000_000

_TEMPLATE_KIND = "photo-renamer-table"


# ─────────────────────────────────────────────────────────────────────────────
#  Icons for this tab, drawn in the same 20x20 grid as the rest
# ─────────────────────────────────────────────────────────────────────────────
def _ico_swap(p: QPainter, c: QColor) -> None:
    # Two arrows going opposite ways: rows become columns.
    p.setPen(_ipen(c, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(3.0, 6.6), QPointF(16.4, 6.6))
    p.drawPolyline(QPolygonF([QPointF(12.8, 3.0), QPointF(16.4, 6.6),
                              QPointF(12.8, 10.2)]))
    p.drawLine(QPointF(17.0, 13.4), QPointF(3.6, 13.4))
    p.drawPolyline(QPolygonF([QPointF(7.2, 9.8), QPointF(3.6, 13.4),
                              QPointF(7.2, 17.0)]))


def _ico_copy(p: QPainter, c: QColor) -> None:
    # Two sheets, one over the other.
    p.setPen(_ipen(c, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7.0, 2.6, 10.4, 12.2), 1.4, 1.4)
    p.drawPolyline(QPolygonF([QPointF(4.2, 6.0), QPointF(3.0, 6.0),
                              QPointF(3.0, 17.4), QPointF(12.6, 17.4),
                              QPointF(12.6, 16.4)]))


def _ico_minus(p: QPainter, c: QColor) -> None:
    p.setPen(_ipen(c, 2.2))
    p.drawLine(QPointF(4.6, 10.0), QPointF(15.4, 10.0))


def _ico_plus(p: QPainter, c: QColor) -> None:
    p.setPen(_ipen(c, 2.2))
    p.drawLine(QPointF(4.6, 10.0), QPointF(15.4, 10.0))
    p.drawLine(QPointF(10.0, 4.6), QPointF(10.0, 15.4))


def _ico_table(p: QPainter, c: QColor) -> None:
    # A grid with a filled heading row.
    p.setPen(_ipen(c, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(2.6, 3.4, 14.8, 13.2))
    p.drawLine(QPointF(2.6, 7.6), QPointF(17.4, 7.6))
    p.drawLine(QPointF(2.6, 12.1), QPointF(17.4, 12.1))
    p.drawLine(QPointF(7.6, 3.4), QPointF(7.6, 16.6))
    p.drawLine(QPointF(12.5, 3.4), QPointF(12.5, 16.6))


pr_t._ICON_RECIPES.update({"swap": _ico_swap, "copy": _ico_copy,
                           "minus": _ico_minus, "plus": _ico_plus,
                           "table": _ico_table})


# ─────────────────────────────────────────────────────────────────────────────
#  What a table is
# ─────────────────────────────────────────────────────────────────────────────
def _clamp_int(v, default: int, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return max(lo, min(hi, int(v)))


@dataclass
class TableSpec:
    """The table, without any pixels. Also exactly what a template file holds.

    The label and cell lists may be LONGER than rows / cols: making the table
    smaller only hides what is past the edge, so typing a smaller number by
    mistake and back again loses nothing. Only the visible part is saved.
    """
    rows: int = 2
    cols: int = 3
    corner: str = ""
    col_labels: list = field(default_factory=list)
    row_labels: list = field(default_factory=list)
    cells: list = field(default_factory=list)       # [row][col] -> path, "" empty
    title: str = ""
    notes: str = ""
    col_heads: bool = True
    row_heads: bool = True
    text_pct: int = 100         # text size, % of the automatic size
    margin_pct: int = 2         # white edge around each picture, % of a cell
    size_pct: int = 100         # the saved picture, % of full resolution

    def __post_init__(self) -> None:
        self.fit()

    def fit(self) -> None:
        self.rows = _clamp_int(self.rows, 2, 1, MAX_ROWS)
        self.cols = _clamp_int(self.cols, 3, 1, MAX_COLS)
        while len(self.col_labels) < self.cols:
            self.col_labels.append("")
        while len(self.row_labels) < self.rows:
            self.row_labels.append("")
        while len(self.cells) < self.rows:
            self.cells.append([])
        for row in self.cells:
            while len(row) < self.cols:
                row.append("")

    def trim(self) -> None:
        """Forget whatever is past the visible edge."""
        self.col_labels = self.col_labels[:self.cols]
        self.row_labels = self.row_labels[:self.rows]
        self.cells = [row[:self.cols] for row in self.cells[:self.rows]]

    def resize(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self.fit()

    # ---- cells --------------------------------------------------------------
    def cell(self, r: int, c: int) -> str:
        return self.cells[r][c]

    def set_cell(self, r: int, c: int, path: str) -> None:
        self.cells[r][c] = path

    def paths(self) -> list[str]:
        """Every picture in the visible table, once, in reading order."""
        out: list[str] = []
        for r in range(self.rows):
            for c in range(self.cols):
                p = self.cells[r][c]
                if p and p not in out:
                    out.append(p)
        return out

    def has_pictures(self) -> bool:
        return bool(self.paths())

    def clear_pictures(self) -> None:
        for row in self.cells:
            for c in range(len(row)):
                row[c] = ""

    def place(self, paths: list[str], by_columns: bool = False,
              start: tuple[int, int] = (0, 0), only_empty: bool = False) -> int:
        """Put pictures into the table in reading order from `start`.

        The table grows -- by rows, or by columns when filling down the columns
        -- when there are more pictures than cells. only_empty skips cells that
        already hold a picture. Returns how many were placed.
        """
        placed = 0
        r, c = start
        todo = list(paths)
        while todo:
            if by_columns:
                if r >= self.rows:
                    r, c = 0, c + 1
                if c >= self.cols:
                    if self.cols >= MAX_COLS:
                        break
                    self.resize(self.rows, self.cols + 1)
            else:
                if c >= self.cols:
                    r, c = r + 1, 0
                if r >= self.rows:
                    if self.rows >= MAX_ROWS:
                        break
                    self.resize(self.rows + 1, self.cols)
            if not (only_empty and self.cells[r][c]):
                self.cells[r][c] = todo.pop(0)
                placed += 1
            if by_columns:
                r += 1
            else:
                c += 1
        return placed

    # ---- shape --------------------------------------------------------------
    def transpose(self) -> None:
        """Rows become columns and columns rows, headings and pictures too."""
        self.trim()
        old_r, old_c = self.rows, self.cols
        old = self.cells
        self.cells = [[old[j][i] for j in range(old_r)] for i in range(old_c)]
        self.rows, self.cols = old_c, old_r
        self.col_labels, self.row_labels = self.row_labels, self.col_labels
        self.col_heads, self.row_heads = self.row_heads, self.col_heads
        self.fit()

    def insert_row(self, i: int) -> None:
        if self.rows >= MAX_ROWS:
            return
        self.trim()
        i = max(0, min(self.rows, i))
        self.row_labels.insert(i, "")
        self.cells.insert(i, [""] * self.cols)
        self.rows += 1
        self.fit()

    def delete_row(self, i: int) -> None:
        if self.rows <= 1 or not 0 <= i < self.rows:
            return
        self.trim()
        del self.row_labels[i]
        del self.cells[i]
        self.rows -= 1
        self.fit()

    def insert_col(self, i: int) -> None:
        if self.cols >= MAX_COLS:
            return
        self.trim()
        i = max(0, min(self.cols, i))
        self.col_labels.insert(i, "")
        for row in self.cells:
            row.insert(i, "")
        self.cols += 1
        self.fit()

    def delete_col(self, i: int) -> None:
        if self.cols <= 1 or not 0 <= i < self.cols:
            return
        self.trim()
        del self.col_labels[i]
        for row in self.cells:
            del row[i]
        self.cols -= 1
        self.fit()

    # ---- file form ----------------------------------------------------------
    def to_dict(self, pictures: bool = True) -> dict:
        R, C = self.rows, self.cols
        return {
            "kind": _TEMPLATE_KIND, "version": 1,
            "rows": R, "cols": C,
            "corner": self.corner,
            "col_labels": list(self.col_labels[:C]),
            "row_labels": list(self.row_labels[:R]),
            "cells": ([list(row[:C]) for row in self.cells[:R]] if pictures
                      else [[""] * C for _ in range(R)]),
            "title": self.title, "notes": self.notes,
            "col_heads": self.col_heads, "row_heads": self.row_heads,
            "text_pct": self.text_pct, "margin_pct": self.margin_pct,
            "size_pct": self.size_pct,
        }

    @classmethod
    def from_dict(cls, d) -> "TableSpec":
        """Tolerant: anything missing or of the wrong type takes its default."""
        if not isinstance(d, dict):
            return cls()

        def text(k: str) -> str:
            v = d.get(k)
            return v if isinstance(v, str) else ""

        def texts(k: str) -> list:
            v = d.get(k)
            return ([x if isinstance(x, str) else "" for x in v]
                    if isinstance(v, list) else [])

        raw = d.get("cells")
        cells = ([[x if isinstance(x, str) else "" for x in row]
                  for row in raw if isinstance(row, list)]
                 if isinstance(raw, list) else [])
        return cls(rows=_clamp_int(d.get("rows"), 2, 1, MAX_ROWS),
                   cols=_clamp_int(d.get("cols"), 3, 1, MAX_COLS),
                   corner=text("corner"),
                   col_labels=texts("col_labels"),
                   row_labels=texts("row_labels"),
                   cells=cells, title=text("title"), notes=text("notes"),
                   col_heads=bool(d.get("col_heads", True)),
                   row_heads=bool(d.get("row_heads", True)),
                   text_pct=_clamp_int(d.get("text_pct"), 100, 30, 400),
                   margin_pct=_clamp_int(d.get("margin_pct"), 2, 0, 20),
                   size_pct=_clamp_int(d.get("size_pct"), 100, 1, 100))

    def part(self, rows: list[int], cols: list[int]) -> "TableSpec":
        """These rows and columns only, headings, title and notes kept."""
        return TableSpec(rows=len(rows), cols=len(cols), corner=self.corner,
                         col_labels=[self.col_labels[c] for c in cols],
                         row_labels=[self.row_labels[r] for r in rows],
                         cells=[[self.cells[r][c] for c in cols] for r in rows],
                         title=self.title, notes=self.notes,
                         col_heads=self.col_heads, row_heads=self.row_heads,
                         text_pct=self.text_pct, margin_pct=self.margin_pct)


# ─────────────────────────────────────────────────────────────────────────────
#  Measuring and drawing -- one code path for the preview and the saved file
# ─────────────────────────────────────────────────────────────────────────────
def _font(px: float, bold: bool) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bold)
    return f


_CENTRE = int(Qt.AlignmentFlag.AlignCenter)
_WRAP_CENTRE = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
                   | Qt.TextFlag.TextWordWrap)
_WRAP_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                 | Qt.TextFlag.TextWordWrap)


def _text_box(text: str, font: QFont, wrap_w: float | None = None,
              flags: int = _CENTRE) -> tuple[float, float]:
    """Width and height the text takes; lines broken only where typed unless
    wrap_w is given."""
    fm = QFontMetricsF(font)
    w = wrap_w if wrap_w is not None else 1e6
    r = fm.boundingRect(QRectF(0, 0, max(1.0, w), 1e6), flags, text or " ")
    return r.width(), r.height()


@dataclass
class TableLayout:
    """Where everything goes, in the saved picture's own pixels."""
    width: int = 1
    height: int = 1
    lw: int = 1                 # line width
    margin: int = 0             # white edge around each picture
    tpad: int = 4               # space around text
    font_px: int = 12           # heading text
    title_px: int = 16
    notes_px: int = 11
    head_x: int = 0             # the heading column
    head_w: int = 0
    head_y: int = 0             # the heading row
    head_h: int = 0
    col_x: list = field(default_factory=list)   # left of each column's cell
    col_w: list = field(default_factory=list)   # picture width (cell - margins)
    row_y: list = field(default_factory=list)
    row_h: list = field(default_factory=list)
    grid_y: int = 0             # top of the table's outer line
    grid_w: int = 1
    grid_h: int = 1
    title_h: int = 0
    notes_h: int = 0

    def cell_rect(self, r: int, c: int) -> QRect:
        m = self.margin
        return QRect(self.col_x[c], self.row_y[r],
                     self.col_w[c] + 2 * m, self.row_h[r] + 2 * m)

    def picture_rect(self, r: int, c: int) -> QRect:
        return QRect(self.col_x[c] + self.margin, self.row_y[r] + self.margin,
                     self.col_w[c], self.row_h[r])


def measure_table(spec: TableSpec, sizes: dict) -> TableLayout:
    """sizes: path -> (width, height) of the picture as it will be drawn,
    edits included. A picture with no size yet counts as an empty cell."""
    R, C = spec.rows, spec.cols
    got = [[sizes.get(spec.cells[r][c]) if spec.cells[r][c] else None
            for c in range(C)] for r in range(R)]
    known = [s for row in got for s in row if s]
    dw = int(median(s[0] for s in known)) if known else 640
    dh = int(median(s[1] for s in known)) if known else 480
    pic_w = [max([got[r][c][0] for r in range(R) if got[r][c]] or [dw])
             for c in range(C)]
    pic_h = [max([got[r][c][1] for c in range(C) if got[r][c]] or [dh])
             for r in range(R)]

    lay = TableLayout()
    ref = max(60, int(median(pic_h)))
    m = lay.margin = int(round(ref * spec.margin_pct / 100.0))
    lw = lay.lw = max(1, int(round(ref * 0.002)))
    fpx = lay.font_px = max(9, int(round(ref * 0.035 * spec.text_pct / 100.0)))
    tpad = lay.tpad = max(3, int(round(fpx * 0.6)))
    hf = _font(fpx, True)

    if spec.col_heads:
        texts = list(spec.col_labels[:C])
        if spec.row_heads:
            texts.append(spec.corner)
        lay.head_h = int(math.ceil(max(_text_box(t, hf)[1] for t in texts))) \
            + 2 * tpad
    if spec.row_heads:
        texts = list(spec.row_labels[:R])
        if spec.col_heads:
            texts.append(spec.corner)
        lay.head_w = max(int(math.ceil(max(_text_box(t, hf)[0] for t in texts)))
                         + 2 * tpad, 4 * fpx)

    x = lw
    lay.head_x = x
    if spec.row_heads:
        x += lay.head_w + lw
    for w in pic_w:
        lay.col_x.append(x)
        lay.col_w.append(w)
        x += w + 2 * m + lw
    lay.grid_w = x

    if spec.title.strip():
        lay.title_px = max(10, int(round(fpx * 1.4)))
        _, th = _text_box(spec.title.strip(), _font(lay.title_px, True),
                          lay.grid_w - 2 * tpad, _WRAP_CENTRE)
        lay.title_h = int(math.ceil(th)) + 2 * tpad

    lay.grid_y = lay.title_h
    y = lay.grid_y + lw
    lay.head_y = y
    if spec.col_heads:
        y += lay.head_h + lw
    for h in pic_h:
        lay.row_y.append(y)
        lay.row_h.append(h)
        y += h + 2 * m + lw
    lay.grid_h = y - lay.grid_y

    if spec.notes.strip():
        lay.notes_px = max(9, int(round(fpx * 0.9)))
        _, nh = _text_box(spec.notes.strip(), _font(lay.notes_px, False),
                          lay.grid_w - 2 * tpad, _WRAP_LEFT)
        lay.notes_h = int(math.ceil(nh)) + 2 * tpad

    lay.width = lay.grid_w
    lay.height = y + lay.notes_h
    return lay


def _draw_label(p: QPainter, text: str, rect: QRect, lay: TableLayout) -> None:
    """A heading, centred; the text gets smaller rather than being cut off."""
    if not text.strip():
        return
    avail_w = rect.width() - 2 * lay.tpad
    avail_h = rect.height() - 2 * lay.tpad
    px = lay.font_px
    while px > 6:
        w, h = _text_box(text, _font(px, True))
        if w <= avail_w and h <= avail_h:
            break
        px = min(px - 1, int(px * 0.92))
    p.setFont(_font(px, True))
    p.setPen(QPen(QColor(_INK)))
    p.drawText(QRectF(rect), _CENTRE, text)


def render_table(spec: TableSpec, lay: TableLayout, pics: dict,
                 scale: float = 1.0, waiting: dict | None = None) -> QImage:
    """Draw the table.

    pics: path -> (image with its edits burnt in, width, height) where width x
    height is the size the layout was measured with; the image itself may be
    a smaller decode (the preview). At scale 1 a picture whose cell is exactly
    its own size is copied pixel for pixel. waiting: path -> short text shown
    in a cell whose picture is not there (yet).
    """
    W = max(1, int(round(lay.width * scale)))
    H = max(1, int(round(lay.height * scale)))
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    try:
        if scale != 1.0:
            p.scale(scale, scale)
        _paint_table(p, spec, lay, pics, scale == 1.0, waiting)
    finally:
        p.end()
    return img


def _paint_table(p: QPainter, spec: TableSpec, lay: TableLayout, pics: dict,
                 exact: bool, waiting: dict | None = None) -> None:
    """The drawing itself, in the layout's own pixels, onto any painter.
    exact: a picture whose cell is exactly its own size is copied pixel for
    pixel."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    R, C = spec.rows, spec.cols
    gy0, gy1 = lay.grid_y, lay.grid_y + lay.grid_h
    gx1 = lay.grid_w

    head = QColor(_HEAD_BG)
    if spec.col_heads:
        p.fillRect(QRect(0, lay.head_y, gx1, lay.head_h), head)
    if spec.row_heads:
        p.fillRect(QRect(lay.head_x, gy0, lay.head_w, gy1 - gy0), head)

    for r in range(R):
        for c in range(C):
            path = spec.cells[r][c]
            if not path:
                continue
            box = lay.picture_rect(r, c)
            got = pics.get(path)
            if got is None:
                p.fillRect(box, QColor("#f0f0f0"))
                note = (waiting or {}).get(path, "")
                if note:
                    p.setFont(_font(lay.font_px, False))
                    p.setPen(QPen(QColor("#555555")))
                    p.drawText(QRectF(box), _CENTRE, note)
                continue
            im, fw, fh = got
            if fw <= 0 or fh <= 0 or im.isNull():
                continue
            k = min(box.width() / fw, box.height() / fh)
            dw = max(1, int(round(fw * k)))
            dh = max(1, int(round(fh * k)))
            x = box.left() + (box.width() - dw) // 2
            y = box.top() + (box.height() - dh) // 2
            if exact and im.width() == dw and im.height() == dh:
                p.drawImage(QPoint(x, y), im)
            else:
                p.drawImage(QRectF(x, y, dw, dh), im)

    line = QColor(_LINE)
    lw = lay.lw
    xs = [0]
    if spec.row_heads:
        xs.append(lay.head_x + lay.head_w)
    xs += [lay.col_x[c] + lay.col_w[c] + 2 * lay.margin for c in range(C)]
    for x in xs:
        p.fillRect(QRect(x, gy0, lw, gy1 - gy0), line)
    ys = [gy0]
    if spec.col_heads:
        ys.append(lay.head_y + lay.head_h)
    ys += [lay.row_y[r] + lay.row_h[r] + 2 * lay.margin for r in range(R)]
    for y in ys:
        p.fillRect(QRect(0, y, gx1, lw), line)

    if spec.col_heads:
        for c in range(C):
            cr = lay.cell_rect(0, c)
            _draw_label(p, spec.col_labels[c],
                        QRect(cr.left(), lay.head_y, cr.width(), lay.head_h),
                        lay)
        if spec.row_heads:
            _draw_label(p, spec.corner, QRect(lay.head_x, lay.head_y,
                                              lay.head_w, lay.head_h), lay)
    if spec.row_heads:
        for r in range(R):
            cr = lay.cell_rect(r, 0)
            _draw_label(p, spec.row_labels[r],
                        QRect(lay.head_x, cr.top(), lay.head_w, cr.height()),
                        lay)

    p.setPen(QPen(QColor(_INK)))
    if lay.title_h:
        p.setFont(_font(lay.title_px, True))
        p.drawText(QRectF(lay.tpad, lay.tpad, gx1 - 2 * lay.tpad,
                          lay.title_h - 2 * lay.tpad),
                   _WRAP_CENTRE, spec.title.strip())
    if lay.notes_h:
        p.setFont(_font(lay.notes_px, False))
        p.drawText(QRectF(lay.tpad, gy1 + lay.tpad, gx1 - 2 * lay.tpad,
                          lay.notes_h - 2 * lay.tpad),
                   _WRAP_LEFT, spec.notes.strip())


def fit_scale(lay: TableLayout) -> float:
    """1.0, or the factor the picture must be reduced by to be made at all."""
    s = min(1.0, _MAX_SIDE / max(1, lay.width), _MAX_SIDE / max(1, lay.height))
    area = 4.0 * lay.width * lay.height
    if area > _MAX_BYTES:
        s = min(s, math.sqrt(_MAX_BYTES / area))
    return s


def out_scale(spec: TableSpec, lay: TableLayout) -> float:
    """The size the picture is made at: the chosen share of full size, never
    more than a picture can hold."""
    return min(spec.size_pct / 100.0, fit_scale(lay))


def size_text(spec: TableSpec, lay: TableLayout) -> str:
    """Full size, and the saved size when that is smaller."""
    s = out_scale(spec, lay)
    full = f"{lay.width} × {lay.height} px"
    if s >= 1.0:
        return full
    w = max(1, int(round(lay.width * s)))
    h = max(1, int(round(lay.height * s)))
    why = (f"{s * 100:.0f} %, the most one picture can hold"
           if fit_scale(lay) < spec.size_pct / 100.0 else f"{spec.size_pct} %")
    return f"{full} at full size  -  saved at {w} × {h} px ({why})"


def write_pdf(spec: TableSpec, lay: TableLayout, pics: dict,
              target: str) -> bool:
    """The table as a one-page PDF with every picture at its full size.

    The page is as large as the table at a resolution chosen so its longer
    side stays under 200 inches, the most Acrobat will open.
    """
    res = max(150, math.ceil(max(lay.width, lay.height) / 190.0))
    writer = QPdfWriter(target)
    writer.setCreator("Photo Renamer")
    writer.setTitle(spec.title.strip().splitlines()[0] if spec.title.strip()
                    else "Table")
    writer.setResolution(res)
    writer.setPageLayout(QPageLayout(
        QPageSize(QSizeF(lay.width / res, lay.height / res),
                  QPageSize.Unit.Inch, "Table",
                  QPageSize.SizeMatchPolicy.ExactMatch),
        QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
    p = QPainter()
    if not p.begin(writer):
        return False
    try:
        k = min(writer.width() / max(1, lay.width),
                writer.height() / max(1, lay.height))
        if abs(k - 1.0) > 1e-9:
            p.scale(k, k)
        _paint_table(p, spec, lay, pics, abs(k - 1.0) <= 1e-9)
    finally:
        p.end()
    return True


def split_table(spec: TableSpec, sizes: dict) -> list[TableSpec]:
    """The table cut into parts, each small enough to be one picture at full
    size; headings, title and notes go on every part.

    Columns are grouped first, by width alone, then rows are grouped so that
    every column group of a row group fits. A single cell too large on its own
    stays a part of its own (it is then made smaller).
    """
    R, C = spec.rows, spec.cols
    allrows = list(range(R))
    head = spec.title.strip()
    probe = f"{head} - part 99 of 99" if head else "Part 99 of 99"

    def fits(rows: list[int], cols: list[int], width_only: bool) -> bool:
        sub = spec.part(rows, cols)
        sub.title = probe       # measured with the longest title it can get
        lay = measure_table(sub, sizes)
        if width_only:
            return lay.width <= _MAX_SIDE
        return fit_scale(lay) >= 1.0

    col_groups: list[list[int]] = []
    cur: list[int] = []
    for c in range(C):
        if cur and not fits(allrows, cur + [c], True):
            col_groups.append(cur)
            cur = []
        cur.append(c)
    col_groups.append(cur)

    row_groups: list[list[int]] = []
    cur = []
    for r in range(R):
        if cur and not all(fits(cur + [r], cols, False) for cols in col_groups):
            row_groups.append(cur)
            cur = []
        cur.append(r)
    row_groups.append(cur)

    parts = [spec.part(rows, cols) for rows in row_groups for cols in col_groups]
    n = len(parts)
    if n > 1:
        for i, part in enumerate(parts, 1):
            part.title = (f"{head} - part {i} of {n}" if head
                          else f"Part {i} of {n}")
    return parts


# ─────────────────────────────────────────────────────────────────────────────
#  Widgets
# ─────────────────────────────────────────────────────────────────────────────
_FIELD_QSS = (
    "QLineEdit, QPlainTextEdit, QSpinBox { background: #ffffff; color: #111;"
    " border: 1px solid #b6b6b6; border-radius: 3px; padding: 2px 4px;"
    " selection-background-color: #2f6fb5; selection-color: #ffffff; }"
    "QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus"
    " { border: 1px solid #2f6fb5; }"
)
_CAPTION_QSS = ("QLabel { color: #16202c; font-size: 12px;"
                " background: transparent; }")
_EDITOR_TABLE_QSS = (
    "QTableWidget { background: #ffffff; color: #111; gridline-color: #9aa1a9;"
    " border: 1px solid #c8ccd2; }"
)


class _Count(QSpinBox):
    """A number box without the tiny stock arrows (the − and + buttons beside
    it do that job). The wheel only works on it once it has been clicked and
    while the mouse is over it."""

    def __init__(self, lo: int, hi: int, value: int, suffix: str = ""):
        super().__init__()
        self.setRange(lo, hi)
        self.setValue(value)
        self.setSuffix(suffix)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(_FIELD_QSS)
        fm = QFontMetrics(self.font())
        self.setFixedWidth(fm.horizontalAdvance(f"{hi}{suffix}") + 22)

    def wheelEvent(self, e) -> None:               # noqa: N802
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


def _stepper(box: _Count) -> QWidget:
    """[−] number [+]"""
    w = QWidget()
    w.setStyleSheet("QWidget { background: transparent; }")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    minus = _tool_btn(action_icon("minus"), "One less")
    plus = _tool_btn(action_icon("plus"), "One more")
    minus.clicked.connect(lambda: box.setValue(box.value() - box.singleStep()))
    plus.clicked.connect(lambda: box.setValue(box.value() + box.singleStep()))
    lay.addWidget(minus)
    lay.addWidget(box)
    lay.addWidget(plus)
    w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return w


def _caption(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_CAPTION_QSS)
    return lbl


class _ImageView(QWidget):
    """The finished table, scaled down to fit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img: QImage | None = None
        self._cache: QPixmap | None = None
        self._cache_for: tuple = ()
        self._message = "Put pictures into the table above"
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Expanding)

    def set_image(self, img: QImage | None, message: str = "") -> None:
        self._img = img
        self._cache = None
        if message:
            self._message = message
        self.update()

    def paintEvent(self, e) -> None:               # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#e6e8eb"))
        if self._img is None or self._img.isNull():
            p.setPen(QPen(QColor("#3d4650")))
            p.drawText(self.rect(), _CENTRE, self._message)
            p.end()
            return
        room = self.rect().adjusted(10, 10, -10, -10)
        k = min(room.width() / self._img.width(),
                room.height() / self._img.height(), 1.0)
        w = max(1, int(self._img.width() * k))
        h = max(1, int(self._img.height() * k))
        dpr = self.devicePixelRatioF()
        want = (int(w * dpr), int(h * dpr))
        if self._cache is None or self._cache_for != want:
            self._cache = QPixmap.fromImage(self._img.scaled(
                max(1, want[0]), max(1, want[1]),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self._cache.setDevicePixelRatio(dpr)
            self._cache_for = want
        x = room.left() + (room.width() - w) // 2
        y = room.top() + (room.height() - h) // 2
        p.drawPixmap(x, y, self._cache)
        p.setPen(QPen(QColor("#9aa1a9"), 1))
        p.drawRect(QRect(x - 1, y - 1, w + 1, h + 1))
        p.end()


class _PictureList(QListWidget):
    """The open folder's pictures; drag them into the table."""

    def __init__(self, tab: "TableTab"):
        super().__init__()
        self._tab = tab
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(72, 54))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setUniformItemSizes(True)
        self.setStyleSheet(_LIST_QSS
                           + "QListWidget { color: #111; font-size: 12px; }"
                           "QListWidget::item { padding: 2px; }"
                           "QListWidget::item:selected { background: #cfe0f7;"
                           " color: #10243c; }"
                           "QListWidget::item:hover:!selected"
                           " { background: #eef4fc; }")
        self.setToolTip("Drag pictures into the table.\n"
                        "Double-click puts a picture into the next empty cell.")
        self.itemDoubleClicked.connect(
            lambda it: self._tab.add_paths([it.data(Qt.ItemDataRole.UserRole)]))

    def selected_paths(self) -> list[str]:
        rows = sorted(self.row(it) for it in self.selectedItems())
        return [self.item(r).data(Qt.ItemDataRole.UserRole) for r in rows]

    def mimeTypes(self):                           # noqa: N802
        return ["text/uri-list"]

    def mimeData(self, items):                     # noqa: N802
        md = QMimeData()
        rows = sorted(self.row(it) for it in items)
        md.setUrls([QUrl.fromLocalFile(self.item(r).data(Qt.ItemDataRole.UserRole))
                    for r in rows])
        return md


_CELL_MIME = "application/x-photo-renamer-table-cell"


class _CellDelegate(QStyledItemDelegate):
    """Paints the editor's cells; headings are typed in a box that takes Enter
    as a new line."""

    def __init__(self, tab: "TableTab"):
        super().__init__(tab)
        self._tab = tab

    def paint(self, p: QPainter, option, index) -> None:
        r, c = index.row(), index.column()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        p.save()
        spec = self._tab.spec
        if r == 0 or c == 0:
            shown = self._tab.heading_shown(r, c)
            p.fillRect(rect, QColor("#dcdcdc" if shown else "#f0f0f0"))
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            f = QFont(option.font)
            if text:
                f.setBold(True)
                p.setPen(QPen(QColor("#111111" if shown else "#8a8a8a")))
            else:
                f.setItalic(True)
                p.setPen(QPen(QColor("#6b7480")))
                text = "not shown" if not shown else "double-click to type"
            f.setPointSizeF(9.0)
            p.setFont(f)
            p.drawText(rect.adjusted(4, 2, -4, -2), _WRAP_CENTRE, text)
        else:
            p.fillRect(rect, QColor("#ffffff"))
            path = spec.cells[r - 1][c - 1]
            f = QFont(option.font)
            f.setPointSizeF(8.5)
            p.setFont(f)
            if path:
                name_h = 16
                room = rect.adjusted(4, 4, -4, -4 - name_h)
                pm = self._tab.cell_pixmap(path)
                if pm is not None and not pm.isNull():
                    s = pm.scaled(room.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(room.left() + (room.width() - s.width()) // 2,
                                 room.top() + (room.height() - s.height()) // 2, s)
                else:
                    p.fillRect(room, QColor("#f0f0f0"))
                    p.setPen(QPen(QColor("#555555")))
                    p.drawText(room, _CENTRE, self._tab.waiting_text(path))
                p.setPen(QPen(QColor("#111111")))
                fm = QFontMetrics(f)
                name_r = QRect(rect.left() + 3, rect.bottom() - name_h - 2,
                               rect.width() - 6, name_h)
                p.drawText(name_r, _CENTRE,
                           fm.elidedText(Path(path).name,
                                         Qt.TextElideMode.ElideMiddle,
                                         name_r.width()))
            else:
                p.setPen(QPen(QColor("#6b7480")))
                f.setItalic(True)
                p.setFont(f)
                p.drawText(rect.adjusted(6, 4, -6, -4), _WRAP_CENTRE,
                           "drop a picture here")
        if selected:
            p.setPen(QPen(QColor("#2f6fb5"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(1, 1, -1, -1))
        p.restore()

    def createEditor(self, parent, option, index):  # noqa: N802
        if index.row() != 0 and index.column() != 0:
            return None
        ed = QPlainTextEdit(parent)
        ed.setStyleSheet("QPlainTextEdit { background: #ffffff; color: #111;"
                         " border: 2px solid #2f6fb5; font-weight: 700; }")
        ed.setToolTip("Enter starts a new line; click elsewhere to finish,"
                      " Esc to cancel")
        return ed

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        editor.setPlainText(index.data(Qt.ItemDataRole.DisplayRole) or "")
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        self._tab.set_heading(index.row(), index.column(), editor.toPlainText())

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: N802
        editor.setGeometry(option.rect)


class _CellTable(QTableWidget):
    """The table being built: row 0 and column 0 are the headings, the rest
    are picture cells. Pictures are dropped in, dragged between cells (which
    swaps them), cleared with Delete."""

    def __init__(self, tab: "TableTab"):
        super().__init__()
        self._tab = tab
        self.horizontalHeader().setVisible(False)
        self.verticalHeader().setVisible(False)
        self.setStyleSheet(_EDITOR_TABLE_QSS)
        self.setItemDelegate(_CellDelegate(tab))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDropIndicatorShown(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.cellDoubleClicked.connect(self._double_clicked)

    # ---- drag and drop ------------------------------------------------------
    def mimeTypes(self):                           # noqa: N802
        return [_CELL_MIME, "text/uri-list"]

    def mimeData(self, items):                     # noqa: N802
        md = QMimeData()
        for it in items:
            r, c = it.row(), it.column()
            if r > 0 and c > 0 and self._tab.spec.cells[r - 1][c - 1]:
                md.setData(_CELL_MIME, f"{r - 1},{c - 1}".encode("ascii"))
                break
        return md

    def _accepts(self, e) -> bool:
        md = e.mimeData()
        return md.hasFormat(_CELL_MIME) or md.hasUrls()

    def dragEnterEvent(self, e) -> None:           # noqa: N802
        if self._accepts(e):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:            # noqa: N802
        idx = self.indexAt(e.position().toPoint())
        if self._accepts(e) and idx.isValid() and idx.row() > 0 and idx.column() > 0:
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e) -> None:                # noqa: N802
        idx = self.indexAt(e.position().toPoint())
        if not idx.isValid() or idx.row() == 0 or idx.column() == 0:
            e.ignore()
            return
        r, c = idx.row() - 1, idx.column() - 1
        md = e.mimeData()
        if md.hasFormat(_CELL_MIME):
            try:
                sr, sc = (int(v) for v in
                          bytes(md.data(_CELL_MIME)).decode("ascii").split(","))
            except ValueError:
                e.ignore()
                return
            self._tab.swap_cells((sr, sc), (r, c))
        else:
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            paths = [p for p in paths if Path(p).suffix.lower() in PICTURE_SUFFIXES]
            if not paths:
                e.ignore()
                return
            self._tab.place_at(paths, r, c)
        # Copy, never Move: a Move would make the view clear the source cell
        # itself, behind the table's back.
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()

    # ---- keys, clicks, menu -------------------------------------------------
    def keyPressEvent(self, e) -> None:            # noqa: N802
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._tab.clear_cells([(i.row(), i.column())
                                   for i in self.selectedIndexes()])
            e.accept()
            return
        super().keyPressEvent(e)

    def _double_clicked(self, r: int, c: int) -> None:
        if r > 0 and c > 0:
            self._tab.choose_file(r - 1, c - 1)

    def contextMenuEvent(self, e) -> None:         # noqa: N802
        idx = self.indexAt(e.pos())
        if not idx.isValid():
            return
        r, c = idx.row(), idx.column()
        spec = self._tab.spec
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        acts = {}
        if r > 0 and c > 0:
            acts[menu.addAction("Choose picture")] = (
                lambda: self._tab.choose_file(r - 1, c - 1))
            a = menu.addAction("Clear")
            a.setEnabled(bool(spec.cells[r - 1][c - 1]))
            acts[a] = lambda: self._tab.clear_cells([(r, c)])
            menu.addSeparator()
        elif r == 0 and c == 0:
            acts[menu.addAction("Clear the text")] = (
                lambda: self._tab.clear_cells([(0, 0)]))
        else:
            acts[menu.addAction("Clear the text")] = (
                lambda: self._tab.clear_cells([(r, c)]))
            menu.addSeparator()
        if r > 0:
            acts[menu.addAction("Insert row above")] = (
                lambda: self._tab.reshape("insert_row", r - 1))
            acts[menu.addAction("Insert row below")] = (
                lambda: self._tab.reshape("insert_row", r))
        if c > 0:
            acts[menu.addAction("Insert column left")] = (
                lambda: self._tab.reshape("insert_col", c - 1))
            acts[menu.addAction("Insert column right")] = (
                lambda: self._tab.reshape("insert_col", c))
        if r > 0 or c > 0:
            menu.addSeparator()
        if r > 0:
            a = menu.addAction("Delete this row")
            a.setEnabled(spec.rows > 1)
            acts[a] = lambda: self._tab.reshape("delete_row", r - 1)
        if c > 0:
            a = menu.addAction("Delete this column")
            a.setEnabled(spec.cols > 1)
            acts[a] = lambda: self._tab.reshape("delete_col", c - 1)
        chosen = menu.exec(e.globalPos())
        fn = acts.get(chosen)
        if fn is not None:
            fn()


# ─────────────────────────────────────────────────────────────────────────────
#  The tab
# ─────────────────────────────────────────────────────────────────────────────
class TableTab(QWidget):
    """owner is the PhotoRenamerWidget: it supplies the folder's pictures,
    their thumbnails and their pending edits."""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.spec = TableSpec.from_dict(pr_t.load_ui_state().get("table"))
        # path -> (decoded image, full width, full height); None = unreadable
        self._decoded: dict[str, tuple | None] = {}
        self._loading: set[str] = set()
        self._thumbs: dict[str, tuple] = {}     # path -> (edit rev, QPixmap)
        self._dirty = True
        self._list_dirty = True

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._sink = _LoadSink(self)
        self._sink.done.connect(self._on_loaded)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(180)
        self._render_timer.timeout.connect(self._refresh_preview)
        self._store_timer = QTimer(self)
        self._store_timer.setSingleShot(True)
        self._store_timer.setInterval(800)
        self._store_timer.timeout.connect(self._store)
        self._icon_timer = QTimer(self)
        self._icon_timer.setSingleShot(True)
        self._icon_timer.setInterval(200)
        self._icon_timer.timeout.connect(self._refresh_list_icons)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(5000)
        self._note_timer.timeout.connect(lambda: self._note.setText(""))

        self._build()
        self._sync_controls()
        self._rebuild_editor()

    # ---- building -----------------------------------------------------------
    def _build(self) -> None:
        self.setStyleSheet("TableTab { background: #f3f3f3; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ---- first row: shape, filling, output ----
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(_caption("Rows"))
        self._rows = _Count(1, MAX_ROWS, self.spec.rows)
        self._rows.valueChanged.connect(self._on_size_changed)
        row1.addWidget(_stepper(self._rows))
        row1.addSpacing(6)
        row1.addWidget(_caption("Columns"))
        self._cols = _Count(1, MAX_COLS, self.spec.cols)
        self._cols.valueChanged.connect(self._on_size_changed)
        row1.addWidget(_stepper(self._cols))
        row1.addSpacing(6)
        b = _btn("Swap rows and columns",
                 "Turn the table: rows become columns, headings and pictures"
                 " go with them", "swap")
        b.clicked.connect(self.transpose)
        row1.addWidget(b)
        row1.addSpacing(10)
        b = _btn("Fill from selection",
                 "Empty the table and put the pictures selected in the list on"
                 " the left\n(or, if none, on the Photos tab) into it, starting"
                 " at the top left", "picture")
        b.clicked.connect(self.fill_from_selection)
        row1.addWidget(b)
        self._chk_down = QCheckBox("Down the columns")
        self._chk_down.setStyleSheet(_check_qss())
        self._chk_down.setToolTip("Off: fill along each row, left to right.\n"
                                  "On: fill down each column, top to bottom.")
        self._chk_down.setChecked(bool(pr_t.load_ui_state().get("table_fill_down",
                                                           False)))
        self._chk_down.toggled.connect(
            lambda on: pr_t._remember("table_fill_down", bool(on)))
        row1.addWidget(self._chk_down)
        b = _btn("Clear pictures", "Take every picture out of the table;"
                 " the headings stay", "reset")
        b.clicked.connect(self._clear_pictures)
        row1.addWidget(b)
        row1.addStretch(1)
        b = _btn("Load template", "Take headings, title, notes and size from a"
                 " saved table", "folder")
        b.clicked.connect(self._load_template)
        row1.addWidget(b)
        b = _btn("Save template", "Keep this table's layout in a file, to"
                 " fill with other pictures later", "save")
        b.clicked.connect(self._save_template)
        row1.addWidget(b)
        outer.addLayout(row1)

        # ---- second row: texts and looks ----
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(_caption("Title"))
        self._title = QLineEdit()
        self._title.setStyleSheet(_FIELD_QSS)
        self._title.setPlaceholderText("none")
        self._title.setToolTip("Written above the table. Empty: no title.")
        self._title.textChanged.connect(self._on_title)
        row2.addWidget(self._title, 1)
        row2.addSpacing(6)
        self._chk_colh = QCheckBox("Column headings")
        self._chk_rowh = QCheckBox("Row headings")
        for chk in (self._chk_colh, self._chk_rowh):
            chk.setStyleSheet(_check_qss())
            chk.toggled.connect(self._on_heads)
            row2.addWidget(chk)
        row2.addSpacing(6)
        row2.addWidget(_caption("Text size"))
        self._text = _Count(30, 400, self.spec.text_pct, " %")
        self._text.setSingleStep(10)
        self._text.setToolTip("100 % is chosen from the size of the pictures")
        self._text.valueChanged.connect(self._on_looks)
        row2.addWidget(_stepper(self._text))
        row2.addWidget(_caption("Margin"))
        self._margin = _Count(0, 20, self.spec.margin_pct, " %")
        self._margin.setToolTip("White edge around each picture, as a share of"
                                " the cell.\n0 %: the pictures touch the lines.")
        self._margin.valueChanged.connect(self._on_looks)
        row2.addWidget(_stepper(self._margin))
        row2.addWidget(_caption("Size"))
        self._out = _Count(1, 100, self.spec.size_pct, " %")
        self._out.setSingleStep(10)
        self._out.setToolTip("Size of the saved and copied picture, as a share"
                             " of full size.\n100 %: every photo pixel for"
                             " pixel.\nNever larger than one picture can hold"
                             " (about 350 million pixels).")
        self._out.valueChanged.connect(self._on_looks)
        row2.addWidget(_stepper(self._out))
        row2.addSpacing(10)
        b = _btn("Copy", "Copy the finished picture to the clipboard,"
                 " to paste into PowerPoint or an e-mail", "copy")
        b.clicked.connect(self.copy_to_clipboard)
        row2.addWidget(b)
        b = _btn("Save in parts", "Save the table cut into several pictures,"
                 " each at full size,\nwith the headings on every part",
                 "save")
        b.clicked.connect(self.save_parts)
        row2.addWidget(b)
        b = _btn("Save image", "Save the finished table as one picture,\nor as"
                 " a PDF with every photo at full size", "save", "save")
        b.clicked.connect(self.save_image)
        row2.addWidget(b)
        outer.addLayout(row2)

        # ---- body: pictures | (table, notes) / preview ----
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet("QSplitter::handle { background: #d5d8dc; }")
        split.setChildrenCollapsible(False)

        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 0, 0)
        llay.setSpacing(4)
        llay.addWidget(_caption("Pictures in the folder"))
        self._list = _PictureList(self)
        llay.addWidget(self._list, 1)
        left.setMinimumWidth(180)
        split.addWidget(left)

        right = QSplitter(Qt.Orientation.Vertical)
        right.setStyleSheet("QSplitter::handle { background: #d5d8dc; }")
        right.setChildrenCollapsible(False)
        top = QWidget()
        tlay = QVBoxLayout(top)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.setSpacing(4)
        self._editor = _CellTable(self)
        tlay.addWidget(self._editor, 1)
        nrow = QHBoxLayout()
        nrow.addWidget(_caption("Notes under the table"))
        nrow.addStretch(1)
        self._note = QLabel("")
        self._note.setStyleSheet("QLabel { color: #8d4a00; font-size: 12px;"
                                 " font-weight: 600; background: transparent; }")
        nrow.addWidget(self._note)
        tlay.addLayout(nrow)
        self._notes = QPlainTextEdit()
        self._notes.setStyleSheet(_FIELD_QSS)
        self._notes.setPlaceholderText("none")
        self._notes.setFixedHeight(64)
        self._notes.textChanged.connect(self._on_notes)
        tlay.addWidget(self._notes)
        right.addWidget(top)

        bottom = QWidget()
        block = QVBoxLayout(bottom)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(4)
        brow = QHBoxLayout()
        brow.addWidget(_caption("Preview of the saved picture"))
        brow.addStretch(1)
        self._size = QLabel("")
        self._size.setStyleSheet("QLabel { color: #16202c; font-size: 12px;"
                                 " font-weight: 700; background: transparent; }")
        brow.addWidget(self._size)
        block.addLayout(brow)
        self._view = _ImageView()
        block.addWidget(self._view, 1)
        right.addWidget(bottom)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 1000])
        outer.addWidget(split, 1)

    def _sync_controls(self) -> None:
        """Put the spec's values into the controls without echoing back."""
        s = self.spec
        for w, v in ((self._rows, s.rows), (self._cols, s.cols),
                     (self._text, s.text_pct), (self._margin, s.margin_pct),
                     (self._out, s.size_pct)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        for w, v in ((self._chk_colh, s.col_heads), (self._chk_rowh, s.row_heads)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self._title.blockSignals(True)
        self._title.setText(s.title)
        self._title.blockSignals(False)
        self._notes.blockSignals(True)
        self._notes.setPlainText(s.notes)
        self._notes.blockSignals(False)

    # ---- the editor grid ----------------------------------------------------
    def heading_shown(self, r: int, c: int) -> bool:
        s = self.spec
        if r == 0 and c == 0:
            return s.col_heads and s.row_heads
        return s.col_heads if r == 0 else s.row_heads

    def _rebuild_editor(self) -> None:
        s = self.spec
        t = self._editor
        t.setRowCount(s.rows + 1)
        t.setColumnCount(s.cols + 1)
        heading_flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                         | Qt.ItemFlag.ItemIsEditable)
        cell_flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                      | Qt.ItemFlag.ItemIsDragEnabled
                      | Qt.ItemFlag.ItemIsDropEnabled)
        for r in range(s.rows + 1):
            for c in range(s.cols + 1):
                if r == 0 and c == 0:
                    it = QTableWidgetItem(s.corner)
                    it.setFlags(heading_flags)
                    it.setToolTip("Corner label. Double-click to type.")
                elif r == 0:
                    it = QTableWidgetItem(s.col_labels[c - 1])
                    it.setFlags(heading_flags)
                    it.setToolTip("Column heading. Double-click to type;"
                                  " Enter starts a new line.")
                elif c == 0:
                    it = QTableWidgetItem(s.row_labels[r - 1])
                    it.setFlags(heading_flags)
                    it.setToolTip("Row heading. Double-click to type;"
                                  " Enter starts a new line.")
                else:
                    path = s.cells[r - 1][c - 1]
                    it = QTableWidgetItem(Path(path).name if path else "")
                    it.setFlags(cell_flags)
                    it.setToolTip(path or "Drop a picture here, or double-click"
                                          " to choose a file")
                t.setItem(r, c, it)
        t.setColumnWidth(0, 150)
        for c in range(1, s.cols + 1):
            t.setColumnWidth(c, 150)
        self._fit_heading_row()
        for r in range(1, s.rows + 1):
            t.setRowHeight(r, 120)
        self._changed()

    def _fit_heading_row(self) -> None:
        """Tall enough for the longest heading: a heading is never cut off."""
        f = QFont(self._editor.font())
        f.setPointSizeF(9.0)
        f.setBold(True)
        fm = QFontMetrics(f)
        texts = [self.spec.corner] + list(self.spec.col_labels[:self.spec.cols])
        lines = max(1, max(t.count("\n") + 1 for t in texts))
        self._editor.setRowHeight(0, max(44, lines * fm.lineSpacing() + 14))

    def _refresh_cell_items(self) -> None:
        s = self.spec
        for r in range(s.rows):
            for c in range(s.cols):
                it = self._editor.item(r + 1, c + 1)
                if it is not None:
                    path = s.cells[r][c]
                    it.setText(Path(path).name if path else "")
                    it.setToolTip(path or "Drop a picture here, or double-click"
                                          " to choose a file")
        self._editor.viewport().update()

    def cell_pixmap(self, path: str) -> QPixmap | None:
        """A small picture for the editor, with the pending edits on it."""
        got = self._decoded.get(path)
        if got is None:
            self._request(path)
            return None
        edits, rev = self._owner.edits_for_path(path)
        hit = self._thumbs.get(path)
        if hit is not None and hit[0] == rev:
            return hit[1]
        img, w, h = got
        img = render_edits(img, w, h, edits)
        pm = QPixmap.fromImage(img.scaled(_THUMB_PX, _THUMB_PX,
                                          Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        self._thumbs[path] = (rev, pm)
        return pm

    def waiting_text(self, path: str) -> str:
        if path in self._decoded and self._decoded[path] is None:
            return "cannot be read"
        return "loading"

    # ---- changes from the controls ------------------------------------------
    def _changed(self) -> None:
        self._dirty = True
        self._render_timer.start()
        self._store_timer.start()

    def _on_size_changed(self, _v=None) -> None:
        self.spec.resize(self._rows.value(), self._cols.value())
        self._rebuild_editor()

    def _on_title(self, text: str) -> None:
        self.spec.title = text
        self._changed()

    def _on_notes(self) -> None:
        self.spec.notes = self._notes.toPlainText()
        self._changed()

    def _on_heads(self, _on=None) -> None:
        self.spec.col_heads = self._chk_colh.isChecked()
        self.spec.row_heads = self._chk_rowh.isChecked()
        self._editor.viewport().update()
        self._changed()

    def _on_looks(self, _v=None) -> None:
        self.spec.text_pct = self._text.value()
        self.spec.margin_pct = self._margin.value()
        self.spec.size_pct = self._out.value()
        self._changed()

    def set_heading(self, r: int, c: int, text: str) -> None:
        s = self.spec
        if r == 0 and c == 0:
            s.corner = text
        elif r == 0:
            s.col_labels[c - 1] = text
        elif c == 0:
            s.row_labels[r - 1] = text
        else:
            return
        it = self._editor.item(r, c)
        if it is not None:
            it.setText(text)
        if r == 0:
            self._fit_heading_row()
        self._changed()

    # ---- pictures in and out ------------------------------------------------
    def _say(self, text: str) -> None:
        self._note.setText(text)
        self._note_timer.start()

    def _after_shape_change(self) -> None:
        self._sync_controls()
        self._rebuild_editor()

    def place_at(self, paths: list[str], r: int, c: int) -> None:
        """Dropped pictures: the first goes where it was dropped, any more into
        the cells after it, in the fill direction."""
        before = (self.spec.rows, self.spec.cols)
        self.spec.place(paths, by_columns=self._chk_down.isChecked(),
                        start=(r, c))
        if (self.spec.rows, self.spec.cols) != before:
            self._after_shape_change()
        else:
            self._refresh_cell_items()
            self._changed()

    def add_paths(self, paths: list[str]) -> int:
        """Into the next empty cells; the table grows when it is full."""
        paths = [p for p in paths if p]
        if not paths:
            return 0
        before = (self.spec.rows, self.spec.cols)
        n = self.spec.place(paths, by_columns=self._chk_down.isChecked(),
                            only_empty=True)
        if (self.spec.rows, self.spec.cols) != before:
            self._after_shape_change()
        else:
            self._refresh_cell_items()
            self._changed()
        if n:
            self._say(f"{n} picture{'s' if n != 1 else ''} added to the table")
        return n

    def fill_from_selection(self) -> None:
        paths = self._list.selected_paths() or self._owner.selected_paths()
        if not paths:
            self._say("Select pictures in the list on the left first")
            return
        before = (self.spec.rows, self.spec.cols)
        self.spec.clear_pictures()
        self.spec.place(paths, by_columns=self._chk_down.isChecked())
        if (self.spec.rows, self.spec.cols) != before:
            self._after_shape_change()
        else:
            self._refresh_cell_items()
            self._changed()

    def swap_cells(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        s = self.spec
        (ar, ac), (br, bc) = a, b
        if not (0 <= ar < s.rows and 0 <= ac < s.cols
                and 0 <= br < s.rows and 0 <= bc < s.cols) or a == b:
            return
        s.cells[ar][ac], s.cells[br][bc] = s.cells[br][bc], s.cells[ar][ac]
        self._refresh_cell_items()
        self._changed()

    def clear_cells(self, cells: list[tuple[int, int]]) -> None:
        """Editor coordinates: row 0 / column 0 are the headings."""
        for r, c in cells:
            if r == 0 or c == 0:
                self.set_heading(r, c, "")
            elif r - 1 < self.spec.rows and c - 1 < self.spec.cols:
                self.spec.cells[r - 1][c - 1] = ""
        self._refresh_cell_items()
        self._changed()

    def choose_file(self, r: int, c: int) -> None:
        cur = self.spec.cells[r][c]
        root = self._owner.root_folder()
        start = (str(Path(cur).parent) if cur
                 else str(root) if root else str(Path.home()))
        pattern = " ".join(f"*{s}" for s in sorted(PICTURE_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", start,
                                              f"Pictures ({pattern})")
        if path:
            self.spec.cells[r][c] = str(Path(path))
            self._refresh_cell_items()
            self._changed()

    def _clear_pictures(self) -> None:
        if not self.spec.has_pictures():
            return
        if not self._ask("Clear pictures",
                         "Take every picture out of the table?\n"
                         "The headings, the title and the notes stay."):
            return
        self.spec.clear_pictures()
        self._refresh_cell_items()
        self._changed()

    def transpose(self) -> None:
        self.spec.transpose()
        self._after_shape_change()

    def reshape(self, how: str, i: int) -> None:
        getattr(self.spec, how)(i)
        self._after_shape_change()

    # ---- picture loading ----------------------------------------------------
    def _request(self, path: str) -> None:
        if path in self._decoded or path in self._loading:
            return
        self._loading.add(path)
        self._pool.start(_LoadTask(Path(path), _PREVIEW_DECODE, self._sink))

    def _on_loaded(self, key: str, _side: int, img: QImage, w: int, h: int) -> None:
        # The loader hands back str(Path(path)); the table keeps whatever
        # spelling it was given, so match both.
        for path in list(self._loading):
            if path == key or str(Path(path)) == key:
                self._loading.discard(path)
                self._decoded[path] = ((img, w, h) if not img.isNull()
                                       and w > 0 and h > 0 else None)
                self._thumbs.pop(path, None)
        self._editor.viewport().update()
        self._changed()

    def _sizes_and_pictures(self) -> tuple[dict, dict, dict]:
        sizes, pics, waiting = {}, {}, {}
        for path in self.spec.paths():
            got = self._decoded.get(path)
            if got is None:
                if path in self._decoded:
                    waiting[path] = "cannot be read"
                else:
                    waiting[path] = "loading"
                    self._request(path)
                continue
            img, w, h = got
            edits, _rev = self._owner.edits_for_path(path)
            fw, fh = frame_after(w, h, edits)
            sizes[path] = (fw, fh)
            pics[path] = (render_edits(img, w, h, edits), fw, fh)
        return sizes, pics, waiting

    # ---- the preview --------------------------------------------------------
    def _refresh_preview(self) -> None:
        if not self.isVisible():
            self._dirty = True
            return
        self._dirty = False
        sizes, pics, waiting = self._sizes_and_pictures()
        lay = measure_table(self.spec, sizes)
        scale = min(1.0, _PREVIEW_MAX / max(lay.width, lay.height))
        self._view.set_image(render_table(self.spec, lay, pics, scale, waiting))
        if waiting and any(v == "loading" for v in waiting.values()):
            self._size.setText("measuring")
        else:
            self._size.setText(size_text(self.spec, lay))

    def showEvent(self, e) -> None:                # noqa: N802
        super().showEvent(e)
        if self._list_dirty:
            self._refresh_list()
        if self._dirty:
            self._render_timer.start(0)

    # ---- the folder list ----------------------------------------------------
    def _refresh_list(self) -> None:
        self._list_dirty = False
        keep = set(self._list.selected_paths())
        self._list.clear()
        for item in self._owner.table_items():
            li = QListWidgetItem(item.new_name)
            path = str(item.path)
            li.setData(Qt.ItemDataRole.UserRole, path)
            li.setToolTip(path)
            pm = self._owner.thumb_for(item, 128)
            if pm is not None and not pm.isNull():
                li.setIcon(pm)
            self._list.addItem(li)
            if path in keep:
                li.setSelected(True)

    def _refresh_list_icons(self) -> None:
        items = {str(it.path): it for it in self._owner.table_items()}
        for row in range(self._list.count()):
            li = self._list.item(row)
            it = items.get(li.data(Qt.ItemDataRole.UserRole))
            if it is None:
                continue
            pm = self._owner.thumb_for(it, 128)
            if pm is not None and not pm.isNull():
                li.setIcon(pm)

    # ---- told by the Photos tab ---------------------------------------------
    def owner_changed(self) -> None:
        """Names, edits or the folder changed on the Photos tab."""
        self._list_dirty = True
        self._thumbs.clear()
        if self.isVisible():
            self._refresh_list()
            self._editor.viewport().update()
        self._changed()

    def owner_thumb_arrived(self) -> None:
        if self.isVisible():
            self._icon_timer.start()

    def after_save(self, moved: dict[str, str]) -> None:
        """Save on the Photos tab renamed files and rewrote edited ones."""
        s = self.spec
        for row in s.cells:
            for c, path in enumerate(row):
                if path in moved:
                    row[c] = moved[path]
        self._decoded.clear()
        self._loading.clear()
        self._thumbs.clear()
        self._refresh_cell_items()
        self.owner_changed()

    # ---- the finished picture -----------------------------------------------
    def _load_full(self) -> tuple[dict, dict] | None:
        """Every picture of the table at full resolution, edits included:
        (sizes, pics). None, with the reason shown, when a picture cannot be
        read or the table is empty. Called with the wait cursor on."""
        paths = self.spec.paths()
        if not paths:
            self._say("The table has no pictures yet")
            return None
        sizes, pics, bad = {}, {}, []
        for path in paths:
            img, w, h = load_scaled(Path(path), 0)
            if img.isNull() or w <= 0 or h <= 0:
                bad.append(path)
                continue
            edits, _rev = self._owner.edits_for_path(path)
            fw, fh = frame_after(w, h, edits)
            sizes[path] = (fw, fh)
            pics[path] = (render_edits(img, w, h, edits), fw, fh)
        if bad:
            QApplication.restoreOverrideCursor()
            self._warn("Pictures could not be read",
                       "These pictures could not be read, so the table was"
                       " not made:\n\n" + "\n".join(bad[:12])
                       + (f"\nand {len(bad) - 12} more" if len(bad) > 12
                          else ""))
            return None
        return sizes, pics

    def build_full_image(self) -> QImage | None:
        """The table at the chosen size (Size %), never more than one picture
        can hold. None, with the reason shown, when a picture cannot be read
        or the table is empty."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            got = self._load_full()
            if got is None:
                return None
            sizes, pics = got
            lay = measure_table(self.spec, sizes)
            return render_table(self.spec, lay, pics, out_scale(self.spec, lay))
        finally:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

    def copy_to_clipboard(self) -> None:
        img = self.build_full_image()
        if img is None:
            return
        QGuiApplication.clipboard().setImage(img)
        self._say(f"Copied, {img.width()} × {img.height()} px")

    def _image_dir(self) -> str:
        folder = pr_t.load_ui_state().get("table_image_dir")
        if isinstance(folder, str) and Path(folder).is_dir():
            return folder
        root = self._owner.root_folder()
        return str(root) if root else str(Path.home())

    def _write(self, target: Path, img: QImage) -> bool:
        data = encode_image(img, target.suffix)
        if data is None:
            self._warn("Not saved", f"{target.suffix} pictures cannot be written.")
            return False
        try:
            target.write_bytes(data)
        except OSError as exc:
            self._warn("Not saved", f"{target}\n\n{exc.strerror or exc}")
            return False
        return True

    def save_image(self) -> None:
        stem = _safe_stem(self.spec.title) or "table"
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save the table as a picture",
            str(Path(self._image_dir()) / f"{stem}.png"),
            "PNG picture, no loss (*.png);;JPEG picture (*.jpg);;"
            "PDF, every photo at full size (*.pdf)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".pdf"):
            low = chosen.lower()
            target = target.with_suffix(".jpg" if "jpg" in low
                                        else ".pdf" if "pdf" in low else ".png")
        if target.suffix.lower() == ".pdf":
            self._save_pdf(target)
            return
        img = self.build_full_image()
        if img is None or not self._write(target, img):
            return
        pr_t._remember("table_image_dir", str(target.parent))
        self._say(f"Saved {target.name}, {img.width()} × {img.height()} px")

    def _save_pdf(self, target: Path) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            got = self._load_full()
            if got is None:
                return
            sizes, pics = got
            lay = measure_table(self.spec, sizes)
            ok = write_pdf(self.spec, lay, pics, str(target))
        finally:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        if not ok or not target.is_file():
            self._warn("Not saved", f"{target}\n\nThe PDF could not be written.")
            return
        pr_t._remember("table_image_dir", str(target.parent))
        self._say(f"Saved {target.name}, {lay.width} × {lay.height} px")

    def save_parts(self) -> None:
        """The table cut into as few pictures as needed for each to be at full
        size: name_1.png, name_2.png, ..."""
        stem = _safe_stem(self.spec.title) or "table"
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save the table in parts - name of the first part",
            str(Path(self._image_dir()) / f"{stem}.png"),
            "PNG pictures, no loss (*.png);;JPEG pictures (*.jpg)")
        if not path:
            return
        first = Path(path)
        if first.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            first = first.with_suffix(".jpg" if "jpg" in chosen.lower()
                                      else ".png")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            got = self._load_full()
            if got is None:
                return
            sizes, pics = got
            parts = split_table(self.spec, sizes)
            n = len(parts)
            if n == 1:
                targets = [first]
            else:
                targets = [first.with_name(f"{first.stem}_{i}{first.suffix}")
                           for i in range(1, n + 1)]
                there = [t.name for t in targets if t.exists()]
                if there:
                    QApplication.restoreOverrideCursor()
                    if not self._ask("Save in parts",
                                     f"The table makes {n} pictures. These are"
                                     " there already and would be replaced:"
                                     "\n\n" + "\n".join(there[:12])
                                     + (f"\nand {len(there) - 12} more"
                                        if len(there) > 12 else "")):
                        return
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            reduced = 0
            for part, target in zip(parts, targets):
                lay = measure_table(part, sizes)
                scale = fit_scale(lay)
                reduced += scale < 1.0
                img = render_table(part, lay, pics, scale)
                ok = self._write(target, img)
                del img
                if not ok:
                    return
        finally:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        pr_t._remember("table_image_dir", str(first.parent))
        what = (f"Saved {targets[0].name}" if n == 1
                else f"Saved {n} parts, {targets[0].name} to {targets[-1].name}")
        if reduced:
            what += (f" - {reduced} with one photo too large even alone,"
                     " made smaller")
        self._say(what)

    # ---- templates ----------------------------------------------------------
    def _template_dir(self) -> str:
        folder = pr_t.load_ui_state().get("table_template_dir")
        if isinstance(folder, str) and Path(folder).is_dir():
            return folder
        root = self._owner.root_folder()
        return str(root) if root else str(Path.home())

    def _save_template(self) -> None:
        stem = _safe_stem(self.spec.title) or "table template"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save table template",
            str(Path(self._template_dir()) / f"{stem}.json"),
            "Table template (*.json)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            target.write_text(json.dumps(self.spec.to_dict(), indent=2,
                                         ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self._warn("Template not saved", f"{target}\n\n{exc.strerror or exc}")
            return
        pr_t._remember("table_template_dir", str(target.parent))
        self._say(f"Template saved: {target.name}")

    def _load_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load table template",
                                              self._template_dir(),
                                              "Table template (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._warn("Template not loaded", f"{path}\n\n{exc}")
            return
        if not isinstance(data, dict) or data.get("kind") != _TEMPLATE_KIND:
            self._warn("Template not loaded",
                       f"{path}\n\nThis is not a table template.")
            return
        pr_t._remember("table_template_dir", str(Path(path).parent))
        new = TableSpec.from_dict(data)
        if self.spec.has_pictures():
            use_theirs = new.has_pictures() and self._ask(
                "Load template",
                "The template has pictures of its own.\n"
                "Yes: use the template's pictures.\n"
                "No: keep the pictures that are in the table now.")
            if not use_theirs:
                self.spec.trim()
                mine = self.spec.cells
                new.clear_pictures()
                for r in range(min(new.rows, len(mine))):
                    for c in range(min(new.cols, len(mine[r]))):
                        new.cells[r][c] = mine[r][c]
        self.spec = new
        self._after_shape_change()
        self._say(f"Template loaded: {Path(path).name}")

    # ---- small things -------------------------------------------------------
    def _ask(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(text)
        box.setStyleSheet(_MSGBOX_QSS)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _warn(self, title: str, text: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(text)
        box.setStyleSheet(_MSGBOX_QSS)
        box.exec()

    def _store(self) -> None:
        pr_t._remember("table", self.spec.to_dict())

    def shutdown(self) -> None:
        self._store_timer.stop()
        self._store()
        try:
            self._pool.clear()
            self._pool.waitForDone(2000)
        except Exception:
            pass


def _safe_stem(text: str) -> str:
    """A file name made from the title: the first line, nothing Windows
    refuses."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    out = "".join(ch for ch in first if ch not in pr_t._INVALID_NAME_CHARS
                  and ord(ch) >= 32)
    return out.strip().rstrip(".")[:80]
