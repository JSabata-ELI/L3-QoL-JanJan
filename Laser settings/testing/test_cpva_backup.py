"""Laser settings — the L3 proxy stands in for an unreachable CPVA archiver.

    python testing/test_cpva_backup.py

No archiver: a local fake answers for the CPVA archiver and for both proxies,
each with its own value, so every reading shows where it came from.
CPVA = 1.0, L3 proxy (the backup) = 2.0, L3BT proxy = 3.0, FCT proxy = 4.0.
The FCT fake also lists "L3-A:X", like the 48 names FCT shares with CPVA: that
one must stay on CPVA.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ls_cpva as A                                             # noqa: E402

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


# ── the fake archive ─────────────────────────────────────────────────────────
HITS = {"cpva": 0, "l3": 0, "l3bt": 0, "fct": 0}
CPVA_MODE = {"mode": "ok"}          # ok | 500 | 503 | slow


def _rows(value):
    return [{"time": 1_000_000_000, "value": [value],
             "severity": {"level": "OK", "hasValue": True}}]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass                        # the client gave up (the timeout check)

    def do_GET(self):
        p = self.path
        if p.startswith(A.CPVA_BASE_PATH):
            HITS["cpva"] += 1
            mode = CPVA_MODE["mode"]
            if mode == "slow":
                time.sleep(1.5)
            if mode in ("500", "503"):
                return self._send(int(mode), {"error": mode})
            if "/channels" in p:
                return self._send(200, ["L3-A:X"])
            return self._send(200, _rows(1.0))
        if p.startswith(A.L3_PROXY_BASE_PATH):
            HITS["l3"] += 1
            if "/channels-by-pattern/" in p:
                return self._send(200, ["L3-A:X"])
            return self._send(200, _rows(2.0))
        if p.startswith(A.PROXY_BASE_PATH):
            HITS["l3bt"] += 1
            if "/channels-by-pattern/" in p:
                return self._send(200, ["L3BT-B:Y"])
            return self._send(200, _rows(3.0))
        if p.startswith(A.FCT_PROXY_BASE_PATH):
            HITS["fct"] += 1
            if "/channels-by-pattern/" in p:
                return self._send(200, ["FCT-C:Z", "L3-A:X"])
            return self._send(200, _rows(4.0))
        self._send(404, {"error": "no such path"})


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
LIVE = f"127.0.0.1:{SERVER.server_address[1]}"
DEAD = "127.0.0.1:1"                # nothing listens there: connection refused


# The fake speaks plain HTTP; the CPVA side of the client speaks HTTPS.
def _plain(host, timeout=None, context=None):
    return http.client.HTTPConnection(host, timeout=timeout)


http.client.HTTPSConnection = _plain

# Both proxies on the one live fake host.
A.PROXY_HOSTS = (LIVE,)
A._proxy_pools.clear()
for _base, _size in A._PROXY_POOL_SIZE.items():
    q = A.queue.LifoQueue(maxsize=_size)
    for _ in range(_size):
        q.put(None)
    A._proxy_pools[(_base, LIVE)] = q


def reset(cpva_host, mode="ok"):
    A.CPVA_HOST = cpva_host
    CPVA_MODE["mode"] = mode
    A._cpva_down_until = 0.0
    A._cpva_names = None
    A._proxy_names = {}
    A._lists_tried_at = 0.0
    A._channels = None
    while not A._pool.empty():
        A._close_quiet(A._pool.get_nowait())
    for _ in range(A._POOL_SIZE):
        A._pool.put(None)
    for k in HITS:
        HITS[k] = 0
    A.STATS["backup"] = 0


def value_of(channel, timeout=1.0):
    rows = A.fetch_samples(channel, 0, 2_000_000_000, timeout=timeout)
    return rows[-1][1] if rows else None


def error_of(channel, timeout=1.0):
    try:
        A.fetch_samples(channel, 0, 2_000_000_000, timeout=timeout)
    except A.CpvaError as exc:
        return str(exc)
    return None


# ── the checks ───────────────────────────────────────────────────────────────

def test_cpva_answering():
    print("the archiver answers: the backup is never asked")
    reset(LIVE)
    check("value from CPVA", value_of("L3-A:X"), 1.0)
    check("L3 proxy samples asked", HITS["l3"], 0)
    check("backup counter", A.STATS["backup"], 0)


def test_refused_goes_to_backup():
    print("the archiver refuses the connection: the L3 proxy answers")
    reset(DEAD)
    check("value from the L3 proxy", value_of("L3-A:X"), 2.0)
    check("backup counter", A.STATS["backup"] >= 1, True)


def test_gateway_error_goes_to_backup_and_latches():
    print("HTTP 503: the L3 proxy answers, and the archiver is left alone a while")
    reset(LIVE, "503")
    A._load_lists()                     # the list load meets the 503 first
    check("list load marked the archiver down", A._cpva_is_down(), True)
    before = HITS["cpva"]
    check("first read from the L3 proxy", value_of("L3-A:X"), 2.0)
    check("CPVA not asked on the first read", HITS["cpva"] - before, 0)
    before = HITS["cpva"]
    check("second read from the L3 proxy", value_of("L3-A:X"), 2.0)
    check("CPVA not asked while it is marked down", HITS["cpva"] - before, 0)
    print("  ... the down mark runs out")
    A._cpva_down_until = 0.0
    CPVA_MODE["mode"] = "ok"
    check("read after the mark from CPVA again", value_of("L3-A:X"), 1.0)


def test_gateway_error_first_read():
    print("HTTP 503 on a cold start: CPVA is asked, then the backup")
    reset(LIVE, "503")
    A._cpva_names = {"L3-A:X"}          # skip the list load
    A._proxy_names = {A.PROXY_BASE_PATH: {"L3BT-B:Y"}, A.FCT_PROXY_BASE_PATH: {"FCT-C:Z"}}
    check("value from the L3 proxy", value_of("L3-A:X"), 2.0)
    check("CPVA asked twice (one retry)", HITS["cpva"], 2)


def test_http_500_is_not_backed_up():
    print("HTTP 500 is an answer (too big a window): no backup")
    reset(LIVE, "500")
    A._cpva_names = {"L3-A:X"}
    A._proxy_names = {A.PROXY_BASE_PATH: {"L3BT-B:Y"}, A.FCT_PROXY_BASE_PATH: {"FCT-C:Z"}}
    err = error_of("L3-A:X")
    check("error raised", err is not None and "HTTP 500" in err, True)
    check("L3 proxy not asked", HITS["l3"], 0)
    check("archiver not marked down", A._cpva_is_down(), False)


def test_timeout_is_not_backed_up():
    print("a slow archiver times out: no backup (same database behind both)")
    reset(LIVE, "slow")
    A._cpva_names = {"L3-A:X"}
    A._proxy_names = {A.PROXY_BASE_PATH: {"L3BT-B:Y"}, A.FCT_PROXY_BASE_PATH: {"FCT-C:Z"}}
    err = error_of("L3-A:X", timeout=0.5)
    check("timeout raised", err is not None and "timeout" in err, True)
    check("L3 proxy not asked", HITS["l3"], 0)
    check("archiver not marked down", A._cpva_is_down(), False)


def test_channel_list_falls_back():
    print("the channel list comes from the L3 proxy when the archiver is gone")
    reset(DEAD)
    names = A.fetch_channels(timeout=1.0)
    check("L3 name listed", "L3-A:X" in names, True)
    check("L3BT name listed", "L3BT-B:Y" in names, True)
    check("FCT name listed", "FCT-C:Z" in names, True)


def test_l3bt_routing_unchanged():
    print("an L3BT-only channel still goes to the L3BT proxy")
    reset(LIVE)
    check("value from the L3BT proxy", value_of("L3BT-B:Y"), 3.0)
    reset(DEAD)
    check("... also while the archiver is gone", value_of("L3BT-B:Y"), 3.0)


def test_fct_routing():
    print("an FCT-only channel goes to the FCT proxy; a shared one stays on CPVA")
    reset(LIVE)
    check("value from the FCT proxy", value_of("FCT-C:Z"), 4.0)
    check("shared name read from CPVA", value_of("L3-A:X"), 1.0)
    check("picker offers the FCT name", "FCT-C:Z" in A.fetch_channels(timeout=1.0), True)


def test_both_down_says_both():
    print("archiver and backup both gone: one error that names both")
    reset(DEAD)
    A._cpva_names = {"L3-A:X"}
    A._proxy_names = {A.PROXY_BASE_PATH: {"L3BT-B:Y"}, A.FCT_PROXY_BASE_PATH: {"FCT-C:Z"}}
    A._proxy_pools.clear()
    q = A.queue.LifoQueue(maxsize=1)
    q.put(None)
    A._proxy_pools[(A.L3_PROXY_BASE_PATH, DEAD)] = q
    saved = A.PROXY_HOSTS
    A.PROXY_HOSTS = (DEAD,)
    try:
        err = error_of("L3-A:X")
    finally:
        A.PROXY_HOSTS = saved
    check("error names the backup", err is not None and "backup" in err, True)


if __name__ == "__main__":
    test_cpva_answering()
    test_refused_goes_to_backup()
    test_gateway_error_goes_to_backup_and_latches()
    test_gateway_error_first_read()
    test_http_500_is_not_backed_up()
    test_timeout_is_not_backed_up()
    test_channel_list_falls_back()
    test_l3bt_routing_unchanged()
    test_fct_routing()
    test_both_down_says_both()
    SERVER.shutdown()
    print()
    print("FAILED:" if FAILED else "all passed")
    for f in FAILED:
        print("  " + f)
    sys.exit(1 if FAILED else 0)
