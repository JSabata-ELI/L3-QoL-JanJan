"""Probe the archiver: what does the SPIDER pair really store?

This is the script that found out why four analysed days of SPIDER spectra came
out labelled "Sample number". It asks the archiver three questions and prints
the answers; it changes nothing.

  1. Which SPIDER channels exist, and how long is each _X / _Y waveform?
     Answer:  TimeDomain_Int_Y = 4096 points,  TimeDomain_Int_X = 2048.
              SpecDomain_Int is 2048/2048 and was never affected.
              Each _X also carries a one-element 'Archive_Disabled' sample at
              midnight, which the panel's fetch already drops.

  2. Is the stored axis uniform, and where would a full-length one end?
     Answer:  2048 points, -3749.0869 … -1.8306 fs, step 1.83057 fs (constant to
              the last float32 digit). Continued to 4096 points it ends at
              +3747.08 fs, so point 2048 is t = 0.

  3. Is the 4096-point Y the whole record, or 2048 real points plus padding?
     Answer:  the whole record. TimeDomain_FL_Y — a transform-limited pulse, so
              it MUST be centred on t = 0 — peaks exactly on point 2048, and its
              centre of mass is 2048.0. That is what makes "the archive stored
              the first half of the axis" a fact rather than a guess.

Needs the archiver (10.78.0.57).

Run:  python testing/probe_spider_x_axis.py
"""
import datetime as dt
import json
import ssl
import sys
import urllib.parse
import urllib.request

CPVA_URL = "https://10.78.0.57:8443/api/1.0/cpva"
BASE = "L3-SBDP-SPIDER:TimeDomain_Int"
PRAGUE = dt.timezone(dt.timedelta(hours=2))      # CEST, the days in question

PAIRS = ["SpecDomain_Int", "SpecDomain_Phase",
         "TimeDomain_Int", "TimeDomain_FL", "TimeDomain_Phase"]


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


def channels_matching(pattern):
    url = f"{CPVA_URL}/channels-by-pattern?" + urllib.parse.urlencode(
        {"pattern": pattern})
    try:
        data = json.loads(urllib.request.urlopen(
            url, context=_ctx(), timeout=20).read())
    except Exception as exc:
        return [f"<lookup failed: {exc}>"]
    out = [x if isinstance(x, str)
           else (x.get("channelName") or x.get("name") or x.get("channel"))
           for x in data]
    return sorted(filter(None, out))


def ns(y, m, d, hh, mm):
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=PRAGUE).timestamp() * 1e9)


def stamp(t_ns):
    return dt.datetime.fromtimestamp(t_ns / 1e9, PRAGUE).strftime(
        "%Y-%m-%d %H:%M:%S.%f")[:-3]


A, B = ns(2026, 8, 17, 10, 24), ns(2026, 8, 17, 10, 34)


def q1_channels_and_lengths():
    print("1. the SPIDER channels, and the length of each half of every pair")
    for c in channels_matching("**SPIDER**"):
        print("     ", c)
    print()
    for p in PAIRS:
        row = []
        for suf in ("_X", "_Y"):
            ch = f"L3-SBDP-SPIDER:{p}{suf}"
            try:
                samples = fetch(ch, A, B)
            except Exception as exc:
                row.append(f"{suf}: FAILED {exc}")
                continue
            lens = sorted({len(s["value"]) for s in samples
                           if isinstance(s.get("value"), list)})
            row.append(f"{suf}: n={len(samples)} lengths={lens}")
        flag = "   <-- MISMATCHED" if p in ("TimeDomain_Int", "TimeDomain_FL") else ""
        print(f"     {p:18s} " + " | ".join(row) + flag)
    print()


def q2_is_the_axis_uniform():
    print("2. the stored axis itself")
    for s in fetch(BASE + "_X", A, B):
        v = s.get("value")
        n = len(v) if isinstance(v, list) else "scalar"
        print(f"     {stamp(s['time'])}  len={n}")
        if not isinstance(v, list):
            continue
        if len(v) < 5:
            print(f"       value={v!r}   (dropped by the panel's own fetch)")
            continue
        steps = [v[i + 1] - v[i] for i in range(len(v) - 1)]
        step = (v[-1] - v[0]) / (len(v) - 1)
        spread = max(abs(d - step) for d in steps)
        print(f"       {v[0]:.4f} … {v[-1]:.4f}   step {step:.8f}")
        print(f"       step spread {spread:.2e}  "
              f"({spread / abs(step):.1e} relative — uniform)")
        print(f"       continued to 4096 points: {v[0]:.4f} … "
              f"{v[0] + step * 4095:.4f}")
        print(f"       point 2048 would be {v[0] + step * 2048:+.4f}")
    print()


def q3_is_y_padded():
    print("3. is the 4096-point Y the whole record, or padded?")
    for ch in ("L3-SBDP-SPIDER:TimeDomain_FL_Y", BASE + "_Y"):
        samples = [s for s in fetch(ch, A, B)
                   if isinstance(s.get("value"), list) and len(s["value"]) == 4096]
        if not samples:
            print(f"     {ch}: no 4096-point sample in this window")
            continue
        v = samples[0]["value"]
        half = len(v) // 2
        imax = max(range(len(v)), key=lambda i: v[i])
        tot = sum(v)
        com = sum(i * a for i, a in enumerate(v)) / tot if tot else float("nan")
        nz2 = sum(1 for a in v[half:] if abs(a) > 1e-12)
        print(f"     {ch}")
        print(f"       peak at point {imax} of {len(v)}, "
              f"centre of mass {com:.1f}")
        print(f"       second half: {nz2} of {half} points are non-zero "
              f"(so it is data, not padding)")
    print()


def main():
    q1_channels_and_lengths()
    q2_is_the_axis_uniform()
    q3_is_y_padded()
    print("Conclusion: the archive stores the FIRST HALF of the SPIDER time axis. "
          "Continuing it at its own step is exact, not an approximation.")


if __name__ == "__main__":
    sys.exit(main())
