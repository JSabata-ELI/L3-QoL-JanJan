"""Live mode -> a static Time window: the switch that killed Image Tools on 2026-10-05.

The operator had a layout tab live on one camera (C310, 14:00 onwards), opened Time
window, typed 10:00 into From and pressed OK. The program vanished without a
traceback. The crash dump showed a widget tree being destroyed by a Python WORKER
thread while the GUI thread was inside CancelIoEx — stopping the live folder watchers.

This replays that switch on the real share, in-process and offscreen: the real
Viewer, the real open_by_date, only the dialog's exec() is replaced by "set 10:00,
press OK". faulthandler prints every thread's Python stack if it crashes, which names
the worker and the object it freed.

  python testing/bench_live_to_static_crash.py              (default camera C310)
  python testing/bench_live_to_static_crash.py --rounds 20
"""
import argparse
import faulthandler
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_common as B

faulthandler.enable(all_threads=True)


def pump(app, seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default="L3-OM1FF-C310-_-IMG")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--from-hour", type=int, default=10)
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])

    now = datetime.now(m.TZ_PRAGUE)
    live_from = max(0, now.hour - 1)

    def accept_static(dlg):
        """The operator's clicks: From = 10:00, To = this hour + 1, Live off, OK."""
        dlg.cb_now.setChecked(False)
        dlg.time_from.setTime(QTime(args.from_hour, 0))
        dlg.time_to.setTime(QTime(min(23, now.hour + 1), 0))
        pump(app, 0.6)            # the debounced camera rescans start, as when typing
        import shiboken6
        print(f"  dialog: owned by Python={shiboken6.ownedByPython(dlg)}, "
              f"parent={type(dlg.parent()).__name__}", flush=True)
        return QDialog.DialogCode.Accepted

    m.DatePickerDialog.exec = accept_static

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    for rnd in range(1, args.rounds + 1):
        v.set_pending_tab_state({
            "cameras": [args.camera], "live": True,
            "window": {"date": now.date().isoformat(), "h_from": live_from,
                       "m_from": 0, "h_to": 23, "m_to": 59}})
        v.start_from_tab_state()
        if not B.wait_for(lambda: v._online_mode and bool(v._dir_watchers), 120.0):
            print(f"round {rnd}: live mode did not start "
                  f"(online={v._online_mode}, watchers={len(v._dir_watchers)})")
            return 1
        pump(app, 3.0)
        print(f"round {rnd}: live, {len(v._dir_watchers)} watcher(s), "
              f"{len(v.ts_list)} frames — switching to a static window", flush=True)
        v.open_by_date()
        ok = B.wait_for(lambda: not v._online_mode and bool(v.ts_list)
                        and v._scan_task is None, 120.0)
        pump(app, 5.0)            # let every scan / watcher thread wind down
        # deleteLater only runs from a real event loop (exec); flush it by hand.
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        left = [c for c in v.findChildren(m.DatePickerDialog)]
        print(f"round {rnd}: static ok={ok}, {len(v.ts_list)} frames, "
              f"{len(left)} Time window dialog(s) still alive", flush=True)

    print("no crash")
    # Stop the preview sweep before leaving: its threads still decoding while the
    # interpreter shuts down crash in QImageReader — a harness exit, not the bug.
    try:
        v._proxy_cancel(drop=True)
        v.cancel_scan()
        v.stop()
    except Exception:
        pass
    pump(app, 2.0)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
