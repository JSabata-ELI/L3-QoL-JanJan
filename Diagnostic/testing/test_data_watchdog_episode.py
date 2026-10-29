"""The "Data connection" alert treats outages close together as one episode:
one ALARM when it starts, a reminder only every 12 h, and one OK only after
data has flowed without a gap for 15 min. Pinned offline, no window, with a
fake clock.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor_tab as mt                                          # noqa: E402

SEC = 1_000_000_000
POLL = 30 * SEC
T0 = 1_758_600_000 * SEC

UP = (20.0, "DegC", T0, "", 0, 20.0, (mt._EMPTY_T, mt._EMPTY_V))
DOWN = (None, "", 0, "connection refused", 0, None, (mt._EMPTY_T, mt._EMPTY_V))


class _Rig:
    def __init__(self, monkeypatch):
        self.now = T0
        monkeypatch.setattr(mt.api, "now_ns", lambda: self.now)
        self.sent = []
        w = SimpleNamespace(
            _monitoring=True,
            pvs=[SimpleNamespace(name="PV-A"), SimpleNamespace(name="PV-B")],
            settings=dict(mt.DEFAULT_SETTINGS),
            _watchdog_fail_streak=0, _watchdog_bad=False,
            _watchdog_since_ns=0, _watchdog_last_alarm_ns=0,
            _watchdog_ok_since_ns=0,
            _watchdog_hhmm=mt.MonitorWidget._watchdog_hhmm,
        )
        w._send_watchdog_alert = \
            lambda level, reason, prev=None: self.sent.append((level, reason))
        self.w = w

    def poll(self, up: bool, n: int = 1):
        for _ in range(n):
            res = UP if up else DOWN
            mt.MonitorWidget._check_data_watchdog(
                self.w, {"PV-A": res, "PV-B": res})
            self.now += POLL

    def levels(self):
        return [lvl for lvl, _ in self.sent]


def test_flapping_gives_one_alarm_and_one_ok(monkeypatch):
    """24.10.2026: down 2 min, up 1 min, down 3 min, up 11 min, down... —
    six messages before, two now."""
    r = _Rig(monkeypatch)
    r.poll(False, 4)
    r.poll(True, 2)
    r.poll(False, 6)
    r.poll(True, 22)
    r.poll(False, 4)
    assert r.levels() == [mt.AlertLevel.ALARM], "still one episode, no OK yet"
    r.poll(True, 31)                      # 15 min and a bit, unbroken
    assert r.levels() == [mt.AlertLevel.ALARM, mt.AlertLevel.OK]
    assert "stable for" in r.sent[-1][1]


def test_a_short_glitch_after_recovery_starts_a_new_episode(monkeypatch):
    r = _Rig(monkeypatch)
    r.poll(False, 2)
    r.poll(True, 31)
    r.poll(False, 2)
    assert r.levels() == [mt.AlertLevel.ALARM, mt.AlertLevel.OK,
                          mt.AlertLevel.ALARM]


def test_one_failed_poll_says_nothing(monkeypatch):
    r = _Rig(monkeypatch)
    r.poll(False, 1)
    r.poll(True, 40)
    assert r.sent == []


def test_a_long_outage_reminds_every_12_hours_not_sooner(monkeypatch):
    r = _Rig(monkeypatch)
    r.poll(False, 2)
    polls_12h = int(12 * 3600 * SEC / POLL)
    r.poll(False, polls_12h - 2)
    assert r.levels() == [mt.AlertLevel.ALARM], "no reminder before 12 h"
    r.poll(False, 2)
    assert r.levels() == [mt.AlertLevel.ALARM, mt.AlertLevel.ALARM]
    assert "Still no data" in r.sent[-1][1]
    r.poll(False, polls_12h - 2)
    assert len(r.sent) == 2
    r.poll(False, 2)
    assert len(r.sent) == 3


def test_ok_waits_for_the_full_hold(monkeypatch):
    r = _Rig(monkeypatch)
    r.poll(False, 2)
    r.poll(True, 29)                      # 14.5 min
    assert r.levels() == [mt.AlertLevel.ALARM]
    r.poll(True, 2)
    assert r.levels()[-1] == mt.AlertLevel.OK
