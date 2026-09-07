"""End-to-end check against the archive: the four days that came out in "samples".

Reads L3-SBDP-SPIDER:TimeDomain_Int_X/_Y for the same four windows the operator
analysed, runs the panel's own axis rebuild and metric code, and prints what the
Spectra tab now reports next to what it reported before the fix (from the CSV the
operator exported):

    Spectrum 1  peak 2045   FWHM 18.197 samples
    Spectrum 2  peak 2044   FWHM 17.918 samples
    Spectrum 3  peak 2051   FWHM 283.55 samples
    Spectrum 4  peak 2042   FWHM 19.549 samples

Needs the archiver (10.78.0.57). Read-only.

Run:  python testing/check_spider_real_days.py
"""
import datetime as dt
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

import sp_t

BASE = "L3-SBDP-SPIDER:TimeDomain_Int"
PRAGUE = dt.timezone(dt.timedelta(hours=2))

# The four windows from the exported CSV, and the numbers it carried.
DAYS = [
    ("Spectrum 1", (2026, 8, 17), (10, 24), (19, 0),  2045, 18.197316),
    ("Spectrum 2", (2026, 8, 18), (8, 0),   (19, 0),  2044, 17.918232),
    ("Spectrum 3", (2026, 8, 19), (8, 0),   (19, 0),  2051, 283.552799),
    ("Spectrum 4", (2026, 8, 20), (8, 0),   (18, 55), 2042, 19.548642),
]


def ns(day, hm):
    return int(dt.datetime(*day, *hm, tzinfo=PRAGUE).timestamp() * 1e9)


def main():
    print(f"{'':11s}  {'shots':>5s}  {'axis':>11s}  "
          f"{'peak was':>8s}  {'peak now':>10s}  {'FWHM was':>9s}  {'FWHM now':>10s}")
    for label, day, h0, h1, old_peak, old_fwhm in DAYS:
        a, b = ns(day, h0), ns(day, h1)
        wf_y = sp_t._fetch_waveforms(BASE + "_Y", a, b)
        wf_x = sp_t._fetch_waveforms(BASE + "_X", a - int(600e9), b)
        if not wf_y:
            print(f"{label:11s}  no spectra in the archive for this window")
            continue
        st = sp_t._compute_stats([arr for _, arr in wf_y])
        y = st["mean"]
        x_stored = wf_x[-1][1] if wf_x else None
        x = sp_t._fit_x_axis(x_stored, len(y))
        axis = ("samples" if x is None else
                f"{len(x_stored)}→{len(x)}")
        if x is None:
            print(f"{label:11s}  {st['n']:5d}  {axis:>11s}   (no measured axis)")
            continue
        m = sp_t._spectral_metrics(x, y)
        print(f"{label:11s}  {st['n']:5d}  {axis:>11s}  "
              f"{old_peak:8d}  {m['peak_wl']:8.2f}fs  "
              f"{old_fwhm:9.3f}  {m['fwhm']:8.2f}fs")
    print("\n'peak was' / 'FWHM was' are array positions; 'now' are femtoseconds.")


if __name__ == "__main__":
    sys.exit(main())
