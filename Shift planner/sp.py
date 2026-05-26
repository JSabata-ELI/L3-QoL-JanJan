import os
import re
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from urllib.parse import quote

_ANN_RE = re.compile(
    r'^\s*(?P<base>[\d,.\s]+?)'
    r'\s*(?P<marker>\*{1,3}|#{1,3})?'
    r'\s*(?:\((?P<dir>[<>]?)\s*(?P<time>\d{1,2}:\d{2})\))?'
    r'\s*$'
)


def _parse_annotation(val_str: str) -> dict:
    m = _ANN_RE.match(val_str.strip())
    if not m:
        return {"base": val_str.strip(), "marker": "", "direction": "", "time": ""}
    base = (m.group("base") or "").strip().rstrip(",").strip()
    return {
        "base":      base,
        "marker":    m.group("marker") or "",
        "direction": m.group("dir") or "",
        "time":      m.group("time") or "",
    }


def _norm_cell(val) -> str:
    """Normalize an Excel cell value to a comparable string.
    Converts float 1.3 → '1,3', 1.23 → '1,2,3', etc."""
    if val is None:
        return ""
    if isinstance(val, float):
        return str(val).replace(".", ",")
    return str(val).strip()


try:
    import openpyxl
    from openpyxl.utils import get_column_letter, column_index_from_string
    _OPENPYXL = True
except ImportError:
    _OPENPYXL = False

FOLDER = Path(r"C:\Users\jan.moucka\OneDrive - ELI Beamlines\L3-HAPLS\General")
PATTERN = re.compile(r"^Shift_plan_.*\.xlsx$", re.IGNORECASE)
SHAREPOINT_BASE = "https://elibeamlines.sharepoint.com/sites/L3-HAPLS/Sdilene%20dokumenty/General"

DAY_CS  = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
VALUE_MAP = {"1,5 shifts": "1,3", "2 shifts": "1,2,3"}


def find_latest() -> Path | None:
    try:
        candidates = [f for f in FOLDER.iterdir() if PATTERN.match(f.name)]
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: [int(n) for n in re.findall(r"\d+", p.name)], reverse=True)
    return candidates[0]


def open_online(local_path: Path):
    sp_url = f"{SHAREPOINT_BASE}/{quote(local_path.name)}"
    os.startfile(f"ms-excel:ofe|u|{sp_url}")


def _rgb_str(fc) -> str | None:
    """Return 6-char hex RGB string from an openpyxl fgColor, or None."""
    try:
        if fc.type == "theme":
            return None  # theme colours — can't resolve without theme XML
        rgb = fc.rgb  # may be an RGB object or a plain str
        s = str(rgb) if not isinstance(rgb, str) else rgb
        s = s.lstrip("#")
        if len(s) == 8:   # AARRGGBB
            if s[:2] in ("00", "FF") and s[2:] in ("000000",):
                return None
            return s[2:]
        if len(s) == 6:
            return s if s != "000000" else None
    except Exception:
        pass
    return None


def _has_color(cell):
    fill = cell.fill
    if not fill or not fill.fgColor:
        return False
    fc = fill.fgColor
    if fc.type == "none":
        return False
    return _rgb_str(fc) is not None


def _cell_bg_hex(cell) -> str | None:
    fill = cell.fill
    if not fill or not fill.fgColor:
        return None
    fc = fill.fgColor
    if fc.type == "none":
        return None
    s = _rgb_str(fc)
    return ("#" + s) if s else None


class _Tooltip:
    def __init__(self, widget, text: str):
        self._widget = widget
        self._text = text
        self._tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height()
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(self._tip, text=self._text, justify="left",
                       bg="#ffffe0", relief="solid", borderwidth=1,
                       font=("TkDefaultFont", 8), padx=4, pady=2)
        lbl.pack()

    def _hide(self, event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


class ShiftPlannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Shift Planner")
        self.geometry("940x860")
        self.resizable(True, True)

        self._filepath       = tk.StringVar(value="")
        self._filepath_short = tk.StringVar(value="")
        self._short_pulse    = tk.BooleanVar(value=False)
        self._wb             = None
        self._ws             = None
        self._days           = []
        self._row_sp         = None
        self._row_main       = None
        self._value_vars     = {}
        self._note_vars: dict = {}
        self._time_vars: dict = {}
        self._day_frame_rows = {}
        self._preview_after_id = None
        self._building       = False
        self._selected_user  = tk.StringVar(value="Honza M.")
        self._all_users: list = []
        self._user_row_map: dict = {}   # name → [row, ...] pre-loaded once per file
        self._third_shift_var: tk.StringVar  # created in _build_ui
        self._row_widgets: dict = {}
        self._grid_label_cache: dict = {}
        self._grid_cell_map: dict = {}
        self._grid_person_rows: list = []

        self._build_ui()
        self._auto_load()

    # ------------------------------------------------------------------ build UI
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        frm_top = ttk.Frame(self)
        frm_top.pack(fill="x", padx=8, pady=(8, 2))

        ttk.Button(frm_top, text="Browse...",      command=self._browse).pack(side="left", padx=(0, 4))
        ttk.Button(frm_top, text="Open in Excel",  command=self._open_in_excel).pack(side="left", padx=4)
        ttk.Button(frm_top, text="Write to Excel", command=self._do_write).pack(side="left", padx=4)
        self._status = tk.StringVar(value="Waiting for file...")
        ttk.Label(frm_top, textvariable=self._status, foreground="#555").pack(side="left", padx=8)
        ttk.Label(frm_top, textvariable=self._filepath_short, anchor="w",
                  foreground="#444").pack(side="left", fill="x", expand=True, padx=8)

        frm_cfg = ttk.Frame(self)
        frm_cfg.pack(fill="x", padx=8, pady=2)

        ttk.Label(frm_cfg, text="User:").pack(side="left", padx=(0, 4))
        self._user_combo = ttk.Combobox(frm_cfg, textvariable=self._selected_user,
                                        width=10, state="readonly")
        self._user_combo.pack(side="left", padx=(0, 12))
        self._user_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_user_changed())

        self._sp_check = ttk.Checkbutton(
            frm_cfg, text="Short Pulse also",
            variable=self._short_pulse
        )
        # shown only for Honza M. — visibility managed in _update_sp_check

        ttk.Label(frm_cfg, text="# 3rd shifts:").pack(side="left", padx=(0, 4))
        self._third_shift_var = tk.StringVar(value="—")
        ttk.Label(frm_cfg, textvariable=self._third_shift_var,
                  font=("TkDefaultFont", 9, "bold"),
                  foreground="#005599").pack(side="left", padx=(0, 12))

        if not _OPENPYXL:
            ttk.Label(frm_cfg, text="openpyxl not installed — fill disabled",
                      foreground="red").pack(side="left", padx=8)

        self._frm_presets = ttk.Frame(self)
        self._frm_presets.pack(fill="x", padx=8, pady=2)

        ttk.Label(self._frm_presets, text="Week:").pack(side="left", padx=(0, 4))
        self._preset_week_var = tk.StringVar()
        self._preset_week_combo = ttk.Combobox(
            self._frm_presets, textvariable=self._preset_week_var,
            width=18, state="readonly"
        )
        self._preset_week_combo.pack(side="left", padx=(0, 8))

        ttk.Button(self._frm_presets, text="Max availability",
                   command=self._preset_max).pack(side="left", padx=4)
        ttk.Button(self._frm_presets, text="All morning (1)",
                   command=self._preset_morning).pack(side="left", padx=4)
        ttk.Button(self._frm_presets, text="All evening (3)",
                   command=self._preset_evening).pack(side="left", padx=4)

        # --- Days table ---
        frm_days = ttk.LabelFrame(self, text="Days")
        frm_days.pack(fill="both", expand=True, **pad)

        headers    = ["Day", "Date", "Shifts", "Value", "Note", "Time", "Status"]
        col_widths = [38, 88, 120, 90, 160, 65, 125]
        for ci, (h, w) in enumerate(zip(headers, col_widths)):
            ttk.Label(frm_days, text=h, anchor="center",
                      font=("TkDefaultFont", 9, "bold")).grid(
                row=0, column=ci, padx=2, pady=2, sticky="ew")
            frm_days.columnconfigure(ci, minsize=w)

        canvas = tk.Canvas(frm_days, borderwidth=0, highlightthickness=0)
        vsb    = ttk.Scrollbar(frm_days, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=1, column=0, columnspan=7, sticky="nsew")
        vsb.grid(row=1, column=7, sticky="ns")
        frm_days.rowconfigure(1, weight=1)

        self._inner = tk.Frame(canvas)
        self._cwin  = canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(self._cwin, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        self._canvas = canvas

        # --- Preview (grid nahoře + diff dole) ---
        frm_prev = ttk.LabelFrame(self, text="Preview")
        frm_prev.pack(fill="x", **pad)

        # Horní část: scrollovatelná mřížka
        self._preview_grid_frame = ttk.Frame(frm_prev)
        self._preview_grid_frame.pack(fill="x", padx=4, pady=(4, 0))

        # No vertical scrollbar — canvas auto-sizes to fit all rows
        self._grid_canvas = tk.Canvas(self._preview_grid_frame,
                                      borderwidth=0, highlightthickness=0)
        gsb_h = ttk.Scrollbar(self._preview_grid_frame, orient="horizontal",
                               command=self._grid_canvas.xview)
        self._grid_canvas.configure(xscrollcommand=gsb_h.set)
        self._grid_canvas.grid(row=0, column=0, sticky="nsew")
        gsb_h.grid(row=1, column=0, sticky="ew")
        self._grid_canvas.bind("<Enter>",
            lambda e: self._grid_canvas.bind_all(
                "<MouseWheel>",
                lambda ev: self._grid_canvas.xview_scroll(int(-1*(ev.delta/120)), "units")
            )
        )
        self._grid_canvas.bind("<Leave>",
            lambda e: self._grid_canvas.unbind_all("<MouseWheel>")
        )
        self._preview_grid_frame.columnconfigure(0, weight=1)

        self._grid_inner = tk.Frame(self._grid_canvas)
        self._grid_cwin  = self._grid_canvas.create_window((0, 0), window=self._grid_inner, anchor="nw")
        self._grid_inner.bind(
            "<Configure>",
            lambda e: (
                self._grid_canvas.configure(
                    scrollregion=self._grid_canvas.bbox("all"),
                    height=e.height,
                ),
            )
        )

        # Dolní část: textový diff
        self._preview_text = tk.Text(frm_prev, height=32, state="disabled",
                                     wrap="word", font=("Consolas", 9))
        sb2 = ttk.Scrollbar(frm_prev, orient="vertical", command=self._preview_text.yview)
        self._preview_text.configure(yscrollcommand=sb2.set)
        self._preview_text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb2.pack(side="right", fill="y", pady=4)


    # ------------------------------------------------------------------ auto-load
    def _auto_load(self):
        local = Path(__file__).parent / "Shift_plan_26_KW22_26.xlsx"
        if local.exists():
            self._load_file(str(local))
            return
        latest = find_latest()
        if latest and latest.exists():
            self._load_file(str(latest))

    # ------------------------------------------------------------------ file ops
    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select Excel file",
            filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")]
        )
        if path:
            self._load_file(path)

    def _open_in_excel(self):
        path = self._filepath.get()
        if not path:
            messagebox.showwarning("No file", "No file loaded yet.")
            return
        try:
            open_online(Path(path))
        except Exception as e:
            messagebox.showerror("Error", str(e))


    def _load_file(self, path: str):
        if not _OPENPYXL:
            self._filepath.set(path)
            self._filepath_short.set(path)
            self._status.set("openpyxl not installed — cannot read file.")
            return
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot open file:\n{e}")
            return
        ws = next((wb[n] for n in wb.sheetnames if "availability" in n.lower()), wb.active)
        self._wb = wb
        self._ws = ws
        self._filepath.set(path)

        parts = Path(path).parts
        try:
            idx = next(i for i, p in enumerate(parts) if p.lower() == "python")
            short = "...\\" + "\\".join(parts[idx:])
        except StopIteration:
            short = path
        self._filepath_short.set(short)

        self._preload_user_rows()
        self._parse_sheet()
        self._build_table_widgets()
        self._build_grid_widgets()
        self._status.set(f"Loaded: {os.path.basename(path)}")

    # ------------------------------------------------------------------ parsing
    def _preload_user_rows(self):
        """Read the entire sheet into RAM once. All subsequent operations are dict lookups."""
        ws = self._ws

        # 1. name → [row numbers] across whole sheet
        row_map: dict[str, list[int]] = {}
        for r in range(1, ws.max_row + 1):
            v = ws.cell(r, 1).value
            if isinstance(v, str) and v.strip():
                row_map.setdefault(v.strip(), []).append(r)
        self._user_row_map = row_map

        # 2. Dropdown names = rows 12-22 only
        seen: set[str] = set()
        all_names: list[str] = []
        for r in range(12, 23):
            v = ws.cell(r, 1).value
            if isinstance(v, str) and v.strip() and v.strip() not in seen:
                all_names.append(v.strip())
                seen.add(v.strip())
        self._all_users = all_names
        self._user_combo["values"] = all_names
        if self._selected_user.get() not in all_names and all_names:
            self._selected_user.set(all_names[0])

        # 3. Pre-read all cell values and colors for rows 10-28, all data columns
        #    _sheet_cache[(row, col)] = {"val": ..., "has_color": bool}
        cache: dict[tuple[int,int], dict] = {}
        for r in range(10, 29):
            for c in range(2, ws.max_column + 1):
                cell = ws.cell(r, c)
                cache[(r, c)] = {"val": cell.value, "has_color": _has_color(cell),
                                 "bg": _cell_bg_hex(cell)}
        # Also cache row 10 dates and row 11 shift labels
        self._sheet_cache = cache

        # 4. Build day column list (col index, letter, date, shift_label)
        self._day_col_defs: list[tuple[int, str, datetime.datetime, str | None]] = []
        for c in range(2, ws.max_column + 1):
            v = cache.get((10, c), {}).get("val")
            if isinstance(v, datetime.datetime):
                slbl = cache.get((11, c), {}).get("val")
                self._day_col_defs.append((c, get_column_letter(c), v, slbl))

    def _parse_sheet(self):
        """Build self._days from pre-loaded cache — no Excel reads."""
        self._days = []
        self._row_sp   = None
        self._row_main = None

        target = self._selected_user.get()
        target_rows = self._user_row_map.get(target, [])
        if len(target_rows) >= 2:
            self._row_sp, self._row_main = target_rows[0], target_rows[1]
        elif len(target_rows) == 1:
            self._row_sp = None
            self._row_main = target_rows[0]
        else:
            self._row_sp = None
            self._row_main = None

        self._update_sp_check()
        self._update_third_shift_count()

        skip = {self._row_sp, self._row_main} - {None}
        for (col, col_letter, date_val, shift_label) in self._day_col_defs:
            default_val = VALUE_MAP.get(shift_label) if shift_label else None

            raw_main = self._sheet_cache.get((self._row_main, col), {}).get("val") if self._row_main else None
            raw_sp   = self._sheet_cache.get((self._row_sp,   col), {}).get("val") if self._row_sp   else None

            if raw_main is not None:
                existing_main = str(raw_main).replace(".", ",") if isinstance(raw_main, float) else str(raw_main).strip()
            else:
                existing_main = ""
            parsed = _parse_annotation(existing_main) if existing_main else {"base": "", "marker": "", "direction": "", "time": ""}
            init_val = parsed["base"]

            init_note = ""
            if parsed["marker"]:
                marker = parsed["marker"]
                for note_row in range(23, 29):
                    note_cell_val = self._sheet_cache.get((note_row, col), {}).get("val")
                    if note_cell_val is not None:
                        note_str = str(note_cell_val).strip()
                        if note_str:
                            init_note = note_str
                            break

            # decided: any color in rows 12-28 for this col, excluding user's own rows
            decided = any(
                self._sheet_cache.get((r, col), {}).get("has_color", False)
                for r in range(12, 29)
                if r not in skip
            )

            self._days.append({
                "col":            col,
                "col_letter":     col_letter,
                "date":           date_val,
                "shift_label":    shift_label,
                "default_val":    default_val,
                "init_val":       init_val,
                "init_note":      init_note,
                "init_time":      (parsed["direction"] + parsed["time"]) if parsed["time"] else "",
                "init_direction": parsed["direction"],
                "init_marker":    parsed["marker"],
                "decided":        decided,
                "val_sp":         raw_sp,
                "val_main":       raw_main,
                "note_vars":      {},
                "time_vars":      {},
            })

    def _on_user_changed(self):
        self._refresh_table_rows()

    def _update_sp_check(self):
        """Show 'Short Pulse also' checkbox only for Honza M. (has two rows in sheet)."""
        if self._selected_user.get() == "Honza M." and self._row_sp is not None:
            self._sp_check.pack(side="left", padx=4)
        else:
            self._sp_check.pack_forget()

    def _update_third_shift_count(self):
        """Count colored cells containing '3' in the user's main row across all day columns."""
        if not self._row_main or not hasattr(self, "_sheet_cache"):
            self._third_shift_var.set("—")
            return
        count = 0
        for (col, _cl, _dt, _slbl) in self._day_col_defs:
            cached = self._sheet_cache.get((self._row_main, col), {})
            if not cached.get("has_color"):
                continue
            val = cached.get("val")
            if val is None:
                continue
            s = str(val).replace(".", ",")
            # Check if the value contains "3" as a shift component (e.g. "3", "1,3", "1,2,3")
            parts = re.split(r"[,\s*#(><=]+", s)
            if "3" in parts:
                count += 1
        self._third_shift_var.set(str(count))

    def _col_is_decided(self, col: int) -> bool:
        skip = {self._row_sp, self._row_main} - {None}
        return any(
            self._sheet_cache.get((r, col), {}).get("has_color", False)
            for r in range(12, 29) if r not in skip
        )

    # ------------------------------------------------------------------ week presets
    def _refresh_preset_weeks(self):
        seen = {}
        for d in self._days:
            if d["date"].weekday() in (5, 6):
                continue
            iso = d["date"].isocalendar()
            wk = iso[1]
            if wk not in seen:
                days_in_wk = [x["date"] for x in self._days
                              if x["date"].isocalendar()[1] == wk
                              and x["date"].weekday() not in (5, 6)]
                if days_in_wk:
                    mn = min(days_in_wk).strftime("%d.%m")
                    mx = max(days_in_wk).strftime("%d.%m")
                    seen[wk] = f"KW{wk} ({mn}–{mx})"
        weeks = [seen[k] for k in sorted(seen.keys())]
        self._preset_week_combo["values"] = weeks
        if weeks and self._preset_week_var.get() not in weeks:
            self._preset_week_var.set(weeks[0])

    def _days_in_preset_week(self) -> list:
        wk = self._preset_week_var.get()
        if not wk:
            return []
        m = re.match(r"KW(\d+)", wk)
        if not m:
            return []
        wk_num = int(m.group(1))
        return [d for d in self._days if d["date"].isocalendar()[1] == wk_num
                and d["date"].weekday() not in (5, 6)
                and bool(d["default_val"])]

    def _preset_max(self):
        for day in self._days_in_preset_week():
            cl = day["col_letter"]
            if cl in self._value_vars:
                self._value_vars[cl].set(day["default_val"])

    def _preset_morning(self):
        for day in self._days_in_preset_week():
            cl = day["col_letter"]
            if cl in self._value_vars:
                self._value_vars[cl].set("1")

    def _preset_evening(self):
        for day in self._days_in_preset_week():
            cl = day["col_letter"]
            if cl in self._value_vars:
                self._value_vars[cl].set("3")

    # ------------------------------------------------------------------ table
    def _build_table_widgets(self):
        self._building = True
        for w in self._inner.winfo_children():
            w.destroy()
        self._value_vars.clear()
        self._note_vars.clear()
        self._time_vars.clear()
        self._day_frame_rows.clear()
        self._row_widgets.clear()

        col_widths = [38, 88, 120, 90, 160, 65, 125]
        for ci in range(7):
            self._inner.columnconfigure(ci, minsize=col_widths[ci])

        entry_kw = {
            "disabledbackground": "#e8e8e8",
            "disabledforeground": "#aaa",
            "relief": "sunken",
        }

        for di, day in enumerate(self._days):
            init_val    = day["init_val"]
            decided     = day["decided"]
            date        = day["date"]
            col_letter  = day["col_letter"]
            has_value   = day["val_main"] is not None
            is_weekend  = date.weekday() in (5, 6)

            editable = bool(day["default_val"]) or has_value or date.date() >= datetime.date.today()

            if is_weekend:
                stav_text, stav_fg, bg, entry_state = "— weekend",  "#999", "#e8e8e8", "disabled"
            elif not editable:
                stav_text, stav_fg, bg, entry_state = "— no shift", "#999", "#f0f0f0", "disabled"
            elif decided:
                stav_text, stav_fg, bg, entry_state = "⚠ decided",  "#cc6600", "#fff8e8", "normal"
            elif has_value:
                stav_text, stav_fg, bg, entry_state = "✎ has value", "#005599", "#eef4ff", "normal"
            else:
                stav_text, stav_fg, bg, entry_state = "✓ empty",    "#007700", "white",   "normal"

            var = tk.StringVar(value=init_val)
            self._value_vars[col_letter] = var

            note_var = tk.StringVar(value=day.get("init_note", ""))
            time_var = tk.StringVar(value=day.get("init_time", ""))
            self._note_vars[col_letter] = note_var
            self._time_vars[col_letter] = time_var

            var.trace_add("write", lambda *_: self._schedule_preview())
            note_var.trace_add("write", lambda *_: self._schedule_preview())
            time_var.trace_add("write", lambda *_: self._schedule_preview())

            entry_bg = "white" if entry_state == "normal" else "#f0f0f0"
            day_fg = "#888" if entry_state == "disabled" else "#333"

            lbl_day = tk.Label(self._inner, text=DAY_CS[date.weekday()], anchor="center",
                               bg=bg, fg=day_fg, font=("TkDefaultFont", 9))
            lbl_day.grid(row=di, column=0, padx=2, pady=1, sticky="ew")

            lbl_date = tk.Label(self._inner, text=date.strftime("%d.%m.%Y"), anchor="center",
                                bg=bg, fg=day_fg, font=("TkDefaultFont", 9))
            lbl_date.grid(row=di, column=1, padx=2, pady=1, sticky="ew")

            shifts_fg = "#cc0000" if (day["shift_label"] == "2 shifts" and entry_state != "disabled") else day_fg
            lbl_shifts = tk.Label(self._inner, text=day["shift_label"] or "—", anchor="center",
                                  bg=bg, fg=shifts_fg, font=("TkDefaultFont", 9))
            lbl_shifts.grid(row=di, column=2, padx=2, pady=1, sticky="ew")

            e_val = tk.Entry(self._inner, textvariable=var, width=11,
                             state=entry_state, bg=entry_bg, **entry_kw)
            e_val.grid(row=di, column=3, padx=2, pady=1, sticky="ew")

            e_note = tk.Entry(self._inner, textvariable=note_var, width=20,
                              state=entry_state, bg=entry_bg, **entry_kw)
            e_note.grid(row=di, column=4, padx=2, pady=1, sticky="ew")

            e_time = tk.Entry(self._inner, textvariable=time_var, width=7,
                              state=entry_state, bg=entry_bg, **entry_kw)
            e_time.grid(row=di, column=5, padx=2, pady=1, sticky="ew")

            lbl_status = tk.Label(self._inner, text=stav_text, anchor="center",
                                  bg=bg, fg=stav_fg, font=("TkDefaultFont", 9))
            lbl_status.grid(row=di, column=6, padx=2, pady=1, sticky="ew")

            self._row_widgets[col_letter] = {
                "lbl_day":    lbl_day,
                "lbl_date":   lbl_date,
                "lbl_shifts": lbl_shifts,
                "e_val":      e_val,
                "e_note":     e_note,
                "e_time":     e_time,
                "lbl_status": lbl_status,
            }

        self._refresh_preset_weeks()
        self._building = False
        self.after(50, self._do_preview)

    def _refresh_table_rows(self):
        self._building = True
        self._parse_sheet()

        for day in self._days:
            cl          = day["col_letter"]
            decided     = day["decided"]
            date        = day["date"]
            has_value   = day["val_main"] is not None
            is_weekend  = date.weekday() in (5, 6)

            editable = bool(day["default_val"]) or has_value or date.date() >= datetime.date.today()

            if is_weekend:
                stav_text, stav_fg, bg, entry_state = "— weekend",  "#999", "#e8e8e8", "disabled"
            elif not editable:
                stav_text, stav_fg, bg, entry_state = "— no shift", "#999", "#f0f0f0", "disabled"
            elif decided:
                stav_text, stav_fg, bg, entry_state = "⚠ decided",  "#cc6600", "#fff8e8", "normal"
            elif has_value:
                stav_text, stav_fg, bg, entry_state = "✎ has value", "#005599", "#eef4ff", "normal"
            else:
                stav_text, stav_fg, bg, entry_state = "✓ empty",    "#007700", "white",   "normal"

            entry_bg = "white" if entry_state == "normal" else "#f0f0f0"
            day_fg   = "#888" if entry_state == "disabled" else "#333"

            if cl in self._value_vars:
                self._value_vars[cl].set(day["init_val"])
            if cl in self._note_vars:
                self._note_vars[cl].set(day.get("init_note", ""))
            if cl in self._time_vars:
                self._time_vars[cl].set(day.get("init_time", ""))

            widgets = self._row_widgets.get(cl)
            if widgets:
                for lbl_key in ("lbl_day", "lbl_date", "lbl_shifts", "lbl_status"):
                    widgets[lbl_key].config(bg=bg)
                widgets["lbl_day"].config(fg=day_fg)
                widgets["lbl_date"].config(fg=day_fg)
                shifts_fg = "#cc0000" if (day["shift_label"] == "2 shifts" and entry_state != "disabled") else day_fg
                widgets["lbl_shifts"].config(fg=shifts_fg, text=day["shift_label"] or "—")
                widgets["lbl_status"].config(fg=stav_fg, text=stav_text)
                for ek in ("e_val", "e_note", "e_time"):
                    widgets[ek].config(state=entry_state, bg=entry_bg)

        self._refresh_preset_weeks()
        self._building = False
        self.after(50, self._do_preview)

    # ------------------------------------------------------------------ marker
    def _pick_marker(self, col: int) -> str:
        ws = self._ws
        used = set()
        for row in range(12, 29):
            val = ws.cell(row, col).value
            if val is None:
                continue
            s = str(val)
            if "***" in s:
                used.add("***")
            elif "**" in s:
                used.add("**")
            elif "*" in s:
                used.add("*")
            if "###" in s:
                used.add("###")
            elif "##" in s:
                used.add("##")
            elif "#" in s:
                used.add("#")
        for marker in ("*", "**", "#", "##", "###"):
            if marker not in used:
                return marker
        return "#"

    # ------------------------------------------------------------------ logic
    def _compute_changes(self):
        use_sp = self._short_pulse.get() and self._row_sp is not None
        changes_main, changes_sp, warnings = [], [], []

        for day in self._days:
            has_value = day["val_main"] is not None
            editable  = bool(day["default_val"]) or has_value or day["date"].date() >= datetime.date.today()
            is_weekend = day["date"].weekday() in (5, 6)
            if not editable or is_weekend:
                continue
            cl         = day["col_letter"]
            date_str   = f"{day['date'].strftime('%d.%m.%Y')} {DAY_CS[day['date'].weekday()]}"
            user_val   = self._value_vars[cl].get().strip()

            note_text  = self._note_vars.get(cl, tk.StringVar()).get().strip()
            time_raw   = self._time_vars.get(cl, tk.StringVar()).get().strip()

            if note_text:
                marker = self._pick_marker(day["col"])
                if time_raw:
                    user_val = f"{user_val}{marker} ({time_raw})"
                else:
                    user_val = f"{user_val}{marker}"

            if not user_val:
                continue

            if day["decided"]:
                warnings.append((cl, date_str))

            cur_main = _norm_cell(day["val_main"])
            if cur_main != user_val:
                changes_main.append({"cl": cl, "date_str": date_str,
                                     "old": day["val_main"], "new": user_val,
                                     "decided": day["decided"]})

            if use_sp:
                cur_sp = _norm_cell(day["val_sp"])
                if cur_sp != user_val:
                    changes_sp.append({"cl": cl, "date_str": date_str,
                                       "old": day["val_sp"], "new": user_val,
                                       "decided": day["decided"]})

        return changes_main, changes_sp, warnings

    # ------------------------------------------------------------------ preview
    def _schedule_preview(self):
        if self._building:
            return
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
        self._preview_after_id = self.after(150, self._do_preview)

    def _do_preview(self):
        if not self._ws:
            self._status.set("Load a file first.")
            return
        self._render_grid_preview()
        self._render_diff_preview()

    def _build_grid_widgets(self):
        for w in self._grid_inner.winfo_children():
            w.destroy()
        self._grid_label_cache.clear()
        self._grid_cell_map.clear()
        self._grid_person_rows.clear()

        ws = self._ws
        if ws is None:
            return

        day_cols = self._day_col_defs
        weekend_cols = {cl for (_, cl, dt, _s) in day_cols if dt.weekday() in (5, 6)}

        # Corner header labels (text never changes)
        lbl_name = tk.Label(self._grid_inner, text="Name", width=12, relief="ridge",
                            bg="#d8d8d8", font=("TkDefaultFont", 8, "bold"), anchor="w")
        lbl_name.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self._grid_label_cache[("hdr_name", 0)] = lbl_name

        lbl_shifts_hdr = tk.Label(self._grid_inner, text="Shifts", width=12, relief="ridge",
                                  bg="#e4e4e4", font=("TkDefaultFont", 7), foreground="#555", anchor="w")
        lbl_shifts_hdr.grid(row=1, column=0, sticky="nsew", padx=1, pady=1)
        self._grid_label_cache[("hdr_shifts", 0)] = lbl_shifts_hdr

        for gi, (col, cl, dt, slbl) in enumerate(day_cols):
            is_weekend = dt.weekday() in (5, 6)
            w = 5 if is_weekend else 8
            bg_hdr = "#bbb" if is_weekend else "#ddd"

            lbl_date_hdr = tk.Label(self._grid_inner, text=dt.strftime("%d.%m"), width=w,
                                    relief="ridge", bg=bg_hdr,
                                    font=("TkDefaultFont", 8, "bold"), anchor="center")
            lbl_date_hdr.grid(row=0, column=gi + 1, sticky="nsew", padx=1, pady=1)
            self._grid_label_cache[("hdr_date", gi)] = lbl_date_hdr

            if is_weekend:
                shift_text = ""
            elif slbl == "1,5 shifts":
                shift_text = "1.5"
            elif slbl == "2 shifts":
                shift_text = "2"
            else:
                shift_text = "?"
            lbl_shift_hdr = tk.Label(self._grid_inner, text=shift_text, width=w,
                                     relief="ridge", bg="#bbb" if is_weekend else "#eee",
                                     font=("TkDefaultFont", 7), foreground="#555", anchor="center")
            lbl_shift_hdr.grid(row=1, column=gi + 1, sticky="nsew", padx=1, pady=1)
            self._grid_label_cache[("hdr_shift", gi)] = lbl_shift_hdr

        # Group person rows 12-22
        groups: list[list[int]] = []
        cur_group: list[int] = []
        for row in range(12, 23):
            v = ws.cell(row, 1).value
            if v not in (None, ""):
                cur_group.append(row)
            else:
                if cur_group:
                    groups.append(cur_group)
                    cur_group = []
        if cur_group:
            groups.append(cur_group)

        grid_row = 2
        for gi_grp, grp in enumerate(groups):
            if gi_grp > 0:
                sep = tk.Frame(self._grid_inner, height=4, bg="#ccc")
                sep.grid(row=grid_row, column=0, columnspan=len(day_cols) + 1,
                         sticky="ew", padx=1, pady=0)
                self._grid_label_cache[("sep", gi_grp)] = sep
                grid_row += 1

            for row in grp:
                name_val = str(ws.cell(row, 1).value)
                name_lbl = tk.Label(self._grid_inner, text=name_val, width=12, anchor="w",
                                    relief="ridge", bg="#f0f0f0", font=("TkDefaultFont", 8))
                name_lbl.grid(row=grid_row, column=0, sticky="nsew", padx=1, pady=1)
                _Tooltip(name_lbl, name_val)
                self._grid_label_cache[(row, 0)] = name_lbl
                self._grid_person_rows.append((row, grid_row))

                for di, (col, cl, dt, _slbl) in enumerate(day_cols):
                    cached   = self._sheet_cache.get((row, col), {})
                    cell_val = cached.get("val")
                    text     = str(cell_val) if cell_val is not None else ""
                    cell_bg  = cached.get("bg")
                    is_wknd  = cl in weekend_cols
                    w = 5 if is_wknd else 8
                    font = ("TkDefaultFont", 8)
                    if not cell_bg:
                        cell_bg = "#e0e0e0" if is_wknd else "white"
                    display = text if text else ("X" if is_wknd else "")
                    lbl = tk.Label(self._grid_inner, text=display, width=w, anchor="w",
                                   relief="ridge", bg=cell_bg, font=font,
                                   fg="#999" if (is_wknd and not text) else "#000")
                    lbl.grid(row=grid_row, column=di + 1, sticky="nsew", padx=1, pady=1)
                    if text:
                        _Tooltip(lbl, text)
                    self._grid_label_cache[(row, col)] = lbl
                    self._grid_cell_map[(row, cl)] = (grid_row, di + 1)

                grid_row += 1

        self._grid_canvas.configure(scrollregion=self._grid_canvas.bbox("all"))

    def _render_grid_preview(self):
        ws = self._ws
        if ws is None:
            return

        if not self._grid_label_cache:
            self._build_grid_widgets()

        cm, cs, _ = self._compute_changes()
        pending_cols = {ch["cl"] for ch in cm + cs}

        day_cols = self._day_col_defs
        weekend_cols = {cl for (_, cl, dt, _s) in day_cols if dt.weekday() in (5, 6)}

        # Update date header backgrounds
        for gi, (col, cl, dt, slbl) in enumerate(day_cols):
            is_weekend = dt.weekday() in (5, 6)
            is_pend = cl in pending_cols
            bg_hdr = "#bbb" if is_weekend else ("#ffe066" if is_pend else "#ddd")
            lbl_dh = self._grid_label_cache.get(("hdr_date", gi))
            if lbl_dh:
                lbl_dh.config(bg=bg_hdr)

        # Update person cell labels
        use_sp = self._short_pulse.get() and self._row_sp is not None
        for (excel_row, grid_row) in self._grid_person_rows:
            for di, (col, cl, dt, _slbl) in enumerate(day_cols):
                lbl = self._grid_label_cache.get((excel_row, col))
                if lbl is None:
                    continue
                cached   = self._sheet_cache.get((excel_row, col), {})
                cell_val = cached.get("val")
                text     = str(cell_val) if cell_val is not None else ""

                if excel_row in (self._row_main, self._row_sp):
                    user_v = self._value_vars.get(cl, tk.StringVar()).get().strip()
                    if user_v or text:
                        text = user_v if user_v else text

                cell_bg  = cached.get("bg")
                is_wknd  = cl in weekend_cols
                is_pend  = cl in pending_cols and (
                    self._row_main == excel_row or (use_sp and self._row_sp == excel_row)
                )
                w = 5 if is_wknd else 8
                if is_pend:
                    cell_bg = "#ffe066"
                    font    = ("TkDefaultFont", 8, "bold")
                else:
                    font = ("TkDefaultFont", 8)
                    if not cell_bg:
                        cell_bg = "#e0e0e0" if is_wknd else "white"

                display = text if text else ("X" if is_wknd else "")
                lbl.config(text=display, bg=cell_bg, font=font,
                           fg="#999" if (is_wknd and not text) else "#000")

        self._grid_canvas.configure(scrollregion=self._grid_canvas.bbox("all"))

    def _render_diff_preview(self):
        if not self._ws:
            return
        cm, cs, warnings = self._compute_changes()
        lines = ["Pending changes:\n"]

        def _fmt_changes(label, changes):
            if changes:
                lines.append(f"  {label}:")
                for ch in changes:
                    w = "  ⚠" if ch["decided"] else ""
                    lines.append(f"    col {ch['cl']} ({ch['date_str']}): {ch['old']!r} → {ch['new']!r}{w}")
            else:
                lines.append(f"  {label}: no changes")

        user = self._selected_user.get()
        _fmt_changes(f"Row {self._row_main} — {user} (default)", cm)
        if self._short_pulse.get() and self._row_sp:
            lines.append("")
            _fmt_changes(f"Row {self._row_sp} — {user} (Short Pulse)", cs)

        if warnings:
            lines.append("\n  ⚠ WARNING — decided days (other people already have color):")
            for cl, ds in warnings:
                lines.append(f"    col {cl} ({ds})")

        if not cm and not cs:
            lines.append("\n  Nothing to write.")

        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.insert("end", "\n".join(lines))
        self._preview_text.configure(state="disabled")

    # ------------------------------------------------------------------ write
    def _do_write(self):
        if not self._ws:
            self._status.set("Load a file first.")
            return
        cm, cs, _ = self._compute_changes()
        if not cm and not cs:
            messagebox.showinfo("Nothing to do", "No changes to write.")
            return

        decided = [ch for ch in cm + cs if ch["decided"]]
        if decided:
            cols_str = ", ".join(f"{ch['cl']} ({ch['date_str']})" for ch in decided)
            if not messagebox.askyesno(
                "Warning — decided days",
                f"These days are already decided (others have color fill):\n\n{cols_str}\n\n"
                "Do you really want to overwrite them?"
            ):
                self._status.set("Write cancelled.")
                return

        filepath = self._filepath.get()
        try:
            wb_w = openpyxl.load_workbook(filepath)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot open file for writing:\n{e}")
            return
        ws_w = next((wb_w[n] for n in wb_w.sheetnames if "availability" in n.lower()), wb_w.active)

        col_map = {d["col_letter"]: d["col"] for d in self._days}
        count = 0
        for ch in cm:
            ci = col_map.get(ch["cl"])
            if ci and self._row_main:
                ws_w.cell(self._row_main, ci).value = ch["new"]
                count += 1
        for ch in cs:
            ci = col_map.get(ch["cl"])
            if ci and self._row_sp:
                ws_w.cell(self._row_sp, ci).value = ch["new"]
                count += 1

        # Zapsat poznámky do řádků 23–28
        note_written = 0
        for day in self._days:
            cl = day["col_letter"]
            note_text = self._note_vars.get(cl, tk.StringVar()).get().strip()
            if not note_text:
                continue
            ci = col_map.get(cl)
            if not ci:
                continue

            # Najdi první prázdnou buňku v řádcích 23–28
            target_row = None
            existing_notes = {}
            for r in range(23, 29):
                v = ws_w.cell(r, ci).value
                if v is None or str(v).strip() == "":
                    target_row = r
                    break
                else:
                    existing_notes[r] = str(v)

            if target_row is None:
                notes_list = "\n".join(f"  Row {r}: {v}" for r, v in existing_notes.items())
                date_str = day["date"].strftime("%d.%m.%Y")
                ans = simpledialog.askinteger(
                    f"No space in column {cl}",
                    f"No empty row for note in column {cl} ({date_str}).\n\n"
                    f"Current notes:\n{notes_list}\n\n"
                    "Enter row number (23–28) to overwrite, or Cancel to skip:",
                    minvalue=23, maxvalue=28, parent=self
                )
                if ans is None:
                    continue
                target_row = ans

            ws_w.cell(target_row, ci).value = note_text
            note_written += 1

        try:
            wb_w.save(filepath)
        except Exception as e:
            messagebox.showerror("Save error", str(e))
            return

        msg = f"Written {count} cells"
        if note_written:
            msg += f", {note_written} notes"
        self._status.set(msg + ". Reloading...")
        self._load_file(filepath)
        self._status.set(msg + ". Done.")


if __name__ == "__main__":
    app = ShiftPlannerApp()
    app.mainloop()
