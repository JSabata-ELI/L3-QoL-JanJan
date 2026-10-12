r"""Dev Tools changes things in place: no list rebuilds, no stray wheel edits.

- the mouse wheel over a combobox without the keyboard focus never changes it
  (Copy Manager's Version column, Builder's Group);
- a Group change and Show ignored move the existing rows, nothing is rebuilt;
- after a copy/build the Copy Manager rows update in place and keep the ticks;
- the log is written in batches, not one redraw per line.

Every window here is withdrawn — nothing is shown and nothing takes the focus.
Run: python -m pytest testing/test_ui_in_place.py  (from the Dev Tools folder)
"""
import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import b_t
import cm_t
import dev_tools


class _Wheel:
    def __init__(self, widget, delta):
        self.widget = widget
        self.delta = delta


@pytest.fixture(scope="module")
def _tk():
    # One Tk per process: a second tk.Tk() after destroy() fails to load ttk.
    r = tk.Tk()
    r.withdraw()
    dev_tools.install_combo_wheel_guard(r)
    yield r
    r.destroy()


@pytest.fixture
def root(_tk):
    yield _tk
    for child in _tk.winfo_children():
        child.destroy()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Projects A, B, C in a temp root; config, settings and state all in temp."""
    src = tmp_path / "src"
    dist = tmp_path / "dist"
    for name in ("A", "B", "C"):
        (src / name).mkdir(parents=True)
        (src / name / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (dist / "A" / "v1.0.0").mkdir(parents=True)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"root_folder": str(src), "dist_root": str(dist)}),
                   encoding="utf-8")
    monkeypatch.setattr(b_t, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(cm_t, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(b_t, "SETTINGS_PATH", tmp_path / "builder_settings.json")
    monkeypatch.setattr(b_t, "USAGE_LOG", tmp_path / "build_usage.json")
    monkeypatch.setattr(cm_t, "_state_path", lambda: tmp_path / "state.ini")
    b_t.invalidate_versions_cache()
    return tmp_path


def _pump(root, seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def test_class_binding_is_replaced(root):
    script = root.bind_class("TCombobox", "<MouseWheel>")
    assert script and "ttk::combobox::Scroll" not in script


def test_wheel_on_unfocused_combo_keeps_value(root):
    cb = ttk.Combobox(root, values=["v1.0.2", "v1.0.1", "v1.0.0"], state="readonly")
    cb.set("v1.0.1")
    for delta in (120, -120, 240, -360):
        assert dev_tools._combo_wheel(_Wheel(cb, delta)) == "break"
    assert cb.get() == "v1.0.1"


def test_wheel_over_combo_scrolls_the_list(root):
    canvas = tk.Canvas(root, height=50)
    inner = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    combos = []
    for i in range(40):
        cb = ttk.Combobox(inner, values=["a", "b"], state="readonly")
        cb.set("a")
        cb.grid(row=i, column=0)
        combos.append(cb)
    # A withdrawn canvas has no real size to scroll, so record the call.
    calls = []
    canvas.yview_scroll = lambda n, what: calls.append((n, what))
    dev_tools._combo_wheel(_Wheel(combos[0], -120))
    dev_tools._combo_wheel(_Wheel(combos[5], 120))
    assert calls == [(1, "units"), (-1, "units")]
    assert all(c.get() == "a" for c in combos)


def test_group_change_moves_rows_without_rebuild(root, sandbox):
    ui = b_t.BuilderUI(root)
    rows_before = {n: str(w) for n, w in ui.project_rows.items()}
    ui.project_checks["B"].set(True)

    ui._select_project(next(p for p in ui.projects_sorted if p.name == "B"))
    ui.group_var.set("Ignored project")
    ui._on_group_changed()

    assert {n: str(w) for n, w in ui.project_rows.items()} == rows_before
    assert ui.project_rows["B"].winfo_exists()
    assert ui.project_rows["B"].grid_info() == {}        # hidden, not destroyed
    assert ui.project_checks["B"].get() is False         # unseen = not built

    ui.show_ignored.set(True)
    ui._layout_project_rows()
    assert str(ui.project_rows["B"]) == rows_before["B"]
    rb = int(ui.project_rows["B"].grid_info()["row"])
    assert rb > int(ui.project_rows["A"].grid_info()["row"])
    assert rb > int(ui.project_rows["C"].grid_info()["row"])

    ui.show_ignored.set(False)
    ui._layout_project_rows()
    assert ui.project_rows["B"].grid_info() == {}


def test_next_version_edit_touches_one_label(root, sandbox):
    ui = b_t.BuilderUI(root)
    ui._select_project(next(p for p in ui.projects_sorted if p.name == "A"))
    ui.next_ver_var.set("2.0.0")
    assert ui.project_next_labels["A"].get() == "Next: 2.0.0"
    assert ui.project_next_labels["B"].get() == "Next: 1.0.0"


def test_copy_manager_updates_rows_in_place(root, sandbox):
    gui = cm_t.DeployGUI(root)
    key = next(k for k in gui.program_vars if k.name == "A")
    cb_before = str(gui.row_widgets[key][0])
    gui.program_vars[key].set(True)
    gui.program_version_vars[key].set("v1.0.0")

    (sandbox / "dist" / "A" / "v1.0.1").mkdir()
    gui._update_rows_in_place()

    cb, status, _deployed = gui.row_widgets[key]
    assert str(cb) == cb_before
    assert "v1.0.1" in tuple(cb.cget("values"))
    assert gui.program_vars[key].get() is True               # tick kept
    assert gui.program_version_vars[key].get() == "v1.0.0"   # choice kept
    assert status.cget("text") == "NEW"


def test_log_is_batched(root):
    text = tk.Text(root)
    pump = b_t.log_pump(text, lambda: True)
    flushes = []
    real_flush = pump._flush

    def counting_flush():
        flushes.append(1)
        real_flush()

    pump._flush = counting_flush

    def writer():
        for i in range(5000):
            pump.write(f"line {i}")

    # Calls from a thread need the main thread inside mainloop(), as in the app.
    t = threading.Thread(target=writer)

    def until_done():
        if t.is_alive():
            root.after(20, until_done)
        else:
            root.after(200, root.quit)

    root.after(0, t.start)
    root.after(20, until_done)
    root.mainloop()

    lines = text.get("1.0", "end").splitlines()
    assert [l for l in lines if l] == [f"line {i}" for i in range(5000)]
    assert len(flushes) < 100, len(flushes)


def _click(ui, name):
    """Click a project's tick box: flip it and run its command, as Tk does."""
    var = ui.project_checks[name]
    var.set(not var.get())
    ui._on_project_check_clicked(next(p for p in ui.projects_sorted if p.name == name))


def test_tick_order_follows_the_clicks(root, sandbox):
    ui = b_t.BuilderUI(root)
    for n in ("C", "A", "B"):
        _click(ui, n)
    assert ui._tick_order == ["C", "A", "B"]
    # The number sits in its own fixed slot left of the tick box; the tick
    # box text stays the bare name, so nothing in the row moves.
    assert ui._order_vars["C"].get() == "1."
    assert ui._order_vars["B"].get() == "3."
    assert ui._check_texts["C"][0].get() == "C"
    assert int(str(ui._order_labels["C"].cget("width"))) == 3

    _click(ui, "A")                       # untick: B moves up
    assert ui._tick_order == ["C", "B"]
    assert ui._order_vars["A"].get() == ""
    assert ui._order_vars["B"].get() == "2."

    _click(ui, "A")                       # ticked again: goes last
    assert [p.name for _k, p in ui._checked_in_tick_order()] == ["C", "B", "A"]

    ui._clear_all_projects()
    assert ui._tick_order == [] and ui._order_vars["C"].get() == ""
    _click(ui, "B")
    ui._select_all_projects()             # B keeps first place, the rest follow
    assert ui._tick_order == ["B", "A", "C"]


def test_each_program_is_copied_right_after_its_build(root, sandbox, monkeypatch):
    ui = b_t.BuilderUI(root)
    gui = cm_t.DeployGUI(root)
    ui._cm_ref = gui
    events = []

    def fake_build(p, ver, live_log=None):
        events.append(("build", p.name))
        return True, str(sandbox / "dist" / p.name / f"v{ver}")

    def fake_copy(name, ver, roots):
        events.append(("copy", name))
        return {"name": name, "version": ver, "dest_ok": len(roots), "error": None,
                "stats": {"exe_copied": 1}, "file_log": []}

    reports = []
    monkeypatch.setattr(ui, "_build_one_project", fake_build)
    monkeypatch.setattr(gui, "copy_one_after_build", fake_copy)
    monkeypatch.setattr(gui, "_get_selected_destination_roots",
                        lambda: [sandbox / "dest"])
    monkeypatch.setattr(gui, "copy_after_build_report",
                        lambda results, *a, **k: reports.append(results))

    class _Inline:                         # the worker runs right here
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(b_t.threading, "Thread", _Inline)

    for n in ("B", "C", "A"):
        _click(ui, n)
    ui.copy_after_build_var.set(True)
    ui._build_selected()
    root.update()

    assert events == [("build", "B"), ("copy", "B"),
                      ("build", "C"), ("copy", "C"),
                      ("build", "A"), ("copy", "A")]
    assert [r["name"] for r in reports[0]] == ["B", "C", "A"]
