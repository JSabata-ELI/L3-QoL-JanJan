"""Settings PVs print as whole numbers; measurements keep their decimals.

The complaint: the GDD, the waveplates and the motors all read "22710.000". They are
whole-number settings — a dispersion order, a waveplate count, a motor position — and
three decimals on them are noise. Energies are not, and must not lose theirs.

Both formatters are checked, because the value reaches the screen through one and the
burn-in on a saved picture through the other, and they used to be able to disagree.

Usage:  python testing/test_pv_whole_numbers.py
Exit code is 1 when a claim fails.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_pvfmt_test_")

import is_t as sl                                     # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  - " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


# (channel, value, expected text)
WHOLE = [
    ("L3-SPFE-AOD03-002:Order2_RB", 22710.0, "22710"),      # GDD
    ("L3-SPFE-AOD03-002:Order3_RB", -1234.4, "-1234"),      # TOD
    ("L3-PFWP6-MTR03-1:RawPos", 14000.0, "14000"),          # waveplate
    ("L3-XYZ-MTR07-2:ActPos", 12.345, "12"),                # a motor
    ("L3-XYZ-STG01:Position", 7.9, "8"),                    # a stage
]
KEEPS_DECIMALS = [
    ("HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy", 2.5, "2.500"),
    ("L3-VAC-P01:Pressure", 0.00123, "0.001"),
]


def main():
    print("Settings print whole")
    for ch, val, want in WHOLE:
        got = sl._format_pv_value(ch, val)
        check(got == want, f"{ch.split(':')[-1]} -> {want}", f"got {got!r}")
        check(sl.pv_decimals_for(ch, ch) == 0, f"{ch.split(':')[-1]} asks for 0 decimals")

    print("Measurements keep their decimals")
    for ch, val, want in KEEPS_DECIMALS:
        got = sl._format_pv_value(ch, val)
        check(got == want, f"{ch.split(':')[-1]} -> {want}", f"got {got!r}")

    print("The waveplate is still SNAPPED, not merely rounded")
    # An off-grid readback must come back on the 1000-count grid, not printed as it is.
    got = sl._format_pv_value("L3-PFWP6-MTR03-1:RawPos", 14003.0)
    check(got == "14000", "an off-grid waveplate readback is snapped", f"got {got!r}")

    print("The per-PV override wins over everything")
    sl.PV_DECIMALS["Fine motor"] = 2
    sl.PV_CUSTOM_CHANNELS["Fine motor"] = "L3-XYZ-MTR09-1:ActPos"
    got = sl.pv_format_value("Fine motor", 1.2345)
    check(got == "1.23", "PV_DECIMALS overrides the settings rule", f"got {got!r}")
    sl.PV_DECIMALS.pop("Fine motor", None)
    sl.PV_CUSTOM_CHANNELS.pop("Fine motor", None)

    print("A derived PV is formatted too")
    # No channel behind it — it used to be hard-coded to three decimals.
    sl.PV_DECIMALS["Shots"] = 0
    got = sl.pv_format_value("Shots", 41.6)
    check(got == "42", "a formula can be given whole numbers", f"got {got!r}")
    sl.PV_DECIMALS.pop("Shots", None)
    got = sl.pv_format_value("Nameless formula", 1.5)
    check(got == "1.500", "a formula with no rule keeps three decimals", f"got {got!r}")

    print("The preset PVs are unaffected")
    for name in ("SBW4", "PTM1", "PCM2"):
        ch = sl.pv_channel_for(name)
        if not ch:
            continue
        got = sl.pv_format_value(name, 2.25)
        check(got == "2.250", f"{name} still reads 2.250", f"got {got!r} (channel {ch})")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
