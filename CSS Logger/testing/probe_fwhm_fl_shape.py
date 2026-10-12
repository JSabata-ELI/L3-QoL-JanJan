"""Probe the archiver: is L3-SBDP-SPIDER:FWHM_FL a spectrum or one number per shot?

Reported: FWHM_FL picked as the spectrum PV, X axis copied from SpecDomain_Int_X,
Analyze drew nothing. This reads ten minutes of both channels and prints what
each sample really holds (type, length). It changes nothing.

  Answer (2026-09-30, 19:10-19:20):
    FWHM_FL            60 samples, every one a list of ONE number (~28 fs)
    SpecDomain_Int_X   1 sample of 2048 points + one 'Archive_Disabled'
    SpecDomain_Int_Y   60 samples of 2048 points
  So FWHM_FL is the Fourier-limit pulse duration, one number per shot — not a
  spectrum. It used to pass as a one-point spectrum, drawn as nothing.

Needs the archiver (10.78.0.57).

Run:  python testing/probe_fwhm_fl_shape.py
"""
import datetime as dt
import json
import ssl
import sys
import urllib.parse
import urllib.request
from collections import Counter

CPVA_URL = "https://10.78.0.57:8443/api/1.0/cpva"
PRAGUE = dt.timezone(dt.timedelta(hours=2))      # CEST
CHANNELS = ["L3-SBDP-SPIDER:FWHM_FL", "L3-SBDP-SPIDER:SpecDomain_Int_X",
            "L3-SBDP-SPIDER:SpecDomain_Int_Y"]


def _ctx():
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def fetch(channel, start_ns, end_ns, timeout=30):
    params = urllib.parse.urlencode({
        "channelName": channel, "start": int(start_ns), "end": int(end_ns)})
    return json.loads(urllib.request.urlopen(
        f"{CPVA_URL}/samples?{params}", context=_ctx(), timeout=timeout).read())


def ns(y, m, d, hh, mm):
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=PRAGUE).timestamp() * 1e9)


def main():
    # The evening of the user's screenshot.
    a, b = ns(2026, 9, 30, 19, 10), ns(2026, 9, 30, 19, 20)
    for ch in CHANNELS:
        try:
            samples = fetch(ch, a, b)
        except Exception as exc:
            print(f"{ch}: FAILED {exc}")
            continue
        shapes = Counter()
        for s in samples:
            v = s.get("value")
            shapes[f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__] += 1
        print(f"{ch}: {len(samples)} samples, shapes {dict(shapes)}")
        for s in samples[:3]:
            v = s.get("value")
            shown = v[:5] if isinstance(v, list) else v
            print(f"     {shown!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
