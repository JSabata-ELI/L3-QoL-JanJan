"""The backstop: a plot gives itself up rather than take the machine down.

Not the fix - the request-size ceiling in cpva_api is - but the thing that has
to hold when a plan is wrong in a way nobody foresaw, which is what happened on
2026-09-02.

Run with:  python testing/test_memory_guard.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import memstats  # noqa: E402

HOUR = int(3600 * 1e9)
GB = 1024 ** 3


def snap(own_gb=0.2, pc_used_gb=10.0, pc_limit_gb=31.3):
    return memstats.MemSnapshot(
        proc_commit=int(own_gb * GB), proc_ram=int(own_gb * GB / 2),
        sys_commit_used=int(pc_used_gb * GB),
        sys_commit_limit=int(pc_limit_gb * GB),
        sys_ram_used=8 * GB, sys_ram_total=16 * GB)


def guard(**kw):
    return ch._MemoryGuard(read_fn=kw.pop("read_fn", lambda: snap()), **kw)


def test_an_ordinary_reading_lets_the_read_go_on():
    g = guard()
    assert g() is False
    assert not g.tripped


def test_it_stops_when_this_program_alone_has_eaten_too_much():
    g = guard(read_fn=lambda: snap(own_gb=5.0))
    assert g() is True
    assert g.tripped and "this program" in g.reason


def test_the_pc_being_full_stops_it_too():
    # 24 GB of a 31.3 GB limit is 77 % - exactly the shape of the crash, where
    # one program held it all and the PC percentage looked survivable.
    assert guard(read_fn=lambda: snap(own_gb=0.2, pc_used_gb=24.0))() is False
    g = guard(read_fn=lambda: snap(own_gb=0.2, pc_used_gb=29.5))
    assert g() is True and "the PC" in g.reason


def test_once_it_has_said_stop_it_keeps_saying_stop():
    state = {"own": 5.0}
    g = guard(read_fn=lambda: snap(own_gb=state["own"]))
    assert g() is True
    state["own"] = 0.1                 # memory freed again
    g._next_check = 0.0                # and the throttle would allow a re-read
    assert g() is True, "an abandoned read resumed itself"


def test_the_operators_own_cancel_still_works():
    g = guard(cancel_fn=lambda: True)
    assert g() is True
    assert not g.tripped, "giving up was reported as a memory problem"


def test_the_figures_are_not_read_on_every_single_request():
    reads = {"n": 0}

    def counted():
        reads["n"] += 1
        return snap()

    g = guard(read_fn=counted)
    for _ in range(500):
        g()
    assert reads["n"] == 1, reads


def test_unreadable_figures_never_stop_a_read():
    g = guard(read_fn=lambda: None)
    assert g() is False


# --- through a whole fetch -------------------------------------------------

class FakeArchiver:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, channel, start_ns, end_ns, timeout=None):
        with self._lock:
            self.calls.append((start_ns, end_ns))
        return [{"time": start_ns, "value": 1.0}]


def test_a_runaway_plot_stops_itself_and_says_so():
    fake = FakeArchiver()
    real_fetch, real_read = api.cpva_fetch_samples, memstats.read
    api.cpva_fetch_samples = fake
    # Fine at first, hopeless after the fourth look.
    looks = {"n": 0}

    def read():
        looks["n"] += 1
        return snap(own_gb=0.2 if looks["n"] <= 4 else 9.0)

    memstats.read = read
    ch.MEM_CHECK_INTERVAL_S, keep = 0.0, ch.MEM_CHECK_INTERVAL_S
    try:
        datas, report = ch.fetch_series_reduced(
            [ch.SeriesRequest("PV", "A chiller")], 0, 50 * HOUR,
            timeout=1.0, max_workers=1, probe=False)
    finally:
        api.cpva_fetch_samples = real_fetch
        memstats.read = real_read
        ch.MEM_CHECK_INTERVAL_S = keep

    assert report.stopped_low_memory
    assert len(fake.calls) < 50, len(fake.calls)   # it did not read on regardless
    text, banner = ch.describe_fetch(datas, report)
    assert "stopped reading early" in text.lower()
    assert "STOPPED EARLY" in banner
    # What it did read is still drawn, and the rest is honestly a gap.
    assert datas[0].has_data
    assert datas[0].coverage < 1.0


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")


if __name__ == "__main__":
    _run_all()
    print("all memory-guard tests passed")
