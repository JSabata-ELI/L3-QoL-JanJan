"""
Headless, no network, no share: the field file, the CSV and the workbook.

Everything is written into a temporary folder. The scratch share is never
touched by this test.

Run:  python testing/test_spfe_store.py
"""
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spfe_store as store            # noqa: E402
from spfe_store import (              # noqa: E402
    SLOT_EVENING, SLOT_MORNING, SLOT_NOW, Record, TZ_PRAGUE,
)

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def _values(wb):
    """The sheet the day blocks are on. The workbook has a Trends sheet and a
    Charts sheet beside it now, and whichever one Excel was left showing comes
    back as wb.active."""
    return wb[store.SHEET_VALUES] if store.SHEET_VALUES in wb.sheetnames \
        else wb.worksheets[0]


def make_fields():
    return store.load_fields()


def rec(day, hh, mm, slot, **values):
    when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ_PRAGUE)
    return Record(when=when, slot=slot, values=dict(values))


# ---------------------------------------------------------------------------

def test_fields_file():
    f = make_fields()
    check("the shipped field file loads", f.table_row_count > 0)
    keys = [r.key for r in f.rows]
    check("every key is unique", len(keys) == len(set(keys)))

    multi = [r for r in f.rows if len(r.pvs) > 1]
    check("a multi-PV row gets one column per number",
          all(len(r.columns) == len(r.pvs) for r in multi))
    single = [r for r in f.rows if len(r.pvs) <= 1]
    check("a single-number row keeps its own key as the column",
          all(r.columns == [r.key] for r in single))
    check("the Notes rows are manual",
          all(r.is_manual for r in f.rows if r.group == "Notes"))


def test_duplicate_key_is_refused(tmp: Path):
    bad = tmp / "bad_fields.json"
    bad.write_text(
        '{"groups":[{"label":"A","rows":['
        '{"key":"x","label":"one","pvs":[""]},'
        '{"key":"x","label":"two","pvs":[""]}]}]}',
        encoding="utf-8")
    try:
        store.load_fields(bad)
        check("a duplicate key is refused", False, "it was accepted")
    except ValueError as exc:
        check("a duplicate key is refused", "used more than once" in str(exc))


def test_csv_append_and_skip(tmp: Path):
    f = make_fields()
    path = tmp / "spfe_log.csv"
    day = date(2026, 9, 1)
    r1 = rec(day, 9, 0, SLOT_MORNING, osc_power=636.0, osc_bandwidth=92.0)
    r2 = rec(day, 18, 0, SLOT_EVENING, osc_power=620.0)

    n = store.append_csv(path, [r1, r2], f.columns)
    check("both rows are appended", n == 2)

    n2 = store.append_csv(path, [r1, r2], f.columns)
    check("the same rows a second time are skipped", n2 == 0)

    rows = store.read_csv(path)
    check("the file holds exactly two rows", len(rows) == 2)
    check("the value came back", rows[0]["osc_power"] == "636.0",
          rows[0]["osc_power"])
    check("the slot is stored", rows[1]["slot"] == SLOT_EVENING)


def test_history_is_per_slot(tmp: Path):
    f = make_fields()
    path = tmp / "hist.csv"
    day = date(2026, 8, 3)
    records = []
    for i in range(12):
        d = day + timedelta(days=i)
        records.append(rec(d, 9, 0, SLOT_MORNING, osc_power=630.0 + i))
        records.append(rec(d, 18, 0, SLOT_EVENING, osc_power=500.0 + i))
    store.append_csv(path, records, f.columns)
    rows = store.read_csv(path)

    morning = store.history_for(rows, "osc_power", SLOT_MORNING, 20)
    evening = store.history_for(rows, "osc_power", SLOT_EVENING, 20)
    check("mornings are kept apart from evenings",
          all(v >= 630 for v in morning) and all(v < 600 for v in evening))
    check("the newest comes first", morning[0] == 641.0, str(morning[:3]))

    capped = store.history_for(rows, "osc_power", SLOT_MORNING, 5)
    check("the limit is honoured", len(capped) == 5)

    before = store.history_for(rows, "osc_power", SLOT_MORNING, 20,
                               before=day + timedelta(days=3))
    check("a day's own value is excluded when asked", len(before) == 3, str(before))


def test_scheduled_slots_skip_weekends_and_the_future():
    cfg = dict(store.DEFAULT_CONFIG)
    friday = date(2026, 8, 28)          # Friday
    end = datetime(2026, 8, 31, 12, 0, tzinfo=TZ_PRAGUE)   # Monday noon
    slots = store.scheduled_slots(friday, end, cfg)
    days = sorted({ts.date() for ts, _ in slots})
    check("Saturday and Sunday are skipped",
          days == [date(2026, 8, 28), date(2026, 8, 31)], str(days))
    check("Monday evening (still ahead) is not produced",
          all(not (ts.date() == date(2026, 8, 31) and s == SLOT_EVENING)
              for ts, s in slots))
    check("Monday morning is produced",
          any(ts.date() == date(2026, 8, 31) and s == SLOT_MORNING
              for ts, s in slots))

    cfg2 = dict(cfg, weekdays_only=False)
    days2 = sorted({ts.date() for ts, _ in store.scheduled_slots(friday, end, cfg2)})
    check("switching weekdays_only off brings the weekend back", len(days2) == 4)


def test_catch_up_start_is_bounded():
    cfg = dict(store.DEFAULT_CONFIG, catch_up_max_days=30)
    today = date(2026, 9, 1)
    check("an empty log starts 30 days back",
          store.catch_up_start([], cfg, today) == today - timedelta(days=30))
    rows = [{"date": "2026-08-28"}, {"date": "2026-08-25"}]
    check("otherwise it resumes at the last recorded day",
          store.catch_up_start(rows, cfg, today) == date(2026, 8, 28))
    old = [{"date": "2020-01-01"}]
    check("a very old log is still bounded",
          store.catch_up_start(old, cfg, today) == today - timedelta(days=30))


def test_slot_for_comparison():
    cfg = dict(store.DEFAULT_CONFIG)
    day = date(2026, 9, 1)
    check("a late-morning extra column is judged as a morning",
          store.slot_for_comparison(rec(day, 11, 0, SLOT_NOW), cfg) == SLOT_MORNING)
    check("a late-afternoon extra column is judged as an evening",
          store.slot_for_comparison(rec(day, 16, 0, SLOT_NOW), cfg) == SLOT_EVENING)
    check("a morning column is judged as itself",
          store.slot_for_comparison(rec(day, 9, 0, SLOT_MORNING), cfg) == SLOT_MORNING)


# Deliberately NOT read out of the shipped field file: the rounding steps and
# the units there are the operator's to change, and a test that pins them stops
# them from being changed.
def _pair_row(**kw) -> store.Row:
    base = dict(key="pair", group="G", label="Pair (X,Y)",
                pvs=("A", "B"), join=";", decimals=0)
    base.update(kw)
    return store.Row(**base)


def test_format_cell():
    row = _pair_row()
    check("both numbers are joined",
          store.format_cell(row, {"pair_1": -367, "pair_2": 605}) == "-367 ; 605")
    check("a missing half shows as a dash",
          store.format_cell(row, {"pair_1": -367}) == "-367 ; -")
    check("an empty row is a single dash", store.format_cell(row, {}) == "-")

    hum = store.Row(key="humidity", group="G", label="", pvs=("A",),
                    unit="%", decimals=1)
    check("the unit is appended",
          store.format_cell(hum, {"humidity": 41.2}) == "41.2 %")
    check("but not for the table, where the unit is in the label",
          store.format_cell(hum, {"humidity": 41.2}, with_unit=False) == "41.2")

    # The separator is shown with a space on each side. "150;-250;2500" is
    # three numbers nobody can read, and the minus sign of the middle one
    # disappears against the semicolon.
    check("the separator is spaced out",
          store.join_text(row) == " ; ")
    check("one number of a row can be formatted on its own",
          store.format_value(row, "pair_2", {"pair_1": -367, "pair_2": 605}) == "605")
    check("and a number that is not there is a dash",
          store.format_value(row, "pair_2", {"pair_1": -367}) == "-")


def test_row_parts():
    """The table gives every number of a quantity a line of its own, so it has
    to know what each number is called and what the quantity is called without
    them."""
    row = _pair_row(label="Input - BA2Loop2 (X,Y)")
    check("the component names come out of the label",
          row.component_names == ["X", "Y"])
    check("and the label without them is what stands beside the lines",
          row.base_label == "Input - BA2Loop2")
    check("a number is named by its column",
          [row.part_label("pair_1"), row.part_label("pair_2")] == ["X", "Y"])

    odd = _pair_row(label="Something")
    check("a label with no list falls back to numbers",
          odd.component_names == ["#1", "#2"])
    check("and keeps its label whole", odd.base_label == "Something")

    one = store.Row(key="humidity", group="G", label="", pvs=("A",), unit="%")
    check("a single-number row has no component name at all",
          one.component_names == [""] and one.part_label("humidity") == "")

    three = _pair_row(pvs=("A", "B", "C"), label="Out (X,Y,SUM)")
    check("a three-number row splits into three",
          three.component_names == ["X", "Y", "SUM"])
    mismatch = _pair_row(pvs=("A", "B", "C"), label="Out (X,Y)")
    check("a list that does not match the numbers is not trusted",
          mismatch.component_names == ["#1", "#2", "#3"]
          and mismatch.base_label == "Out (X,Y)")


def test_rounding():
    tens = _pair_row(round_to=(10.0, 100.0))
    check("the first number is shown to the nearest ten",
          store.format_cell(tens, {"pair_1": -367, "pair_2": 2496}) == "-370 ; 2500")
    check("a half rounds away from zero, both ways",
          [store.format_cell(tens, {"pair_1": 365}),
           store.format_cell(tens, {"pair_1": -365})] == ["370 ; -", "-370 ; -"])
    check("a step of 0 leaves the number alone",
          store.format_cell(_pair_row(), {"pair_1": -367}) == "-367 ; -")
    check("text that is not a number is shown as it stands",
          store.format_cell(tens, {"pair_1": "n/a"}) == "n/a ; -")

    check("one step covers every number of the row",
          store._rounding_steps(10, 3) == (10.0, 10.0, 10.0))
    check("a short list is padded, so a third number is simply not rounded",
          store._rounding_steps([10, 10], 3) == (10.0, 10.0, 0.0))
    check("a nonsense step is refused rather than used",
          store._rounding_steps([-5, "x"], 2) == (0.0, 0.0))

    f = make_fields()
    check("the shipped SUM columns are rounded to hundreds",
          [f.row_by_key("ba2loop2").step_for("ba2loop2_3"),
           f.row_by_key("ba2loop2").step_for("ba2loop2_1")] == [100.0, 10.0])
    check("and the dazzlers to whole percent",
          f.row_by_key("dazz1").step_for("dazz1_1") == 1.0)


def test_parse_cell():
    row = _pair_row()
    check("both numbers come back",
          store.parse_cell(row, "-367;605") == {"pair_1": "-367", "pair_2": "605"})
    check("typing one number leaves the other alone",
          store.parse_cell(row, "-367") == {"pair_1": "-367"})
    check("typing the separator is how the second is cleared",
          store.parse_cell(row, "-367;") == {"pair_1": "-367", "pair_2": ""})
    check("a dash read back is empty, never the text '-'",
          store.parse_cell(row, "-367;-") == {"pair_1": "-367", "pair_2": ""})
    check("clearing the cell clears every number of the row",
          store.parse_cell(row, "") == {"pair_1": "", "pair_2": ""})
    check("more numbers than the row has are dropped",
          store.parse_cell(row, "1;2;3") == {"pair_1": "1", "pair_2": "2"})

    note = store.Row(key="notes", group="Notes", label="Notes", manual="text")
    check("a free-text row is taken verbatim",
          store.parse_cell(note, "a;b") == {"notes": "a;b"})

    f = make_fields()
    xyz = f.row_by_key("ba2loop2")
    check("what the table shows reads back into the same three columns",
          store.parse_cell(xyz, store.format_cell(
              xyz, {"ba2loop2_1": 140, "ba2loop2_2": -270, "ba2loop2_3": 2500},
              with_unit=False))
          == {"ba2loop2_1": "140", "ba2loop2_2": "-270", "ba2loop2_3": "2500"})


def test_display_label_and_defaults():
    f = store.load_fields()

    gdd = f.row_by_key("gdd_baseline")
    check("the unit stands in the Quantity column",
          gdd.display_label == "L3 baseline (fs2)", gdd.display_label)
    check("a row with no label shows only its unit",
          f.row_by_key("humidity").display_label == "(%)")
    check("and a row with no unit is just its label",
          f.row_by_key("notes").display_label == "Notes")

    check("the prefilled numbers came out of the field file",
          [gdd.default, f.row_by_key("spider_e5").default] == ["22710", "-917"])

    values = {"osc_power": 636.0, "gdd_baseline": "24000"}
    store.apply_defaults(f, values)
    check("a default fills a row nobody typed into",
          values["spider_e5"] == "-917")
    check("but never overwrites what was typed",
          values["gdd_baseline"] == "24000")
    check("and a measured value is left alone", values["osc_power"] == 636.0)

    check("the Energy row carries the link to the comparison sheet",
          "SPFE_Energy" in f.row_by_key("energy").url,
          f.row_by_key("energy").url)


def test_a_default_does_not_reach_into_the_past():
    """The Spider settings and the best GDD were true from the day they were
    written down. A day before that must stay empty, on screen and in the
    files."""
    f = make_fields()
    gdd = f.row_by_key("gdd_baseline")
    check("the shipped file says which day the settings start on",
          gdd.default_from == "2026-09-01", gdd.default_from)
    check("the default counts on that day", gdd.default_applies(date(2026, 9, 1)))
    check("and on any day after it", gdd.default_applies(date(2026, 9, 20)))
    check("but not the day before", not gdd.default_applies(date(2026, 8, 31)))

    old = {}
    store.apply_defaults(f, old, date(2026, 8, 20))
    check("filling in the past writes no settings at all",
          not [k for k in old if k.startswith(("gdd_", "spider_"))], str(old))

    new = {}
    store.apply_defaults(f, new, date(2026, 9, 2))
    check("a day after the start still gets them", new["gdd_baseline"] == "22710")

    plain = store.Row(key="x", group="G", label="x", manual="number",
                      default="5")
    check("a default with no starting day counts on every day",
          plain.default_applies(date(2000, 1, 1)))


def test_delete_leaves_a_tombstone(tmp: Path):
    """Deleting a row from one log only is undone by the next sync -- the two
    are compared by length. The note that says 'this moment is gone' is what
    actually deletes it."""
    f = make_fields()
    home = tmp / "delete_home"
    share = tmp / "delete_share"
    home.mkdir()
    share.mkdir()

    day = date(2026, 9, 3)
    extra = rec(day, 14, 32, SLOT_NOW, osc_power=631.0)
    later = rec(day, 16, 5, SLOT_NOW, osc_power=628.0)
    st = _store_on(f, home, share)
    st.save([rec(day, 9, 0, SLOT_MORNING, osc_power=636.0), extra, later])

    check("all three moments are in the log", len(st.all_rows()) == 3)
    res = st.delete_record(extra)
    check("deleting it is reported without a problem", not res.problem, res.problem)
    check("it is gone from the log", len(st.all_rows()) == 2)
    check("the morning is untouched",
          st.all_rows()[0].get("slot") == SLOT_MORNING)
    check("the note names the moment",
          extra.stamp in store.read_json_map(st.local_tombstones, "deleted"))

    # The exact case that used to resurrect it: the moment is offered again.
    st.save([extra])
    check("saving it again does not bring it back", len(st.all_rows()) == 2)
    st.sync()
    check("and neither does a sync", len(st.all_rows()) == 2)

    if _openpyxl():
        from openpyxl import load_workbook
        ws = _values(load_workbook(share / "SPFE Values" / store.WORKBOOK_NAME))
        head = store._find_block(ws, day)
        # A moment is `parts` columns wide -- X, Y and SUM -- so the moment
        # beside it starts a whole moment further on, not one column.
        col = store.first_extra_col(f)
        nxt = store.moment_col(f, 3)
        check("its columns in the workbook are empty again",
              ws.cell(head, col).value is None, str(ws.cell(head, col).value))
        check("the moment beside it kept its own place",
              str(ws.cell(head, nxt).value) == "16:05",
              str(ws.cell(head, nxt).value))
        check("a new moment goes after the last one, not into the gap",
              store._target_column(ws, head, rec(day, 17, 40, SLOT_NOW), f)
              == store.moment_col(f, 4))
        check("and a moment already written keeps its place",
              store._target_column(ws, head, later, f) == nxt)


def test_references(tmp: Path):
    """The ranges somebody sets: stored beside the log, merged like the
    campaigns, and echoed into the workbook as a colour."""
    f = make_fields()
    home = tmp / "ref_home"
    share = tmp / "ref_share"
    home.mkdir()
    share.mkdir()
    st = _store_on(f, home, share)

    st.set_references({"osc_power": {"min": 600, "max": 660},
                       "humidity": {"min": 30, "max": 42}})
    back = st.references()
    check("a range comes back as it was set",
          [back["osc_power"]["min"], back["osc_power"]["max"]] == [600, 660])
    check("both copies were written",
          st.local_references.exists()
          and (share / "SPFE Values" / store.REFERENCES_NAME).exists())
    check("the writer and the time are recorded",
          "set_at" in back["osc_power"])

    # A reference set on another PC is a line of the same table, not a
    # replacement for it.
    store.write_json_map(share / "SPFE Values" / store.REFERENCES_NAME, "limits",
                         {"osc_bandwidth": {"min": 85, "max": 95,
                                            "set_at": "2026-09-01 08:00:00"}})
    merged = st.references()
    check("a range from the other PC joins the table",
          "osc_bandwidth" in merged and "osc_power" in merged)

    if _openpyxl():
        from openpyxl import load_workbook
        st.set_references({"osc_power": {"min": 600, "max": 660}})
        day = date(2026, 9, 2)
        st.save([rec(day, 9, 0, SLOT_MORNING, osc_power=212.0)])
        ws = _values(load_workbook(share / "SPFE Values" / store.WORKBOOK_NAME))
        head = store._find_block(ws, day)
        line = head + store.BLOCK_HEAD_LINES + [r.key for r in f.rows].index("osc_power")
        cell = ws.cell(line, store.col_morning(f))
        check("a value outside its range is filled red in the workbook",
              cell.fill.fgColor.rgb.endswith("C62828"), str(cell.fill.fgColor.rgb))
        check("and its text is white, because black could not be read on it",
              cell.font.color.rgb.endswith("FFFFFF"), str(cell.font.color.rgb))
        check("the reason is in the note",
              cell.comment is not None and "reference" in cell.comment.text)


def test_campaigns_carry_forward(tmp: Path):
    f = make_fields()
    home = tmp / "camp_home"
    share = tmp / "camp_share"
    home.mkdir()
    share.mkdir()
    st = _store_on(f, home, share)

    st.set_campaign(date(2026, 9, 1), "E5 electrons")
    check("the day that was named shows its own name",
          st.campaign_for_day(date(2026, 9, 1)) == ("E5 electrons", True))
    check("a later day carries it, and knows it is carried",
          st.campaign_for_day(date(2026, 9, 4)) == ("E5 electrons", False))
    check("an earlier day has nothing",
          st.campaign_for_day(date(2026, 8, 30)) == ("", False))

    st.set_campaign(date(2026, 9, 5), "")
    check("an emptied name ends the campaign rather than reviving the old one",
          st.campaign_for_day(date(2026, 9, 9)) == ("", False))
    check("and the entry stays, so the other PC cannot revive it either",
          "2026-09-05" in store.read_json_map(st.local_campaigns, "days"))

    mine = {"2026-09-01": {"name": "mine", "set_at": "2026-09-01 08:00:00"},
            "2026-09-02": {"name": "only mine", "set_at": "2026-09-02 08:00:00"}}
    theirs = {"2026-09-01": {"name": "theirs", "set_at": "2026-09-01 09:00:00"},
              "2026-09-03": {"name": "only theirs", "set_at": "2026-09-03 08:00:00"}}
    merged = store.merge_day_map(mine, theirs)
    check("the later of two names for the same day wins",
          merged["2026-09-01"]["name"] == "theirs")
    check("a day only one side knows is kept",
          [merged["2026-09-02"]["name"], merged["2026-09-03"]["name"]]
          == ["only mine", "only theirs"])

    if _openpyxl():
        from openpyxl import load_workbook
        st.save([rec(date(2026, 9, 2), 9, 0, SLOT_MORNING, osc_power=636.0)])
        ws = _values(load_workbook(share / "SPFE Values" / store.WORKBOOK_NAME))
        head = store._find_block(ws, date(2026, 9, 2))
        check("the workbook carries the campaign beside the date",
              ws.cell(head, 2).value == "E5 electrons", str(ws.cell(head, 2).value))


def test_sync_repairs_the_workbook(tmp: Path):
    """The workbook used to lose whole weeks: the writes ran day after day
    inside one try, so one locked file abandoned the rest of the run."""
    if not _openpyxl():
        print("  SKIP  workbook repair - openpyxl is not installed")
        return
    from openpyxl import load_workbook

    f = make_fields()
    home = tmp / "repair_home"
    share = tmp / "repair_share"
    home.mkdir()
    share.mkdir()
    st = _store_on(f, home, share)

    days = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]
    st.save([rec(d, 9, 0, SLOT_MORNING, osc_power=636.0) for d in days])
    wb_path = share / "SPFE Values" / store.WORKBOOK_NAME
    check("every day reached the workbook",
          all(store._find_block(_values(load_workbook(wb_path)), d) is not None
              for d in days))

    wb_path.unlink()                      # as if the whole run had been refused
    st.save([rec(date(2026, 9, 10), 9, 0, SLOT_MORNING, osc_power=636.0)])
    st.sync()
    ws = _values(load_workbook(wb_path))
    check("a sync brings the missing days back",
          all(store._find_block(ws, d) is not None for d in days))


def _openpyxl() -> bool:
    try:
        import openpyxl                   # noqa: F401
        return True
    except ImportError:
        return False


def _store_on(fields, home: Path, share: Path) -> store.Store:
    """A Store writing into two temporary folders. The scratch share is never
    touched by this test."""
    import os
    os.environ["APPDATA"] = str(home)
    cfg = dict(store.DEFAULT_CONFIG)
    st = store.Store(cfg, fields)
    st.local_csv = home / "SPFE_Values" / store.CSV_NAME
    st.share_root = str(share)
    return st


def test_block_shape_survives_a_new_quantity(tmp: Path):
    """A day written before a quantity was added must not take the new values
    one line too low."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("  SKIP  block shape check - openpyxl is not installed")
        return

    full = store.load_fields()
    short = store.Fields([(g, list(rows)) for g, rows in full.groups][:-1])
    path = tmp / "grown.xlsx"
    day = date(2026, 9, 2)

    store.write_workbook_record(
        path, short, rec(day, 9, 0, SLOT_MORNING, osc_power=636.0))
    store.write_workbook_record(
        path, full, rec(day, 18, 0, SLOT_EVENING, osc_power=620.0, notes="grown"))

    ws = _values(load_workbook(path))
    head = store._find_block(ws, day)
    row_of = {r.key: head + store.BLOCK_HEAD_LINES + i for i, r in enumerate(full.rows)}
    check("the block grew to hold the new quantity",
          str(ws.cell(row_of["notes"], 2).value) == "Notes",
          str(ws.cell(row_of["notes"], 2).value))
    check("and the new value landed on its own line",
          ws.cell(row_of["notes"], store.col_evening(full)).value == "grown",
          str(ws.cell(row_of["notes"], store.col_evening(full)).value))
    check("while the older column kept its value",
          ws.cell(row_of["osc_power"], store.col_morning(full)).value == "636 mW",
          str(ws.cell(row_of["osc_power"], store.col_morning(full)).value))


def test_rewrite_csv(tmp: Path):
    f = make_fields()
    path = tmp / "edit.csv"
    day = date(2026, 9, 1)
    r1 = rec(day, 9, 0, SLOT_MORNING, osc_power=636.0)
    r2 = rec(day, 18, 0, SLOT_EVENING, osc_power=620.0)
    store.append_csv(path, [r1, r2], f.columns)

    store.rewrite_csv(path, {r1.stamp: {"notes": "hard reset in the morning"}},
                      f.columns)
    rows = store.read_csv(path)
    check("the note landed on the right row",
          rows[0]["notes"] == "hard reset in the morning")
    check("the other row is untouched", rows[1]["notes"] == "")
    check("the numbers survived the rewrite", rows[0]["osc_power"] == "636.0")


# ---------------------------------------------------------------------------
# The workbook. Skipped, loudly, when openpyxl is missing.
# ---------------------------------------------------------------------------

def test_workbook(tmp: Path):
    try:
        import openpyxl                              # noqa: F401
        from openpyxl import load_workbook
    except ImportError:
        print("  SKIP  workbook checks - openpyxl is not installed")
        return

    f = make_fields()
    path = tmp / "SPFE values.xlsx"
    d1 = date(2026, 9, 1)
    d2 = date(2026, 9, 2)

    store.write_workbook_record(
        path, f, rec(d1, 9, 0, SLOT_MORNING, osc_power=636.0, osc_bandwidth=92.0),
        {"osc_power": "12.4 x the usual spread"})
    store.write_workbook_record(
        path, f, rec(d1, 18, 0, SLOT_EVENING, osc_power=620.0))

    ws = _values(load_workbook(path))
    head1 = store._find_block(ws, d1)
    check("the day's block was created", head1 is not None)
    check("Morning is in the fixed column",
          ws.cell(head1, store.col_morning(f)).value == "Morning")
    check("At the end is in the fixed column",
          ws.cell(head1, store.col_evening(f)).value == "At the end")

    row_of = {r.key: head1 + store.BLOCK_HEAD_LINES + i for i, r in enumerate(f.rows)}
    check("the morning value is written",
          ws.cell(row_of["osc_power"], store.col_morning(f)).value == "636 mW",
          str(ws.cell(row_of["osc_power"], store.col_morning(f)).value))
    check("the evening value is written",
          ws.cell(row_of["osc_power"], store.col_evening(f)).value == "620 mW")
    check("a quantity with no PV shows a dash",
          ws.cell(row_of["humidity"], store.col_morning(f)).value == "-")

    flagged = ws.cell(row_of["osc_power"], store.col_morning(f))
    check("the flagged cell is amber",
          flagged.fill.fgColor.rgb.endswith(store._FILL_FLAGGED), str(flagged.fill.fgColor.rgb))
    check("and its ink is black, not left to the theme",
          flagged.font.color.rgb == store._INK, str(flagged.font.color.rgb))
    check("and it carries the reason",
          flagged.comment is not None and "usual spread" in flagged.comment.text)
    plain = ws.cell(row_of["osc_bandwidth"], store.col_morning(f))
    check("an ordinary cell is white with black ink",
          plain.fill.fgColor.rgb.endswith(store._FILL_VALUE)
          and plain.font.color.rgb == store._INK)

    # --- a "record now" column -----------------------------------------
    store.write_workbook_record(path, f, rec(d1, 14, 32, SLOT_NOW, osc_power=628.0))
    ws = _values(load_workbook(path))
    check("the extra column is headed with the time",
          ws.cell(head1, store.first_extra_col(f)).value == "14:32",
          str(ws.cell(head1, store.first_extra_col(f)).value))
    check("the extra column holds its value",
          ws.cell(row_of["osc_power"], store.first_extra_col(f)).value == "628 mW")
    check("the morning column was not disturbed",
          ws.cell(row_of["osc_power"], store.col_morning(f)).value == "636 mW")

    # --- a second day, and a hand edit above it ------------------------
    wb = load_workbook(path)
    wb.active.cell(row_of["notes"], store.col_morning(f)).value = "typed straight into Excel"
    wb.save(path)

    store.write_workbook_record(path, f, rec(d2, 9, 0, SLOT_MORNING, osc_power=640.0))
    ws = _values(load_workbook(path))
    head2 = store._find_block(ws, d2)
    check("the second day got its own block below the first",
          head2 is not None and head2 > head1 + f.table_row_count)
    check("the hand-typed note in the first block survived",
          ws.cell(row_of["notes"], store.col_morning(f)).value == "typed straight into Excel")
    row2_of = {r.key: head2 + store.BLOCK_HEAD_LINES + i for i, r in enumerate(f.rows)}
    check("the second day's value is in its own block",
          ws.cell(row2_of["osc_power"], store.col_morning(f)).value == "640 mW")

    # --- writing over the same slot again ------------------------------
    store.write_workbook_record(path, f, rec(d2, 9, 0, SLOT_MORNING, osc_power=641.0))
    ws = _values(load_workbook(path))
    check("re-recording a slot replaces its column, it does not add one",
          ws.cell(row2_of["osc_power"], store.col_morning(f)).value == "641 mW"
          and ws.cell(head2, store.first_extra_col(f)).value in (None, ""))


def test_workbook_puts_every_number_in_its_own_cell(tmp: Path):
    """X, Y and SUM stand side by side in the workbook, as on screen and as on
    the paper form -- not joined into "150 ; -250 ; 2500" in one box."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("  SKIP  split-cell checks - openpyxl is not installed")
        return

    f = make_fields()
    path = tmp / "split.xlsx"
    day = date(2026, 9, 4)
    store.write_workbook_record(
        path, f, rec(day, 9, 0, SLOT_MORNING,
                     ba2loop2_1=136.0, ba2loop2_2=-276.0, ba2loop2_3=2500.0,
                     osc_power=636.0),
        refs={"ba2loop2_3": {"min": 2000, "max": 2400}})

    ws = _values(load_workbook(path))
    head = store._find_block(ws, day)
    parts = store.parts_per_moment(f)
    col = store.col_morning(f)
    check("a moment is as many columns as the widest quantity has numbers",
          parts == 3, str(parts))
    check("the moment's name stands across all of them",
          str(ws.cell(head, col).value) == "Morning"
          and any(str(r) == f"{ws.cell(head, col).coordinate}:"
                  f"{ws.cell(head, col + parts - 1).coordinate}"
                  for r in ws.merged_cells.ranges),
          str(list(ws.merged_cells.ranges)[:3]))
    check("and the line under it names each number",
          [ws.cell(head + 1, col + i).value for i in range(3)]
          == ["X", "Y", "SUM"],
          str([ws.cell(head + 1, col + i).value for i in range(3)]))

    row_of = {r.key: head + store.BLOCK_HEAD_LINES + i
              for i, r in enumerate(f.rows)}
    line = row_of["ba2loop2"]
    check("the three numbers are three cells",
          [ws.cell(line, col + i).value for i in range(3)]
          == ["136", "-280", "2500"],
          str([ws.cell(line, col + i).value for i in range(3)]))

    # Only the number that is out of range is red -- the whole point of the
    # split. SUM is 2500 against a maximum of 2400; X and Y have no range.
    check("only the number outside its range is red",
          ws.cell(line, col + 2).fill.fgColor.rgb.endswith(store._FILL_BAD)
          and ws.cell(line, col).fill.fgColor.rgb.endswith(store._FILL_VALUE),
          str(ws.cell(line, col).fill.fgColor.rgb))
    check("and the red one has white ink, because black cannot be read on it",
          ws.cell(line, col + 2).font.color.rgb.endswith("FFFFFF"))

    # A quantity that IS one number lies across the whole moment, so there is
    # no empty box beside it.
    single = row_of["osc_power"]
    check("a single-number quantity takes the whole moment",
          any(r.min_row == single and r.min_col == col
              and r.max_col == col + parts - 1 for r in ws.merged_cells.ranges),
          str(ws.cell(single, col).value))


def test_a_block_in_the_old_layout_is_left_alone(tmp: Path):
    """Values go into a block BY POSITION. A block written before the numbers
    were split has one heading line, not two, so writing into it would put
    every value one line too high and under the wrong heading. It has to be
    refused and said out loud, not guessed at."""
    try:
        from openpyxl import Workbook, load_workbook
    except ImportError:
        print("  SKIP  old-layout guard - openpyxl is not installed")
        return

    f = make_fields()
    path = tmp / "old.xlsx"
    day = date(2026, 9, 5)

    # A block the way the program used to write one: the date, then straight
    # into the quantities, with all three numbers in a single cell.
    wb = Workbook()
    ws = wb.active
    ws.title = store.SHEET_VALUES
    ws.cell(1, 1).value = day
    ws.cell(1, store.FIRST_VALUE_COL).value = "Morning"
    ws.cell(1, store.FIRST_VALUE_COL + 1).value = "At the end"
    for i, row_def in enumerate(f.rows):
        ws.cell(2 + i, 2).value = row_def.label
    ws.cell(2 + [r.key for r in f.rows].index("ba2loop2"),
            store.FIRST_VALUE_COL).value = "150;-250;2500"
    wb.save(path)

    said = []
    store.write_workbook_record(
        path, f, rec(day, 18, 0, SLOT_EVENING, osc_power=620.0),
        log_fn=said.append)

    ws = _values(load_workbook(path))
    check("the old block is left exactly as it was",
          ws.cell(2 + [r.key for r in f.rows].index("ba2loop2"),
                  store.FIRST_VALUE_COL).value == "150;-250;2500")
    check("and the refusal names the way out",
          any("rebuild_workbook" in s for s in said), str(said))


def test_trends_sheet_and_charts(tmp: Path):
    """The picture of it: one line per day, and a chart per group pointing at
    that line. The day blocks cannot be charted -- each day is its own block
    with a blank line between, so one quantity across thirty days is thirty
    separate cells, not a range."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("  SKIP  chart checks - openpyxl is not installed")
        return

    f = make_fields()
    path = tmp / "trends.xlsx"
    days = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]
    recs = []
    for i, d in enumerate(days):
        recs.append(rec(d, 9, 0, SLOT_MORNING, osc_power=630.0 + i))
        recs.append(rec(d, 18, 0, SLOT_EVENING, osc_power=620.0 + i))
    for r in recs:
        store.write_workbook_record(path, f, r)
    store.write_workbook_trends(path, f, recs)

    wb = load_workbook(path)
    check("the workbook has a Trends sheet and a Charts sheet",
          store.SHEET_TRENDS in wb.sheetnames and store.SHEET_CHARTS in wb.sheetnames,
          str(wb.sheetnames))
    ws = wb[store.SHEET_TRENDS]
    check("one line per day, not per moment",
          [ws.cell(r, 1).value.date() if hasattr(ws.cell(r, 1).value, "date")
           else ws.cell(r, 1).value for r in (2, 3, 4)] == days,
          str([ws.cell(r, 1).value for r in (2, 3, 4)]))

    headings = [ws.cell(1, c).value for c in range(2, ws.max_column + 1)]
    check("the morning and the end of the day are two curves of one quantity",
          "Power - morning" in headings and "Power - at the end" in headings,
          str(headings[:6]))
    col = 2 + headings.index("Power - morning")
    check("and the numbers under them are numbers, not text",
          [ws.cell(r, col).value for r in (2, 3, 4)] == [630, 631, 632],
          str([ws.cell(r, col).value for r in (2, 3, 4)]))
    check("a text row is not charted",
          not any("Notes" in str(h) for h in headings), str(headings))

    charts = wb[store.SHEET_CHARTS]._charts
    check("there is a chart per group", len(charts) == len(f.groups),
          f"{len(charts)} charts vs {len(f.groups)} groups")
    check("each one is named after its group",
          {str(c.title.tx.rich.p[0].r[0].t) for c in charts}
          == {g for g, _rows in f.groups},
          str([str(c.title.tx.rich.p[0].r[0].t) for c in charts]))

    # Written again from scratch every time, so they can never go stale.
    store.write_workbook_trends(path, f, recs)
    wb = load_workbook(path)
    check("redrawing them does not pile up a second set",
          len(wb[store.SHEET_CHARTS]._charts) == len(f.groups),
          str(len(wb[store.SHEET_CHARTS]._charts)))
    check("and the day blocks are untouched by it",
          store._find_block(_values(wb), days[0]) is not None)


def test_workbook_locked(tmp: Path):
    """A workbook that cannot be written must raise WorkbookLocked, so the
    caller can say 'the values are saved, close Excel' instead of losing them."""
    try:
        import openpyxl                              # noqa: F401
    except ImportError:
        print("  SKIP  locked-workbook check - openpyxl is not installed")
        return

    f = make_fields()
    path = tmp / "locked.xlsx"
    store.write_workbook_record(path, f, rec(date(2026, 9, 1), 9, 0, SLOT_MORNING,
                                             osc_power=636.0))
    handle = open(path, "r+b")          # Windows: an open handle blocks the replace
    try:
        try:
            store.write_workbook_record(
                path, f, rec(date(2026, 9, 1), 18, 0, SLOT_EVENING, osc_power=620.0))
            check("a locked workbook is reported", False,
                  "the write went through - is this not Windows?")
        except store.WorkbookLocked:
            check("a locked workbook is reported as WorkbookLocked", True)
        except PermissionError as exc:
            check("a locked workbook is reported as WorkbookLocked", False,
                  f"raw PermissionError leaked out: {exc}")
    finally:
        handle.close()
    leftovers = list(tmp.glob("*.saving.xlsx"))
    check("no half-written file is left behind", not leftovers, str(leftovers))


def main():
    print("spfe_store")
    with tempfile.TemporaryDirectory(prefix="spfe_test_") as td:
        tmp = Path(td)
        test_fields_file()
        test_duplicate_key_is_refused(tmp)
        test_csv_append_and_skip(tmp)
        test_history_is_per_slot(tmp)
        test_scheduled_slots_skip_weekends_and_the_future()
        test_catch_up_start_is_bounded()
        test_slot_for_comparison()
        test_format_cell()
        test_row_parts()
        test_rounding()
        test_references(tmp)
        test_parse_cell()
        test_display_label_and_defaults()
        test_a_default_does_not_reach_into_the_past()
        test_rewrite_csv(tmp)
        test_workbook(tmp)
        test_workbook_puts_every_number_in_its_own_cell(tmp)
        test_a_block_in_the_old_layout_is_left_alone(tmp)
        test_trends_sheet_and_charts(tmp)
        test_block_shape_survives_a_new_quantity(tmp)
        test_workbook_locked(tmp)
        test_delete_leaves_a_tombstone(tmp)
        test_campaigns_carry_forward(tmp)
        test_sync_repairs_the_workbook(tmp)
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
