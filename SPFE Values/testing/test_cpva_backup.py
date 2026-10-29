"""SPFE Values -- the L3 proxy stands in for an unreachable CPVA archiver.

    python -m pytest testing/test_cpva_backup.py
    python testing/test_cpva_backup.py

No archiver: a local fake answers for the CPVA archiver and for the L3 proxy,
each with its own value, so every reading shows where it came from.
CPVA = 1.0, L3 proxy (the backup) = 2.0.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests                                                 # noqa: E402

import spfe_core as C                                           # noqa: E402

HITS = {"cpva": 0, "l3": 0}
CPVA_MODE = {"mode": "ok"}          # ok | 500 | 503 | slow
CPVA_PATH = "/api/1.0/cpva"
L3_PATH = "/l3proxy/archive-access/api/1.0/archive/1"


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
        if p.startswith(CPVA_PATH):
            HITS["cpva"] += 1
            mode = CPVA_MODE["mode"]
            if mode == "slow":
                time.sleep(1.5)
            if mode in ("500", "503"):
                return self._send(int(mode), {"error": mode})
            return self._send(200, _rows(1.0))
        if p.startswith(L3_PATH):
            HITS["l3"] += 1
            return self._send(200, _rows(2.0))
        self._send(404, {"error": "no such path"})


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
LIVE = f"127.0.0.1:{SERVER.server_address[1]}"
DEAD = "127.0.0.1:1"                # nothing listens there: connection refused


def reset(cpva_host, mode="ok", proxy_host=LIVE):
    C.CPVA_BASE_URL = f"http://{cpva_host}{CPVA_PATH}"
    C.L3_PROXY_BASE_URLS = (f"http://{proxy_host}{L3_PATH}",)
    CPVA_MODE["mode"] = mode
    C._cpva_down_until = 0.0
    for k in HITS:
        HITS[k] = 0


def value_of(timeout=2.0):
    rows = C.cpva_fetch_samples("L3-A:X", 0, 2_000_000_000, timeout=timeout)
    return rows[-1]["value"][0] if rows else None


def error_of(timeout=2.0):
    try:
        C.cpva_fetch_samples("L3-A:X", 0, 2_000_000_000, timeout=timeout)
    except Exception as exc:
        return exc
    return None


def test_cpva_answering():
    reset(LIVE)
    assert value_of() == 1.0
    assert HITS["l3"] == 0


def test_refused_goes_to_backup():
    reset(DEAD)
    assert value_of() == 2.0


def test_gateway_error_latches():
    reset(LIVE, "503")
    assert value_of() == 2.0
    asked = HITS["cpva"]
    assert value_of() == 2.0
    assert HITS["cpva"] == asked, "CPVA asked while marked down"
    C._cpva_down_until = 0.0
    CPVA_MODE["mode"] = "ok"
    assert value_of() == 1.0


def test_http_500_is_not_backed_up():
    reset(LIVE, "500")
    exc = error_of()
    assert getattr(getattr(exc, "response", None), "status_code", None) == 500
    assert HITS["l3"] == 0


def test_timeout_is_not_backed_up():
    reset(LIVE, "slow")
    exc = error_of(timeout=0.5)
    assert isinstance(exc, requests.ReadTimeout), repr(exc)
    assert HITS["l3"] == 0


def test_both_down_names_both():
    reset(DEAD, proxy_host=DEAD)
    exc = error_of()
    assert isinstance(exc, requests.ConnectionError)
    assert "backup" in str(exc)


if __name__ == "__main__":
    bad = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok    {name}")
            except AssertionError as exc:
                bad += 1
                print(f"  FAIL  {name}: {exc}")
    SERVER.shutdown()
    print("all passed" if not bad else f"{bad} failed")
    sys.exit(1 if bad else 0)
