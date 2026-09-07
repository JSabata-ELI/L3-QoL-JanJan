"""
Chiller Explorer  —  Interactive PV data explorer from CPVA archiver.
"""
# cpt.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import json
import sys
import re
import os
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import ssl
import orjson
import requests
import csv
from copy import copy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from matplotlib.dates import MonthLocator

TZ_PRAGUE = ZoneInfo("Europe/Prague")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _icon_app_id(prefix, ico_path):
    """Taskbar identity for `prefix`, tagged with the icon *and* this build.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it: an id that was once seen without a usable icon keeps drawing the
    generic placeholder for good, whatever icon the window later carries, and
    clearing the shell icon cache would have to be repeated on every PC.

    Hashing the icon's own bytes into the id was the first fix, but a
    content-only id can be poisoned just as well, and then it never recovers
    because it only changes when the picture is redrawn. Measured again on
    2026-09-03: Calibrations, CSS Logger, Git Work and Image Tools all drew
    the blank window placeholder on the taskbar while their title bars carried
    the right icon, and Diagnostic -- the only one whose id also carried its
    file name -- drew its icon. So the running build's own file name, which
    carries the version, goes into the hash too: every rebuild runs under an
    id Windows has never seen, so it cannot be serving a stale picture for it,
    on this PC or any other.

    Returns None when the icon cannot be read; the caller then sets no id at
    all rather than burning an id on a run that has no picture to give it.
    The same helper sits in every program here.
    """
    # A frozen build gets no taskbar identity at all, deliberately.
    # Windows caches the taskbar picture per AppUserModelID and never re-reads
    # it, so one bad cache entry breaks that build for good; tagging the id
    # with the build's file name only postponed it (Diagnostic v1.1.3's id
    # drew the blank placeholder within a day of the build). Measured
    # 2026-09-04 with three otherwise identical windows: the app's own id ->
    # placeholder, a never-seen id -> the right icon, no id at all -> the icon
    # from the exe's own resource, which the builder always embeds (verified
    # on a purpose-built PyInstaller exe). With no id Windows keys the button
    # on the exe itself, so there is no per-id cache left to go stale. An id
    # is still worth having when running from source, where the process is
    # python.exe and would otherwise wear the Python icon.
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return None
    if not ico_path:
        return None
    import hashlib
    import os
    import sys
    try:
        with open(ico_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    build = os.path.basename(sys.executable if getattr(sys, "frozen", False)
                             else (sys.argv[0] or __file__))
    tag = hashlib.sha1(data + b"\x00" + build.encode("utf-8", "replace"))
    return f"{prefix}.{tag.hexdigest()[:12]}"


def set_app_icon(win, ico_path: str, app_id: str | None = None) -> None:
    """Give the window (title bar) AND the Windows taskbar button our icon.

    tkinter's iconbitmap only fixes the title bar / WM_BIG icon; the Windows 11
    taskbar reads the *small* icon slots (ICON_SMALL/SMALL2) and the window-class
    icon (GCLP_HICONSM), which Tk otherwise leaves as its default feather logo.
    We force every slot from icon.ico via Win32 before the window is first shown.
    Silently does nothing when icon.ico is not next to the exe / script.
    """
    import ctypes
    _aumid = _icon_app_id(app_id, ico_path) if app_id else None
    if _aumid:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass
    try:
        win.iconbitmap(default=ico_path)
    except Exception:
        pass
    try:
        u = ctypes.windll.user32
        hwnd = u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        big = u.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)  # LR_LOADFROMFILE|LR_DEFAULTSIZE
        sm  = u.LoadImageW(None, ico_path, 1, 16, 16, 0x10)       # LR_LOADFROMFILE
        for which, h in ((1, big), (0, sm), (2, sm)):             # ICON_BIG, ICON_SMALL, ICON_SMALL2
            if h:
                u.SendMessageW(hwnd, 0x0080, which, h)            # WM_SETICON
        set_cls = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
        if big:
            set_cls(hwnd, -14, big)   # GCLP_HICON
        if sm:
            set_cls(hwnd, -34, sm)    # GCLP_HICONSM
    except Exception:
        pass


APP_DIR      = get_app_dir()
CONFIG_FILE  = APP_DIR / "cpva_explorer_config.json"
HISTORICAL_DATA_FILE = APP_DIR / "historical_chiller_data.xlsx"
ARCHIVE_FILE = APP_DIR / "chiller_archive.csv"
ARCHIVE_COLUMNS = (
    ["datetime", "source"]
    + [f"ch{i}_flow" for i in range(1, 7)]
    + [f"ch{i}_temp" for i in range(1, 7)]
)

# ---------------------------------------------------------------------------
# CPVA archiver API constants
# ---------------------------------------------------------------------------

CPVA_BASE_URL          = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_SAMPLES_ENDPOINT  = "/samples"

CPVA_HTTP_TIMEOUT      = 10.0


# The archiver only returns reliable data when the query window <= 1 h.
CHUNK_SIZE_NS = int(3600 * 1e9)   # 1 hour in nanoseconds


_SESSION = requests.Session()
_SESSION.verify = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "time_from": "",
    "time_to": "",
    "http_timeout": 10.0,
}


ALLOWED_PVS = []

for i in range(1, 7):
    ALLOWED_PVS.extend([
        f"L3-UTIL-CHL03-{i:03d}:Flow_GPM",
        f"L3-UTIL-CHL03-{i:03d}:Temp",
        f"L3-UTIL-CHL03-{i:03d}:PumpON",
    ])

GRAPH_GROUPS = {

    "Flow_GPM": [
        f"L3-UTIL-CHL03-{i:03d}:Flow_GPM"
        for i in range(1,7)
    ],

    "Temp": [
        f"L3-UTIL-CHL03-{i:03d}:Temp"
        for i in range(1,7)
    ],
}


CHILLER_NAMES = {
    1: "Diode Array Chiller 1",
    2: "Diode Array Chiller 2",
    3: "Diode Array Chiller 3",
    4: "Diode Array Chiller 4",
    5: "Helium Chiller",
    6: "Utility Chiller",
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
# CPVA API - HTTP
# ---------------------------------------------------------------------------

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT):

    resp = _SESSION.get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json"},
    )

    resp.raise_for_status()

    return orjson.loads(resp.content)


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
                               log_fn=None,
                               max_workers: int = 12) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    chunks = []
    cs = start_ns
    i = 0

    while cs < end_ns:
        ce = min(cs + CHUNK_SIZE_NS, end_ns)
        if not _chunk_is_night(cs, ce):
            chunks.append((i, cs, ce))
        i += 1
        cs = ce

    if not chunks:
        return []

    if len(chunks) == 1:
        return cpva_fetch_samples(channel, chunks[0][1], chunks[0][2], timeout)

    if log_fn:
        log_fn(f"      {channel}: {len(chunks)} chunks")

    workers = min(max_workers, len(chunks))
    results_map = {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(cpva_fetch_samples, channel, cs, ce, timeout): idx
            for idx, cs, ce in chunks
        }

        for fut in as_completed(futures):
            idx = futures[fut]
            results_map[idx] = fut.result()

    results = []
    for idx in sorted(results_map):
        results.extend(results_map[idx])

    return results

def cpva_decode_value(sample: dict):
    val = sample.get("value")

    if val is None:
        return None

    if isinstance(val, (int, float, str)):
        return val

    if isinstance(val, list):
        if len(val) == 1:
            return val[0]

        # ASCII decode zkoušej jen pro kratší listy
        if len(val) <= 512 and val and all(isinstance(x, int) and 0 <= x < 128 for x in val):
            try:
                decoded = "".join(map(chr, val))
                if decoded.strip():
                    return decoded
            except Exception:
                pass

        return val

    return val


def cpva_fetch_channels(timeout: float = CPVA_HTTP_TIMEOUT) -> list[str]:
    return sorted(ALLOWED_PVS)

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
        self.root.title("Chiller Log")
        set_app_icon(self.root, str(APP_DIR / "icon.ico"), "ELI.ChillerLog")
        self.root.minsize(1200, 750)
        self.root.state("zoomed")

        self.config = load_config()

        self._samples_by_pv: dict[str, list] = {}
        self._table_rows:    list             = []
        self._pv_order:      list[str]        = []
        
        self._col_full_names: dict[str, str]  = {}
        
        
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
        
        self._crosshair_vlines = []
        self._crosshair_hlines = []
        self._crosshair_texts  = []
        self._crosshair_cid    = None



        
        # Time state: two datetime objects
        now = datetime.now()
        cfg_from = parse_user_datetime(self.config.get("time_from", ""))
        cfg_to   = parse_user_datetime(self.config.get("time_to",   ""))
        self._dt_from: datetime = cfg_from if cfg_from else now - timedelta(hours=1)
        self._dt_to:   datetime = cfg_to   if cfg_to   else now

        self._build_ui()
        self._populate_ui()
        self._ensure_archive_exists()
        self._refresh_archive_view()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_ui(self):
        main = tk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_graph = tk.Frame(self.notebook)
        self.tab_table = tk.Frame(self.notebook)
        self.tab_log   = tk.Frame(self.notebook)

        self.notebook.add(self.tab_graph, text="  Graph  ")
        self.notebook.add(self.tab_table, text="  Data Archive  ")
        self.notebook.add(self.tab_log,   text="  Log    ")

        self._build_graph_tab()
        self._build_table_tab()
        self._build_log_tab()

        self.notebook.select(self.tab_graph)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        status = tk.Frame(self.root)
        status.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.lbl_status = tk.Label(
            status,
            text="Ready.",
            font=FONT_NORMAL,
            fg=COLOR_GRAY,
            anchor=tk.W
        )
        self.lbl_status.pack(fill=tk.X)

    # -- Graph tab ------------------------------------------------------------

    def _get_pv_default_settings(self, pv: str, idx: int) -> dict:
        colors = [
            "#1976D2", "#e53935", "#4CAF50",
            "#FF9800", "#9C27B0", "#00BCD4",
            "#FF5722", "#607D8B", "#795548", "#009688"
        ]

        s = self._pv_settings.setdefault(pv, {})

        s.setdefault("show", True)
        s.setdefault("display_name", shorten_pv_name(pv))
        s.setdefault("color", colors[idx % len(colors)])
        s.setdefault("width", None)
        s.setdefault("smooth", None)
        s.setdefault("grid", idx == 0)
        s.setdefault("ymin", None)
        s.setdefault("ymax", None)
        s.setdefault("auto_scale", True)

        return s

    def _ensure_archive_exists(self):
        if ARCHIVE_FILE.exists():
            return

        if not HISTORICAL_DATA_FILE.exists():
            self._log(f"Historical XLSX not found: {HISTORICAL_DATA_FILE}")
            return

        rows = self._load_history_xlsx(HISTORICAL_DATA_FILE)

        with open(ARCHIVE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ARCHIVE_COLUMNS)
            writer.writeheader()

            for r in rows:
                out = {c: "" for c in ARCHIVE_COLUMNS}
                out["datetime"] = r["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                out["source"] = "historical_xlsx"

                for ch in range(1, 7):
                    flow = r.get(f"ch{ch}_flow", "")
                    temp = r.get(f"ch{ch}_temp", "")

                    out[f"ch{ch}_flow"] = f"{float(flow):.2f}" if flow != "" else ""
                    out[f"ch{ch}_temp"] = f"{float(temp):.2f}" if temp != "" else ""

                writer.writerow(out)

        self._log(f"Created archive CSV from XLSX: {ARCHIVE_FILE}")

    def _load_history_xlsx(self, path: Path) -> list[dict]:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
        ws = wb.active

        headers = [
            str(cell.value).strip().lower().replace(" ", "_")
            for cell in ws[1]
            if cell.value is not None
        ]

        rows = []

        for excel_row in ws.iter_rows(min_row=2, values_only=True):
            row = dict(zip(headers, excel_row))

            raw_date = row.get("date") or row.get("datum")
            if raw_date is None:
                continue

            if isinstance(raw_date, datetime):
                dt = raw_date
            else:
                dt = datetime.strptime(str(raw_date).strip(), "%d.%m.%Y")

            row["datetime"] = dt.replace(tzinfo=TZ_PRAGUE)

            for ch in range(1, 7):
                for name in ("flow", "temp"):
                    key = f"ch{ch}_{name}"
                    val = row.get(key)

                    if val in ("", None):
                        row[key] = ""
                    else:
                        try:
                            row[key] = float(str(val).replace(",", "."))
                        except Exception:
                            row[key] = ""

            rows.append(row)

        rows.sort(key=lambda r: r["datetime"])
        return rows


    def _set_display_range_preset(self, preset: str):
        rows = self._load_archive_rows()

        if not rows:
            self.lbl_status.config(text="Archive is empty.", fg=COLOR_RED)
            return

        dates = [r["datetime"] for r in rows if r.get("datetime") is not None]

        if not dates:
            self.lbl_status.config(text="No valid dates in archive.", fg=COLOR_RED)
            return

        max_dt = max(dates)

        if preset == "all":
            self._dt_from = min(dates).replace(tzinfo=None)
            self._dt_to = max_dt.replace(tzinfo=None)

        elif preset == "1y":
            self._dt_to = max_dt.replace(tzinfo=None)
            self._dt_from = (max_dt - timedelta(days=365)).replace(tzinfo=None)

        elif preset == "6m":
            self._dt_to = max_dt.replace(tzinfo=None)
            self._dt_from = (max_dt - timedelta(days=183)).replace(tzinfo=None)

        elif preset == "1m":
            self._dt_to = max_dt.replace(tzinfo=None)
            self._dt_from = (max_dt - timedelta(days=31)).replace(tzinfo=None)

        self._refresh_time_info_labels()

        self.config["time_from"] = self._dt_from.strftime("%Y-%m-%d %H:%M:%S")
        self.config["time_to"] = self._dt_to.strftime("%Y-%m-%d %H:%M:%S")
        save_config(self.config)

        self._refresh_archive_view()

    def _load_archive_rows(self) -> list[dict]:
        if not ARCHIVE_FILE.exists():
            return []

        rows = []

        with open(ARCHIVE_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for r in reader:
                try:
                    dt = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
                    r["datetime"] = dt.replace(tzinfo=TZ_PRAGUE)
                except Exception:
                    continue

                rows.append(r)

        rows.sort(key=lambda r: r["datetime"])
        return rows
    
    def _get_last_archive_datetime(self) -> datetime | None:
        rows = self._load_archive_rows()

        if not rows:
            return None

        dates = [
            r["datetime"]
            for r in rows
            if r.get("datetime") is not None
        ]

        if not dates:
            return None

        return max(dates)

    def _build_graph_tab(self):
        tab = self.tab_graph
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=3)   # graph gets most of the space

        GRAPH_TYPES = [
            "Flow_GPM",
            "Temp",
        ]
       
        ctrl = tk.Frame(tab)
        ctrl.grid(row=0, column=0, sticky=tk.EW, padx=4, pady=(4, 0))

        # Left side: graph controls

        _btn(
            ctrl,
            "📅 Display Range",
            self._open_time_window_dialog,
            padx=8,
            pady=3
        ).pack(side=tk.LEFT, padx=(0, 6))


        _btn(ctrl, "All", lambda: self._set_display_range_preset("all"),
            padx=6, pady=3).pack(side=tk.LEFT, padx=(0, 3))

        _btn(ctrl, "1Y", lambda: self._set_display_range_preset("1y"),
            padx=6, pady=3).pack(side=tk.LEFT, padx=(0, 3))

        _btn(ctrl, "6M", lambda: self._set_display_range_preset("6m"),
            padx=6, pady=3).pack(side=tk.LEFT, padx=(0, 3))

        _btn(ctrl, "1M", lambda: self._set_display_range_preset("1m"),
            padx=6, pady=3).pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_tw_from = tk.Label(
            ctrl,
            text="From: --",
            font=FONT_NORMAL,
            fg=COLOR_GRAY
        )
        self.lbl_tw_from.pack(side=tk.LEFT, padx=(0, 4))

        self.lbl_tw_to = tk.Label(
            ctrl,
            text="To: --",
            font=FONT_NORMAL,
            fg=COLOR_GRAY
        )
        self.lbl_tw_to.pack(side=tk.LEFT, padx=(0, 12))

        _btn(
            ctrl,
            "🔄 Update archive",
            self._update_archive_from_cpva,
            padx=8,
            pady=3
        ).pack(side=tk.LEFT, padx=(0, 6))

        _btn(ctrl, "💾 Save graph", self._save_graph,
             padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 6))
        
        
        self.btn_zoom_back = _btn(ctrl, "↩ Back", self._zoom_back,
                                   padx=8, pady=3)
        self.btn_zoom_back.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_zoom_back.config(state=tk.DISABLED)

       
        self._graph_mode = tk.StringVar(value="Flow_GPM")

        tk.Label(
            ctrl,
            text="Choose Variable:"
        ).pack(side=tk.LEFT)

        self.graph_type_combo = ttk.Combobox(
            ctrl,
            textvariable=self._graph_mode,
            values=GRAPH_TYPES,
            state="readonly",
            width=15
        )

        self.graph_type_combo.pack(
            side=tk.LEFT,
            padx=(5,20)
        )


        self._visible_chillers = {}

        for ch in range(1, 7):
            var = tk.BooleanVar(value=True)
            self._visible_chillers[ch] = var

            tk.Checkbutton(
                ctrl,
                text=f"CH{ch}",
                variable=var,
                command=self._refresh_archive_view
            ).pack(side=tk.LEFT)

        self.graph_type_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._refresh_archive_view()
        )

        self._show_grid = tk.BooleanVar(value=True)

        tk.Checkbutton(
            ctrl,
            text="Grid",
            variable=self._show_grid,
            command=self._refresh_archive_view
        ).pack(side=tk.LEFT, padx=(10,0))

        self._show_legend = tk.BooleanVar(value=True)

        tk.Checkbutton(
            ctrl,
            text="Legend",
            variable=self._show_legend,
            command=self._refresh_archive_view
        ).pack(side=tk.LEFT)

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



        if self._Figure is None:
            tk.Label(self.graph_container,
                     text="matplotlib not installed.\nRun:  pip install matplotlib",
                     font=("Segoe UI", 12), fg=COLOR_RED, bg="#f5f5f5",
                     justify=tk.CENTER).grid(row=0, column=0)

    def _on_tab_changed(self, event=None):  # noqa: ARG002
        selected = self.notebook.select()

        if selected == str(self.tab_graph):
            if self._samples_by_pv:
                self._plot_graph()

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
            self.lbl_graph_info.config(text="No archive data in selected range.", fg=COLOR_GRAY)
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

        
        fig.subplots_adjust(
            left=0.08,
            right=0.98,
            top=0.95,
            bottom=0.12
        )

        # Axes fraction step = figure step / axes-width-in-figure-coords



        ax = fig.add_subplot(111)

        main_ax = ax
        axes = [ax]

        self._graph_spine_xpos = [(0.0, "left")]

        import matplotlib.dates as mdates

        def _moving_avg(vals, w):
            if w <= 1 or len(vals) < w:
                return vals
            padded = vals[:w-1][::-1] + vals
            return [sum(padded[j:j+w]) / w for j in range(len(vals))]

        all_times = []
        self._graph_lines = []
        self._graph_pvs   = []
        self._graph_raw   = []

        for i, pv in enumerate(numeric_pvs):
            
            m = re.search(r"CHL03-(\d{3})", pv)

            if not m:
                continue

            chiller_num = int(m.group(1))

            if not self._visible_chillers[chiller_num].get():
                continue
            self._graph_pvs.append(pv)
            pv_setting = self._get_pv_default_settings(pv, i)
            ax = main_ax

            pairs = []

            for ts_ns, row_dict in self._table_rows:
                if pv not in row_dict:
                    continue

                value, _units = row_dict[pv]

                if not isinstance(value, (int, float)):
                    continue

                pairs.append((ts_ns, value))

            MAX_GRAPH_POINTS = 30000

            if len(pairs) > MAX_GRAPH_POINTS:
                step = max(1, len(pairs) // MAX_GRAPH_POINTS)
                pairs = pairs[::step]

            times = [datetime.fromtimestamp(ts / 1e9, tz=timezone.utc) for ts, v in pairs]
            values = [float("nan") if v is None else v for ts, v in pairs]
            all_times.extend(times)
            color = colors[(chiller_num - 1) % len(colors)]
            short  = shorten_pv_name(pv)


            line_color  = pv_setting.get("color", color)
            line_width  = pv_setting.get("width", None)
            pv_smooth   = pv_setting.get("smooth", None)
            disp_name = disp_name = CHILLER_NAMES.get(
                chiller_num,
                f"CH{chiller_num}"
                f" ({self._graph_mode.get()})"
            )
            visible     = pv_setting.get("show", True)
            eff_smooth  = pv_smooth if pv_smooth is not None else 1

            import matplotlib.dates as _mdates_inner
            times_num = _mdates_inner.date2num(times)
            self._graph_raw.append((times_num, values))

            if eff_smooth > 1 and len(values) >= eff_smooth:
                # Raw data: thin + transparent, step style
                lw_raw = (line_width * 0.5) if line_width is not None else 0.8
                raw_line, = main_ax.plot(times, values, color=line_color,
                                    linewidth=lw_raw, alpha=0.3, marker=None,
                                    drawstyle="steps-post", zorder=1)
                raw_line.set_visible(visible)
                # Smoothed trend: solid, step style
                smoothed = _moving_avg(values, eff_smooth)
                lw_sm = line_width if line_width is not None else 1.8
                sm_line, = main_ax.plot(times, smoothed, color=line_color,
                                   linewidth=lw_sm, marker=None,
                                   drawstyle="steps-post",
                                   label=f"{disp_name} (avg {eff_smooth})", zorder=2)
                sm_line.set_visible(visible)
                self._graph_lines.append([raw_line, sm_line])
            else:
                lw = line_width if line_width is not None else 1.2
                line, = main_ax.plot(times, values, color=line_color, linewidth=lw,
                                marker="." if len(times) < 200 else None, markersize=3,
                                drawstyle="steps-post",
                                label=disp_name, zorder=2)
                line.set_visible(visible)
                self._graph_lines.append([line])


            from matplotlib.ticker import AutoMinorLocator as _AutoMinorLocator
            ax.yaxis.set_minor_locator(_AutoMinorLocator(5))
            ax.tick_params(axis="y", which="minor", length=3, labelsize=0)
            ax.grid(self._show_grid.get())

            # Apply per-PV Y axis limits if set
            ymin_pv = pv_setting.get("ymin")
            ymax_pv = pv_setting.get("ymax")
            auto_scale = pv_setting.get("auto_scale", True)
            if not auto_scale and (ymin_pv is not None or ymax_pv is not None):
                ax.set_ylim(ymin_pv, ymax_pv)

        # Smart x-axis formatter
        ax0 = axes[0]

        ylabel_map = {
            "Flow_GPM": "Flow [GPM]",
            "Temp": "Temperature",
        }

        ax0.set_ylabel(
            ylabel_map.get(
                self._graph_mode.get(),
                self._graph_mode.get()
            )
        )

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

        from matplotlib.ticker import FuncFormatter, AutoMinorLocator
        from matplotlib.dates import AutoDateLocator
        from matplotlib.dates import ConciseDateFormatter

        t_min_local = t_min.astimezone(TZ_PRAGUE) if total_seconds > 0 else datetime.now(TZ_PRAGUE)
        t_max_local = t_max.astimezone(TZ_PRAGUE) if total_seconds > 0 else t_min_local
        same_day = t_min_local.date() == t_max_local.date()

        locator = AutoDateLocator()

        ax0.xaxis.set_major_locator(locator)

        ax0.xaxis.set_major_formatter(
            ConciseDateFormatter(locator)
        )

        if same_day:
            ax0.set_xlabel(f"Time (Prague)  {t_min_local.strftime('%Y-%m-%d')}", fontsize=_fsize)
        else:
            ax0.set_xlabel("Time (Prague)", fontsize=_fsize)

        if total_seconds > 0:
            pad = timedelta(seconds=max(total_seconds * 0.02, 5))
            ax0.set_xlim(t_min - pad, t_max + pad)
        ax0.tick_params(axis="x", which="major", labelsize=_fsize, rotation=0)
        ax0.tick_params(axis="x", which="minor", length=3, labelsize=0)

        if self._show_legend.get():
            ax0.legend(
                loc="upper left",
                fontsize=max(7, _fsize - 2),
                framealpha=0.9,
                ncol=2
            )

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

        points = sum(
            len(self._samples_by_pv.get(pv, []))
            for pv in self._graph_pvs
        )

        self.lbl_graph_info.config(
            text=f"{len(self._graph_pvs)} chillers | {points} points",
            fg=COLOR_GREEN
        )
        if not self.lbl_graph_stats.has_stats:
            self.lbl_graph_stats.config(text="Drag on the graph to select a region for stats.", fg=COLOR_GRAY)

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

        sy = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.tree.yview)
        sx = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.tree.xview)
        sy.grid(row=0, column=1, sticky=tk.NS)
        sx.grid(row=1, column=0, sticky=tk.EW)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)

        self._tree_menu = tk.Menu(self.root, tearoff=0)
        self._tree_menu.add_command(label="Copy cell",  command=self._copy_cell)
        self._tree_menu.add_command(label="Copy row",   command=self._copy_row)
        self._tree_menu.add_separator()
        self._tree_menu.add_command(label="Delete row", command=self._delete_selected_row)
        self._tree_menu.add_command(label="Open image", command=self._open_image_from_selection)

        self.tree.bind("<Button-3>",       self._on_tree_right_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        self.tree.bind("<Motion>",          self._on_tree_motion)
        self.tree.bind("<Delete>", lambda _: self._delete_selected_row())

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
            
    def _refresh_archive_view(self):
        if not ARCHIVE_FILE.exists():
            self.lbl_status.config(text="Archive CSV not found.", fg=COLOR_RED)
            return

        rows = []

        start_dt = self._dt_from.replace(tzinfo=TZ_PRAGUE)
        end_dt = self._dt_to.replace(tzinfo=TZ_PRAGUE)

        with open(ARCHIVE_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for r in reader:
                try:
                    dt = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=TZ_PRAGUE)
                except Exception:
                    continue

                if start_dt <= dt <= end_dt:
                    r["_datetime_obj"] = dt
                    rows.append(r)

        self._archive_rows_to_graph_data(rows)
        self._populate_archive_table(rows)

        if self.notebook.select() == str(self.tab_graph):
            self._plot_graph()

        self.lbl_status.config(
            text=f"Archive loaded: {len(rows)} rows.",
            fg=COLOR_GREEN
        )

    def _populate_archive_table(self, rows: list[dict]):
        for item in self.tree.get_children():
            self.tree.delete(item)

        columns = list(ARCHIVE_COLUMNS)
        self.tree.configure(columns=columns)

        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=110, anchor=tk.W, stretch=False)

        for r in rows:
            self.tree.insert("", tk.END, values=[r.get(c, "") for c in columns])

        self.lbl_table_info.config(text=f"{len(rows)} archive rows shown.")

    def _archive_rows_to_graph_data(self, rows: list[dict]):
        mode = self._graph_mode.get()

        key_map = {
            "Flow_GPM": "flow",
            "Temp": "temp",
        }

        archive_key = key_map.get(mode)
        if not archive_key:
            return

        pv_order = [
            f"L3-UTIL-CHL03-{ch:03d}:{mode}"
            for ch in range(1, 7)
        ]

        table_rows = []
        samples_by_pv = {pv: [] for pv in pv_order}

        for r in rows:
            dt = r.get("_datetime_obj")
            if dt is None:
                continue

            ts_ns = int(dt.astimezone(timezone.utc).timestamp() * 1e9)
            row_dict = {}

            for ch in range(1, 7):
                pv = f"L3-UTIL-CHL03-{ch:03d}:{mode}"
                col = f"ch{ch}_{archive_key}"

                raw = r.get(col, "")
                if raw in ("", None):
                    continue

                try:
                    value = float(str(raw).replace(",", "."))
                except Exception:
                    continue

                units = self._archive_units_for_mode(mode)

                row_dict[pv] = (value, units)
                samples_by_pv[pv].append((ts_ns, value, units))

            if row_dict:
                table_rows.append((ts_ns, row_dict))

        self._pv_order = pv_order
        self._table_rows = table_rows
        self._samples_by_pv = samples_by_pv

    def _archive_units_for_mode(self, mode: str) -> str:
        return {
            "Flow_GPM": "GPM",
            "Temp": "°C",
        }.get(mode, "")
        

    def _open_time_window_dialog(self):
        """Open a clear Start/End time-window dialog with clickable calendars."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Select time window")
        dlg.resizable(True, True)
        dlg.minsize(920, 540)
        dlg.transient(self.root)
        dlg.grab_set()

        def _make_abs_frame(parent, init_dt: datetime, status_var: tk.StringVar):
            import calendar as _cal

            frm = tk.Frame(parent)

            year_var = tk.IntVar(value=init_dt.year)
            month_var = tk.IntVar(value=init_dt.month)
            day_var = tk.IntVar(value=init_dt.day)
            hour_var = tk.StringVar(value=f"{init_dt.hour:02d}")
            min_var = tk.StringVar(value=f"{init_dt.minute:02d}")
            sec_var = tk.StringVar(value=f"{init_dt.second:02d}")

            MONTHS = [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"
            ]
            DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

            nav = tk.Frame(frm)
            nav.pack(fill=tk.X, padx=8, pady=(8, 4))

            tk.Button(nav, text="<<", width=3, relief=tk.RAISED,
                    command=lambda: _shift_year(-1)).pack(side=tk.LEFT)

            tk.Button(nav, text="<", width=3, relief=tk.RAISED,
                    command=lambda: _shift_month(-1)).pack(side=tk.LEFT, padx=3)

            lbl_my = tk.Label(nav, font=FONT_HEADER, width=18, anchor=tk.CENTER)
            lbl_my.pack(side=tk.LEFT, expand=True)

            tk.Button(nav, text=">", width=3, relief=tk.RAISED,
                    command=lambda: _shift_month(1)).pack(side=tk.RIGHT, padx=3)

            tk.Button(nav, text=">>", width=3, relief=tk.RAISED,
                    command=lambda: _shift_year(1)).pack(side=tk.RIGHT)

            cal_frm = tk.Frame(frm)
            cal_frm.pack(padx=8, pady=(2, 6))

            for c, dn in enumerate(DAY_NAMES):
                tk.Label(
                    cal_frm,
                    text=dn,
                    font=FONT_NORMAL,
                    width=4,
                    fg=COLOR_RED if c >= 5 else "black",
                    anchor=tk.CENTER
                ).grid(row=0, column=c, padx=1, pady=1)

            day_btns = []

            for r in range(6):
                for c in range(7):
                    b = tk.Button(
                        cal_frm,
                        text="",
                        width=4,
                        relief=tk.FLAT,
                        font=FONT_NORMAL,
                        command=lambda r=r, c=c: _on_day(r, c)
                    )
                    b.grid(row=r + 1, column=c, padx=1, pady=1)
                    day_btns.append(b)

            time_frm = tk.Frame(frm)
            time_frm.pack(padx=8, pady=(4, 8))

            tk.Label(time_frm, text="Time:", font=FONT_HEADER).pack(side=tk.LEFT, padx=(0, 6))

            for var, lo, hi in [
                (hour_var, 0, 23),
                (min_var, 0, 59),
                (sec_var, 0, 59),
            ]:
                sb = tk.Spinbox(
                    time_frm,
                    textvariable=var,
                    from_=lo,
                    to=hi,
                    width=4,
                    font=FONT_MONO,
                    format="%02.0f",
                    command=lambda: _refresh_status()
                )
                sb.pack(side=tk.LEFT, padx=1)
                var.trace_add("write", lambda *_: _refresh_status())

            def _safe_int(var: tk.StringVar, lo: int, hi: int) -> int:
                try:
                    value = int(var.get().strip() or "0")
                except ValueError:
                    value = lo
                return max(lo, min(hi, value))

            def get_dt():
                return datetime(
                    year_var.get(),
                    month_var.get(),
                    day_var.get(),
                    _safe_int(hour_var, 0, 23),
                    _safe_int(min_var, 0, 59),
                    _safe_int(sec_var, 0, 59),
                )

            def _refresh_status():
                try:
                    status_var.set(get_dt().strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    status_var.set("Invalid date/time")

            def _draw():
                y, m, d = year_var.get(), month_var.get(), day_var.get()
                lbl_my.config(text=f"{MONTHS[m - 1]} {y}")

                first_wd, n_days = _cal.monthrange(y, m)

                for i, b in enumerate(day_btns):
                    dn2 = i - first_wd + 1
                    col = i % 7

                    if 1 <= dn2 <= n_days:
                        selected = dn2 == d
                        b.config(
                            text=str(dn2),
                            state=tk.NORMAL,
                            bg=COLOR_BLUE if selected else "SystemButtonFace",
                            fg="white" if selected else (COLOR_RED if col >= 5 else "black"),
                            relief=tk.SOLID if selected else tk.FLAT
                        )
                    else:
                        b.config(
                            text="",
                            state=tk.DISABLED,
                            bg="SystemButtonFace",
                            relief=tk.FLAT
                        )

                _refresh_status()

            def _on_day(r, c):
                y, m = year_var.get(), month_var.get()
                first_wd, n_days = _cal.monthrange(y, m)
                dn2 = r * 7 + c - first_wd + 1

                if 1 <= dn2 <= n_days:
                    day_var.set(dn2)
                    _draw()

            def _shift_month(delta):
                y, m = year_var.get(), month_var.get()
                m += delta

                if m < 1:
                    m = 12
                    y -= 1
                elif m > 12:
                    m = 1
                    y += 1

                year_var.set(y)
                month_var.set(m)

                _, n_days = _cal.monthrange(y, m)
                if day_var.get() > n_days:
                    day_var.set(n_days)

                _draw()

            def _shift_year(delta):
                y = year_var.get() + delta
                m = month_var.get()

                year_var.set(y)

                _, n_days = _cal.monthrange(y, m)
                if day_var.get() > n_days:
                    day_var.set(n_days)

                _draw()

            def _set_now():
                now = datetime.now()
                year_var.set(now.year)
                month_var.set(now.month)
                day_var.set(now.day)
                hour_var.set(f"{now.hour:02d}")
                min_var.set(f"{now.minute:02d}")
                sec_var.set(f"{now.second:02d}")
                _draw()

            quick_row = tk.Frame(frm)
            quick_row.pack(padx=8, pady=(0, 8))

            tk.Button(quick_row, text="Now", command=_set_now).pack(side=tk.LEFT, padx=3)
            tk.Button(quick_row, text="00:00", command=lambda: [hour_var.set("00"), min_var.set("00"), sec_var.set("00")]).pack(side=tk.LEFT, padx=3)
            tk.Button(quick_row, text="12:00", command=lambda: [hour_var.set("12"), min_var.set("00"), sec_var.set("00")]).pack(side=tk.LEFT, padx=3)

            _draw()

            return frm, get_dt

        def _make_rel_frame(parent, status_var: tk.StringVar):
            frm = tk.Frame(parent)

            def _valid_number(s):
                return s == "" or s.isdigit()

            vcmd = (frm.register(_valid_number), "%P")

            fields_frame = tk.Frame(frm)
            fields_frame.pack(fill=tk.X, padx=10, pady=(12, 8))

            years_var = tk.StringVar(value="0")
            months_var = tk.StringVar(value="0")
            days_var = tk.StringVar(value="0")
            hours_var = tk.StringVar(value="0")
            minutes_var = tk.StringVar(value="0")
            seconds_var = tk.StringVar(value="0")

            fields = [
                ("Years:", years_var, 0, 0),
                ("Months:", months_var, 1, 0),
                ("Days:", days_var, 2, 0),
                ("Hours:", hours_var, 0, 2),
                ("Minutes:", minutes_var, 1, 2),
                ("Secs:", seconds_var, 2, 2),
            ]

            for label, var, row, col in fields:
                tk.Label(fields_frame, text=label, font=FONT_NORMAL).grid(
                    row=row, column=col, sticky=tk.W, padx=(0, 4), pady=3
                )
                tk.Spinbox(
                    fields_frame,
                    textvariable=var,
                    from_=0,
                    to=9999,
                    width=6,
                    font=FONT_MONO,
                    validate="key",
                    validatecommand=vcmd,
                    command=lambda: _refresh_status()
                ).grid(row=row, column=col + 1, sticky=tk.W, pady=3)

                var.trace_add("write", lambda *_: _refresh_status())

            before_var = tk.BooleanVar(value=True)

            def _int(var: tk.StringVar) -> int:
                try:
                    return int(var.get().strip() or "0")
                except ValueError:
                    return 0

            def get_dt():
                total_seconds = (
                    _int(years_var) * 365 * 86400
                    + _int(months_var) * 30 * 86400
                    + _int(days_var) * 86400
                    + _int(hours_var) * 3600
                    + _int(minutes_var) * 60
                    + _int(seconds_var)
                )

                now = datetime.now()
                if before_var.get():
                    return now - timedelta(seconds=total_seconds)
                return now + timedelta(seconds=total_seconds)

            def _refresh_status():
                try:
                    status_var.set(get_dt().strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    status_var.set("Invalid relative time")

            preset_row = tk.Frame(frm)
            preset_row.pack(fill=tk.X, padx=10, pady=(8, 8))

            def _set_relative(hours=0, days=0):
                years_var.set("0")
                months_var.set("0")
                days_var.set(str(days))
                hours_var.set(str(hours))
                minutes_var.set("0")
                seconds_var.set("0")
                before_var.set(True)
                _refresh_status()

            tk.Button(preset_row, text="12 h", command=lambda: _set_relative(hours=12)).pack(side=tk.LEFT, padx=3)
            tk.Button(preset_row, text="1 Day", command=lambda: _set_relative(days=1)).pack(side=tk.LEFT, padx=3)
            tk.Button(preset_row, text="7 Days", command=lambda: _set_relative(days=7)).pack(side=tk.LEFT, padx=3)
            tk.Button(preset_row, text="30 Days", command=lambda: _set_relative(days=30)).pack(side=tk.LEFT, padx=3)

            bottom_row = tk.Frame(frm)
            bottom_row.pack(fill=tk.X, padx=10, pady=(6, 8))

            tk.Checkbutton(
                bottom_row,
                text="Before now",
                variable=before_var,
                font=FONT_NORMAL,
                command=_refresh_status
            ).pack(side=tk.LEFT)

            tk.Button(bottom_row, text="Now", command=lambda: _set_relative()).pack(side=tk.RIGHT)

            _refresh_status()

            return frm, get_dt

        outer = tk.Frame(dlg)
        outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        get_from_dt = [lambda: self._dt_from]
        get_to_dt = [lambda: self._dt_to]

        for col, (which, title, init_dt) in enumerate([
            ("from", "START TIME", self._dt_from),
            ("to", "END TIME", self._dt_to),
        ]):
            col_frame = tk.LabelFrame(
                outer,
                text=title,
                font=FONT_HEADER,
                fg=COLOR_BLUE,
                padx=10,
                pady=10
            )
            col_frame.grid(row=0, column=col, sticky=tk.NSEW, padx=(0, 20) if col == 0 else (0, 0))

            nb = ttk.Notebook(col_frame)
            nb.pack(fill=tk.BOTH, expand=True)

            abs_tab = tk.Frame(nb)
            rel_tab = tk.Frame(nb)

            nb.add(abs_tab, text="  Absolute  ")
            nb.add(rel_tab, text="  Relative  ")

            status_row = tk.Frame(col_frame)
            status_row.pack(fill=tk.X, pady=(8, 0))

            tk.Label(
                status_row,
                text="Selected:",
                font=FONT_NORMAL,
                fg=COLOR_GRAY
            ).pack(side=tk.LEFT, padx=(0, 6))

            status_var = tk.StringVar(value=init_dt.strftime("%Y-%m-%d %H:%M:%S"))

            tk.Label(
                status_row,
                textvariable=status_var,
                font=FONT_MONO,
                fg="black",
                bg="white",
                relief=tk.SUNKEN,
                padx=6,
                pady=3,
                width=22,
                anchor=tk.W
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            abs_frm, get_abs_dt = _make_abs_frame(abs_tab, init_dt, status_var)
            abs_frm.pack(fill=tk.BOTH, expand=True)

            rel_frm, get_rel_dt = _make_rel_frame(rel_tab, status_var)
            rel_frm.pack(fill=tk.BOTH, expand=True)

            def _make_getter(nb_ref, get_abs, get_rel):
                def _get():
                    if nb_ref.index(nb_ref.select()) == 0:
                        return get_abs()
                    return get_rel()
                return _get

            if which == "from":
                get_from_dt[0] = _make_getter(nb, get_abs_dt, get_rel_dt)
            else:
                get_to_dt[0] = _make_getter(nb, get_abs_dt, get_rel_dt)

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        button_row = tk.Frame(dlg)
        button_row.pack(fill=tk.X, padx=20, pady=(0, 16))

        def _ok():
            try:
                new_from = get_from_dt[0]()
                new_to = get_to_dt[0]()
            except Exception as e:
                messagebox.showerror("Invalid time", str(e), parent=dlg)
                return

            if new_to <= new_from:
                messagebox.showerror("Invalid range", "END TIME must be later than START TIME.", parent=dlg)
                return

            self._dt_from = new_from
            self._dt_to = new_to

            self._refresh_time_info_labels()
            self._refresh_archive_view()

            self.config["time_from"] = self._dt_from.strftime("%Y-%m-%d %H:%M:%S")
            self.config["time_to"] = self._dt_to.strftime("%Y-%m-%d %H:%M:%S")
            save_config(self.config)

            self.lbl_status.config(
                text=f"Time window set: {self._dt_from:%Y-%m-%d %H:%M:%S} -> {self._dt_to:%Y-%m-%d %H:%M:%S}",
                fg=COLOR_BLUE
            )

            self._refresh_archive_view()
            dlg.destroy()
            

        tk.Button(
            button_row,
            text="Cancel",
            relief=tk.RAISED,
            padx=16,
            pady=4,
            command=dlg.destroy
        ).pack(side=tk.RIGHT)

        tk.Button(
            button_row,
            text="OK",
            relief=tk.RAISED,
            padx=22,
            pady=4,
            bg=COLOR_BLUE,
            fg="white",
            command=_ok
        ).pack(side=tk.RIGHT, padx=(0, 8))

        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        win_w = 920
        win_h = 540
        dlg.geometry(f"{win_w}x{win_h}+{(sw - win_w) // 2}+{(sh - win_h) // 2}")

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
    # Data loading
    # -------------------------------------------------------------------------

    def _update_archive_from_cpva(self):
        last_dt = self._get_last_archive_datetime()

        if last_dt is None:
            start_dt = datetime(2026, 1, 1, tzinfo=TZ_PRAGUE)
        else:
            start_dt = last_dt + timedelta(seconds=1)

        end_dt = datetime.now(TZ_PRAGUE)

        if end_dt <= start_dt:
            self.lbl_status.config(text="Archive is already up to date.", fg=COLOR_GRAY)
            return

        self.lbl_status.config(
            text=f"Updating archive from {start_dt:%Y-%m-%d %H:%M} to {end_dt:%Y-%m-%d %H:%M}...",
            fg="orange"
        )

        self._log("")
        self._log("==============================================")
        self._log("Archive update started")
        self._log(f"From: {start_dt:%Y-%m-%d %H:%M:%S}")
        self._log(f"To:   {end_dt:%Y-%m-%d %H:%M:%S}")
        self._log("==============================================")

        threading.Thread(
            target=self._update_archive_worker,
            args=(start_dt, end_dt),
            daemon=True
        ).start()

    def _update_archive_worker(self, start_dt: datetime, end_dt: datetime):
        try:
            self._update_archive_worker_impl(start_dt, end_dt)
        except Exception as e:
            self.root.after(0, lambda: self.lbl_status.config(
                text=f"Archive update failed: {type(e).__name__}: {e}",
                fg=COLOR_RED
            ))
            self._log(f"Archive update failed: {type(e).__name__}: {e}")

    def _fetch_scheduled_average_with_pump_samples(
        self,
        pv_name: str,
        pump_samples: list[tuple[int, float]],
        start_ns: int,
        end_ns: int,
        timeout: float = CPVA_HTTP_TIMEOUT,
    ):
        MINUTE_NS = 60 * 1_000_000_000
        CHECK_BEFORE_NS = 10 * MINUTE_NS
        DAY_TIMES = [(9, 0), (13, 30), (18, 0)]

        def pump_is_on_at(ts_ns: int) -> bool:
            state = 0.0
            for pts, val in pump_samples:
                if pts > ts_ns:
                    break
                state = val
            return state == 1.0

        result = []

        start_dt = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).astimezone(TZ_PRAGUE)
        end_dt = datetime.fromtimestamp(end_ns / 1e9, tz=timezone.utc).astimezone(TZ_PRAGUE)

        day = start_dt.date()
        end_day = end_dt.date()

        while day <= end_day:
            for hour, minute in DAY_TIMES:
                dt_local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ_PRAGUE)
                ts_ns = int(dt_local.astimezone(timezone.utc).timestamp() * 1e9)

                if ts_ns < start_ns or ts_ns >= end_ns:
                    continue

                if not (pump_is_on_at(ts_ns - CHECK_BEFORE_NS) and pump_is_on_at(ts_ns)):
                    continue

                raw = cpva_fetch_samples(
                    pv_name,
                    ts_ns,
                    min(ts_ns + MINUTE_NS, end_ns),
                    timeout
                )

                vals = []

                for s in raw:
                    v = cpva_decode_value(s)
                    if isinstance(v, (int, float)):
                        vals.append(float(v))

                if vals:
                    result.append((ts_ns, sum(vals) / len(vals)))

            day += timedelta(days=1)

        return result    
    
    def _update_archive_worker_impl(self, start_dt: datetime, end_dt: datetime):
        start_ns = int(start_dt.astimezone(timezone.utc).timestamp() * 1e9)
        end_ns = int(end_dt.astimezone(timezone.utc).timestamp() * 1e9)

        timeout = float(self.config.get("http_timeout", CPVA_HTTP_TIMEOUT))

        rows_by_ts: dict[int, dict] = {}

        groups = {
            "flow": "Flow_GPM",
            "temp": "Temp",
        }

        for ch in range(1, 7):
            chiller_id = f"{ch:03d}"
            pump_pv = f"L3-UTIL-CHL03-{chiller_id}:PumpON"

            self._log(f"Fetching PumpON for chiller {ch}...")

            pump_raw = cpva_fetch_samples_chunked(
                pump_pv,
                start_ns - 24 * 3600 * 1_000_000_000,
                end_ns,
                timeout=timeout,
                max_workers=8,
                log_fn=self._log,
            )

            pump_samples = []

            for s in pump_raw:
                ts = s.get("time")
                if ts is None:
                    continue
                try:
                    pump_samples.append((int(ts), float(cpva_decode_value(s))))
                except Exception:
                    pass

            pump_samples.sort()

            self._log(f"Chiller {ch}: PumpON samples = {len(pump_samples)}")

            for archive_key, pv_suffix in groups.items():
                pv = f"L3-UTIL-CHL03-{chiller_id}:{pv_suffix}"

                self._log(f"  Fetching {pv}...")

                samples = self._fetch_scheduled_average_with_pump_samples(
                    pv_name=pv,
                    pump_samples=pump_samples,
                    start_ns=start_ns,
                    end_ns=end_ns,
                    timeout=timeout,
                )

                self._log(f"  OK {pv}: {len(samples)} values")

                col = f"ch{ch}_{archive_key}"

                self._log(
                        f"    {pv}: generated {len(samples)} archive points"
                    )

                for ts_ns, value in samples:
                    rows_by_ts.setdefault(ts_ns, {})[col] = value

        if not rows_by_ts:
            self._log("No new archive rows to append.")
            self.root.after(0, lambda: self.lbl_status.config(
                text="No new archive data found.",
                fg=COLOR_GRAY
            ))
            return

        existing_datetimes = set()

        if ARCHIVE_FILE.exists():
            with open(ARCHIVE_FILE, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    existing_datetimes.add(r.get("datetime", ""))

        append_rows = []

        for ts_ns in sorted(rows_by_ts):
            dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(TZ_PRAGUE)
            dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

            if dt_str in existing_datetimes:
                continue

            out = {c: "" for c in ARCHIVE_COLUMNS}
            out["datetime"] = dt_str
            out["source"] = "cpva_update"

            for key, value in rows_by_ts[ts_ns].items():
                out[key] = f"{value:.2f}"

            append_rows.append(out)

        file_exists = ARCHIVE_FILE.exists()

        with open(ARCHIVE_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ARCHIVE_COLUMNS)

            if not file_exists:
                writer.writeheader()

            writer.writerows(append_rows)

        self._log(f"Archive update done. Appended rows: {len(append_rows)}")

        self.root.after(
            0,
            lambda: self._set_display_range_preset("1Y")
        )
        self.root.after(0, lambda: self.lbl_status.config(
            text=f"Archive update done. Added {len(append_rows)} rows.",
            fg=COLOR_GREEN
        ))


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

        all_rows: list[tuple[list[str], bool]] = []
        for ts_ns, row_dict in self._table_rows:
            vals = [ns_to_local_str(ts_ns)]
            for pv in pv_order:
                if pv in row_dict:
                    v, u = row_dict[pv]
                    vals += [self._format_value(v), u]
                else:
                    vals += ["", ""]
            
            all_rows.append((vals, True))
            for c, cell in zip(columns, vals):
                w = self._text_px(cell)
                if w > col_widths[c]:
                    col_widths[c] = w

        for col in columns:
            self.tree.heading(col, text=display[col])
            w = min(col_widths[col] + 16, 600)
            self.tree.column(col, width=max(w, 40), minwidth=40,
                             anchor=tk.W, stretch=False)

        MAX_TABLE_ROWS = 100000

        shown_rows = all_rows[-MAX_TABLE_ROWS:]

        self.tree.tag_configure("condition_fail", foreground=COLOR_RED)

        for vals, row_ok in shown_rows:
            if row_ok:
                self.tree.insert("", tk.END, values=vals)
            else:
                self.tree.insert("", tk.END, values=vals, tags=("condition_fail",))

        if len(all_rows) > MAX_TABLE_ROWS:
            self.lbl_status.config(
                text=f"Table shows last {MAX_TABLE_ROWS} of {len(all_rows)} rows. Export CSV still uses all rows.",
                fg=COLOR_BLUE
            )

        self.lbl_table_info.config(
            text=f"{len(self._table_rows)} rows total | showing {min(len(all_rows), MAX_TABLE_ROWS)} | {len(pv_order)} PV(s)"
        )

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

    def _delete_selected_row(self):
        """Delete selected row from table data."""

        sel = self.tree.selection()

        if not sel:
            return

        item_id = sel[0]

        values = self.tree.item(item_id, "values")

        if not values:
            return

        ts_str = values[0]

        # najdi odpovídající timestamp
        delete_index = None

        for i, (ts_ns, _row_dict) in enumerate(self._table_rows):
            if ns_to_local_str(ts_ns) == ts_str:
                delete_index = i
                break

        if delete_index is None:
            return

        # smaž z dat
        self._table_rows.pop(delete_index)

        # smaž i z unfiltered rows pokud existuje
        if delete_index < len(self._table_rows_unfiltered):
            self._table_rows_unfiltered.pop(delete_index)

        # refresh table
        self._populate_table(self._pv_order)

        # refresh graph
        if self.notebook.select() == str(self.tab_graph):
            self._plot_graph()


        self.lbl_status.config(
            text=f"Row deleted. Remaining rows: {len(self._table_rows)}",
            fg=COLOR_RED
        )

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
