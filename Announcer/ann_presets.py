"""Announcer — the presets: named sets of alarms, and the boxes that edit them.

A preset is a set of alarms to work in. One is chosen in the header and then it
is the only thing listed and the only thing watched, so a night shift can be set
up without the commissioning alarms in the way. An alarm may be in several sets;
one in none of them shows under the automatic set "Unassigned".

The membership is NOT a table column, deliberately. It was one, under the name
"Group", and a column is where it went unread: the point of a preset is to hide
the other alarms, which a column cannot do. It is edited here instead — in the
alarm's own editor, or with Assign to preset on however many rows are selected.

Everything in this file states both its colours. This PC runs Windows in dark
mode, so a list left to the theme comes up with black text on a black ground.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QVBoxLayout,
                               QWidget)

import ann_core as C
import ann_ui as U

# The tick box of a list row is NOT a QCheckBox and takes none of CHK_QSS, so
# it is styled here as well. Left to the theme it comes out as a white outline
# on a white row — an empty space where the tick is supposed to be, which is
# exactly how the first render of this box looked.
LIST_QSS = (
    "QListWidget { background: #ffffff; color: #16202c;"
    " border: 1px solid #b6b6b6; border-radius: 3px; font-size: 12px;"
    " selection-background-color: #cfe0f7; selection-color: #10243c; }"
    "QListWidget::item { padding: 3px 4px; }"
    "QListWidget::indicator { width: 16px; height: 16px; margin-right: 4px;"
    " border: 2px solid #4a4a4a; border-radius: 3px; background: #ffffff; }"
    "QListWidget::indicator:hover { border-color: #2d7dff; background: #f4f8ff; }"
    "QListWidget::indicator:checked { border-color: #1f5fc4; background: #2d7dff; }"
    "QListWidget::indicator:indeterminate { border-color: #1f5fc4;"
    " background: #9dc2ff; }"
) + U.SCROLLBAR_QSS


def preset_word(name):
    """A preset name as it is written on screen."""
    if name == C.PRESET_ALL:
        return "All alarms"
    if name == C.PRESET_UNASSIGNED:
        return "Unassigned"
    return name


def fill_preset_combo(combo, cfg, chosen):
    """Load a drop-down with All · every preset · Unassigned, and pick one.

    The real name travels as the item's data, never as its text: "All alarms"
    and "Unassigned" are captions, and a preset the operator called "Unassigned"
    would otherwise be indistinguishable from the automatic one.
    """
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(preset_word(C.PRESET_ALL), C.PRESET_ALL)
    for name in C.alarm_presets(cfg):
        combo.addItem(name, name)
    combo.addItem(preset_word(C.PRESET_UNASSIGNED), C.PRESET_UNASSIGNED)
    index = combo.findData(chosen)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)
    return combo.currentData()


class PresetBox(QWidget):
    """The tick boxes that say which presets one alarm belongs to.

    Sits in the alarm's editor where the Group box used to be. Nothing ticked
    means the alarm is Unassigned, which is a set like any other, not an error.
    """

    def __init__(self, parent, cfg, chosen=()):
        super().__init__(parent)
        self._cfg = cfg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._list = QListWidget()
        self._list.setStyleSheet(LIST_QSS)
        self._list.setMaximumHeight(108)
        self._list.setToolTip(
            "The sets this alarm belongs to. Pick a set in the header and only "
            "its alarms are listed and watched.\nTick none and it belongs to "
            "Unassigned.")
        lay.addWidget(self._list)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        new = U.button("New preset", icon_name="plus",
                       tip="Make a new set and tick this alarm into it.")
        new.clicked.connect(self._new)
        row.addWidget(new)
        row.addStretch(1)
        lay.addLayout(row)

        self._reload(clean_chosen(chosen))

    def _reload(self, chosen):
        self._list.clear()
        names = C.alarm_presets(self._cfg)
        if not names:
            note = QListWidgetItem("No preset yet — press New preset")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            note.setForeground(_ink(U.QUIET))
            self._list.addItem(note)
            self._pending = list(chosen)
            return
        for name in names:
            row = QListWidgetItem(name)
            row.setFlags(Qt.ItemFlag.ItemIsEnabled
                         | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if name in chosen
                              else Qt.CheckState.Unchecked)
            row.setForeground(_ink(U.INK))
            self._list.addItem(row)
        self._pending = []

    def _new(self):
        name, ok = QInputDialog.getText(self, "New preset", "Name of the set:")
        if not ok:
            return
        problem = C.add_alarm_preset(self._cfg, name)
        if problem:
            QMessageBox.warning(self, "Preset", problem)
            return
        self._reload(self.chosen() + [str(name).strip()])

    def chosen(self):
        out = list(self._pending)
        for i in range(self._list.count()):
            row = self._list.item(i)
            if row.flags() & Qt.ItemFlag.ItemIsUserCheckable \
                    and row.checkState() == Qt.CheckState.Checked:
                out.append(row.text())
        return C.clean_presets(out)


class AssignDialog(QDialog):
    """Put the rows that are selected into presets, in one go.

    Several rows may be selected, so several rows are changed — a box that
    quietly acted on one of them would be worse than no box. A set that only
    some of the selected alarms are in starts partly ticked and stays that way
    unless it is clicked.
    """

    def __init__(self, parent, cfg, items):
        super().__init__(parent)
        self._cfg = cfg
        self._items = items
        self.setWindowTitle("Assign to preset")
        self.setModal(True)
        U.paint_dialog(self)
        self.resize(360, 340)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"{len(items)} alarm{'' if len(items) == 1 else 's'} selected:\n"
            + U.name_list(items)))
        self._list = QListWidget()
        self._list.setStyleSheet(LIST_QSS)
        lay.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        new = U.button("New preset", icon_name="plus",
                       tip="Make a new set and tick these alarms into it.")
        new.clicked.connect(self._new)
        row.addWidget(new)
        row.addStretch(1)
        lay.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Apply")
        ok.setStyleSheet(U.PRIMARY_QSS)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(U.BTN_QSS)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._reload()

    def _reload(self):
        self._list.clear()
        names = C.alarm_presets(self._cfg)
        if not names:
            note = QListWidgetItem("No preset yet — press New preset")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            note.setForeground(_ink(U.QUIET))
            self._list.addItem(note)
            return
        for name in names:
            holding = sum(1 for it in self._items
                          if name in (it.get("presets") or []))
            row = QListWidgetItem(
                name if holding in (0, len(self._items))
                else f"{name}   (in {holding} of {len(self._items)})")
            row.setData(Qt.ItemDataRole.UserRole, name)
            row.setFlags(Qt.ItemFlag.ItemIsEnabled
                         | Qt.ItemFlag.ItemIsUserCheckable)
            if holding == 0:
                row.setCheckState(Qt.CheckState.Unchecked)
            elif holding == len(self._items):
                row.setCheckState(Qt.CheckState.Checked)
            else:
                row.setCheckState(Qt.CheckState.PartiallyChecked)
            row.setForeground(_ink(U.INK))
            self._list.addItem(row)

    def _new(self):
        name, ok = QInputDialog.getText(self, "New preset", "Name of the set:")
        if not ok:
            return
        problem = C.add_alarm_preset(self._cfg, name)
        if problem:
            QMessageBox.warning(self, "Preset", problem)
            return
        self._reload()
        name = str(name).strip()
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self._list.item(i).setCheckState(Qt.CheckState.Checked)

    def apply(self):
        """Write the ticks onto the items. A half-tick is left alone."""
        for i in range(self._list.count()):
            row = self._list.item(i)
            name = row.data(Qt.ItemDataRole.UserRole)
            if not name:
                continue
            state = row.checkState()
            if state == Qt.CheckState.PartiallyChecked:
                continue
            for it in self._items:
                names = list(it.get("presets") or [])
                if state == Qt.CheckState.Checked:
                    names.append(name)
                else:
                    names = [n for n in names if n != name]
                it["presets"] = C.clean_presets(names)


class ManageDialog(QDialog):
    """Make, rename and forget presets, with what each one holds."""

    def __init__(self, parent, cfg, items):
        super().__init__(parent)
        self._cfg = cfg
        self._items = items
        self.setWindowTitle("Presets")
        self.setModal(True)
        U.paint_dialog(self)
        self.resize(380, 360)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "A preset is a set of alarms to work in. Pick one in the header "
            "and\nonly its alarms are listed and watched."))
        self._list = QListWidget()
        self._list.setStyleSheet(LIST_QSS)
        self._list.itemSelectionChanged.connect(self._sync)
        lay.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        self._btn_new = U.button("New preset", icon_name="plus",
                                 tip="Make a new, empty set.")
        self._btn_new.clicked.connect(self._new)
        self._btn_rename = U.button("Rename", icon_name="pencil",
                                    tip="Rename the set. Its alarms come with "
                                        "it.")
        self._btn_rename.clicked.connect(self._rename)
        self._btn_delete = U.button("Delete", icon_name="trash", danger=True,
                                    tip="Forget the set. Its alarms are kept "
                                        "and become Unassigned.")
        self._btn_delete.clicked.connect(self._delete)
        for b in (self._btn_new, self._btn_rename, self._btn_delete):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = btns.button(QDialogButtonBox.StandardButton.Close)
        close.setStyleSheet(U.BTN_QSS)
        btns.rejected.connect(self.accept)
        lay.addWidget(btns)

        self.changed = False
        self._reload()

    def _reload(self, keep=None):
        self._list.clear()
        counts, loose = C.preset_counts(self._items, self._cfg)
        for name in C.alarm_presets(self._cfg):
            n = counts.get(name, 0)
            row = QListWidgetItem(f"{name}   —   {n} alarm{'' if n == 1 else 's'}")
            row.setData(Qt.ItemDataRole.UserRole, name)
            row.setForeground(_ink(U.INK))
            self._list.addItem(row)
            if name == keep:
                self._list.setCurrentItem(row)
        row = QListWidgetItem(
            f"Unassigned   —   {loose} alarm{'' if loose == 1 else 's'}")
        row.setFlags(Qt.ItemFlag.ItemIsEnabled)
        row.setForeground(_ink(U.QUIET))
        row.setToolTip("Every alarm that is in no preset. It is made by the "
                       "program and cannot be renamed or deleted.")
        self._list.addItem(row)
        # Land on a real preset, so Rename and Delete are live and the row with
        # the focus frame round it is the row they would act on.
        if self._chosen() is None and self._list.count() > 1:
            self._list.setCurrentRow(0)
        self._sync()

    def _chosen(self):
        row = self._list.currentItem()
        return row.data(Qt.ItemDataRole.UserRole) if row is not None else None

    def _sync(self):
        has = self._chosen() is not None
        self._btn_rename.setEnabled(has)
        self._btn_delete.setEnabled(has)

    def _new(self):
        name, ok = QInputDialog.getText(self, "New preset", "Name of the set:")
        if not ok:
            return
        problem = C.add_alarm_preset(self._cfg, name)
        if problem:
            QMessageBox.warning(self, "Preset", problem)
            return
        self.changed = True
        self._reload(keep=str(name).strip())

    def _rename(self):
        old = self._chosen()
        if old is None:
            return
        name, ok = QInputDialog.getText(self, "Rename preset",
                                        "New name:", text=old)
        if not ok:
            return
        problem = C.rename_alarm_preset(self._cfg, self._items, old, name)
        if problem:
            QMessageBox.warning(self, "Preset", problem)
            return
        self.changed = True
        self._reload(keep=str(name).strip())

    def _delete(self):
        name = self._chosen()
        if name is None:
            return
        counts, _loose = C.preset_counts(self._items, self._cfg)
        n = counts.get(name, 0)
        answer = QMessageBox.question(
            self, "Delete preset",
            f'Forget the preset "{name}"?\n\n'
            + (f"Its {n} alarm{'' if n == 1 else 's'} "
               f"{'is' if n == 1 else 'are'} kept and become"
               f"{'s' if n == 1 else ''} Unassigned."
               if n else "It holds no alarms."))
        if answer != QMessageBox.StandardButton.Yes:
            return
        C.delete_alarm_preset(self._cfg, self._items, name)
        self.changed = True
        self._reload()


def clean_chosen(chosen):
    return C.clean_presets(list(chosen or []))


def _ink(colour):
    """A QColor made where it is used — never kept in a module global."""
    from PySide6.QtGui import QColor
    return QColor(colour)
