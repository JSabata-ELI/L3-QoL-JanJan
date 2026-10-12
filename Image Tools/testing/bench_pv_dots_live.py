"""Bench: the PV table's dots in REAL live mode, logged once a second.

Opens one camera's current hour folders off the real share, switches live mode on and
prints, for every picked PV, what decides its dot: the text, the dot state, the
archived sample's offset from the frame, how much older the frame it was fetched for
is, and whether it is held / awaited. Read-only: share listing + archiver reads.

  python testing/bench_pv_dots_live.py --cam L3-OM1FF-C310-_-IMG --seconds 60
  python testing/bench_pv_dots_live.py --pv Back_Ref --pv "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"
"""
import argparse
import time
from datetime import datetime, timedelta

import bench_common as B


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", default="L3-OM1FF-C310-_-IMG")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--minutes-back", type=float, default=5.0)
    ap.add_argument("--pv", action="append", default=None)
    ap.add_argument("--changes", action="store_true",
                    help="print a PV only when its dot or the frame changes")
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    m.pv_registry_load()
    names = args.pv or ["Back_Ref", "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
                        "compressed SBW4", "L3-SPFE-AOD03-002:Order2_RB"]

    now = datetime.now(m.TZ_PRAGUE)
    s_ns = int((now - timedelta(minutes=args.minutes_back)).timestamp() * 1e9)
    e_ns = int((now + timedelta(minutes=5)).timestamp() * 1e9)
    folders = [d / args.cam for d in m.hour_dirs_for_windows([(s_ns, e_ns)])
               if (d / args.cam).is_dir()]
    if not folders:
        print("no folder for that camera now")
        return 1
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = [n for n in names if n in set(m.pv_all_names()) or ":" in n]
    v._pv_hidden = set()
    v._ts_windows = None
    v._start_scan(folders, axis_override=(s_ns, e_ns), folder_label=str(folders[-1]))
    if not B.wait_for(lambda: bool(v.ts_list), 120.0):
        print("scan did not finish")
        return 1
    v._btn_auto_follow.setChecked(True)
    print(f"live on {args.cam}: {len(v.ts_list)} frames; PVs {v._pv_enabled}\n")
    end = time.monotonic() + args.seconds
    nxt = time.monotonic()
    tally = {n: {} for n in v._pv_enabled}
    last = {}
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if time.monotonic() < nxt:
            continue
        nxt += 0.25
        cur = v._pv_current_ts()
        when = datetime.fromtimestamp(cur / 1e9).strftime("%H:%M:%S.%f")[:-3] if cur else "-"
        for n in v._pv_enabled:
            st = v._pv_state(n) or "own"
            tally[n][st] = tally[n].get(st, 0) + 1
            off = v._pv_sample_offset_s(n)
            age = v._pv_held_age_s(n)
            line = (f"{m.pv_label_for(n)[:22]:22s} {v._pv_values.get(n, '')!r:20s} "
                    f"dot={st:8s} off={'-' if off is None else f'{off:+.2f}'} "
                    f"age={'-' if age is None else f'{age:.2f}'} "
                    f"nosample={n in getattr(v, '_pv_no_sample', ())} "
                    f"await={n in v._pv_awaiting}")
            if args.changes and last.get(n) == (st, when):
                continue
            last[n] = (st, when)
            print(f"{time.strftime('%H:%M:%S')} frame {when}  {line}")
    print("\nshare of time per dot (sampled every 0.25 s):")
    for n, t in tally.items():
        tot = sum(t.values()) or 1
        print(f"  {m.pv_label_for(n)[:22]:22s} "
              + "  ".join(f"{k} {100 * c / tot:.0f}%" for k, c in sorted(t.items())))
    try:
        v._btn_auto_follow.setChecked(False)
        v._hard_reset_runtime()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
