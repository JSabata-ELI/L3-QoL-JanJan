"""
make_sample_folder.py  --  a throwaway folder of pictures to try the program on.

Writes ~18 small PNGs plus a sub-folder and one text file (the text file is there
on purpose: renaming a picture onto its name must be reported as a clash).

    python make_sample_folder.py <target folder>
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication

_COLOURS = ["#2f6fb5", "#8d1c12", "#2e7d32", "#7a5c1e", "#5b2f78", "#1e6b73"]


def _write(path: Path, label: str, w: int, h: int, colour: str) -> None:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    grad = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    grad.setColorAt(0.0, QColor(colour))
    grad.setColorAt(1.0, QColor("#101418"))
    p = QPainter(img)
    p.fillRect(QRectF(0, 0, w, h), grad)
    p.setPen(QColor("#ffffff"))
    f = QFont()
    f.setPointSize(max(10, h // 8))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, w, h), int(Qt.AlignmentFlag.AlignCenter), label)
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), path.suffix.lstrip(".").upper())


def build(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    sizes = [(640, 480), (800, 300), (400, 700), (512, 512)]
    for i in range(1, 13):
        w, h = sizes[i % len(sizes)]
        suffix = ".jpg" if i % 4 == 0 else ".png"
        _write(root / f"shot_{i:02d}{suffix}", f"{i:02d}", w, h,
               _COLOURS[i % len(_COLOURS)])
    sub = root / "day2"
    for i in range(1, 7):
        _write(sub / f"beam_{i:02d}.png", f"d2-{i:02d}", 600, 400,
               _COLOURS[(i + 2) % len(_COLOURS)])
    (root / "notes.txt").write_text("not a picture\n", encoding="utf-8")
    print(f"wrote sample pictures into {root}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    app = QApplication([])          # QImage text drawing needs a Qt application
    build(Path(sys.argv[1]))
