"""Assert the PV panel keeps reading over a long session instead of freezing.

The report: with the PV overlay on, the values stopped updating after a few hours
and only a restart of the program brought them back.

Both single points of failure are covered here, because both end the same way — a
panel that never asks the archiver again:

  [1] a shared day-cache fetch that never finishes. Callers waited on its record
      with NO bound, so a fetcher that vanished after registering itself wedged
      every later caller for the life of the process (cpva_client._INFLIGHT_MAX_WAIT_S).
  [2] a PV fetch that never comes back. The panel is single-flight, so its
      in-flight flag stayed raised and every later trigger returned immediately
      (is_t.PV_FETCH_WATCHDOG_S).
  [3] a panel nothing triggers any more: refreshes ride on frame changes, so a
      broken trigger chain — or simply a still view on a live camera — used to
      mean the numbers were never re-read (is_t.PV_KEEPALIVE_S).

Runs offscreen against a fake archiver transport and local files — no share, no
network:

  python testing/test_pv_resilience.py
"""
import tempfile
import threading
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def case_leaked_inflight(cpva) -> None:
    """[1] A day-cache record whose fetcher never publishes a result must not wedge
    the next caller: it is dropped and the caller fetches for itself."""
    print("\n[1] a single-flight record whose fetcher never finished")
    cpva.invalidate()
    cpva.invalidate_lookback()
    key = ("TEST:Channel", cpva.today_key())
    # Exactly what a died-mid-fetch thread leaves behind: registered, never completed.
    cpva._inflight[key] = cpva._InFlight()
    orig_patience = cpva._INFLIGHT_MAX_WAIT_S
    cpva._INFLIGHT_MAX_WAIT_S = 1.0
    takeovers_before = cpva.STATS.get("takeovers", 0)
    try:
        t0 = time.monotonic()
        res = cpva.get_day(key[0], key[1], today_ttl=0.0, timeout=1.0)
        waited = time.monotonic() - t0
    finally:
        cpva._INFLIGHT_MAX_WAIT_S = orig_patience
        cpva._inflight.pop(key, None)
    check("the caller returns instead of waiting forever", waited < 20.0,
          f"waited {waited:.1f} s")
    check("and it returns real data, not an error", res.status in ("ok", "empty"),
          f"status={res.status!r}")
    check("the abandoned record is counted",
          cpva.STATS.get("takeovers", 0) > takeovers_before,
          f"takeovers={cpva.STATS.get('takeovers')}")
    check("and it is gone from the in-flight table", key not in cpva._inflight)


def case_watchdog(m, v, hang: threading.Event) -> None:
    """[2] A PV fetch that never returns is written off, and the panel asks again."""
    print("\n[2] a PV fetch that never comes back")
    m.PV_FETCH_WATCHDOG_S = 2.0
    stalls_before = v._pv_stall_recoveries
    hang.set()                      # every archiver read from here on blocks
    v._pv_force_refresh()
    stuck = B.wait_for(lambda: v._pv_fetch_inflight, 10.0)
    check("the fetch is in flight and cannot finish", stuck,
          f"inflight={v._pv_fetch_inflight}")
    freed = B.wait_for(lambda: v._pv_stall_recoveries > stalls_before, 30.0)
    check("the watchdog writes it off", freed,
          f"stalls={v._pv_stall_recoveries} inflight={v._pv_fetch_inflight}")
    hang.clear()                    # the archiver answers again
    back = B.wait_for(
        lambda: (not v._pv_fetch_inflight)
        and v._pv_values.get("PTM1", "").startswith("1"), 30.0)
    check("and the values come back without a restart", back,
          f"values={v._pv_values} inflight={v._pv_fetch_inflight}")


def case_keepalive(m, v) -> None:
    """[3] With nothing moving on screen, the panel still re-reads on its own."""
    print("\n[3] nothing triggers a refresh")
    m.PV_KEEPALIVE_S = 2.0
    v._pv_last_result_mono = 0.0
    v._pv_last_fetch_mono = 0.0
    before = v._pv_fetch_gen
    asked = B.wait_for(lambda: v._pv_fetch_gen > before, 30.0)
    check("the panel asks again by itself", asked,
          f"gen {before} -> {v._pv_fetch_gen}")


def main():
    m = B.load_slider()
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # ── fake archiver transport, with a switch that makes it hang ────────────────
    published: "list[tuple[int, float]]" = []
    hang = threading.Event()

    def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=None,
                             try_value_suffix=True):
        while hang.is_set():
            time.sleep(0.05)
        return ([s for s in published if start_ns <= s[0] <= end_ns], channel)

    cpva.fetch_values_ex = fake_fetch_values_ex
    cpva.fetch_values = lambda ch, a, b, **k: fake_fetch_values_ex(ch, a, b)[0]

    case_leaked_inflight(cpva)

    # Three shots 25 s apart, all published — the panel starts healthy, and every
    # failure below is therefore the failure being tested and not a missing sample.
    step_ns = 25_000_000_000
    newest_ns = int(time.time() * 1e9) - 2_000_000_000
    start_ns = newest_ns - 2 * step_ns
    tmp = Path(tempfile.mkdtemp(prefix="is_pvres_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, 1, 3, hz=1.0 / 25.0, start_ns=start_ns)
    frame_ts = sorted(t for p in cam_folder_lists[0][0].glob("*.png")
                      if (t := m.parse_unix_ns_from_name(p)))
    published += [(t + 25_000_000, 10.0 + i) for i, t in enumerate(frame_ts)]
    cpva.invalidate()
    cpva.invalidate_lookback()

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()
    orig_watchdog = m.PV_FETCH_WATCHDOG_S
    orig_keepalive = m.PV_KEEPALIVE_S
    try:
        v._ts_windows = None
        v._start_scan(list(cam_folder_lists[0]),
                      axis_override=(frame_ts[0], frame_ts[-1]),
                      folder_label=str(cam_folder_lists[0][0]))
        if not B.wait_for(lambda: bool(v.ts_list), 120.0):
            print("scan did not finish")
            return 1
        v._display_exact_index(len(v.items) - 1, v.items[-1].ts_ns, True)
        healthy = B.wait_for(
            lambda: v._pv_values.get("PTM1", "").startswith("1"), 30.0)
        check("the panel reads normally to begin with", healthy,
              f"values={v._pv_values}")

        case_watchdog(m, v, hang)
        case_keepalive(m, v)
    finally:
        hang.clear()
        m.PV_FETCH_WATCHDOG_S = orig_watchdog
        m.PV_KEEPALIVE_S = orig_keepalive
        try:
            v.close()
        except Exception:
            pass

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED"))
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
