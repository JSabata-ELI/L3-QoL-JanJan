"""Cancel scan must really cancel — single camera AND multi-camera.

The multi-camera scan used to ignore the button: cancel_scan only knew the single-camera
task, so with several cameras picked the scan kept walking the share and the timeline
filled up anyway. A single-camera cancel pressed after the worker's last check let the
finished list land as well.

Runs the real Viewer offscreen on a small synthetic set; each file name parse is slowed
down so a scan takes long enough to be cancelled half-way.

  python testing/test_cancel_scan.py
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_common as B

FAILS = []


def check(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def main():
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    root = Path(tempfile.gettempdir()) / "image_tools_test_cancel_scan"
    cam_names, cam_folder_lists = B.make_synthetic_set(root, cams=3, frames=150,
                                                       w=64, h=48)

    # Slow every parsed name down, and count the parses: a cancelled worker must stop.
    parses = [0]
    lock = threading.Lock()
    orig_parse = m.parse_unix_ns_from_name

    def slow_parse(p):
        with lock:
            parses[0] += 1
        time.sleep(0.004)
        return orig_parse(p)

    m.parse_unix_ns_from_name = slow_parse

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._ts_windows = None
    v._clear_pending_landing()

    def scan_ui_idle():
        return (not v.btn_cancel_scan.isVisibleTo(v) and not v.prog.isVisibleTo(v))

    # ── 1. multi-camera, cancelled half-way ─────────────────────────────────────
    print("multi-camera scan, Cancel scan pressed half-way")
    v._start_multi_cam_scan(cam_names, cam_folder_lists, None, online=False)
    B.wait_for(lambda: parses[0] >= 60, 20.0)
    v.btn_cancel_scan.click()
    at_cancel = parses[0]
    B.wait_for(lambda: False, 2.5)          # long enough for an uncancelled scan to land
    check(not v.items, "timeline stays empty")
    check(all(not c for c in v._cam_items), "no camera got frames")
    check(v._multi_scan_stop is None, "no scan left in flight")
    check(scan_ui_idle(), "progress bar and Cancel button hidden")
    check("cancelled" in v.lbl_filename.text(), "says the scan was cancelled")
    # 3 workers may each finish the file they are on.
    check(parses[0] - at_cancel <= 6,
          f"workers stopped (files read after cancel: {parses[0] - at_cancel})")

    # ── 2. single camera, cancelled half-way ────────────────────────────────────
    print("single-camera scan, Cancel scan pressed half-way")
    parses[0] = 0
    v._start_scan(cam_folder_lists[0], None, "single")
    B.wait_for(lambda: parses[0] >= 60, 20.0)
    v.btn_cancel_scan.click()
    B.wait_for(lambda: False, 1.5)
    check(not v.items and v.current_idx is None, "timeline stays empty")
    check(v._scan_task is None, "no scan left in flight")
    check(scan_ui_idle(), "progress bar and Cancel button hidden")

    # ── 3. single camera, the result arrives after the cancel ───────────────────
    print("single-camera scan, result delivered after the cancel")
    parses[0] = 0
    v._start_scan(cam_folder_lists[0], None, "single")
    task, old_gen = v._scan_task, v._gen
    v.btn_cancel_scan.click()
    late = [m.Item(Path("late.png"), 1_760_000_000_000_000_000)]
    task.signals.finished.emit(old_gen, late)
    B.wait_for(lambda: False, 1.0)
    check(not v.items, "late result dropped")

    # ── 4. control: an uncancelled multi-camera scan still loads ────────────────
    print("multi-camera scan, not cancelled")
    m.parse_unix_ns_from_name = orig_parse
    v._start_multi_cam_scan(cam_names, cam_folder_lists, None, online=False)
    ok = B.wait_for(lambda: bool(v.items), 30.0)
    check(ok and all(len(c) == 150 for c in v._cam_items), "all frames loaded")
    check(v._multi_scan_stop is None, "scan flag released when done")
    check(scan_ui_idle(), "progress bar and Cancel button hidden")

    B.wait_for(lambda: False, 1.0)
    print("\nPASS" if not FAILS else f"\nFAILED: {len(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
