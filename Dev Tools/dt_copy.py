# dt_copy.py
"""Copy Manager tab: copy built versions from dist to the destinations
(Scratch, SharePoint), keep their archives tidy, ship the shared libraries."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import dt_core as core
from dt_common import (
    LogHub, ask_on_ui, guard_wheel, icon_compare_dialog, log_box, message, on_ui,
    SEC_SAVE, SEC_SOURCE, SEC_TIMELINE, Section, path_row, set_paths_dialog, wrap_path,
)

COL_NAME, COL_VERSION, COL_STATUS, COL_DEPLOYED = 0, 1, 2, 3
_STATUS_COLOURS = {"gray": "#7a7a7a", "green": "#1b7f2a", "red": "#cc0000"}


def _status_of(has_versions: bool, is_new: bool, deployed: str) -> tuple[str, str]:
    if not has_versions:
        return "NEW", "gray"
    if is_new:
        return "NEW", "green"
    if deployed:
        return "UTD", "red"
    return "", "gray"


class CopyTab(QWidget):
    def __init__(self, hub: LogHub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.builder = None             # set by the main window
        self.state_deployed = core.load_state()
        # name -> {"item", "combo", "key" (source folder or programs_root/name),
        #          "is_new", "latest"}
        self.rows: dict[str, dict] = {}
        self.helper_names_by_parent: dict[str, list[str]] = {}
        self.helper_parent_dir: dict[str, Path] = {}
        # Folded/unfolded survives a Refresh.
        self.expanded: set[str] = set()
        self.dest_checks: dict[str, QCheckBox] = {}
        self._updating = False
        self._busy = False
        self._build_ui()
        self.load_programs()

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

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)

        upper = QWidget()
        ul = QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        body = QHBoxLayout()
        ul.addLayout(body, 1)

        left = Section("Select programs + version", SEC_TIMELINE)
        ll = left.body_layout
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Program", "Version", "Status", "Last deployed"])
        self.tree.setIndentation(18)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setFocusPolicy(Qt.NoFocus)
        hdr = self.tree.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_VERSION, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.Fixed)
        self.tree.setColumnWidth(COL_VERSION, 130)
        self.tree.setColumnWidth(COL_STATUS, 70)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(lambda it: self.expanded.add(it.text(COL_NAME)))
        self.tree.itemCollapsed.connect(lambda it: self.expanded.discard(it.text(COL_NAME)))
        ll.addWidget(self.tree, 1)
        body.addWidget(left, 1)

        self.dest_box = Section("Destinations", SEC_SAVE)
        self.dest_box.setFixedWidth(340)
        self.dest_layout = self.dest_box.body_layout
        body.addWidget(self.dest_box)
        self._build_dest_rows()

        actions = QHBoxLayout()
        for text, slot in (("Select all", self._select_all), ("Select new", self._select_new),
                           ("Clear", self._clear), ("Refresh", self.refresh)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions.addStretch(1)
        self.fix_btn = QPushButton("Fix")
        self.fix_btn.setToolTip("Tidy the archives and icons of every program on the ticked destinations")
        self.fix_btn.clicked.connect(self._on_fix)
        self.libs_btn = QPushButton("Deploy Libraries")
        self.libs_btn.setToolTip("Unpack the shared _internal folder into the ticked programs on the Scratch")
        self.libs_btn.clicked.connect(self._on_deploy_libraries)
        actions.addWidget(self.fix_btn)
        actions.addWidget(self.libs_btn)
        actions.addStretch(1)
        self.readme_btn = QPushButton("Copy ReadMe only")
        self.readme_btn.clicked.connect(self._on_copy_readme_only)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("primary")
        self.copy_btn.setMinimumWidth(110)
        self.copy_btn.clicked.connect(self._on_copy)
        actions.addWidget(self.readme_btn)
        actions.addWidget(self.copy_btn)
        ul.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setFormat("")
        self.progress_label = QLabel()
        self.progress.hide()
        self.progress_label.hide()
        ul.addWidget(self.progress)
        ul.addWidget(self.progress_label)
        split.addWidget(upper)
        split.addWidget(log_box(self.hub))
        split.setSizes([420, 380])

    def _log(self, msg: str, force_scroll: bool = False):
        self.hub.write(msg, force_scroll)

    def _build_dest_rows(self):
        while self.dest_layout.count():
            item = self.dest_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.dest_checks.clear()
        dests = core.destination_roots()
        if not dests:
            lbl = QLabel("No paths configured.\nPress Set paths.")
            lbl.setStyleSheet("color: #666;")
            self.dest_layout.addWidget(lbl)
        for name, path in dests:
            box = QWidget()
            box.setStyleSheet("background: transparent;")
            grid = QGridLayout(box)
            grid.setContentsMargins(0, 2, 0, 6)
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.dest_checks[name] = cb
            grid.addWidget(cb, 0, 0)
            lbl = QLabel(wrap_path(str(path)))
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #333;")
            grid.addWidget(lbl, 1, 0)
            self.dest_layout.addWidget(box)
        if core.PRACTICE:
            note = QLabel("Practice mode — these are test folders on C:, "
                          "not the real Scratch / SharePoint.")
            note.setWordWrap(True)
            note.setStyleSheet("color: #8a4b00;")
            self.dest_layout.addWidget(note)
        self.dest_layout.addStretch(1)

    def selected_destination_roots(self) -> list[Path]:
        all_dests = dict(core.destination_roots())
        return [all_dests[n] for n, cb in self.dest_checks.items()
                if cb.isChecked() and n in all_dests]

    def set_busy(self, busy: bool):
        self._busy = busy
        for b in (self.copy_btn, self.readme_btn, self.fix_btn, self.libs_btn):
            b.setEnabled(not busy)

    def _progress(self, done: int, total: int, label: str = ""):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        self.progress_label.setText(f"{label}  ({done}/{total})" if label else f"{done}/{total}")
        self.progress.show()
        self.progress_label.show()

    def _progress_hide(self):
        self.progress.hide()
        self.progress_label.hide()

    # ----------------- PATHS -----------------
    def _change_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select programs folder",
                                                  str(core.programs_root()))
        if not folder:
            return
        cfg = core.load_config()
        cfg["root_folder"] = str(Path(folder))
        core.save_config(cfg)
        self.apply_paths()
        if self.builder is not None:
            self.builder.apply_paths()

    def _open_set_paths(self):
        if set_paths_dialog(self):
            self._log(f"Paths saved to {core.CONFIG_PATH}")
            self.apply_paths()
            if self.builder is not None:
                self.builder.apply_paths()

    def apply_paths(self):
        self._build_dest_rows()
        self.load_programs()

    def refresh(self):
        self.state_deployed = core.load_state()
        self.load_programs()

    # ----------------- PROGRAM LIST -----------------
    def load_programs(self):
        self.root_lbl.setText(str(core.programs_root()))
        self._updating = True
        try:
            self.tree.clear()
            self.rows.clear()
            self.helper_names_by_parent.clear()
            self.helper_parent_dir.clear()
            root = core.programs_root()
            dist = core.dist_root()
            if not root.exists():
                self._log(f"ERROR: programs root does not exist: {root}")
                return
            program_dirs = core.list_program_dirs(root)
            if not program_dirs:
                self._log(f"No program folders found in: {root}")
                return
            # A helper whose name is also a program folder is that program.
            taken = {p.name.lower() for p in program_dirs}
            self.helper_names_by_parent = {
                parent: [n for n in names if n.lower() not in taken]
                for parent, names in core.find_helper_programs(program_dirs).items()}
            core.helper_parents(refresh=True)
            # Read once for the whole list — it lives on the share.
            versions_map = core.read_versions_txt(force=True)
            for p in program_dirs:
                item = self._make_row(p.name, p, dist, versions_map)
                self.tree.addTopLevelItem(item)
                self._attach_combo(p.name)
                for n in self.helper_names_by_parent.get(p.name, []):
                    self.helper_parent_dir[n] = p
                    child = self._make_row(n, root / n, dist, versions_map)
                    item.addChild(child)
                    self._attach_combo(n)
                item.setExpanded(p.name in self.expanded)
        finally:
            self._updating = False
        self._fit_name_column()

    def _fit_name_column(self):
        """Wide enough for the longest name at its depth, folded or not — a
        name is never cut. Measured by hand: the column's own fit-to-contents
        leaves out the tick box and the indent of a helper row."""
        fm = self.tree.fontMetrics()
        widest = fm.horizontalAdvance("Program") + 20
        for name, row in self.rows.items():
            depth = 2 if row["item"].parent() is not None else 1
            widest = max(widest, fm.horizontalAdvance(name)
                         + depth * self.tree.indentation() + 14 + 28)
        self.tree.setColumnWidth(COL_NAME, widest)

    def _make_row(self, name: str, key: Path, dist: Path, versions_map: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setText(COL_NAME, name)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(COL_NAME, Qt.Unchecked)
        combo = QComboBox()
        combo.setMinimumWidth(110)
        guard_wheel(combo)
        self.rows[name] = {"item": item, "combo": combo, "key": key, "is_new": False, "latest": ""}
        self._fill_row(name, dist, versions_map)
        return item

    def _attach_combo(self, name: str):
        row = self.rows[name]
        holder = QWidget()
        # The row's own (white / alternate) ground shows through.
        holder.setObjectName("comboHolder")
        holder.setStyleSheet("#comboHolder { background: transparent; }")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.addWidget(row["combo"])
        self.tree.setItemWidget(row["item"], COL_VERSION, holder)

    def _fill_row(self, name: str, dist: Path, versions_map: dict):
        """Versions, status and last deployed of one row; touches only what changed."""
        row = self.rows[name]
        combo: QComboBox = row["combo"]
        names = [vf.name for vf in core.list_versions(dist / name)]
        latest = names[0] if names else ""
        current = combo.currentText()
        if [combo.itemText(i) for i in range(combo.count())] != names:
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            combo.setCurrentText(current if current in names else latest)
            combo.blockSignals(False)
        combo.setEnabled(bool(names))
        deployed = core.last_deployed(name, versions_map, self.state_deployed)
        is_new = bool(latest) and core.is_newer_version(latest, deployed)
        row["latest"], row["is_new"] = latest, is_new
        text, colour = _status_of(bool(names), is_new, deployed)
        item = row["item"]
        item.setText(COL_STATUS, text)
        item.setForeground(COL_STATUS, QBrush(QColor(_STATUS_COLOURS[colour])))
        item.setText(COL_DEPLOYED, deployed or "(none)")
        item.setForeground(COL_DEPLOYED, QBrush(QColor("#555555")))

    def update_rows_in_place(self):
        """After a copy or a build: new versions, NEW/UTD and last deployed follow,
        while the ticks, the chosen versions and the scroll stay as they were."""
        self.state_deployed = core.load_state()
        dist = core.dist_root()
        versions_map = core.read_versions_txt(force=True)
        self._updating = True
        try:
            for name in self.rows:
                try:
                    self._fill_row(name, dist, versions_map)
                except Exception:
                    continue
        finally:
            self._updating = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._updating or column != COL_NAME or item.parent() is not None:
            return
        # Ticking a program takes its helpers along (one-way push) and opens them.
        name = item.text(COL_NAME)
        helpers = self.helper_names_by_parent.get(name, [])
        if not helpers:
            return
        state = item.checkState(COL_NAME)
        self._updating = True
        try:
            for n in helpers:
                self.rows[n]["item"].setCheckState(COL_NAME, state)
        finally:
            self._updating = False
        if state == Qt.Checked:
            item.setExpanded(True)

    def _set_checked(self, name: str, on: bool):
        self.rows[name]["item"].setCheckState(COL_NAME, Qt.Checked if on else Qt.Unchecked)

    def _select_all(self):
        self._updating = True
        try:
            for n in self.rows:
                self._set_checked(n, True)
        finally:
            self._updating = False

    def _select_new(self):
        any_new = False
        self._updating = True
        try:
            for n, row in self.rows.items():
                self._set_checked(n, row["is_new"])
                if row["is_new"]:
                    row["combo"].setCurrentText(row["latest"])
                    any_new = True
        finally:
            self._updating = False
        if not any_new:
            self._log("Select new: no NEW versions found.", force_scroll=True)

    def _clear(self):
        self._updating = True
        try:
            for n in self.rows:
                self._set_checked(n, False)
        finally:
            self._updating = False

    def selected_programs(self) -> list[str]:
        return [n for n, row in self.rows.items()
                if row["item"].checkState(COL_NAME) == Qt.Checked]

    # ----------------- ICONS -----------------
    def resolve_icon_conflicts(self, scan: list[tuple]) -> dict[str, bool]:
        """The dialog part of the icon check — UI thread only."""
        decisions: dict[str, bool] = {}
        no_ask = bool(self.builder is not None and self.builder.replace_icons.isChecked())
        for name, icon_src, conflict, dst_exists in scan:
            if conflict is not None and no_ask:
                replace = True
                self._log(f"[{name}] icon.ico differs — replaced without asking")
            elif conflict is not None:
                replace = icon_compare_dialog(self, name, conflict, icon_src)
            else:
                replace = None
            decisions.update(core.icon_decisions_for(name, dst_exists, replace))
        return decisions

    # ----------------- COPY -----------------
    def _record_deployed(self, name: str, version: str):
        self.state_deployed[name.lower()] = version
        core.write_version_to_txt(name, version)

    def copy_one_after_build(self, name: str, version: str, roots: list[Path]) -> dict:
        """Copy ONE freshly built program — called from the Builder's worker thread.
        The icon dialog runs on the UI thread; the worker waits for the answer."""
        program_dir = core.dist_root() / name
        try:
            found = core.scan_icon_conflicts([(program_dir, version)], roots)
        except Exception as e:
            found = []
            self._log(f"Icon check failed ({e}) — icons copied as usual")
        decisions = ask_on_ui(self.resolve_icon_conflicts, found) if found else {}
        try:
            stats, file_log = core.deploy_one_program(
                program_dir, version, roots, dict(self.helper_parent_dir),
                self._log, decisions)
            self._record_deployed(name, version)
            core.save_state(self.state_deployed)
            return {"name": name, "version": version, "dest_ok": stats.get("dest_ok", 0),
                    "error": None, "stats": dict(stats), "file_log": file_log}
        except Exception as e:
            self._log(f"[{name}] ERROR: {e}\n")
            return {"name": name, "version": version, "dest_ok": 0, "error": str(e),
                    "stats": {"program_failed": 1}, "file_log": [f"  [failed  ]  {e}"]}

    def copy_after_build_report(self, results: list[dict], roots: list[Path],
                                copy_elapsed: float, build_text: str, build_info: dict):
        """The end-of-run reports of a build where each program was copied
        straight after its own build (UI thread)."""
        self._write_reports(results, roots, copy_elapsed, build_text, build_info)
        self.update_rows_in_place()

    def _write_reports(self, results, roots, elapsed, build_text=None, build_info=None):
        total = defaultdict(int)
        detail = []
        for r in results:
            for k, v in r["stats"].items():
                total[k] += v
            detail.append(f"\n  {r['name']}  {r['version']}")
            detail.extend(r["file_log"])
        n_errors = sum(total[k] for k in core.ERROR_KEYS)
        per_program = [{k: r[k] for k in ("name", "version", "dest_ok", "error")} for r in results]
        if build_text:
            self._log(build_text)
        self._log(core.copy_summary(total, per_program, len(results), roots, elapsed,
                                    n_errors, detail), force_scroll=True)
        self._log(core.overall_summary(per_program, roots, elapsed, n_errors, build_info),
                  force_scroll=True)

    def _on_copy(self):
        names = self.selected_programs()
        roots = self.selected_destination_roots()
        if not names:
            message(self, "warning", "Nothing selected", "Select at least one program.")
            return
        if not roots:
            message(self, "warning", "No destination", "Select at least one destination.")
            return
        dist = core.dist_root()
        jobs = [(dist / n, self.rows[n]["combo"].currentText().strip()) for n in names]
        helper_dirs = dict(self.helper_parent_dir)
        self.hub.clear()
        self._log("Checking icon conflicts...\n")
        self.set_busy(True)

        def scan():
            try:
                found = core.scan_icon_conflicts(jobs, roots)
            except Exception as e:
                found = []
                self._log(f"Icon check failed ({e}) — icons copied as usual")
            on_ui(start, found)

        def start(found):
            decisions = self.resolve_icon_conflicts(found)
            self._log("Starting deploy...\n")
            threading.Thread(target=worker, args=(decisions,), daemon=True).start()

        def worker(decisions):
            t0 = time.perf_counter()
            results = []
            try:
                for program_dir, version in jobs:
                    name = program_dir.name
                    try:
                        stats, file_log = core.deploy_one_program(
                            program_dir, version, roots, helper_dirs, self._log, decisions)
                        self._record_deployed(name, version)
                        results.append({"name": name, "version": version,
                                        "dest_ok": stats.get("dest_ok", 0), "error": None,
                                        "stats": stats, "file_log": file_log})
                    except Exception as e:
                        self._log(f"[{name}] ERROR: {e}\n")
                        results.append({"name": name, "version": version, "dest_ok": 0,
                                        "error": str(e), "stats": {"program_failed": 1},
                                        "file_log": [f"  [failed  ]  {e}"]})
                if any(r["error"] is None for r in results):
                    core.save_state(self.state_deployed)
                elapsed = time.perf_counter() - t0
                on_ui(self._write_reports, results, roots, elapsed)
            finally:
                on_ui(self.set_busy, False)
                on_ui(self.update_rows_in_place)

        threading.Thread(target=scan, daemon=True).start()

    def _on_copy_readme_only(self):
        names = self.selected_programs()
        roots = self.selected_destination_roots()
        if not names:
            message(self, "warning", "Nothing selected", "Select at least one program.")
            return
        if not roots:
            message(self, "warning", "No destination", "Select at least one destination.")
            return
        helper_dirs = dict(self.helper_parent_dir)
        self.hub.clear()
        self._log("Copying ReadMe files only...\n")
        self.set_busy(True)

        def worker():
            try:
                core.copy_readme_only(names, roots, helper_dirs, self._log)
                self._log("\nDone.", force_scroll=True)
            finally:
                on_ui(self.set_busy, False)
        threading.Thread(target=worker, daemon=True).start()

    def _on_fix(self):
        roots = self.selected_destination_roots()
        if not roots:
            message(self, "warning", "No destination", "Select at least one destination.")
            return
        self.hub.clear()
        self._log("Fixing archives and icons...\n")
        self.set_busy(True)

        def worker():
            try:
                core.fix_destinations(roots, self._log)
                self._log("\nDone.", force_scroll=True)
            except Exception as e:
                self._log(f"\nFix aborted: {e}", force_scroll=True)
            finally:
                on_ui(self.set_busy, False)
        threading.Thread(target=worker, daemon=True).start()

    def _on_deploy_libraries(self):
        names = self.selected_programs()
        if not names:
            message(self, "warning", "Nothing selected", "Tick at least one program.")
            return
        internal, err = core.latest_internal_folder()
        if internal is None:
            message(self, "error", "Deploy Libraries", err)
            return
        scratch = core.scratch_root()
        if scratch is None:
            message(self, "error", "Deploy Libraries", "Scratch path not configured.\nPress Set paths first.")
            return
        self.hub.clear()
        self._log(f"Packing _internal from: {internal.parent}\n")
        self.set_busy(True)
        self._progress(0, 1, "")

        def progress(done, total, label):
            on_ui(self._progress, done, total, label)

        def worker():
            try:
                ok, fail = core.deploy_libraries(names, internal, scratch, self._log, progress)
                self._log(f"\n{'=' * 40}\nDeploy libraries DONE  ✓ {ok} OK  |  ✗ {fail} failed\n"
                          f"{'=' * 40}", force_scroll=True)
            except Exception as e:
                self._log(f"ERROR: {e}", force_scroll=True)
            finally:
                on_ui(self._progress_hide)
                on_ui(self.set_busy, False)
        threading.Thread(target=worker, daemon=True).start()
