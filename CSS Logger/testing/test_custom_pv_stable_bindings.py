"""
A custom PV always reads the PVs it was defined on, whatever letters they have
today — and SBW4 is read under both of its archived names.

  * an unbound letter never borrows whichever PV holds that letter now: the
    custom PV stays empty and says why;
  * the saved custom_pvs.json entries give the same numbers from the same PVs
    under every saved preset (the letters differ, the PVs do not), and survive
    a round trip through the editor dialog unchanged;
  * renaming a custom PV in the dialog carries the rename into the customs
    built on it;
  * SBW4: a day with nothing under the HAPLS name is read under the L3 name and
    filed as SBW4; a day with HAPLS data never asks the L3 name; the bracket
    samples around a range are not mixed in; the pre-window value follows the
    same day rule; Spectra's own fetch does the same.

Runs headless, no archiver, no windows.

Run:  python test_custom_pv_stable_bindings.py   (prints PASS/FAIL, exits non-zero)
"""
import json
import os
import pathlib
import sys
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from PySide6.QtWidgets import QApplication                   # noqa: E402

import cpva_core                                             # noqa: E402
import main as app_main                                      # noqa: E402
import sp_t                                                  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

app = QApplication.instance() or QApplication(sys.argv)
W = app_main.CSSLoggerWidget
failures = []


def check(what, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + what + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(what)


class _Host:
    """Just the parts of the CSS Logger widget the custom-PV code reads."""
    _col_letter                 = staticmethod(W._col_letter)
    _col_label                  = W._col_label
    _channel_letters            = W._channel_letters
    _cpv_dialog_channels        = W._cpv_dialog_channels
    _cpv_plan                   = W._cpv_plan
    _compute_custom_pvs_in_rows = W._compute_custom_pvs_in_rows
    cpv_defs_for_spectra        = W.cpv_defs_for_spectra
    _pv_time_resolve            = W._pv_time_resolve

    def __init__(self, custom_pvs, real_pvs):
        self._custom_pvs = custom_pvs
        self._real = list(real_pvs)
        self._pv_order = list(real_pvs) + [d["name"] for d in custom_pvs]
        self._pv_settings = {}

    def _real_pv_names(self):
        return list(self._real)


def _canon(expr, bindings):
    """The formula written in PV names — what it means, whatever the letters."""
    return app_main._CPV_VAR_RE.sub(
        lambda m: "{" + bindings.get(m.group(0), "?" + m.group(0)) + "}", expr)


# ── 1. an unbound letter reads nothing ─────────────────────────────────────────
pv1, pv2 = "L3-TEST-A:Energy", "L3-TEST-B:Energy"
h = _Host([{"name": "Half bound", "expr": "A+B", "bindings": {"A": pv1}}],
          [pv1, pv2])                       # pv2 IS letter B in today's list
rows = [(1_000, {pv1: (1.0, ""), pv2: (10.0, "")})]
h._compute_custom_pvs_in_rows(rows)
check("unbound letter: no value, even though B is a loaded channel today",
      rows[0][1]["Half bound"][0] is None)
check("unbound letter: the reason is reported",
      any("B is not bound" in m for m in h._cpv_diag))
spec = h.cpv_defs_for_spectra()
check("unbound letter: Spectra gets the same verdict",
      spec and "B" in spec[0]["problem"] and ("B", pv2) not in spec[0]["srcs"])
_real, _plan, problems = h._pv_time_resolve(["Half bound"])
check("unbound letter: the PV-time graph reports it too",
      any("B is not bound" in p for p in problems))

# ── 2. the saved entries, under every saved preset ─────────────────────────────
saved = json.loads((_HERE / "custom_pvs.json").read_text(encoding="utf-8"))["custom_pvs"]
presets = json.loads((_HERE / "cpva_presets.json").read_text(encoding="utf-8"))["presets"]
check("every saved custom PV has a binding for every letter",
      all(set(app_main._cpv_vars(d["expr"])) <= set(d.get("bindings") or {})
          for d in saved))

# Distinct numbers per PV, so a letter pointing at the wrong PV shows.
all_pvs = sorted({pv for d in saved for pv in d["bindings"].values()}
                 | {pv for p in presets for pv in p["pvs"]})
VAL = {pv: 3.0 + 7.0 * i for i, pv in enumerate(all_pvs)}
meaning = {d["name"]: _canon(d["expr"], d["bindings"]) for d in saved}


def _expected(name, have):
    expr = meaning[name]
    d = next(x for x in saved if x["name"] == name)
    if not set(d["bindings"].values()) <= have:
        return None
    for pv in d["bindings"].values():
        expr = expr.replace("{" + pv + "}", repr(VAL[pv]))
    return eval(expr, {"__builtins__": {}})


for p in presets:
    real = p["pvs"]
    entries = json.loads(json.dumps(saved))
    h = _Host(entries, real)
    rows = [(1_000, {pv: (VAL[pv], "") for pv in real})]
    h._compute_custom_pvs_in_rows(rows)
    ok = True
    for d in saved:
        got = rows[0][1][d["name"]][0]
        exp = _expected(d["name"], set(real))
        if (got is None) != (exp is None) or (exp is not None and abs(got - exp) > 1e-9):
            ok = False
            print(f"      {p['name']}: {d['name']} = {got}, expected {exp}")
    check(f"preset {p['name']!r}: every custom PV reads its own PVs", ok)

    # Through the editor and back: the letters may change, the meaning may not.
    chans = h._cpv_dialog_channels()
    dlg = app_main._CustomPVDialog(entries, chans)
    shown = {r["name"].text(): r["expr"].text() for r in dlg._rows}
    dlg._accept()
    back = {d["name"]: _canon(d["expr"], d["bindings"]) for d in dlg.result_pvs}
    check(f"preset {p['name']!r}: the editor keeps every formula's PVs",
          back == meaning)
    if p["name"] in ("Operation", "Ramping"):
        print(f"      shown in {p['name']}: " + ", ".join(
            f"{n} = {e}" for n, e in shown.items()))
    dlg.deleteLater()

# ── 3. renaming a custom PV keeps the customs built on it ─────────────────────
base = {"name": "Base", "expr": "A*2", "bindings": {"A": pv1}}
dep = {"name": "Dep", "expr": "B+1", "bindings": {"B": "Base"}}
chans = [("A", pv1, pv1, True), ("B", "Base", "Base", True), ("C", "Dep", "Dep", True)]
dlg = app_main._CustomPVDialog([base, dep], chans)
dlg._rows[0]["name"].setText("Base renamed")
dlg._accept()
res = {d["name"]: d for d in dlg.result_pvs}
check("rename: the dependant now names the renamed custom",
      res.get("Dep", {}).get("bindings") == {"B": "Base renamed"})
h = _Host(dlg.result_pvs, [pv1])
rows = [(1_000, {pv1: (5.0, "")})]
h._compute_custom_pvs_in_rows(rows)
check("rename: the dependant still computes (5*2+1)",
      rows[0][1]["Dep"][0] == 11.0)
dlg.deleteLater()

# ── 4. SBW4 under both names ───────────────────────────────────────────────────
HAP, L3 = cpva_core.SBW4_HAPLS, cpva_core.SBW4_L3


def _ns(y, m, d, hh=0, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=cpva_core.TZ_PRAGUE).timestamp() * 1e9)


D1, D2, D3 = _ns(2026, 9, 1), _ns(2026, 9, 2), _ns(2026, 9, 3)
ARCH = {
    HAP: [_ns(2026, 8, 31, 23, 0), _ns(2026, 9, 1, 10), _ns(2026, 9, 1, 11)],
    L3:  [_ns(2026, 9, 1, 12), _ns(2026, 9, 2, 9), _ns(2026, 9, 2, 15),
          _ns(2026, 9, 3, 8)],
}
calls = []


def _fake_samples(name, start_ns, end_ns, timeout=10.0, count=None):
    """Like the archiver: everything in range plus one bracket sample each side."""
    calls.append((name, int(start_ns), int(end_ns)))
    ts = ARCH.get(name, [])
    inside = [t for t in ts if start_ns <= t < end_ns]
    before = [t for t in ts if t < start_ns][-1:]
    after = [t for t in ts if t >= end_ns][:1]
    return [{"time": t, "value": float(t % 97), "quality": "Original"}
            for t in before + inside + after]


def _fake_fetch(channels, start_ns, end_ns, **kw):
    """The whole-channel shape, for the Spectra check below."""
    return {ch: _fake_samples(ch, start_ns, end_ns) for ch in channels}, {}, None


orig = cpva_core.cpva_fetch_samples
cpva_core.cpva_fetch_samples = _fake_samples
try:
    out, errs, _rep = cpva_core.cpva_fetch_many_adaptive([L3], D1, D3, preflight=False)
    got = [s["time"] for s in out[L3]]
    hap_calls = [(c[1], c[2]) for c in calls if c[0] == HAP]
    l3_calls = [(c[1], c[2]) for c in calls if c[0] == L3]
    check("SBW4: the HAPLS name is asked first, for every day, even when the L3 "
          "name is the one picked",
          calls[0][0] == HAP
          and cpva_core._coalesce_ranges(hap_calls) == [(D1, D3)], str(hap_calls))
    check("SBW4: the L3 name is asked only for the day HAPLS has nothing (2.9.)",
          cpva_core._coalesce_ranges(l3_calls) == [(D2, D3)], str(l3_calls))
    check("SBW4: day 2 comes from the L3 name, filed under the picked name",
          ARCH[L3][1] in got and ARCH[L3][2] in got)
    check("SBW4: the L3 stray on a HAPLS day and its bracket samples stay out",
          ARCH[L3][0] not in got and ARCH[L3][3] not in got, str(got))
    check("SBW4: the result is in time order", got == sorted(got))

    calls.clear()
    cpva_core.cpva_fetch_many_adaptive([HAP], D1, D2, preflight=False)
    check("SBW4: a window HAPLS covers never asks the L3 name",
          all(c[0] != L3 for c in calls))

    calls.clear()
    cpva_core.cpva_fetch_many_adaptive([pv1], D1, D3, preflight=False)
    check("other PVs: read under their own name only, over the whole window",
          cpva_core._coalesce_ranges([(c[1], c[2]) for c in calls]) == [(D1, D3)]
          and all(c[0] == pv1 for c in calls), str(calls))

    # The archiver's invented grid: a thinned request for a day a name holds
    # nothing for comes back as 144 interpolated points, all one value
    # (measured 12.10.2026 for 12.-29.08.). It must not stop the other name.
    calls.clear()

    def _grid(name, start_ns, end_ns, timeout=10.0, count=None):
        calls.append((name, int(start_ns), int(end_ns)))
        if name == HAP and count:
            step = (end_ns - start_ns) // 144
            return [{"time": start_ns + i * step, "value": [-0.108],
                     "minimum": [-0.108], "maximum": [-0.108],
                     "quality": "Interpolated"} for i in range(144)]
        return _fake_samples(name, start_ns, end_ns, timeout, count)

    cpva_core.cpva_fetch_samples = _grid
    out, _e, _r = cpva_core.cpva_fetch_many_adaptive([HAP], D2, D3, count=2000,
                                                    preflight=False)
    vals = {s["time"] for s in out[HAP]}
    check("SBW4: a flat invented HAPLS grid sends the day to the L3 name",
          any(c[0] == L3 for c in calls) and ARCH[L3][1] in vals
          and not any(isinstance(s["value"], list) for s in out[HAP]),
          f"{len(out[HAP])} samples, calls={[c[0] for c in calls]}")
finally:
    cpva_core.cpva_fetch_samples = orig

orig_lb = cpva_core._last_before_one
asked = []


def _fake_last(ch, before_ns, timeout=None, cancel_fn=None, budget=None,
               strict=False):
    asked.append(ch)
    ts = [t for t in ARCH.get(ch, []) if t < before_ns]
    return {"time": ts[-1], "value": 1.0} if ts else None


cpva_core._last_before_one = _fake_last
try:
    s = cpva_core.cpva_fetch_last_before(L3, _ns(2026, 9, 1, 20))
    check("pre-window: HAPLS is hunted first and has a value that day -> L3 not asked",
          s["time"] == ARCH[HAP][2] and asked == [HAP], str(asked))
    asked.clear()
    s = cpva_core.cpva_fetch_last_before(HAP, _ns(2026, 9, 2, 20))
    check("pre-window: HAPLS silent that day -> the newer L3 value wins",
          s["time"] == ARCH[L3][2] and asked == [HAP, L3])
finally:
    cpva_core._last_before_one = orig_lb

days = cpva_core._prague_days(_ns(2026, 10, 24), _ns(2026, 10, 27))
check("day cut: the clock-change day is 25 hours long",
      [round((b - a) / 3.6e12) for a, b in days] == [24, 25, 24])

orig_sp = sp_t._cpva_fetch_chunked_one
sp_t._cpva_fetch_chunked_one = lambda ch, a, b: _fake_fetch([ch], a, b)[0][ch]
try:
    got = [s["time"] for s in sp_t._cpva_fetch_chunked(HAP, D1, D3)]
    check("Spectra: SBW4 day 2 is filled from the L3 name the same way",
          ARCH[L3][1] in got and ARCH[L3][0] not in got and got == sorted(got))
finally:
    sp_t._cpva_fetch_chunked_one = orig_sp

print()
print("PASS" if not failures else f"FAIL ({len(failures)})")
sys.exit(1 if failures else 0)
