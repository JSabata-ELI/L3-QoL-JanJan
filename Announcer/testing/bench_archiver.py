"""Against the REAL archiver: can the fourteen standard values be read at the
rate the program reads them, and does a failure still look like a failure?

Run:  python testing/bench_archiver.py

Nothing here writes anything. It needs the facility network; off it, every read
fails, which is itself one of the things being checked.
"""
import os
import sys
import time
from pathlib import Path

# The units are °C and the sentences use an em dash, and a cp1250 console raises
# on both rather than printing them.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ann_core as C          # noqa: E402
import ann_cpva as A          # noqa: E402


def one_pass(reader, items, deadline_s):
    t0 = time.monotonic()
    out = reader.read_many(items, deadline_s=deadline_s,
                           readable_error=C.readable_pv_error)
    return out, time.monotonic() - t0


def main():
    items = C.default_value_items()
    reader = A.PassReader(workers=6)
    print(f"Reading the {len(items)} standard values, three passes.\n")
    try:
        for n in range(1, 4):
            out, took = one_pass(reader, items, deadline_s=8.0)
            good = [r for r in out.values() if r.ok and r.value is not None]
            empty = [r for r in out.values() if r.ok and r.value is None]
            bad = [r for r in out.values() if not r.ok]
            print(f"pass {n}: {took:.2f} s   "
                  f"{len(good)} with a value, {len(empty)} nothing archived, "
                  f"{len(bad)} could not be read")
            if n == 1:
                for it in items:
                    r = out.get(int(it["id"]))
                    state, text = C.judge_value(
                        it, r.value if r else None,
                        error=r.error if r else "not read",
                        samples=r.samples if r else 0)
                    print(f"    {state:8} {it['name']:32} {text}")
            time.sleep(0.5)

        print("\nA channel that does not exist must be 'nothing archived', "
              "never an error:")
        ghost = C.new_value_item(999, "ghost", "L3-THIS-DOES-NOT:Exist")
        r = A.read_value(ghost, last_reader=reader.last_reader,
                         readable_error=C.readable_pv_error)
        print(f"    ok={r.ok}  value={r.value}  samples={r.samples}  "
              f"error={r.error}")

        print("\nA sparse channel read with the widening look-back "
              "(a chiller setpoint):")
        sp = C.new_value_item(998, "setpoint", "L3-UTIL-CHL03-001:TempSP",
                              read="last", unit="°C")
        t0 = time.monotonic()
        r = A.read_value(sp, last_reader=reader.last_reader,
                         readable_error=C.readable_pv_error)
        print(f"    {r.value} °C in {time.monotonic() - t0:.2f} s "
              f"(ok={r.ok}, error={r.error})")
        t0 = time.monotonic()
        r = A.read_value(sp, last_reader=reader.last_reader,
                         readable_error=C.readable_pv_error)
        print(f"    again: {r.value} °C in {time.monotonic() - t0:.2f} s "
              f"— the remembered window should make this the faster one")

        print("\n" + A.stats_line())
    finally:
        reader.close()


if __name__ == "__main__":
    main()
