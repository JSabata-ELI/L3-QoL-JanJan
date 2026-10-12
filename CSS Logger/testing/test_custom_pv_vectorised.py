"""Custom PVs are computed over whole columns now — they must give the same
numbers the old one-eval-per-row code gave.

The per-row eval cost about a second per live table refresh with 100 000 rows
and three formulas, on the GUI thread. The column version is checked here
against a copy of the old loop on made-up rows that contain the awkward cases:
a source with no value yet, a word value, a custom built on another custom, a
formula numpy cannot take whole (``A if A > 0 else B``), three-argument min/max,
round, math, and division by zero.

Also checked: the live tail (_extend_custom_series_live) continues a trace with
the values the next full rebuild gives, so a derived trace moves with its
sources between table rebuilds instead of stopping.

    python testing/test_custom_pv_vectorised.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication      # noqa: E402

import main as css                              # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


A, B, C = "TEST:A", "TEST:B", "TEST:C"
FORMULAS = [
    ("Sum",     "A+B"),
    ("Ratio",   "A/B"),
    ("Pick",    "A if A > 2 else B"),
    ("Low3",    "min(A, B, C)"),
    ("High3",   "max(A, B, C)"),
    ("Rounded", "round(A / 3, 2)"),
    ("Root",    "math.sqrt(abs(A))"),
    ("Chain",   "D * 10"),           # D = Sum, another custom
    ("DivZero", "A / (B - B)"),
]


def _old_rowwise(plan, rows):
    """The loop _compute_custom_pvs_in_rows had before, writing into the rows."""
    for _ts, r in rows:
        for name, code, srcs in plan:
            res = None
            if code is not None:
                ns = {}
                for letter, pv in srcs:
                    v = r.get(pv, (None,))[0]
                    ns[letter] = v if isinstance(v, (int, float)) else None
                try:
                    res = eval(code, css._CPV_SAFE_ENV, ns)
                except Exception:
                    res = None
            r[name] = (res, "")


def _rows(n=400):
    rows = []
    t0 = 1_790_000_000_000_000_000
    for i in range(n):
        row = {}
        if i >= 3:
            row[A] = ((i % 7) - 1.5, "")
        if i % 50 == 13:
            row[B] = ("OPEN", "")            # a word value
        else:
            row[B] = (float(i % 5), "")
        row[C] = (float(i) * 0.5, "")
        rows.append((t0 + i * 1_000_000_000, row))
    return rows


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))


def _widget():
    w = css.CSSLoggerWidget()
    w._pv_order = [A, B, C]
    w._base_pv_order = [A, B, C]
    w._custom_pvs = []
    for name, expr in FORMULAS:
        letters = css._cpv_vars(expr)
        bind = {"A": A, "B": B, "C": C, "D": "Sum"}
        w._custom_pvs.append({"name": name, "expr": expr,
                              "bindings": {lt: bind[lt] for lt in letters}})
    w._pv_order += [n for n, _ in FORMULAS]
    return w


def main() -> int:
    print("test_custom_pv_vectorised")
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app

    # ── 1. same numbers as the old loop ───────────────────────────────────────
    w = _widget()
    rows = _rows()
    old_rows = _rows()
    plan, _broken = w._cpv_plan()
    _old_rowwise(plan, old_rows)
    w._compute_custom_pvs_in_rows(rows)
    for name, _expr in FORMULAS:
        bad = [i for i, ((_t, o), (_t2, r)) in enumerate(zip(old_rows, rows))
               if not _same(o[name][0], r[name][0])]
        check(f"{name}: every row as before", not bad,
              f"first difference at row {bad[0]}: old {old_rows[bad[0]][1][name][0]!r}, "
              f"new {rows[bad[0]][1][name][0]!r}" if bad else "")
    check("division by zero is still reported by name",
          any("DivZero" in m and "ZeroDivisionError" in m for m in w._cpv_diag),
          str(w._cpv_diag))

    # ── 2. faster ─────────────────────────────────────────────────────────────
    # Interleaved, median of five: one run is mostly the garbage collector's
    # mood, not the code.
    import statistics
    olds, news = [], []
    for k in range(5):
        for kind in (("old", "new") if k % 2 else ("new", "old")):
            big = _rows(60_000)
            t = time.perf_counter()
            if kind == "old":
                _old_rowwise(plan, big)
            else:
                w._compute_custom_pvs_in_rows(big)
            (olds if kind == "old" else news).append(time.perf_counter() - t)
            del big
    t_old, t_new = statistics.median(olds), statistics.median(news)
    check("the column version is faster", t_new < t_old,
          f"old {t_old * 1000:.0f} ms, new {t_new * 1000:.0f} ms (median of 5)")

    # ── 3. the live tail matches the next full rebuild ────────────────────────
    w = css.CSSLoggerWidget()
    w._pv_order = [A, B]
    w._base_pv_order = [A, B]
    w._custom_pvs = [{"name": "Sum", "expr": "A+B", "bindings": {"A": A, "B": B}}]
    gap = css.SAMPLE_HOLD_MIN_GAP_MS * 1_000_000
    t0 = 1_790_000_000_000_000_000
    step = max(gap * 3, 1_000_000_000)
    w._samples_by_pv = {
        A: [(t0 + i * step, float(i), "") for i in range(20)],
        B: [(t0 + i * step + step // 2, float(10 * i), "") for i in range(20)],
    }
    w._pre_window_vals = {}

    def _rebuild():
        r = w._build_table_rows(w._samples_by_pv, [A, B], w._pre_window_vals)
        w._table_rows_unfiltered = r
        w._rebuild_custom_pvs()

    _rebuild()
    n_before = len(w._samples_by_pv["Sum"])
    # Live: more samples arrive, the table is not rebuilt yet.
    w._samples_by_pv[A] += [(t0 + i * step, float(i), "") for i in range(20, 30)]
    w._samples_by_pv[B] += [(t0 + i * step + step // 2, float(10 * i), "")
                           for i in range(20, 30)]
    w._extend_custom_series_live()
    tail = list(w._samples_by_pv["Sum"])
    check("the derived trace grows between rebuilds", len(tail) > n_before,
          f"{n_before} -> {len(tail)} points")
    _rebuild()
    full = {t: v for t, v, _u in w._samples_by_pv["Sum"]}
    mism = [(t, v) for t, v, _u in tail if t in full and not _same(full[t], v)]
    check("the tail has the values the next rebuild gives", not mism,
          f"{len(mism)} differ, e.g. {mism[:2]}" if mism else "")
    check("and covers up to the newest sample",
          tail[-1][0] == max(w._samples_by_pv[A][-1][0], w._samples_by_pv[B][-1][0]),
          f"tail ends {tail[-1][0]}")

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
