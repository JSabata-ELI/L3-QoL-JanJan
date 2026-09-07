"""What the bot says about a plot it has just read.

The one thing that must never happen: reporting a failure to read as an empty
archive. Both look like a blank patch on the picture, so only the words can tell
them apart.

Run with:  python testing/test_plot_message.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402

NS = 1_000_000_000
HOUR = 3600 * NS
DAY = 24 * HOUR
START = int(1.8e18)
END = START + 180 * DAY


def build(name="Chiller 1", *, readings=True, failed_chunks=0, ok_chunks=1,
          vmin=None, vmax=None, rejected_pairs=()):
    rd = ch._BinReducer(ch.SeriesRequest(name, name, vmin, vmax),
                        START, END, 900)
    step = (END - START) // max(1, ok_chunks + failed_chunks)
    t = START
    for _ in range(ok_chunks):
        task = api.ChunkTask(name, t, t + step, 0)
        pairs = ([(t + step // 2, 20.0)] if readings else []) + \
                list(rejected_pairs)
        rd.add_chunk(task, [{"time": int(ts), "value": v} for ts, v in pairs])
        t += step
    for _ in range(failed_chunks):
        rd.mark_failed(api.ChunkTask(name, t, t + step, 0),
                       RuntimeError("HTTP 500"))
        t += step
    return rd.finish()


def report(mode="full", coverage=1.0, n_requests=180, elapsed=42.0):
    return ch.FetchReport(mode=mode, coverage=coverage, n_requests=n_requests,
                          elapsed_s=elapsed, n_done=n_requests, n_pvs=1)


def text(datas, rep):
    return ch.describe_fetch(datas, rep)


# --- read but empty is not the same as unreadable ---------------------------

def test_an_empty_window_says_the_archiver_holds_nothing():
    body, banner = text([build(readings=False)], report())
    assert "holds no readings" in body
    assert "could not be read" not in body
    assert banner == ""


def test_a_total_failure_is_never_called_an_empty_window():
    body, _ = text([build(readings=False, ok_chunks=0, failed_chunks=4)],
                   report())
    assert "could not read the archive" in body
    assert "HTTP 500" in body
    assert "holds no" not in body


def test_a_partly_unreadable_window_keeps_the_rest():
    body, banner = text([build(ok_chunks=3, failed_chunks=1)], report())
    assert "could not be read" in body
    assert "blank on the plot, not zero" in body
    assert "INCOMPLETE" in banner
    assert "25 %" in body


def test_one_dead_channel_among_healthy_ones_is_named():
    good = build("Chiller 1")
    bad = build("Chiller 2", readings=False, ok_chunks=0, failed_chunks=3)
    body, _ = text([good, bad], report())
    assert "Chiller 2 could not be read" in body
    assert "Chiller 1" not in body
    assert "could not read the archive" not in body


def test_a_channel_that_is_simply_not_archived_is_named_differently():
    good = build("Chiller 1")
    quiet = build("Chiller 2", readings=False)
    body, _ = text([good, quiet], report())
    assert "Chiller 2: read without trouble" in body
    assert "Chiller 2 could not be read" not in body


# --- sampling ---------------------------------------------------------------

def test_sampling_says_so_in_the_message_and_on_the_picture():
    body, banner = text([build()], report(mode="sampled", coverage=0.24))
    assert "Sampled: 24 %" in body
    assert "detail" in body
    assert "SAMPLED - 24 % of the window read" in banner


def test_a_complete_read_says_how_much_it_cost():
    body, banner = text([build()], report())
    assert "Read the whole window (180 requests, 42 s)" in body
    assert banner == ""


# --- other notes ------------------------------------------------------------

def test_dropped_readings_are_reported_when_there_are_many():
    d = build(vmin=0.0, vmax=80.0,
              rejected_pairs=[(START + i, 1e38) for i in range(9)])
    body, _ = text([d], report())
    assert "outside the valid range" in body


def test_a_few_dropped_readings_are_not_worth_saying():
    d = build(vmin=0.0, vmax=80.0, rejected_pairs=[(START + 1, 1e38)])
    # one dropped out of two is over the threshold, so make it clearly small
    rd = ch._BinReducer(ch.SeriesRequest("PV", "PV", 0.0, 80.0), START, END, 900)
    rd.add_chunk(api.ChunkTask("PV", START, END, 0),
                 [{"time": START + i, "value": 20.0} for i in range(100)]
                 + [{"time": START, "value": 1e38}])
    body, _ = text([rd.finish()], report())
    assert "outside the valid range" not in body
    assert d is not None


def test_condensing_is_explained():
    rd = ch._BinReducer(ch.SeriesRequest("PV", "PV"), START, END, 900,
                        raw_keep=5)
    rd.add_chunk(api.ChunkTask("PV", START, END, 0),
                 [{"time": START + i * DAY, "value": 20.0} for i in range(50)])
    body, _ = text([rd.finish()], report())
    assert "Condensed to 900 points" in body
    assert "lowest and highest" in body


def test_a_short_raw_window_is_not_called_condensed():
    rd = ch._BinReducer(ch.SeriesRequest("PV", "PV"), START, START + HOUR, 900)
    rd.add_chunk(api.ChunkTask("PV", START, START + HOUR, 0),
                 [{"time": START + i * NS, "value": 20.0} for i in range(60)])
    body, _ = text([rd.finish()], report(mode="short", n_requests=1))
    assert "Condensed" not in body


def test_a_non_numeric_channel_says_why_it_is_missing():
    rd = ch._BinReducer(ch.SeriesRequest("Sw", "Switch"), START, END, 900)
    rd.add_chunk(api.ChunkTask("Sw", START, END, 0),
                 [{"time": START, "value": True}])
    body, _ = text([rd.finish()], report())
    assert "Switch is not a number" in body


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", f.__name__, "->", repr(e))
    print("---")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
