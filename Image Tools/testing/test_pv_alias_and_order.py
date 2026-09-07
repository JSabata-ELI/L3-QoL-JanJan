"""Two rules the Image Finder's PV Search now follows.

1. **A channel with two archived names is asked for under both.** SBW4 lives under
   a HAPLS-era name and an L3 name, and which of them carries a given stretch of
   time depends on the configuration that ran — not on the date. A day that comes
   back empty under one name is asked for under the other before it is believed.

2. **Cameras and moments are independent halves.** The PV graph does not depend on
   a camera at all, so PV Search opens with none picked; the finished search is
   HELD and starts the moment cameras are checked, whichever half came first.

No archiver and no share: the transport is stubbed, so this runs anywhere.
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


# ── 1. the two names of SBW4 ──────────────────────────────────────────────────
def check_alias(m):
    cpva = m.cpva
    new, old = cpva.SBW4_CHANNEL, cpva.SBW4_CHANNEL_LEGACY

    check("the two SBW4 names know about each other",
          cpva.channel_aliases(new) == (old,)
          and cpva.channel_aliases(old) == (new,))
    check("nothing else has an alias — an alias is a promise, not a guess",
          cpva.channel_aliases("HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy") == ())

    day = "2026-09-01"          # AFTER the rename: the date rule says the L3 name
    check("the date rule still names the L3 channel for a recent day",
          cpva.channel_for_day(new, day) == new)
    check("and the HAPLS one for a day before the rename",
          cpva.channel_for_day(new, "2026-08-01") == old)

    # The archiver: only the OLD name holds this day. Under the date rule alone the
    # day reads back as empty — which is what made a search find nothing.
    t0, t1 = cpva.day_bounds_ns(day)
    stored = {old: [(t0 + i * 1_000_000_000, 10.0 + i) for i in range(5)]}
    asked: list = []

    def fake_values_ex(channel, start_ns, end_ns, **kw):
        asked.append(channel)
        return [(t, v) for (t, v) in stored.get(channel, [])
                if start_ns <= t <= end_ns], channel

    def fake_values(channel, start_ns, end_ns, **kw):
        return fake_values_ex(channel, start_ns, end_ns)[0]

    orig = (cpva.fetch_values_ex, cpva.fetch_values)
    cpva.fetch_values_ex, cpva.fetch_values = fake_values_ex, fake_values
    try:
        cpva._day_cache.clear()
        cpva._error_until.clear()
        res = cpva.get_day(cpva.channel_for_day(new, day), day)
        check("the empty L3 day comes back with the HAPLS samples",
              len(res.samples) == 5, f"{len(res.samples)} sample(s)")
        check("and it is reported as data, not as an empty day",
              res.status == "ok", res.status)
        check("the name that answered is carried out, so it can be SAID",
              res.src_channel == old, res.src_channel)
        check("both names were tried, the one asked for first",
              asked == [new, old], str(asked))

        # A channel that answers costs nothing extra — the second request only
        # ever happens on an empty day.
        asked.clear()
        stored[new] = [(t0 + 1, 1.0)]
        cpva._day_cache.clear()
        res = cpva.get_day(new, day)
        check("a name that answers is not asked twice", asked == [new], str(asked))
        check("and its own samples are what comes back",
              len(res.samples) == 1, f"{len(res.samples)} sample(s)")
    finally:
        cpva.fetch_values_ex, cpva.fetch_values = orig
        cpva._day_cache.clear()

    # A name the archiver REFUSES is not the end of the story either. Measured on
    # 04.09.2026: the L3 name answers HTTP 400 while the HAPLS name holds every
    # sample of the day, and PV Search reported "the archiver did not answer" and
    # drew nothing at all — a statement about the machine, from one bad name.
    asked.clear()

    def refuse_new(channel, start_ns, end_ns, **kw):
        asked.append(channel)
        if channel == new:
            raise cpva.CpvaError("HTTP 400 for /samples?channelName=" + channel)
        return [(t, v) for (t, v) in stored.get(channel, [])
                if start_ns <= t <= end_ns], channel

    orig2 = (cpva.fetch_values_ex, cpva.fetch_values)
    cpva.fetch_values_ex = refuse_new
    cpva.fetch_values = lambda ch, a, b, **kw: refuse_new(ch, a, b)[0]
    try:
        stored.pop(new, None)
        cpva._day_cache.clear()
        cpva._error_until.clear()
        res = cpva.get_day(new, day)
        check("a REFUSED name falls back to the other one",
              len(res.samples) == 5 and res.status == "ok",
              f"{len(res.samples)} sample(s), status={res.status}")
        check("and the day says which name answered", res.src_channel == old,
              res.src_channel)

        # But a genuine outage still reads as one: both names failing is an
        # archiver problem, and caching that as "empty" would be a lie.
        def refuse_both(channel, start_ns, end_ns, **kw):
            raise cpva.CpvaError("HTTP 500 for /samples?channelName=" + channel)

        cpva.fetch_values_ex = refuse_both
        cpva._day_cache.clear()
        cpva._error_until.clear()
        res = cpva.get_day(new, day)
        check("with BOTH names failing it is an archiver failure, not an empty day",
              res.status == "error" and not res.samples,
              f"status={res.status} n={len(res.samples)}")
    finally:
        cpva.fetch_values_ex, cpva.fetch_values = orig2
        cpva._day_cache.clear()
        cpva._error_until.clear()

    # The ".value" probe is a GUESS about the name, so its refusal must not turn a
    # successful empty answer into a failure — that is what stopped the fallback
    # above from ever being reached.
    def bare_empty(channel, start_ns, end_ns, **kw):
        if channel.endswith(".value"):
            raise cpva.CpvaError("HTTP 400 for /samples?channelName=" + channel)
        return []

    orig3 = cpva.fetch_samples_split
    cpva.fetch_samples_split = bare_empty
    try:
        got, src = cpva.fetch_values_ex(new, t0, t1)
        check("an empty day survives a refused .value probe",
              got == [] and src == new, f"{got}, {src}")
        # A 5xx or a timeout on the probe is a real failure and still raised.
        def bare_500(channel, start_ns, end_ns, **kw):
            if channel.endswith(".value"):
                raise cpva.CpvaError("HTTP 500 for /samples?channelName=" + channel)
            return []
        cpva.fetch_samples_split = bare_500
        try:
            cpva.fetch_values_ex(new, t0, t1)
            check("a 5xx on the probe is still raised", False, "no error raised")
        except cpva.CpvaError as e:
            check("a 5xx on the probe is still raised", "HTTP 500" in str(e), str(e))
    finally:
        cpva.fetch_samples_split = orig3

    # The windowed fetch (the region search's own path) does the same.
    got: list = []

    def fake_samples(channel, start_ns, end_ns, **kw):
        got.append(channel)
        return ([{"time": t, "value": v} for (t, v) in stored.get(channel, [])
                 if start_ns <= t <= end_ns])

    orig_s = cpva.fetch_samples
    cpva.fetch_samples = fake_samples
    try:
        stored.pop(new, None)
        got.clear()
        out = m._cpva_fetch_samples(new, t0, t1)
        check("a region read of an empty name falls back too",
              len(out) == 5 and got == [new, old], f"{len(out)}, {got}")
    finally:
        cpva.fetch_samples = orig_s

    # The window says which name it drew, rather than quietly drawing it.
    new_name = new
    dlg = types.SimpleNamespace(
        _load_gen=1, _series={}, _day_status={}, _seeds={},
        _alias_used={}, _moments=[], _regions=[],
        _pv_meta_for=lambda ch: {},
        _shown_days=lambda: [],
        _pv_list=_FakeList([("SBW4", new)]),
        _pv_label_for=lambda ch: "SBW4",
        _status=_FakeLabel(), _redraw=lambda: None,
        _refresh_day_list=lambda: None, _refresh_stats=lambda: None,
        _derived_reason={}, _checked_channels=lambda: [("SBW4", new)])
    # The row tooltips are built by the window itself — the real methods, so the
    # test cannot pass with a tooltip the window would never write.
    dlg._pv_row_tip = lambda ch: m.PVRegionSearchDialog._pv_row_tip(dlg, ch)
    dlg._set_pv_row_tip = (lambda it, extra="":
                           m.PVRegionSearchDialog._set_pv_row_tip(dlg, it, extra))
    m.PVRegionSearchDialog._on_series_loaded(
        dlg, {"gen": 1, "series": {}, "status": {}, "alias": {new: old}})
    check("the status line names the PV read under its other name",
          "other archived name" in dlg._status.text()
          and "SBW4" in dlg._status.text(), dlg._status.text())
    check("and the PV row's tooltip says which name that was",
          old in dlg._pv_list.item(0).toolTip(),
          dlg._pv_list.item(0).toolTip())
    # The row must STILL name its own channel: the note used to be written in
    # place of the tooltip, so hovering a row said nothing about which PV it is.
    check("the row still names the channel it plots",
          dlg._pv_list.item(0).toolTip().startswith(new_name),
          dlg._pv_list.item(0).toolTip())


class _FakeItem:
    def __init__(self, text, data):
        self._t, self._d, self._tip = text, data, ""

    def text(self):
        return self._t

    def data(self, _role):
        return self._d

    def setToolTip(self, s):
        self._tip = s

    def toolTip(self):
        return self._tip


class _FakeList:
    def __init__(self, rows):
        self._rows = [_FakeItem(t, d) for t, d in rows]

    def count(self):
        return len(self._rows)

    def item(self, i):
        return self._rows[i]


class _FakeLabel:
    def __init__(self):
        self._t = ""

    def setText(self, s):
        self._t = s

    def text(self):
        return self._t


# ── 2. either half first ──────────────────────────────────────────────────────
def check_order(m):
    """The search is held while there is no camera, and runs when there is one."""
    state = {"cams": [], "picker": 0, "ran": None, "logs": []}

    def checked_cameras(self):
        return list(state["cams"])

    def open_camera_picker(self):
        state["picker"] += 1
        # What the real picker does when the operator picks something.
        state["cams"] = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
        m.ImageFinderWidget._run_pending_pv_search(self)

    def load_moments(self, ts_list):
        state["ran"] = list(ts_list)

    stub = types.SimpleNamespace(
        _pending_pv_cfg=None,
        _checked_cameras=lambda: checked_cameras(stub),
        _open_camera_picker=lambda: open_camera_picker(stub),
        _load_moments=lambda ts: load_moments(stub, ts),
        _run_multiday_search=lambda cfg: state.update(ran="regions"),
        _run_condition_search=lambda cfg, cond: state.update(ran="condition"),
        _log=lambda msg: state["logs"].append(msg))
    stub._run_pending_pv_search = (
        m.ImageFinderWidget._run_pending_pv_search.__get__(stub))
    stub._start_pv_search = m.ImageFinderWidget._start_pv_search.__get__(stub)
    start = stub._start_pv_search

    picks = [1786953620000000000, 1786953645000000000]
    cfg = {"cameras": [], "days": [], "regions": {}, "condition": None,
           "moments_ns": list(picks), "moment_ns": picks[0],
           "primary_channel": None, "start_hour": 0, "max_hour": 23}

    start(cfg)
    check("with no camera picked the search is not refused — it asks for them",
          state["picker"] == 1)
    check("and then it runs, with every picked moment",
          state["ran"] == picks, str(state["ran"]))
    check("nothing is left held afterwards", stub._pending_pv_cfg is None)
    check("the log says what it was waiting for",
          any("waiting for the cameras" in s for s in state["logs"]),
          str(state["logs"]))

    # Cameras first, moments second: the same call, nothing held at all.
    state.update(picker=0, ran=None)
    start(cfg)
    check("with cameras already picked it runs straight away",
          state["ran"] == picks and state["picker"] == 0,
          f"{state['ran']}, picker {state['picker']}")

    # Cancelling the picker leaves the search held, and the next pick starts it.
    state.update(cams=[], picker=0, ran=None)
    stub._open_camera_picker = lambda: state.update(picker=state["picker"] + 1)
    start(cfg)
    check("a cancelled picker holds the search rather than losing it",
          stub._pending_pv_cfg is not None and state["ran"] is None)
    state["cams"] = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    stub._run_pending_pv_search()
    check("picking cameras later starts the held search",
          state["ran"] == picks, str(state["ran"]))

    # The cameras used are the ones checked NOW, not the ones the window opened
    # with — the whole point of letting the halves be answered in either order.
    state.update(ran=None)
    seen = {}
    stub._run_multiday_search = lambda cfg: seen.update(cams=cfg["cameras"])
    start({**cfg, "moments_ns": [], "moment_ns": None,
           "regions": {date(2026, 9, 1): [(1, 2)]},
           "days": [date(2026, 9, 1)]})
    check("a region search runs on the cameras checked now",
          seen.get("cams") == state["cams"], str(seen.get("cams")))


def main() -> int:
    m = load_finder()
    print("=== SBW4 under either of its two names ===")
    check_alias(m)
    print("\n=== cameras or moments, in either order ===")
    check_order(m)
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
