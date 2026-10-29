# tc.py
from tkinter import (Tk, filedialog, Toplevel, Button, Label, messagebox,
                     BooleanVar, Checkbutton, StringVar, ttk, Frame,
                     Scrollbar, END, BOTH, RIGHT, Y, LEFT, RIDGE)
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import time
import concurrent.futures

# --------- filename rules ----------
FINAL_RE = re.compile(r"\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d{6}$")
SOURCE_RE = re.compile(r"(\d+)$")

PRAGUE = ZoneInfo("Europe/Prague")

# Sanity check – roky 2000–2100 v nanosekundách
TS_MIN_NS = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
TS_MAX_NS = int(datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


# --------- helpers ----------
def _app_version():
    """Version of the running build, e.g. "v1.4.3", or "" when run from source.
    The builder names every exe "<Program> vX.Y.Z.exe", so the exe's own file
    name is the one place that always tells the truth."""
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""


APP_TITLE = (f"Time Converter {_app_version()}" if _app_version()
             else "Time Converter (source)")


def _fmt_hms(seconds):
    if seconds is None or seconds != seconds or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _center_window(win, w, h):
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"{w}x{h}+{x}+{y}")


# --------- filename parsing ----------
def split_stem(stem: str):
    """
    Rozloží stem na (prefix, raw_timestamp_str).
    prefix  = vše před posledním číslem (bez trailing underscore)
    raw_ts  = samotné číslo jako string
    Vrátí (prefix, raw_ts) nebo (stem, None) pokud číslo nenajde.
    """
    stem_clean = stem.replace("-_-", "_").replace("_-_", "_")
    m = SOURCE_RE.search(stem_clean)
    if not m:
        return stem_clean, None
    prefix = stem_clean[:m.start(1)].rstrip("_")
    return prefix, m.group(1)


# --------- timestamp conversion ----------
def convert_timestamp(ns: int, use_prague_time: bool) -> str:
    dt_utc = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    dt = dt_utc.astimezone(PRAGUE) if use_prague_time else dt_utc
    return dt.strftime("%Y_%m_%d--%H_%M_%S__%f")

def orig_ts_to_utc(orig_ts_str: str) -> str:
    """Převede UNIX ns string na čitelný UTC čas."""
    try:
        ns = int(orig_ts_str)
        dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
        return dt.strftime("%Y_%m_%d--%H_%M_%S__%f")
    except Exception:
        return "—"


# --------- renaming logic ----------
def build_new_name(stem: str, use_prague_time: bool):
    stem_clean = stem.replace("-_-", "_").replace("_-_", "_")

    if FINAL_RE.search(stem_clean):
        return None, "already_converted"

    m = SOURCE_RE.search(stem_clean)
    if not m:
        return None, "no_trailing_number"

    ns = int(m.group(1))
    if not (TS_MIN_NS <= ns <= TS_MAX_NS):
        return None, "invalid_timestamp"

    time_str = convert_timestamp(ns, use_prague_time)
    new_stem = stem_clean[:m.start(1)] + time_str
    return new_stem, None


# --------- UI: intro ----------
def show_intro_and_get_options(parent):
    result = {"use_prague_time": True, "show_report": True, "ok": False}

    def on_ok():
        result["use_prague_time"] = prague_var.get()
        result["show_report"]     = report_var.get()
        result["ok"] = True
        win.destroy()

    def on_cancel():
        win.destroy()

    win = Toplevel(parent)
    # The program's own name, not a second one: the root window is withdrawn,
    # so this is the window the title bar and the taskbar button are read from.
    win.title(APP_TITLE)
    win.resizable(False, False)

    Label(
        win,
        text=(
            "How to use:\n"
            "1) Click OK.\n"
            "2) Select the files you want to convert.\n"
            "3) Choose the target folder.\n"
            "4) The program will COPY the files there with converted timestamps.\n\n"
            "Notes:\n"
            "- Files already in final format will be skipped.\n"
            "- Files without a trailing UNIX timestamp will be skipped.\n"
            "- Files with an out-of-range timestamp will be skipped.\n"
            "- If a file with the same name already exists in the target folder,\n"
            "  it will be overwritten."
        ),
        justify="left",
        padx=16,
        pady=12
    ).pack()

    prague_var = BooleanVar(value=True)
    Checkbutton(
        win,
        text="Convert to local time (Europe - Prague)?",
        variable=prague_var
    ).pack(anchor="w", padx=16, pady=(0, 4))

    report_var = BooleanVar(value=True)
    Checkbutton(
        win,
        text="Show detailed report after copying?",
        variable=report_var
    ).pack(anchor="w", padx=16, pady=(0, 10))

    btn_frame = Frame(win)
    btn_frame.pack(pady=(0, 12))
    Button(btn_frame, text="OK", width=12, command=on_ok).pack(side="left", padx=6)
    Button(btn_frame, text="Cancel", width=12, command=on_cancel).pack(side="left", padx=6)

    win.grab_set()
    win.wait_window()
    return result if result["ok"] else None


# --------- sdílený tabulkový widget ----------
def _make_file_table(parent, rows, height=22):
    """
    rows: list of dict s klíči: prefix, orig_ts, new_ts, status, tag
    tag: "copy" | "overwrite" | "skip" | "warn" | "error"
    """
    frame = Frame(parent)

    cols = ("prefix", "orig_ts", "orig_utc", "new_ts", "status")
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=height)

    tree.heading("prefix",   text="Prefix")
    tree.heading("orig_ts",  text="Original timestamp")
    tree.heading("orig_utc", text="Orig. (UTC)")
    tree.heading("new_ts",   text="New timestamp")
    tree.heading("status",   text="Status")

    # Automatická šířka: změříme nejdelší hodnotu v každém sloupci (px ≈ znaky × 7)
    CHAR_PX    = 7
    PAD_PX     = 5
    MIN_WIDTHS = {"prefix": 110, "orig_ts": 120, "orig_utc": 130, "new_ts": 140, "status": 100}
    col_vals = {"prefix": ["Prefix"], "orig_ts": ["Original timestamp"],
            "orig_utc": ["Orig. (UTC)"],
            "new_ts": ["New timestamp"], "status": ["Status"]}
    for r in rows:
        col_vals["prefix"].append(r["prefix"])
        col_vals["orig_ts"].append(r["orig_ts"])
        col_vals["orig_utc"].append(r.get("orig_utc", ""))
        col_vals["new_ts"].append(r["new_ts"])
        col_vals["status"].append(r["status"])
    col_widths = {
        col: max(MIN_WIDTHS[col], max(len(str(v)) for v in vals) * CHAR_PX + PAD_PX)
        for col, vals in col_vals.items()
    }
    tree.column("prefix",   width=col_widths["prefix"],   anchor="w",      stretch=True)
    tree.column("orig_ts",  width=col_widths["orig_ts"],  anchor="center", stretch=False)
    tree.column("orig_utc", width=col_widths["orig_utc"], anchor="center", stretch=False)
    tree.column("new_ts",   width=col_widths["new_ts"],   anchor="center", stretch=False)
    tree.column("status",   width=col_widths["status"],   anchor="center",      stretch=False)
    # Uložíme preferovanou šířku pro výpočet velikosti okna
    frame._preferred_width = sum(col_widths.values()) + 20

    vsb = Scrollbar(frame, orient="vertical",   command=tree.yview)
    hsb = Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    frame.grid_rowconfigure(0, weight=1)
    frame.grid_columnconfigure(0, weight=1)

    # Barvy – čitelné tmavé odstíny na bílém pozadí
    tree.tag_configure("copy",      foreground="#1a6e1a")   # zelená  – bude zkopírováno
    tree.tag_configure("overwrite", foreground="#0055aa")   # modrá   – přepíše existující
    tree.tag_configure("skip",      foreground="#444444")   # tmavě šedá – přeskočeno
    tree.tag_configure("warn",      foreground="#b85c00")   # oranžová – varování
    tree.tag_configure("error",     foreground="#cc0000")   # červená – chyba

    for r in rows:
        tree.insert("", END,
                    values=(r["prefix"], r["orig_ts"], r.get("orig_utc", ""), r["new_ts"], r["status"]),
                    tags=(r["tag"],))

    return frame


# --------- dashboard summary boxy ----------
def _make_dashboard(parent, counts):
    """
    counts: list of (label, value, color_hex)
    Vykreslí čísla v rámečcích vedle sebe.
    """
    frame = Frame(parent)
    for label, value, color in counts:
        box = Frame(frame, relief=RIDGE, bd=2, padx=14, pady=8)
        box.pack(side="left", padx=6, pady=6)
        Label(box, text=str(value), font=("", 22, "bold"), fg=color).pack()
        Label(box, text=label, font=("", 8)).pack()
    return frame


# --------- row builder helpers ----------
def _plan_row(prefix, orig_ts, new_ts, will_overwrite):
    status = "will overwrite existing file" if will_overwrite else "will copy"
    tag    = "overwrite" if will_overwrite else "copy"
    return {
        "prefix": prefix, "orig_ts": orig_ts, "orig_utc": orig_ts_to_utc(orig_ts),
        "new_ts": new_ts, "status": status, "tag": tag, "will_overwrite": will_overwrite,
    }


def _skip_row(prefix, orig_ts, reason):
    labels = {
        "already_converted": "skipped – already converted",
        "no_trailing_number": "skipped – no timestamp found",
        "invalid_timestamp":  "skipped – timestamp out of range",
    }
    tags = {
        "already_converted": "skip",
        "no_trailing_number": "skip",
        "invalid_timestamp":  "warn",
    }
    return {
        "prefix": prefix, "orig_ts": orig_ts, "orig_utc": orig_ts_to_utc(orig_ts),
        "new_ts": "—", "status": labels.get(reason, f"skipped – {reason}"),
        "tag": tags.get(reason, "skip"), "will_overwrite": False,
    }


def _done_row(prefix, orig_ts, new_ts, ok, err_msg, was_overwrite):
    if not ok:
        return {
            "prefix": prefix, "orig_ts": orig_ts, "new_ts": "—",
            "status": f"error: {err_msg}", "tag": "error", "will_overwrite": False,
        }
    status = "overwritten" if was_overwrite else "copied"
    tag    = "overwrite"   if was_overwrite else "copy"
    return {
        "prefix": prefix, "orig_ts": orig_ts, "new_ts": new_ts,
        "status": status, "tag": tag, "will_overwrite": False,
    }


# --------- Preview okno ----------
def show_preview(parent, plan_rows, skip_rows):
    """
    plan_rows: list of dict – soubory, které budou zkopírovány/přepsány
    skip_rows: list of dict – soubory, které budou přeskočeny
    Vrací True = Proceed, False = Cancel.
    """
    result = {"proceed": False}

    def on_proceed():
        result["proceed"] = True
        win.destroy()

    def on_cancel():
        win.destroy()

    n_copy      = sum(1 for r in plan_rows if not r["will_overwrite"])
    n_overwrite = sum(1 for r in plan_rows if r["will_overwrite"])
    n_skip      = len(skip_rows)
    total       = n_copy + n_overwrite + n_skip

    win = Toplevel(parent)
    win.title(f"Preview — {total} file(s)")
    win.resizable(True, True)

    Label(win, text="Review the changes before copying:",
          padx=12, pady=8, anchor="w").pack(fill="x")

    _make_dashboard(win, [
        ("Will copy",      n_copy,      "#1a6e1a"),
        ("Will overwrite", n_overwrite, "#0055aa"),
        ("Will skip",      n_skip,      "#888888"),
    ]).pack(padx=12, anchor="w")

    tbl = _make_file_table(win, plan_rows + skip_rows, height=18)
    tbl.pack(fill=BOTH, expand=True, padx=12, pady=(4, 4))

    btn_frame = Frame(win)
    btn_frame.pack(pady=(4, 12))
    Button(btn_frame, text="Proceed", width=14, command=on_proceed).pack(side="left", padx=6)
    Button(btn_frame, text="Cancel",  width=14, command=on_cancel).pack(side="left", padx=6)

    win.grab_set()
    win.update_idletasks()
    pref_w   = getattr(tbl, "_preferred_width", 800) + 40   # +40 padding okna
    screen_h = win.winfo_screenheight()
    pref_h   = min(win.winfo_reqheight(), int(screen_h * 0.82))
    pref_h   = max(pref_h, 540)   # min výška: vždy viditelná tlačítka
    _center_window(win, max(pref_w, 700), pref_h)
    win.wait_window()
    return result["proceed"]


# --------- Report okno ----------
def show_report(parent, done_rows, skip_rows, cancelled, total):
    n_copied    = sum(1 for r in done_rows if r["tag"] == "copy")
    n_overwrite = sum(1 for r in done_rows if r["tag"] == "overwrite")
    n_errors    = sum(1 for r in done_rows if r["tag"] == "error")
    n_skip      = len(skip_rows)

    win = Toplevel(parent)
    win.title("Report" + (" — CANCELLED" if cancelled else ""))
    win.resizable(True, True)

    Label(win,
          text="Stopped by user." if cancelled else "Done.",
          font=("", 11, "bold"), padx=12, pady=8).pack(anchor="w")

    _make_dashboard(win, [
        ("Copied",      n_copied,    "#1a6e1a"),
        ("Overwritten", n_overwrite, "#0055aa"),
        ("Skipped",     n_skip,      "#888888"),
        ("Errors",      n_errors,    "#cc0000"),
    ]).pack(padx=12, anchor="w")

    Label(win, text="Details:", padx=12, pady=4, anchor="w").pack(fill="x")

    tbl = _make_file_table(win, done_rows + skip_rows, height=18)
    tbl.pack(fill=BOTH, expand=True, padx=12, pady=(0, 4))

    Button(win, text="Close", width=14, command=win.destroy).pack(pady=(10, 16))

    win.grab_set()
    win.update_idletasks()
    pref_w   = getattr(tbl, "_preferred_width", 800) + 40
    screen_h = win.winfo_screenheight()
    pref_h   = min(win.winfo_reqheight(), int(screen_h * 0.82))
    pref_h   = max(pref_h, 420)
    _center_window(win, max(pref_w, 700), pref_h)
    win.wait_window()


# --------- progress window ----------
def create_progress_window(parent, total):
    win = Toplevel(parent)
    cancelled = {"value": False}

    def on_cancel():
        cancelled["value"] = True
        win.title("Cancelling...")

    win.title("Converting (copying)...")
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", on_cancel)

    frame = ttk.Frame(win, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Copying & renaming files…").pack(anchor="w")

    pb = ttk.Progressbar(frame, mode="determinate", maximum=total, length=420)
    pb.pack(fill="x", pady=(8, 6))

    info_var = StringVar(value=f"0 / {total} (0%)")
    time_var = StringVar(value="Elapsed: 00:00")

    ttk.Label(frame, textvariable=info_var).pack(anchor="w")
    ttk.Label(frame, textvariable=time_var).pack(anchor="w")

    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{x}+{y}")

    return win, pb, info_var, time_var, cancelled


# --------- copy worker ----------
def copy_one(args):
    src, dest, old_name = args
    try:
        shutil.copy2(src, dest)
        return (old_name, dest.name, True, None)
    except Exception as e:
        return (old_name, None, False, str(e))


def _icon_app_id(prefix, ico_path):
    """A taskbar identity that is new on every launch.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so every *stable* id tried here eventually picked up a bad cache entry
    and then drew the blank window placeholder for good: a fixed string, a hash
    of the icon, and a hash tagged with the build's file name each broke within
    days. Setting no id at all was no better -- Windows then keys the button on
    the exe path and caches the picture there instead (Diagnostic v1.3.1,
    measured 2026-09-17: the window icon, the exe's own icon resource and the
    shell's own file icon all correct, the taskbar button blank).

    An id Windows has never seen has no cache entry, so the button falls back
    to the window icon, which every program here sets itself -- measured on a
    fresh id on 2026-09-04 and again on 2026-09-17. A random suffix per launch
    makes every run a first-time id, which is why this is the one form that
    cannot go stale. Nothing here needs a stable identity: no program registers
    a shortcut, pins itself or sends Windows toasts. The one cost is pinning a
    *running* taskbar button -- that pin would carry this run's id and would
    not start the program again, so pin the exe instead.

    Returns None when there is no icon at all; the caller then sets no id and
    the button keeps taking the exe's own picture.
    """
    import os.path
    if not ico_path or not os.path.exists(str(ico_path)):
        return None
    import uuid
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def _nudge_taskbar_icon(hwnd) -> None:
    """Make the taskbar re-read this window's icon a few times after start.

    Explorer reads a taskbar button's picture once, when it creates the
    button, and does not ask again by itself. When that one read misses, the
    button keeps the blank window placeholder for the whole run although the
    window holds the right icon -- measured on Diagnostic v1.3.4, 15.10.2026:
    started from C:, a fresh per-launch id, WM_GETICON answering with the
    magnifier, the button blank. Re-sending the same icon changed nothing;
    taking the icon away and giving it back made Explorer re-read it, and the
    same running window drew the magnifier at once. So that is done here a few
    times while the button may still be appearing (a slow start, a busy
    Explorer after logon). Runs on a daemon thread; the window's own icon
    handles are put back, so nothing is created or leaked.
    """
    if not hwnd:
        return
    import threading

    def run():
        import ctypes
        import ctypes.wintypes as wt
        import time
        user32 = ctypes.windll.user32
        send = user32.SendMessageTimeoutW
        send.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT,
                         wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_size_t
        res = ctypes.c_size_t()
        for delay in (2, 8, 20, 30):          # at about 2, 10, 30 and 60 s
            time.sleep(delay)
            try:
                if not user32.IsWindow(wt.HWND(hwnd)):
                    return
                held = {}
                for which in (1, 0):          # ICON_BIG, ICON_SMALL
                    res.value = 0
                    # SMTO_ABORTIFHUNG: a frozen window just skips this round
                    if send(hwnd, 0x007F, which, 0, 0x0002, 2000,
                            ctypes.byref(res)) and res.value:
                        held[which] = res.value
                for which in held:
                    send(hwnd, 0x0080, which, 0, 0x0002, 2000, ctypes.byref(res))
                for which, h in held.items():
                    send(hwnd, 0x0080, which, h, 0x0002, 2000, ctypes.byref(res))
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name="taskbar-icon").start()


def set_app_icon(win, ico_path, app_id=None):
    """Apply icon.ico to the title bar AND the Windows taskbar button.

    tkinter's iconbitmap only sets the title bar icon; the Windows 11 taskbar
    reads the small icon slots + window-class icon, which Tk leaves as its
    default feather. We force every slot from icon.ico via Win32.
    """
    import ctypes
    import os
    import sys
    # The Builder bundles icon.ico inside the exe (--add-data), and a copy next
    # to the exe is not guaranteed -- without this fallback iconbitmap failed
    # and the title bar kept Tk's feather. Resolved before the id is made, so
    # the id and every icon call below see the same file.
    if not ico_path or not os.path.exists(str(ico_path)):
        _mp = getattr(sys, "_MEIPASS", None)
        _alt = os.path.join(_mp, "icon.ico") if _mp else None
        if _alt and os.path.exists(_alt):
            ico_path = _alt
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
    # The outer window (title bar, taskbar button) exists only once Tk has
    # mapped the window; forcing it with update_idletasks() showed the window
    # before it was built. So the slots are set just after the first map.
    def _apply(tries=0):
        try:
            if not win.winfo_ismapped() and tries < 50:
                win.after(200, lambda: _apply(tries + 1))
                return
            u = ctypes.windll.user32
            try:
                hwnd = int(win.wm_frame(), 16)
            except Exception:
                hwnd = u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
            try:
                big = u.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)
                sm  = u.LoadImageW(None, ico_path, 1, 16, 16, 0x10)
                for which, h in ((1, big), (0, sm), (2, sm)):
                    if h:
                        u.SendMessageW(hwnd, 0x0080, which, h)
                set_cls = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
                if big:
                    set_cls(hwnd, -14, big)
                if sm:
                    set_cls(hwnd, -34, sm)
            except Exception:
                pass
            _nudge_taskbar_icon(hwnd)
        except Exception:
            pass                              # the window is gone: just stop

    try:
        win.after(100, _apply)
    except Exception:
        pass


# --------- main ----------
def main():
    root = Tk()
    root.withdraw()
    root.title(APP_TITLE)
    import sys as _sys
    if getattr(_sys, "frozen", False):
        _base = Path(_sys.executable).resolve().parent
    else:
        _base = Path(__file__).resolve().parent
    set_app_icon(root, str(_base / "icon.ico"), "ELI.TimeConverter")

    while True:
        opts = show_intro_and_get_options(root)
        if not opts:
            break

        use_prague_time = opts["use_prague_time"]
        show_report_opt = opts["show_report"]

        files = filedialog.askopenfilenames(
            title="Select files to convert (copy)",
            initialdir=r"\\users-L3.tier0.lcs.local\cpva-image-2026\2026"
        )
        if not files:
            continue

        d = filedialog.askdirectory(
            title="Select target folder",
            initialdir=str(Path.home() / "Documents")
        )
        if not d:
            continue

        target_dir = Path(d)

        plan      = []
        plan_rows = []
        skip_rows = []

        for item in files:
            p = Path(item)
            prefix, orig_ts_str = split_stem(p.stem)
            new_stem, reason = build_new_name(p.stem, use_prague_time)
            if new_stem is None:
                skip_rows.append(_skip_row(prefix, orig_ts_str or "—", reason))
                continue
            new_filename   = new_stem + p.suffix
            dest           = target_dir / new_filename
            will_overwrite = dest.exists()
            new_ts_str = new_stem[len(prefix):].lstrip("_")
            plan.append((p, dest, p.name, prefix, orig_ts_str or "—", new_ts_str, will_overwrite))
            plan_rows.append(_plan_row(prefix, orig_ts_str or "—", new_ts_str, will_overwrite))

        if show_report_opt:
            if not show_preview(root, plan_rows, skip_rows):
                continue

        total = len(plan)
        if total == 0:
            messagebox.showinfo("Nothing to do", "All files were skipped.")
            continue

        prog_win, pb, info_var, time_var, cancelled = create_progress_window(root, total)

        start_t    = time.perf_counter()
        done_count = 0
        done_rows  = []

        USE_EMA = total >= 20
        alpha   = 0.08
        ema_spf = None

        last_ui_update = 0
        ui_period      = 0.15

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        futures  = {
            executor.submit(copy_one, (src, dest, old)): (prefix, orig_ts, new_ts, will_ow)
            for src, dest, old, prefix, orig_ts, new_ts, will_ow in plan
        }

        pending = list(futures.keys())

        while pending and not cancelled["value"]:
            just_done = [f for f in pending if f.done()]
            pending   = [f for f in pending if not f.done()]

            for fut in just_done:
                old_name, dest_name, ok, err_msg    = fut.result()
                prefix, orig_ts, new_ts, was_ow     = futures[fut]
                done_rows.append(_done_row(prefix, orig_ts, new_ts, ok, err_msg, was_ow))
                done_count += 1

                elapsed = time.perf_counter() - start_t
                if done_count > 1:
                    spf = elapsed / done_count
                    ema_spf = spf if (not USE_EMA or ema_spf is None) else (
                        alpha * spf + (1 - alpha) * ema_spf)

            t_now = time.perf_counter()
            if t_now - last_ui_update >= ui_period or done_count == total:
                pb["value"] = done_count
                pct = int((done_count / total) * 100)
                info_var.set(f"{done_count} / {total} ({pct}%)")

                elapsed = t_now - start_t
                eta     = ((total - done_count) * ema_spf) if (ema_spf and done_count >= 3) else None
                eta_str = f"  ETA: {_fmt_hms(eta)}" if eta is not None else ""
                time_var.set(f"Elapsed: {_fmt_hms(elapsed)}{eta_str}")

                prog_win.update()
                last_ui_update = t_now

            if pending:
                time.sleep(0.05)

        executor.shutdown(wait=False, cancel_futures=True)
        prog_win.destroy()

        if show_report_opt:
            show_report(root, done_rows, skip_rows, cancelled["value"], len(files))
        else:
            lines = []
            if cancelled["value"]:
                lines.append("Stopped by user.")
            if n_copied := sum(1 for r in done_rows if r["tag"] == "copy"):
                lines.append(f"Copied:      {n_copied}")
            if n_overwrite := sum(1 for r in done_rows if r["tag"] == "overwrite"):
                lines.append(f"Overwritten: {n_overwrite}")
            if n_skip := len(skip_rows):
                lines.append(f"Skipped:     {n_skip}")
            if n_errors := sum(1 for r in done_rows if r["tag"] == "error"):
                lines.append(f"Errors:      {n_errors}")
            messagebox.showinfo(
                "Done" if not cancelled["value"] else "Cancelled",
                "\n".join(lines))

if __name__ == "__main__":
    main()
