"""The session-only list of saved moments, and the quiet prefetch behind it.

Three rules this pins, all of them things the operator asked for by name:

* **Navigation never saves.** Stepping through shots, or clicking a moment in the
  graph, records nothing. Save is an explicit press, and it greys out once the
  moment on the wall is already on the list.
* **The list is session-only.** It is never written to the settings file — closing
  the program empties it.
* **The row for the moment ON SCREEN wears a light blue band with dark ink**,
  painted on the item, never left to Qt's selection colour: the selection is where
  the operator last clicked, which is not the same thing.

Plus the prefetch: the newest saved moments are looked for quietly, and only after
what was asked for is on the wall.

Offscreen, no share and no archiver.
"""
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def make_stub(m):
    """The saved-moments half of the tab, without building the whole widget."""
    from PySide6.QtWidgets import QListWidget, QPushButton

    state = {"loaded": [], "logs": [], "resolved": []}
    stub = types.SimpleNamespace(
        _saved_moments=[],
        _moments_ns=[],
        _moment_ns=None,
        _res_cache={},
        _scan_cache=None,
        _moment_stop=__import__("threading").Event(),
        _moment_list=QListWidget(),
        _btn_moment_save=QPushButton("Save"),
        _btn_moment_forget=QPushButton("Forget"),
        _btn_moment_clear=QPushButton("Clear"),
        _log=lambda msg: state["logs"].append(msg),
        _checked_cameras=lambda: [("CAM1", "CAM1", Path("x")),
                                  ("CAM2", "CAM2", Path("y"))],
        _ensure_scan_cache=lambda: None,
        _load_moments=lambda ts: state["loaded"].append(list(ts)),
    )
    for name in ("_fmt_moment", "_refresh_moment_list", "_save_current_moment",
                 "_forget_saved_moment", "_clear_saved_moments",
                 "_on_saved_moment_clicked", "_start_moment_prefetch",
                 "_on_prefetch_item", "_res_cache_get", "_res_cache_put"):
        setattr(stub, name, getattr(m.ImageFinderWidget, name).__get__(stub))
    stub._prefetch_sig = m._MomentSignals()
    stub._prefetch_sig.item.connect(stub._on_prefetch_item)
    return stub, state


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QColor
    from PySide6.QtCore import Qt
    QApplication.instance() or QApplication(sys.argv)

    stub, state = make_stub(m)
    t0 = 1_788_330_000_000_000_000

    print("=== nothing is saved by looking ===")
    stub._moment_ns = t0
    stub._moments_ns = [t0]
    stub._refresh_moment_list()
    check("nothing is on the list after a moment is merely shown",
          stub._saved_moments == [], str(stub._saved_moments))
    check("and the empty box says what it is for",
          stub._moment_list.count() == 1
          and "press Save" in stub._moment_list.item(0).text(),
          stub._moment_list.item(0).text() if stub._moment_list.count() else "")
    check("that line cannot be clicked",
          stub._moment_list.item(0).flags() == Qt.ItemFlag.NoItemFlags)
    check("and Save is offered", stub._btn_moment_save.isEnabled())
    check("while Forget and Clear have nothing to do",
          not stub._btn_moment_forget.isEnabled()
          and not stub._btn_moment_clear.isEnabled())

    print("\n=== Save is an explicit press ===")
    stub._save_current_moment()
    check("one moment on the list", stub._saved_moments == [t0],
          str(stub._saved_moments))
    check("the row is there", stub._moment_list.count() == 1)
    check("Save greys out — it has nothing left to do",
          not stub._btn_moment_save.isEnabled())
    check("pressing it again changes nothing",
          (stub._save_current_moment() or True) and len(stub._saved_moments) == 1)

    print("\n=== the row for what is on screen is banded ===")
    it = stub._moment_list.item(0)
    check("light blue band", it.background().color().name().lower() == "#bbdef6"
          or it.background().color().name().lower() == "#bbdefb",
          it.background().color().name())
    check("with dark ink", it.foreground().color().name().lower() == "#0d47a1",
          it.foreground().color().name())
    check("and it says why", "on the wall" in it.toolTip(), it.toolTip())

    # Move to another moment: the band must move with it, not stay behind.
    t1 = t0 + 60_000_000_000
    stub._moment_ns = t1
    stub._moments_ns = [t1]
    stub._save_current_moment()
    check("newest first", stub._saved_moments == [t1, t0], str(stub._saved_moments))
    top, second = stub._moment_list.item(0), stub._moment_list.item(1)
    check("the band is on the new row",
          top.background().color().name().lower() == "#bbdefb")
    check("and off the old one",
          second.background().color().name().lower() != "#bbdefb",
          second.background().color().name())

    print("\n=== clicking a row goes back to that moment ===")
    stub._on_saved_moment_clicked(second)
    check("the tab was asked for that moment", state["loaded"] == [[t0]],
          str(state["loaded"]))

    print("\n=== Forget and Clear ===")
    stub._moment_list.setCurrentItem(stub._moment_list.item(1))
    stub._forget_saved_moment()
    check("Forget takes one row off", stub._saved_moments == [t1],
          str(stub._saved_moments))
    stub._forget_saved_moment.__self__._moment_list.setCurrentItem(None)
    n_logs = len(state["logs"])
    stub._forget_saved_moment()
    check("with nothing selected it says so instead of doing nothing",
          len(state["logs"]) > n_logs, str(state["logs"][-1:]))
    stub._clear_saved_moments()
    check("Clear empties the list", stub._saved_moments == []
          and "press Save" in stub._moment_list.item(0).text())

    print("\n=== the list is capped ===")
    for i in range(80):
        stub._moment_ns = t0 + i * 1_000_000_000
        stub._save_current_moment()
    check(f"never more than {m._SAVED_MOMENTS_MAX}",
          len(stub._saved_moments) == m._SAVED_MOMENTS_MAX,
          f"{len(stub._saved_moments)}")
    check("and the newest is kept, not the oldest",
          stub._saved_moments[0] == t0 + 79 * 1_000_000_000)

    print("\n=== the prefetch fills the cache the click reads ===")
    calls = []

    def fake_resolve(ts_ns, cam, scan=None):
        calls.append((int(ts_ns), cam))
        return {"cam": cam, "path": Path(f"{cam}_{ts_ns}.png"),
                "ts_ns": int(ts_ns), "asked_ns": int(ts_ns), "note": ""}

    orig = m._resolve_moment_one
    m._resolve_moment_one = fake_resolve
    try:
        stub._saved_moments = [t0 + i * 1_000_000_000 for i in range(20)]
        stub._res_cache = {}
        stub._start_moment_prefetch()
        B.wait_for(lambda: len(calls) >= 2 * m._MOMENT_PREFETCH_KEEP,
                   timeout_s=10.0)
        check("only the newest few are prefetched",
              len(calls) == 2 * m._MOMENT_PREFETCH_KEEP,
              f"{len(calls)} resolve(s) for 2 cameras")
        moments = {ts for ts, _cam in calls}
        check("and they are the newest ones",
              moments == set(stub._saved_moments[:m._MOMENT_PREFETCH_KEEP]),
              f"{len(moments)} moment(s)")
        B.wait_for(lambda: stub._res_cache_get("CAM1", stub._saved_moments[0])
                   is not None, timeout_s=10.0)
        check("a prefetched answer is in the cache a click reads",
              stub._res_cache_get("CAM1", stub._saved_moments[0]) is not None)
        # A second run asks for nothing: everything is already known.
        calls.clear()
        stub._start_moment_prefetch()
        B.wait_for(lambda: True, timeout_s=0.5)
        check("a repeat prefetch costs nothing", calls == [], str(calls[:3]))
    finally:
        m._resolve_moment_one = orig

    print("\n=== nothing of this is persisted ===")
    src = Path(m.__file__).read_text(encoding="utf-8", errors="ignore") \
        if getattr(m, "__file__", None) else ""
    if not src:
        src = (Path(__file__).resolve().parent.parent / "if_t.py").read_text(
            encoding="utf-8", errors="ignore")
    idx = src.find("_saved_moments")
    check("the saved list appears in the code",
          idx > 0, f"at {idx}")
    check("and never inside a settings write",
          "_saved_moments" not in _settings_writes(src),
          "found in a settings save")

    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


def _settings_writes(src: str) -> str:
    """Every line of the state-writing functions, so a test can check that the
    session-only list is not among them."""
    out = []
    keep = False
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("def ") and ("save_state" in s or "_write_state" in s
                                     or "save_settings" in s):
            keep = True
        elif s.startswith("def ") and keep:
            keep = False
        if keep:
            out.append(line)
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
