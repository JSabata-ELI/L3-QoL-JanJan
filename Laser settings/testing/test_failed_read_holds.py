"""Laser settings — one failed read does not throw a good value away.

    python testing/test_failed_read_holds.py

13.10.2026, from the office: GDD, TOD and SBW4 flickered to a grey dash. A
pass whose read failed or was not answered in time replaced a good reading
a second old. Now the good reading is kept until it is `STALE_AFTER_S` old.
Headless and offline: the engine is never started, its result slot is fed.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(what)
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def main():
    from PySide6.QtCore import QCoreApplication

    import ls_core as C
    import ls_cpva as A
    from ls_engine import PollEngine

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa
    engine = PollEngine(lambda: ["GDD"])
    said = []
    engine.logged.connect(lambda text, _hint: said.append(text))

    def pass_(reading):
        engine._gen += 1
        engine._pass_done(engine._gen, {"GDD": reading})

    pass_(A.Reading(23250.0, 1))
    pass_(A.Reading(error="not read in time — the archiver did not answer"))
    check("a failed read keeps the value", engine.reading("GDD").value, 23250.0)
    check("and says so once in the log", len(said), 1)
    pass_(A.Reading(error="again"))
    check("not twice", len(said), 1)
    engine._last_ok["GDD"] = time.time() - C.STALE_AFTER_S - 1
    pass_(A.Reading(error="still failing"))
    check("once stale, the failure shows", engine.reading("GDD").error,
          "still failing")
    pass_(A.Reading(23300.0, 2))
    check("a good read comes back", engine.reading("GDD").value, 23300.0)
    pass_(A.Reading(error="once more"))
    check("and a new failure streak is logged again", len(said), 2)
    print()
    print("all good" if not FAILED else f"{len(FAILED)} FAILED")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
