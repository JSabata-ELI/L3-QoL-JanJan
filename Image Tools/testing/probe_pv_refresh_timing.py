"""Probe: where does the time of one PV-panel refresh go?

Runs the Image Slider panel's own fetch (is_t._pv_last_known_ex, the same
4-wide pool) for the operator's saved PV list against the REAL archiver, and
times every HTTP request underneath it. That splits "the archiver is slow" from
"our code asks too much":

  * request time  — the archiver's answer, per request;
  * request count — how many requests one refresh costs;
  * total         — what the operator waits for.

Each moment is fetched twice: cold (empty cache, as after a start or a jump to
another day) and warm (the next refresh of a running panel, which is the one
the operator sees every 0.25 s).

Run:  python testing/probe_pv_refresh_timing.py
      python testing/probe_pv_refresh_timing.py --at "2026-10-08 10:30"
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cpva_client as cpva                             # noqa: E402
import is_t                                            # noqa: E402

REQS: list = []
_lock = threading.Lock()
_real_request = cpva._request_json


def _timed_request(path_qs, timeout):
    t0 = time.monotonic()
    err = ""
    n = None
    try:
        data = _real_request(path_qs, timeout)
        n = len(data) if isinstance(data, list) else None
        return data
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:80]
        raise
    finally:
        dt = time.monotonic() - t0
        q = dict(p.split("=", 1) for p in path_qs.split("?", 1)[-1].split("&")
                 if "=" in p)
        try:
            span_h = (int(q.get("end", 0)) - int(q.get("start", 0))) / 3.6e12
        except ValueError:
            span_h = 0.0
        with _lock:
            REQS.append((dt, q.get("channelName", "?"), span_h, n, err))


cpva._request_json = _timed_request


def saved_pv_names() -> list:
    """The operator's PV list, as the Slider restores it at start."""
    path = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" \
        / "slider_ui_state.json"
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    is_t.pv_registry_load(fallback=st)
    saved = st.get("pv_enabled")
    if not isinstance(saved, list):
        return []
    saved = set(str(n) for n in saved)
    return [n for n in is_t.pv_all_names() if n in saved]


def one_refresh(sources, ts_ns, fresh=False):
    """The panel's _fetch, minus the GUI: one task per source, 4 at a time."""
    def _one(name):
        ch = is_t.pv_channel_for(name)
        if not ch:
            return name, None, "missing", 0.0
        t0 = time.monotonic()
        val, status, _s = is_t._pv_last_known_ex(ch, ts_ns, fresh=fresh)
        return name, val, status, time.monotonic() - t0

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(len(sources),
                                            is_t.PV_FETCH_MAX_WORKERS)) as ex:
        out = list(ex.map(_one, sources))
    return time.monotonic() - t0, out


def report(tag, total, out):
    with _lock:
        reqs = list(REQS)
        REQS.clear()
    print(f"\n{tag}: {total:.2f} s for the whole refresh, {len(reqs)} requests")
    for name, val, status, dt in sorted(out, key=lambda r: -r[3]):
        v = "—" if val is None else f"{val:g}"
        print(f"    {name:<22} {dt:6.2f} s  {status:<9} {v}")
    if reqs:
        t_sum = sum(r[0] for r in reqs)
        print(f"  archiver time: sum {t_sum:.2f} s, slowest {max(r[0] for r in reqs):.2f} s")
        for dt, ch, span_h, n, err in sorted(reqs, reverse=True)[:8]:
            print(f"    {dt:6.2f} s  {span_h:7.2f} h  "
                  f"{('n=' + str(n)) if n is not None else err:<14} {ch}")


def clear_caches():
    with cpva._day_cache_lock:
        cpva._day_cache.clear()
    for name in ("_step_verdict", "_before_cache", "_before_error_until",
                 "_error_until"):
        obj = getattr(cpva, name, None)
        if hasattr(obj, "clear"):
            obj.clear()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="local time 'YYYY-MM-DD HH:MM' of a shot moment")
    args = ap.parse_args()

    names = saved_pv_names()
    sources = is_t.pv_source_names(names)
    print(f"archiver: {cpva.CPVA_BASE_URL}")
    print(f"panel PVs ({len(names)}): {', '.join(names)}")
    print(f"read from the archiver ({len(sources)}): {', '.join(sources)}")
    if not sources:
        print("nothing to read — no saved PV list")
        return 1

    moments = [("now (laser off)", int(time.time() * 1e9))]
    if args.at:
        dt = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(
            tzinfo=cpva.TZ_PRAGUE)
        moments.append((f"{args.at}", int(dt.timestamp() * 1e9)))

    for tag, ts in moments:
        clear_caches()
        total, out = one_refresh(sources, ts)
        report(f"{tag} — cold", total, out)
        total, out = one_refresh(sources, ts)
        report(f"{tag} — warm (next refresh)", total, out)
        total, out = one_refresh(sources, ts, fresh=True)
        report(f"{tag} — wait-ladder retry (fresh)", total, out)
    print("\n" + cpva.stats_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
