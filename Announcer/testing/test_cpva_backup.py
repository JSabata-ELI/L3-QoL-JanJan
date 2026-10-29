"""Announcer — the L3 proxy stands in for an unreachable CPVA archiver, and
L3BT / FCT channels are listed and read from their own proxies.

    python testing/test_cpva_backup.py

No archiver: a local fake answers for the CPVA archiver and for the L3 proxy,
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

import ann_cpva as A                                            # noqa: E402

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
                return self._send(200, ["L3-A:X", "L3-A:Z"])
            return self._send(200, _rows(2.0))
        if p.startswith(A.L3BT_PROXY_BASE_PATH):
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


def _proxy_on(host):
    A.PROXY_HOSTS = (host,)
    A._proxy_pools.clear()
    for base, size in A._PROXY_POOL_SIZE.items():
        q = A.queue.LifoQueue(maxsize=size)
        for _ in range(size):
            q.put(None)
        A._proxy_pools[(base, host)] = q


def reset(cpva_host, mode="ok"):
    A.CPVA_HOST = cpva_host
    CPVA_MODE["mode"] = mode
    A._cpva_down_until = 0.0
    A._channels = None
    # The lists as if already loaded, so the counts below are the reads only.
    A._cpva_names = {"L3-A:X", "L3-A:Z"}
    A._proxy_names = {A.L3BT_PROXY_BASE_PATH: {"L3BT-B:Y"},
                      A.FCT_PROXY_BASE_PATH: {"FCT-C:Z", "L3-A:X"}}
    A._lists_tried_at = 0.0
    while not A._pool.empty():
        A._close_quiet(A._pool.get_nowait())
    for _ in range(A._POOL_SIZE):
        A._pool.put(None)
    _proxy_on(LIVE)
    for k in HITS:
        HITS[k] = 0
    A.STATS["backup"] = 0


def value_of(timeout=1.0, channel="L3-A:X"):
    rows = A.fetch_samples(channel, 0, 2_000_000_000, timeout=timeout)
    return rows[-1][1] if rows else None


def error_of(timeout=1.0):
    try:
        A.fetch_samples("L3-A:X", 0, 2_000_000_000, timeout=timeout)
    except A.CpvaError as exc:
        return str(exc)
    return None


# ── the checks ───────────────────────────────────────────────────────────────

def test_cpva_answering():
    print("the archiver answers: the backup is never asked")
    reset(LIVE)
    check("value from CPVA", value_of(), 1.0)
    check("L3 proxy asked", HITS["l3"], 0)


def test_refused_goes_to_backup():
    print("the archiver refuses the connection: the L3 proxy answers")
    reset(DEAD)
    check("value from the L3 proxy", value_of(), 2.0)
    check("backup counter", A.STATS["backup"], 1)


def test_gateway_error_latches():
    print("HTTP 503: the L3 proxy answers, and the archiver is left alone a while")
    reset(LIVE, "503")
    check("first read from the L3 proxy", value_of(), 2.0)
    check("CPVA asked twice (one retry)", HITS["cpva"], 2)
    check("second read from the L3 proxy", value_of(), 2.0)
    check("CPVA not asked while it is marked down", HITS["cpva"], 2)
    print("  ... the down mark runs out")
    A._cpva_down_until = 0.0
    CPVA_MODE["mode"] = "ok"
    check("read after the mark from CPVA again", value_of(), 1.0)


def test_http_500_is_not_backed_up():
    print("HTTP 500 is an answer: no backup")
    reset(LIVE, "500")
    err = error_of()
    check("error raised", err is not None and "HTTP 500" in err, True)
    check("L3 proxy not asked", HITS["l3"], 0)


def test_timeout_is_not_backed_up():
    print("a slow archiver times out: no backup (same database behind both)")
    reset(LIVE, "slow")
    err = error_of(timeout=0.5)
    check("timeout raised", err is not None and "timeout" in err, True)
    check("L3 proxy not asked", HITS["l3"], 0)


def test_channel_list_falls_back():
    print("the channel list comes from the L3 proxy when the archiver is gone")
    reset(DEAD)
    check("list", A.fetch_channels(timeout=1.0),
          ["FCT-C:Z", "L3-A:X", "L3-A:Z", "L3BT-B:Y"])


def test_extra_archives():
    print("L3BT and FCT names: listed, and read from their own proxy")
    reset(LIVE)
    A._cpva_names = None
    A._proxy_names = {}
    names = A.fetch_channels(timeout=1.0)
    check("picker offers L3BT and FCT names",
          {"L3BT-B:Y", "FCT-C:Z"} <= set(names), True)
    check("L3BT name from the L3BT proxy", value_of(channel="L3BT-B:Y"), 3.0)
    check("FCT name from the FCT proxy", value_of(channel="FCT-C:Z"), 4.0)
    check("name shared with FCT stays on CPVA", value_of(), 1.0)
    reset(DEAD)
    check("FCT read while the archiver is gone", value_of(channel="FCT-C:Z"), 4.0)


def test_both_down_says_both():
    print("archiver and backup both gone: one error that names both")
    reset(DEAD)
    _proxy_on(DEAD)
    err = error_of()
    check("error names the backup", err is not None and "backup" in err, True)


if __name__ == "__main__":
    test_cpva_answering()
    test_refused_goes_to_backup()
    test_gateway_error_latches()
    test_http_500_is_not_backed_up()
    test_timeout_is_not_backed_up()
    test_channel_list_falls_back()
    test_extra_archives()
    test_both_down_says_both()
    SERVER.shutdown()
    print()
    print("FAILED:" if FAILED else "all passed")
    for f in FAILED:
        print("  " + f)
    sys.exit(1 if FAILED else 0)
