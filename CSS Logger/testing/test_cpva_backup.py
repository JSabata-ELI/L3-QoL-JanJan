"""CSS Logger — the L3 proxy stands in for an unreachable CPVA archiver, and
L3BT / FCT channels are listed and read from their own proxies.

    python testing/test_cpva_backup.py

No archiver: a local fake answers for the CPVA archiver and for the proxies,
each with its own value, so every reading shows where it came from.
CPVA = 1.0, L3 proxy (the backup) = 2.0, L3BT proxy = 3.0, FCT proxy = 4.0.
The FCT fake also lists "L3-A:X", like the 48 names FCT shares with CPVA: that
one must stay on CPVA.

Covered (cpva_core._get_json_or_backup):
  [1] archiver answering → the proxy is never asked;
  [2] connection refused / HTTP 503 → the proxy answers (with `count=` passed
      on), the archiver is left alone for CPVA_DOWN_S, then asked again;
  [3] HTTP 500 and a read timeout are NOT backed up, and the 500 still splits;
  [4] on the backup, the proxy's own 500 still splits; both gone is NOT split;
  [5] the channel list and the Spectra tab's fetch fall back too.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests                                                 # noqa: E402

import cpva_core as C                                           # noqa: E402

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


# ── the fake archive ─────────────────────────────────────────────────────────
HITS = {"cpva": 0, "l3": 0, "l3bt": 0, "fct": 0}
LAST_PROXY_QUERY = {}
CPVA_MODE = {"mode": "ok"}          # ok | 500 | 503 | slow
PROXY_MODE = {"mode": "ok"}         # ok | 500
CPVA_PATH = "/api/1.0/cpva"
L3_PATH = "/l3proxy/archive-access/api/1.0/archive/1"
L3BT_PATH = "/l3btproxy/archive-access/api/1.0/archive/1"
FCT_PATH = "/fctproxy/archive-access/api/1.0/archive/1"


def _rows(value):
    return [{"time": 1_000_000_000, "value": [value], "quality": "Original",
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
        if p.startswith(CPVA_PATH):
            HITS["cpva"] += 1
            mode = CPVA_MODE["mode"]
            if mode == "slow":
                time.sleep(1.5)
            if mode in ("500", "503"):
                return self._send(int(mode), {"error": mode})
            if "/channels" in p:
                return self._send(200, ["L3-A:X"])
            return self._send(200, _rows(1.0))
        if p.startswith(L3_PATH):
            HITS["l3"] += 1
            if "/channels-by-pattern/" in p:
                return self._send(200, ["L3-A:X", "L3-A:Z"])
            if PROXY_MODE["mode"] == "500":
                return self._send(500, {"error": "too big"})
            LAST_PROXY_QUERY.clear()
            LAST_PROXY_QUERY.update(
                urllib.parse.parse_qs(urllib.parse.urlsplit(p).query))
            return self._send(200, _rows(2.0))
        if p.startswith(L3BT_PATH):
            HITS["l3bt"] += 1
            if "/channels-by-pattern/" in p:
                return self._send(200, ["L3BT-B:Y"])
            return self._send(200, _rows(3.0))
        if p.startswith(FCT_PATH):
            HITS["fct"] += 1
            if "/channels-by-pattern/" in p:
                return self._send(200, ["FCT-C:Z", "L3-A:X"])
            return self._send(200, _rows(4.0))
        self._send(404, {"error": "no such path"})


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
LIVE = f"127.0.0.1:{SERVER.server_address[1]}"
DEAD = "127.0.0.1:1"                # nothing listens there: connection refused


def reset(cpva_host, mode="ok", proxy_host=LIVE):
    C.CPVA_BASE_URL = f"http://{cpva_host}{CPVA_PATH}"
    C.L3_PROXY_BASE_URLS = (f"http://{proxy_host}{L3_PATH}",)
    C.EXTRA_ARCHIVES = {"l3bt": (f"http://{proxy_host}{L3BT_PATH}",),
                        "fct": (f"http://{proxy_host}{FCT_PATH}",)}
    # The lists as if already loaded, so the counts below are the reads only.
    C._cpva_names = {"L3-A:X", "L3-A:Z"}
    C._proxy_names = {"l3bt": {"L3BT-B:Y"}, "fct": {"FCT-C:Z", "L3-A:X"}}
    C._lists_tried_at = 0.0
    CPVA_MODE["mode"] = mode
    PROXY_MODE["mode"] = "ok"
    C._cpva_down_until = 0.0
    C.BACKUP_STATS["backup"] = 0
    for k in HITS:
        HITS[k] = 0


def value_of(timeout=2.0, count=None):
    rows = C.cpva_fetch_samples("L3-A:X", 0, 2_000_000_000, timeout=timeout,
                                count=count)
    return rows[-1]["value"][0] if rows else None


def error_of(timeout=2.0):
    try:
        C.cpva_fetch_samples("L3-A:X", 0, 2_000_000_000, timeout=timeout)
    except Exception as exc:
        return exc
    return None


# ── the checks ───────────────────────────────────────────────────────────────

def test_cpva_answering():
    print("[1] the archiver answers: the backup is never asked")
    reset(LIVE)
    check("value from CPVA", value_of(), 1.0)
    check("L3 proxy asked", HITS["l3"], 0)


def test_refused_goes_to_backup():
    print("[2] the archiver refuses the connection: the L3 proxy answers")
    reset(DEAD)
    check("value from the L3 proxy", value_of(count=2000), 2.0)
    check("count passed on", LAST_PROXY_QUERY.get("count"), ["2000"])
    check("no channelName in the proxy query", "channelName" in LAST_PROXY_QUERY, False)
    check("backup counter", C.BACKUP_STATS["backup"], 1)


def test_gateway_error_latches():
    print("[2] HTTP 503: the L3 proxy answers, the archiver is left alone a while")
    reset(LIVE, "503")
    check("first read from the L3 proxy", value_of(), 2.0)
    check("CPVA asked once", HITS["cpva"], 1)
    check("second read from the L3 proxy", value_of(), 2.0)
    check("CPVA not asked while it is marked down", HITS["cpva"], 1)
    print("  ... the down mark runs out")
    C._cpva_down_until = 0.0
    CPVA_MODE["mode"] = "ok"
    check("read after the mark from CPVA again", value_of(), 1.0)


def test_http_500_is_not_backed_up():
    print("[3] HTTP 500 is the archiver's 'too big': no backup, still split")
    reset(LIVE, "500")
    exc = error_of()
    check("HTTP 500 raised", getattr(getattr(exc, "response", None),
                                     "status_code", None), 500)
    check("still splittable", C._is_splittable_error(exc), True)
    check("L3 proxy not asked", HITS["l3"], 0)


def test_timeout_is_not_backed_up():
    print("[3] a slow archiver times out: no backup (same database behind both)")
    reset(LIVE, "slow")
    exc = error_of(timeout=0.5)
    check("read timeout raised", isinstance(exc, requests.ReadTimeout), True)
    check("L3 proxy not asked", HITS["l3"], 0)


def test_proxy_500_still_splits():
    print("[4] on the backup, the proxy's own 500 still means 'split'")
    reset(DEAD)
    PROXY_MODE["mode"] = "500"
    exc = error_of()
    check("HTTP 500 raised", getattr(getattr(exc, "response", None),
                                     "status_code", None), 500)
    check("splittable", C._is_splittable_error(exc), True)


def test_both_down_is_not_split():
    print("[4] archiver and backup both gone: one error, never fanned out")
    reset(DEAD, proxy_host=DEAD)
    exc = error_of()
    check("ConnectionError", isinstance(exc, requests.ConnectionError), True)
    check("names the backup", "backup" in str(exc), True)
    check("not splittable", C._is_splittable_error(exc), False)


def test_channel_list_falls_back():
    print("[5] the channel list comes from the L3 proxy when the archiver is gone")
    reset(DEAD)
    check("list", C.cpva_fetch_channels(timeout=2.0),
          ["L3-A:X", "L3-A:Z", "L3BT-B:Y", "FCT-C:Z"])


def test_spectra_fetch_falls_back():
    print("[5] the Spectra tab's own fetch and listing fall back too")
    import sp_t
    reset(DEAD)
    sp_t.CPVA_URL = C.CPVA_BASE_URL
    rows = sp_t._cpva_fetch("L3-A:X", 0, 2_000_000_000)
    check("Spectra value from the L3 proxy", rows[-1]["value"][0], 2.0)
    check("Spectra listing", sp_t._cpva_load_all_channels(),
          ["FCT-C:Z", "L3-A:X", "L3-A:Z", "L3BT-B:Y"])
    rows = sp_t._cpva_fetch("FCT-C:Z", 0, 2_000_000_000)
    check("Spectra reads an FCT name from the FCT proxy", rows[-1]["value"][0], 4.0)


def test_extra_archives():
    print("[6] L3BT and FCT names: listed, and read from their own proxy")
    reset(LIVE)
    C._cpva_names = None
    C._proxy_names = {}
    names = C.cpva_fetch_channels(timeout=2.0)
    check("picker offers L3BT and FCT names",
          {"L3BT-B:Y", "FCT-C:Z"} <= set(names), True)
    check("no name twice", len(names), len(set(names)))

    def val(ch, count=None):
        rows = C.cpva_fetch_samples(ch, 0, 2_000_000_000, timeout=2.0, count=count)
        return rows[-1]["value"][0] if rows else None

    check("L3BT name from the L3BT proxy", val("L3BT-B:Y"), 3.0)
    check("FCT name from the FCT proxy (thinned)", val("FCT-C:Z", count=500), 4.0)
    check("name shared with FCT stays on CPVA", val("L3-A:X"), 1.0)
    print("  ... routing loads its lists by itself, without a picker")
    reset(LIVE)
    C._cpva_names = None
    C._proxy_names = {}
    check("FCT name read on a cold start", val("FCT-C:Z"), 4.0)
    reset(DEAD)
    check("FCT read while the archiver is gone", val("FCT-C:Z"), 4.0)


if __name__ == "__main__":
    test_cpva_answering()
    test_refused_goes_to_backup()
    test_gateway_error_latches()
    test_http_500_is_not_backed_up()
    test_timeout_is_not_backed_up()
    test_proxy_500_still_splits()
    test_both_down_is_not_split()
    test_channel_list_falls_back()
    test_spectra_fetch_falls_back()
    test_extra_archives()
    SERVER.shutdown()
    print()
    print("FAILED:" if FAILED else "all passed")
    for f in FAILED:
        print("  " + f)
    sys.exit(1 if FAILED else 0)
