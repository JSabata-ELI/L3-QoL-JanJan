"""A PV with Show unticked is not read from the archiver.

Asked for on 26.10.2026: a PV nobody watches must not slow Live down. Checked:

1. A load reads only the shown PVs; the hidden one keeps its PV-table row
   with no samples.
2. A Live tick asks only for the shown PVs.
3. Ticking Show again reads exactly that PV and draws it in.
4. Unticking Show lets the PV's samples go.
5. A hidden PV that a Condition or a shown custom PV needs is still read.

No network: the archiver calls are replaced by recorders over the smoke
test's synthetic data. Headless (offscreen), no window takes the focus:

    python testing/test_hidden_pv_not_fetched.py
"""
import os
import sys
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import test_smoke as smoke                      # noqa: E402
import cpva_core                                # noqa: E402

FAILURES: "list[str]" = []
PV_A = "L3-TEST-A:Energy"
PV_B = "L3-TEST-B:Energy"


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


ASKED: "list[list[str]]" = []
_orig_adaptive = cpva_core.cpva_fetch_many_adaptive


def _rec_adaptive(pvs, *a, **k):
    ASKED.append(list(pvs))
    return _orig_adaptive(pvs, *a, **k)


def _rec_chunked(pvs, *a, **k):
    ASKED.append(list(pvs))
    return {}, {}


cpva_core.cpva_fetch_many_adaptive = _rec_adaptive
cpva_core.cpva_fetch_many_chunked = _rec_chunked
cpva_core.cpva_fetch_last_before_many = lambda *a, **k: {}


def main() -> int:
    print("test_hidden_pv_not_fetched")
    smoke._install_network_mock()
    smoke._install_dialog_mocks()
    w = smoke._new_widget()
    smoke._set_pvs(w, [PV_A, PV_B])
    w._pv_settings.setdefault(PV_B, {})["show"] = False

    # 1. A load.
    ASKED.clear()
    smoke._load_and_wait(w)
    check("the load reads only the shown PV", ASKED == [[PV_A]], str(ASKED))
    check("the hidden PV keeps its row", PV_B in w._pv_order)
    check("the hidden PV has no samples", not w._samples_by_pv.get(PV_B))
    check("the shown PV has samples", bool(w._samples_by_pv.get(PV_A)))
    check("…and is the only one drawn", list(w._graph_pvs) == [PV_A],
          str(w._graph_pvs))

    # 3. Show ticked again: only that PV is read, and drawn.
    ASKED.clear()
    w._pv_settings[PV_B]["show"] = True
    w._sync_fetched_to_show()
    smoke._pump_until(lambda: not getattr(w, "_catchup_busy", False))
    smoke._app.processEvents()
    check("ticking Show reads exactly that PV", ASKED == [[PV_B]], str(ASKED))
    check("…its samples arrive", bool(w._samples_by_pv.get(PV_B)))
    check("…and it is drawn", PV_B in w._graph_pvs, str(w._graph_pvs))

    # 4. Show unticked: its samples go.
    w._pv_settings[PV_A]["show"] = False
    w._sync_fetched_to_show()
    check("unticking Show drops the samples", not w._samples_by_pv.get(PV_A))
    check("…and it is no longer counted as read", PV_A not in w._fetched_pvs)

    # 5. Still needed although hidden.
    w._conditions = [{"pv": PV_A, "min": 0, "max": 1e9, "enabled": True}]
    check("a Condition channel is read although hidden",
          PV_A in w._fetch_pv_names())
    w._conditions = []
    w._custom_pvs = [{"name": "SUM", "expr": "a + b",
                      "bindings": {"a": PV_A, "b": PV_B}}]
    w._pv_settings.setdefault("SUM", {})["show"] = True
    check("a shown custom PV's source is read although hidden",
          PV_A in w._fetch_pv_names())
    w._pv_settings["SUM"]["show"] = False
    check("…but not when the custom PV is hidden too",
          PV_A not in w._fetch_pv_names())
    w._custom_pvs = []

    # 2. A Live tick.
    ASKED.clear()
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    w._live_last_ts = None
    w._live_tick()
    smoke._pump_until(lambda: bool(ASKED))
    check("a Live tick asks only for the shown PV", ASKED == [[PV_B]], str(ASKED))
    w._stop_live()

    w.close()
    print("FAILED: " + "; ".join(FAILURES) if FAILURES else "ALL PASS")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
