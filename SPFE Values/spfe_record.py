"""
spfe_record.py  --  turning a moment in time into a recorded column.

This is the layer between the archiver (spfe_core), the files (spfe_store) and
the window (spfe_t): read every PV as it stood at a moment, judge each number
against the same slot on previous days, and hand back a Record plus the reasons
for anything that looks wrong.

Nothing here touches a GUI, so the whole recording path can be exercised
without opening a window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Inside CSS Logger the archiver functions come from that program's own module;
# standalone they come from the copy shipped here. The names are identical, so
# nothing below this line changes when the widget moves.
try:
    import cpva_core as core          # CSS Logger
except ImportError:
    import spfe_core as core          # standalone

import spfe_stats as stats
from spfe_store import (
    Fields, Record, Row, SLOT_NOW, apply_defaults, history_for,
    slot_for_comparison,
)


@dataclass
class Reading:
    """What came back for one moment, before it is written anywhere."""
    record: Record
    flags: dict[str, str] = field(default_factory=dict)     # row key -> reason
    missing: list[str] = field(default_factory=list)        # PVs that gave nothing
    measured: int = 0                                       # values that came from a PV

    @property
    def anything_flagged(self) -> bool:
        return bool(self.flags)


def fetch_moment(fields: Fields, when: datetime, slot: str,
                 *, timeout: float = core.CPVA_HTTP_TIMEOUT,
                 log_fn=None, cancel_fn=None) -> Record:
    """Read every configured PV as it stood at `when`.

    A PV that does not answer leaves its number empty and is noted; the rest of
    the row is still recorded. Chiller Log does the opposite -- one dead PV
    aborts the whole run and every value already fetched is thrown away.
    """
    pvs = fields.pv_names
    rec = Record(when=when, slot=slot, source="cpva")
    if not pvs:
        return rec

    before_ns = core.dt_to_ns(when)
    samples = core.cpva_fetch_last_before_many(
        pvs, before_ns, timeout=timeout, cancel_fn=cancel_fn)

    for row in fields.rows:
        if row.is_manual:
            continue
        for column, pv in zip(row.columns, row.pvs):
            if not pv:
                continue
            s = samples.get(pv)
            if s is None:
                if log_fn:
                    log_fn(f"   {pv}: nothing archived before {when:%Y-%m-%d %H:%M}")
                continue
            value = core.cpva_decode_value(s)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                if log_fn:
                    log_fn(f"   {pv}: value is not a number ({value!r})")
                continue
            rec.values[column] = float(value)
    return rec


def judge_columns(rec: Record, fields: Fields, rows: list[dict],
                  cfg: dict) -> dict[str, str]:
    """Reasons for every NUMBER of `rec` that stands out against its history.

    Returns ``{CSV column: sentence}``; a number not in the dictionary is fine,
    or has no history yet. The value itself is never changed or dropped -- a
    number nobody can see is worse than a number nobody trusts.

    One verdict per number, never one per quantity. The table gives X, Y and
    SUM a cell each and so does the workbook, and each cell is coloured for
    itself -- marking all three because one of them is unusual is how an alarm
    stops meaning anything. (There used to be a `judge_record` beside this one,
    rolling the three verdicts into a single sentence, because the workbook had
    one cell for all three numbers. It has three now, and that function is
    gone.)
    """
    limit = int(cfg.get("history_days", 20))
    min_history = int(cfg.get("min_history", 8))
    mad_factor = float(cfg.get("mad_factor", 5.0))
    flat_pct = float(cfg.get("flat_tolerance_pct", 20.0))
    slot = slot_for_comparison(rec, cfg)
    slot_label = {"morning": "morning", "evening": "evening"}.get(slot, "")

    out: dict[str, str] = {}
    for row in fields.rows:
        if not row.check or row.is_text:
            continue
        for column in row.columns:
            raw = rec.values.get(column, "")
            if raw in ("", None):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            history = history_for(rows, column, slot, limit, before=rec.day)
            verdict = stats.judge(
                value, history,
                min_history=min_history,
                mad_factor=mad_factor,
                flat_tolerance_pct=flat_pct,
                slot_label=slot_label,
            )
            if verdict.flagged:
                out[column] = verdict.reason
    return out


def read_and_judge(fields: Fields, when: datetime, slot: str, rows: list[dict],
                   cfg: dict, *, log_fn=None, cancel_fn=None) -> Reading:
    """The whole path for one moment: fetch, fill in the defaults, then judge."""
    rec = fetch_moment(fields, when, slot,
                       timeout=float(cfg.get("http_timeout", 10.0)),
                       log_fn=log_fn, cancel_fn=cancel_fn)
    missing = [pv for row in fields.rows if not row.is_manual
               for column, pv in zip(row.columns, row.pvs)
               if pv and column not in rec.values]

    # Counted before the defaults go in: "did any PV answer?" is what decides
    # whether this moment is worth recording, and a default is not an answer.
    measured = len(rec.values)
    # The day matters: a setting that was only written down later must not be
    # claimed for a day that predates it (Row.default_from).
    apply_defaults(fields, rec.values, rec.day)

    flags = judge_columns(rec, fields, rows, cfg)
    return Reading(record=rec, flags=flags, missing=missing, measured=measured)


def record_now(fields: Fields, rows: list[dict], cfg: dict,
               *, log_fn=None, cancel_fn=None) -> Reading:
    """The Record now button: this moment, its own column."""
    when = datetime.now(core.TZ_PRAGUE).replace(second=0, microsecond=0)
    return read_and_judge(fields, when, SLOT_NOW, rows, cfg,
                          log_fn=log_fn, cancel_fn=cancel_fn)
