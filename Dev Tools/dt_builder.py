# dt_builder.py
"""Builder tab: pick projects (and their helper exes), build them with PyInstaller,
optionally copy each one straight after its build."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLineEdit, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

import dt_core as core
from dt_common import (
    LogHub, ask_dist_missing, ask_yes_no, guard_wheel, log_box, message, on_ui,
    SEC_DISPLAY, SEC_SOURCE, SEC_TIMELINE, Section, path_row, set_paths_dialog,
)

KIND_ROLE = Qt.UserRole
NAME_ROLE = Qt.UserRole + 1

COL_ORDER, COL_NAME, COL_NEXT = 0, 1, 2

SECTIONS = (("Main project", "Main projects"),
            ("Side project", "Side projects"),
            ("Ignored project", "Ignored projects"))
_GROUP_MIGRATE = {"main": "Main project", "side": "Side project", "ignored": "Ignored project",
                  "Main projects": "Main project", "Side projects": "Side project",
                  "Ignored": "Ignored project"}


class BuilderTab(QWidget):
    def __init__(self, hub: LogHub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.copy_tab = None            # set by the main window

        settings = core.load_json(core.settings_path(), {})
        self.project_groups: dict[str, str] = {
            n: _GROUP_MIGRATE.get(g, g) for n, g in settings.get("project_groups", {}).items()}
        self.root_folder = core.programs_root()
        self.dist_root = core.dist_root()

        self.projects: list[Path] = []
        self.helpers_by_parent: dict[str, list[dict]] = {}
        self._helpers_cache: dict = {}
        self._last_version_cache: dict[str, tuple] = {}
        self.next_override: dict[str, str] = {}

        # Ticks are the user's selection and survive a rebuild of the list.
        self.ticked: set[str] = set()
        # Program names in the order they were ticked: BUILD builds them in this
        # order, each copied straight after its own build.
        self.tick_order: list[str] = []
        self.items: dict[str, QTreeWidgetItem] = {}
        self.section_items: dict[str, QTreeWidgetItem] = {}
        self.expanded: set[str] = set()
        self._updating = False
        self.focused: tuple[str, object] | None = None   # ("project", Path) / ("helper", dict)
        self._building = False

        self._build_ui()
        self.reload_projects(select_first=True)

    # ----------------- UI -----------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        top = Section("Root folder", SEC_SOURCE)
        row, self.root_lbl = path_row(top)
        change = QPushButton("Change")
        change.clicked.connect(self._change_root)
        paths = QPushButton("Set paths")
        paths.clicked.connect(self._open_set_paths)
        row.addWidget(change)
        row.addWidget(paths)
        top.body_layout.addLayout(row)
        outer.addWidget(top)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)

        left = Section("Projects", SEC_TIMELINE)
        ll = left.body_layout
        bar = QHBoxLayout()
        sel_all = QPushButton("Select all")
        sel_all.clicked.connect(self._select_all)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_all)
        self.show_ignored = QCheckBox("Show ignored")
        self.show_ignored.toggled.connect(self._layout_rows)
        bar.addWidget(sel_all)
        bar.addWidget(clear)
        bar.addSpacing(10)
        bar.addWidget(self.show_ignored)
        bar.addStretch(1)
        ll.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["#", "Program", "Next version"])
        # The fold arrow sits beside the name, not in the build-order column.
        self.tree.setTreePosition(COL_NAME)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(18)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setExpandsOnDoubleClick(False)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COL_ORDER, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_NEXT, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(COL_ORDER, 34)
        self.tree.setMinimumWidth(420)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemExpanded.connect(lambda it: self.expanded.add(it.data(0, NAME_ROLE)))
        self.tree.itemCollapsed.connect(lambda it: self.expanded.discard(it.data(0, NAME_ROLE)))
        ll.addWidget(self.tree, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        panel = Section("Selected program", SEC_DISPLAY)
        form = QFormLayout()
        panel.body_layout.addLayout(form)
        self.name_edit = QLineEdit()
        self.main_edit = QLineEdit()
        self.last_edit = QLineEdit()
        for e in (self.name_edit, self.main_edit, self.last_edit):
            e.setReadOnly(True)
        self.next_edit = QLineEdit()
        self.next_edit.setMaximumWidth(140)
        self.last_edit.setMaximumWidth(140)
        self.next_edit.textEdited.connect(self._on_next_edited)
        self.group_combo = QComboBox()
        self.group_combo.addItems([g for g, _ in SECTIONS])
        self.group_combo.setMaximumWidth(160)
        guard_wheel(self.group_combo)
        self.group_combo.activated.connect(self._on_group_changed)
        self.readme_btn = QPushButton("Open ReadMe")
        self.readme_btn.clicked.connect(self._open_focused_readme)
        form.addRow("Name:", self.name_edit)
        form.addRow("Main script:", self.main_edit)
        form.addRow("Last version:", self.last_edit)
        form.addRow("Next version:", self.next_edit)
        form.addRow("Group:", self.group_combo)
        readme_row = QHBoxLayout()
        readme_row.addWidget(self.readme_btn)
        readme_row.addStretch(1)
        form.addRow("", readme_row)
        rl.addWidget(panel)

        actions = QHBoxLayout()
        self.btn_build = QPushButton("BUILD selected")
        self.btn_build.setObjectName("primary")
        self.btn_build.clicked.connect(self._build_selected)
        open_dist = QPushButton("Open dist folder")
        open_dist.clicked.connect(self._open_dist)
        self.copy_after = QCheckBox("Copy after build")
        self.copy_after.setChecked(True)
        # Remembered between runs: a differing icon.ico on a destination is
        # overwritten without the Icon conflict dialog.
        self.replace_icons = QCheckBox("Replace icons without asking")
        self.replace_icons.setChecked(bool(core.load_json(core.settings_path(), {})
                                           .get("replace_icons_without_asking", False)))
        self.replace_icons.toggled.connect(self._save_replace_icons)
        actions.addWidget(self.btn_build)
        actions.addWidget(open_dist)
        actions.addSpacing(16)
        actions.addWidget(self.copy_after)
        actions.addWidget(self.replace_icons)
        actions.addStretch(1)
        rl.addLayout(actions)
        rl.addWidget(log_box(hub=self.hub), 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([480, 900])

    def _log(self, msg: str, force_scroll: bool = False):
        self.hub.write(msg, force_scroll)

    def _save_settings(self, **changes):
        settings = core.load_json(core.settings_path(), {})
        settings.update(changes)
        try:
            core.save_json(core.settings_path(), settings)
        except OSError as e:
            self._log(f"Could not save builder settings: {e}")

    def _save_replace_icons(self, on: bool):
        self._save_settings(replace_icons_without_asking=bool(on))

    # ----------------- VERSIONS -----------------
    def last_version(self, name: str) -> tuple[str | None, bool]:
        cached = self._last_version_cache.get(name)
        if cached is None:
            cached = core.last_built_version(name, self.dist_root)
            self._last_version_cache[name] = cached
        return cached

    def default_next(self, name: str) -> str:
        last, is_new = self.last_version(name)
        return "1.0.0" if is_new else core.bump_patch(last)

    def effective_next(self, name: str) -> str:
        return self.next_override.get(name) or self.default_next(name)

    def invalidate_version_cache(self):
        self._last_version_cache.clear()
        core.invalidate_versions_cache()

    def helpers_of(self, name: str) -> list[dict]:
        return self.helpers_by_parent.get(name, [])

    # ----------------- LIST -----------------
    def reload_projects(self, select_first: bool = False):
        self.invalidate_version_cache()
        self.root_lbl.setText(str(self.root_folder))
        self.projects = core.find_projects(self.root_folder)
        self.helpers_by_parent = core.find_helpers(self.projects, self._helpers_cache)
        self._render()
        if not self.projects:
            self._show_focus(None)
            message(self, "info", "No projects found",
                    f"No project folders with a usable .py found in:\n{self.root_folder}")
            return
        if select_first:
            first = next((self.items[p.name] for p in self.projects
                          if not self.items[p.name].isHidden()), None)
            if first is not None:
                self.tree.setCurrentItem(first)

    def _make_item(self, kind: str, name: str, label: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem()
        it.setData(0, KIND_ROLE, kind)
        it.setData(0, NAME_ROLE, name)
        it.setText(COL_NAME, label)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        it.setCheckState(COL_NAME, Qt.Checked if name in self.ticked else Qt.Unchecked)
        it.setTextAlignment(COL_ORDER, Qt.AlignRight | Qt.AlignVCenter)
        it.setText(COL_NEXT, self.effective_next(name))
        return it

    def _render(self):
        """Build every row (Ignored ones too); _layout_rows only places them."""
        self._updating = True
        try:
            live = {p.name for p in self.projects}
            for p in self.projects:
                live.update(h["name"] for h in self.helpers_of(p.name))
            self.ticked &= live
            self.tick_order = [n for n in self.tick_order if n in self.ticked]

            self.tree.clear()
            self.items.clear()
            self.section_items.clear()
            # Section rows in Image Tools' group-label look: small, grey, bold capitals.
            cap = QFont(self.tree.font())
            cap.setBold(True)
            cap.setPointSizeF(max(7.0, cap.pointSizeF() - 1))
            cap.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
            for grp, title in SECTIONS:
                sec = QTreeWidgetItem()
                sec.setData(0, KIND_ROLE, "section")
                sec.setData(0, NAME_ROLE, grp)
                sec.setText(COL_NAME, title.upper())
                sec.setFont(COL_NAME, cap)
                sec.setForeground(COL_NAME, QBrush(QColor("#555555")))
                sec.setFlags(Qt.ItemIsEnabled)
                for c in range(3):
                    sec.setBackground(c, QBrush(QColor("#eef1f5")))
                self.section_items[grp] = sec
            for p in self.projects:
                it = self._make_item("project", p.name, p.name)
                self.items[p.name] = it
                for h in self.helpers_of(p.name):
                    hit = self._make_item("helper", h["name"], h["name"])
                    hit.setForeground(COL_NAME, QBrush(QColor("#333333")))
                    it.addChild(hit)
                    self.items[h["name"]] = hit
        finally:
            self._updating = False
        self._layout_rows()
        self._paint_order()

    def _group_of(self, name: str) -> str:
        g = self.project_groups.get(name, "Main project")
        return g if g in dict(SECTIONS) else "Main project"

    def _layout_rows(self):
        """Put the rows under their section headers — nothing is rebuilt.

        The tree's signals are off meanwhile: while the rows are out, Qt hands
        the current row to whichever neighbour is left, and that used to count
        as a click on it — choosing a Group unfolded Diagnostic and showed its
        listener. The current row is put back before the signals come on, so
        nothing reacts to the shuffle at all.
        """
        current = self.tree.currentItem()
        self._updating = True
        self.tree.blockSignals(True)
        try:
            while self.tree.topLevelItemCount():
                self.tree.takeTopLevelItem(0)
            show_ign = self.show_ignored.isChecked()
            visible: set[str] = set()
            for grp, _title in SECTIONS:
                members = [p for p in self.projects if self._group_of(p.name) == grp]
                shown = bool(members) and (grp != "Ignored project" or show_ign)
                if not shown:
                    continue
                self.tree.addTopLevelItem(self.section_items[grp])
                for p in members:
                    it = self.items[p.name]
                    self.tree.addTopLevelItem(it)
                    it.setExpanded(p.name in self.expanded)
                    visible.add(p.name)
                    visible.update(h["name"] for h in self.helpers_of(p.name))
            # What builds has to be what you see: a hidden program loses its tick.
            dropped = [n for n in self.ticked if n not in visible]
            for n in dropped:
                self.ticked.discard(n)
                self.items[n].setCheckState(COL_NAME, Qt.Unchecked)
            if current is not None and current.treeWidget() is self.tree:
                self.tree.setCurrentItem(current)
        finally:
            self.tree.blockSignals(False)
            self._updating = False
        # Signals were off, so the view has not repainted the moved rows yet.
        self.tree.viewport().update()
        if dropped:
            self.tick_order = [n for n in self.tick_order if n in self.ticked]
            self._paint_order()
        if current is not None and current.treeWidget() is self.tree:
            self.tree.scrollToItem(current)

    def _paint_order(self):
        for name, it in self.items.items():
            pos = self.tick_order.index(name) + 1 if name in self.tick_order else 0
            text = f"{pos}." if pos else ""
            if it.text(COL_ORDER) != text:
                it.setText(COL_ORDER, text)

    def _refresh_version_labels(self, only: str | None = None):
        for name, it in self.items.items():
            if only is None or name == only:
                text = self.effective_next(name)
                if it.text(COL_NEXT) != text:
                    it.setText(COL_NEXT, text)

    # ----------------- TICKS -----------------
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._updating or column != COL_NAME:
            return
        name = item.data(0, NAME_ROLE)
        kind = item.data(0, KIND_ROLE)
        on = item.checkState(COL_NAME) == Qt.Checked
        if on == (name in self.ticked):
            return
        self._set_tick(name, on)
        if kind == "project":
            # Ticking a program takes its helpers with it — a one-way push, not a
            # lock: untick a helper afterwards and it stays unticked.
            helpers = self.helpers_of(name)
            self._updating = True
            try:
                for h in helpers:
                    self._set_tick(h["name"], on)
                    self.items[h["name"]].setCheckState(COL_NAME, Qt.Checked if on else Qt.Unchecked)
            finally:
                self._updating = False
            if helpers and on:
                item.setExpanded(True)
        self._paint_order()
        self.tree.setCurrentItem(item)

    def _set_tick(self, name: str, on: bool):
        if on:
            self.ticked.add(name)
            if name not in self.tick_order:
                self.tick_order.append(name)
        else:
            self.ticked.discard(name)
            if name in self.tick_order:
                self.tick_order.remove(name)

    def _visible_names(self) -> list[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, KIND_ROLE) == "project":
                out.append(it.data(0, NAME_ROLE))
                out += [h["name"] for h in self.helpers_of(it.data(0, NAME_ROLE))]
        return out

    def _select_all(self):
        # What was already ticked keeps its place; the rest queue up behind it.
        self._updating = True
        try:
            for n in self._visible_names():
                self._set_tick(n, True)
                self.items[n].setCheckState(COL_NAME, Qt.Checked)
        finally:
            self._updating = False
        self._paint_order()

    def _clear_all(self):
        self._updating = True
        try:
            for n in list(self.ticked):
                self.items[n].setCheckState(COL_NAME, Qt.Unchecked)
            self.ticked.clear()
            self.tick_order.clear()
        finally:
            self._updating = False
        self._paint_order()

    def build_queue(self) -> list[tuple[str, object]]:
        """Ticked programs as ("project", Path) / ("helper", dict), in tick order."""
        by_name: dict[str, tuple[str, object]] = {}
        for p in self.projects:
            if p.name in self.ticked:
                by_name[p.name] = ("project", p)
            for h in self.helpers_of(p.name):
                if h["name"] in self.ticked:
                    by_name[h["name"]] = ("helper", h)
        order = [n for n in self.tick_order if n in by_name]
        order += [n for n in by_name if n not in order]
        return [by_name[n] for n in order]

    # ----------------- FOCUS PANEL -----------------
    def _on_current_changed(self, cur: QTreeWidgetItem, _prev):
        # Rows moved by code (rebuild, re-layout) are not a click.
        if cur is None or self._updating:
            return
        kind = cur.data(0, KIND_ROLE)
        name = cur.data(0, NAME_ROLE)
        if kind == "project":
            p = next((p for p in self.projects if p.name == name), None)
            self._show_focus(("project", p) if p else None)
            if self.helpers_of(name):
                cur.setExpanded(True)
        elif kind == "helper":
            parent = cur.parent().data(0, NAME_ROLE)
            h = next((h for h in self.helpers_of(parent) if h["name"] == name), None)
            self._show_focus(("helper", h) if h else None)

    def _focused_name(self) -> str | None:
        if self.focused is None:
            return None
        kind, obj = self.focused
        return obj.name if kind == "project" else obj["name"]

    def _show_focus(self, focus):
        self.focused = focus
        if focus is None:
            for e in (self.name_edit, self.main_edit, self.last_edit, self.next_edit):
                e.clear()
            self.group_combo.setEnabled(False)
            self.readme_btn.setEnabled(False)
            return
        kind, obj = focus
        name = self._focused_name()
        last, is_new = self.last_version(name)
        self.name_edit.setText(name)
        if kind == "project":
            main = core.guess_main_py(obj)
            self.main_edit.setText(main.name if main else "")
            self.group_combo.setEnabled(True)
            self.group_combo.setCurrentText(self._group_of(name))
        else:
            # A helper follows its parent's group; its version is its own.
            self.main_edit.setText(obj["script"].name)
            self.group_combo.setEnabled(False)
            self.group_combo.setCurrentIndex(-1)
        self.last_edit.setText("— NEW —" if is_new else last)
        self.next_edit.setText(self.effective_next(name))
        self.readme_btn.setEnabled(True)

    def _on_next_edited(self, text: str):
        name = self._focused_name()
        if name is None:
            return
        val = text.strip()
        if not val or val == self.default_next(name):
            self.next_override.pop(name, None)
        else:
            self.next_override[name] = val
        self._refresh_version_labels(only=name)

    def _on_group_changed(self, _index: int):
        if self.focused is None or self.focused[0] != "project":
            return
        name = self.focused[1].name
        grp = self.group_combo.currentText()
        if grp == "Main project":
            self.project_groups.pop(name, None)
        else:
            self.project_groups[name] = grp
        self._save_settings(project_groups=self.project_groups)
        self._layout_rows()

    def _open_focused_readme(self):
        if self.focused is None:
            return
        kind, obj = self.focused
        if kind == "project":
            self._open_readme(obj, obj.name, allow_generic=True)
        else:
            # A helper's documentation lives in its parent's folder, named after
            # the helper — that is the name the Launcher looks for.
            self._open_readme(obj["parent"], obj["name"], allow_generic=False)

    def _open_readme(self, folder: Path, name: str, allow_generic: bool):
        norm = lambda s: s.lower().replace("_", "").replace(" ", "")
        target = norm(f"ReadMe_{name}")
        readme = next((f for f in folder.iterdir() if f.is_file() and norm(f.stem) == target), None)
        if readme is None and allow_generic:
            readme = next((folder / c for c in ("ReadMe.txt", "README.md", "README.txt", "ReadMe.md")
                           if (folder / c).exists()), None)
        if readme is None:
            if not ask_yes_no(self, "ReadMe not found",
                              f"No ReadMe for {name} in:\n{folder}\n\nCreate ReadMe_{name}.txt?"):
                return
            readme = folder / f"ReadMe_{name}.txt"
            readme.write_text("", encoding="utf-8-sig")
        os.startfile(str(readme))

    # ----------------- ROOT / PATHS -----------------
    def _change_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select programs folder", str(self.root_folder))
        if not folder:
            return
        cfg = core.load_config()
        cfg["root_folder"] = str(Path(folder))
        core.save_config(cfg)
        self.apply_paths()
        if self.copy_tab is not None:
            self.copy_tab.apply_paths()

    def _open_set_paths(self):
        if set_paths_dialog(self):
            self._log(f"Paths saved to {core.CONFIG_PATH}")
            self.apply_paths()
            if self.copy_tab is not None:
                self.copy_tab.apply_paths()

    def apply_paths(self):
        self.root_folder = core.programs_root()
        self.dist_root = core.dist_root()
        self.reload_projects(select_first=True)

    def _open_dist(self):
        self.dist_root.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.dist_root))

    # ----------------- BUILD -----------------
    def _ensure_dist_root(self) -> bool:
        """Asked here, on the UI thread: the build thread must not open dialogs."""
        if self.dist_root.exists():
            return True
        ans = ask_dist_missing(self, self.dist_root)
        if ans == "cancel":
            return False
        if ans == "choose":
            chosen = QFileDialog.getExistingDirectory(self, "Select output (dist) folder",
                                                      str(self.dist_root.parent))
            if not chosen:
                return False
            self.dist_root = Path(chosen)
            return True
        try:
            self.dist_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            message(self, "error", "dist folder", f"Could not create:\n{self.dist_root}\n\n{e}")
            return False
        return True

    def _build_selected(self):
        if self._building:
            return
        queue = self.build_queue()
        if not queue:
            message(self, "info", "No selection", "Select at least one project.")
            return
        names = [obj.name if kind == "project" else obj["name"] for kind, obj in queue]
        versions = {n: self.effective_next(n).strip() for n in names}
        invalid = [f"{n}: {v or '(empty)'}" for n, v in versions.items()
                   if not core.parse_version_tuple(v)]
        if invalid:
            message(self, "error", "Invalid version",
                    "These programs have an invalid next version:\n\n" + "\n".join(invalid))
            return
        if not self._ensure_dist_root():
            return

        # Destinations are read once, here, on the UI thread.
        cm = self.copy_tab
        copy_each = bool(self.copy_after.isChecked() and cm is not None)
        copy_roots = cm.selected_destination_roots() if copy_each else []
        if copy_each:
            cm.set_busy(True)
        self._building = True
        self.btn_build.setEnabled(False)
        self._log("Build order: " + ", ".join(f"{i}. {n}" for i, n in enumerate(names, 1)))
        dist, root = self.dist_root, self.root_folder
        total = len(queue)

        workers = core.parallel_builds(total)
        if workers > 1:
            self._log(f"{workers} builds run at once")
        # Copy after build opens the icon dialog and records what is deployed:
        # one copy at a time, while the other builds go on.
        copy_lock = threading.Lock()

        def build_one(i, kind, obj, name):
            ver = versions[name]
            # Lines of builds running side by side interleave: each says whose it is.
            log = (lambda line, n=name: self._log(f"[{n}] {line}")) if workers > 1 else self._log
            self._log(f"[{i}/{total}] {'Building' if kind == 'project' else 'Building helper'}: "
                      f"{name}  ->  v{ver}")
            clock = core.PhaseClock()
            t0 = time.perf_counter()
            try:
                if kind == "helper":
                    ok, msg = core.build_one_helper(obj, ver, dist, log, clock)
                elif name.lower() == "internal builder":
                    ok, msg = core.build_internal_builder(root, log, clock)
                else:
                    ok, msg = core.build_one_project(obj, ver, dist, root, log, clock)
            except Exception as e:
                ok, msg = False, f"ERROR: {e}"
            phases = clock.stop()
            elapsed = time.perf_counter() - t0
            if not ok:
                self._log(f"[{i}/{total}] FAILED: {name}\n{msg}")
                return {"ok": False, "name": name, "msg": msg, "elapsed": elapsed}
            split = f": {clock.short()}" if clock.short() else ""
            self._log(f"[{i}/{total}] Built: {name}  v{ver}  ({elapsed:.1f}s{split})")
            res = {"ok": True, "name": name, "ver": f"v{ver}", "elapsed": elapsed,
                   "split": clock.short(), "out_dir": core.as_out_dir(msg),
                   "copy": None, "copy_s": None}
            if copy_each and copy_roots:
                with copy_lock:
                    self._log(f"[{i}/{total}] Copying: {name}  v{ver}")
                    t1 = time.perf_counter()
                    res["copy"] = cm.copy_one_after_build(name, f"v{ver}", copy_roots)
                    res["copy_s"] = time.perf_counter() - t1
            core.record_build_times(name, f"v{ver}", elapsed, phases, res["copy_s"])
            return res

        def worker():
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(build_one, i, kind, obj, name)
                           for i, ((kind, obj), name) in enumerate(zip(queue, names), 1)]
                results = []
                for f in futures:
                    try:
                        results.append(f.result())
                    except Exception as e:
                        results.append({"ok": False, "name": "?", "msg": f"ERROR: {e}",
                                        "elapsed": 0.0})
            build_time = time.perf_counter() - t0
            ok_count = sum(1 for r in results if r["ok"])
            fail_count = len(results) - ok_count
            failed = [f"{r['name']}:\n{r['msg']}" for r in results if not r["ok"]]
            built_paths = [(r["name"], r["ver"], r["elapsed"], r["out_dir"], r["split"])
                           for r in results if r["ok"]]
            copy_results = [r["copy"] for r in results if r["ok"] and r["copy"] is not None]
            copy_time = sum(r["copy_s"] for r in results if r["ok"] and r["copy_s"] is not None)
            on_ui(self._on_build_finished, ok_count, fail_count, failed, built_paths,
                  build_time, copy_each, copy_roots, copy_results, copy_time)

        threading.Thread(target=worker, daemon=True).start()

    def _on_build_finished(self, ok_count, fail_count, failed, built_paths, elapsed,
                           copy_each, copy_roots, copy_results, copy_time):
        self._building = False
        self.btn_build.setEnabled(True)
        cm = self.copy_tab
        if copy_each and cm is not None:
            cm.set_busy(False)

        self.invalidate_version_cache()
        self.next_override.clear()
        old = ([p.name for p in self.projects],
               {k: [h["name"] for h in v] for k, v in self.helpers_by_parent.items()})
        self.projects = core.find_projects(self.root_folder)
        self.helpers_by_parent = core.find_helpers(self.projects, self._helpers_cache)
        new = ([p.name for p in self.projects],
               {k: [h["name"] for h in v] for k, v in self.helpers_by_parent.items()})
        if new != old:
            self._render()
        else:
            # Only the version numbers moved: no rebuild, the scroll stays put.
            self._refresh_version_labels()
        self._on_current_changed(self.tree.currentItem(), None)
        if cm is not None:
            cm.update_rows_in_place()

        summary = core.build_summary(ok_count, fail_count, failed, built_paths, elapsed)
        build_info = {"ok": ok_count, "fail": fail_count, "elapsed": elapsed,
                      "items": [{"name": n, "version": v, "seconds": t, "out_dir": o}
                                for n, v, t, o, *_ in built_paths],
                      "failed": [f.splitlines()[0] for f in failed]}
        if copy_each and built_paths and copy_roots:
            cm.copy_after_build_report(copy_results, copy_roots, copy_time, summary, build_info)
        else:
            self._log(summary, force_scroll=True)
            if copy_each and built_paths:
                self._log("Copy after build: no destination is ticked in "
                          "Copy Manager — nothing was copied.", force_scroll=True)
        if fail_count:
            message(self, "warning", "Build finished with errors",
                    "\n".join([f"Failed: {fail_count}"] + [f.splitlines()[0] for f in failed[:10]]))
