"""
spfe_store.py  --  where the SPFE values are kept.

Two files live side by side in ``<scratch>\\Software\\SPFE Values``:

  SPFE values.xlsx   for people. One block per day, laid out like the paper
                     table: rows are the quantities, columns are Morning,
                     At the end, and any extra moment somebody recorded.
  spfe_log.csv       for the program. One flat row per recorded moment, one
                     column per number. This is what the out-of-range
                     statistics read, and what survives if the workbook is
                     ever mangled by hand.

Three rules this module exists to keep:

1. **A measurement is never lost.** Every record is written to the local mirror
   in %APPDATA% first, where nothing can fail, and only then pushed to the
   share. A dead share or a workbook open in Excel therefore costs a delay,
   never data. Chiller Log does the opposite -- an unguarded append at the end
   of a multi-minute catch-up -- and throws the whole run away if the file
   happens to be open.

2. **The workbook is append-only.** A new day appends a block at the bottom; a
   new moment fills a fresh column inside that day's block. Nothing above is
   ever rewritten, so a note somebody typed straight into Excel stays.

3. **The share is probed, never assumed.** The two share names resolve to
   different machines and a plain isdir() on the wrong one blocks for about 48
   seconds. The probing here is the pattern from Diagnostic/shared_pvs.py:
   daemon threads, decide as soon as the highest-priority candidate is known
   good, never a ThreadPoolExecutor (its workers are joined at exit, which
   would move the 48 s stall to shutdown).

No GUI toolkit is imported. openpyxl is imported lazily, so the CSV half keeps
working -- and the tests keep running -- on a machine without it.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Deliberately not imported from spfe_core: that module pulls in requests and
# orjson, and the tests for this file must run on a bare interpreter.
TZ_PRAGUE = ZoneInfo("Europe/Prague")


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR      = get_app_dir()
FIELDS_FILE  = APP_DIR / "spfe_fields.json"
CONFIG_FILE  = APP_DIR / "spfe_config.json"

WORKBOOK_NAME = "SPFE values.xlsx"
CSV_NAME      = "spfe_log.csv"

# Two small side files that live beside the CSV, local and shared, and are
# merged key by key rather than "whichever is longer" (see merge_day_map).
CAMPAIGNS_NAME  = "spfe_campaigns.json"
TOMBSTONES_NAME = "spfe_tombstones.json"

# The ranges somebody said each number is allowed to be in. Beside the log for
# the same reason as the campaigns: a reference is a decision about the
# machine, not about this PC, so everybody's window has to colour the same
# values. Merged number by number, so two people setting two different
# quantities never wipe each other.
REFERENCES_NAME = "spfe_references.json"

# Local only, and deliberately so: the edits this PC has made and not yet got
# onto the share. Replayed by sync(), which otherwise only ever pushes rows the
# share is MISSING and would never notice a changed value.
PENDING_EDITS_NAME = "pending_edits.json"

# Priority order, never "whichever answered first": a machine that can reach
# both legs must always pick the same file, or the two sites silently diverge.
SHARE_CANDIDATES: tuple[str, ...] = (
    r"\\hapls-share.cs.eli-beams.eu\scratch\Software",   # office leg
    r"\\hapls-share.lcs.local\scratch\Software",         # lab leg
)

SLOT_MORNING = "morning"
SLOT_EVENING = "evening"
SLOT_NOW     = "now"

SLOT_HEADINGS = {
    SLOT_MORNING: "Morning",
    SLOT_EVENING: "At the end",
}


def local_dir() -> Path:
    """The mirror that cannot fail. Per user, survives a redeploy."""
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "SPFE_Values"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "slot_morning": "09:00",
    "slot_evening": "18:00",
    "weekdays_only": True,
    "share_root": "",
    "share_subdir": "SPFE Values",
    "share_probe_timeout_s": 3.0,
    "history_days": 20,
    "min_history": 8,
    "mad_factor": 5.0,
    "flat_tolerance_pct": 20.0,
    # How close to the edge of a reference still counts as "nearly out". One
    # number for the whole program; a quantity that wants its own carries it in
    # its own reference entry.
    "warn_pct": 5.0,
    "http_timeout": 10.0,
    "catch_up_on_start": True,
    "catch_up_max_days": 30,
}


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_FILE
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass                      # a broken config must not stop the program
    return cfg


def save_config(cfg: dict, path: Path | None = None) -> None:
    """Unlike Chiller Log's, this one creates the file if it is missing --
    there, a fresh installation silently never persists anything."""
    path = path or CONFIG_FILE
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The quantities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One line of the table."""
    key: str
    group: str
    label: str
    pvs: tuple[str, ...] = ()
    join: str = ";"
    unit: str = ""
    decimals: int = 2
    manual: str = ""            # "", "text" or "number"
    check: bool = True
    default: str = ""           # what a manual row shows until it is typed over
    default_from: str = ""      # ISO day the default starts applying on
    url: str = ""               # makes the label a link
    # One rounding step per number of the row: 10 shows the value to the
    # nearest ten, 0 leaves it alone. A tuple, not a list, because Row is
    # frozen -- a list would be shared, mutable and unhashable.
    round_to: tuple[float, ...] = ()

    @property
    def is_manual(self) -> bool:
        return bool(self.manual) or not self.pvs

    def default_applies(self, day: date | None) -> bool:
        """Whether this row's `default` counts on `day`.

        The Spider settings and the best GDD were only ever true from the day
        somebody wrote them down. Showing them on days before that would put a
        number on the screen -- and into the files -- that nobody measured and
        nobody agreed to.
        """
        if not self.default:
            return False
        if not self.default_from or day is None:
            return True
        return day.isoformat() >= self.default_from

    def step_for(self, column: str) -> float:
        """The rounding step of one number of this row, 0 for none."""
        try:
            idx = self.columns.index(column)
        except ValueError:
            return 0.0
        if idx < len(self.round_to):
            return float(self.round_to[idx])
        return 0.0

    @property
    def is_text(self) -> bool:
        return self.manual == "text"

    @property
    def component_names(self) -> list[str]:
        """What the numbers of this row are called: ['X', 'Y', 'SUM'].

        Read out of the label itself -- "Input - BA2Loop2 (X,Y,SUM)" -- so a
        quantity is still described in one place, spfe_fields.json, and adding
        a number to a row cannot leave its name behind. A label that does not
        end in a matching list falls back to '#1', '#2'; a single-number row
        has no component name at all, because the row IS the number.
        """
        n = len(self.columns)
        if n <= 1:
            return [""]
        label = self.label
        if "(" in label and label.rstrip().endswith(")"):
            inner = label[label.rfind("(") + 1:label.rfind(")")]
            parts = [p.strip() for p in inner.split(",") if p.strip()]
            if len(parts) == n:
                return parts
        return [f"#{i + 1}" for i in range(n)]

    @property
    def base_label(self) -> str:
        """The label without the list of component names.

        The table gives every number a line of its own with its own name
        beside it, so repeating "(X,Y,SUM)" on all three lines says nothing and
        costs a third of the width of the column.
        """
        label = self.label
        if len(self.columns) <= 1:
            return label
        if "(" in label and label.rstrip().endswith(")"):
            inner = label[label.rfind("(") + 1:label.rfind(")")]
            parts = [p.strip() for p in inner.split(",") if p.strip()]
            if len(parts) == len(self.columns):
                return label[:label.rfind("(")].strip(" -–")
        return label

    def part_label(self, column: str) -> str:
        """The name of one number of this row, '' for a single-number row."""
        try:
            idx = self.columns.index(column)
        except ValueError:
            return ""
        names = self.component_names
        return names[idx] if idx < len(names) else f"#{idx + 1}"

    @property
    def display_label(self) -> str:
        """The label as it stands in the Quantity column: the unit belongs here,
        not in the value cell. The operator types the number and nothing else."""
        label = self.base_label
        if not self.unit:
            return label
        return f"{label} ({self.unit})" if label else f"({self.unit})"

    @property
    def columns(self) -> list[str]:
        """CSV column name per number in this row."""
        if len(self.pvs) > 1:
            return [f"{self.key}_{i + 1}" for i in range(len(self.pvs))]
        return [self.key]


@dataclass
class Fields:
    groups: list[tuple[str, list[Row]]] = field(default_factory=list)

    @property
    def rows(self) -> list[Row]:
        return [r for _, rows in self.groups for r in rows]

    @property
    def columns(self) -> list[str]:
        return [c for r in self.rows for c in r.columns]

    @property
    def pv_names(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            for pv in r.pvs:
                if pv and pv not in seen:
                    seen.append(pv)
        return seen

    def row_by_key(self, key: str) -> Row | None:
        for r in self.rows:
            if r.key == key:
                return r
        return None

    @property
    def table_row_count(self) -> int:
        return len(self.rows)


def _rounding_steps(raw, count: int) -> tuple[float, ...]:
    """The "round" field of one row as one step per number.

    A bare number applies to every number of the row; a list gives each its
    own. The result is always `count` long -- a three-number row given two
    steps must not read past the end of the list at format time -- and a
    negative step is refused rather than turned into a division by nothing.
    """
    if raw is None:
        return ()
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        steps = [float(raw)] * count
    elif isinstance(raw, (list, tuple)):
        steps = []
        for v in raw:
            try:
                steps.append(float(v))
            except (TypeError, ValueError):
                steps.append(0.0)
    else:
        return ()
    steps = [s if s > 0 else 0.0 for s in steps]
    steps += [0.0] * (count - len(steps))
    return tuple(steps[:count])


def load_fields(path: Path | None = None) -> Fields:
    """Read spfe_fields.json. Raises ValueError with a plain sentence on a
    duplicate key -- two rows sharing a key would silently overwrite each other
    in the CSV, and the damage would only show up weeks later."""
    path = path or FIELDS_FILE
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    groups: list[tuple[str, list[Row]]] = []
    seen: set[str] = set()

    for g in data.get("groups", []):
        label = str(g.get("label", ""))
        rows: list[Row] = []
        for r in g.get("rows", []):
            key = str(r.get("key", "")).strip()
            if not key:
                raise ValueError(f"A row in group '{label}' has no key.")
            if key in seen:
                raise ValueError(f"The key '{key}' is used more than once.")
            seen.add(key)
            pvs = tuple(str(p).strip() for p in r.get("pvs", []))
            rows.append(Row(
                key=key,
                group=label,
                label=str(r.get("label", "")),
                pvs=pvs,
                join=str(r.get("join", ";")),
                unit=str(r.get("unit", "")),
                decimals=int(r.get("decimals", 2)),
                manual=str(r.get("manual", "")),
                check=bool(r.get("check", True)),
                default=str(r.get("default", "")).strip(),
                default_from=str(r.get("default_from", "")).strip(),
                url=str(r.get("url", "")).strip(),
                round_to=_rounding_steps(r.get("round"), max(1, len(pvs))),
            ))
        if rows:
            groups.append((label, rows))

    if not groups:
        raise ValueError("spfe_fields.json defines no quantities.")
    return Fields(groups)


# ---------------------------------------------------------------------------
# One recorded moment
# ---------------------------------------------------------------------------

CSV_HEAD = ["datetime", "date", "slot", "source"]


@dataclass
class Record:
    when: datetime                       # Prague
    slot: str                            # morning / evening / now
    source: str = "cpva"                 # cpva / manual
    values: dict[str, object] = field(default_factory=dict)   # column -> value

    @property
    def stamp(self) -> str:
        return f"{self.when:%Y-%m-%d %H:%M:%S}"

    @property
    def day(self) -> date:
        return self.when.date()

    def heading(self) -> str:
        """What stands above this column in the workbook."""
        return SLOT_HEADINGS.get(self.slot, f"{self.when:%H:%M}")

    def to_csv_row(self, columns: list[str]) -> dict:
        row = {
            "datetime": self.stamp,
            "date": f"{self.when:%Y-%m-%d}",
            "slot": self.slot,
            "source": self.source,
        }
        for c in columns:
            v = self.values.get(c, "")
            row[c] = "" if v is None else v
        return row


# ---------------------------------------------------------------------------
# The CSV of record
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def append_csv(path: Path, records: list[Record], columns: list[str],
               deleted: set | None = None) -> int:
    """Append the records that are not already there. Returns how many landed.

    A timestamp already in the file is skipped, so running the catch-up twice
    is a no-op. A timestamp somebody deleted is skipped too -- without that,
    the next run would write the deleted moment straight back.
    """
    if not records:
        return 0
    header = CSV_HEAD + columns
    existing = {r.get("datetime", "") for r in read_csv(path)}
    if deleted:
        existing |= set(deleted)
    fresh = [r for r in records if r.stamp not in existing]
    if not fresh:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for rec in fresh:
            w.writerow(rec.to_csv_row(columns))
    return len(fresh)


def rewrite_csv(path: Path, edits: dict[str, dict[str, str]],
                columns: list[str]) -> int:
    """Patch named columns of named rows, in place. Returns how many rows
    were actually patched.

    The count is the point: a file that does not hold the moment being edited
    is silently left alone, and a caller that assumed otherwise would report
    "saved" with nothing written.

    Written beside the file and moved over it, so an interrupted write can
    never leave half a log behind.
    """
    rows = read_csv(path)
    if not rows:
        return 0
    hit = 0
    for row in rows:
        patch = edits.get(row.get("datetime", ""))
        if not patch:
            continue
        row.update(patch)
        hit += 1
    if not hit:
        return 0

    header = CSV_HEAD + columns
    tmp = path.with_name(path.stem + ".saving.csv")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in header})
    os.replace(tmp, path)
    return hit


def remove_from_csv(path: Path, stamps: set, columns: list[str]) -> int:
    """Drop whole rows by timestamp. Returns how many went.

    The sibling of rewrite_csv, down to the write-beside-and-move: the log is
    the machine-readable record and half of it is worse than none of it.
    """
    if not stamps:
        return 0
    rows = read_csv(path)
    if not rows:
        return 0
    keep = [r for r in rows if r.get("datetime", "") not in stamps]
    gone = len(rows) - len(keep)
    if not gone:
        return 0

    header = CSV_HEAD + columns
    tmp = path.with_name(path.stem + ".saving.csv")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for row in keep:
            w.writerow({k: row.get(k, "") for k in header})
    os.replace(tmp, path)
    return gone


def history_for(rows: list[dict], column: str, slot: str,
                limit: int, before: date | None = None) -> list[float]:
    """The most recent numbers of one column in one slot.

    Morning is compared with mornings and evening with evenings, because the
    two are genuinely different operating points -- comparing them would widen
    the spread until nothing ever looks wrong.
    """
    out: list[float] = []
    for r in reversed(rows):                    # newest first
        if r.get("slot") != slot:
            continue
        if before is not None:
            try:
                if datetime.strptime(r.get("date", ""), "%Y-%m-%d").date() >= before:
                    continue
            except ValueError:
                continue
        raw = r.get(column, "")
        if raw in ("", None):
            continue
        try:
            out.append(float(raw))
        except (TypeError, ValueError):
            continue
        if len(out) >= limit:
            break
    return out


def slot_for_comparison(rec: Record, cfg: dict) -> str:
    """Which slot's history a record is judged against.

    A "record now" column has no history of its own, so it borrows the nearer
    of the two scheduled slots.
    """
    if rec.slot in (SLOT_MORNING, SLOT_EVENING):
        return rec.slot
    m = _parse_hhmm(cfg.get("slot_morning", "09:00"))
    e = _parse_hhmm(cfg.get("slot_evening", "18:00"))
    mid = (m[0] * 60 + m[1] + e[0] * 60 + e[1]) / 2
    minutes = rec.when.hour * 60 + rec.when.minute
    return SLOT_MORNING if minutes < mid else SLOT_EVENING


def _parse_hhmm(s: str) -> tuple[int, int]:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return 9, 0


# ---------------------------------------------------------------------------
# The two side files: campaigns and deleted moments
# ---------------------------------------------------------------------------
#
# Both are a plain {key: entry} map, written to the local mirror and to the
# share, and merged KEY BY KEY -- never "whichever file is longer", the way the
# log itself is merged. Two people typing on two PCs each own the days they
# touched, and neither can wipe the other's.

def read_json_map(path: Path, section: str) -> dict:
    """One section of a side file, or {} for anything unreadable. A side file
    is a convenience; it may never stop the program."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        got = data.get(section)
        return dict(got) if isinstance(got, dict) else {}
    except Exception:
        return {}


def write_json_map(path: Path, section: str, entries: dict) -> None:
    """Write beside the file and move it over, as everywhere else here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".saving")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({section: entries}, f, indent=2, ensure_ascii=False,
                  sort_keys=True)
    os.replace(tmp, path)


def merge_day_map(a: dict, b: dict, *, stamp_field: str = "set_at") -> dict:
    """Union of two side-file maps.

    A key on one side only is kept -- an entry is never removed, or the two
    copies would resurrect each other for ever. A key on both sides is decided
    by the later `stamp_field`; the stamps are `%Y-%m-%d %H:%M:%S`, so
    comparing the strings is comparing the times.
    """
    out = dict(a)
    for key, entry in b.items():
        old = out.get(key)
        if old is None:
            out[key] = entry
            continue
        if str(entry.get(stamp_field, "")) > str(old.get(stamp_field, "")):
            out[key] = entry
    return out


def campaign_for(entries: dict, day: date) -> tuple[str, bool]:
    """(name, is_its_own) for a day.

    A campaign is typed once and carried forward until somebody types another
    one, so a day with no entry of its own shows the newest entry before it.
    An emptied name is stored as "" and carried too -- that is how a campaign
    is ended, and why an entry is never deleted.
    """
    want = day.isoformat()
    own = entries.get(want)
    if own is not None:
        return str(own.get("name", "")), True
    best = ""
    for key in sorted(entries):
        if key <= want:
            best = str(entries[key].get("name", ""))
        else:
            break
    return best, False


def now_stamp() -> str:
    return datetime.now(TZ_PRAGUE).strftime("%Y-%m-%d %H:%M:%S")


def who() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "?"


# ---------------------------------------------------------------------------
# Reaching the share
# ---------------------------------------------------------------------------

def candidate_roots(override: str = "") -> list[str]:
    override = (override or "").strip()
    if override:
        return [override]
    return list(SHARE_CANDIDATES)


def _first_reachable(roots: list[str], timeout_s: float) -> str:
    """Highest-priority reachable root, or "" if none answered in time.

    One daemon thread per root, so a dead host never queues in front of a live
    one, and the answer is returned the moment the first entry in priority
    order is known good. Waiting for every probe would cost the full timeout on
    any machine that cannot see one of the hosts -- that is, on every start.
    """
    state: dict[str, bool] = {}
    cond = threading.Condition()

    def probe(root: str):
        try:
            ok = os.path.isdir(root)
        except OSError:
            ok = False
        with cond:
            state[root] = ok
            cond.notify_all()

    for root in roots:
        threading.Thread(target=probe, args=(root,), daemon=True,
                         name="spfe-share-probe").start()

    deadline = time.monotonic() + max(0.1, timeout_s)
    with cond:
        while True:
            undecided = False
            for root in roots:
                if root not in state:
                    undecided = True          # a preferred root may still win
                    break
                if state[root]:
                    return root
            if not undecided:
                return ""                     # all answered, none reachable
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            cond.wait(remaining)
        for root in roots:                    # out of time: take what answered
            if state.get(root):
                return root
    return ""


def resolve_share(cfg: dict, cached_root: str = "") -> tuple[str, str]:
    """(root_or_empty, source) with source in override / cache / probe / none."""
    override = str(cfg.get("share_root", "") or "").strip()
    roots = candidate_roots(override)
    src = "override" if override else ""
    cached_root = (cached_root or "").strip()
    timeout = float(cfg.get("share_probe_timeout_s", 3.0))

    if cached_root and cached_root in roots:
        if _first_reachable([cached_root], timeout):
            return cached_root, (src or "cache")

    root = _first_reachable(roots, timeout)
    if root:
        return root, (src or "probe")
    return "", "none"


def share_dir(root: str, cfg: dict) -> Path:
    """The program's own folder under the share root."""
    sub = str(cfg.get("share_subdir", "SPFE Values"))
    p = Path(root)
    if p.name.lower() != sub.lower():
        p = p / sub
    return p


# ---------------------------------------------------------------------------
# The workbook
# ---------------------------------------------------------------------------

# The sheet, column by column:
#
#   A            the group             B    the quantity
#   C ..         the first moment, one column PER NUMBER of the quantity --
#                X, Y and SUM side by side, the way the paper form has them
#   then         the second moment, the same width, and so on
#
# A block is two heading lines, not one: the moment's name across its own
# columns, and under it the name of each number.
FIRST_VALUE_COL = 3          # column C -- A is the group, B is the quantity
MAX_EXTRA_COLS  = 20         # extra MOMENTS, not extra columns

SHEET_VALUES = "SPFE values"   # the days, one block each
SHEET_TRENDS = "Trends"        # one line per day, for the charts to point at
SHEET_CHARTS = "Charts"        # the pictures themselves


def parts_per_moment(fields: Fields) -> int:
    """How many columns one moment takes: the widest quantity's count.

    Derived from the field file, never written down, so a quantity given a
    fourth number widens every block instead of falling off the end of it.
    """
    return max((len(r.columns) for r in fields.rows), default=1)


def moment_col(fields: Fields, index: int) -> int:
    """The first column of the index-th moment. 0 = Morning, 1 = At the end."""
    return FIRST_VALUE_COL + index * parts_per_moment(fields)


def col_morning(fields: Fields) -> int:
    return moment_col(fields, 0)


def col_evening(fields: Fields) -> int:
    return moment_col(fields, 1)


def first_extra_col(fields: Fields) -> int:
    return moment_col(fields, 2)


def part_headings(fields: Fields) -> list[str]:
    """What each column of a moment is called: ['X', 'Y', 'SUM'].

    Only a name every multi-number quantity agrees on at that position is used;
    where they disagree the heading is blank and the quantity's own label --
    "Input - BA2Loop2 (X,Y,SUM)" -- is what says which number is which.
    """
    out = []
    for i in range(parts_per_moment(fields)):
        names = {r.component_names[i] for r in fields.rows
                 if len(r.columns) > 1 and i < len(r.component_names)}
        out.append(names.pop() if len(names) == 1 else "")
    return out

_FILL_HEADER  = "C9C9C9"     # the day header: a step darker than the quantity
                             # rows AND than the screen's own table header, so
                             # the line that starts a day is the heavier one
_FILL_GROUP   = "F2F2F2"     # the group-label column
_FILL_VALUE   = "FFFFFF"
_FILL_FLAGGED = "FFE0B2"     # light amber -- black text stays readable on it
# The two reference colours, the same two the window uses. A background is
# never chosen without its ink: the amber and the yellow keep black text, the
# red gets white bold text, which is the only thing readable on it.
_FILL_WARN    = "FFEB3B"
_FILL_BAD     = "C62828"
_INK          = "FF000000"
_INK_ON_BAD   = "FFFFFFFF"


def _styles():
    """Built lazily so importing this module never needs openpyxl."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color="FF808080")
    return {
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
        "font_head": Font(bold=True, color=_INK, size=11),
        "font_group": Font(bold=True, color=_INK, size=10),
        "font_cell": Font(color=_INK, size=10),
        "font_warn": Font(color=_INK, size=10, bold=True),
        "font_bad": Font(color=_INK_ON_BAD, size=10, bold=True),
        "fill_warn": PatternFill("solid", fgColor=_FILL_WARN),
        "fill_bad": PatternFill("solid", fgColor=_FILL_BAD),
        "fill_head": PatternFill("solid", fgColor=_FILL_HEADER),
        "fill_group": PatternFill("solid", fgColor=_FILL_GROUP),
        "fill_value": PatternFill("solid", fgColor=_FILL_VALUE),
        "fill_flag": PatternFill("solid", fgColor=_FILL_FLAGGED),
        "mid": Alignment(horizontal="center", vertical="center"),
        "left": Alignment(horizontal="left", vertical="center"),
        "wrap": Alignment(horizontal="left", vertical="top", wrap_text=True),
    }


class WorkbookLocked(Exception):
    """The workbook is open in Excel. The CSV has already been written."""


def _load_or_create(path: Path, fields: Fields):
    from openpyxl import Workbook, load_workbook

    if path.exists():
        return load_workbook(path)

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_VALUES
    ws.column_dimensions["A"].width = 18
    # The campaign shares this cell with nothing, and a campaign name runs to
    # sixty characters.
    ws.column_dimensions["B"].width = 40
    parts = parts_per_moment(fields)
    last = FIRST_VALUE_COL + (2 + MAX_EXTRA_COLS) * parts
    for i in range(FIRST_VALUE_COL, last):
        ws.column_dimensions[_letter(i)].width = 13
    ws.sheet_view.showGridLines = False
    return wb


def _letter(idx: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(idx)


def _values_sheet(wb):
    """The sheet the days are on.

    Never `wb.active`: the workbook has a Trends sheet and a Charts sheet
    beside it now, and whichever one Excel was left showing is the active one
    when the file is read back. Written blindly into that, a day's values would
    land on top of a chart's data.
    """
    if SHEET_VALUES in wb.sheetnames:
        return wb[SHEET_VALUES]
    return wb.worksheets[0]


def _find_block(ws, day: date) -> int | None:
    """Row number of a day's header, or None. Days are matched on the stored
    date value, not on the formatted text, so a locale change cannot orphan a
    block."""
    want = day.isoformat()
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if isinstance(v, datetime):
            v = v.date()
        if isinstance(v, date) and v == day:
            return r
        if isinstance(v, str) and v.strip() == want:
            return r
    return None


def _sheet_is_empty(ws) -> bool:
    """A brand-new openpyxl sheet reports max_row == 1 with nothing in it."""
    if ws.max_row > 1:
        return False
    return all(ws.cell(1, c).value in (None, "") for c in range(1, ws.max_column + 1))


# A block has TWO heading lines: the moment's name across its own columns, and
# under it the name of each number. The quantities start below both.
BLOCK_HEAD_LINES = 2


def _unmerge_run(ws, row: int, col: int, span: int) -> None:
    """Take a merge off a run of cells so it can be written into again.

    Everything but the top-left cell of a merged range is a MergedCell, whose
    value is read-only. A block is written into more than once -- a second
    moment, a correction, a repair -- so every merge this module makes has to
    be undone before it is made again.
    """
    for rng in list(ws.merged_cells.ranges):
        if (rng.min_row == row and rng.max_row == row
                and rng.min_col >= col and rng.max_col < col + span):
            ws.unmerge_cells(str(rng))


def _write_moment_head(ws, header_row: int, fields: Fields, st, col: int,
                       text: str) -> None:
    """The name of one moment, across the columns that moment owns.

    Merged, so "Morning" stands over X, Y and SUM together rather than over X
    with two empty boxes beside it. The cells under a merge still have to be
    filled and bordered by hand: openpyxl only draws the top-left one.
    """
    parts = parts_per_moment(fields)
    _unmerge_run(ws, header_row, col, parts)
    for i in range(parts):
        c = ws.cell(header_row, col + i)
        c.value = text if i == 0 else None
        c.font, c.fill, c.alignment, c.border = (
            st["font_head"], st["fill_head"], st["mid"], st["border"])
    if parts > 1:
        ws.merge_cells(start_row=header_row, start_column=col,
                       end_row=header_row, end_column=col + parts - 1)

    # The line under it: which number each column holds.
    names = part_headings(fields)
    for i in range(parts):
        c = ws.cell(header_row + 1, col + i)
        c.value = names[i] or None
        c.font, c.fill, c.alignment, c.border = (
            st["font_head"], st["fill_head"], st["mid"], st["border"])


def _append_block(ws, day: date, fields: Fields, st, campaign: str = "") -> int:
    """Write an empty block for a day at the bottom and return its header row."""
    if _sheet_is_empty(ws):
        start = 1
    else:
        start = ws.max_row + 2            # one blank line between days

    head = ws.cell(start, 1)
    head.value = day
    head.number_format = "D.M.YYYY"
    head.font, head.fill, head.alignment = st["font_head"], st["fill_head"], st["mid"]
    head.border = st["border"]

    # The campaign stands beside the date, in the one cell of the day header
    # that has nothing else to say. Both heading lines of column A and B are
    # the day's band, so the block starts with a solid bar.
    b = ws.cell(start, 2)
    b.fill, b.border = st["fill_head"], st["border"]
    b.font, b.alignment = st["font_head"], st["left"]
    if campaign:
        b.value = campaign
    for c in (1, 2):
        cell = ws.cell(start + 1, c)
        cell.fill, cell.border, cell.font = (
            st["fill_head"], st["border"], st["font_head"])

    for index, slot in enumerate((SLOT_MORNING, SLOT_EVENING)):
        _write_moment_head(ws, start, fields, st, moment_col(fields, index),
                           SLOT_HEADINGS[slot])

    _relabel_block(ws, start, fields, st)
    return start


def _relabel_block(ws, header_row: int, fields: Fields, st) -> None:
    """Write the group column and the quantity column of one day's block."""
    r = header_row + BLOCK_HEAD_LINES
    for group, rows in fields.groups:
        first_row = r
        for row_def in rows:
            a = ws.cell(r, 1)
            a.fill, a.border, a.font = st["fill_group"], st["border"], st["font_group"]
            a.alignment = st["mid"]
            a.value = group if r == first_row else None

            b = ws.cell(r, 2)
            b.value = row_def.label
            b.font, b.fill, b.border, b.alignment = (
                st["font_cell"], st["fill_value"], st["border"], st["left"])

            # Only the two scheduled moments are drawn now. An extra one is
            # given its borders when it is first used, so a day with no extra
            # moment does not leave a row of empty boxes trailing to the right.
            for index in (0, 1):
                _style_value_row(ws, r, moment_col(fields, index), row_def,
                                 fields, st)
            if row_def.is_text:
                ws.row_dimensions[r].height = 78
            r += 1

        if r - 1 > first_row:
            ws.merge_cells(start_row=first_row, start_column=1,
                           end_row=r - 1, end_column=1)


def _block_height(ws, header_row: int, fields: Fields) -> int:
    """How many quantity lines the block under `header_row` already has.

    A day block ends at the first completely empty line -- the blank separator
    written between days.
    """
    last_col = max(ws.max_column, first_extra_col(fields))
    r = header_row + BLOCK_HEAD_LINES
    n = 0
    while r <= ws.max_row:
        if all(ws.cell(r, c).value in (None, "") for c in range(1, last_col + 1)):
            break
        n += 1
        r += 1
    return n


def _ensure_block_shape(ws, header_row: int, fields: Fields, st) -> None:
    """Make an older block as tall as the quantity list is now.

    Values are written into a block by position. A block written before a
    quantity was added is one line short, so every value from that quantity down
    would land one line too low -- the last one on the blank separator, and the
    rest under the wrong labels. Lines are inserted or removed at the bottom of
    the block and the labels rewritten; anything typed into Excel by hand in the
    value columns keeps its line.
    """
    want = len(fields.rows)
    have = _block_height(ws, header_row, fields)
    first = header_row + BLOCK_HEAD_LINES
    # An empty label comes back from the file as None, not as "" -- a spreadsheet
    # does not store an empty string. Both sides are read the same way, or the
    # humidity line alone would make every block look wrong.
    labels = [str(ws.cell(first + i, 2).value or "") for i in range(have)]
    if have == want and labels == [r.label for r in fields.rows]:
        return

    # openpyxl does not move merged ranges when rows are inserted, so the group
    # column is unmerged first and merged again by _relabel_block. The heading
    # lines are merged too and must NOT be touched, so the range has to start
    # below them.
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= first and rng.max_row < first + have:
            ws.unmerge_cells(str(rng))

    if have < want:
        ws.insert_rows(first + have, want - have)
    elif have > want:
        ws.delete_rows(first + want, have - want)
    _relabel_block(ws, header_row, fields, st)


def _style_value_row(ws, r: int, col: int, row_def: Row, fields: Fields,
                     st) -> None:
    """The cells one quantity has inside one moment.

    A quantity that IS one number lies across the whole moment, merged, so
    there is no empty box beside it -- the same as on screen.
    """
    parts = parts_per_moment(fields)
    _unmerge_run(ws, r, col, parts)
    for i in range(parts):
        cell = ws.cell(r, col + i)
        cell.fill, cell.border, cell.font = (
            st["fill_value"], st["border"], st["font_cell"])
        cell.alignment = st["wrap"] if row_def.is_text else st["mid"]
    if len(row_def.columns) == 1 and parts > 1:
        ws.merge_cells(start_row=r, start_column=col,
                       end_row=r, end_column=col + parts - 1)


def _block_is_old_shape(ws, header_row: int, fields: Fields) -> bool:
    """True for a block written before the numbers were split into columns.

    Such a block has one heading line, not two, and one cell holding
    "150 ; -250 ; 2500". Values are written by position, so writing into it
    would put every number one line too high and under the wrong heading. The
    fix is testing/rebuild_workbook.py, not a guess.
    """
    if parts_per_moment(fields) <= 1:
        return False
    names = [n for n in part_headings(fields) if n]
    if not names:
        return False
    row = header_row + 1
    have = [str(ws.cell(row, moment_col(fields, 0) + i).value or "")
            for i in range(parts_per_moment(fields))]
    return have != part_headings(fields)


def _target_column(ws, header_row: int, rec: Record, fields: Fields,
                   log_fn=None) -> int:
    """Which column this record's FIRST number belongs in.

    Morning and evening always sit in the same two places, so the days line up
    when you scroll. A "record now" moment takes the room to the right; other
    days simply leave theirs empty, which is why no column ever has to be
    inserted -- inserting one would shift every other block on the sheet.

    Two things this has to get right:

    * **A moment already written keeps its own place.** It is found by its
      heading, the time it was recorded. Looking for "the first free column"
      instead sent every correction of a recorded moment into a brand-new
      column and left the stale value where it was.
    * **A new moment goes after the last one used**, not into the first gap. A
      deleted column leaves a gap, and refilling it would put 16:05 to the left
      of 14:32 inside the same day.
    """
    if rec.slot == SLOT_MORNING:
        return col_morning(fields)
    if rec.slot == SLOT_EVENING:
        return col_evening(fields)

    last_used = -1
    heading = rec.heading()
    for index in range(2, 2 + MAX_EXTRA_COLS):
        col = moment_col(fields, index)
        value = ws.cell(header_row, col).value
        if value in (None, ""):
            continue
        if str(value) == heading:
            return col                       # its own place
        last_used = index

    nxt = max(2, last_used + 1)
    if nxt < 2 + MAX_EXTRA_COLS:
        return moment_col(fields, nxt)
    if log_fn:
        log_fn(f"The block for {rec.day} already holds {MAX_EXTRA_COLS} extra "
               f"columns - {heading} overwrites the last one.")
    return moment_col(fields, 1 + MAX_EXTRA_COLS)   # overwrite rather than lose it


def reference_verdicts(row_def: Row, values: dict, refs: dict,
                       default_warn_pct: float = 5.0) -> dict:
    """The reference verdict of every number of one row.

    ``{column: LimitVerdict}``, and only for the numbers that actually have a
    reference -- a row nobody has set a range for is simply absent, which is
    what leaves it uncoloured.
    """
    import spfe_limits as limits

    out = {}
    if row_def.is_text:
        return out
    for column in row_def.columns:
        entry = refs.get(column)
        if not limits.has_range(entry):
            continue
        verdict = limits.judge(values.get(column, ""), entry,
                               default_warn_pct=default_warn_pct)
        if verdict.flagged:
            out[column] = verdict
    return out


# There used to be a `row_reference_level` here, rolling the verdicts of X, Y
# and SUM into the single worst one, because the workbook kept all three
# numbers in one cell and a cell can only have one colour. Each number has its
# own cell now, in the workbook as well as on screen, so both sides use
# `reference_verdicts` and nothing has to be rolled up.


def write_workbook_record(path: Path, fields: Fields, rec: Record,
                          flags: dict[str, str] | None = None,
                          campaign: str = "", log_fn=None,
                          refs: dict | None = None,
                          default_warn_pct: float = 5.0) -> None:
    """Put one recorded moment into the workbook.

    Only this day's block is touched, and inside it only this one moment's
    columns, so a note typed straight into Excel elsewhere in the file survives
    untouched. An existing block only has its campaign overwritten by a name
    that is really there, for the same reason: a name typed straight into Excel
    must not be blanked by a program that happens not to know it.

    `flags` is the statistical remark per NUMBER -- ``{CSV column: sentence}``,
    what `spfe_record.judge_columns` returns -- because each number has a cell
    of its own here now.

    Raises WorkbookLocked when the file is open in Excel.
    """
    from openpyxl.comments import Comment

    flags = flags or {}
    st = _styles()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        wb = _load_or_create(path, fields)
    except PermissionError as exc:
        raise WorkbookLocked(str(exc)) from exc
    ws = _values_sheet(wb)

    header_row = _find_block(ws, rec.day)
    if header_row is None:
        header_row = _append_block(ws, rec.day, fields, st, campaign)
    else:
        if _block_is_old_shape(ws, header_row, fields):
            if log_fn:
                log_fn(f"The block for {rec.day} is in the old layout, with "
                       "X, Y and SUM in one cell. Nothing was written into the "
                       "workbook - run testing/rebuild_workbook.py. The log "
                       "files have the values.")
            return
        _ensure_block_shape(ws, header_row, fields, st)
        if campaign:
            ws.cell(header_row, 2).value = campaign

    col = _target_column(ws, header_row, rec, fields, log_fn)
    _write_moment_head(ws, header_row, fields, st, col, rec.heading())

    refs = refs or {}
    r = header_row + BLOCK_HEAD_LINES
    for row_def in fields.rows:
        _style_value_row(ws, r, col, row_def, fields, st)
        verdicts = reference_verdicts(row_def, rec.values, refs,
                                      default_warn_pct)

        for i, column in enumerate(row_def.columns):
            reason = flags.get(column, "")
            cell = ws.cell(r, col + i)
            cell.value = (format_cell(row_def, rec.values)
                          if row_def.is_text or len(row_def.columns) == 1
                          else format_value(row_def, column, rec.values))

            verdict = verdicts.get(column)
            level = verdict.level if verdict else ""
            # Never a background without deciding the foreground. Three fills
            # and three inks: the red one is the only one black text cannot be
            # read on, so it is the only one that turns the text white.
            #
            # Per NUMBER now, exactly as on screen: X, Y and SUM have a cell
            # each, so marking all three because one of them is out of range is
            # over.
            #
            # A reference is somebody's decision about the machine and the
            # statistical remark is only "this differs from the other days", so
            # the reference is what colours the cell and the remark joins its
            # note.
            if level == "bad":
                cell.fill, cell.font = st["fill_bad"], st["font_bad"]
            elif level == "warn":
                cell.fill, cell.font = st["fill_warn"], st["font_warn"]
            elif reason:
                cell.fill = st["fill_flag"]
            else:
                cell.fill = st["fill_value"]
            note = "\n".join(t for t in (verdict.reason if verdict else "",
                                         reason) if t)
            cell.comment = Comment(note, "SPFE Values") if note else None
        r += 1

    _save_atomic(wb, path)


def clear_workbook_column(path: Path, fields: Fields, rec: Record) -> bool:
    """Wipe the columns one deleted moment had in the workbook.

    The moment is found by its heading -- the time it was recorded -- and then
    everything about it goes: the values, the out-of-range comments (clearing a
    value does NOT remove the comment, and a red marker explaining a number
    that is no longer there is worse than nothing), and the styling. An extra
    moment is only given borders when it is first used, exactly so that a day
    with no extra moment does not trail empty boxes to the right; a cleared one
    has to look like a moment that was never used.

    Never `delete_cols`: that would shift every other day's block on the sheet.
    Returns False when the day or the moment is not in the workbook at all.
    """
    from openpyxl.styles import Alignment, Border, Font, PatternFill

    if rec.slot in (SLOT_MORNING, SLOT_EVENING):
        return False                          # the two fixed moments stay

    if not path.exists():
        return False
    try:
        wb = _load_or_create(path, fields)
    except PermissionError as exc:
        raise WorkbookLocked(str(exc)) from exc
    ws = _values_sheet(wb)

    header_row = _find_block(ws, rec.day)
    if header_row is None:
        return False

    heading = rec.heading()
    parts = parts_per_moment(fields)
    col = 0
    for index in range(2, 2 + MAX_EXTRA_COLS):
        c = moment_col(fields, index)
        if str(ws.cell(header_row, c).value or "") == heading:
            col = c
            break
    if not col:
        return False

    # The merges go first: a merged range left behind would keep drawing a box
    # round columns that are now supposed to look untouched.
    last_row = header_row + BLOCK_HEAD_LINES + len(fields.rows) - 1
    for rng in list(ws.merged_cells.ranges):
        if (rng.min_col >= col and rng.max_col < col + parts
                and header_row <= rng.min_row <= last_row):
            ws.unmerge_cells(str(rng))

    blank_fill = PatternFill(fill_type=None)
    for r in range(header_row, last_row + 1):
        for i in range(parts):
            cell = ws.cell(r, col + i)
            cell.value = None
            cell.comment = None
            cell.fill = blank_fill
            cell.border = Border()
            cell.font = Font()
            cell.alignment = Alignment()

    _save_atomic(wb, path)
    return True


def write_workbook_campaigns(path: Path, fields: Fields, entries: dict) -> int:
    """Refresh the campaign beside the date of every day that has a block.

    A day with no record has no block and must not be given one: blocks are
    appended at the bottom, so a block created out of turn would put a later
    day above an earlier one and destroy the one thing the workbook is for --
    scrolling down through the days in order. Such a day keeps its campaign in
    the side file and gets it into the workbook the moment its first value is
    recorded.

    Idempotent, so it can run on every sync, and it also repairs a name typed
    on another PC.
    """
    if not entries or not path.exists():
        return 0
    st = _styles()
    try:
        wb = _load_or_create(path, fields)
    except PermissionError as exc:
        raise WorkbookLocked(str(exc)) from exc
    ws = _values_sheet(wb)

    # Every block on the sheet, not only the days somebody typed on: a carried
    # campaign belongs on each of its days, because the workbook is read one
    # day at a time.
    written = 0
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if isinstance(v, datetime):
            v = v.date()
        elif isinstance(v, str):
            try:
                v = datetime.strptime(v.strip(), "%Y-%m-%d").date()
            except ValueError:
                continue
        if not isinstance(v, date):
            continue
        name, own = campaign_for(entries, v)
        if not name and not own:
            continue          # nothing is known: leave whatever Excel holds
        cell = ws.cell(r, 2)
        if str(cell.value or "") == name:
            continue
        cell.value = name or None
        cell.font, cell.fill, cell.alignment, cell.border = (
            st["font_head"], st["fill_head"], st["left"], st["border"])
        written += 1

    if written:
        _save_atomic(wb, path)
    return written


# ---------------------------------------------------------------------------
# The picture of it: a Trends sheet the charts point at, and the charts
# ---------------------------------------------------------------------------
#
# The day blocks cannot be charted. Each day is its own block with a blank line
# between, so one quantity across thirty days is thirty separate cells, not a
# range -- and openpyxl can only point a chart at a range. So the same numbers
# are laid out a second time, one line per DAY, and the charts point at that.
#
# One column per number PER SLOT, so the morning and the end of the day are two
# curves of the same quantity and the drift through a day is visible.

def trend_columns(fields: Fields) -> list[tuple[str, str, str, str]]:
    """Every curve the workbook draws: (group, heading, CSV column, slot).

    Text rows are left out -- a note is not a curve. Everything numeric is in,
    including the settings somebody types by hand: "when did the GDD change?"
    is exactly the question this sheet is here to answer.
    """
    out = []
    for group, rows in fields.groups:
        for row_def in rows:
            if row_def.is_text:
                continue
            for column in row_def.columns:
                name = row_def.part_label(column)
                # A quantity that IS its group has no label of its own --
                # Humidity is the whole of the Humidity group -- so the group's
                # name is what a legend has to say. Falling through to the key
                # put a lower-case "humidity" in the chart.
                base = row_def.base_label or row_def.label or group
                what = f"{base} {name}".strip() if name else base
                for slot, label in ((SLOT_MORNING, "morning"),
                                    (SLOT_EVENING, "at the end")):
                    out.append((group, f"{what} - {label}", column, slot))
    return out


def write_workbook_trends(path: Path, fields: Fields, records: list[Record],
                          log_fn=None) -> None:
    """Rewrite the Trends sheet and the charts on it from every record.

    Both sheets are the program's, not the operator's: they are wiped and
    written again on every save, so they can never show yesterday's picture.
    Anything typed on them by hand is lost, which is why the day blocks are on
    a sheet of their own and are only ever touched one moment at a time.

    Raises WorkbookLocked when the file is open in Excel.
    """
    from openpyxl.chart import LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if not path.exists():
        return
    try:
        wb = _load_or_create(path, fields)
    except PermissionError as exc:
        raise WorkbookLocked(str(exc)) from exc

    for name in (SHEET_TRENDS, SHEET_CHARTS):
        if name in wb.sheetnames:
            del wb[name]
    ws = wb.create_sheet(SHEET_TRENDS)
    wc = wb.create_sheet(SHEET_CHARTS)
    ws.sheet_view.showGridLines = False

    curves = trend_columns(fields)
    head_font = Font(bold=True, color=_INK, size=10)
    head_fill = PatternFill("solid", fgColor=_FILL_HEADER)
    upright = Alignment(horizontal="center", vertical="bottom", wrap_text=True,
                        textRotation=60)

    a = ws.cell(1, 1)
    a.value = "Date"
    a.font, a.fill = head_font, head_fill
    ws.column_dimensions["A"].width = 12
    for i, (_group, heading, _column, _slot) in enumerate(curves):
        c = ws.cell(1, 2 + i)
        c.value = heading
        c.font, c.fill, c.alignment = head_font, head_fill, upright
        ws.column_dimensions[get_column_letter(2 + i)].width = 11
    ws.row_dimensions[1].height = 120

    # One line per day, oldest first, so the chart's x axis runs forwards.
    by_day: dict[date, dict[str, Record]] = {}
    for rec in records:
        by_day.setdefault(rec.day, {})[rec.slot] = rec
    days = sorted(by_day)

    for r, day in enumerate(days, start=2):
        cell = ws.cell(r, 1)
        cell.value = day
        cell.number_format = "D.M.YYYY"
        for i, (_group, _heading, column, slot) in enumerate(curves):
            rec = by_day[day].get(slot)
            value = _as_number(rec.values.get(column)) if rec else None
            if value is not None:
                ws.cell(r, 2 + i).value = value

    if len(days) < 2:
        # One point is not a trend, and a chart of it is a line with nowhere to
        # go. The sheet is still written, so the numbers are there to look at.
        _save_atomic(wb, path)
        if log_fn:
            log_fn("Trends: one day only - the charts start at the second day.")
        return

    last = 1 + len(days)
    at = 1
    for group, _rows in fields.groups:
        wanted = [i for i, (g, _h, _c, _s) in enumerate(curves) if g == group]
        if not wanted:
            continue
        chart = LineChart()
        chart.title = group
        chart.style = 2
        chart.height, chart.width = 7.5, 16
        chart.y_axis.title = None
        chart.x_axis.title = "Day"
        # Without this openpyxl writes the axes but Excel draws neither: the
        # delete flag defaults to True on a chart built from scratch.
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        days_ref = Reference(ws, min_col=1, min_row=2, max_row=last)
        for i in wanted:
            col = 2 + i
            chart.add_data(Reference(ws, min_col=col, min_row=1, max_row=last),
                           titles_from_data=True)
        chart.set_categories(days_ref)
        for series in chart.series:
            series.smooth = False
        wc.add_chart(chart, f"A{at}")
        at += 16

    _save_atomic(wb, path)


def _as_number(raw):
    """A stored value as a number, or None. Everything on the Trends sheet has
    to be a real number or Excel charts a row of zeros."""
    if raw in ("", None):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return int(value) if value == int(value) else value


def _save_atomic(wb, path: Path) -> None:
    """Write beside the target, then move into place.

    A half-written workbook on a share is worse than no workbook, and a save
    straight onto a file somebody has open in Excel fails part-way through.
    """
    tmp = path.with_name(path.stem + ".saving.xlsx")
    try:
        wb.save(tmp)
    except PermissionError as exc:
        raise WorkbookLocked(str(exc)) from exc
    try:
        os.replace(tmp, path)
    except PermissionError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise WorkbookLocked(str(exc)) from exc


def format_cell(row_def: Row, values: dict, *, with_unit: bool = True) -> str:
    """The text of one cell: every number of the row, joined.

    On screen the unit stands in the Quantity column instead, so the table is
    called with ``with_unit=False`` and a value cell holds nothing but the
    number -- which is all the operator ever has to type. The workbook keeps its
    unit in the cell, because a spreadsheet column has no label beside it.
    """
    if row_def.is_text:
        # A dash in a free-text row reads as a value; an empty box reads as a
        # box waiting to be filled in, which is what it is.
        v = values.get(row_def.columns[0], "")
        return "" if v in ("", None) else str(v)

    parts = [format_value(row_def, column, values) for column in row_def.columns]

    if all(p == "-" for p in parts):
        return "-"
    text = join_text(row_def).join(parts)
    if with_unit and row_def.unit:
        text = f"{text} {row_def.unit}"
    return text


def join_text(row_def: Row) -> str:
    """The separator as it is SHOWN: a space on each side of it.

    "150;-250;2500" is three numbers nobody can read at a glance, and the minus
    sign of the second one hides against the semicolon. The stored separator
    (`row_def.join`) is unchanged, and `parse_cell` strips the spaces again, so
    this is a matter of the eye only -- nothing in the files moves.
    """
    return f" {row_def.join.strip()} " if row_def.join.strip() else row_def.join


def format_value(row_def: Row, column: str, values: dict) -> str:
    """ONE number of a row, as it is shown: rounded, to its decimals.

    Its own function because the table now gives every number a line of its
    own -- X, Y and SUM are three cells that are typed into separately -- while
    the workbook still keeps the three of them in one cell. Both have to print
    a number the same way, and they did not while each had its own loop.
    """
    v = values.get(column, "")
    if v in ("", None):
        return "-"
    try:
        return f"{_rounded(float(v), row_def.step_for(column)):.{row_def.decimals}f}"
    except (TypeError, ValueError):
        # Not a number at all: shown exactly as it stands in the file, so a
        # value somebody typed by hand is never quietly reinterpreted.
        return str(v)


def _rounded(value: float, step: float) -> float:
    """`value` to the nearest multiple of `step`. 0 leaves it alone.

    An exact half goes away from zero, so 365 reads 370 and -365 reads -370.
    Neither of the obvious one-liners does that: round() sends a half to the
    even side (365 -> 360) and floor(v/step + 0.5) sends every half upwards, so
    the negative values -- half of these quantities -- would round the opposite
    way from the positive ones.
    """
    if step <= 0:
        return value
    sign = -1.0 if value < 0 else 1.0
    return sign * math.floor(abs(value) / step + 0.5) * step


def parse_cell(row_def: Row, text: str) -> dict:
    """The inverse of `format_cell`: one cell of text back into its columns.

    Written beside the formatter on purpose -- the two have to agree, and a
    round trip through them is what happens every time somebody corrects a
    value on screen.

    Three rules, each one there to stop a loss:

    * A dash is what the formatter shows for a number that is not there, so a
      dash read back is empty, never the literal text "-". Five quantities have
      no PV at all and every one of their cells shows a dash on every day.
    * Only the numbers actually typed are returned. Typing "136" into an
      X;Y;SUM cell means "correct X", not "delete Y and SUM" -- and there is no
      undo anywhere in this program.
    * Typing the separators is how the rest is cleared on purpose: "136;;"
      returns three keys, "136" returns one.
    """
    if row_def.is_text:
        return {row_def.columns[0]: str(text)}

    cols = row_def.columns
    parts = [p.strip() for p in str(text).split(row_def.join)][:len(cols)]
    if len(parts) == 1 and parts[0] in ("", "-"):
        return {c: "" for c in cols}           # the whole cell was cleared
    return {col: ("" if p in ("", "-") else p) for col, p in zip(cols, parts)}


def apply_defaults(fields: Fields, values: dict, day: date | None = None) -> dict:
    """Put every manual row's default into `values` where nothing was typed.

    Done when a moment is recorded, so the CSV and the workbook say what the
    table says. Without it the screen would show numbers the files do not have.

    `day` is the day being recorded: a default that only starts on a later day
    (`default_from`) is left out, so filling in the past does not invent
    settings for days that predate them.
    """
    for row in fields.rows:
        if not row.default_applies(day):
            continue
        column = row.columns[0]
        if values.get(column, "") in ("", None):
            values[column] = row.default
    return values


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

@dataclass
class SaveResult:
    local_rows: int = 0
    share_rows: int = 0
    workbook_written: bool = False
    share_root: str = ""
    problem: str = ""          # a plain sentence for the status line, or ""

    @property
    def pending(self) -> bool:
        return bool(self.problem)


class Store:
    """Local mirror first, share second. Nothing here ever raises at the caller."""

    def __init__(self, cfg: dict, fields: Fields):
        self.cfg = cfg
        self.fields = fields
        self.local_csv = local_dir() / CSV_NAME
        self.share_root = ""
        self.share_source = "none"
        self._log_fn = None
        # Every write goes through this. A rewrite is read-modify-replace, so
        # an edit being saved while a catch-up appends would drop the appended
        # record; the two must not overlap.
        self._io_lock = threading.RLock()
        self._deleted: set[str] | None = None       # tombstones, read once

    def set_log(self, fn) -> None:
        self._log_fn = fn

    def _log(self, msg: str) -> None:
        if self._log_fn:
            try:
                self._log_fn(msg)
            except Exception:
                pass

    # -- share ------------------------------------------------------------
    def connect(self) -> str:
        """Probe for the share. Call this off the UI thread."""
        cached = str(self.cfg.get("_last_share_root", "") or "")
        self.share_root, self.share_source = resolve_share(self.cfg, cached)
        if self.share_root:
            self.cfg["_last_share_root"] = self.share_root
            self._log(f"Share: {self.share_root} ({self.share_source})")
        else:
            self._log("Share: neither name answered - working locally.")
        return self.share_root

    @property
    def share_folder(self) -> Path | None:
        if not self.share_root:
            return None
        return share_dir(self.share_root, self.cfg)

    # -- the side files ---------------------------------------------------
    @property
    def local_campaigns(self) -> Path:
        return local_dir() / CAMPAIGNS_NAME

    @property
    def local_tombstones(self) -> Path:
        return local_dir() / TOMBSTONES_NAME

    @property
    def local_pending(self) -> Path:
        return local_dir() / PENDING_EDITS_NAME

    @property
    def local_references(self) -> Path:
        return local_dir() / REFERENCES_NAME

    def reference_args(self) -> dict:
        """The reference table and the warning width, as the workbook writer
        wants them. Read ONCE before a loop over records: `references()` opens
        two files, and a day being filled in writes sixteen records."""
        try:
            return {"refs": self.references(),
                    "default_warn_pct": float(self.cfg.get("warn_pct", 5.0))}
        except Exception:
            return {"refs": {}, "default_warn_pct": 5.0}

    def references(self) -> dict:
        """The ranges somebody set, both copies merged.

        Keyed by the CSV column of one number -- `ba2loop2_3`, not `ba2loop2` --
        so X, Y and SUM each get their own range, which is the whole point:
        two of them are a position in micrometres and the third is a sum of
        counts.
        """
        entries = read_json_map(self.local_references, "limits")
        folder = self.share_folder
        if folder:
            entries = merge_day_map(
                entries, read_json_map(folder / REFERENCES_NAME, "limits"))
        return entries

    def set_references(self, entries: dict) -> SaveResult:
        """Write the whole reference table, local first and then the share.

        The whole table, not the one line that changed: it comes straight out
        of the References window, where every line is on screen at once, and a
        merge of a half table against the share is how a range somebody cleared
        on purpose comes back to life.
        """
        res = SaveResult(share_root=self.share_root)
        stamped = {}
        for key, entry in entries.items():
            e = dict(entry)
            e.setdefault("set_at", now_stamp())
            e.setdefault("by", who())
            stamped[key] = e
        with self._io_lock:
            try:
                write_json_map(self.local_references, "limits", stamped)
            except Exception as exc:
                res.problem = f"Could not save the references: {exc}"
                self._log(res.problem)
                return res
            folder = self.share_folder
            if folder is None:
                res.problem = ("The shared folder is not reachable - the "
                               "references are saved on this PC and will be "
                               "sent with Sync now.")
                return res
            try:
                write_json_map(folder / REFERENCES_NAME, "limits", stamped)
            except Exception as exc:
                res.problem = f"The shared references could not be written: {exc}"
                self._log(res.problem)
        return res

    def campaigns(self) -> dict:
        """Every day somebody named, both copies merged."""
        entries = read_json_map(self.local_campaigns, "days")
        folder = self.share_folder
        if folder:
            entries = merge_day_map(
                entries, read_json_map(folder / CAMPAIGNS_NAME, "days"))
        return entries

    def campaign_for_day(self, day: date) -> tuple[str, bool]:
        return campaign_for(self.campaigns(), day)

    def set_campaign(self, day: date, name: str) -> SaveResult:
        """Name a day. An empty name is stored, not deleted -- that is how a
        campaign is ended, and a deleted entry would be filled in again by the
        other copy on the next merge."""
        res = SaveResult(share_root=self.share_root)
        with self._io_lock:
            entries = self.campaigns()
            entries[day.isoformat()] = {
                "name": str(name), "set_at": now_stamp(), "by": who()}
            try:
                write_json_map(self.local_campaigns, "days", entries)
            except Exception as exc:
                res.problem = f"Could not save the campaign name: {exc}"
                self._log(res.problem)
                return res

            folder = self.share_folder
            if folder is None:
                res.problem = ("The shared folder is not reachable - the "
                               "campaign name is saved on this PC and will be "
                               "sent with Sync now.")
                return res
            try:
                write_json_map(folder / CAMPAIGNS_NAME, "days", entries)
            except Exception as exc:
                res.problem = f"The shared campaign list could not be written: {exc}"
                self._log(res.problem)
                return res

            try:
                write_workbook_campaigns(
                    folder / WORKBOOK_NAME, self.fields, entries)
                res.workbook_written = True
            except WorkbookLocked:
                res.problem = ("'" + WORKBOOK_NAME + "' is open in Excel - the "
                               "campaign name is saved, it reaches the workbook "
                               "when Excel is closed.")
            except ImportError:
                pass
            except Exception as exc:
                res.problem = f"The workbook could not be updated: {exc}"
                self._log(res.problem)
        return res

    def deleted_stamps(self, refresh: bool = False) -> set[str]:
        """The moments somebody deleted, both copies merged.

        Read once and kept: it is consulted on every read of the log.
        """
        if self._deleted is not None and not refresh:
            return self._deleted
        entries = read_json_map(self.local_tombstones, "deleted")
        folder = self.share_folder
        if folder:
            entries = merge_day_map(
                entries, read_json_map(folder / TOMBSTONES_NAME, "deleted"),
                stamp_field="at")
        self._deleted = set(entries)
        return self._deleted

    # -- reading ----------------------------------------------------------
    def all_rows(self) -> list[dict]:
        """Every recorded moment, oldest first. The share wins when it holds
        more, because somebody else's records live only there.

        Deleted moments are dropped here, in the one place every reader goes
        through. Filtering anywhere else would let a half-finished delete come
        back: the two CSVs are compared by length, so the copy that still has
        the row would simply win.
        """
        local = read_csv(self.local_csv)
        folder = self.share_folder
        rows = local
        if folder:
            shared = read_csv(folder / CSV_NAME)
            if len(shared) > len(local):
                rows = shared
        gone = self.deleted_stamps()
        if gone:
            rows = [r for r in rows if r.get("datetime", "") not in gone]
        return rows

    def recorded_stamps(self) -> set[str]:
        return {r.get("datetime", "") for r in self.all_rows()}

    # -- writing ----------------------------------------------------------
    def save(self, records: list[Record],
             flags_by_stamp: dict[str, dict[str, str]] | None = None) -> SaveResult:
        """Write records everywhere they belong. Never raises."""
        flags_by_stamp = flags_by_stamp or {}
        cols = self.fields.columns
        res = SaveResult(share_root=self.share_root)
        gone = self.deleted_stamps()

        # A moment somebody deleted is not written again. Recording the same
        # minute twice is the only way to hit this, and saying so is better
        # than reporting values that were quietly dropped.
        refused = [r for r in records if r.stamp in gone]
        records = [r for r in records if r.stamp not in gone]
        if refused and not records:
            res.problem = (f"{refused[0].heading()} on "
                           f"{refused[0].day:%d.%m.%Y} was deleted earlier - "
                           "it is not recorded again. Try again in a minute.")
            self._log(res.problem)
            return res

        with self._io_lock:
            # 1. the mirror that cannot fail
            try:
                res.local_rows = append_csv(self.local_csv, records, cols, gone)
            except Exception as exc:
                res.problem = f"Could not write the local copy: {exc}"
                self._log(res.problem)
                return res

            folder = self.share_folder
            if folder is None:
                res.problem = ("The shared folder is not reachable - the values are "
                               "saved on this PC and will be sent with Sync now.")
                return res

            # 2. the shared CSV
            try:
                res.share_rows = append_csv(folder / CSV_NAME, records, cols, gone)
            except PermissionError:
                res.problem = ("The shared log is in use - the values are saved on "
                               "this PC, press Sync now later.")
                self._log(res.problem)
                return res
            except Exception as exc:
                res.problem = f"The shared log could not be written: {exc}"
                self._log(res.problem)
                return res

            # 3. the workbook people look at
            #
            # One try PER RECORD. Sharing one try meant the first locked or
            # unwritable moment abandoned every day after it -- which is how a
            # log holding three weeks ended up as three days in the workbook.
            wb_path = folder / WORKBOOK_NAME
            entries = self.campaigns()
            missed: list[str] = []
            locked = False
            ref_args = self.reference_args()
            for rec in records:
                if rec.stamp in gone:
                    continue
                try:
                    write_workbook_record(
                        wb_path, self.fields, rec, flags_by_stamp.get(rec.stamp),
                        campaign=campaign_for(entries, rec.day)[0],
                        log_fn=self._log, **ref_args)
                    res.workbook_written = True
                except WorkbookLocked:
                    locked = True
                    missed.append(rec.stamp)
                except ImportError:
                    res.problem = ("openpyxl is not installed - only the log was "
                                   "written.")
                    self._log(res.problem)
                    return res
                except Exception as exc:
                    missed.append(rec.stamp)
                    self._log(f"{rec.stamp} could not be written into the "
                              f"workbook: {type(exc).__name__}: {exc}")

            if res.workbook_written:
                self._refresh_trends(wb_path)

            if missed:
                if locked:
                    res.problem = (
                        "'" + WORKBOOK_NAME + "' is open in Excel - the values "
                        f"are saved, {len(missed)} moment(s) reach the workbook "
                        "with Sync now once it is closed.")
                else:
                    res.problem = (f"{len(missed)} moment(s) could not be written "
                                   "into the workbook - see the Log window. The "
                                   "values are saved.")
                self._log(res.problem)
        return res

    def _refresh_trends(self, wb_path: Path) -> None:
        """Draw the Trends sheet and its charts again, from the whole log.

        Never allowed to spoil a save: the values are already in the CSV and in
        the day blocks by the time this runs, and a picture that could not be
        redrawn is not worth losing them over.
        """
        try:
            # all_rows already drops the deleted moments -- it is the one place
            # every reader goes through.
            records = []
            for row in self.all_rows():
                rec = record_from_csv(row, self.fields)
                if rec is not None:
                    records.append(rec)
            write_workbook_trends(wb_path, self.fields, records,
                                  log_fn=self._log)
        except WorkbookLocked:
            pass                      # the next Sync now redraws them
        except ImportError:
            pass
        except Exception as exc:
            self._log(f"The charts could not be redrawn: "
                      f"{type(exc).__name__}: {exc}")

    def update_values(self, edits: dict[str, dict[str, str]]) -> SaveResult:
        """Change the typed values of moments that are already recorded.

        This is the one place that rewrites instead of appending, because it is
        the operator correcting their own note. It touches only the columns
        named in `edits`, so a note somebody typed elsewhere in the workbook is
        still safe.
        """
        res = SaveResult(share_root=self.share_root)
        if not edits:
            return res

        with self._io_lock:
            # The local mirror can be missing a moment that only the share
            # holds -- all_rows prefers the longer file, so the screen may well
            # be showing somebody else's records. Pull those rows in first, or
            # the patch would find nothing to patch and still report success.
            self._seed_local_rows(list(edits))

            try:
                hit = rewrite_csv(self.local_csv, edits, self.fields.columns)
            except Exception as exc:
                res.problem = f"Could not update the local copy: {exc}"
                self._log(res.problem)
                return res
            res.local_rows = hit
            if not hit:
                res.problem = ("The moment being edited is not in the log any "
                               "more - nothing was changed.")
                self._log(res.problem)
                return res
            self._remember_pending(edits)

            folder = self.share_folder
            if folder is None:
                res.problem = ("The shared folder is not reachable - the change is "
                               "saved on this PC and will be sent with Sync now.")
                return res

            try:
                rewrite_csv(folder / CSV_NAME, edits, self.fields.columns)
            except PermissionError:
                res.problem = ("The shared log is in use - the change is saved on "
                               "this PC, press Sync now later.")
                self._log(res.problem)
                return res
            except Exception as exc:
                res.problem = f"The shared log could not be updated: {exc}"
                self._log(res.problem)
                return res

            # Read back from the LOCAL log, which has just been patched and
            # cannot fail. Reading the share instead silently skipped any
            # moment the share did not have.
            rows = {r.get("datetime", ""): r for r in read_csv(self.local_csv)}
            wb_path = folder / WORKBOOK_NAME
            entries = self.campaigns()
            ref_args = self.reference_args()
            try:
                for stamp in edits:
                    row = rows.get(stamp)
                    if row is None:
                        continue
                    rec = record_from_csv(row, self.fields)
                    if rec is not None:
                        write_workbook_record(
                            wb_path, self.fields, rec,
                            campaign=campaign_for(entries, rec.day)[0],
                            log_fn=self._log, **ref_args)
                res.workbook_written = True
            except WorkbookLocked:
                res.problem = ("'" + WORKBOOK_NAME + "' is open in Excel - the change "
                               "is saved, it will reach the workbook when it is closed.")
                self._log(res.problem)
                return res
            except ImportError:
                res.problem = "openpyxl is not installed - only the log was updated."
                return res
            except Exception as exc:
                res.problem = f"The workbook could not be updated: {exc}"
                self._log(res.problem)
                return res

            self._forget_pending(edits)
        return res

    def _seed_local_rows(self, stamps: list[str]) -> None:
        """Copy rows the share has and the local mirror does not."""
        folder = self.share_folder
        if folder is None:
            return
        have = {r.get("datetime", "") for r in read_csv(self.local_csv)}
        want = [s for s in stamps if s not in have]
        if not want:
            return
        shared = {r.get("datetime", ""): r for r in read_csv(folder / CSV_NAME)}
        recs = [record_from_csv(shared[s], self.fields) for s in want if s in shared]
        recs = [r for r in recs if r is not None]
        if recs:
            try:
                append_csv(self.local_csv, recs, self.fields.columns)
            except Exception:
                pass

    # -- edits that have not reached the share yet ------------------------
    #
    # sync() only ever pushes rows the share is MISSING, so a value corrected
    # while the share was down would have stayed on this PC for ever. These
    # three keep it queued until it lands, and are what makes closing the
    # program mid-save safe.
    def _remember_pending(self, edits: dict[str, dict[str, str]]) -> None:
        try:
            queued = read_json_map(self.local_pending, "edits")
            for stamp, patch in edits.items():
                queued.setdefault(stamp, {}).update(patch)
            write_json_map(self.local_pending, "edits", queued)
        except Exception:
            pass

    def _forget_pending(self, edits: dict[str, dict[str, str]]) -> None:
        try:
            queued = read_json_map(self.local_pending, "edits")
            if not queued:
                return
            for stamp, patch in edits.items():
                left = queued.get(stamp)
                if left is None:
                    continue
                for column in patch:
                    left.pop(column, None)
                if not left:
                    queued.pop(stamp, None)
            write_json_map(self.local_pending, "edits", queued)
        except Exception:
            pass

    def pending_edits(self) -> dict:
        return read_json_map(self.local_pending, "edits")

    def queue_edits(self, edits: dict[str, dict[str, str]]) -> None:
        """Put edits on the queue before they are written anywhere.

        Called by the window the moment a cell is committed, so an edit
        survives even a program that is closed a heartbeat later.
        """
        with self._io_lock:
            self._remember_pending(edits)

    def sync(self) -> SaveResult:
        """Bring the share level with this PC. Used by Sync now and on start.

        Five things, in this order, because each one can fail on its own and
        none of them may stop the next:

        1. the campaign names, merged both ways -- they have to run even when
           there is not a single record to push, because a day nobody recorded
           can still have been named;
        2. the deleted moments, merged and then actually swept out of both
           logs and the workbook;
        3. the edits this PC has made and not yet got onto the share;
        4. the rows the share is missing;
        5. the day blocks the workbook is missing, which is the repair for a
           run that was interrupted half-way.
        """
        res = SaveResult(share_root=self.share_root)
        folder = self.share_folder
        if folder is None:
            res.problem = "The shared folder is still not reachable."
            return res

        with self._io_lock:
            self._sync_campaigns(folder)
            self._sync_references(folder)
            self._sync_deletions(folder)
            self._replay_pending(folder)

            local = read_csv(self.local_csv)
            shared_path = folder / CSV_NAME
            have = {r.get("datetime", "") for r in read_csv(shared_path)}
            gone = self.deleted_stamps()
            missing = [r for r in local
                       if r.get("datetime", "") not in have
                       and r.get("datetime", "") not in gone]

            if missing:
                records = [record_from_csv(r, self.fields) for r in missing]
                records = [r for r in records if r is not None]
                res = self.save(records)
            self._repair_workbook_blocks(folder, res)
            # Last, and always: a day recorded on another PC is now in the
            # workbook, and the charts have to know about it even when this PC
            # had nothing of its own to push.
            self._refresh_trends(folder / WORKBOOK_NAME)
        return res

    def _sync_references(self, folder: Path) -> None:
        """Merge the reference ranges both ways.

        Number by number and by `set_at`, exactly like the campaigns: a
        reference set on the lab PC and one set here are two different lines of
        the same table, and the newer wins only where they collide.
        """
        shared = folder / REFERENCES_NAME
        entries = merge_day_map(read_json_map(self.local_references, "limits"),
                                read_json_map(shared, "limits"))
        if not entries:
            return
        for path in (self.local_references, shared):
            try:
                if read_json_map(path, "limits") != entries:
                    write_json_map(path, "limits", entries)
            except Exception as exc:
                self._log(f"The references could not be synced: {exc}")

    def _sync_campaigns(self, folder: Path) -> None:
        shared = folder / CAMPAIGNS_NAME
        entries = merge_day_map(read_json_map(self.local_campaigns, "days"),
                                read_json_map(shared, "days"))
        if not entries:
            return
        for path in (self.local_campaigns, shared):
            try:
                if read_json_map(path, "days") != entries:
                    write_json_map(path, "days", entries)
            except Exception as exc:
                self._log(f"The campaign list could not be synced: {exc}")
        try:
            write_workbook_campaigns(folder / WORKBOOK_NAME, self.fields, entries)
        except (WorkbookLocked, ImportError):
            pass
        except Exception as exc:
            self._log(f"The campaign names could not reach the workbook: {exc}")

    def _sync_deletions(self, folder: Path) -> None:
        shared = folder / TOMBSTONES_NAME
        entries = merge_day_map(read_json_map(self.local_tombstones, "deleted"),
                                read_json_map(shared, "deleted"),
                                stamp_field="at")
        if not entries:
            return
        for path in (self.local_tombstones, shared):
            try:
                if read_json_map(path, "deleted") != entries:
                    write_json_map(path, "deleted", entries)
            except Exception as exc:
                self._log(f"The list of deleted moments could not be synced: {exc}")
        self._deleted = set(entries)

        stamps = set(entries)
        for path in (self.local_csv, folder / CSV_NAME):
            try:
                gone = remove_from_csv(path, stamps, self.fields.columns)
                if gone:
                    self._log(f"Swept {gone} deleted moment(s) out of {path.name}.")
            except Exception as exc:
                self._log(f"{path.name}: the deleted moments could not be "
                          f"removed: {exc}")

    def _replay_pending(self, folder: Path) -> None:
        queued = self.pending_edits()
        if not queued:
            return
        try:
            rewrite_csv(folder / CSV_NAME, queued, self.fields.columns)
        except Exception as exc:
            self._log(f"The queued changes could not be sent: {exc}")
            return

        rows = {r.get("datetime", ""): r for r in read_csv(self.local_csv)}
        entries = self.campaigns()
        ref_args = self.reference_args()
        try:
            for stamp in queued:
                row = rows.get(stamp)
                if row is None:
                    continue
                rec = record_from_csv(row, self.fields)
                if rec is not None:
                    write_workbook_record(
                        folder / WORKBOOK_NAME, self.fields, rec,
                        campaign=campaign_for(entries, rec.day)[0],
                        log_fn=self._log, **ref_args)
        except (WorkbookLocked, ImportError):
            return                       # keep them queued, try again next time
        except Exception as exc:
            self._log(f"The queued changes could not reach the workbook: {exc}")
            return
        self._forget_pending(queued)
        self._log(f"Sent {len(queued)} queued change(s) to the shared folder.")

    def _repair_workbook_blocks(self, folder: Path, res: SaveResult) -> None:
        """Write the block of every recorded day the workbook does not have.

        The workbook is the half people read, and it used to lose whole weeks:
        the writes ran day after day inside one try, so the first locked file
        abandoned the rest of the run and nothing ever went back for them.
        """
        wb_path = folder / WORKBOOK_NAME
        rows = self.all_rows()
        if not rows or not wb_path.exists():
            return
        try:
            wb = _load_or_create(wb_path, self.fields)
        except (PermissionError, ImportError):
            return
        except Exception:
            return
        ws = _values_sheet(wb)
        entries = self.campaigns()

        days: list[date] = []
        for r in rows:
            try:
                day = datetime.strptime(r.get("date", ""), "%Y-%m-%d").date()
            except ValueError:
                continue
            if day not in days:
                days.append(day)
        wanted = [d for d in sorted(days) if _find_block(ws, d) is None]
        if not wanted:
            return

        by_day: dict[date, list[Record]] = {}
        for r in rows:
            rec = record_from_csv(r, self.fields)
            if rec is not None and rec.day in wanted:
                by_day.setdefault(rec.day, []).append(rec)

        written = 0
        ref_args = self.reference_args()
        for day in sorted(by_day):
            for rec in by_day[day]:
                try:
                    write_workbook_record(
                        wb_path, self.fields, rec,
                        campaign=campaign_for(entries, rec.day)[0],
                        log_fn=self._log, **ref_args)
                    written += 1
                except (WorkbookLocked, ImportError):
                    return
                except Exception as exc:
                    self._log(f"{rec.stamp}: {type(exc).__name__}: {exc}")
        if written:
            res.workbook_written = True
            self._log(f"Added {len(by_day)} missing day(s) to the workbook.")

    # -- deleting ---------------------------------------------------------
    def delete_record(self, rec: Record) -> SaveResult:
        """Delete one recorded moment for good.

        Order matters, and not the obvious way. Deleting the row from one log
        and not the other brings it straight back: the two are compared by
        length, so the copy that still has the row wins, and sync() then pushes
        it to the one that lost it. So the first thing written is the
        tombstone -- the note that says this moment is gone. From that instant
        the moment is invisible to every reader, and every step after it can be
        retried or finished by the next sync.
        """
        res = SaveResult(share_root=self.share_root)
        stamp = rec.stamp
        with self._io_lock:
            entries = read_json_map(self.local_tombstones, "deleted")
            folder = self.share_folder
            if folder:
                entries = merge_day_map(
                    entries, read_json_map(folder / TOMBSTONES_NAME, "deleted"),
                    stamp_field="at")
            entries[stamp] = {"at": now_stamp(), "by": who(),
                              "day": rec.day.isoformat(), "slot": rec.slot}
            try:
                write_json_map(self.local_tombstones, "deleted", entries)
            except Exception as exc:
                res.problem = f"The moment could not be deleted: {exc}"
                self._log(res.problem)
                return res
            self._deleted = set(entries)

            if folder is not None:
                try:
                    write_json_map(folder / TOMBSTONES_NAME, "deleted", entries)
                except Exception as exc:
                    self._log(f"The shared list of deleted moments could not be "
                              f"written: {exc}")

            paths = [self.local_csv]
            if folder is not None:
                paths.insert(0, folder / CSV_NAME)
            for path in paths:
                try:
                    remove_from_csv(path, {stamp}, self.fields.columns)
                except Exception as exc:
                    res.problem = (f"The moment is deleted, but {path.name} "
                                   f"could not be rewritten yet: {exc}")
                    self._log(res.problem)

            if folder is None:
                res.problem = ("The shared folder is not reachable - the moment "
                               "is deleted here and will be removed there with "
                               "Sync now.")
                return res

            try:
                clear_workbook_column(folder / WORKBOOK_NAME, self.fields, rec)
                res.workbook_written = True
            except WorkbookLocked:
                res.problem = ("'" + WORKBOOK_NAME + "' is open in Excel - the "
                               "moment is deleted, its column is cleared when "
                               "Excel is closed.")
                self._log(res.problem)
            except ImportError:
                pass
            except Exception as exc:
                res.problem = f"The workbook column could not be cleared: {exc}"
                self._log(res.problem)
        return res


def record_from_csv(row: dict, fields: Fields) -> Record | None:
    try:
        when = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        return None
    when = when.replace(tzinfo=TZ_PRAGUE)
    rec = Record(when=when,
                 slot=row.get("slot", SLOT_NOW),
                 source=row.get("source", "cpva"))
    for c in fields.columns:
        v = row.get(c, "")
        if v not in ("", None):
            rec.values[c] = v
    return rec


# ---------------------------------------------------------------------------
# Which moments should exist
# ---------------------------------------------------------------------------

def scheduled_slots(start: date, end_dt: datetime, cfg: dict) -> list[tuple[datetime, str]]:
    """Every scheduled moment from `start` up to now, in order.

    Weekends are skipped when configured, and a moment in the future is never
    produced -- the archiver cannot answer for it.
    """
    out: list[tuple[datetime, str]] = []
    times = [(_parse_hhmm(cfg.get("slot_morning", "09:00")), SLOT_MORNING),
             (_parse_hhmm(cfg.get("slot_evening", "18:00")), SLOT_EVENING)]
    times.sort()
    weekdays_only = bool(cfg.get("weekdays_only", True))

    day = start
    last = end_dt.date()
    while day <= last:
        if not (weekdays_only and day.weekday() >= 5):
            for (h, m), slot in times:
                ts = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ_PRAGUE)
                if ts <= end_dt:
                    out.append((ts, slot))
        day += timedelta(days=1)
    return out


def catch_up_start(rows: list[dict], cfg: dict, today: date) -> date:
    """Where a catch-up run begins: the day after the last record, bounded so a
    first run on an empty log does not try to reconstruct a year."""
    max_days = int(cfg.get("catch_up_max_days", 30))
    floor = today - timedelta(days=max_days)
    if not rows:
        return floor
    last = ""
    for r in rows:
        d = r.get("date", "")
        if d > last:
            last = d
    try:
        start = datetime.strptime(last, "%Y-%m-%d").date()
    except ValueError:
        return floor
    return max(start, floor)
