# ssq_dialogs.py
"""The side windows: Preset Manager, Labels, Detail notes, and the monitor
numbers shown by Identify. None of them is modal — the main window stays
usable while one is open."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QScrollArea, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import ssq_common as ui
import ssq_core as core


def _window_icon(w: QWidget):
    ico = core.icon_file()
    if ico:
        w.setWindowIcon(QIcon(str(ico)))


def _dialog(parent, title: str) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    dlg.setModal(False)
    dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dlg.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
    ui.paint_window(dlg)
    _window_icon(dlg)
    return dlg


# ─────────────────────────────────────────────────────────────────────────────
# Preset Manager
# ─────────────────────────────────────────────────────────────────────────────
class PresetManager(QDialog):
    """Add, rename, remove and reorder (drag a row) the presets.

    `host` is the main window: its `_presets` (the ordered list),
    `_active_presets`, `_preset_cached_monitors`, `toggle_preset`,
    `save_presets` and `rebuild_preset_buttons` are what this edits.
    """

    CAM_COLS = 3

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setWindowTitle("Preset Manager")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        ui.paint_window(self)
        _window_icon(self)
        self.resize(860, 580)

        # one entry per camera — a few of them sit in two sections
        self.all_cams = list(dict.fromkeys(
            cam for cams in core.CAM_CATEGORIES.values() for cam in cams))

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ── LEFT: preset list ────────────────────────────────────
        left = QGroupBox("Presets")
        ll = QVBoxLayout(left)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setMinimumWidth(200)
        self.list.currentItemChanged.connect(self._on_select)
        self.list.model().rowsMoved.connect(self._on_reordered)
        ll.addWidget(self.list, 1)
        btns = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.new_btn.clicked.connect(self.on_new)
        btns.addWidget(self.new_btn)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self.on_remove)
        btns.addWidget(self.remove_btn)
        btns.addStretch(1)
        ll.addLayout(btns)
        ll.addWidget(ui.hint_label("Drag a row to reorder"))
        root.addWidget(left)

        # ── RIGHT: name, search, cameras ─────────────────────────
        right = QVBoxLayout()
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit()
        name_row.addWidget(self.name_edit, 1)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.on_save)
        name_row.addWidget(self.save_btn)
        right.addLayout(name_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._regrid_cams)
        search_row.addWidget(self.search_edit, 1)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.search_edit.setText(""))
        search_row.addWidget(clear)
        right.addLayout(search_row)

        self.msg = QLabel("")
        self.msg.setStyleSheet("color: #b00000;")
        right.addWidget(self.msg)
        right.addWidget(QLabel("Cameras:"))

        self.cam_scroll = QScrollArea()
        self.cam_scroll.setWidgetResizable(True)
        self.cam_inner = QWidget()
        self.cam_inner.setAttribute(Qt.WA_StyledBackground, True)
        self.cam_inner.setStyleSheet("background: white;")
        self.cam_grid = QGridLayout(self.cam_inner)
        self.cam_grid.setHorizontalSpacing(14)
        self.cam_grid.setVerticalSpacing(2)
        self.cam_boxes: dict[str, QCheckBox] = {}
        for cam in self.all_cams:
            cb = QCheckBox(cam, self.cam_inner)
            cb.hide()
            self.cam_boxes[cam] = cb
        self.cam_scroll.setWidget(self.cam_inner)
        right.addWidget(self.cam_scroll, 1)

        bottom = QHBoxLayout()
        restore = QPushButton("Restore defaults")
        restore.clicked.connect(self.on_restore)
        bottom.addWidget(restore)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        right.addLayout(bottom)
        root.addLayout(right, 1)

        self._regrid_cams()
        self._refresh_list()

    # ── helpers ──────────────────────────────────────────────────
    def _matching_cams(self) -> list[str]:
        """Every word of the query must appear in the name, in any order."""
        words = self.search_edit.text().strip().lower().split()
        if not words:
            return self.all_cams
        return [c for c in self.all_cams if all(w in c.lower() for w in words)]

    def _regrid_cams(self, *_):
        for cb in self.cam_boxes.values():
            self.cam_grid.removeWidget(cb)
            cb.hide()
        shown = self._matching_cams()
        for i, cam in enumerate(shown):
            r, c = divmod(i, self.CAM_COLS)
            self.cam_grid.addWidget(self.cam_boxes[cam], r, c, Qt.AlignLeft)
            self.cam_boxes[cam].show()
        self.cam_grid.setRowStretch(self.cam_grid.rowCount(), 1)
        self.cam_grid.setColumnStretch(self.CAM_COLS, 1)
        self.cam_scroll.verticalScrollBar().setValue(0)

    def _refresh_list(self, select_name: str | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for nm in self.host._presets:
            QListWidgetItem(nm, self.list)
        self.list.blockSignals(False)
        if select_name:
            for i in range(self.list.count()):
                if self.list.item(i).text() == select_name:
                    self.list.setCurrentRow(i)
                    break

    def _selected_name(self) -> str | None:
        it = self.list.currentItem()
        return it.text() if it is not None else None

    def _load(self, name: str):
        self.msg.setText("")
        self.name_edit.setText(name)
        self.search_edit.setText("")
        cams = set(self.host._presets.get(name, {}).get("cams", []))
        for cam, cb in self.cam_boxes.items():
            cb.setChecked(cam in cams)
        self.remove_btn.setEnabled(True)

    def _after_change(self, select: str | None):
        problem = self.host.save_presets()
        if problem:
            self.msg.setText(f"Could not save the presets: {problem}")
        self.host.rebuild_preset_buttons()
        self._refresh_list(select_name=select)

    # ── actions ──────────────────────────────────────────────────
    def _on_select(self, cur, _prev):
        if cur is not None:
            self._load(cur.text())

    def on_new(self):
        self.msg.setText("")
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.clearSelection()
        self.list.blockSignals(False)
        self.name_edit.setText("")
        self.search_edit.setText("")
        for cb in self.cam_boxes.values():
            cb.setChecked(False)
        self.remove_btn.setEnabled(False)

    def on_save(self):
        h = self.host
        n = self.name_edit.text().strip()
        if not n:
            self.msg.setText("Type a name first.")
            return
        old = self._selected_name()
        if n in h._presets and n != old:
            self.msg.setText(f'A preset named "{n}" already exists.')
            return
        data = {"cams": [c for c in self.all_cams if self.cam_boxes[c].isChecked()],
                "mons": (h._presets.get(old) or {}).get("mons") if old else None}
        if old and old != n:
            # Rename: rebuild the list with the key swapped, so the row keeps its place.
            h._presets = {(n if k == old else k): (data if k == old else v)
                          for k, v in h._presets.items()}
            if old in h._active_presets:
                h._active_presets.discard(old)
                h._active_presets.add(n)
            if old in h._preset_cached_monitors:
                h._preset_cached_monitors[n] = h._preset_cached_monitors.pop(old)
        else:
            h._presets[n] = data
        self._after_change(n)
        self._load(n)

    def on_remove(self):
        h = self.host
        nm = self._selected_name()
        if not nm:
            return
        if not ui.ask_yes_no(self, "Remove preset", f'Remove the preset "{nm}"?'):
            return
        if nm in h._active_presets:
            h.toggle_preset(nm)      # switch it off first, so the monitor counts stay right
        h._presets.pop(nm, None)
        h._preset_cached_monitors.pop(nm, None)
        self._after_change(None)
        self.on_new()

    def on_restore(self):
        if not ui.ask_yes_no(
                self, "Restore defaults",
                "Put the built-in presets back?\n\n"
                "Missing ones are added at the end, edited ones go back to their original "
                "cameras. Your own presets are left alone."):
            return
        for nm, data in core.factory_presets().items():
            self.host._presets[nm] = data
        keep = self._selected_name()
        self._after_change(keep)
        if keep and keep in self.host._presets:
            self._load(keep)
        else:
            self.on_new()

    def _on_reordered(self, *_):
        h = self.host
        order = [self.list.item(i).text() for i in range(self.list.count())]
        if order == list(h._presets) or set(order) != set(h._presets):
            return
        h._presets = {nm: h._presets[nm] for nm in order}
        keep = self._selected_name()
        problem = h.save_presets()
        if problem:
            self.msg.setText(f"Could not save the presets: {problem}")
        h.rebuild_preset_buttons()
        if keep:
            self._load(keep)


# ─────────────────────────────────────────────────────────────────────────────
# Labels
# ─────────────────────────────────────────────────────────────────────────────
class LabelsDialog(QDialog):
    """Per camera: a text (and optional counter) put in front of or behind the
    file name. Session state — kept in the copier until the program closes."""

    HEADERS = ["Camera", "On", "Mode", "Text", "Use idx", "Counter", "Reset"]

    def __init__(self, host, cams: list[str]):
        super().__init__(host)
        self.host = host
        self.copier = host.copier
        self.cams = cams
        self.setWindowTitle("Labels")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        ui.paint_window(self)
        _window_icon(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        if not cams:
            lbl = QLabel("No cameras selected.")
            lbl.setStyleSheet("color: #555555;")
            lbl.setAlignment(Qt.AlignCenter)
            root.addWidget(lbl, 1)
            row = QHBoxLayout()
            row.addStretch(1)
            close = QPushButton("Close")
            close.clicked.connect(self.close)
            row.addWidget(close)
            root.addLayout(row)
            self.resize(500, 140)
            return

        self.table = QTableWidget(len(cams), len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setStyleSheet(
            "QTableWidget { background: white; color: #111111; gridline-color: #d4d4d4;"
            " border: 1px solid #b0b0b0; }"
            "QHeaderView::section { background: #dcdcdc; color: #111111; border: none;"
            " border-right: 1px solid #c4c4c4; border-bottom: 1px solid #b0b0b0;"
            " padding: 4px 6px; font-weight: bold; }")
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.rows: dict[str, dict] = {}
        for r, cam in enumerate(cams):
            s = self.copier.label_for(cam)
            name = QTableWidgetItem(cam)
            name.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, name)
            on = self._centered(QCheckBox())
            on.box.setChecked(bool(s.get("enabled", False)))
            self.table.setCellWidget(r, 1, on)
            mode = QComboBox()
            mode.addItems(["prefix", "suffix"])
            mode.setCurrentText(s.get("mode", "prefix"))
            self.table.setCellWidget(r, 2, mode)
            text = QLineEdit(s.get("text", ""))
            self.table.setCellWidget(r, 3, text)
            use_idx = self._centered(QCheckBox())
            use_idx.box.setChecked(bool(s.get("use_index", False)))
            self.table.setCellWidget(r, 4, use_idx)
            counter = QTableWidgetItem(f"{s.get('index', 1):02d}")
            counter.setFlags(Qt.ItemIsEnabled)
            counter.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 5, counter)
            reset = ui.icon_button("reset", tip="Counter back to 01", tool=True)
            reset.clicked.connect(lambda _=False, c=cam: self._reset(c))
            self.table.setCellWidget(r, 6, self._centered(reset, attr="btn"))
            self.rows[cam] = {"enabled": on.box, "mode": mode, "text": text,
                              "use_index": use_idx.box, "counter": counter}
        self.table.resizeRowsToContents()
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        reset_all = QPushButton("Reset all")
        reset_all.setToolTip("Every counter back to 01")
        reset_all.clicked.connect(self._reset_all)
        btns.addWidget(reset_all)
        btns.addStretch(1)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save_and_close)
        btns.addWidget(save)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        btns.addWidget(close)
        root.addLayout(btns)
        self.resize(620, min(140 + len(cams) * 34 + 60, 720))

    @staticmethod
    def _centered(w: QWidget, attr: str = "box") -> QWidget:
        holder = QWidget()
        holder.setObjectName("cellHolder")
        holder.setAttribute(Qt.WA_StyledBackground, True)
        # Scoped to the holder: a bare "background:" here would reach the
        # tick box inside and wipe out its frame.
        holder.setStyleSheet("QWidget#cellHolder { background: white; }")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        if isinstance(w, QCheckBox):
            # A text-less box measures itself smaller than its styled frame,
            # which then came out clipped to an "L".
            w.setFixedSize(20, 20)
        lay.addWidget(w, 0, Qt.AlignCenter)
        setattr(holder, attr, w)
        return holder

    def _reset(self, cam: str):
        with self.copier._label_lock:
            self.copier.label_settings.setdefault(cam, {})["index"] = 1
        self.rows[cam]["counter"].setText("01")

    def _reset_all(self):
        for cam in self.cams:
            self._reset(cam)

    def _save_and_close(self):
        with self.copier._label_lock:
            for cam in self.cams:
                r = self.rows[cam]
                cur = self.copier.label_settings.get(cam, {})
                self.copier.label_settings[cam] = {
                    "enabled": r["enabled"].isChecked(),
                    "mode": r["mode"].currentText(),
                    "text": r["text"].text(),
                    "use_index": r["use_index"].isChecked(),
                    "index": cur.get("index", 1),
                }
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Detail notes
# ─────────────────────────────────────────────────────────────────────────────
class DetailDialog(QDialog):
    """A .txt note beside the run's pictures."""

    def __init__(self, host, out_dir: Path):
        super().__init__(host)
        self.host = host
        self.out_dir = out_dir
        self.setWindowTitle("Detail notes")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        ui.paint_window(self)
        _window_icon(self)
        self.resize(540, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("File name:"))
        self.name_edit = QLineEdit(host._notes_name)
        self.name_edit.setFixedWidth(200)
        self.name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self.name_edit)
        name_row.addWidget(QLabel(".txt"))
        name_row.addStretch(1)
        root.addLayout(name_row)
        where = QLabel(str(out_dir))
        where.setStyleSheet("color: #555555;")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(where)
        self.text = QPlainTextEdit()
        self.text.setStyleSheet("QPlainTextEdit { background: white; color: #111111;"
                                " font-family: 'Segoe UI'; font-size: 10pt; }")
        root.addWidget(self.text, 1)

        btns = QHBoxLayout()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        btns.addWidget(close)
        btns.addStretch(1)
        delete = QPushButton("Delete file")
        delete.clicked.connect(self._delete)
        btns.addWidget(delete)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        btns.addWidget(save)
        root.addLayout(btns)
        self._load_gen = 0
        self._try_load()
        self.text.setFocus()

    def _dst(self) -> Path:
        fname = core.sanitize_folder_name(self.name_edit.text()) or "notes"
        return self.out_dir / f"{fname}.txt"

    def _on_name_changed(self, text: str):
        self.host._notes_name = text
        self._try_load()

    def _try_load(self):
        """Read the note in the background — the folder may be on the share."""
        dst = self._dst()
        self._load_gen += 1
        gen = self._load_gen
        self.text.document().setModified(False)

        def _show(content):
            import shiboken6
            if not shiboken6.isValid(self) or gen != self._load_gen:
                return
            # Typed into while the share was being read: the typing wins.
            if self.text.document().isModified():
                return
            self.text.setPlainText(content)
            self.text.document().setModified(False)

        def _worker():
            try:
                content = dst.read_text(encoding="utf-8") if dst.exists() else ""
            except Exception:
                return
            ui.on_ui(_show, content)
        threading.Thread(target=_worker, daemon=True).start()

    def _save(self):
        dst = self._dst()
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(self.text.toPlainText(), encoding="utf-8")
            self.host.log(f"[DETAIL] Saved -> {dst.name}")
        except Exception as e:
            ui.message(self, "error", "Save error", str(e))

    def _delete(self):
        dst = self._dst()
        try:
            exists = dst.exists()
        except Exception:
            exists = False
        if not exists:
            ui.message(self, "info", "Delete", "File does not exist.")
            return
        if ui.ask_yes_no(self, "Delete", f"Delete {dst.name}?"):
            try:
                dst.unlink()
                self.text.clear()
                self.host.log(f"[DETAIL] Deleted -> {dst.name}")
            except Exception as e:
                ui.message(self, "error", "Delete error", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Identify: the monitor numbers
# ─────────────────────────────────────────────────────────────────────────────
class _NumberOverlay(QWidget):
    def __init__(self, text: str):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: black;")
        self.setWindowOpacity(0.55)
        lay = QVBoxLayout(self)
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        f = QFont("Segoe UI")
        f.setPixelSize(140)
        f.setBold(True)
        lbl.setFont(f)
        lbl.setStyleSheet("color: white; background: transparent;"
                          " font-family: 'Segoe UI'; font-size: 140px; font-weight: bold;")
        lay.addWidget(lbl)


def identify_monitors(parent, ms: int = core.IDENTIFY_MS) -> int:
    """'M1', 'M2', … across every monitor for `ms` — the same numbers the
    monitor boxes use. Returns how many monitors were found."""
    devices = core.list_monitor_devices()
    screens = {s.name(): s for s in QApplication.screens()}
    overlays = []
    for i, dev in enumerate(devices):
        scr = screens.get(dev)
        if scr is None and i < len(QApplication.screens()):
            scr = QApplication.screens()[i]
        if scr is None:
            continue
        ov = _NumberOverlay(f"M{i + 1}")
        ov.setGeometry(scr.geometry())
        ov.show()
        overlays.append(ov)

    def _close():
        for ov in overlays:
            ov.close()
            ov.deleteLater()
    QTimer.singleShot(ms, _close)
    parent._identify_overlays = overlays   # kept alive until closed
    return len(devices)
