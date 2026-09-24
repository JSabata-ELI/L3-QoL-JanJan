"""Tests for asking the Webex bot about a GROUP of PVs.

`/list PLFE` used to answer with every configured PV, because /list ignored
whatever was typed after it, and `/status PLFE` answered "ambiguous" and
nothing else. A fragment that fits several PVs is a filter, not a mistake: the
reply is the matching part of the list.

Two halves:

* the pure matcher in bot_commands (tokens, AND, ranked) — no Qt, no network;
* the replies themselves, rendered offline on a stand-in object carrying only
  the few attributes they read.

Run with:  python testing/test_pv_filter.py
"""

import os
import sys
import types
from collections import deque

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

import bot_commands as bc  # noqa: E402
import monitor_tab as mt  # noqa: E402
from alerting import AlertLevel  # noqa: E402

SEC = 1_000_000_000
MIN = 60 * SEC
NOW = 1_000_000 * SEC

_APP = QApplication.instance() or QApplication([])

# A slice of the real configuration: PLFE pressures next to names that must NOT
# come back for "plfe", plus two chillers so a second group can be asked for.
CONFIG = [
    ("L3-UTIL-CHILL-DA1:Temp", "DA1 Chiller"),
    ("L3-UTIL-CHILL-DA2:Temp", "DA2 Chiller"),
    ("L3-UTIL-CHILL-HE:Temp", "Helium Chiller"),
    ("L3-UTIL-DAQ-PresVRTPL1:Press", "PLFE VRT1"),
    ("L3-UTIL-DAQ-PresVRTPL2:Press", "PLFE VRT2"),
    ("L3-UTIL-DAQ-PresVRTPL3:Press", "PLFE VRT3"),
    ("L3-UTIL-DAQ-PresVRTPL4:Press", "PLFE VRT4"),
    ("L3-UTIL-DAQ-PresVRTGPL1:Press", "GPL Transport VRT1"),
    ("L3-UTIL-DAQ-PresAlpha:Press", "Alpha Vessel"),
]


def _pvs(enabled=True):
    return [mt.PVConfig(name=n, display_name=d, enabled=enabled)
            for n, d in CONFIG]


def _win(pvs=None, runtime=None, monitoring=True):
    """Stand-in for MonitorWidget carrying what the list replies read."""
    win = types.SimpleNamespace(
        settings=dict(mt.DEFAULT_SETTINGS),
        pvs=list(pvs if pvs is not None else _pvs()),
        runtime=dict(runtime or {}),
        _monitoring=monitoring,
        _refresh_bad=False,
        _refresh_bad_since_ns=0,
        _last_poll_ok_ns=NOW,
        _poll_inflight=False,
        _poll_started_ns=0,
        _mem_start_ns=NOW - 3600 * SEC,
        _menu_error="",
    )
    for name in ("_find_pv", "_find_pvs", "_resolve_pvs", "_cmd_list",
                 "_cmd_status", "_cmd_alarms", "_status_line",
                 "_pv_stale_note", "_frozen_check_on", "_sample_age_limit_s",
                 "_refresh_limit_s", "_refresh_age_s",
                 "_refresh_fault", "_refresh_note_short", "_freshness_header",
                 "_freshness_footer", "_menu_signin_note"):
        setattr(win, name, types.MethodType(getattr(mt.MonitorWidget, name), win))
    return win


def _rt(value, level=AlertLevel.OK, age_s=30):
    rt = mt.PVRuntime(current_value=value,
                      history=deque([(NOW - i * MIN, value) for i in range(5)]),
                      last_update_ns=NOW - int(age_s) * SEC,
                      data_ts_ns=NOW - int(age_s) * SEC)
    rt.live_level = level
    rt.alert.level = level
    return rt


def _clock(ns=NOW):
    mt.api.now_ns = lambda: ns


_clock()


# ---------------------------------------------------------------------------
# The matcher
# ---------------------------------------------------------------------------

def test_fragment_takes_the_whole_group():
    hits = bc.search_pvs(_pvs(), "plfe")
    assert [p.display_name for p in hits] == ["PLFE VRT1", "PLFE VRT2",
                                              "PLFE VRT3", "PLFE VRT4"]


def test_several_words_are_and_matched():
    hits = bc.search_pvs(_pvs(), "plfe vrt3")
    assert [p.display_name for p in hits] == ["PLFE VRT3"]
    assert bc.search_pvs(_pvs(), "plfe alpha") == []


def test_words_may_be_typed_in_any_order():
    """A wrong guess at the order must never hide a PV."""
    assert [p.display_name for p in bc.search_pvs(_pvs(), "vrt3 plfe")] \
        == ["PLFE VRT3"]


def test_the_epics_name_counts_too():
    hits = bc.search_pvs(_pvs(), "chill")
    assert [p.display_name for p in hits] == ["DA1 Chiller", "DA2 Chiller",
                                              "Helium Chiller"]


def test_the_best_hit_comes_first():
    """'da1' sits in one display name and in no other; the group order holds."""
    assert bc.search_pvs(_pvs(), "da1")[0].display_name == "DA1 Chiller"
    ranked = bc.search_pvs(_pvs(), "vrt1")
    assert ranked[0].display_name == "PLFE VRT1"


def test_a_query_that_fits_nothing_returns_nothing():
    assert bc.search_pvs(_pvs(), "sbw4") == []
    assert bc.search_pvs(_pvs(), "") == []


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------

def test_list_without_a_filter_is_the_whole_list():
    reply = _win()._cmd_list([])
    assert reply.startswith("**PVs:**")
    assert reply.count("\n- ") == len(CONFIG)


def test_list_with_a_filter_is_only_the_group():
    reply = _win()._cmd_list(["plfe"])
    assert "PLFE VRT1" in reply and "PLFE VRT4" in reply
    assert "Chiller" not in reply and "Alpha" not in reply
    assert "4 of 9" in reply          # says it is a part of the list


def test_list_all_is_the_whole_list():
    assert _win()._cmd_list(["all"]).startswith("**PVs:**")


def test_list_of_something_unknown_says_so_and_points_at_the_full_list():
    reply = _win()._cmd_list(["sbw4"])
    assert "no PV matches 'sbw4'" in reply and "`/list`" in reply


def test_list_of_two_groups_keeps_both():
    reply = _win()._cmd_list(["plfe", "chiller"])
    assert reply.count("\n- ") == 7
    assert "Alpha" not in reply


# ---------------------------------------------------------------------------
# /status and /alarms
# ---------------------------------------------------------------------------

def test_status_of_a_group_shows_every_pv_in_it():
    pvs = _pvs()
    win = _win(pvs, {p.name: _rt(1.0 + i) for i, p in enumerate(pvs)})
    reply = win._cmd_status(["plfe"])
    assert reply.count("- **") == 4
    assert "Chiller" not in reply
    assert "4 of 9 PVs" in reply


def test_status_of_a_group_is_not_an_ambiguity_complaint():
    win = _win()
    assert "ambiguous" not in win._cmd_status(["plfe"])


def test_alarms_can_be_asked_about_one_group():
    pvs = _pvs()
    runtime = {p.name: _rt(1.0) for p in pvs}
    runtime["L3-UTIL-CHILL-DA1:Temp"] = _rt(99.0, AlertLevel.ALARM)
    runtime["L3-UTIL-DAQ-PresVRTPL2:Press"] = _rt(0.5, AlertLevel.WARNING)
    win = _win(pvs, runtime)

    everything = win._cmd_alarms()
    assert "DA1 Chiller" in everything and "PLFE VRT2" in everything

    only_plfe = win._cmd_alarms(["plfe"])
    assert "PLFE VRT2" in only_plfe and "DA1 Chiller" not in only_plfe

    quiet = win._cmd_alarms(["alpha"])
    assert quiet.startswith("✅") and "alpha" in quiet


def test_alarms_of_a_group_with_alerting_off_says_that_instead_of_ok():
    win = _win(_pvs(enabled=False))
    reply = win._cmd_alarms(["plfe"])
    assert "Alerting is off" in reply and "✅" not in reply


# ---------------------------------------------------------------------------
# The single-PV commands keep their own rule
# ---------------------------------------------------------------------------

def test_the_live_graph_still_wants_one_pv_and_names_the_candidates():
    pv, err = _win()._find_pv("plfe")
    assert pv is None
    assert "matches several PVs" in err and "PLFE VRT1" in err


def test_an_exact_name_wins_over_a_group_hit():
    win = _win()
    pv, err = win._find_pv("PLFE VRT1")
    assert err == "" and pv.display_name == "PLFE VRT1"


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
