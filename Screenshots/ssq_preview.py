# ssq_preview.py
"""The preview: a grid of the copied pictures, and the zoom window of one.

Every picture is painted by ssq_core.Tone._render_tuned — the one rendering
path for the grid, the hover popup, the zoom window and Save, the same one the
Tk program used — so what is saved is what was seen.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog,
                               QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QScrollArea, QSlider,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

import ssq_common as ui
import ssq_core as core
from ssq_core import Tone

try:
    from PIL import Image
    _PIL_OK = True
except Exception:
    _PIL_OK = False


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: #c4c4c4;")
    return f


def _vline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet("color: #c4c4c4;")
    return f


def _slider(lo: int, hi: int, value: int, width: int) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    s.setFixedWidth(width)
    return s


def _small_button(name: str, tip: str) -> QToolButton:
    b = ui.icon_button(name, tip=tip, tool=True)
    b.setFixedSize(26, 24)
    return b


class _Thumb(QLabel):
    """One thumbnail: hover shows the big popup, a click opens the zoom window."""

    entered = Signal(object)
    left = Signal()
    clicked = Signal(object)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("QLabel { background: transparent; border: 1px solid #d0d0d0; }")

    def enterEvent(self, ev):
        self.entered.emit(self)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.left.emit()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(ev)


class _Popup(QLabel):
    """The large hover picture. Never takes the focus and never sits under the
    pointer (a popup under the pointer takes the hover away from the thumbnail,
    closes, and the thumbnail opens it again — the flashing loop)."""

    def __init__(self, pm: QPixmap):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("QLabel { background: white; border: 2px solid #333333; }")
        self.setPixmap(pm)
        self.adjustSize()


class PreviewWindow(QWidget):
    """Popup window with the grid of the copied pictures."""

    _THUMB = Tone._THUMB
    _MIN_THUMB = Tone._MIN_THUMB
    _CELL_PAD = Tone._CELL_PAD
    _FILL_SLACK = Tone._FILL_SLACK

    closed = Signal()

    def __init__(self, paths: list[Path], on_keep=None, on_delete=None,
                 on_try_again=None, cycle_mode: bool = False, on_cycle_change=None,
                 parent=None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        ui.paint_window(self)
        ico = core.icon_file()
        if ico:
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(ico)))
        self.setWindowTitle("Auto copy — live preview" if cycle_mode
                            else "Preview — copied images")
        # Wide enough for the whole image toolbar.
        self.resize(1100, 780 if cycle_mode else 720)

        self._cycle_mode = cycle_mode
        self._on_cycle_change = on_cycle_change
        self._cycle_history: list[dict] = []
        self._shown_cycle: int | None = None
        self._caption_extra: dict[Path, str] = {}
        self._paths = list(paths)
        self._on_keep = on_keep
        self._on_delete = on_delete
        self._on_try_again = on_try_again
        # Per-image state set in the zoom window: path -> {overlay, palette,
        # brightness, auto, contrast, auto_bright, gamma, crop_rect, zoom}.
        self._img_states: dict[Path, dict] = {}
        # Decoded pictures, so a tone change never reads the share again.
        self._src_cache: dict[Path, "Image.Image"] = {}
        self._fit_cache: dict[tuple, "Image.Image"] = {}
        self._popup_cache: dict[tuple, "Image.Image"] = {}
        self._cells: dict[Path, _Thumb] = {}
        self._cell_widgets: list[QWidget] = []
        self._popup: _Popup | None = None
        self._zoom_windows: list[QWidget] = []
        self._last_auto_applied: dict | None = None
        self._syncing_sliders = False
        self._thumb_size = self._THUMB
        self._grid_layout: tuple[int, int] | None = None
        self._placed_cells = 0
        self._configured_cols = 0
        self._cap_font = self.font()
        self._cap_font.setPointSizeF(7.5)

        self._retone_timer = QTimer(self)
        self._retone_timer.setSingleShot(True)
        self._retone_timer.setInterval(0)
        self._retone_timer.timeout.connect(self._retone)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(220)
        self._resize_timer.timeout.connect(self._relayout_if_needed)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        if not _PIL_OK:
            root.addWidget(QLabel("Pillow not available."))
            return

        # ── cycle bar (auto runs only) ────────────────────────────
        if cycle_mode:
            cbar = QHBoxLayout()
            cbar.addWidget(QLabel("Cycle:"))
            self._cycle_cb = QComboBox()
            self._cycle_cb.setMinimumWidth(170)
            self._cycle_cb.activated.connect(self._on_cycle_picked)
            cbar.addWidget(self._cycle_cb)
            prev_b = _small_button("prev", "Previous cycle")
            prev_b.clicked.connect(lambda: self._step_cycle(-1))
            next_b = _small_button("next", "Next cycle")
            next_b.clicked.connect(lambda: self._step_cycle(1))
            cbar.addSpacing(6)
            cbar.addWidget(prev_b)
            cbar.addWidget(next_b)
            cbar.addSpacing(10)
            self._follow_cb = QCheckBox("Follow latest")
            self._follow_cb.setChecked(True)
            self._follow_cb.toggled.connect(self._on_follow_toggle)
            cbar.addWidget(self._follow_cb)
            cbar.addSpacing(10)
            self._cycle_status = QLabel("")
            self._cycle_status.setStyleSheet("color: #444444;")
            cbar.addWidget(self._cycle_status, 1)
            root.addLayout(cbar)
            root.addWidget(_hline())

        # ── action bar (Save / Delete / Try again) ────────────────
        abar = QHBoxLayout()
        save_b = ui.icon_button("check", "Save")
        save_b.clicked.connect(self._do_keep)
        abar.addWidget(save_b)
        del_b = ui.icon_button("trash", "Delete this cycle" if cycle_mode else "Delete")
        del_b.clicked.connect(self._do_delete)
        abar.addWidget(del_b)
        if not cycle_mode:
            # In cycle mode there is nothing sensible to retry: the cycle's
            # moment has passed.
            retry_b = ui.icon_button("retry", "Delete and Try Again")
            retry_b.clicked.connect(self._do_try_again)
            abar.addWidget(retry_b)
        abar.addStretch(1)
        root.addLayout(abar)
        root.addWidget(_hline())

        # ── image toolbar, row 1: the tones ───────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addWidget(QLabel("Contrast:"))
        self._contrast_s = _slider(-127, 127, 0, 120)
        self._contrast_s.valueChanged.connect(self._schedule_retone)
        bar.addWidget(self._contrast_s)
        b_c = _small_button("reset", "Contrast back to 0 (Auto off)")
        b_c.clicked.connect(self._reset_contrast)
        bar.addWidget(b_c)
        self._auto_c = QCheckBox("Auto")
        self._auto_c.toggled.connect(self._on_auto_toggle)
        bar.addWidget(self._auto_c)
        bar.addSpacing(12)

        bar.addWidget(QLabel("Brightness:"))
        self._bright_s = _slider(-255, 255, 0, 120)
        self._bright_s.valueChanged.connect(self._schedule_retone)
        bar.addWidget(self._bright_s)
        b_b = _small_button("reset", "Brightness back to 0 (Auto off)")
        b_b.clicked.connect(self._reset_brightness)
        bar.addWidget(b_b)
        self._auto_b = QCheckBox("Auto")
        self._auto_b.toggled.connect(self._on_auto_toggle)
        bar.addWidget(self._auto_b)
        bar.addSpacing(12)

        bar.addWidget(QLabel("Gamma:"))
        self._gamma_s = _slider(20, 300, 100, 120)
        self._gamma_s.valueChanged.connect(self._schedule_retone)
        bar.addWidget(self._gamma_s)
        b_g = _small_button("reset", "Gamma back to 1")
        b_g.clicked.connect(lambda: self._gamma_s.setValue(100))
        bar.addWidget(b_g)
        bar.addStretch(1)
        root.addLayout(bar)
        # Greyed out together on an NI palette; the two sliders also follow
        # their Auto box — see _sync_tone_enable.
        self._tone_widgets = [b_c, self._auto_c, b_b, self._auto_b, self._gamma_s, b_g]

        # Row 2: what the grid looks like rather than what the tones are.
        bar2 = QHBoxLayout()
        bar2.setSpacing(4)
        bar2.addWidget(QLabel("Zoom:"))
        self._zoom_s = _slider(30, 300, 100, 120)
        self._zoom_s.valueChanged.connect(lambda _v: self._relayout_if_needed())
        bar2.addWidget(self._zoom_s)
        bar2.addSpacing(12)
        bar2.addWidget(QLabel("Palette:"))
        self._palette_cb = QComboBox()
        self._palette_cb.addItems(Tone._PALETTES)
        self._palette_cb.setMinimumWidth(130)
        self._palette_cb.currentTextChanged.connect(self._on_palette_changed)
        bar2.addWidget(self._palette_cb)
        bar2.addStretch(1)
        root.addLayout(bar2)

        # ── the grid ──────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._inner = QWidget()
        self._inner.setAttribute(Qt.WA_StyledBackground, True)
        self._inner.setStyleSheet(f"background: {ui.PANEL};")
        self._grid = QGridLayout(self._inner)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(8)
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll, 1)

        QTimer.singleShot(0, self._redraw)

    # ── action callbacks ─────────────────────────────────────────
    def _do_keep(self):
        self._save_all_annotated()
        if self._on_keep:
            self._on_keep()
        if self._cycle_mode:
            # The window is the live view of a running run — saving must not close it.
            self.set_cycle_status("saved")
            return
        self.close()

    def _save_all_annotated(self):
        """Write <name>_annotated.png for every picture edited in the zoom
        window — rendered again at full size, drawing and crop baked in."""
        if not _PIL_OK:
            return
        for path, state in self._img_states.items():
            try:
                overlay = state.get("overlay")
                has_drawing = False
                if overlay is not None:
                    import numpy as np
                    has_drawing = bool(np.array(overlay)[:, :, 3].max() > 0)
                img = Image.open(path)
                base_rgb, _ = Tone._render_tuned(img, **self._settings_for(state))
                base_img = base_rgb.convert("RGBA")
                orig_w, orig_h = img.width, img.height
                base_full = (base_img if base_img.size == (orig_w, orig_h)
                             else base_img.resize((orig_w, orig_h), Image.LANCZOS))
                if has_drawing:
                    ov_scaled = overlay.resize((orig_w, orig_h), Image.LANCZOS)
                    result_full = Image.alpha_composite(base_full, ov_scaled).convert("RGB")
                else:
                    result_full = base_full.convert("RGB")
                crop = state.get("crop_rect")
                if crop is not None:
                    x1, y1, x2, y2 = crop
                    x1 = max(0, min(x1, orig_w))
                    y1 = max(0, min(y1, orig_h))
                    x2 = max(0, min(x2, orig_w))
                    y2 = max(0, min(y2, orig_h))
                    if x2 > x1 and y2 > y1:
                        result_full = result_full.crop((x1, y1, x2, y2))
                result_full.save(path.parent / f"{path.stem}_annotated.png", "PNG")
            except Exception as e:
                print(f"[ANNOTATE] Failed {path.name}: {e}")

    def _do_delete(self):
        if not self._paths:
            if not self._cycle_mode:
                self.close()
            return
        if not ui.ask_yes_no(self, "Delete", f"Delete {len(self._paths)} file(s)?"):
            return
        for p in self._paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if self._on_delete:
            self._on_delete()
        if self._cycle_mode:
            # Only the cycle on screen goes; the window and the rest of the run stay.
            gone = self._shown_cycle
            self._cycle_history = [e for e in self._cycle_history if e["cycle"] != gone]
            self._paths = []
            self._img_states.clear()
            self._shown_cycle = None
            self._refresh_cycle_box()
            if self._cycle_history:
                self.show_cycle(self._cycle_history[-1]["cycle"])
            else:
                self._grid_layout = None
                self._redraw()
            self.set_cycle_status(f"cycle {gone} deleted")
            if self._on_cycle_change:
                self._on_cycle_change(gone)
            return
        self.close()

    def _do_try_again(self):
        if not self._paths:
            self.close()
            return
        if not ui.ask_yes_no(self, "Delete and Try Again",
                             f"Delete {len(self._paths)} file(s) and copy again?"):
            return
        for p in self._paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        self.close()
        if self._on_try_again:
            self._on_try_again()

    # ── tone controls ────────────────────────────────────────────
    def _settings(self) -> dict:
        """The toolbar's viewing settings."""
        return {
            "palette": self._palette_cb.currentText(),
            "auto_contrast": self._auto_c.isChecked(),
            "contrast": self._contrast_s.value(),
            "auto_bright": self._auto_b.isChecked(),
            "brightness": self._bright_s.value(),
            "gamma": self._gamma_s.value() / 100.0,
        }

    def _settings_for(self, state: dict | None) -> dict:
        """An image's own settings if it was edited in the zoom window,
        otherwise the toolbar's."""
        if state:
            return {
                "palette": state.get("palette", "Original"),
                "auto_contrast": state.get("auto", False),
                "contrast": state.get("contrast", 0),
                "auto_bright": state.get("auto_bright", False),
                "brightness": state.get("brightness", 0),
                "gamma": state.get("gamma", 1.0),
            }
        return self._settings()

    def _sync_tone_enable(self):
        """Grey out every tone control on an NI palette (they would do nothing);
        otherwise each slider is live unless its own Auto box is on."""
        ni = self._palette_cb.currentText() in Tone._NI_PALETTES
        for w in self._tone_widgets:
            w.setEnabled(not ni)
        self._contrast_s.setEnabled(not (ni or self._auto_c.isChecked()))
        self._bright_s.setEnabled(not (ni or self._auto_b.isChecked()))

    def _on_palette_changed(self, _name):
        self._sync_tone_enable()
        self._schedule_retone()

    def _on_auto_toggle(self, _on):
        # Auto overrides its slider → grey it out while on; the retone parks
        # the slider where Auto landed.
        self._sync_tone_enable()
        self._schedule_retone()

    def _reset_contrast(self):
        """Back to untouched, Auto included — with Auto left on the next
        redraw would park the slider right back and the button looks dead."""
        self._auto_c.setChecked(False)
        self._contrast_s.setValue(0)
        self._schedule_retone()

    def _reset_brightness(self):
        self._auto_b.setChecked(False)
        self._bright_s.setValue(0)
        self._schedule_retone()

    def _schedule_retone(self, *_):
        """A tone / palette setting changed: re-colour the thumbnails on screen
        once the event loop is idle — a slider drag folds into one pass."""
        if self._syncing_sliders:
            return
        self._retone_timer.start()

    def _retone(self, paths=None):
        """Re-colour `paths` (default: every thumbnail on screen) in place."""
        size = self._thumb_size
        for path in (paths if paths is not None else list(self._cells)):
            lbl = self._cells.get(path)
            if lbl is None:
                continue
            pm = self._process(path, size)
            if pm is not None:
                lbl.setPixmap(pm)
        self._sync_auto_sliders()

    def _sync_auto_sliders(self):
        """Park the disabled sliders where the auto modes landed, so the UI
        shows the contrast/brightness actually applied."""
        applied = self._last_auto_applied
        if not applied:
            return
        self._syncing_sliders = True
        try:
            if self._auto_c.isChecked():
                self._contrast_s.setValue(applied["contrast"])
            if self._auto_b.isChecked():
                self._bright_s.setValue(applied["brightness"])
        finally:
            self._syncing_sliders = False

    # ── pictures ─────────────────────────────────────────────────
    def _load_source(self, path: Path):
        """The file as opened, read from disk once per window."""
        img = self._src_cache.get(path)
        if img is None:
            img = Image.open(path)
            img.load()
            self._src_cache[path] = img
        return img

    def _fitted(self, path: Path, size: int, resample):
        key = (path, size, resample)
        img = self._fit_cache.get(key)
        if img is None:
            img = Tone._fit_to_box(self._load_source(path), size, resample)
            self._fit_cache[key] = img
        return img

    def _trim_caches(self, size: int | None = None):
        keep = set(self._paths)
        self._src_cache = {p: im for p, im in self._src_cache.items() if p in keep}
        self._fit_cache = {k: im for k, im in self._fit_cache.items()
                           if k[0] in keep and (size is None or k[1] == size)}
        self._popup_cache = {k: im for k, im in self._popup_cache.items() if k[0] in keep}

    def _dpr(self) -> float:
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:
            return 1.0

    def _process(self, path: Path, size: int) -> QPixmap | None:
        """One thumbnail with the current settings, `size` logical pixels on
        its long side — rendered at the screen's own pixel density."""
        try:
            state = self._img_states.get(path)
            settings = self._settings_for(state)
            dpr = self._dpr()
            img = self._fitted(path, max(1, round(size * dpr)),
                               Tone._resample_for(settings["palette"]))
            base_rgb, applied = Tone._render_tuned(img, **settings)
            if not state:
                # Drawn with the toolbar settings → remember where the auto
                # modes landed so the sliders can be parked there.
                self._last_auto_applied = applied
            if state and state.get("overlay") is not None:
                base_rgba = base_rgb.convert("RGBA")
                ov = state["overlay"].resize(base_rgba.size, Image.LANCZOS)
                base_rgb = Image.alpha_composite(base_rgba, ov).convert("RGB")
            pm = ui.pil_to_pixmap(base_rgb)
            pm.setDevicePixelRatio(dpr)
            return pm
        except Exception:
            return None

    # ── grid layout ──────────────────────────────────────────────
    def _avail_width(self) -> int:
        avail = self._scroll.viewport().width() - 8
        if avail < 100:   # not shown yet — fall back to the window width
            avail = max(200, self.width() - 40)
        return avail

    def _compute_layout(self) -> tuple[int, int, int]:
        """(thumb_size, columns, columns_that_fit) for the zoom and the width.

        The Zoom slider asks for a thumbnail size; the grid takes as many
        columns of that size as fit and lets the thumbnails grow up to
        _FILL_SLACK× so the row fills the window exactly.
        """
        avail = self._avail_width()
        pad = self._CELL_PAD
        want = max(self._MIN_THUMB, int(self._THUMB * self._zoom_s.value() / 100.0))
        want = min(want, max(self._MIN_THUMB, avail - pad))
        cols_fit = max(1, avail // (want + pad))
        size = max(self._MIN_THUMB,
                   min(avail // cols_fit - pad, int(want * self._FILL_SLACK)))
        cols = max(1, min(cols_fit, len(self._paths)))
        return size, cols, cols_fit

    def _relayout_if_needed(self):
        if self._syncing_sliders:
            return
        size, cols, _fit = self._compute_layout()
        if (size, cols) != self._grid_layout:
            self._redraw()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if ev.oldSize().width() != ev.size().width():
            self._resize_timer.start()

    def _fit_caption(self, text: str, max_px: int) -> str:
        """Middle-ellipsize `text` until it fits `max_px`."""
        fm = QFontMetrics(self._cap_font)
        if fm.horizontalAdvance(text) <= max_px:
            return text
        keep = len(text)
        while keep > 4:
            keep -= 1
            head = keep // 2
            cand = text[:head] + "…" + text[len(text) - (keep - head):]
            if fm.horizontalAdvance(cand) <= max_px:
                return cand
        return text[:4] + "…"

    def _redraw(self):
        if self._syncing_sliders or not _PIL_OK:
            return
        self._hide_popup()
        self._retone_timer.stop()
        for w in self._cell_widgets:
            self._grid.removeWidget(w)
            w.deleteLater()
        self._cell_widgets.clear()
        self._cells.clear()

        size, cols, cols_fit = self._compute_layout()
        self._thumb_size = size
        self._grid_layout = (size, cols)
        self._trim_caches(None)

        # Full rows share the leftover pixels evenly; a grid narrower than the
        # window stays left-packed instead of drifting apart.
        stretch = cols >= cols_fit
        for c in range(max(self._configured_cols, cols) + 1):
            inside = c < cols
            self._grid.setColumnStretch(c, 1 if (inside and stretch) else 0)
            self._grid.setColumnMinimumWidth(c, size + 8 if inside else 0)
        self._grid.setColumnStretch(cols, 0 if stretch else 1)
        self._configured_cols = cols + 1
        self._placed_cells = 0
        for path in self._paths:
            if self._make_cell(path, size, cols, self._placed_cells):
                self._placed_cells += 1
        self._grid.setRowStretch(self._grid.rowCount(), 1)
        self._sync_auto_sliders()

    def _make_cell(self, path: Path, size: int, cols: int, index: int) -> bool:
        """Render one thumbnail into the grid at `index`. False if unreadable."""
        pm = self._process(path, size)
        if pm is None:
            return False
        r, c = divmod(index, cols)
        cell = QWidget()
        cell.setAttribute(Qt.WA_StyledBackground, True)
        cell.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(cell)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        thumb = _Thumb(path)
        thumb.setPixmap(pm)
        dpr = pm.devicePixelRatio() or 1.0
        thumb.setFixedSize(round(pm.width() / dpr) + 2, round(pm.height() / dpr) + 2)
        thumb.entered.connect(self._show_popup)
        thumb.left.connect(self._hide_popup)
        thumb.clicked.connect(self._on_click)
        lay.addWidget(thumb, 0, Qt.AlignHCenter)

        # The caption is never wider than the thumbnail.
        caption = core.display_timestamp_only(path.name)
        fm = QFontMetrics(self._cap_font)
        if fm.horizontalAdvance(caption) > size and " " in caption:
            caption = caption.split(" ", 1)[1]   # tiny thumbnails: keep the time
        extra = self._caption_extra.get(path, "")
        if extra:
            caption = f"{caption} {extra}"
        cap = QLabel(self._fit_caption(caption, size))
        cap.setFont(self._cap_font)
        cap.setStyleSheet("color: #555555; font-size: 7.5pt;")
        cap.setAlignment(Qt.AlignHCenter)
        cap.setToolTip(path.name)   # full file name incl. timestamp
        lay.addWidget(cap, 0, Qt.AlignHCenter)

        self._grid.addWidget(cell, r, c, Qt.AlignTop | Qt.AlignHCenter)
        self._cell_widgets.append(cell)
        self._cells[path] = thumb
        return True

    # ── live update (one persistent window) ──────────────────────
    def append_path(self, path: Path):
        """Add one just-copied file without rebuilding the grid — a full
        redraw would decode every earlier picture again for each new file."""
        self._paths.append(path)
        size, cols, _fit = self._compute_layout()
        if (size, cols) != self._grid_layout:
            self._grid_layout = None
            self._redraw()
            return
        if self._make_cell(path, size, cols, self._placed_cells):
            self._placed_cells += 1
        self._sync_auto_sliders()

    # ── cycle picker (auto runs) ─────────────────────────────────
    @staticmethod
    def _cycle_label(entry: dict) -> str:
        return f"Cycle {entry['cycle']}  ({entry['ts']})"

    def _refresh_cycle_box(self):
        if not self._cycle_mode:
            return
        self._cycle_cb.blockSignals(True)
        self._cycle_cb.clear()
        self._cycle_cb.addItems([self._cycle_label(e) for e in self._cycle_history])
        idx = next((i for i, e in enumerate(self._cycle_history)
                    if e["cycle"] == self._shown_cycle), -1)
        self._cycle_cb.setCurrentIndex(idx)
        self._cycle_cb.blockSignals(False)

    def set_cycle_history(self, history: list[dict]):
        """Publish the run's cycle list; the user's own pick is left alone."""
        if not self._cycle_mode:
            return
        self._cycle_history = list(history)
        self._refresh_cycle_box()

    @property
    def following(self) -> bool:
        return bool(self._cycle_mode and self._follow_cb.isChecked())

    @property
    def shown_cycle(self) -> int | None:
        return self._shown_cycle

    def set_cycle_status(self, text: str):
        if self._cycle_mode:
            self._cycle_status.setText(text)

    def show_cycle(self, cycle_num: int | None):
        """Swap the grid to this cycle's files, in place."""
        if not self._cycle_mode:
            return
        entry = next((e for e in self._cycle_history if e["cycle"] == cycle_num), None)
        self._shown_cycle = cycle_num if entry else None
        paths = list(entry["files"]) if entry else []
        keep = set(paths)
        self._img_states = {p: st for p, st in self._img_states.items() if p in keep}
        self._caption_extra = {p: t for p, t in self._caption_extra.items() if p in keep}
        self._paths = paths
        self._grid_layout = None
        self._refresh_cycle_box()
        self._redraw()

    def set_caption_extra(self, path: Path, text: str):
        self._caption_extra[Path(path)] = text

    def _on_cycle_picked(self, idx: int):
        if idx < 0 or idx >= len(self._cycle_history):
            return
        # Picking a cycle by hand means "stay here" — stop jumping to the newest.
        self._follow_cb.setChecked(False)
        self.show_cycle(self._cycle_history[idx]["cycle"])

    def _step_cycle(self, delta: int):
        if not self._cycle_history:
            return
        nums = [e["cycle"] for e in self._cycle_history]
        try:
            idx = nums.index(self._shown_cycle)
        except ValueError:
            idx = len(nums) - 1
        new_idx = max(0, min(idx + delta, len(nums) - 1))
        self._follow_cb.setChecked(False)
        self.show_cycle(nums[new_idx])

    def _on_follow_toggle(self, on: bool):
        if on and self._cycle_history:
            self.show_cycle(self._cycle_history[-1]["cycle"])

    # ── large hover popup ────────────────────────────────────────
    def _show_popup(self, thumb: _Thumb):
        self._hide_popup()
        path = thumb.path
        try:
            state = self._img_states.get(path)
            settings = self._settings_for(state)
            resample = Tone._resample_for(settings["palette"])
            src = self._load_source(path)
            scr = thumb.screen() or QApplication.primaryScreen()
            area = scr.availableGeometry()
            dpr = self._dpr()
            # Off the size on screen, kept on screen, never past the file itself.
            logical = min(max(self._MIN_THUMB, self._thumb_size) * 3,
                          int(area.height() * 0.8))
            phys = min(round(logical * dpr), max(src.width, src.height))
            key = (path, phys, resample)
            img = self._popup_cache.get(key)
            if img is None:
                img = Tone._fit_to_box(src, phys, resample)
                self._popup_cache[key] = img
            base_rgb, _ = Tone._render_tuned(img, **settings)
            pm = ui.pil_to_pixmap(base_rgb)
            pm.setDevicePixelRatio(dpr)
        except Exception:
            return
        popup = _Popup(pm)
        pw, ph = popup.width(), popup.height()
        top_left = thumb.mapToGlobal(QPoint(0, 0))
        ax, ay, aw, ah = top_left.x(), top_left.y(), thumb.width(), thumb.height()
        from PySide6.QtGui import QCursor
        px = QCursor.pos().x()
        left, top = area.left(), area.top()
        right, bottom = area.right() + 1, area.bottom() + 1
        gap = 8
        if ax + aw + gap + pw <= right:
            x = ax + aw + gap                      # right of the thumbnail
        elif ax - gap - pw >= left:
            x = ax - gap - pw                      # left of it
        else:
            x = left if (ax - left) > (right - ax - aw) else right - pw
            if x <= px <= x + pw:
                x = px + gap if px + gap + pw <= right else px - gap - pw
        y = max(top, min(ay, bottom - ph))
        popup.move(int(x), int(y))
        popup.show()
        self._popup = popup

    def _hide_popup(self):
        if self._popup is not None:
            try:
                self._popup.close()
                self._popup.deleteLater()
            except Exception:
                pass
            self._popup = None

    def _on_click(self, path: Path):
        self._hide_popup()
        self._open_zoom_window(path)

    def _open_zoom_window(self, path: Path):
        import shiboken6
        self._zoom_windows = [w for w in self._zoom_windows if shiboken6.isValid(w)]
        win = ZoomWindow(self, path)
        self._zoom_windows.append(win)
        win.show()

    def apply_image_state(self, path: Path, state: dict | None):
        """The zoom window's ✔ Apply / ↺ Revert."""
        if state is None:
            self._img_states.pop(path, None)
        else:
            self._img_states[path] = state
        self._retone([path])   # only this picture changed

    def hideEvent(self, ev):
        self._hide_popup()
        super().hideEvent(ev)

    def closeEvent(self, ev):
        self._hide_popup()
        import shiboken6
        for w in self._zoom_windows:
            if shiboken6.isValid(w):
                w.close()
        self._zoom_windows = []
        self.closed.emit()
        super().closeEvent(ev)


# ─────────────────────────────────────────────────────────────────────────────
# Zoom window: one picture, its own tones, crop and drawing
# ─────────────────────────────────────────────────────────────────────────────
class _Canvas(QWidget):
    """The picture at the chosen zoom, plus the shapes being dragged out.

    Left button draws with the chosen tool (or zooms into a dragged box with
    the Zoom tool); the right button drags the crop that Save applies.
    """

    def __init__(self, owner: "ZoomWindow"):
        super().__init__()
        self.owner = owner
        self.base: QPixmap | None = None
        self.overlay: QPixmap | None = None
        self.preview: tuple | None = None        # (tool, QRect) while dragging
        self.free_pts: list[QPoint] = []
        self.crop_preview: QRect | None = None
        self.zoom_preview: QRect | None = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: #3a3a3a;")
        self.setMouseTracking(False)

    def set_pictures(self, base: QPixmap, overlay: QPixmap | None):
        self.base = base
        self.overlay = overlay
        self.setFixedSize(base.size())
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        if self.base is not None:
            p.drawPixmap(0, 0, self.base)
        if self.overlay is not None:
            p.drawPixmap(0, 0, self.overlay)
        o = self.owner
        p.setRenderHint(QPainter.Antialiasing)
        if self.preview is not None:
            tool, rect = self.preview
            pen = QPen(QColor(o.draw_color), o.width_spin.value())
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if tool == "rect":
                p.drawRect(rect)
            elif tool == "circle":
                p.drawEllipse(rect)
            elif tool == "cross":
                c = rect.center()
                half = max(abs(rect.width()), abs(rect.height())) // 2
                p.drawLine(c.x() - half, c.y(), c.x() + half, c.y())
                p.drawLine(c.x(), c.y() - half, c.x(), c.y() + half)
        if len(self.free_pts) > 1:
            pen = QPen(QColor(o.draw_color), o.width_spin.value())
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(self.free_pts)
        for rect, colour in ((self.crop_preview, "#00ff00"), (self.zoom_preview, "#ffff00")):
            if rect is not None:
                pen = QPen(QColor(colour), 2, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.normalized())
        p.end()

    def mousePressEvent(self, ev):
        self.owner.on_press(ev)

    def mouseMoveEvent(self, ev):
        self.owner.on_move(ev)

    def mouseReleaseEvent(self, ev):
        self.owner.on_release(ev)


class ZoomWindow(QWidget):
    """One picture at a chosen zoom, with its own tones, a crop and drawings.
    ✔ Apply hands the result back to the grid; ↺ Revert forgets it."""

    DEFAULT_W, DEFAULT_H = 900, 600

    def __init__(self, preview: PreviewWindow, path: Path):
        super().__init__(preview, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        ui.paint_window(self)
        ico = core.icon_file()
        if ico:
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(ico)))
        self.setWindowTitle(path.name)
        self.preview = preview
        self.path = path
        st = preview._img_states.get(path, {})
        glob = preview._settings()

        self._src_img = None
        self._resized_key = None
        self._resized_img = None
        self._base_pil = None
        self._overlay_pil = None    # the drawing at the size on screen
        # The drawing as last drawn on. A zoom change shows a resized copy of
        # it, so zooming back and forth never blurs or throws the drawing away.
        self._overlay_src = None
        if st.get("overlay") is not None:
            self._overlay_pil = st["overlay"].copy()
            self._overlay_src = self._overlay_pil
        self._undo: list = []
        self._crop_rect = st.get("crop_rect")
        self._drag_start: QPoint | None = None
        self._crop_start: QPoint | None = None
        self._syncing = False
        self.draw_color = "#ff0000"

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._render_now)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        # ── row 1: the tones ──────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addWidget(QLabel("Contrast:"))
        self.contrast_s = _slider(-127, 127, int(st.get("contrast", glob["contrast"])), 110)
        bar.addWidget(self.contrast_s)
        b_c = _small_button("reset", "Contrast back to 0 (Auto off)")
        bar.addWidget(b_c)
        self.auto_c = QCheckBox("Auto")
        self.auto_c.setChecked(bool(st.get("auto", glob["auto_contrast"])))
        bar.addWidget(self.auto_c)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Brightness:"))
        self.bright_s = _slider(-255, 255, int(st.get("brightness", glob["brightness"])), 110)
        bar.addWidget(self.bright_s)
        b_b = _small_button("reset", "Brightness back to 0 (Auto off)")
        bar.addWidget(b_b)
        self.auto_b = QCheckBox("Auto")
        self.auto_b.setChecked(bool(st.get("auto_bright", glob["auto_bright"])))
        bar.addWidget(self.auto_b)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Gamma:"))
        self.gamma_s = _slider(20, 300, int(round(st.get("gamma", glob["gamma"]) * 100)), 110)
        bar.addWidget(self.gamma_s)
        b_g = _small_button("reset", "Gamma back to 1")
        bar.addWidget(b_g)
        bar.addStretch(1)
        root.addLayout(bar)
        self._tone_widgets = [b_c, self.auto_c, b_b, self.auto_b, self.gamma_s, b_g]

        # ── row 2: zoom, palette and the decisions ────────────────
        bar_b = QHBoxLayout()
        bar_b.setSpacing(4)
        bar_b.addWidget(QLabel("Zoom:"))
        self.zoom_s = _slider(10, 500, int(round(st.get("zoom", 1.0) * 100)), 140)
        bar_b.addWidget(self.zoom_s)
        bar_b.addSpacing(12)
        bar_b.addWidget(QLabel("Palette:"))
        self.palette_cb = QComboBox()
        self.palette_cb.addItems(Tone._PALETTES)
        self.palette_cb.setCurrentText(st.get("palette", glob["palette"]))
        self.palette_cb.setMinimumWidth(130)
        bar_b.addWidget(self.palette_cb)
        bar_b.addSpacing(12)
        clear_crop = ui.icon_button("clear", "Clear crop")
        clear_crop.clicked.connect(self._clear_crop)
        bar_b.addWidget(clear_crop)
        bar_b.addSpacing(8)
        bar_b.addWidget(_vline())
        bar_b.addSpacing(8)
        apply_b = ui.icon_button("check", "Apply")
        apply_b.clicked.connect(self._apply_changes)
        bar_b.addWidget(apply_b)
        revert_b = ui.icon_button("reset", "Revert")
        revert_b.clicked.connect(self._revert_changes)
        bar_b.addWidget(revert_b)
        bar_b.addSpacing(12)
        clear_crop.setToolTip("The crop Save applies is dragged out with the right mouse button")
        crop_hint = ui.hint_label("Crop: right-drag")
        bar_b.addWidget(crop_hint)
        bar_b.addStretch(1)
        root.addLayout(bar_b)
        root.addWidget(_hline())

        # ── row 3: drawing ────────────────────────────────────────
        draw_bar = QHBoxLayout()
        draw_bar.setSpacing(4)
        draw_bar.addWidget(QLabel("Draw:"))
        self.tool_group = QButtonGroup(self)
        for i, (name, val) in enumerate([("None", "none"), ("Rect", "rect"),
                                         ("Circle", "circle"), ("Cross", "cross"),
                                         ("Free", "free"), ("Zoom", "zoom")]):
            rb = QRadioButton(name)
            rb.setProperty("tool", val)
            rb.setChecked(i == 0)
            if val == "zoom":
                rb.setToolTip("Drag a box to magnify it (the crop for Save is "
                              "set with the right mouse button alone)")
            self.tool_group.addButton(rb)
            draw_bar.addWidget(rb)
        draw_bar.addSpacing(10)
        draw_bar.addWidget(QLabel("Width:"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20)
        self.width_spin.setValue(2)
        draw_bar.addWidget(self.width_spin)
        draw_bar.addSpacing(10)
        self.color_btn = QPushButton("Color")
        self.color_btn.clicked.connect(self._pick_color)
        self._paint_color_btn()
        draw_bar.addWidget(self.color_btn)
        draw_bar.addSpacing(10)
        undo_b = QPushButton("Undo")
        undo_b.clicked.connect(self._undo_last)
        draw_bar.addWidget(undo_b)
        clear_b = QPushButton("Clear drawing")
        clear_b.clicked.connect(self._clear_overlay)
        draw_bar.addWidget(clear_b)
        draw_bar.addStretch(1)
        root.addLayout(draw_bar)
        root.addWidget(_hline())

        # ── the picture ───────────────────────────────────────────
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.scroll.setStyleSheet("QScrollArea { background: #3a3a3a; border: none; }")
        self.canvas = _Canvas(self)
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)

        # wiring
        for s in (self.zoom_s, self.contrast_s, self.bright_s, self.gamma_s):
            s.valueChanged.connect(self._schedule_render)
        self.auto_c.toggled.connect(self._on_auto_toggle)
        self.auto_b.toggled.connect(self._on_auto_toggle)
        self.palette_cb.currentTextChanged.connect(self._on_auto_toggle)
        b_c.clicked.connect(self._reset_contrast)
        b_b.clicked.connect(self._reset_bright)
        b_g.clicked.connect(lambda: self.gamma_s.setValue(100))
        self._sync_auto_enable()
        self._fit_window()
        self._schedule_render()

    # ── controls ─────────────────────────────────────────────────
    @property
    def tool(self) -> str:
        b = self.tool_group.checkedButton()
        return b.property("tool") if b is not None else "none"

    @property
    def zoom(self) -> float:
        return self.zoom_s.value() / 100.0

    def _sync_auto_enable(self):
        ni = self.palette_cb.currentText() in Tone._NI_PALETTES
        for w in self._tone_widgets:
            w.setEnabled(not ni)
        self.contrast_s.setEnabled(not (ni or self.auto_c.isChecked()))
        self.bright_s.setEnabled(not (ni or self.auto_b.isChecked()))

    def _on_auto_toggle(self, *_):
        self._sync_auto_enable()
        self._schedule_render()

    def _reset_contrast(self):
        # Auto off as well — with Auto on, the next render parks the slider
        # back where Auto wants it and the button looks dead.
        self.auto_c.setChecked(False)
        self.contrast_s.setValue(0)

    def _reset_bright(self):
        self.auto_b.setChecked(False)
        self.bright_s.setValue(0)

    def _paint_color_btn(self):
        c = QColor(self.draw_color)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        ink = "#111111" if lum > 140 else "#ffffff"
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background: {self.draw_color}; color: {ink};"
            " border: 1px solid #555555; border-radius: 3px; padding: 3px 14px; }")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self.draw_color), self, "Pick draw color")
        if c.isValid():
            self.draw_color = c.name()
            self._paint_color_btn()

    def _apply_changes(self):
        self.preview.apply_image_state(self.path, {
            "overlay": self._overlay_pil.copy() if self._overlay_pil is not None else None,
            "palette": self.palette_cb.currentText(),
            "brightness": self.bright_s.value(),
            "auto": self.auto_c.isChecked(),
            "contrast": self.contrast_s.value(),
            "auto_bright": self.auto_b.isChecked(),
            "gamma": self.gamma_s.value() / 100.0,
            "crop_rect": self._crop_rect,
            "zoom": self.zoom,
        })
        self.close()

    def _revert_changes(self):
        self.preview.apply_image_state(self.path, None)
        self.close()

    # ── overlay ──────────────────────────────────────────────────
    def _new_overlay(self, w, h):
        self._overlay_pil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        self._overlay_src = self._overlay_pil

    def _push_undo(self):
        # From here on the drawing on screen is the one being drawn on.
        if self._overlay_pil is not None:
            self._undo.append(self._overlay_pil.copy())
            if len(self._undo) > 30:
                self._undo.pop(0)
            self._overlay_src = self._overlay_pil

    def _undo_last(self):
        if self._undo:
            self._overlay_pil = self._undo.pop()
            self._overlay_src = self._overlay_pil
            self._render_now()

    def _clear_overlay(self):
        if self._overlay_pil is not None:
            self._push_undo()
            self._new_overlay(*self._overlay_pil.size)
            self._render_now()

    def _clear_crop(self):
        self._crop_rect = None
        self.canvas.crop_preview = None
        self.canvas.update()

    def _rgba(self, alpha=200):
        c = QColor(self.draw_color)
        return (c.red(), c.green(), c.blue(), alpha)

    # ── render ───────────────────────────────────────────────────
    def _schedule_render(self, *_):
        if self._syncing:
            return   # the auto modes parking their sliders
        self._render_timer.start()

    def _render_now(self):
        try:
            if self._src_img is None:
                img0 = Image.open(self.path)
                img0.load()
                self._src_img = img0
            img = self._src_img
            z = self.zoom
            w = max(1, int(img.width * z))
            h = max(1, int(img.height * z))
            palette = self.palette_cb.currentText()
            resample = Tone._resample_for(palette)
            if self._resized_key == ((w, h), resample):
                img_r = self._resized_img          # tone change only: no resize
            else:
                try:
                    img_r = img.resize((w, h), resample)
                except Exception:
                    img_r = img.resize((w, h), Image.NEAREST)
                self._resized_key, self._resized_img = ((w, h), resample), img_r
            use_auto, use_auto_b = self.auto_c.isChecked(), self.auto_b.isChecked()
            base_rgb, applied = Tone._render_tuned(
                img_r, palette, use_auto, self.contrast_s.value(),
                use_auto_b, self.bright_s.value(), self.gamma_s.value() / 100.0)
            if use_auto or use_auto_b:
                self._syncing = True
                try:
                    if use_auto:
                        self.contrast_s.setValue(applied["contrast"])
                    if use_auto_b:
                        self.bright_s.setValue(applied["brightness"])
                finally:
                    self._syncing = False
            self._base_pil = base_rgb
            if self._overlay_src is None:
                self._new_overlay(w, h)
            elif self._overlay_pil is None or self._overlay_pil.size != (w, h):
                # Another zoom: the drawing resized from the one drawn on —
                # never thrown away, never resized from a resize.
                src_ov = self._overlay_src
                self._overlay_pil = (src_ov.copy() if src_ov.size == (w, h)
                                     else src_ov.resize((w, h), Image.LANCZOS))
            self._show_pictures()
        except Exception as ex:
            print(f"[ZOOM RENDER] {ex}")

    def _show_pictures(self):
        base = ui.pil_to_pixmap(self._base_pil)
        ov = ui.pil_to_pixmap(self._overlay_pil) if self._overlay_pil is not None else None
        self.canvas.set_pictures(base, ov)
        self._show_crop()

    def _show_crop(self):
        if self._crop_rect is None:
            self.canvas.crop_preview = None
        else:
            z = self.zoom
            x1, y1, x2, y2 = self._crop_rect
            self.canvas.crop_preview = QRect(QPoint(int(x1 * z), int(y1 * z)),
                                             QPoint(int(x2 * z), int(y2 * z)))
        self.canvas.update()

    def _fit_window(self):
        """Big enough for the picture, never past 85 % of the screen."""
        try:
            with Image.open(self.path) as info:
                iw, ih = info.width, info.height
            area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            tw = min(max(iw + 30, self.DEFAULT_W), int(area.width() * 0.85))
            th = min(max(ih + 170, self.DEFAULT_H), int(area.height() * 0.85))
        except Exception:
            tw, th = self.DEFAULT_W, self.DEFAULT_H
        self.setMinimumSize(self.DEFAULT_W, self.DEFAULT_H)
        self.resize(tw, th)

    # ── mouse ────────────────────────────────────────────────────
    def on_press(self, ev):
        pos = ev.position().toPoint()
        if ev.button() == Qt.RightButton:
            self._crop_start = pos
            self.canvas.crop_preview = None
            self.canvas.update()
            return
        if ev.button() != Qt.LeftButton:
            return
        tool = self.tool
        if tool == "none":
            return
        self._drag_start = pos
        self.canvas.preview = None
        if tool == "zoom":
            self.canvas.zoom_preview = None
            return
        self._push_undo()
        if tool == "free":
            self.canvas.free_pts = [pos]

    def on_move(self, ev):
        pos = ev.position().toPoint()
        if self._crop_start is not None and (ev.buttons() & Qt.RightButton):
            self.canvas.crop_preview = QRect(self._crop_start, pos)
            self.canvas.update()
            return
        tool = self.tool
        if tool == "none" or self._drag_start is None:
            return
        if tool == "free":
            self.canvas.free_pts.append(pos)
        elif tool == "zoom":
            self.canvas.zoom_preview = QRect(self._drag_start, pos)
        else:
            self.canvas.preview = (tool, QRect(self._drag_start, pos))
        self.canvas.update()

    def on_release(self, ev):
        pos = ev.position().toPoint()
        if ev.button() == Qt.RightButton:
            if self._crop_start is None:
                return
            sx, sy = self._crop_start.x(), self._crop_start.y()
            z = self.zoom
            x1, y1 = int(min(sx, pos.x()) / z), int(min(sy, pos.y()) / z)
            x2, y2 = int(max(sx, pos.x()) / z), int(max(sy, pos.y()) / z)
            if x2 > x1 and y2 > y1:
                self._crop_rect = (x1, y1, x2, y2)
            self._crop_start = None
            self._show_crop()
            return
        if ev.button() != Qt.LeftButton:
            return
        tool = self.tool
        if tool == "none" or self._drag_start is None:
            self._drag_start = None
            return
        sx, sy = self._drag_start.x(), self._drag_start.y()
        ix, iy = pos.x(), pos.y()
        self._drag_start = None
        self.canvas.preview = None

        if tool == "zoom":
            self.canvas.zoom_preview = None
            z = self.zoom
            x1, y1 = int(min(sx, ix) / z), int(min(sy, iy) / z)
            x2, y2 = int(max(sx, ix) / z), int(max(sy, iy) / z)
            if x2 > x1 and y2 > y1:
                # Only magnifies — the crop for Save is the right button's alone.
                vw, vh = self.scroll.viewport().width(), self.scroll.viewport().height()
                new_z = max(0.1, min(min(vw / (x2 - x1), vh / (y2 - y1)) * 0.95, 5.0))
                self.zoom_s.setValue(int(round(new_z * 100)))
                self._render_now()
                nz = self.zoom
                cx = (x1 + x2) / 2 * nz
                cy = (y1 + y2) / 2 * nz
                QTimer.singleShot(0, lambda: (
                    self.scroll.horizontalScrollBar().setValue(int(cx - vw / 2)),
                    self.scroll.verticalScrollBar().setValue(int(cy - vh / 2))))
            self.canvas.update()
            return

        from PIL import ImageDraw
        if self._overlay_pil is None:
            return
        draw = ImageDraw.Draw(self._overlay_pil)
        color = self._rgba()
        lw = self.width_spin.value()
        if tool == "free":
            pts = [(p.x(), p.y()) for p in self.canvas.free_pts]
            if len(pts) > 1:
                draw.line(pts, fill=color, width=lw, joint="curve")
            self.canvas.free_pts = []
        else:
            box = [min(sx, ix), min(sy, iy), max(sx, ix), max(sy, iy)]
            if tool == "rect":
                draw.rectangle(box, outline=color, width=lw)
            elif tool == "circle":
                draw.ellipse(box, outline=color, width=lw)
            elif tool == "cross":
                cx2, cy2 = (sx + ix) // 2, (sy + iy) // 2
                half = max(abs(ix - sx), abs(iy - sy)) // 2
                draw.line([(cx2 - half, cy2), (cx2 + half, cy2)], fill=color, width=lw)
                draw.line([(cx2, cy2 - half), (cx2, cy2 + half)], fill=color, width=lw)
        self._overlay_src = self._overlay_pil
        self._show_pictures()
