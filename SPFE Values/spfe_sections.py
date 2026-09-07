"""The collapsible panel section the whole suite uses.

Copied from Image Tools (`is_t.py`, class CollapsibleSection) so this program
looks like the rest of the suite without importing a 23 000-line module for one
widget -- the same reason daypicker.py is a copy here rather than an import.
The class is self-contained: four widget classes and two Qt names, nothing from
either program.

Keep it in step with the Image Tools original by hand if that one changes.

    sec = CollapsibleSection("Day", "day", expanded=True, accent="#2e9e5b")
    sec.body_layout.addWidget(...)   /   .addLayout(...)
    sec.set_expanded(bool)
    sec.toggled -> Signal(key: str, expanded: bool)
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """Clickable header (bold UPPERCASE title + a coloured bar + arrow) over a
    body that hides and shows on toggle."""

    toggled = Signal(str, bool)

    _ACCENT = "#4a78c0"

    @staticmethod
    def _shade(hex_color: str, factor: float) -> str:
        """hex_color scaled toward black (factor < 1) or white (factor > 1)."""
        try:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            if factor <= 1.0:
                r, g, b = (int(c * factor) for c in (r, g, b))
            else:
                r, g, b = (int(c + (255 - c) * (factor - 1.0)) for c in (r, g, b))
            r, g, b = (max(0, min(255, c)) for c in (r, g, b))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    @classmethod
    def _header_qss(cls, accent: str) -> str:
        return (
            "QToolButton {"
            "  text-align: left; border: none; border-radius: 4px;"
            "  padding: 7px 9px; margin-top: 6px;"
            "  font-weight: 700; font-size: 11px; letter-spacing: 1px;"
            "  color: #fff; background: %(acc)s;"
            "}"
            "QToolButton:hover { background: %(hov)s; }"
        ) % {"acc": accent, "hov": cls._shade(accent, 0.85)}

    def __init__(self, title: str, key: str, expanded: bool = True, parent=None,
                 accent: str | None = None):
        super().__init__(parent)
        self._key = key
        self._title = title
        self._expanded = expanded
        accent = accent or self._ACCENT

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._header = QToolButton()
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        self._header.setStyleSheet(self._header_qss(accent))
        self._header.clicked.connect(self._on_header_clicked)
        lay.addWidget(self._header)

        self.body = QWidget()
        self.body.setObjectName("secBody")
        # An accent-tinted left stripe plus a very light wash of the same
        # accent ties the body to its coloured header. The tint is deliberately
        # near-white (factor 1.93): anything stronger and the black control
        # text stops being comfortably legible. An ID selector, so only this
        # widget is painted and the controls inside keep their own background.
        self.body.setStyleSheet(
            f"#secBody {{ border-left: 3px solid {self._shade(accent, 1.35)};"
            f" background: {self._shade(accent, 1.93)};"
            " border-bottom-right-radius: 4px; }")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 5, 6, 8)
        self.body_layout.setSpacing(5)
        lay.addWidget(self.body)

        self.body.setVisible(expanded)
        self._update_header()

    @property
    def key(self) -> str:
        return self._key

    def _update_header(self):
        arrow = "▾" if self._expanded else "▸"
        # '&' is escaped, or QToolButton takes it for a keyboard shortcut.
        title = self._title.upper().replace("&", "&&")
        self._header.setText(f"{arrow}  {title}")

    def _on_header_clicked(self):
        self.set_expanded(self._header.isChecked())
        self.toggled.emit(self._key, self._expanded)

    def set_expanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self._header.setChecked(self._expanded)
        self.body.setVisible(self._expanded)
        self._update_header()

    def is_expanded(self) -> bool:
        return self._expanded
