"""
Headless, no network: judging a whole recorded column.

The archiver is never contacted -- the history is synthetic and the record is
built by hand, so this runs anywhere.

Run:  python testing/test_spfe_record.py
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spfe_record as recorder        # noqa: E402
import spfe_store as store            # noqa: E402
from spfe_store import SLOT_MORNING, SLOT_NOW, Record, TZ_PRAGUE   # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def rec(day, hh, mm, slot, **values):
    when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ_PRAGUE)
    return Record(when=when, slot=slot, values=dict(values))


def history_rows(fields, start: date, n: int, **series) -> list[dict]:
    """`series` maps a column name to a callable taking the day index."""
    rows = []
    for i in range(n):
        d = start + timedelta(days=i)
        r = rec(d, 9, 0, SLOT_MORNING, **{k: fn(i) for k, fn in series.items()})
        rows.append(r.to_csv_row(fields.columns))
    return rows


def test_a_steady_value_is_not_flagged():
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    rows = history_rows(f, date(2026, 8, 3), 12, osc_power=lambda i: 630.0 + i % 5)
    flags = recorder.judge_columns(
        rec(date(2026, 8, 20), 9, 0, SLOT_MORNING, osc_power=632.0), f, rows, cfg)
    check("an ordinary value produces no flag", flags == {}, str(flags))


def test_an_outlier_is_flagged_and_explained():
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    rows = history_rows(f, date(2026, 8, 3), 12, osc_power=lambda i: 630.0 + i % 5)
    flags = recorder.judge_columns(
        rec(date(2026, 8, 20), 9, 0, SLOT_MORNING, osc_power=90.0), f, rows, cfg)
    check("a wild value is flagged", "osc_power" in flags, str(flags))
    check("and the flag is keyed by the number, not by the quantity",
          list(flags) == ["osc_power"], str(flags))
    check("the reason mentions the mornings",
          "morning" in flags.get("osc_power", ""), flags.get("osc_power"))


def test_only_the_bad_half_of_a_pair_is_named():
    """A row of two numbers must flag the one that moved, not the pair."""
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    rows = history_rows(
        f, date(2026, 8, 3), 12,
        ba1loop4_1=lambda i: -367.0 + i % 3,
        ba1loop4_2=lambda i: 605.0 + i % 3,
    )
    flags = recorder.judge_columns(
        rec(date(2026, 8, 20), 9, 0, SLOT_MORNING,
            ba1loop4_1=-366.0, ba1loop4_2=9000.0), f, rows, cfg)
    check("only Y, the half that moved, is flagged",
          list(flags) == ["ba1loop4_2"], str(flags))


def test_a_manual_text_row_is_never_judged():
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    rows = history_rows(f, date(2026, 8, 3), 12, osc_power=lambda i: 630.0)
    r = rec(date(2026, 8, 20), 9, 0, SLOT_MORNING, osc_power=630.0)
    r.values["notes"] = "hard reset of the oscillator"
    flags = recorder.judge_columns(r, f, rows, cfg)
    check("free text is not compared with anything", "notes" not in flags, str(flags))


def test_a_record_now_column_borrows_a_slot():
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    # Mornings sit near 630, evenings near 500. An 11:00 extra column judged
    # against the evenings would look wildly wrong; against the mornings it is
    # ordinary.
    rows = []
    for i in range(12):
        d = date(2026, 8, 3) + timedelta(days=i)
        rows.append(rec(d, 9, 0, store.SLOT_MORNING,
                        osc_power=630.0 + i % 3).to_csv_row(f.columns))
        rows.append(rec(d, 18, 0, store.SLOT_EVENING,
                        osc_power=500.0 + i % 3).to_csv_row(f.columns))
    flags = recorder.judge_columns(
        rec(date(2026, 8, 20), 11, 0, SLOT_NOW, osc_power=631.0), f, rows, cfg)
    check("a late-morning extra column is judged against the mornings",
          flags == {}, str(flags))


def test_no_history_means_no_noise():
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    rows = history_rows(f, date(2026, 8, 3), 3, osc_power=lambda i: 630.0)
    flags = recorder.judge_columns(
        rec(date(2026, 8, 20), 9, 0, SLOT_MORNING, osc_power=9999.0), f, rows, cfg)
    check("the first days flag nothing at all", flags == {}, str(flags))


def test_component_names_come_from_the_label():
    f = store.load_fields()
    row = f.row_by_key("ba2loop4")
    names = row.component_names
    check("X,Y,SUM is read out of the label", names == ["X", "Y", "SUM"], str(names))
    single = f.row_by_key("osc_power")
    check("a single-number row has no component name",
          single.part_label("osc_power") == "")
    check("and the third number of a triple is called SUM",
          row.part_label("ba2loop4_3") == "SUM", row.part_label("ba2loop4_3"))


def test_a_verdict_belongs_to_one_number():
    """Every number has a cell of its own -- on screen AND in the workbook --
    so a verdict belongs to one number. Marking all three because one of them
    is unusual is how an alarm stops meaning anything."""
    f = store.load_fields()
    cfg = dict(store.DEFAULT_CONFIG)
    rows = history_rows(f, date(2026, 8, 3), 12,
                        ba2loop2_1=lambda i: 136.0 + i % 3,
                        ba2loop2_3=lambda i: 2500.0 + i)
    bad = rec(date(2026, 8, 20), 9, 0, SLOT_MORNING,
              ba2loop2_1=136.0, ba2loop2_3=99999.0)

    by_number = recorder.judge_columns(bad, f, rows, cfg)
    check("only the number that is off is flagged",
          list(by_number) == ["ba2loop2_3"], str(by_number))
    check("the reason mentions the mornings",
          "morning" in by_number["ba2loop2_3"], by_number["ba2loop2_3"])


def test_defaults_are_written_into_a_new_record():
    """The prefilled numbers must end up in the file, not only on the screen.

    Nothing is fetched here: a field list with no PV names never reaches the
    archiver, which is exactly the path this checks.
    """
    full = store.load_fields()
    manual_only = store.Fields(
        [(g, [r for r in rows if r.is_manual]) for g, rows in full.groups])
    cfg = dict(store.DEFAULT_CONFIG)

    reading = recorder.read_and_judge(
        manual_only, datetime(2026, 9, 1, 9, 0, tzinfo=TZ_PRAGUE),
        SLOT_MORNING, [], cfg)
    check("the prefilled numbers are in the record",
          reading.record.values.get("gdd_baseline") == "22710",
          str(reading.record.values))
    check("but no PV answered, so nothing is worth recording",
          reading.measured == 0, str(reading.measured))
    check("and the free-text row stays empty",
          "notes" not in reading.record.values)


def main():
    print("spfe_record")
    for fn in (test_a_steady_value_is_not_flagged,
               test_an_outlier_is_flagged_and_explained,
               test_only_the_bad_half_of_a_pair_is_named,
               test_a_manual_text_row_is_never_judged,
               test_a_record_now_column_borrows_a_slot,
               test_no_history_means_no_noise,
               test_component_names_come_from_the_label,
               test_a_verdict_belongs_to_one_number,
               test_defaults_are_written_into_a_new_record):
        fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
