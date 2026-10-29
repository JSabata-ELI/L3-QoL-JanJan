"""Both tabs built offscreen on throw-away folders — nothing is shown.

Run:  py -m pytest testing/test_ui.py -q
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dt_core as core  # noqa: E402
from test_core import _fake_build, env  # noqa: E402,F401


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    import dt_common
    dt_common.init_ui_thread()
    return app


@pytest.fixture
def win(qapp, env, monkeypatch):  # noqa: F811
    other = env["repo"] / "Other"
    other.mkdir()
    (other / "Other.py").write_text("", encoding="utf-8")
    _fake_build(env["dist"], "App", "v1.0.0")
    core.write_version_to_txt("App", "v0.9.0")
    import dev_tools
    w = dev_tools.DevToolsWindow()
    yield w, env
    w.deleteLater()
    qapp.processEvents()


def _pump(qapp, until, timeout=10.0):
    t0 = time.monotonic()
    while not until():
        qapp.processEvents()
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out")
        time.sleep(0.01)


def test_builder_list_ticks_and_groups(win, qapp):
    w, env = win
    b = w.builder
    assert set(b.items) == {"App", "App Listener", "Other"}
    # ticking a program takes its helper along, in click order
    b.items["Other"].setCheckState(1, Qt.Checked)
    b.items["App"].setCheckState(1, Qt.Checked)
    assert b.tick_order == ["Other", "App", "App Listener"]
    assert b.items["App"].text(0) == "2." and b.items["App Listener"].text(0) == "3."
    assert [n for _k, n in [(k, o.name if k == "project" else o["name"]) for k, o in b.build_queue()]] \
        == ["Other", "App", "App Listener"]
    # next version: Versions.txt says v0.9.0
    assert b.items["App"].text(2) == "0.9.1"
    # moving App to Ignored hides it and drops its ticks
    b.tree.setCurrentItem(b.items["App"])
    b.group_combo.setCurrentText("Ignored project")
    b._on_group_changed(0)
    assert b.items["App"].treeWidget() is None
    assert b.tick_order == ["Other"]
    b.show_ignored.setChecked(True)
    assert b.items["App"].treeWidget() is b.tree


def test_group_change_unfolds_nothing(win, qapp):
    """Choosing a Group used to unfold the neighbour that carries a helper
    (Diagnostic and its listener): the re-layout counted as a click on it."""
    w, _env = win
    b = w.builder
    b.items["App"].setExpanded(False)
    b.expanded.discard("App")
    b.tree.setCurrentItem(b.items["Other"])
    assert b.name_edit.text() == "Other"
    for grp in ("Side project", "Main project", "Side project"):
        b.group_combo.setCurrentText(grp)
        b._on_group_changed(0)
        qapp.processEvents()
        assert not b.items["App"].isExpanded()
        assert "App" not in b.expanded
        assert b.name_edit.text() == "Other"
        assert b.tree.currentItem() is b.items["Other"]


def test_next_version_override(win):
    w, _env = win
    b = w.builder
    b.tree.setCurrentItem(b.items["App Listener"])
    assert b.name_edit.text() == "App Listener" and not b.group_combo.isEnabled()
    b.next_edit.setText("3.0.0")
    b._on_next_edited("3.0.0")
    assert b.items["App Listener"].text(2) == "3.0.0"
    assert b.items["App"].text(2) == "0.9.1"


def test_builds_run_side_by_side(win, qapp, monkeypatch):
    """Several ticked programs build at once; the report keeps the tick order."""
    import threading
    w, _env = win
    b = w.builder
    running, peak, lock = [0], [0], threading.Lock()

    def fake_build(name, log, clock, delay):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        log("1 INFO: Building PYZ")
        time.sleep(delay)
        with lock:
            running[0] -= 1
        return True, ""

    monkeypatch.setattr(core, "parallel_builds", lambda n: min(3, n))
    monkeypatch.setattr(core, "build_one_project",
                        lambda p, ver, dist, root, log, clock:
                        fake_build(p.name, log, clock, 0.4 if p.name == "Other" else 0.2))
    monkeypatch.setattr(core, "build_one_helper",
                        lambda h, ver, dist, log, clock: fake_build(h["name"], log, clock, 0.1))
    b.copy_after.setChecked(False)
    for n in ("Other", "App"):
        b.items[n].setCheckState(1, Qt.Checked)
    b._build_selected()
    _pump(qapp, lambda: not b._building)
    assert peak[0] == 3
    _pump(qapp, lambda: "BUILD DONE" in b.hub._views[0].toPlainText())
    text = b.hub._views[0].toPlainText()
    assert "[Other] 1 INFO: Building PYZ" in text
    report = text[text.index("BUILD DONE"):]
    assert report.index("Other v") < report.index("App v") < report.index("App Listener v")
    lines = core.build_times_path().read_text(encoding="utf-8").splitlines()
    assert sorted(ln.split("\t")[1] for ln in lines[1:]) == ["App", "App Listener", "Other"]


def test_copy_tab_rows_and_copy(win, qapp):
    w, env = win
    c = w.copy
    assert {"App", "App Listener", "Other"} <= set(c.rows)
    row = c.rows["App"]
    assert row["combo"].currentText() == "v1.0.0"
    assert row["item"].text(2) == "NEW"
    row["item"].setCheckState(0, Qt.Checked)
    assert c.rows["App Listener"]["item"].checkState(0) == Qt.Checked
    c.rows["App Listener"]["item"].setCheckState(0, Qt.Unchecked)
    assert c.selected_programs() == ["App"]
    c._on_copy()
    _pump(qapp, lambda: not c._busy and (env["scratch"] / "App" / "App v1.0.0.exe").exists())
    _pump(qapp, lambda: c.rows["App"]["item"].text(2) == "UTD")
    assert (env["share"] / "App" / "App v1.0.0.exe").exists()
    assert core.read_versions_txt(force=True)["App"] == "v1.0.0"
    # The log is written in batches every 40 ms; wait for the last one.
    _pump(qapp, lambda: "ALL DONE" in w.copy.hub._views[0].toPlainText())
