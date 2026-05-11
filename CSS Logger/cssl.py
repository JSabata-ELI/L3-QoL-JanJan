"""
CPVA Explorer  —  Interactive PV data explorer from CPVA archiver.
"""
# cssl.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import json
import sys
import re
import fnmatch
import os
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import ssl
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

TZ_PRAGUE = ZoneInfo("Europe/Prague")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


APP_DIR      = get_app_dir()
CONFIG_FILE  = APP_DIR / "cpva_explorer_config.json"
PRESETS_FILE = APP_DIR / "cpva_presets.json"


# ---------------------------------------------------------------------------
# CPVA archiver API constants
# ---------------------------------------------------------------------------

CPVA_BASE_URL          = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_SAMPLES_ENDPOINT  = "/samples"
CPVA_CHANNELS_ENDPOINT = "/channels"
CPVA_HTTP_TIMEOUT      = 10.0
IMAGE_ROOT             = r"\\users-L3.tier0.lcs.local\cpva-image-2026"

# The archiver only returns reliable data when the query window <= 1 h.
CHUNK_SIZE_NS = int(3600 * 1e9)   # 1 hour in nanoseconds

# Rows within this many milliseconds of each other are merged into one.
MERGE_WINDOW_MS = 100
SAMPLE_HOLD_MIN_GAP_MS = 137


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "pv_list":      [],
    "time_from":    "",
    "time_to":      "",
    "http_timeout": 10.0,
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in DEFAULT_CONFIG.items():
                data.setdefault(key, val)
            return data
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    if not CONFIG_FILE.exists():
        return
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Presets  (external file — survives re-deploy, shared across stations)
# ---------------------------------------------------------------------------

def load_presets() -> list[dict]:
    """
    Load presets from PRESETS_FILE.
    Each preset: {"name": str, "pvs": [str, ...], "time_window_hours": float|None}
    Returns [] if file missing or invalid.
    """
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("presets"), list):
                return data["presets"]
        except Exception:
            pass
    return []


def save_presets(presets: list[dict]) -> None:
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump({"presets": presets}, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CPVA API - HTTP
# ---------------------------------------------------------------------------

def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT):
    ctx = _make_ssl_context()
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                       timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    url  = f"{CPVA_BASE_URL}{CPVA_SAMPLES_ENDPOINT}?{params}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data


def _chunk_is_night(chunk_start_ns: int, chunk_end_ns: int) -> bool:
    """Return True if the entire chunk is within 22:00-06:00 Prague time (no data expected)."""
    TZ = ZoneInfo("Europe/Prague")
    now_ns_val = int(datetime.now(timezone.utc).timestamp() * 1e9)
    # Never skip chunks that extend to current time or future
    if chunk_end_ns >= now_ns_val - 60 * 1_000_000_000:  # within 1 min of now
        return False
    dt_start = datetime.fromtimestamp(chunk_start_ns / 1e9, tz=TZ)
    dt_end   = datetime.fromtimestamp(chunk_end_ns   / 1e9, tz=TZ)
    def is_night(h): return h >= 22 or h < 6
    return is_night(dt_start.hour) and is_night(dt_end.hour)


def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                                timeout: float = CPVA_HTTP_TIMEOUT,
                                log_fn=None) -> list[dict]:
    """
    Fetch samples in <=1 h chunks in parallel and concatenate results.
    Night chunks (22:00-06:00 Prague time) are skipped entirely.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total_ns = end_ns - start_ns

    # Build list of (chunk_index, chunk_start, chunk_end) skipping night chunks
    chunks = []
    cs = start_ns
    chunk_idx = 0
    while cs < end_ns:
        ce = min(cs + CHUNK_SIZE_NS, end_ns)
        if not _chunk_is_night(cs, ce):
            chunks.append((chunk_idx, cs, ce))
        chunk_idx += 1
        cs = ce

    if log_fn and total_ns > CHUNK_SIZE_NS:
        log_fn(f"      fetching {len(chunks)} chunk(s) (parallel, night chunks skipped)")

    results_map: dict[int, list] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(cpva_fetch_samples, channel, cs, ce, timeout): i
            for i, cs, ce in chunks
        }
        for fut in as_completed(futures):
            i = futures[fut]
            results_map[i] = fut.result()

    # Reassemble in original chunk order
    results: list[dict] = []
    for i in sorted(results_map):
        results.extend(results_map[i])
    return results


def cpva_decode_value(sample: dict):
    """Extract a usable value from a CPVA sample."""
    val = sample.get("value")
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, list):
        # ASCII string?
        if val and all(isinstance(x, int) and 0 <= x < 128 for x in val):
            try:
                decoded = "".join(chr(x) for x in val)
                if decoded.strip() and all(c.isprintable() or c in "\t\n" for c in decoded):
                    return decoded
            except Exception:
                pass
        if len(val) == 1:
            return val[0]
        return val
    return val


def cpva_fetch_channels(timeout: float = CPVA_HTTP_TIMEOUT) -> list[str]:
    url  = f"{CPVA_BASE_URL}{CPVA_CHANNELS_ENDPOINT}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response from /channels: {type(data).__name__}")
    return [str(x) for x in data]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def dt_to_ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp() * 1e9)


def ns_to_local_str(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    dt  = datetime.fromtimestamp(sec)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{ms:03d}"


def parse_user_datetime(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# PV name shortening
# ---------------------------------------------------------------------------

# Segments stripped from PV names for display (case-insensitive).
_STRIP_PATTERNS = re.compile(
    r"HAPLS[-_]?|"
    r"ENER[-_]?|"
    r"[-_]?IN[-_]?|"
    r"[-_]?LT\d[-_]?|"
    r"[-_]?DIAG\d?[-_]?|"
    r"[-_]{2,}",
    re.IGNORECASE,
)


def shorten_pv_name(full_name: str) -> str:
    """
    Return a compact display label, e.g.:
      HAPLS-ENER-IN-PFM8-LT1-DIAG2:Energy.value  ->  PFM8 - Energy
    """
    # Split on colon: device part and field part
    if ":" in full_name:
        device_part, field_part = full_name.split(":", 1)
    else:
        device_part, field_part = full_name, ""

    # Strip noise from device part
    device = _STRIP_PATTERNS.sub("_", device_part)
    device = re.sub(r"_+", "_", device).strip("_")

    # From the field, take only up to the first dot segment (drop .RBV, .value, etc.)
    field = field_part.split(".")[0] if field_part else ""

    if device and field:
        return f"{device} - {field}"
    return device or field or full_name


# ---------------------------------------------------------------------------
# Wildcard filter
# ---------------------------------------------------------------------------

def _matches_wildcard(text: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern:
        return True
    tl = text.lower()
    pl = pattern.lower()
    if "*" in pl:
        return fnmatch.fnmatch(tl, pl)
    return all(t in tl for t in pl.split())


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _open_path(path_str: str):
    try:
        if sys.platform == "win32":
            os.startfile(path_str)           # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path_str])
        else:
            subprocess.Popen(["xdg-open", path_str])
    except Exception as e:
        messagebox.showerror("Cannot open", f"Failed to open:\n{path_str}\n\n{e}")


def _looks_like_image_path(value: str) -> bool:
    return any(value.lower().endswith(ext)
               for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"))


def _image_file_size(value: str) -> str:
    """Return a human-readable file size for an image path, or '' if unavailable."""
    try:
        path = Path(_resolve_image_path(value))
        sz = path.stat().st_size
        if sz < 1024:
            return f"{sz} B"
        elif sz < 1024 * 1024:
            return f"{sz/1024:.1f} kB"
        else:
            return f"{sz/1024/1024:.2f} MB"
    except Exception:
        return ""


def _resolve_image_path(value: str) -> str:
    """Prepend UNC root; folder structure already uses UTC hours."""
    clean = value.replace("/", "\\").lstrip("\\")
    return IMAGE_ROOT + "\\" + clean


def _popup_geometry(widget: tk.Widget, width: int = 0, height: int = 0) -> str:
    """
    Return a geometry string '+x+y' that places a popup just below the given
    widget, clamped to the screen. Works correctly on secondary monitors.
    """
    widget.update_idletasks()
    x = widget.winfo_rootx()
    y = widget.winfo_rooty() + widget.winfo_height() + 2
    if width and height:
        sw = widget.winfo_screenwidth()
        sh = widget.winfo_screenheight()
        x = min(x, sw - width - 4)
        y = min(y, sh - height - 4)
    return f"+{max(0, x)}+{max(0, y)}"


# ---------------------------------------------------------------------------
# GUI constants
# ---------------------------------------------------------------------------

FONT_NORMAL = ("Segoe UI", 9)
FONT_MONO   = ("Consolas", 9)
FONT_HEADER = ("Segoe UI", 10, "bold")
COLOR_GREEN = "#4CAF50"
COLOR_RED   = "#e53935"
COLOR_BLUE  = "#1976D2"
COLOR_GRAY  = "#666666"


# ---------------------------------------------------------------------------
# Button helper
# ---------------------------------------------------------------------------

def _btn(parent, text, command, bg=None, fg=None, padx=8, pady=4, font=None):
    """Create a consistently styled raised button."""
    kw = dict(
        text=text, command=command,
        font=font or FONT_NORMAL,
        relief=tk.RAISED, bd=1,
        padx=padx, pady=pady,
        cursor="hand2",
    )
    if bg: kw["bg"] = bg
    if fg: kw["fg"] = fg
    if bg and not fg: kw["activebackground"] = bg
    return tk.Button(parent, **kw)


# ---------------------------------------------------------------------------
# Date/time picker dialog  (pure tkinter)
# ---------------------------------------------------------------------------

class DatePickerDialog(tk.Toplevel):
    """
    Standalone modal calendar+time picker.
    on_ok_callback(dt: datetime) is called when user clicks OK.
    """
    MONTHS    = ["January","February","March","April","May","June",
                 "July","August","September","October","November","December"]
    DAY_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    def __init__(self, parent, init_dt: datetime, on_ok_callback,
                 click_x: int = 0, click_y: int = 0):
        super().__init__(parent)
        self.title("Pick date & time")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._callback = on_ok_callback

        self._year  = tk.IntVar(value=init_dt.year)
        self._month = tk.IntVar(value=init_dt.month)
        self._day   = tk.IntVar(value=init_dt.day)
        # Store hour/min/sec as plain StringVar to avoid octal-parse bug
        # (Spinbox with format="%02.0f" produces "09" which IntVar rejects)
        self._hour = tk.StringVar(value=f"{init_dt.hour:02d}")
        self._min  = tk.StringVar(value=f"{init_dt.minute:02d}")
        self._sec  = tk.StringVar(value=f"{init_dt.second:02d}")

        self._build_ui()
        self._draw_calendar()

        # Position near the trigger point
        self.update_idletasks()
        dw = self.winfo_reqwidth()
        dh = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        px = min(click_x + 4, sw - dw - 4)
        py = min(click_y + 4, sh - dh - 4)
        self.geometry(f"{dw}x{dh}+{max(0,px)}+{max(0,py)}")

    def _build_ui(self):
        # Navigation
        nav = tk.Frame(self)
        nav.pack(fill=tk.X, padx=6, pady=4)
        tk.Button(nav, text="<<", width=3, relief=tk.RAISED,
                  command=lambda: self._shift_year(-1)).pack(side=tk.LEFT)
        tk.Button(nav, text="<",  width=2, relief=tk.RAISED,
                  command=lambda: self._shift_month(-1)).pack(side=tk.LEFT, padx=2)
        self.lbl_month_year = tk.Label(nav, font=FONT_HEADER, width=18)
        self.lbl_month_year.pack(side=tk.LEFT, expand=True)
        tk.Button(nav, text=">",  width=2, relief=tk.RAISED,
                  command=lambda: self._shift_month(1)).pack(side=tk.RIGHT, padx=2)
        tk.Button(nav, text=">>", width=3, relief=tk.RAISED,
                  command=lambda: self._shift_year(1)).pack(side=tk.RIGHT)

        # Calendar grid
        cal = tk.Frame(self)
        cal.pack(padx=6)
        for c, name in enumerate(self.DAY_NAMES):
            tk.Label(cal, text=name, font=FONT_NORMAL,
                     fg=COLOR_RED if c >= 5 else "black",
                     width=4, anchor=tk.CENTER).grid(row=0, column=c)
        self._day_btns: list[tk.Button] = []
        for r in range(6):
            for c in range(7):
                btn = tk.Button(cal, text="", width=3, relief=tk.FLAT, font=FONT_NORMAL,
                                command=lambda r=r, c=c: self._on_day_click(r, c))
                btn.grid(row=r+1, column=c, padx=1, pady=1)
                self._day_btns.append(btn)

        # Time  - use StringVar to avoid the "09 = invalid octal" bug with IntVar+format
        tf = tk.Frame(self)
        tf.pack(fill=tk.X, padx=6, pady=(6, 2))
        tk.Label(tf, text="Time (HH : MM : SS):", font=FONT_NORMAL).pack(side=tk.LEFT)
        for var, lo, hi in [(self._hour, 0, 23), (self._min, 0, 59), (self._sec, 0, 59)]:
            sb = tk.Spinbox(tf, textvariable=var, from_=lo, to=hi,
                            width=3, font=FONT_MONO,
                            command=self._update_label)
            sb.pack(side=tk.LEFT, padx=1)
            var.trace_add("write", lambda *_: self._update_label())

        # Selected label + buttons
        br = tk.Frame(self)
        br.pack(fill=tk.X, padx=6, pady=(4, 8))
        self.lbl_selected = tk.Label(br, font=FONT_MONO, fg=COLOR_GRAY)
        self.lbl_selected.pack(side=tk.LEFT)
        tk.Button(br, text="Cancel", relief=tk.RAISED, padx=10,
                  command=self.destroy).pack(side=tk.RIGHT)
        tk.Button(br, text="OK", relief=tk.RAISED, padx=14,
                  bg=COLOR_BLUE, fg="white",
                  command=self._on_ok).pack(side=tk.RIGHT, padx=(0, 6))

    def _draw_calendar(self):
        import calendar as _cal
        y, m, d = self._year.get(), self._month.get(), self._day.get()
        self.lbl_month_year.config(text=f"{self.MONTHS[m-1]}  {y}")
        first_wd, n_days = _cal.monthrange(y, m)
        for i, btn in enumerate(self._day_btns):
            day_num = i - first_wd + 1
            col     = i % 7
            if 1 <= day_num <= n_days:
                sel = (day_num == d)
                btn.config(text=str(day_num), state=tk.NORMAL,
                           bg=COLOR_BLUE if sel else "SystemButtonFace",
                           fg="white" if sel else (COLOR_RED if col >= 5 else "black"),
                           relief=tk.SOLID if sel else tk.FLAT)
            else:
                btn.config(text="", state=tk.DISABLED,
                           bg="SystemButtonFace", relief=tk.FLAT)
        self._update_label()

    def _get_time_ints(self) -> tuple[int, int, int]:
        """Parse hour/min/sec StringVars safely (handles leading zeros like '09')."""
        def _s(v: tk.StringVar) -> int:
            try:
                return int(v.get().strip() or "0")
            except ValueError:
                return 0
        return _s(self._hour), _s(self._min), _s(self._sec)

    def _update_label(self):
        try:
            h, m, s = self._get_time_ints()
            dt = datetime(self._year.get(), self._month.get(), self._day.get(), h, m, s)
            self.lbl_selected.config(text=dt.strftime("%Y-%m-%d %H:%M:%S"))
        except ValueError:
            self.lbl_selected.config(text="")

    def _on_day_click(self, row: int, col: int):
        import calendar as _cal
        y, m = self._year.get(), self._month.get()
        first_wd, n_days = _cal.monthrange(y, m)
        day_num = row * 7 + col - first_wd + 1
        if 1 <= day_num <= n_days:
            self._day.set(day_num)
            self._draw_calendar()

    def _shift_month(self, delta: int):
        import calendar as _cal
        y, m = self._year.get(), self._month.get()
        m += delta
        if m < 1:   m = 12; y -= 1
        elif m > 12: m = 1;  y += 1
        self._year.set(y); self._month.set(m)
        _, n = _cal.monthrange(y, m)
        if self._day.get() > n: self._day.set(n)
        self._draw_calendar()

    def _shift_year(self, delta: int):
        import calendar as _cal
        self._year.set(self._year.get() + delta)
        _, n = _cal.monthrange(self._year.get(), self._month.get())
        if self._day.get() > n: self._day.set(n)
        self._draw_calendar()

    def _on_ok(self):
        try:
            h, m, s = self._get_time_ints()
            dt = datetime(self._year.get(), self._month.get(), self._day.get(),
                          max(0, min(23, h)), max(0, min(59, m)), max(0, min(59, s)))
        except ValueError as e:
            messagebox.showerror("Invalid date", str(e), parent=self)
            return
        self._callback(dt)
        self.destroy()


# ---------------------------------------------------------------------------
# PV Browser dialog
# ---------------------------------------------------------------------------

class PVBrowserDialog(tk.Toplevel):
    def __init__(self, parent, on_add_callback, timeout: float = CPVA_HTTP_TIMEOUT,
                 initial_search: str = "", on_close_callback=None,
                 x: int = 0, y: int = 0):
        super().__init__(parent)
        self.title("Browse PVs in archiver")
        self.transient(parent)
        self.grab_set()

        self._on_add    = on_add_callback
        self._on_close  = on_close_callback
        self._timeout   = timeout
        self._initial   = initial_search
        self._all: list[str]      = []
        self._filtered: list[str] = []

        self.protocol("WM_DELETE_WINDOW", self._on_destroy)
        self._build_ui()

        # Position near the trigger widget
        self.update_idletasks()
        w, h = 700, 550
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        px = min(x, sw - w - 4) if x else (sw - w) // 2
        py = min(y, sh - h - 4) if y else (sh - h) // 2
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")

        self._load_async()

    def _build_ui(self):
        top = tk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=(8, 2))

        tk.Label(top, text="Search:", font=FONT_NORMAL).pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar(value=self._initial)
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        entry = tk.Entry(top, textvariable=self.search_var, font=FONT_MONO)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus_set()
        entry.icursor(tk.END)

        self.lbl_count = tk.Label(top, text="loading...", font=FONT_NORMAL, fg="gray", width=18)
        self.lbl_count.pack(side=tk.RIGHT, padx=(8, 0))

        tk.Label(self, text="Tip: use * as wildcard  (e.g. *ENERGY*,  HAPLS*PFM*)",
                 font=("Segoe UI", 8), fg=COLOR_GRAY).pack(anchor=tk.W, padx=8)

        mid = tk.Frame(self)
        mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        sb = tk.Scrollbar(mid)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(mid, font=FONT_MONO, selectmode=tk.EXTENDED,
                                   yscrollcommand=sb.set, activestyle="dotbox")
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda _: self._add_selected())

        bot = tk.Frame(self)
        bot.pack(fill=tk.X, padx=8, pady=(4, 8))
        _btn(bot, "X  Close", self._on_destroy,
             padx=12, pady=5).pack(side=tk.RIGHT)
        _btn(bot, "+  Add selected", self._add_selected,
             bg=COLOR_GREEN, fg="white", padx=12, pady=5).pack(side=tk.RIGHT, padx=(0, 6))

    def _load_async(self):
        def worker():
            try:
                ch = cpva_fetch_channels(timeout=self._timeout)
                self.after(0, lambda: self._on_loaded(ch))
            except Exception as e:
                self.after(0, lambda: self._on_err(e))
        threading.Thread(target=worker, daemon=True).start()

    def _on_loaded(self, channels):
        self._all = sorted(channels)
        self._apply_filter()

    def _on_err(self, exc):
        self.lbl_count.config(text="load error", fg=COLOR_RED)
        messagebox.showerror("Error", f"Failed to load PV list:\n\n{exc}", parent=self)

    def _apply_filter(self):
        q = self.search_var.get().strip()
        self._filtered = [ch for ch in self._all if _matches_wildcard(ch, q)] if q else list(self._all)
        self._refresh_listbox()

    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        MAX = 2000
        for ch in self._filtered[:MAX]:
            self.listbox.insert(tk.END, ch)
        total = len(self._filtered); total_all = len(self._all)
        label = f"{min(MAX,total)}/{total} (of {total_all})" if total > MAX else f"{total} / {total_all}"
        self.lbl_count.config(text=label, fg="gray")

    def _add_selected(self):
        idxs = self.listbox.curselection()
        if idxs:
            self._on_add([self.listbox.get(i) for i in idxs])

    def _add_and_close(self):
        self._add_selected()
        self._on_destroy()

    def _on_destroy(self):
        if self._on_close:
            self._on_close(self.search_var.get())
        self.destroy()


# ---------------------------------------------------------------------------
# matplotlib import helper
# ---------------------------------------------------------------------------

def _try_import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("TkAgg")
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        return Figure, FigureCanvasTkAgg
    except ImportError:
        return None, None


# ---------------------------------------------------------------------------
# Stats shim — makes a tk.Frame behave like a single-color tk.Label for
# simple .config(text=..., fg=...) calls, while also supporting multi-color
# display via set_colored(parts) where parts = [(text, color), ...]
# ---------------------------------------------------------------------------

class _StatsShim:
    def __init__(self, frame: tk.Frame):
        self._frame = frame
        self._labels: list[tk.Label] = []
        self.has_stats = False  # True when real stats are shown (not just hint text)

    def config(self, text: str = "", fg: str = COLOR_GRAY, **_):
        self._clear()
        self.has_stats = False
        if text:
            lbl = tk.Label(self._frame, text=text, font=FONT_NORMAL,
                           fg=fg, anchor=tk.W)
            lbl.pack(side=tk.LEFT)
            self._labels.append(lbl)

    def set_colored(self, parts: list):
        """parts = [(text, color), ...]  — each part gets its own colored label."""
        self._clear()
        self.has_stats = True
        for i, (txt, color) in enumerate(parts):
            if i > 0:
                sep = tk.Label(self._frame, text="  |  ", font=FONT_NORMAL,
                               fg=COLOR_GRAY, anchor=tk.W)
                sep.pack(side=tk.LEFT)
                self._labels.append(sep)
            lbl = tk.Label(self._frame, text=txt, font=FONT_NORMAL,
                           fg=color, anchor=tk.W)
            lbl.pack(side=tk.LEFT)
            self._labels.append(lbl)

    def _clear(self):
        for lbl in self._labels:
            lbl.destroy()
        self._labels = []


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class CPVAExplorerApp:
    def __init__(self, root: tk.Tk):
        self.root   = root
        self.root.title("CPVA Explorer")
        self.root.minsize(1200, 750)
        self.root.state("zoomed")

        self.config = load_config()

        self._samples_by_pv: dict[str, list] = {}
        self._table_rows:    list             = []
        self._pv_order:      list[str]        = []
        self._last_pv_search: str             = ""
        self._col_full_names: dict[str, str]  = {}
        self._presets: list[dict]             = load_presets()

        self._Figure, self._FigureCanvas = _try_import_matplotlib()
        self._mpl_canvas = None
        self._mpl_figure = None
        self._span_selector    = None
        self._zoom_selector    = None
        self._zoom_history: list[tuple] = []   # stack of (xlim, {pv: ylim})
        self._graph_axes       = []
        self._graph_lines: list[list] = []   # outer list per PV, inner list: 1 or 2 Line2D objects
        self._graph_pvs:   list[str]  = []   # PV names corresponding to _graph_lines
        # Raw (times_num, values) per PV for cursor snapping — populated by _plot_graph/_update_graph_data
        self._graph_raw: list[tuple] = []   # list of (times_as_mpl_num_array, values_list)
        # Per-PV settings: keyed by PV name
        # Keys: show, display_name, color, axis, ymin, ymax, auto_scale, width, smooth, grid
        self._pv_settings: dict[str, dict] = {}
        self._pinned_ylim: "tuple | None" = None   # (ymin|None, ymax|None) set by Apply
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._crosshair_cid    = None

        # Live-mode countdown progress bar state
        self._live_countdown_id: str | None = None
        self._live_countdown_elapsed_ms: int = 0

        # Reference lines for graph
        self._ref_lines: list[dict] = []

        # Time state: two datetime objects
        now = datetime.now()
        cfg_from = parse_user_datetime(self.config.get("time_from", ""))
        cfg_to   = parse_user_datetime(self.config.get("time_to",   ""))
        self._dt_from: datetime = cfg_from if cfg_from else now - timedelta(hours=1)
        self._dt_to:   datetime = cfg_to   if cfg_to   else now

        self._build_ui()
        self._populate_ui()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_ui(self):
        main = tk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.sidebar = tk.Frame(main, width=300)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tab_graph = tk.Frame(self.notebook)
        self.tab_table = tk.Frame(self.notebook)
        self.tab_log   = tk.Frame(self.notebook)

        self.notebook.add(self.tab_graph, text="  Graph  ")
        self.notebook.add(self.tab_table, text="  Table  ")
        self.notebook.add(self.tab_log,   text="  Log    ")
        self.notebook.select(self.tab_graph)

        self._build_graph_tab()
        self._build_table_tab()
        self._build_log_tab()

        self.notebook.select(self.tab_graph)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        status = tk.Frame(self.root)
        status.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.lbl_status = tk.Label(status, text="Ready.", font=FONT_NORMAL,
                                    fg=COLOR_GRAY, anchor=tk.W)
        self.lbl_status.pack(fill=tk.X)

    # -- Sidebar --------------------------------------------------------------

    def _build_sidebar(self):
        bar = self.sidebar

        # -- Time Window ------------------------------------------------------
        tk.Label(bar, text="TIME WINDOW", font=FONT_HEADER,
                 fg=COLOR_BLUE, anchor=tk.W).pack(fill=tk.X, pady=(2, 4))

        _btn(bar, "\U0001f4c5 Set time window", self._open_time_window_dialog,
             padx=8, pady=4).pack(fill=tk.X, padx=8, pady=(0, 4))

        ttk.Separator(bar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 4))

        self.lbl_tw_from = tk.Label(bar, text="From: --", font=FONT_NORMAL,
                                     fg=COLOR_GRAY, anchor=tk.W)
        self.lbl_tw_from.pack(fill=tk.X, padx=8, pady=1)
        self.lbl_tw_to = tk.Label(bar, text="To:   --", font=FONT_NORMAL,
                                   fg=COLOR_GRAY, anchor=tk.W)
        self.lbl_tw_to.pack(fill=tk.X, padx=8, pady=1)
        self.lbl_tw_live = tk.Label(bar, text="Live: OFF", font=FONT_NORMAL,
                                     fg=COLOR_GRAY, anchor=tk.W)
        self.lbl_tw_live.pack(fill=tk.X, padx=8, pady=(1, 6))

        # Keep btn_from / btn_to as hidden stubs so _refresh_time_labels still works
        self.btn_from = tk.Button(bar)
        self.btn_to   = tk.Button(bar)

        ttk.Separator(bar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # -- Presets ----------------------------------------------------------
        tk.Label(bar, text="PRESETS", font=FONT_HEADER,
                 fg=COLOR_BLUE, anchor=tk.W).pack(fill=tk.X, pady=(0, 4))

        # Row 1: combobox + Delete button
        preset_sel_row = tk.Frame(bar)
        preset_sel_row.pack(fill=tk.X, padx=8, pady=(0, 2))
        self._preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(preset_sel_row, textvariable=self._preset_var,
                                          state="readonly", font=FONT_NORMAL)
        self.preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        _btn(preset_sel_row, "✕", self._delete_preset,
             bg=COLOR_RED, fg="white", padx=6, pady=2).pack(side=tk.LEFT)

        # Row 2: Load  Save  New
        preset_btn_row = tk.Frame(bar)
        preset_btn_row.pack(fill=tk.X, padx=8, pady=(0, 4))
        _btn(preset_btn_row, "Load", self._load_preset,
             bg=COLOR_BLUE, fg="white", padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))
        _btn(preset_btn_row, "Save", self._save_preset,
             padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))
        _btn(preset_btn_row, "Save as new…", self._save_preset_as,
             padx=8, pady=3).pack(side=tk.LEFT)

        self._refresh_preset_combo()

        ttk.Separator(bar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # -- PV List ----------------------------------------------------------
        tk.Label(bar, text="PV LIST", font=FONT_HEADER,
                 fg=COLOR_BLUE, anchor=tk.W).pack(fill=tk.X, pady=(0, 4))

        lf = tk.Frame(bar)
        lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        sb = tk.Scrollbar(lf)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.pv_listbox = tk.Listbox(lf, font=FONT_MONO, selectmode=tk.EXTENDED,
                                      yscrollcommand=sb.set, height=10)
        self.pv_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self.pv_listbox.yview)
        self.pv_listbox.bind("<Delete>", lambda _: self._remove_selected_pvs())
        self.pv_listbox.bind("<BackSpace>", lambda _: self._remove_selected_pvs())
        self.pv_listbox.bind("<Double-Button-1>", self._on_pv_double_click)

        pv_btns = tk.Frame(bar)
        pv_btns.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.btn_browse = _btn(pv_btns, "Browse...", self._open_pv_browser,
                               bg=COLOR_GREEN, fg="white", padx=8, pady=3)
        self.btn_browse.pack(side=tk.LEFT, padx=(0, 4))
        _btn(pv_btns, "X  Remove", self._remove_selected_pvs,
             padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_clear_pvs = _btn(pv_btns, "Trash", self._clear_pv_list, padx=6, pady=3)
        self.btn_clear_pvs.pack(side=tk.LEFT)

        self.lbl_pv_count = tk.Label(bar, text="0 PV", font=FONT_NORMAL, fg=COLOR_GRAY)
        self.lbl_pv_count.pack(anchor=tk.W, padx=8)

        # -- Merge mode --------------------------------------------------------
        ttk.Separator(bar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        tk.Label(bar, text="MERGE MODE", font=FONT_HEADER,
                fg=COLOR_BLUE, anchor=tk.W).pack(fill=tk.X, pady=(0, 4))

        self._merge_mode_var = tk.StringVar(value="window")

        tk.Radiobutton(
            bar, text="Window merge",
            variable=self._merge_mode_var,
            value="window",
            font=FONT_NORMAL,
            anchor=tk.W
        ).pack(fill=tk.X, padx=8)

        tk.Radiobutton(
            bar, text="Sample & hold",
            variable=self._merge_mode_var,
            value="sample_hold",
            font=FONT_NORMAL,
            anchor=tk.W
        ).pack(fill=tk.X, padx=8)

        # -- Load button ------------------------------------------------------
        self.btn_load = _btn(bar, "LOAD DATA", self._on_load_clicked,
                             bg=COLOR_GREEN, fg="white", padx=12, pady=10,
                             font=FONT_HEADER)
        self.btn_load.pack(fill=tk.X, padx=8, pady=(0, 4))

        # -- Live mode --------------------------------------------------------
        self._live_mode = False
        self._live_after_id = None
        self._live_autoscroll = True   # False when user scrolled away from bottom
        live_row = tk.Frame(bar)
        live_row.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.btn_live = _btn(live_row, "⏵ Live", self._toggle_live_mode,
                             padx=8, pady=4)
        self.btn_live.pack(side=tk.LEFT, padx=(0, 8))

        tk.Label(live_row, text="Refresh interval:", font=FONT_NORMAL).pack(side=tk.LEFT)
        self._live_interval_var = tk.StringVar(value="1")
        vcmd = (bar.register(lambda s: s == "" or (s.replace(".", "", 1).isdigit())), "%P")
        tk.Entry(live_row, textvariable=self._live_interval_var,
                 width=5, font=FONT_MONO, validate="key",
                 validatecommand=vcmd).pack(side=tk.LEFT, padx=(4, 2))
        tk.Label(live_row, text="s", font=FONT_NORMAL).pack(side=tk.LEFT)

        self.progress = None  # progress bar removed

    # -- Graph tab ------------------------------------------------------------

    def _build_graph_tab(self):
        tab = self.tab_graph
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=3)   # graph gets most of the space
        tab.rowconfigure(3, weight=1)   # axis settings panel

        ctrl = tk.Frame(tab)
        ctrl.grid(row=0, column=0, sticky=tk.EW, padx=4, pady=(4, 0))

        # Left side: graph controls
        _btn(ctrl, "Clean graph", self._clean_graph,
             padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))

        _btn(ctrl, "💾 Save graph", self._save_graph,
             padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 6))

        self.btn_zoom_back = _btn(ctrl, "↩ Back", self._zoom_back,
                                   padx=8, pady=3)
        self.btn_zoom_back.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_zoom_back.config(state=tk.DISABLED)

        # Reference lines button
        _btn(ctrl, "≡ Reference lines", self._open_ref_lines_dialog,
             padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(ctrl, text="Font:", font=FONT_NORMAL).pack(side=tk.LEFT, padx=(8, 2))
        self._font_size_var = tk.StringVar(value="11")
        vcmd_fs = (ctrl.register(lambda s: s == "" or s.isdigit()), "%P")
        fs_entry = tk.Entry(ctrl, textvariable=self._font_size_var, width=3, font=FONT_MONO,
                            validate="key", validatecommand=vcmd_fs)
        fs_entry.pack(side=tk.LEFT)
        fs_entry.bind("<Return>", lambda _: self._apply_font_size())
        tk.Label(ctrl, text="pt", font=FONT_NORMAL).pack(side=tk.LEFT, padx=(2, 4))

        self.lbl_graph_info = tk.Label(ctrl, text="", font=FONT_NORMAL, fg=COLOR_GRAY)
        self.lbl_graph_info.pack(side=tk.RIGHT, padx=8)

        self.graph_container = tk.Frame(tab, bg="#f5f5f5")
        self.graph_container.grid(row=1, column=0, sticky=tk.NSEW, padx=4, pady=4)
        self.graph_container.columnconfigure(0, weight=1)
        self.graph_container.rowconfigure(0, weight=1)

        # Stats area below graph — frame holds per-PV colored labels
        self._stats_frame = tk.Frame(tab)
        self._stats_frame.grid(row=2, column=0, sticky=tk.EW, padx=8, pady=(0, 4))
        # Compatibility shim: lbl_graph_stats.config(text=..., fg=...) still works
        self.lbl_graph_stats = _StatsShim(self._stats_frame)

        # Axis settings panel (row 3)
        self._build_axis_settings_panel(tab)

        if self._Figure is None:
            tk.Label(self.graph_container,
                     text="matplotlib not installed.\nRun:  pip install matplotlib",
                     font=("Segoe UI", 12), fg=COLOR_RED, bg="#f5f5f5",
                     justify=tk.CENTER).grid(row=0, column=0)

    def _on_tab_changed(self, event=None):  # noqa: ARG002
        if self.notebook.select() == str(self.tab_graph):
            if self._samples_by_pv:
                self._plot_graph()

    def _reset_pv_ylim(self, pv: str):
        """Reset Y limits for a single PV to auto-scale."""
        s = self._pv_settings.get(pv, {})
        s["ymin"] = None
        s["ymax"] = None
        s["auto_scale"] = True
        self._pv_settings[pv] = s
        self._apply_pv_settings_to_graph()
        self._refresh_axis_settings_tv()

    def _apply_pv_ylim(self, pv: str, ymin_str: str, ymax_str: str):
        """Apply Y limits for a single PV from string values."""
        s = self._pv_settings.get(pv, {})
        try:
            s["ymin"] = float(ymin_str) if ymin_str.strip() else None
        except ValueError:
            s["ymin"] = None
        try:
            s["ymax"] = float(ymax_str) if ymax_str.strip() else None
        except ValueError:
            s["ymax"] = None
        if s["ymin"] is not None or s["ymax"] is not None:
            s["auto_scale"] = False
        self._pv_settings[pv] = s
        self._apply_pv_settings_to_graph()
        self._refresh_axis_settings_tv()

    def _on_zoom_select(self, xmin, xmax):
        """Zoom into the X range selected with right mouse button."""
        if abs(xmax - xmin) < 1e-9 or not self._graph_axes:
            return
        ax0 = self._graph_axes[0]

        # Save current limits to history
        current_xlim = ax0.get_xlim()
        current_ylims = {pv: self._graph_axes[i].get_ylim()
                         for i, pv in enumerate(self._graph_pvs)
                         if i < len(self._graph_axes)}
        self._zoom_history.append((current_xlim, current_ylims))
        if hasattr(self, "btn_zoom_back"):
            self.btn_zoom_back.config(state=tk.NORMAL)

        # Apply new X limits
        ax0.set_xlim(xmin, xmax)

        # Auto-scale Y for each axis within new X range
        import numpy as np
        for ax_idx, (ax, pv) in enumerate(zip(self._graph_axes, self._graph_pvs)):
            s = self._pv_settings.get(pv, {})
            if not s.get("auto_scale", True):
                continue
            raw = self._graph_raw[ax_idx] if ax_idx < len(self._graph_raw) else None
            if raw is None:
                continue
            times_num, values = raw
            mask = (np.asarray(times_num) >= xmin) & (np.asarray(times_num) <= xmax)
            vals_in = np.asarray(values)[mask]
            if len(vals_in) == 0:
                continue
            vmin, vmax = float(vals_in.min()), float(vals_in.max())
            pad = (vmax - vmin) * 0.05 if vmax != vmin else abs(vmax) * 0.05 or 0.1
            ax.set_ylim(vmin - pad, vmax + pad)

        self._blit_bg = None   # invalidate blit cache — axes changed
        self._mpl_canvas.draw_idle()

    def _zoom_back(self):
        """Restore previous zoom level."""
        if not self._zoom_history or not self._graph_axes:
            return
        xlim, ylims = self._zoom_history.pop()
        self._graph_axes[0].set_xlim(xlim)
        for i, pv in enumerate(self._graph_pvs):
            if pv in ylims and i < len(self._graph_axes):
                self._graph_axes[i].set_ylim(ylims[pv])
        if hasattr(self, "btn_zoom_back"):
            self.btn_zoom_back.config(
                state=tk.NORMAL if self._zoom_history else tk.DISABLED)
        self._blit_bg = None   # invalidate blit cache
        self._mpl_canvas.draw_idle()

    def _on_span_select(self, xmin, xmax):
        import matplotlib.dates as mdates
        dt_min = mdates.num2date(xmin, tz=timezone.utc)
        dt_max = mdates.num2date(xmax, tz=timezone.utc)
        colored_parts = []
        for pv in self._pv_order:
            vals = [v for ts, v, _ in self._samples_by_pv.get(pv, [])
                    if isinstance(v, (int, float))
                    and dt_min <= datetime.fromtimestamp(ts / 1e9, tz=timezone.utc) <= dt_max]
            if vals:
                short = shorten_pv_name(pv)
                pv_color = self._pv_settings.get(pv, {}).get("color", COLOR_BLUE)
                txt = (f"{short}: min={min(vals):.4g}  max={max(vals):.4g}"
                    f"  mean={sum(vals)/len(vals):.4g}  n={len(vals)} std={((sum((x - sum(vals)/len(vals))**2 for x in vals) / len(vals))**0.5):.4g}"
                    f"  Stability={((sum((x - sum(vals)/len(vals))**2 for x in vals) / len(vals))**0.5) / (sum(vals)/len(vals)) if sum(vals)/len(vals) != 0 else 0:.2%}")
                colored_parts.append((txt, pv_color))
        if colored_parts:
            self.lbl_graph_stats.set_colored(colored_parts)
        else:
            self.lbl_graph_stats.config(text="No data in selection.", fg=COLOR_GRAY)

    def _plot_graph(self):
        if self._Figure is None:
            messagebox.showerror("matplotlib missing",
                "Install matplotlib:\n\n  pip install matplotlib")
            return
        if not self._samples_by_pv:
            self.lbl_graph_info.config(text="Load data first.", fg=COLOR_GRAY)
            return

        self._clear_graph()

        numeric_pvs = [pv for pv in self._pv_order
                       if any(isinstance(v, (int, float))
                              for _, v, _ in self._samples_by_pv.get(pv, []))]

        if not numeric_pvs:
            self.lbl_graph_info.config(
                text="No numeric PVs to plot.", fg=COLOR_RED)
            return

        fig = self._Figure(figsize=(10, 5), dpi=96)
        n   = len(numeric_pvs)
        colors = ["#1976D2","#e53935","#4CAF50","#FF9800","#9C27B0",
                  "#00BCD4","#FF5722","#607D8B","#795548","#009688"]

        # Get current font size setting to compute axis spacing
        try:
            _fsize = max(5, int(self._font_size_var.get() or "7"))
        except (ValueError, AttributeError):
            _fsize = 7

        # Per-axis width in figure-fraction units.
        # Rotated (90°) tick labels have height ≈ font_pt px; ylabel also rotated ≈ font_pt px.
        # Add tick length (~4px) and padding (~6px).
        # Figure width = 10in × 96dpi = 960px.
        _fig_w_px = 10 * 96
        _axis_px  = _fsize * 2.2 + 10      # tight estimate: rotated ticks + ylabel + padding
        STEP_fig  = max(0.025, _axis_px / _fig_w_px)   # per-axis step in figure coords

        # Count left vs right axes
        left_pvs  = [pv for pv in numeric_pvs
                     if self._pv_settings.get(pv, {}).get("side", "left") != "right"]
        right_pvs = [pv for pv in numeric_pvs
                     if self._pv_settings.get(pv, {}).get("side", "left") == "right"]
        n_left  = max(len(left_pvs),  1)
        n_right = max(len(right_pvs), 0)

        # Subplot margins in figure coords.
        # left_margin must leave room for (n_left-1) extra left axes + a small base margin.
        BASE_L = 0.03
        BASE_R = 0.01
        left_margin  = BASE_L + (n_left  - 1) * STEP_fig
        right_margin = 1.0    - BASE_R - n_right * STEP_fig
        fig.subplots_adjust(left=left_margin, right=right_margin, top=0.95, bottom=0.12)

        # Axes fraction step = figure step / axes-width-in-figure-coords
        axes_width = right_margin - left_margin
        STEP_ax = STEP_fig / axes_width   # step in axes fraction

        axes = [fig.add_subplot(111)]
        for i in range(1, n):
            ax = axes[0].twinx()
            axes.append(ax)

        # Position each axis spine; record spine X in axes fraction for cursor labels
        left_idx  = 0
        right_idx = 0
        self._graph_spine_xpos = []   # (xpos_axes_frac, side) per axis
        for i, (pv, ax) in enumerate(zip(numeric_pvs, axes)):
            side = self._pv_settings.get(pv, {}).get("side", "left")
            if i == 0:
                ax.yaxis.set_label_position("left")
                ax.yaxis.tick_left()
                self._graph_spine_xpos.append((0.0, "left"))
                left_idx = 1
            elif side == "right":
                ax.yaxis.set_label_position("right")
                ax.yaxis.tick_right()
                xfrac = 1.0 + right_idx * STEP_ax
                if right_idx > 0:
                    ax.spines["right"].set_position(("axes", xfrac))
                self._graph_spine_xpos.append((xfrac, "right"))
                right_idx += 1
            else:
                ax.yaxis.set_label_position("left")
                ax.yaxis.tick_left()
                xfrac = -left_idx * STEP_ax
                ax.spines["left"].set_position(("axes", xfrac))
                self._graph_spine_xpos.append((xfrac, "left"))
                left_idx += 1

        import matplotlib.dates as mdates

        def _moving_avg(vals, w):
            if w <= 1 or len(vals) < w:
                return vals
            padded = vals[:w-1][::-1] + vals
            return [sum(padded[j:j+w]) / w for j in range(len(vals))]

        all_times = []
        self._graph_lines = []
        self._graph_pvs   = list(numeric_pvs)
        self._graph_raw   = []
        for i, (pv, ax) in enumerate(zip(numeric_pvs, axes)):
            # Use UTC-aware datetimes so matplotlib epoch math is always correct
            times  = [datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                      for ts, v, _ in self._samples_by_pv[pv] if isinstance(v, (int, float))]
            values = [v for _, v, _ in self._samples_by_pv[pv] if isinstance(v, (int, float))]
            all_times.extend(times)
            color  = colors[i % len(colors)]
            short  = shorten_pv_name(pv)

            pv_setting = self._get_pv_default_settings(pv, i)
            line_color  = pv_setting.get("color", color)
            line_width  = pv_setting.get("width", None)
            pv_smooth   = pv_setting.get("smooth", None)
            disp_name   = pv_setting.get("display_name", short)
            visible     = pv_setting.get("show", True)
            eff_smooth  = pv_smooth if pv_smooth is not None else 1

            import matplotlib.dates as _mdates_inner
            times_num = _mdates_inner.date2num(times)
            self._graph_raw.append((times_num, values))

            if eff_smooth > 1 and len(values) >= eff_smooth:
                # Raw data: thin + transparent, step style
                lw_raw = (line_width * 0.5) if line_width is not None else 0.8
                raw_line, = ax.plot(times, values, color=line_color,
                                    linewidth=lw_raw, alpha=0.3, marker=None,
                                    drawstyle="steps-post", zorder=1)
                raw_line.set_visible(visible)
                # Smoothed trend: solid, step style
                smoothed = _moving_avg(values, eff_smooth)
                lw_sm = line_width if line_width is not None else 1.8
                sm_line, = ax.plot(times, smoothed, color=line_color,
                                   linewidth=lw_sm, marker=None,
                                   drawstyle="steps-post",
                                   label=f"{disp_name} (avg {eff_smooth})", zorder=2)
                sm_line.set_visible(visible)
                self._graph_lines.append([raw_line, sm_line])
            else:
                lw = line_width if line_width is not None else 1.2
                line, = ax.plot(times, values, color=line_color, linewidth=lw,
                                marker="." if len(times) < 200 else None, markersize=3,
                                drawstyle="steps-post",
                                label=disp_name, zorder=2)
                line.set_visible(visible)
                self._graph_lines.append([line])

            ax.set_ylabel(disp_name, color=line_color, fontsize=_fsize,
                          rotation=90, labelpad=2)
            ax.tick_params(axis="y", labelcolor=line_color, labelsize=_fsize,
                           pad=2, labelrotation=90)
            from matplotlib.ticker import AutoMinorLocator as _AutoMinorLocator
            ax.yaxis.set_minor_locator(_AutoMinorLocator(5))
            ax.tick_params(axis="y", which="minor", length=3, labelsize=0)
            ax.grid(pv_setting.get("grid", i == 0))

            # Apply per-PV Y axis limits if set
            ymin_pv = pv_setting.get("ymin")
            ymax_pv = pv_setting.get("ymax")
            auto_scale = pv_setting.get("auto_scale", True)
            if not auto_scale and (ymin_pv is not None or ymax_pv is not None):
                ax.set_ylim(ymin_pv, ymax_pv)

        # Smart x-axis formatter
        ax0 = axes[0]
        if all_times and len(all_times) > 1:
            t_min, t_max = min(all_times), max(all_times)
            total_seconds = (t_max - t_min).total_seconds()
        else:
            t_min = t_max = (all_times[0] if all_times else
                             datetime.now(tz=timezone.utc))
            total_seconds = 0

        from matplotlib.ticker import FuncFormatter, AutoMinorLocator

        # Determine date range
        t_min_local = t_min.astimezone(TZ_PRAGUE) if total_seconds > 0 else datetime.now(TZ_PRAGUE)
        t_max_local = t_max.astimezone(TZ_PRAGUE) if total_seconds > 0 else t_min_local
        same_day = t_min_local.date() == t_max_local.date()

        # Custom locator: always tick at t_min and t_max, plus ~5 evenly-spaced
        # "round" ticks in between (whole seconds / minutes / hours / days).
        from matplotlib.ticker import FixedLocator

        def _make_x_ticks(t_lo: datetime, t_hi: datetime) -> list:
            span_s = (t_hi - t_lo).total_seconds()
            # Pick a step that fits ~5–7 intermediate ticks
            _STEPS_S = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                        3600, 7200, 10800, 21600, 43200, 86400, 172800]
            step_s = _STEPS_S[-1]
            for s in _STEPS_S:
                if span_s / s <= 8:
                    step_s = s
                    break

            # First round tick >= t_lo + step/4  (leave room for the edge label)
            import math
            epoch = datetime(t_lo.year, t_lo.month, t_lo.day, tzinfo=t_lo.tzinfo)
            offset_s = (t_lo - epoch).total_seconds()
            first_step_s = math.ceil((offset_s + step_s * 0.25) / step_s) * step_s
            ticks_dt = []
            cur_s = first_step_s
            while True:
                dt_tick = epoch + timedelta(seconds=cur_s)
                if dt_tick >= t_hi - timedelta(seconds=step_s * 0.25):
                    break
                ticks_dt.append(dt_tick)
                cur_s += step_s

            # Always include start and end
            all_ticks_num = (
                [mdates.date2num(t_lo)]
                + [mdates.date2num(d) for d in ticks_dt]
                + [mdates.date2num(t_hi)]
            )
            return all_ticks_num

        _ticks_num = _make_x_ticks(t_min.astimezone(TZ_PRAGUE),
                                    t_max.astimezone(TZ_PRAGUE)) if total_seconds > 0 else []
        if _ticks_num:
            ax0.xaxis.set_major_locator(FixedLocator(_ticks_num))
        else:
            ax0.xaxis.set_major_locator(mdates.AutoDateLocator(tz=TZ_PRAGUE, minticks=5, maxticks=8))

        def _fmt_x(x, _):
            try:
                dt = mdates.num2date(x, tz=TZ_PRAGUE)
            except Exception:
                return ""
            if same_day:
                return dt.strftime("%H:%M:%S")
            else:
                if dt.hour == 0 and dt.minute == 0:
                    return dt.strftime("%m-%d\n00:00")
                return dt.strftime("%H:%M:%S")

        ax0.xaxis.set_major_formatter(FuncFormatter(_fmt_x))
        ax0.xaxis.set_minor_locator(AutoMinorLocator(5))

        if same_day:
            ax0.set_xlabel(f"Time (Prague)  {t_min_local.strftime('%Y-%m-%d')}", fontsize=_fsize)
        else:
            ax0.set_xlabel("Time (Prague)", fontsize=_fsize)

        if total_seconds > 0:
            pad = timedelta(seconds=max(total_seconds * 0.02, 5))
            ax0.set_xlim(t_min - pad, t_max + pad)
        ax0.tick_params(axis="x", which="major", labelsize=_fsize, rotation=0)
        ax0.tick_params(axis="x", which="minor", length=3, labelsize=0)

        # Draw reference lines
        ax0_ref = axes[0] if axes else None
        for rl in self._ref_lines:
            lbl = rl.get("label", "") or f"y={rl['y']}"
            artist = ax0_ref.axhline(
                y=rl["y"], color=rl["color"],
                linewidth=1.2, linestyle="--", label=lbl)
            rl["artist"] = artist

        # No legend — PV names are shown on the Y-axis labels in their respective colors

        canvas = self._FigureCanvas(fig, master=self.graph_container)
        canvas.draw()
        canvas.get_tk_widget().grid(row=0, column=0, sticky=tk.NSEW)

        self._mpl_canvas = canvas
        self._mpl_figure = fig
        self._graph_axes = axes

        # Crosshair lines — one pair per axis
        # Text annotations are pre-allocated and reused (set_text/set_position)
        # to avoid the cost of ax.text() + remove() on every mouse move.
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._x_cursor_ann     = None
        from matplotlib.transforms import blended_transform_factory as _btf
        try:
            _ann_fsize = max(5, int(self._font_size_var.get() or "7"))
        except Exception:
            _ann_fsize = 7
        for ax_i, ax in enumerate(axes):
            vl = ax.axvline(color="#888888", linewidth=0.8, linestyle="--", visible=False)
            hl = ax.axhline(color="#888888", linewidth=0.8, linestyle="--", visible=False)
            self._crosshair_vlines.append(vl)
            self._crosshair_hlines.append(hl)

            pv       = numeric_pvs[ax_i] if ax_i < len(numeric_pvs) else None
            pv_color = self._pv_settings.get(pv, {}).get("color", "#555") if pv else "#555"
            spine_xf = self._graph_spine_xpos[ax_i][0] if ax_i < len(self._graph_spine_xpos) else 0.0
            side_s   = self._graph_spine_xpos[ax_i][1] if ax_i < len(self._graph_spine_xpos) else "left"
            ha_s     = "left" if side_s == "right" else "right"
            blend    = _btf(ax.transAxes, ax.transData)
            ann = ax.text(
                spine_xf, 0, "",
                ha=ha_s, va="center", fontsize=_ann_fsize,
                color=pv_color, zorder=10, visible=False,
                transform=blend, clip_on=False,
                bbox=dict(boxstyle="round,pad=0.15", fc="white",
                          ec=pv_color, alpha=0.85, linewidth=0.6),
            )
            self._crosshair_texts.append(ann)

        # Pre-allocate X-axis timestamp annotation
        ax0_ann = axes[0]
        self._x_cursor_ann = ax0_ann.text(
            0, -0.01, "",
            ha="center", va="top", fontsize=_ann_fsize,
            color="#333333", zorder=10, visible=False,
            transform=ax0_ann.get_xaxis_transform(), clip_on=False,
            bbox=dict(boxstyle="round,pad=0.15", fc="white",
                      ec="#888888", alpha=0.9, linewidth=0.6),
        )

        # Pre-compute numpy arrays for fast snap lookup
        import numpy as np
        self._graph_raw_np = [
            (np.asarray(t, dtype=float), np.asarray(v, dtype=float))
            for t, v in self._graph_raw
        ]

        self._mouse_pending   = False
        self._blit_bg         = None   # cached background bitmap for blit
        # Capture background after first full draw (axes ticks etc. must be rendered)
        canvas.mpl_connect("draw_event", self._on_canvas_draw)
        self._crosshair_cid = canvas.mpl_connect("motion_notify_event",
                                                  self._on_graph_mouse_move)

        # Span selector for interactive stats (left button)
        try:
            from matplotlib.widgets import SpanSelector
            self._span_selector = SpanSelector(
                axes[0], self._on_span_select,
                direction="horizontal",
                useblit=False,
                props=dict(alpha=0.15, facecolor=COLOR_BLUE),
                interactive=True,
            )
        except Exception:
            self._span_selector = None

        # Zoom selector (right mouse button)
        try:
            self._zoom_selector = SpanSelector(
                axes[0], self._on_zoom_select,
                direction="horizontal",
                useblit=False,
                button=3,
                props=dict(alpha=0.20, facecolor="#FF6600"),
            )
        except Exception:
            self._zoom_selector = None

        # Reset zoom history on fresh plot
        self._zoom_history = []
        if hasattr(self, "btn_zoom_back"):
            self.btn_zoom_back.config(state=tk.DISABLED)

        self.lbl_graph_info.config(text=f"Showing {n} numeric PV(s).", fg=COLOR_GREEN)
        if not self.lbl_graph_stats.has_stats:
            self.lbl_graph_stats.config(text="Drag on the graph to select a region for stats.", fg=COLOR_GRAY)

        # Refresh axis settings panel
        self._refresh_axis_settings_tv()

    def _clean_graph(self):
        """Remove PV data from the graph (hide all lines) but keep the graph frame."""
        if self._mpl_canvas is None:
            return
        for pv_lines in self._graph_lines:
            for line in pv_lines:
                line.set_visible(False)
        self._graph_lines = []
        self._graph_pvs   = []
        self._graph_raw   = []
        self._samples_by_pv = {}
        self._pv_order      = []
        self._zoom_history = []
        if hasattr(self, "btn_zoom_back"):
            self.btn_zoom_back.config(state=tk.DISABLED)
        self.lbl_graph_stats.config(text="", fg=COLOR_GRAY)
        self.lbl_graph_info.config(text="Graph cleaned.", fg=COLOR_GRAY)
        self._mpl_canvas.draw_idle()
        self._refresh_axis_settings_tv()

    def _save_graph(self):
        """Save the current graph to a file (PNG, PDF, SVG)."""
        if self._mpl_figure is None:
            messagebox.showinfo("No graph", "Load data and plot a graph first.")
            return
        from tkinter import filedialog
        default = f"cpva_graph_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = filedialog.asksaveasfilename(
            title="Save graph",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("PDF", "*.pdf"),
                       ("SVG vector", "*.svg"), ("All files", "*.*")],
            initialfile=default,
        )
        if not path:
            return
        try:
            self._mpl_figure.savefig(path, dpi=150, bbox_inches="tight")
            self.lbl_status.config(text=f"Graph saved: {path}", fg=COLOR_GREEN)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _clear_graph(self):
        self._span_selector = None
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._graph_axes       = []
        if hasattr(self, "_crosshair_cid") and self._mpl_canvas:
            try:
                self._mpl_canvas.mpl_disconnect(self._crosshair_cid)
            except Exception:
                pass
        if self._mpl_canvas:
            self._mpl_canvas.get_tk_widget().destroy()
            self._mpl_canvas = None
        if self._mpl_figure:
            try:
                import matplotlib.pyplot as plt
                plt.close(self._mpl_figure)
            except Exception:
                pass
            self._mpl_figure = None
        for w in self.graph_container.winfo_children():
            w.destroy()
        self.lbl_graph_stats.config(text="", fg=COLOR_GRAY)
        self._graph_lines = []
        self._graph_pvs   = []
        self._graph_raw   = []

    def _update_graph_data(self):
        """Update existing line data in-place to avoid redraw flicker during live mode."""
        if self._mpl_canvas is None or not self._graph_lines:
            return

        import matplotlib.dates as mdates

        def _moving_avg(vals, w):
            if w <= 1 or len(vals) < w:
                return vals
            padded = vals[:w-1][::-1] + vals
            return [sum(padded[j:j+w]) / w for j in range(len(vals))]

        all_times = []
        new_graph_raw = []
        for i, (pv, ax, pv_lines) in enumerate(zip(
                self._graph_pvs, self._graph_axes, self._graph_lines)):
            samples = self._samples_by_pv.get(pv, [])
            times  = [datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                      for ts, v, _ in samples if isinstance(v, (int, float))]
            values = [v for _, v, _ in samples if isinstance(v, (int, float))]

            if not times:
                new_graph_raw.append(self._graph_raw[i] if i < len(self._graph_raw) else ([], []))
                continue

            all_times.extend(times)

            times_num = mdates.date2num(times)
            new_graph_raw.append((times_num, values))

            pv_setting = self._pv_settings.get(pv, {})
            pv_smooth  = pv_setting.get("smooth", None)
            eff_smooth = pv_smooth if pv_smooth is not None else 1

            if len(pv_lines) == 2:
                # raw + smoothed
                pv_lines[0].set_xdata(times)
                pv_lines[0].set_ydata(values)
                smoothed = _moving_avg(values, eff_smooth) if eff_smooth > 1 and len(values) >= eff_smooth else values
                pv_lines[1].set_xdata(times)
                pv_lines[1].set_ydata(smoothed)
            elif pv_lines:
                pv_lines[0].set_xdata(times)
                pv_lines[0].set_ydata(values)

            # Rescale x axis
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)

            # Rescale y only if per-PV auto-scale is active
            pv_auto = pv_setting.get("auto_scale", True)
            if pv_auto:
                ax.relim()
                ax.autoscale_view(scalex=True, scaley=True)

        self._graph_raw = new_graph_raw

        # Update x-axis range and formatter based on new time span
        if all_times and len(all_times) > 1:
            t_min, t_max = min(all_times), max(all_times)
            total_seconds = (t_max - t_min).total_seconds()
        else:
            t_min = t_max = (all_times[0] if all_times else datetime.now(tz=timezone.utc))
            total_seconds = 0

        if self._graph_axes:
            ax0 = self._graph_axes[0]
            from matplotlib.ticker import FuncFormatter as _FuncFormatter
            t_min_local = t_min.astimezone(TZ_PRAGUE) if total_seconds > 0 else datetime.now(TZ_PRAGUE)
            t_max_local = t_max.astimezone(TZ_PRAGUE) if total_seconds > 0 else t_min_local
            same_day = t_min_local.date() == t_max_local.date()

            def _fmt_x_live(x, _pos):
                try:
                    dt = mdates.num2date(x, tz=TZ_PRAGUE)
                except Exception:
                    return ""
                if same_day:
                    return dt.strftime("%H:%M:%S")
                else:
                    if dt.hour == 0 and dt.minute == 0:
                        return dt.strftime("%m-%d\n00:00")
                    return dt.strftime("%H:%M")

            ax0.xaxis.set_major_formatter(_FuncFormatter(_fmt_x_live))
            # Always update x limits in live mode so timeline scrolls forward
            if total_seconds > 0:
                pad = timedelta(seconds=max(total_seconds * 0.02, 5))
                ax0.set_xlim(t_min - pad, t_max + pad)

        self._mpl_canvas.draw_idle()

    def _on_canvas_draw(self, *_):
        """Cache the background bitmap after every full redraw — used for blit."""
        if self._mpl_canvas is None:
            return
        # Hide crosshair artists so they are NOT part of the cached background
        for vl in self._crosshair_vlines:
            vl.set_visible(False)
        for hl in self._crosshair_hlines:
            hl.set_visible(False)
        for ann in self._crosshair_texts:
            if ann is not None:
                ann.set_visible(False)
        if self._x_cursor_ann is not None:
            self._x_cursor_ann.set_visible(False)
        self._blit_bg = self._mpl_canvas.copy_from_bbox(self._mpl_figure.bbox)

    def _on_graph_mouse_move(self, event):
        if not self._mpl_canvas or not self._crosshair_vlines:
            return
        # Throttle: always store last event; only schedule one redraw per 16ms (~60fps)
        self._mouse_last_event = event
        if not getattr(self, "_mouse_pending", False):
            self._mouse_pending = True
            self._mpl_canvas.get_tk_widget().after(16, self._process_mouse_move)

    def _process_mouse_move(self):
        self._mouse_pending = False
        event = getattr(self, "_mouse_last_event", None)
        if event is None or self._mpl_canvas is None:
            return

        import matplotlib.dates as mdates
        import numpy as np

        canvas = self._mpl_canvas
        bg     = getattr(self, "_blit_bg", None)

        # Outside axes — restore background and hide all crosshair artists
        if event.inaxes is None:
            if bg is not None:
                canvas.restore_region(bg)
                canvas.blit(self._mpl_figure.bbox)
            else:
                canvas.draw_idle()
            return

        x = event.xdata
        if x is None:
            return

        x_f    = float(x)
        disp_x = event.x
        disp_y = event.y
        raw_np = getattr(self, "_graph_raw_np", None)

        # Restore clean background before drawing crosshair
        if bg is not None:
            canvas.restore_region(bg)

        # Vertical crosshair
        for vl in self._crosshair_vlines:
            vl.set_xdata([x_f, x_f])
            vl.set_visible(True)
            if bg is not None:
                vl.axes.draw_artist(vl)

        # Per-axis: H line + annotation
        for ax_idx, (ax, hl) in enumerate(zip(self._graph_axes, self._crosshair_hlines)):
            pv = self._graph_pvs[ax_idx] if ax_idx < len(self._graph_pvs) else None

            try:
                y_mouse = float(ax.transData.inverted().transform((disp_x, disp_y))[1])
            except Exception:
                hl.set_visible(False)
                continue

            hl.set_ydata([y_mouse, y_mouse])
            hl.set_visible(True)
            if bg is not None:
                ax.draw_artist(hl)

            # Snap cursor value (for treeview)
            snap_val = None
            if raw_np is not None and ax_idx < len(raw_np):
                arr, vals = raw_np[ax_idx]
                if len(arr):
                    idx = int(np.argmin(np.abs(arr - x_f)))
                    snap_val = float(vals[idx])
            if pv and pv in self._pv_settings:
                self._pv_settings[pv]["cursor_val"] = f"{snap_val:.6g}" if snap_val is not None else ""

            # Y annotation
            if ax_idx < len(self._crosshair_texts):
                ann = self._crosshair_texts[ax_idx]
                if ann is not None:
                    spine_info = (self._graph_spine_xpos[ax_idx]
                                  if hasattr(self, "_graph_spine_xpos")
                                  and ax_idx < len(self._graph_spine_xpos)
                                  else (0.0, "left"))
                    xfrac, side = spine_info
                    txt = f" {y_mouse:.4g}" if side == "right" else f"{y_mouse:.4g} "
                    ann.set_position((xfrac, y_mouse))
                    ann.set_text(txt)
                    ann.set_visible(True)
                    if bg is not None:
                        ax.draw_artist(ann)

        # X timestamp annotation
        if self._x_cursor_ann is not None:
            try:
                ts_str = mdates.num2date(x_f, tz=TZ_PRAGUE).strftime("%H:%M:%S")
                self._x_cursor_ann.set_position((x_f, -0.01))
                self._x_cursor_ann.set_text(ts_str)
                self._x_cursor_ann.set_visible(True)
                if bg is not None:
                    self._graph_axes[0].draw_artist(self._x_cursor_ann)
                self.lbl_graph_info.config(
                    text=mdates.num2date(x_f, tz=TZ_PRAGUE).strftime("%Y-%m-%d %H:%M:%S"),
                    fg=COLOR_GRAY)
            except Exception:
                pass

        # Blit updated region to screen — much faster than draw_idle()
        if bg is not None:
            canvas.blit(self._mpl_figure.bbox)
        else:
            canvas.draw_idle()

        # Update treeview (low priority — after blit so screen updates first)
        if hasattr(self, "_axis_tv"):
            tv      = self._axis_tv
            val_idx = self._axis_tv_cols.index("cursor_val")
            for pv in self._graph_pvs:
                if tv.exists(pv):
                    row = list(tv.item(pv, "values"))
                    row[val_idx] = self._pv_settings.get(pv, {}).get("cursor_val", "")
                    tv.item(pv, values=row)

    # -- Axis settings panel --------------------------------------------------

    def _build_axis_settings_panel(self, parent):
        """Build the always-visible 'Axis settings' treeview panel at the bottom of the graph tab."""
        # Also update parent rowconfigure for row 4 (treeview)
        parent.rowconfigure(4, weight=1)

        hdr = tk.Frame(parent)
        hdr.grid(row=3, column=0, sticky=tk.EW, padx=4, pady=(2, 0))
        tk.Label(hdr, text="Axis settings", font=FONT_HEADER,
                 fg=COLOR_BLUE, anchor=tk.W).pack(side=tk.LEFT)

        self._axis_panel_visible = tk.BooleanVar(value=True)

        self._axis_settings_frame = tk.Frame(parent)
        self._axis_settings_frame.grid(row=4, column=0, sticky=tk.NSEW, padx=4, pady=(0, 4))

        # Treeview columns — tree column (leftmost) carries the color image
        _COLS = ("show", "pv", "display_name", "color", "cursor_val",
                 "side", "ymin", "ymax", "auto_scale", "width", "smooth", "grid")
        _HEADS = {
            "show":         "☑ Show",
            "pv":           "PV",
            "display_name": "Display Name",
            "color":        "Color",
            "cursor_val":   "Cursor Value",
            "side":         "Side",
            "ymin":         "Y min",
            "ymax":         "Y max",
            "auto_scale":   "☑ Auto",
            "width":        "Width",
            "smooth":       "Smooth",
            "grid":         "☑ Grid",
        }
        _COL_WIDTHS = {
            "show": 45, "pv": 220, "display_name": 120, "color": 45,
            "cursor_val": 100, "side": 50,
            "ymin": 60, "ymax": 60,
            "auto_scale": 50, "width": 50, "smooth": 55, "grid": 45,
        }

        sf = self._axis_settings_frame
        sf.columnconfigure(0, weight=1)
        sf.rowconfigure(0, weight=1)

        inner = tk.Frame(sf)
        inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)

        tv_sb_y = ttk.Scrollbar(inner, orient=tk.VERTICAL)
        tv_sb_x = ttk.Scrollbar(inner, orient=tk.HORIZONTAL)
        self._axis_tv = ttk.Treeview(inner, columns=_COLS, show="headings",
                                      height=5,
                                      yscrollcommand=tv_sb_y.set,
                                      xscrollcommand=tv_sb_x.set)
        tv_sb_y.config(command=self._axis_tv.yview)
        tv_sb_x.config(command=self._axis_tv.xview)
        self._axis_tv.grid(row=0, column=0, sticky=tk.NSEW)
        tv_sb_y.grid(row=0, column=1, sticky=tk.NS)
        tv_sb_x.grid(row=1, column=0, sticky=tk.EW)

        for col in _COLS:
            self._axis_tv.heading(col, text=_HEADS[col])
            self._axis_tv.column(col, width=_COL_WIDTHS[col], minwidth=30, stretch=False)

        self._axis_tv.bind("<Double-Button-1>", self._on_axis_tv_double_click)
        self._axis_tv.bind("<Button-1>",        self._on_axis_tv_single_click)
        self._axis_tv.bind("<Configure>",        lambda _: self._reposition_tv_swatches())
        self._axis_tv.bind("<<TreeviewSelect>>", lambda _: self._reposition_tv_swatches())

        style = ttk.Style(self._axis_tv)
        self._axis_tv.configure(style="AxisTV.Treeview")
        style.configure("AxisTV.Treeview", rowheight=22)
        style.map("AxisTV.Treeview",
                  background=[("selected", "#ddeeff")],
                  foreground=[("selected", "black")])

        # Canvas swatch overlays — stored to allow cleanup on refresh
        self._axis_tv_swatches: list[tk.Canvas] = []

        self._axis_tv_cols = _COLS

    def _open_ref_lines_dialog(self):
        """Open the reference lines manager as a non-modal floating window."""
        if hasattr(self, "_ref_dlg") and self._ref_dlg and self._ref_dlg.winfo_exists():
            self._ref_dlg.lift()
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Reference lines")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._ref_dlg = dlg

        # Input row
        inp = tk.Frame(dlg)
        inp.pack(fill=tk.X, padx=8, pady=(8, 2))

        tk.Label(inp, text="Y:", font=FONT_NORMAL).pack(side=tk.LEFT)
        self._ref_y_var = tk.StringVar()
        tk.Entry(inp, textvariable=self._ref_y_var, width=8, font=FONT_MONO).pack(side=tk.LEFT, padx=2)

        self._ref_color_var = tk.StringVar(value="#ff6600")
        self._ref_color_btn = tk.Button(inp, text="  Color  ", font=FONT_NORMAL,
                                         bg="#ff6600", relief=tk.RAISED,
                                         command=self._pick_ref_line_color)
        self._ref_color_btn.pack(side=tk.LEFT, padx=2)

        tk.Label(inp, text="Label:", font=FONT_NORMAL).pack(side=tk.LEFT, padx=(4, 0))
        self._ref_label_var = tk.StringVar()
        tk.Entry(inp, textvariable=self._ref_label_var, width=12, font=FONT_MONO).pack(side=tk.LEFT, padx=2)

        _btn(inp, "+ Add", self._add_ref_line, bg=COLOR_GREEN, fg="white",
             padx=6, pady=2).pack(side=tk.LEFT, padx=(4, 0))

        # Listbox + Remove
        lst_frm = tk.Frame(dlg)
        lst_frm.pack(fill=tk.X, padx=8, pady=(2, 8))
        self._ref_listbox = tk.Listbox(lst_frm, font=FONT_MONO, height=6,
                                        selectmode=tk.SINGLE, width=40)
        self._ref_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _btn(lst_frm, "Remove", self._remove_ref_line, padx=6, pady=2).pack(side=tk.LEFT, padx=(4, 0))

        # Repopulate listbox with existing ref lines
        for rl in self._ref_lines:
            lbl = rl.get("label", "") or f"y={rl['y']}"
            self._ref_listbox.insert(tk.END, f"y={rl['y']}  {lbl}  {rl['color']}")

        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry(f"+{(sw-dw)//2}+{(sh-dh)//2}")

    def _pick_ref_line_color(self):
        from tkinter.colorchooser import askcolor
        current = self._ref_color_var.get()
        result = askcolor(color=current, title="Reference line color")
        if result and result[1]:
            self._ref_color_var.set(result[1])
            self._ref_color_btn.config(bg=result[1])

    def _add_ref_line(self):
        try:
            y_val = float(self._ref_y_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid Y", "Enter a numeric Y value.")
            return
        color = self._ref_color_var.get() or "#ff6600"
        label = self._ref_label_var.get().strip()
        self._ref_lines.append({"y": y_val, "color": color, "label": label})
        self._ref_listbox.insert(tk.END, f"y={y_val}  {label}  {color}")
        # Redraw if graph is active
        if self._mpl_canvas is not None:
            self._plot_graph()

    def _remove_ref_line(self):
        sel = self._ref_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self._ref_listbox.delete(idx)
        self._ref_lines.pop(idx)
        if self._mpl_canvas is not None:
            self._plot_graph()

    def _toggle_axis_settings_panel(self):
        self._refresh_axis_settings_tv()

    def _get_pv_default_settings(self, pv: str, idx: int) -> dict:
        """Return the settings dict for a PV, initialising defaults if missing."""
        colors = ["#1976D2", "#e53935", "#4CAF50", "#FF9800", "#9C27B0",
                  "#00BCD4", "#FF5722", "#607D8B", "#795548", "#009688"]
        s = self._pv_settings.setdefault(pv, {})
        s.setdefault("show",         True)
        s.setdefault("display_name", shorten_pv_name(pv))
        s.setdefault("color",        colors[idx % len(colors)])
        s.setdefault("cursor_val",   "")
        s.setdefault("cursor_ts",    "")
        s.setdefault("axis",         f"Value {idx + 1}")
        s.setdefault("side",         "left")
        s.setdefault("ymin",         None)
        s.setdefault("ymax",         None)
        s.setdefault("auto_scale",   True)
        s.setdefault("width",        None)
        s.setdefault("smooth",       None)
        s.setdefault("grid",         idx == 0)  # grid only on first PV by default
        return s

    def _refresh_axis_settings_tv(self):
        """Repopulate the axis settings treeview from _pv_settings."""
        if not hasattr(self, "_axis_tv"):
            return
        tv = self._axis_tv

        # Destroy old swatch canvases
        for c in getattr(self, "_axis_tv_swatches", []):
            try:
                c.destroy()
            except Exception:
                pass
        self._axis_tv_swatches = []
        # Store pv→color for swatch reposition
        self._axis_tv_swatch_colors: dict[str, str] = {}

        for item in tv.get_children():
            tv.delete(item)

        numeric_pvs = [pv for pv in self._pv_order
                       if any(isinstance(v, (int, float))
                              for _, v, _ in self._samples_by_pv.get(pv, []))]

        for idx, pv in enumerate(numeric_pvs):
            s = self._get_pv_default_settings(pv, idx)
            hex_color = s["color"]
            self._axis_tv_swatch_colors[pv] = hex_color

            row = (
                "☑" if s["show"]       else "☐",
                pv,
                s["display_name"],
                "",                                # color column — covered by Canvas swatch
                s.get("cursor_val", ""),
                s.get("side", "left"),
                "" if s["ymin"]       is None else str(s["ymin"]),
                "" if s["ymax"]       is None else str(s["ymax"]),
                "☑" if s["auto_scale"] else "☐",
                "" if s["width"]      is None else str(s["width"]),
                "" if s["smooth"]     is None else str(s["smooth"]),
                "☑" if s["grid"]    else "☐",
            )
            tv.insert("", tk.END, iid=pv, values=row)

        # Draw Canvas swatches over the "color" column cells
        tv.update_idletasks()
        self._reposition_tv_swatches()

    def _reposition_tv_swatches(self):
        """Place/reposition Canvas color swatches over the 'color' column cells."""
        tv = getattr(self, "_axis_tv", None)
        if tv is None or not tv.winfo_exists():
            return
        swatch_colors = getattr(self, "_axis_tv_swatch_colors", {})
        swatches      = getattr(self, "_axis_tv_swatches", [])

        # Destroy old swatches and rebuild (simplest for scroll correctness)
        for c in swatches:
            try:
                c.destroy()
            except Exception:
                pass
        self._axis_tv_swatches = []

        col_id = f"#{list(self._axis_tv_cols).index('color') + 1}"
        for pv, hex_color in swatch_colors.items():
            if not tv.exists(pv):
                continue
            bbox = tv.bbox(pv, col_id)
            if not bbox:
                continue
            x, y, w, h = bbox
            pad = 3
            sw = tk.Canvas(tv, width=w - pad * 2, height=h - pad * 2,
                           bg=hex_color, highlightthickness=1,
                           highlightbackground="#666666", cursor="hand2")
            sw.place(x=x + pad, y=y + pad)
            # Click on swatch opens color picker
            sw.bind("<Button-1>", lambda _, p=pv: self._pick_pv_color(p))
            self._axis_tv_swatches.append(sw)

    def _pick_pv_color(self, pv: str):
        from tkinter.colorchooser import askcolor
        s = self._pv_settings.get(pv, {})
        result = askcolor(color=s.get("color", "#1976D2"), title=f"Color for {pv}")
        if result and result[1]:
            s["color"] = result[1]
            self._pv_settings[pv] = s
            self._apply_pv_settings_to_graph()
            self._refresh_axis_settings_tv()

    def _on_axis_tv_single_click(self, event):
        """Handle clicks on toggle-type columns (show, auto_scale, grid)."""
        region = self._axis_tv.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id  = self._axis_tv.identify_column(event.x)
        row_id  = self._axis_tv.identify_row(event.y)
        if not row_id:
            return
        col_idx  = int(col_id.lstrip("#")) - 1
        col_name = self._axis_tv_cols[col_idx]

        if col_name == "show":
            pv = row_id
            s  = self._pv_settings.get(pv, {})
            s["show"] = not s.get("show", True)
            self._pv_settings[pv] = s
            self._apply_pv_settings_to_graph()
            self._refresh_axis_settings_tv()

        elif col_name == "auto_scale":
            pv = row_id
            s  = self._pv_settings.get(pv, {})
            s["auto_scale"] = not s.get("auto_scale", True)
            self._pv_settings[pv] = s
            self._apply_pv_settings_to_graph()
            self._refresh_axis_settings_tv()

        elif col_name == "grid":
            pv = row_id
            s  = self._pv_settings.get(pv, {})
            s["grid"] = not s.get("grid", True)
            self._pv_settings[pv] = s
            self._apply_pv_settings_to_graph()
            self._refresh_axis_settings_tv()

        elif col_name == "side":
            pv = row_id
            s  = self._pv_settings.get(pv, {})
            s["side"] = "right" if s.get("side", "left") == "left" else "left"
            self._pv_settings[pv] = s
            self._plot_graph()   # full redraw needed to reposition axes

        elif col_name == "color":
            self._pick_pv_color(row_id)

    def _on_axis_tv_double_click(self, event):
        """Open inline editor for editable text columns."""
        region = self._axis_tv.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id  = self._axis_tv.identify_column(event.x)
        row_id  = self._axis_tv.identify_row(event.y)
        if not row_id:
            return
        col_idx  = int(col_id.lstrip("#")) - 1
        col_name = self._axis_tv_cols[col_idx]

        # Read-only / indicator columns
        if col_name in ("pv", "cursor_val", "cursor_ts"):
            return

        # Toggle columns are handled by single-click
        if col_name in ("show", "auto_scale", "grid", "side"):
            return

        # Button columns handled by single-click
        if col_name in ("color",):
            return

        # Dropdown for axis column (kept for backward compat, now unused)
        if col_name == "axis":
            return

        # Inline Entry for text/numeric columns
        self._axis_tv_inline_entry(row_id, col_id, col_name)

    def _axis_tv_inline_entry(self, row_id: str, col_id: str, col_name: str):
        """Place an Entry widget over the treeview cell for inline editing."""
        tv = self._axis_tv
        bbox = tv.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox

        pv = row_id
        s  = self._pv_settings.get(pv, {})
        # Current display value
        col_idx = int(col_id.lstrip("#")) - 1
        cur_val = tv.item(row_id, "values")[col_idx]

        var = tk.StringVar(value=cur_val)
        entry = tk.Entry(tv, textvariable=var, font=FONT_MONO, bd=0, highlightthickness=1)
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()
        entry.select_range(0, tk.END)

        def _commit(event=None):  # noqa: ARG001
            entry.destroy()
            new_val = var.get().strip()
            if col_name == "display_name":
                s["display_name"] = new_val or shorten_pv_name(pv)
            elif col_name == "ymin":
                try:
                    s["ymin"] = float(new_val) if new_val else None
                    if new_val:
                        s["auto_scale"] = False
                except ValueError:
                    pass
            elif col_name == "ymax":
                try:
                    s["ymax"] = float(new_val) if new_val else None
                    if new_val:
                        s["auto_scale"] = False
                except ValueError:
                    pass
            elif col_name == "width":
                try:    s["width"] = float(new_val) if new_val else None
                except ValueError: pass
            elif col_name == "smooth":
                try:    s["smooth"] = int(new_val) if new_val else None
                except ValueError: pass
                self._pv_settings[pv] = s
                self._plot_graph()  # full redraw needed for smooth change
                self._refresh_axis_settings_tv()
                return  # skip the call below
            self._pv_settings[pv] = s
            self._apply_pv_settings_to_graph()
            self._refresh_axis_settings_tv()

        entry.bind("<Return>",  _commit)
        entry.bind("<Escape>",  lambda _: entry.destroy())
        entry.bind("<FocusOut>", _commit)

    def _axis_tv_inline_dropdown(self, row_id: str, col_id: str, col_name: str):
        """Place a Combobox dropdown over an axis cell."""
        tv   = self._axis_tv
        bbox = tv.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        pv  = row_id
        s   = self._pv_settings.get(pv, {})
        n   = max(len(self._graph_pvs), 1)
        options = [f"Value {i+1}" for i in range(n)]
        col_idx = int(col_id.lstrip("#")) - 1
        cur_val = tv.item(row_id, "values")[col_idx]
        var = tk.StringVar(value=cur_val)
        cb  = ttk.Combobox(tv, textvariable=var, values=options, state="readonly",
                           font=FONT_NORMAL)
        cb.place(x=x, y=y, width=max(w, 80), height=h)
        cb.focus_set()

        def _commit(event=None):  # noqa: ARG001
            cb.destroy()
            s["axis"] = var.get()
            self._pv_settings[pv] = s
            self._refresh_axis_settings_tv()

        cb.bind("<<ComboboxSelected>>", _commit)
        cb.bind("<FocusOut>", lambda _: cb.destroy())

    def _apply_pv_settings_to_graph(self):
        """Push current _pv_settings to the live graph lines without full redraw."""
        if self._mpl_canvas is None:
            return
        for i, (pv, ax, pv_lines) in enumerate(zip(
                self._graph_pvs, self._graph_axes, self._graph_lines)):
            s = self._pv_settings.get(pv, {})
            color   = s.get("color")
            visible = s.get("show", True)
            width   = s.get("width")
            grid    = s.get("grid", True)
            for line in pv_lines:
                if color:
                    line.set_color(color)
                if width is not None:
                    line.set_linewidth(width)
                line.set_visible(visible)
            ax.grid(grid)

            # Y limits
            auto_scale = s.get("auto_scale", True)
            ymin = s.get("ymin")
            ymax = s.get("ymax")
            if not auto_scale and (ymin is not None or ymax is not None):
                ax.set_ylim(ymin, ymax)
            elif auto_scale:
                ax.set_ylim(auto=True)
                ax.relim()
                ax.autoscale_view(scaley=True)

            # Ylabel and colour
            disp = s.get("display_name", shorten_pv_name(pv))
            line_color = s.get("color", ax.get_ylabel())
            try:
                _fs = max(5, int(self._font_size_var.get() or "7"))
            except (ValueError, AttributeError):
                _fs = 7
            ax.set_ylabel(disp, color=line_color if color else ax.yaxis.label.get_color(),
                          fontsize=_fs, rotation=90, labelpad=2)
            ax.tick_params(axis="y", labelsize=_fs, labelrotation=90)

        self._mpl_canvas.draw_idle()

    def _apply_font_size(self):
        if not self._mpl_figure or not self._samples_by_pv:
            return
        # Full redraw so axis spacing recalculates based on new font size
        self._plot_graph()

    # -- Table tab ------------------------------------------------------------

    def _build_table_tab(self):
        tab = self.tab_table
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        tf = tk.Frame(tab)
        tf.grid(row=0, column=0, sticky=tk.NSEW, padx=4, pady=4)
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tf, show="headings")
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)

        self._live_programmatic_scroll = False

        def _on_yscroll(first, last):
            sy.set(first, last)
            if self._live_programmatic_scroll:
                return   # ignore scroll events triggered by our own tree.see()
            if float(last) >= 1.0:
                self._live_autoscroll = True
            else:
                self._live_autoscroll = False

        sy = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.tree.yview)
        sx = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.tree.xview)
        sy.grid(row=0, column=1, sticky=tk.NS)
        sx.grid(row=1, column=0, sticky=tk.EW)
        self.tree.configure(yscrollcommand=_on_yscroll, xscrollcommand=sx.set)

        self._tree_menu = tk.Menu(self.root, tearoff=0)
        self._tree_menu.add_command(label="Copy cell",  command=self._copy_cell)
        self._tree_menu.add_command(label="Copy row",   command=self._copy_row)
        self._tree_menu.add_separator()
        self._tree_menu.add_command(label="Open image", command=self._open_image_from_selection)

        self.tree.bind("<Button-3>",       self._on_tree_right_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        self.tree.bind("<Motion>",          self._on_tree_motion)

        self._clicked_col_id: str | None = None

        # Tooltip
        self._tooltip = tk.Toplevel(self.root)
        self._tooltip.withdraw()
        self._tooltip.overrideredirect(True)
        self._tooltip.attributes("-topmost", True)
        self._tip_label = tk.Label(self._tooltip, font=FONT_MONO,
                                    bg="#ffffcc", relief=tk.SOLID, borderwidth=1, padx=4, pady=2)
        self._tip_label.pack()
        self._tip_col: str | None = None

        info = tk.Frame(tab)
        info.grid(row=1, column=0, sticky=tk.EW, padx=4, pady=(0, 4))
        self.lbl_table_info = tk.Label(info, text="No data - click 'Load data'.",
                                         font=FONT_NORMAL, fg=COLOR_GRAY, anchor=tk.W)
        self.lbl_table_info.pack(side=tk.LEFT)
        self.btn_export_csv = _btn(info, "Export CSV...", self._export_csv, padx=8, pady=2)
        self.btn_export_csv.pack(side=tk.RIGHT)

    # -- Log tab --------------------------------------------------------------

    def _build_log_tab(self):
        tab = self.tab_log
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        self.log_area = scrolledtext.ScrolledText(
            tab, font=FONT_MONO, state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        self.log_area.grid(row=0, column=0, sticky=tk.NSEW, padx=4, pady=4)

        _btn(tab, "Trash  Clear log", self._clear_log).grid(
            row=1, column=0, sticky=tk.W, padx=4, pady=(0, 4))

    # -------------------------------------------------------------------------
    # Populate UI from state/config
    # -------------------------------------------------------------------------

    def _populate_ui(self):
        for pv in self.config.get("pv_list", []):
            self.pv_listbox.insert(tk.END, pv)
        self._update_pv_count()
        self._refresh_time_labels()

    def _refresh_time_labels(self):
        self._refresh_time_info_labels()

    def _refresh_time_info_labels(self):
        """Update the From/To/Live info labels in the sidebar."""
        if hasattr(self, "lbl_tw_from"):
            self.lbl_tw_from.config(
                text="From: " + self._dt_from.strftime("%Y-%m-%d  %H:%M"))
        if hasattr(self, "lbl_tw_to"):
            self.lbl_tw_to.config(
                text="To:   " + self._dt_to.strftime("%Y-%m-%d  %H:%M"))
        if hasattr(self, "lbl_tw_live"):
            if self._live_mode:
                self.lbl_tw_live.config(text="Live: ON", fg=COLOR_GREEN)
            else:
                self.lbl_tw_live.config(text="Live: OFF", fg=COLOR_GRAY)

    def _open_time_window_dialog(self):
        """Open a From/To time-window dialog — Relative and Absolute tabs per side."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Start/End Time")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        # ── helpers ────────────────────────────────────────────────────────────
        def _make_abs_frame(parent, init_dt: datetime):
            """Absolute tab: calendar grid + HH / MM / SS spinboxes."""
            import calendar as _cal
            frm = tk.Frame(parent)

            # --- calendar ---
            year_var  = tk.IntVar(value=init_dt.year)
            month_var = tk.IntVar(value=init_dt.month)
            day_var   = tk.IntVar(value=init_dt.day)
            hour_var  = tk.StringVar(value=f"{init_dt.hour:02d}")
            min_var   = tk.StringVar(value=f"{init_dt.minute:02d}")
            sec_var   = tk.StringVar(value=f"{init_dt.second:02d}")

            MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
                      "Jul","Aug","Sep","Oct","Nov","Dec"]
            DAY_NAMES = ["Mo","Tu","We","Th","Fr","Sa","Su"]

            nav = tk.Frame(frm)
            nav.pack(fill=tk.X, padx=4, pady=(4, 0))
            tk.Button(nav, text="<<", width=2, relief=tk.RAISED,
                      command=lambda: _shift_year(-1)).pack(side=tk.LEFT)
            tk.Button(nav, text="<", width=2, relief=tk.RAISED,
                      command=lambda: _shift_month(-1)).pack(side=tk.LEFT, padx=2)
            lbl_my = tk.Label(nav, font=FONT_NORMAL, width=14, anchor=tk.CENTER)
            lbl_my.pack(side=tk.LEFT, expand=True)
            tk.Button(nav, text=">", width=2, relief=tk.RAISED,
                      command=lambda: _shift_month(1)).pack(side=tk.RIGHT, padx=2)
            tk.Button(nav, text=">>", width=2, relief=tk.RAISED,
                      command=lambda: _shift_year(1)).pack(side=tk.RIGHT)

            cal_frm = tk.Frame(frm)
            cal_frm.pack(padx=4)
            for c, dn in enumerate(DAY_NAMES):
                tk.Label(cal_frm, text=dn, font=FONT_NORMAL, width=3,
                         fg=COLOR_RED if c >= 5 else "black").grid(row=0, column=c)
            day_btns = []
            for r in range(6):
                for c in range(7):
                    b = tk.Button(cal_frm, text="", width=2, relief=tk.FLAT,
                                  font=FONT_NORMAL,
                                  command=lambda r=r, c=c: _on_day(r, c))
                    b.grid(row=r+1, column=c, padx=1, pady=1)
                    day_btns.append(b)

            def _draw():
                y, m, d = year_var.get(), month_var.get(), day_var.get()
                lbl_my.config(text=f"{MONTHS[m-1]}  {y}")
                first_wd, n_days = _cal.monthrange(y, m)
                for i, b in enumerate(day_btns):
                    dn2 = i - first_wd + 1
                    col = i % 7
                    if 1 <= dn2 <= n_days:
                        sel = (dn2 == d)
                        b.config(text=str(dn2), state=tk.NORMAL,
                                 bg=COLOR_BLUE if sel else "SystemButtonFace",
                                 fg="white" if sel else (COLOR_RED if col >= 5 else "black"),
                                 relief=tk.SOLID if sel else tk.FLAT)
                    else:
                        b.config(text="", state=tk.DISABLED,
                                 bg="SystemButtonFace", relief=tk.FLAT)

            def _on_day(r, c):
                import calendar as _cal2
                y, m = year_var.get(), month_var.get()
                first_wd, n_days = _cal2.monthrange(y, m)
                dn2 = r*7 + c - first_wd + 1
                if 1 <= dn2 <= n_days:
                    day_var.set(dn2); _draw()

            def _shift_month(delta):
                import calendar as _cal2
                y, m = year_var.get(), month_var.get()
                m += delta
                if m < 1:   m = 12; y -= 1
                elif m > 12: m = 1;  y += 1
                year_var.set(y); month_var.set(m)
                _, n = _cal2.monthrange(y, m)
                if day_var.get() > n: day_var.set(n)
                _draw()

            def _shift_year(delta):
                import calendar as _cal2
                year_var.set(year_var.get() + delta)
                _, n = _cal2.monthrange(year_var.get(), month_var.get())
                if day_var.get() > n: day_var.set(n)
                _draw()

            _draw()

            # --- time spinboxes ---
            tf = tk.Frame(frm)
            tf.pack(padx=4, pady=(4, 4))
            tk.Label(tf, text="HH", font=FONT_NORMAL).grid(row=0, column=0)
            tk.Label(tf, text="MM", font=FONT_NORMAL).grid(row=0, column=2)
            tk.Label(tf, text="SS", font=FONT_NORMAL).grid(row=0, column=4)
            tk.Spinbox(tf, textvariable=hour_var, from_=0, to=23, width=3,
                       font=FONT_MONO, format="%02.0f").grid(row=0, column=1)
            tk.Label(tf, text=":", font=FONT_NORMAL).grid(row=0, column=1, sticky=tk.E, padx=(0,1))
            tk.Spinbox(tf, textvariable=min_var, from_=0, to=59, width=3,
                       font=FONT_MONO, format="%02.0f").grid(row=0, column=3)
            tk.Label(tf, text=":", font=FONT_NORMAL).grid(row=0, column=3, sticky=tk.E, padx=(0,1))
            tk.Spinbox(tf, textvariable=sec_var, from_=0, to=59, width=3,
                       font=FONT_MONO, format="%02.0f").grid(row=0, column=5)

            def get_dt():
                try:
                    h = int(hour_var.get().strip() or "0")
                    m = int(min_var.get().strip()  or "0")
                    s = int(sec_var.get().strip()  or "0")
                except ValueError:
                    h, m, s = 0, 0, 0
                return datetime(year_var.get(), month_var.get(), day_var.get(),
                                max(0,min(23,h)), max(0,min(59,m)), max(0,min(59,s)))

            return frm, get_dt

        def _make_rel_frame(parent, lbl_status: tk.Label, show_now: bool = False):
            """Relative tab: spinboxes (2 rows) + presets + Before checkbox + Now."""
            frm = tk.Frame(parent)
            result_dt = [None]
            before_var = tk.BooleanVar(value=True)

            def _vi(sv):
                return sv == "" or sv.isdigit()
            vcmd = (frm.register(_vi), "%P")

            # Row 1: Years / Months / Days
            # Row 2: Hours / Minutes / Secs
            sp_frm = tk.Frame(frm)
            sp_frm.pack(fill=tk.X, padx=6, pady=(6, 2))

            y_var  = tk.StringVar(value="0")
            mo_var = tk.StringVar(value="0")
            d_var  = tk.StringVar(value="0")
            h_var  = tk.StringVar(value="0")
            m_var  = tk.StringVar(value="0")
            s_var  = tk.StringVar(value="0")

            row1 = [("Years", y_var), ("Months", mo_var), ("Days", d_var)]
            row2 = [("Hours", h_var), ("Minutes", m_var), ("Secs", s_var)]

            for r, row_fields in enumerate([row1, row2]):
                for c, (lbl_txt, var) in enumerate(row_fields):
                    tk.Label(sp_frm, text=lbl_txt, font=FONT_NORMAL, width=7,
                             anchor=tk.W).grid(row=r*2, column=c, padx=(6, 0), pady=(4,0), sticky=tk.W)
                    tk.Spinbox(sp_frm, textvariable=var, from_=0, to=9999, width=5,
                               font=FONT_MONO, validate="key", validatecommand=vcmd
                               ).grid(row=r*2+1, column=c, padx=(6, 0), pady=(0, 2), sticky=tk.W)

            all_vars = [y_var, mo_var, d_var, h_var, m_var, s_var]

            def _compute_dt():
                try:
                    total_sec = (int(y_var.get() or 0)*365*86400
                                 + int(mo_var.get() or 0)*30*86400
                                 + int(d_var.get() or 0)*86400
                                 + int(h_var.get() or 0)*3600
                                 + int(m_var.get() or 0)*60
                                 + int(s_var.get() or 0))
                except ValueError:
                    return None
                now = datetime.now()
                delta = timedelta(seconds=total_sec)
                return now - delta if before_var.get() else now + delta

            def _refresh_status(*_):
                dt = _compute_dt()
                result_dt[0] = dt
                lbl_status.config(text=dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "")

            for var in all_vars:
                var.trace_add("write", _refresh_status)
            before_var.trace_add("write", _refresh_status)

            # Preset buttons: 12h / 1 Day / 3 Days / 7 Days
            preset_frm = tk.Frame(frm)
            preset_frm.pack(fill=tk.X, padx=6, pady=(4, 2))
            PRESETS = [
                ("12 h",   dict(h="12")),
                ("1 Day",  dict(d="1")),
                ("3 Days", dict(d="3")),
                ("7 Days", dict(d="7")),
            ]

            def _apply_preset_rel(kw):
                for var in all_vars:
                    var.set("0")
                if "h" in kw: h_var.set(kw["h"])
                if "d" in kw: d_var.set(kw["d"])

            for i, (lbl_txt, kw) in enumerate(PRESETS):
                tk.Button(preset_frm, text=lbl_txt, font=FONT_NORMAL, padx=4,
                          command=lambda kw=kw: _apply_preset_rel(kw)
                          ).grid(row=0, column=i, padx=2, sticky=tk.EW)
            for i in range(len(PRESETS)):
                preset_frm.columnconfigure(i, weight=1)

            # Now button (only for End side)
            if show_now:
                bot_frm = tk.Frame(frm)
                bot_frm.pack(fill=tk.X, padx=6, pady=(2, 6))
                tk.Button(bot_frm, text="Now", font=FONT_NORMAL, padx=6,
                          command=lambda: [v.set("0") for v in all_vars]
                          ).pack(side=tk.RIGHT)

            _refresh_status()

            def get_dt():
                return result_dt[0]

            return frm, get_dt, None

        # ── layout ─────────────────────────────────────────────────────────────
        outer = tk.Frame(dlg)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        get_from_dt = [lambda: self._dt_from]
        get_to_dt   = [lambda: self._dt_to]

        for col, (which, side_label, init_dt) in enumerate([
                ("from", "Start", self._dt_from),
                ("to",   "End",   self._dt_to),
        ]):
            col_frm = tk.LabelFrame(outer, text=side_label, font=FONT_HEADER,
                                    fg=COLOR_BLUE, padx=4, pady=4)
            col_frm.grid(row=0, column=col, sticky=tk.NSEW,
                         padx=(0, 6) if col == 0 else (0, 0))

            nb = ttk.Notebook(col_frm)
            nb.pack(fill=tk.BOTH, expand=True)

            abs_tab = tk.Frame(nb)
            rel_tab = tk.Frame(nb)
            nb.add(abs_tab, text="  Absolute  ")
            nb.add(rel_tab, text="  Relative  ")

            # Status label shared between tabs — shows resolved datetime
            lbl_status_var = tk.StringVar(value="")
            lbl_status = tk.Label(col_frm, textvariable=lbl_status_var,
                                  font=FONT_NORMAL, fg=COLOR_BLUE, anchor=tk.W)
            lbl_status.pack(fill=tk.X, padx=4, pady=(2, 0))

            abs_frm, get_abs_dt = _make_abs_frame(abs_tab, init_dt)
            abs_frm.pack(fill=tk.BOTH, expand=True)

            rel_frm, get_rel_dt, _ = _make_rel_frame(rel_tab, lbl_status, show_now=(which == "to"))
            rel_frm.pack(fill=tk.BOTH, expand=True)

            # Update status label when abs tab fields change via focus
            def _update_abs_status(nb_ref=nb, get_abs=get_abs_dt, lbl=lbl_status_var):
                if nb_ref.index(nb_ref.select()) == 0:
                    try:
                        lbl.set(get_abs().strftime("%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        pass
            abs_tab.bind("<FocusOut>", lambda _e, f=_update_abs_status: f())
            nb.bind("<<NotebookTabChanged>>", lambda _e, f=_update_abs_status: f())

            def _make_getter(nb_ref, get_abs, get_rel, fallback):
                def _get():
                    if nb_ref.index(nb_ref.select()) == 0:
                        return get_abs()
                    else:
                        v = get_rel()
                        return v if v is not None else fallback
                return _get
            getter = _make_getter(nb, get_abs_dt, get_rel_dt,
                                  self._dt_from if which == "from" else self._dt_to)

            if which == "from":
                get_from_dt[0] = getter
            else:
                get_to_dt[0]   = getter

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        # ── OK / Cancel ────────────────────────────────────────────────────────
        result = [False]

        def _ok():
            result[0] = True
            dlg.destroy()

        btn_row = tk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=10, pady=(4, 10))
        tk.Button(btn_row, text="Cancel", relief=tk.RAISED, padx=10,
                  command=dlg.destroy).pack(side=tk.RIGHT)
        tk.Button(btn_row, text="OK", relief=tk.RAISED, padx=14,
                  bg=COLOR_BLUE, fg="white", command=_ok).pack(side=tk.RIGHT, padx=(0, 6))

        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry(f"+{(sw-dw)//2}+{(sh-dh)//2}")

        dlg.wait_window()
        if not result[0]:
            return

        try:
            new_from = get_from_dt[0]()
            new_to   = get_to_dt[0]()
        except Exception:
            return
        if new_from is None or new_to is None:
            messagebox.showerror("Invalid date", "Could not determine time range.", parent=None)
            return
        self._dt_from = new_from
        self._dt_to   = new_to
        self._refresh_time_info_labels()
        self.config["time_from"] = self._dt_from.strftime("%Y-%m-%d %H:%M:%S")
        self.config["time_to"]   = self._dt_to.strftime("%Y-%m-%d %H:%M:%S")
        save_config(self.config)

    # -------------------------------------------------------------------------
    # Time window controls
    # -------------------------------------------------------------------------

    def _apply_preset(self, hours: int):
        self._dt_to   = datetime.now()
        self._dt_from = self._dt_to - timedelta(hours=hours)
        self._refresh_time_labels()

    def _pick_date(self, which: str, click_x: int = 0, click_y: int = 0):
        init = self._dt_from if which == "from" else self._dt_to

        def on_ok(dt: datetime):
            if which == "from":
                self._dt_from = dt
            else:
                self._dt_to = dt
            self._refresh_time_labels()
            self.config["time_from"] = self._dt_from.strftime("%Y-%m-%d %H:%M:%S")
            self.config["time_to"]   = self._dt_to.strftime("%Y-%m-%d %H:%M:%S")
            save_config(self.config)

        DatePickerDialog(self.root, init, on_ok, click_x=click_x, click_y=click_y)

    def _resolve_time_window(self) -> tuple[int, int] | None:
        start_ns = dt_to_ns(self._dt_from)
        end_ns   = dt_to_ns(self._dt_to)
        if end_ns <= start_ns:
            messagebox.showerror("Invalid range", "'To' must be later than 'From'.")
            return None
        return start_ns, end_ns

    # -------------------------------------------------------------------------
    # PV list
    # -------------------------------------------------------------------------

    def _get_pv_list(self) -> list[str]:
        return list(self.pv_listbox.get(0, tk.END))

    def _update_pv_count(self):
        self.lbl_pv_count.config(text=f"{self.pv_listbox.size()} PV")

    def _open_pv_browser(self):
        btn = self.btn_browse
        PVBrowserDialog(self.root, on_add_callback=self._add_pvs_from_browser,
                        timeout=float(self.config.get("http_timeout", CPVA_HTTP_TIMEOUT)),
                        initial_search=self._last_pv_search,
                        on_close_callback=lambda t: setattr(self, "_last_pv_search", t),
                        x=btn.winfo_rootx(),
                        y=btn.winfo_rooty() + btn.winfo_height() + 2)

    def _add_pvs_from_browser(self, pv_names: list[str]):
        existing = set(self._get_pv_list())
        for pv in pv_names:
            if pv not in existing:
                self.pv_listbox.insert(tk.END, pv)
                existing.add(pv)
        self._update_pv_count()
        self._save_pv_list_to_config()

    def _remove_selected_pvs(self):
        sel = self.pv_listbox.curselection()
        if not sel:
            return
        # Remember lowest index to re-select after deletion
        first_sel = sel[0]
        for i in reversed(sel):
            self.pv_listbox.delete(i)
        self._update_pv_count()
        self._save_pv_list_to_config()
        # Re-select the item that took the place of the first deleted item
        new_size = self.pv_listbox.size()
        if new_size > 0:
            new_sel = min(first_sel, new_size - 1)
            self.pv_listbox.selection_set(new_sel)
            self.pv_listbox.activate(new_sel)
            self.pv_listbox.see(new_sel)

    def _on_pv_double_click(self, event):
        """Double-click: select only the clicked PV (deselect all others)."""
        idx = self.pv_listbox.nearest(event.y)
        if idx < 0 or idx >= self.pv_listbox.size():
            return
        self.pv_listbox.selection_clear(0, tk.END)
        self.pv_listbox.selection_set(idx)
        self.pv_listbox.activate(idx)

    def _clear_pv_list(self):
        if self.pv_listbox.size() == 0:
            return

        btn = self.btn_clear_pvs
        dlg = tk.Toplevel(self.root)
        dlg.title("Confirm")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Remove all PVs from the list?",
                 font=FONT_NORMAL, padx=16, pady=12).pack()
        row = tk.Frame(dlg)
        row.pack(fill=tk.X, padx=12, pady=(0, 10))
        confirmed = [False]

        def _yes():
            confirmed[0] = True
            dlg.destroy()

        tk.Button(row, text="Cancel", relief=tk.RAISED, padx=10,
                  command=dlg.destroy).pack(side=tk.RIGHT)
        tk.Button(row, text="Remove all", relief=tk.RAISED, padx=10,
                  bg=COLOR_RED, fg="white", command=_yes).pack(side=tk.RIGHT, padx=(0, 6))

        dlg.update_idletasks()
        dw = dlg.winfo_reqwidth()
        dh = dlg.winfo_reqheight()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        bx = btn.winfo_rootx()
        by = btn.winfo_rooty() + btn.winfo_height() + 2
        dlg.geometry(f"{dw}x{dh}+{max(0, min(bx, sw-dw-4))}+{max(0, min(by, sh-dh-4))}")

        dlg.wait_window()
        if confirmed[0]:
            self.pv_listbox.delete(0, tk.END)
            self._update_pv_count()
            self._save_pv_list_to_config()

    def _save_pv_list_to_config(self):
        self.config["pv_list"] = self._get_pv_list()
        save_config(self.config)

    # -------------------------------------------------------------------------
    # Presets
    # -------------------------------------------------------------------------

    def _refresh_preset_combo(self):
        names = [p["name"] for p in self._presets]
        self.preset_combo["values"] = names
        if names and self._preset_var.get() not in names:
            self._preset_var.set(names[0])
        elif not names:
            self._preset_var.set("")

    def _load_preset(self):
        name = self._preset_var.get()
        preset = next((p for p in self._presets if p["name"] == name), None)
        if preset is None:
            messagebox.showinfo("No preset", "Select a preset from the dropdown first.")
            return
        # Load PV list
        self.pv_listbox.delete(0, tk.END)
        for pv in preset.get("pvs", []):
            self.pv_listbox.insert(tk.END, pv)
        self._update_pv_count()
        self._save_pv_list_to_config()
        # Optionally apply time window
        hours = preset.get("time_window_hours")
        if hours:
            self._apply_preset(float(hours))
        self.lbl_status.config(text=f"Preset loaded: {name}", fg=COLOR_BLUE)

    def _save_preset(self):
        """Overwrite currently selected preset with the current PV list."""
        name = self._preset_var.get()
        if not name:
            # No preset selected — fall through to Save as new
            self._save_preset_as()
            return
        pvs = self._get_pv_list()
        if not pvs:
            messagebox.showwarning("Empty list", "Add PVs to the list before saving.")
            return
        existing = next((p for p in self._presets if p["name"] == name), None)
        if existing is None:
            # Shouldn't happen, but handle gracefully
            self._save_preset_as()
            return
        existing["pvs"] = list(pvs)
        save_presets(self._presets)
        self._refresh_preset_combo()
        self._preset_var.set(name)
        self.lbl_status.config(
            text=f"Preset '{name}' saved to {PRESETS_FILE}", fg=COLOR_GREEN)

    def _delete_preset(self):
        """Delete the currently selected preset after confirmation."""
        name = self._preset_var.get()
        if not name:
            messagebox.showinfo("No preset", "Select a preset from the dropdown first.")
            return
        if not messagebox.askyesno("Delete preset", f'Delete preset "{name}"?'):
            return
        self._presets = [p for p in self._presets if p["name"] != name]
        save_presets(self._presets)
        self._refresh_preset_combo()
        self.lbl_status.config(text=f"Preset deleted: {name}", fg=COLOR_GRAY)

    def _save_preset_as(self):
        pvs = self._get_pv_list()
        if not pvs:
            messagebox.showwarning("Empty list", "Add PVs to the list before saving a preset.")
            return

        btn = self.preset_combo
        dlg = tk.Toplevel(self.root)
        dlg.title("Save preset")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Preset name:", font=FONT_NORMAL).pack(anchor=tk.W, padx=12, pady=(12, 2))
        name_var = tk.StringVar(value=self._preset_var.get() or "")
        entry = tk.Entry(dlg, textvariable=name_var, font=FONT_MONO, width=30)
        entry.pack(padx=12, pady=(0, 4))
        entry.select_range(0, tk.END)
        entry.focus_set()

        tk.Label(dlg, text="Apply time window on load:", font=FONT_NORMAL).pack(anchor=tk.W, padx=12, pady=(4, 2))
        hours_var = tk.StringVar(value="")
        hours_row = tk.Frame(dlg)
        hours_row.pack(fill=tk.X, padx=12, pady=(0, 8))
        tk.Entry(hours_row, textvariable=hours_var, width=8, font=FONT_MONO).pack(side=tk.LEFT)
        tk.Label(hours_row, text="hours  (leave empty = don't change)", font=FONT_NORMAL,
                 fg=COLOR_GRAY).pack(side=tk.LEFT, padx=6)

        result = [False]

        def _ok():
            result[0] = True
            dlg.destroy()

        entry.bind("<Return>", lambda _: _ok())
        btn_row = tk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Button(btn_row, text="Cancel", relief=tk.RAISED, padx=10,
                  command=dlg.destroy).pack(side=tk.RIGHT)
        tk.Button(btn_row, text="Save", relief=tk.RAISED, padx=14,
                  bg=COLOR_BLUE, fg="white", command=_ok).pack(side=tk.RIGHT, padx=(0, 6))

        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        bx = btn.winfo_rootx()
        by = btn.winfo_rooty() + btn.winfo_height() + 2
        dlg.geometry(f"{dw}x{dh}+{max(0, min(bx, sw-dw-4))}+{max(0, min(by, sh-dh-4))}")

        dlg.wait_window()
        if not result[0]:
            return

        name = name_var.get().strip()
        if not name:
            messagebox.showwarning("No name", "Enter a name for the preset.")
            return

        try:
            hours = float(hours_var.get().strip()) if hours_var.get().strip() else None
        except ValueError:
            messagebox.showwarning("Invalid hours", "Enter a number or leave empty.")
            return

        # Update existing or append
        existing = next((p for p in self._presets if p["name"] == name), None)
        if existing:
            existing["pvs"] = pvs
            existing["time_window_hours"] = hours
        else:
            self._presets.append({"name": name, "pvs": pvs, "time_window_hours": hours})

        save_presets(self._presets)
        self._refresh_preset_combo()
        self._preset_var.set(name)
        self.lbl_status.config(
            text=f"Preset '{name}' saved to {PRESETS_FILE}", fg=COLOR_GREEN)

    def _manage_presets(self):
        if not self._presets:
            messagebox.showinfo("No presets", "No presets saved yet.")
            return

        btn = self.preset_combo
        dlg = tk.Toplevel(self.root)
        dlg.title("Manage presets")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Select a preset to rename or delete:",
                 font=FONT_NORMAL).pack(anchor=tk.W, padx=12, pady=(10, 4))

        listvar = tk.StringVar(value=[p["name"] for p in self._presets])
        lb = tk.Listbox(dlg, listvariable=listvar, font=FONT_MONO,
                        selectmode=tk.SINGLE, width=36, height=min(10, len(self._presets)))
        lb.pack(padx=12, pady=(0, 4))
        if self._preset_var.get():
            names = [p["name"] for p in self._presets]
            if self._preset_var.get() in names:
                lb.selection_set(names.index(self._preset_var.get()))

        # Rename
        rename_row = tk.Frame(dlg)
        rename_row.pack(fill=tk.X, padx=12, pady=(4, 2))
        tk.Label(rename_row, text="New name:", font=FONT_NORMAL, width=10,
                 anchor=tk.W).pack(side=tk.LEFT)
        rename_var = tk.StringVar()
        tk.Entry(rename_row, textvariable=rename_var, font=FONT_MONO, width=22).pack(side=tk.LEFT)

        def _on_lb_select():
            sel = lb.curselection()
            if sel:
                rename_var.set(self._presets[sel[0]]["name"])
        lb.bind("<<ListboxSelect>>", lambda *_: _on_lb_select())

        def _rename():
            sel = lb.curselection()
            if not sel:
                return
            new_name = rename_var.get().strip()
            if not new_name:
                return
            idx = sel[0]
            if any(p["name"] == new_name for i, p in enumerate(self._presets) if i != idx):
                messagebox.showwarning("Duplicate", f'Preset "{new_name}" already exists.',
                                       parent=dlg)
                return
            self._presets[idx]["name"] = new_name
            save_presets(self._presets)
            listvar.set([p["name"] for p in self._presets])
            lb.selection_set(idx)
            self._refresh_preset_combo()
            self._preset_var.set(new_name)

        def _delete():
            sel = lb.curselection()
            if not sel:
                return
            idx = sel[0]
            name = self._presets[idx]["name"]
            if not messagebox.askyesno("Delete", f'Delete preset "{name}"?', parent=dlg):
                return
            self._presets.pop(idx)
            save_presets(self._presets)
            listvar.set([p["name"] for p in self._presets])
            self._refresh_preset_combo()

        action_row = tk.Frame(dlg)
        action_row.pack(fill=tk.X, padx=12, pady=(2, 8))
        tk.Button(action_row, text="Rename", relief=tk.RAISED, padx=10,
                  command=_rename).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(action_row, text="Delete", relief=tk.RAISED, padx=10,
                  bg=COLOR_RED, fg="white", command=_delete).pack(side=tk.LEFT)
        tk.Button(action_row, text="Close", relief=tk.RAISED, padx=10,
                  command=dlg.destroy).pack(side=tk.RIGHT)

        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        bx = btn.winfo_rootx()
        by = btn.winfo_rooty() + btn.winfo_height() + 2
        dlg.geometry(f"{dw}x{dh}+{max(0, min(bx, sw-dw-4))}+{max(0, min(by, sh-dh-4))}")

        dlg.wait_window()

    # -------------------------------------------------------------------------
    # Data loading
    # -------------------------------------------------------------------------

    def _on_load_clicked(self, live_callback=None):
        pv_list = self._get_pv_list()
        if not pv_list:
            messagebox.showwarning("Empty list", "Add at least one PV (click Browse).")
            return

        window = self._resolve_time_window()
        if window is None:
            return
        start_ns, end_ns = window

        self.btn_load.config(state=tk.DISABLED)
        self.lbl_status.config(text="Downloading data from archiver...", fg="orange")

        duration_h = (end_ns - start_ns) / 1e9 / 3600
        n_chunks   = max(1, int((end_ns - start_ns + CHUNK_SIZE_NS - 1) // CHUNK_SIZE_NS))

        self._log(f"\n{'─'*50}")
        self._log(f"Loading data")
        self._log(f"   From: {ns_to_local_str(start_ns)}")
        self._log(f"   To:   {ns_to_local_str(end_ns)}")
        self._log(f"   PVs:  {len(pv_list)}   |   span: {duration_h:.1f} h   |   chunks: {n_chunks}")
        self._log(f"{'─'*50}")

        timeout = float(self.config.get("http_timeout", CPVA_HTTP_TIMEOUT))

        def fetch_one(pv_name: str) -> tuple[str, list, str | None]:
            """Fetch + parse one PV. Returns (pv_name, samples, error_str|None)."""
            try:
                raw = cpva_fetch_samples_chunked(
                    pv_name, start_ns, end_ns, timeout=timeout,
                    log_fn=self._log)
                parsed = []
                for s in raw:
                    t_ns = s.get("time")
                    if t_ns is None:
                        continue
                    value = cpva_decode_value(s)
                    units = (s.get("metaData") or {}).get("units", "") or ""
                    parsed.append((int(t_ns), value, units))
                self._log(f"   OK  {pv_name}: {len(parsed)} samples")
                return pv_name, parsed, None
            except urllib.error.URLError as e:
                self._log(f"   ERR  {pv_name}: network error: {e}")
                return pv_name, [], str(e)
            except Exception as e:
                self._log(f"   ERR  {pv_name}: {type(e).__name__}: {e}")
                return pv_name, [], f"{type(e).__name__}: {e}"

        def worker():
            from concurrent.futures import ThreadPoolExecutor, as_completed

            samples_by_pv: dict[str, list] = {}
            errors: list[str] = []

            # Fetch all PVs in parallel — one thread per PV, cap at 16
            max_workers = min(len(pv_list), 16)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(fetch_one, pv): pv for pv in pv_list}
                for future in as_completed(futures):
                    pv_name, parsed, err = future.result()
                    samples_by_pv[pv_name] = parsed
                    if err:
                        errors.append(f"{pv_name}: {err}")

            self.root.after(0, lambda: self._on_load_finished(
                samples_by_pv, pv_list, errors, live_callback=live_callback))

        threading.Thread(target=worker, daemon=True).start()

    def _on_load_finished(self, samples_by_pv, pv_order, errors, live_callback=None):
        # Parallel fetch may return chunks out of order — sort every PV by timestamp
        for pv in samples_by_pv:
            samples_by_pv[pv].sort(key=lambda s: s[0])

        # In live mode: keep previous data for PVs that returned empty results this tick
        # (prevents PVs from "disappearing" when they have no new samples in the window)
        if self._live_mode and self._samples_by_pv:
            for pv in pv_order:
                if not samples_by_pv.get(pv):
                    samples_by_pv[pv] = self._samples_by_pv.get(pv, [])

        self._samples_by_pv = samples_by_pv
        self._pv_order      = pv_order
        if self._merge_mode_var.get() == "sample_hold":
            self._table_rows = self._merge_samples_sample_hold(samples_by_pv, pv_order)
        else:
            self._table_rows = self._merge_samples_into_rows(samples_by_pv, pv_order)

        self._populate_table(pv_order)

        # Refresh graph if graph tab is active or if live mode is running
        if self._samples_by_pv:
            graph_tab_active = (self.notebook.select() == str(self.tab_graph))
            numeric_pvs_now = [pv for pv in pv_order
                               if any(isinstance(v, (int, float))
                                      for _, v, _ in self._samples_by_pv.get(pv, []))]
            if graph_tab_active or live_callback is not None:
                if (self._mpl_canvas is not None
                        and set(self._graph_pvs) == set(numeric_pvs_now)
                        and len(self._graph_pvs) == len(numeric_pvs_now)):
                    # In-place update — no flicker
                    self._update_graph_data()
                else:
                    self.lbl_graph_stats.has_stats = False
                    self._plot_graph()

        total = sum(len(s) for s in samples_by_pv.values())
        self._log(f"\nDone. {total} samples, {len(self._table_rows)} merged rows.")

        self.btn_load.config(state=tk.NORMAL)

        if errors:
            self.lbl_status.config(text=f"Done with {len(errors)} error(s).", fg=COLOR_RED)
        else:
            self.lbl_status.config(
                text=f"Loaded {total} samples for {len(pv_order)} PV(s).", fg=COLOR_GREEN)

        if live_callback is not None:
            live_callback()

    # -------------------------------------------------------------------------
    # Live mode
    # -------------------------------------------------------------------------

    def _toggle_live_mode(self):
        if self._live_mode:
            self._live_mode = False
            if self._live_after_id:
                self.root.after_cancel(self._live_after_id)
                self._live_after_id = None
            # Cancel any running countdown
            if self._live_countdown_id:
                self.root.after_cancel(self._live_countdown_id)
                self._live_countdown_id = None
            self.btn_live.config(text="⏵ Live", bg=self.btn_live.cget("bg"))
            self.lbl_status.config(text="Live mode off.", fg=COLOR_GRAY)
            self._refresh_time_info_labels()
        else:
            pvs = self._get_pv_list()
            if not pvs:
                self.lbl_status.config(text="Add PVs first.", fg=COLOR_RED)
                return
            self._live_mode = True
            self._live_autoscroll = True   # always start with auto-scroll on
            self.btn_live.config(text="⏹ Stop Live", bg="#cc3300",
                                 fg="white", activebackground="#aa2200")
            self.lbl_status.config(text="Live mode on — loading…", fg=COLOR_BLUE)
            self._refresh_time_info_labels()
            self._live_tick()

    def _live_tick(self):
        if not self._live_mode:
            return
        # Shift time window: to = now, from = now - current window length
        now = datetime.now()
        span = self._dt_to - self._dt_from
        self._dt_to   = now
        self._dt_from = now - span
        self._refresh_time_labels()
        # Trigger load; after finish, schedule next tick
        self._on_load_clicked(live_callback=self._schedule_live_tick)

    def _schedule_live_tick(self):
        if not self._live_mode:
            return
        self.lbl_status.config(text="Live — refreshing…", fg=COLOR_BLUE)
        self._start_live_countdown()

    def _start_live_countdown(self):
        """Start the countdown timer for the live interval."""
        if self._live_countdown_id:
            self.root.after_cancel(self._live_countdown_id)
            self._live_countdown_id = None
        self._live_countdown_elapsed_ms = 0
        self._live_countdown_tick()

    def _live_countdown_tick(self):
        """Advance the countdown by 50 ms; trigger _live_tick when done."""
        if not self._live_mode:
            return
        try:
            interval_ms = max(200, int(float(self._live_interval_var.get()) * 1000))
        except (ValueError, AttributeError):
            interval_ms = 1000
        TICK = 50
        self._live_countdown_elapsed_ms += TICK
        if self._live_countdown_elapsed_ms >= interval_ms:
            self._live_countdown_id = None
            self._live_tick()
        else:
            self._live_countdown_id = self.root.after(TICK, self._live_countdown_tick)

    # -------------------------------------------------------------------------
    # Row merging
    # -------------------------------------------------------------------------

    def _merge_samples_into_rows(self, samples_by_pv: dict, pv_order: list[str]) -> list:
        """
        Merge all PV events into rows, collapsing events that fall within
        MERGE_WINDOW_MS milliseconds of each other into a single row.
        Within a window, the last value for each PV wins.
        Image-path PVs are never merged — each shot gets its own row.
        """
        # Detect which PVs carry image paths (check first non-None value)
        image_pvs: set[str] = set()
        for pv_name in pv_order:
            for _, value, _ in samples_by_pv.get(pv_name, []):
                if value is not None:
                    if isinstance(value, str) and _looks_like_image_path(value):
                        image_pvs.add(pv_name)
                    break

        # Events from non-image PVs go through the merge window
        mergeable_events: list[tuple] = []
        for pv_name in pv_order:
            if pv_name in image_pvs:
                continue
            for ts_ns, value, units in samples_by_pv.get(pv_name, []):
                mergeable_events.append((ts_ns, pv_name, value, units))

        mergeable_events.sort(key=lambda e: e[0])

        window_ns = MERGE_WINDOW_MS * 1_000_000
        rows: list[tuple[int, dict]] = []
        win_start: int | None = None
        win_dict: dict = {}

        for ts_ns, pv_name, value, units in mergeable_events:
            if win_start is None:
                win_start = ts_ns
                win_dict  = {}

            if ts_ns - win_start <= window_ns:
                win_dict[pv_name] = (value, units)
            else:
                rows.append((win_start, win_dict))
                win_start = ts_ns
                win_dict  = {pv_name: (value, units)}

        if win_start is not None:
            rows.append((win_start, win_dict))

        # Image PVs: each sample becomes its own individual row
        for pv_name in pv_order:
            if pv_name not in image_pvs:
                continue
            for ts_ns, value, units in samples_by_pv.get(pv_name, []):
                rows.append((ts_ns, {pv_name: (value, units)}))

        rows.sort(key=lambda r: r[0])
        return rows

    def _merge_samples_sample_hold(self, samples_by_pv: dict, pv_order: list[str]) -> list:
        events = []

        for pv_name in pv_order:
            for ts_ns, value, units in samples_by_pv.get(pv_name, []):
                events.append((ts_ns, pv_name, value, units))

        events.sort(key=lambda e: e[0])

        last_values = {}
        rows = []

        for ts_ns, pv_name, value, units in events:
            last_values[pv_name] = (value, units)

            row_dict = {}
            for pv in pv_order:
                if pv in last_values:
                    row_dict[pv] = last_values[pv]

            rows.append((ts_ns, row_dict))

        min_gap_ns = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000

        filtered = []
        last_kept_ts = None

        for ts_ns, row_dict in rows:
            if last_kept_ts is None or ts_ns - last_kept_ts >= min_gap_ns:
                filtered.append((ts_ns, row_dict))
                last_kept_ts = ts_ns

        return filtered

    # -------------------------------------------------------------------------
    # Table population
    # -------------------------------------------------------------------------

    def _populate_table(self, pv_order: list[str]):
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not pv_order:
            self.tree.configure(columns=())
            self.lbl_table_info.config(text="No data.")
            return

        # Build column list + display names (shortened) + tooltip map
        columns: list[str] = ["time"]
        display: dict[str, str] = {"time": "Timestamp"}
        self._col_full_names = {}

        for pv in pv_order:
            short = shorten_pv_name(pv)
            vc = f"{pv}:value"
            uc = f"{pv}:units"
            columns += [vc, uc]
            display[vc] = short
            display[uc] = f"{short} [unit]"
            self._col_full_names[vc] = pv
            self._col_full_names[uc] = pv + "  (units)"

        self.tree.configure(columns=columns)

        # Measure column widths from data
        col_widths = {c: 0 for c in columns}

        all_rows: list[list[str]] = []
        for ts_ns, row_dict in self._table_rows:
            vals = [ns_to_local_str(ts_ns)]
            for pv in pv_order:
                if pv in row_dict:
                    v, u = row_dict[pv]
                    vals += [self._format_value(v), u]
                else:
                    vals += ["", ""]
            all_rows.append(vals)
            for c, cell in zip(columns, vals):
                w = self._text_px(cell)
                if w > col_widths[c]:
                    col_widths[c] = w

        for col in columns:
            self.tree.heading(col, text=display[col])
            w = min(col_widths[col] + 16, 600)
            self.tree.column(col, width=max(w, 40), minwidth=40,
                             anchor=tk.W, stretch=False)

        for vals in all_rows:
            self.tree.insert("", tk.END, values=vals)

        # Auto-scroll to bottom in live mode (unless user scrolled away)
        if self._live_mode and self._live_autoscroll:
            children = self.tree.get_children()
            if children:
                self._live_programmatic_scroll = True
                self.tree.see(children[-1])
                self.root.after(50, lambda: setattr(self, "_live_programmatic_scroll", False))

        self.lbl_table_info.config(
            text=f"{len(self._table_rows)} rows | {len(pv_order)} PV(s)  "
                 f"[merged <={MERGE_WINDOW_MS} ms]")

    @staticmethod
    def _text_px(text: str, char_px: int = 7) -> int:
        return max(len(str(text)) * char_px, 30)

    def _format_value(self, val) -> str:
        if val is None:
            return ""
        if isinstance(val, float):
            if val == 0.0:
                return "0"
            import math
            magnitude = math.floor(math.log10(abs(val))) if val != 0 else 0
            decimals  = max(0, 6 - magnitude - 1)
            decimals  = min(decimals, 12)
            return f"{val:.{decimals}f}"
        if isinstance(val, list):
            return f"<array, {len(val)} items>"
        if isinstance(val, str) and _looks_like_image_path(val):
            sz = _image_file_size(val)
            return f"{val}  [{sz}]" if sz else val
        return str(val)

    # -------------------------------------------------------------------------
    # Tooltip on column header hover
    # -------------------------------------------------------------------------

    def _on_tree_motion(self, event: tk.Event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            col_id  = self.tree.identify_column(event.x)
            col_idx = int(col_id.lstrip("#")) - 1
            cols    = list(self.tree["columns"])
            if 0 <= col_idx < len(cols):
                col_key = cols[col_idx]
                full    = self._col_full_names.get(col_key, "")
                if full and full != col_key:
                    if self._tip_col != col_key:
                        self._tip_col = col_key
                        self._tip_label.config(text=full)
                        self._tooltip.deiconify()
                    self._tooltip.geometry(f"+{event.x_root+12}+{event.y_root+10}")
                    return
        self._tooltip.withdraw()
        self._tip_col = None

    # -------------------------------------------------------------------------
    # Table interaction: copy + open image
    # -------------------------------------------------------------------------

    def _on_tree_right_click(self, event: tk.Event):
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if row_id:
            self.tree.selection_set(row_id)
        self._clicked_col_id = col_id
        cell_val = self._get_cell_value(row_id, col_id) if row_id else ""
        state = tk.NORMAL if _looks_like_image_path(cell_val) else tk.DISABLED
        self._tree_menu.entryconfig("Open image", state=state)
        self._tree_menu.post(event.x_root, event.y_root)

    def _on_tree_double_click(self, event: tk.Event):
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id:
            return
        cell_val = self._get_cell_value(row_id, col_id)
        if _looks_like_image_path(cell_val):
            _open_path(_resolve_image_path(cell_val))

    def _get_cell_value(self, row_id: str, col_id: str) -> str:
        if not row_id or not col_id:
            return ""
        try:
            idx    = int(col_id.lstrip("#")) - 1
            values = self.tree.item(row_id, "values")
            return str(values[idx]) if idx < len(values) else ""
        except Exception:
            return ""

    def _copy_cell(self):
        sel = self.tree.selection()
        if not sel:
            return
        val = self._get_cell_value(sel[0], self._clicked_col_id or "#1")
        self.root.clipboard_clear()
        self.root.clipboard_append(val)

    def _copy_row(self):
        sel = self.tree.selection()
        if not sel:
            return
        text = "\t".join(str(v) for v in self.tree.item(sel[0], "values"))
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _open_image_from_selection(self):
        sel = self.tree.selection()
        if not sel:
            return
        val = self._get_cell_value(sel[0], self._clicked_col_id or "#1")
        if _looks_like_image_path(val):
            _open_path(_resolve_image_path(val))
        else:
            messagebox.showinfo("Not an image",
                f"Value does not look like an image path:\n{val}")

    # -------------------------------------------------------------------------
    # CSV Export
    # -------------------------------------------------------------------------

    def _export_csv(self):
        import csv
        from tkinter import filedialog

        if not self._table_rows or not self._pv_order:
            messagebox.showinfo("No data", "Load data first before exporting.")
            return

        # If more than 1 PV, ask which ones to export
        pv_order = self._pv_order
        if len(pv_order) > 1:
            selected = self._ask_export_pvs(pv_order)
            if selected is None:
                return  # cancelled
        else:
            selected = pv_order

        if not selected:
            return

        path = filedialog.asksaveasfilename(
            title="Save CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="cpva_export.csv",
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # Header
                header = ["Timestamp"]
                for pv in selected:
                    short = shorten_pv_name(pv)
                    header += [f"{short} value", f"{short} unit"]
                writer.writerow(header)
                # Data rows
                for ts_ns, row_dict in self._table_rows:
                    row = [ns_to_local_str(ts_ns)]
                    for pv in selected:
                        if pv in row_dict:
                            v, u = row_dict[pv]
                            row += [self._format_value(v), u]
                        else:
                            row += ["", ""]
                    writer.writerow(row)

            self._log(f"Exported {len(self._table_rows)} rows -> {path}")
            messagebox.showinfo("Export done",
                f"Saved {len(self._table_rows)} rows to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _ask_export_pvs(self, pv_order: list[str]) -> list[str] | None:
        """
        Show a small dialog to pick which PVs to include in the export.
        Returns the selected list, or None if cancelled.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Select PVs to export")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.update_idletasks()

        tk.Label(dlg, text="Select PVs to include in the CSV export:",
                 font=FONT_NORMAL).pack(anchor=tk.W, padx=12, pady=(10, 4))

        # Checkboxes
        vars_: list[tk.BooleanVar] = []
        for pv in pv_order:
            v = tk.BooleanVar(value=True)
            vars_.append(v)
            short = shorten_pv_name(pv)
            tk.Checkbutton(dlg, text=f"{short}  ({pv})", variable=v,
                           font=FONT_MONO, anchor=tk.W).pack(anchor=tk.W, padx=16)

        # Select all / none buttons
        sel_row = tk.Frame(dlg)
        sel_row.pack(fill=tk.X, padx=12, pady=(4, 0))
        tk.Button(sel_row, text="All",  relief=tk.FLAT, padx=6,
                  command=lambda: [v.set(True)  for v in vars_]).pack(side=tk.LEFT, padx=2)
        tk.Button(sel_row, text="None", relief=tk.FLAT, padx=6,
                  command=lambda: [v.set(False) for v in vars_]).pack(side=tk.LEFT, padx=2)

        result: list[str] | None = [None]   # mutable container

        def on_ok():
            result[0] = [pv for pv, v in zip(pv_order, vars_) if v.get()]
            dlg.destroy()

        btn_row = tk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=12, pady=(8, 10))
        tk.Button(btn_row, text="Cancel", relief=tk.FLAT, padx=10,
                  command=dlg.destroy).pack(side=tk.RIGHT)
        tk.Button(btn_row, text="Export", relief=tk.FLAT, padx=14,
                  bg=COLOR_GREEN, fg="white", command=on_ok).pack(side=tk.RIGHT, padx=(0, 6))

        # Position near the Export CSV button
        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dw = dlg.winfo_reqwidth()
        dh = dlg.winfo_reqheight()
        bx = self.btn_export_csv.winfo_rootx()
        by = self.btn_export_csv.winfo_rooty() + self.btn_export_csv.winfo_height() + 2
        px = min(bx, sw - dw - 4)
        py = min(by, sh - dh - 4)
        dlg.geometry(f"{dw}x{dh}+{max(0, px)}+{max(0, py)}")

        dlg.wait_window()
        return result[0]

    # -------------------------------------------------------------------------
    # Log
    # -------------------------------------------------------------------------

    def _log(self, message: str):
        def _write():
            self.log_area.config(state=tk.NORMAL)
            self.log_area.insert(tk.END, message + "\n")
            self.log_area.see(tk.END)
            self.log_area.config(state=tk.DISABLED)
        self.root.after(0, _write)

    def _clear_log(self):
        self.log_area.config(state=tk.NORMAL)
        self.log_area.delete("1.0", tk.END)
        self.log_area.config(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = tk.Tk()
    app  = CPVAExplorerApp(root)
    root.mainloop()
