"""Read a long window off the real archiver and watch what it costs in memory.

The question this answers is the one the freeze of 2026-09-02 asked: does a
180-day plot stay within a sane amount of memory, or does it grow with the
window? Reads for real - no fakes - and samples this process's committed memory
throughout, so the answer is a number and not an opinion.

    python testing/bench_long_plot.py --days 180 --pvs L3-UTIL-CHL03-001:Temp

Prints the plan, the progress, how long it took, and the peak memory. A run
whose peak is a few hundred MB is the fix working; a peak that grows with
--days is it not working.
"""

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import memstats  # noqa: E402

NS = 1_000_000_000


class MemWatch:
    """Samples this process's committed memory in the background."""

    def __init__(self, every_s=0.2):
        self.every_s = every_s
        self.peak = 0
        self.start = 0
        self._stop = threading.Event()
        self._t = None

    def __enter__(self):
        snap = memstats.read()
        self.start = self.peak = snap.proc_commit if snap else 0
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.every_s):
            snap = memstats.read()
            if snap and snap.proc_commit > self.peak:
                self.peak = snap.proc_commit

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join(timeout=2.0)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--pvs", nargs="+", default=["L3-UTIL-CHL03-001:Temp"])
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--force-span-h", type=float, default=0.0,
                    help="ignore the measured rate and ask for this many hours "
                         "per request - reproduces the plan that froze the "
                         "laptop on 2026-09-02, when whole days were asked for "
                         "of a channel far too busy for them")
    args = ap.parse_args()

    if args.force_span_h:
        forced = int(args.force_span_h * 3600 * NS)
        ch.plan_span_for = lambda probe, **kw: forced
        print(f"  FORCED plan: {args.force_span_h:g} h per request, "
              f"whatever the channel's rate")

    end_ns = api.now_ns()
    start_ns = end_ns - int(args.days * 86400 * NS)
    reqs = [ch.SeriesRequest(pv, pv) for pv in args.pvs]

    last = [0.0]

    def progress(done, total):
        now = time.monotonic()
        if now - last[0] > 2.0 or done == total:
            last[0] = now
            print(f"    {done}/{total} requests", flush=True)

    def plan(mode, n_req, est_s, coverage):
        print(f"  plan: mode={mode}, {n_req} requests, "
              f"~{est_s:.0f} s estimated, coverage {coverage * 100:.0f} %")

    print(f"Reading {args.days:g} days of {', '.join(args.pvs)} "
          f"with {args.workers} workers")
    print(f"  request ceiling {api.MAX_RESPONSE_BYTES // (1024 * 1024)} MB, "
          f"own memory ceiling "
          f"{ch.MEM_OWN_CEILING_BYTES // (1024 ** 3)} GB")

    # Count what was really asked of the archiver, and how big the biggest
    # single answer was: a planned request that trips the size ceiling is split
    # into several real ones, and that is the difference between a bounded read
    # and the one that froze the laptop.
    real_fetch = api.cpva_fetch_samples
    stats = {"n": 0, "refused": 0, "biggest": 0}
    stats_lock = threading.Lock()

    def counting_fetch(channel, s, e, timeout=None):
        try:
            out = real_fetch(channel, s, e, timeout)
        except api.ResponseTooLarge:
            with stats_lock:
                stats["n"] += 1
                stats["refused"] += 1
            raise
        with stats_lock:
            stats["n"] += 1
            stats["biggest"] = max(stats["biggest"], len(out))
        return out

    api.cpva_fetch_samples = counting_fetch
    t0 = time.monotonic()
    with MemWatch() as mem:
        datas, report = ch.fetch_series_reduced(
            reqs, start_ns, end_ns, max_workers=args.workers,
            detail=args.detail, progress_fn=progress, plan_fn=plan,
            log_fn=lambda s: print(s, flush=True))
    elapsed = time.monotonic() - t0
    api.cpva_fetch_samples = real_fetch

    print()
    print(f"asked the archiver {stats['n']} times in all (the rate probes "
          f"included) for {report.n_requests} planned requests "
          f"({stats['refused']} answers refused as too large); "
          f"biggest answer held: {stats['biggest']} readings")
    print(f"mode={report.mode}  requests={report.n_requests} "
          f"(ok {report.n_done}, failed {report.n_failed})  "
          f"coverage={report.coverage * 100:.0f} %  in {elapsed:.0f} s")
    for pv, span in report.span_ns.items():
        print(f"  {pv}: {span / 3.6e12:g} h per request")
    for d in datas:
        print(f"  {d.request.display_name}: {d.n_samples} readings read, "
              f"{int((d.bin_count > 0).sum())} of {len(d.bin_count)} points "
              f"filled, units {d.units or '-'}")
    if report.stopped_low_memory:
        print(f"  STOPPED EARLY: {report.stopped_reason}")
    print(f"memory: {memstats.fmt(mem.start)} before, "
          f"{memstats.fmt(mem.peak)} peak, "
          f"+{memstats.fmt(mem.peak - mem.start)} for the read")
    text, banner = ch.describe_fetch(datas, report)
    if banner:
        print(f"banner: {banner}")
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
