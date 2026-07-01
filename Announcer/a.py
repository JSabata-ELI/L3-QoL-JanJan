"""
Screen Region Change Tracker
Monitors a specific region on screen and alerts on change.
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import json
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
import socket
import ssl
from PIL import ImageGrab, ImageTk, ImageChops, ImageStat
import screeninfo
import sys
from pathlib import Path

# --- Configuration ---
POLL_INTERVAL_MS = 500      # how often to check (ms)
CHANGE_THRESHOLD = 2        # average pixel deviation (0-255)
FLASH_DURATION_MS = 3000    # how long to flash after detection
FLASH_INTERVAL_MS = 300     # flash blink speed

# --- PV monitoring ---
PV_AVG_COUNT = 25   # number of recent values to average
PV_POLL_MS   = 500  # how often to read PVs (ms)

# (channel, label, lo_orange, lo_red, hi_orange, hi_red, unit)
# `channel` is either a PV name (str) or a (minuend, subtrahend) tuple whose
# difference (minuend - subtrahend) is monitored.
_PV_MONITORS = [
    ("L3-UTIL-HEB03-001:PressOut_PSI", "Helium volume", 46, 45.5, 57, 60, "PSI"),
    ("HAPLS-VOLT_IN_CGL-SEEDER_ER3_ALPHA1:SeederPZTVoltage", "Alpha voltage", 1.2, 1.1, 1.9, 2.2, "V"),
]

# Chiller temperature deviations from setpoint (Temp - TempSP).
_CHILLER_LABELS = ["Chiller DA1", "Chiller DA2", "Chiller DA3", "Chiller DA4",
                   "Helium Chiller", "Utility chiller"]

# Absolute-temperature limits (°C) — an "external" condition checked on the raw
# Temp value, index-aligned with _PV_MONITORS. Outside this range => PURPLE alert
# (takes priority over the deviation thresholds). None = no absolute check.
# TODO: per-chiller values to be refined later.
_PV_ABS_RANGE = [None, None]   # Helium volume, Alpha voltage: no absolute range

for _i, _label in enumerate(_CHILLER_LABELS, start=1):
    _ch = f"L3-UTIL-CHL03-{_i:03d}"
    _PV_MONITORS.append(
        ((f"{_ch}:Temp", f"{_ch}:TempSP"), _label, -0.3, -0.6, 0.3, 0.6, "°C")
    )
    # DA1-DA4 + Helium Chiller: 7..17.5 °C ; Utility chiller: 18..22 °C
    _PV_ABS_RANGE.append((18.0, 22.0) if _i == 6 else (7.0, 17.5))


def _pv_key(channel):
    """Stable string key for a channel spec (str or (minuend, subtrahend) tuple)."""
    if isinstance(channel, tuple):
        return f"{channel[0]}-{channel[1]}"
    return channel


# Each entry: HTTP code -> (short label, plain-language explanation)
_HTTP_MESSAGES = {
    400: ("Bad request",
          "We sent the request in a form the archiver didn't accept. Likely a bug in the request, not your network."),
    401: ("Unauthorized",
          "The archiver wants credentials we didn't provide."),
    403: ("Access denied",
          "The archiver refused the request — you may not be allowed to read this channel."),
    404: ("Channel not found",
          "The archiver doesn't know this PV name. It may be misspelled or simply not archived."),
    500: ("Archiver server error",
          "The archiver was reached and answered, but crashed internally while handling the request. A server-side problem — usually temporary, not your network."),
    502: ("Archiver gateway error",
          "A gateway in front of the archiver got a broken reply from it."),
    503: ("Archiver unavailable",
          "The archiver is temporarily down, overloaded, or restarting. Usually clears up on its own."),
    504: ("Archiver gateway timeout",
          "A gateway forwarded the request but the archiver behind it never replied in time."),
}

# detail label -> plain-language explanation for the non-HTTP cases
_NETWORK_HINTS = {
    "Connection timed out":
        "The request left your PC but no reply came back within the time limit. The archiver is reachable in principle but too slow, overloaded, or the network is congested. (Compare: 'failed' = couldn't even start the connection.)",
    "Archiver unreachable":
        "We couldn't open a connection to the archiver at all — server is down, the address is wrong, or you're not on the right network/VPN. Nothing was sent.",
    "Connection failed":
        "The connection to the archiver was actively refused or dropped mid-way. The server (or a firewall) said 'no' rather than just staying silent.",
    "Invalid server response":
        "The archiver answered, but the data wasn't readable (not valid JSON). The server may be returning an error page instead of data.",
}


def _readable_pv_error(pv_name, exc):
    """Translate a raw fetch exception into (short message, plain explanation)."""
    if isinstance(exc, urllib.error.HTTPError):
        label, hint = _HTTP_MESSAGES.get(
            exc.code, ("Server error", "The archiver returned an unexpected error code."))
        detail = f"{label} (HTTP {exc.code})"
    elif isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        detail = "Connection timed out" if isinstance(reason, (socket.timeout, TimeoutError)) else "Archiver unreachable"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        detail = "Connection timed out"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, ConnectionError):
        detail = "Connection failed"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, (json.JSONDecodeError, ValueError)):
        detail = "Invalid server response"
        hint = _NETWORK_HINTS[detail]
    else:
        detail = str(exc) or exc.__class__.__name__
        hint = "An unexpected error occurred while reading this PV."
    return f"{pv_name} — {detail}", hint


_RESERVED_PRESET_KEYS = {"pv_thresholds", "window_geometry", "flash_mode", "image_file"}


class RegionSelector(tk.Toplevel):
    """Overlay window for selecting a screen region.

    Two-window approach:
      - main overlay: semi-transparent dim (alpha=0.15) so the monitor is visible
      - border_win:   fully opaque Toplevel drawn exactly over the drag rectangle,
                      so the red border is never affected by alpha
    """

    def __init__(self, parent, monitor, callback):
        super().__init__(parent)
        self.callback = callback
        self.monitor = monitor
        self.start_x = self.start_y = 0
        self._border_win = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.15)
        self.configure(bg="#000000")

        x, y = monitor.x, monitor.y
        w, h = monitor.width, monitor.height
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.canvas = tk.Canvas(self, cursor="cross", bg="#000000",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        lbl = tk.Label(self.canvas,
                       text="Drag to select region — ESC to cancel",
                       bg="black", fg="white", font=("Arial", 14))
        lbl.place(relx=0.5, rely=0.05, anchor="center")

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", self._cancel)

        self.bind("<Destroy>", lambda e: (parent._on_selector_closed() if e.widget is self else None))

    def _cancel(self, *_):
        if self._border_win:
            try: self._border_win.destroy()
            except Exception: pass
        self.destroy()

    def _on_press(self, event):
        self.start_x = event.x + self.monitor.x
        self.start_y = event.y + self.monitor.y

    def _on_drag(self, event):
        ex = event.x + self.monitor.x
        ey = event.y + self.monitor.y
        x1 = min(self.start_x, ex)
        y1 = min(self.start_y, ey)
        x2 = max(self.start_x, ex)
        y2 = max(self.start_y, ey)
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        T = 4  # border thickness in px
        if self._border_win is None:
            self._border_win = tk.Toplevel(self)
            self._border_win.overrideredirect(True)
            self._border_win.attributes("-topmost", True)
            self._border_win.attributes("-alpha", 1.0)
            self._border_win.configure(bg="#ff0000")
        # Resize/reposition to form a hollow red border using the window background
        self._border_win.geometry(f"{w}x{h}+{x1}+{y1}")
        # Draw hollow rectangle by placing a transparent inner frame
        for child in self._border_win.winfo_children():
            child.destroy()
        inner = tk.Frame(self._border_win, bg="#000000")
        inner.place(x=T, y=T, width=w - 2*T, height=h - 2*T)
        self._border_win.attributes("-transparentcolor", "#000000")

    def _on_release(self, event):
        if self._border_win:
            try: self._border_win.destroy()
            except Exception: pass
            self._border_win = None
        ex = event.x + self.monitor.x
        ey = event.y + self.monitor.y
        x1 = min(self.start_x, ex)
        y1 = min(self.start_y, ey)
        x2 = max(self.start_x, ex)
        y2 = max(self.start_y, ey)
        self.destroy()
        if (x2 - x1) > 5 and (y2 - y1) > 5:
            self.callback((x1, y1, x2, y2))


class ScreenTracker(tk.Tk):
    def __init__(self):
        try:
            import ctypes as _ct
            _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ELI.Announcer")
        except Exception:
            pass
        super().__init__()
        self.title("Announcer")

        try:
            self.iconbitmap(self._get_icon_path())
        except Exception:
            pass

        self.resizable(True, True)
        self.attributes("-topmost", True)

        self.region = None
        self.reference = None
        self.tracking = False
        self.changed = False
        self._selector_open = False

        self.change_threshold = tk.DoubleVar(value=CHANGE_THRESHOLD)
        self._flash_job = None
        self._poll_job = None
        self._flash_state = False
        self._flash_deadline = 0

        self._monitors = screeninfo.get_monitors()
        self._selected_monitor_idx = tk.IntVar(value=0)
        self._monitor_labels = [
            f"Monitor {i+1}"
            for i, m in enumerate(self._monitors)
        ]

        self._is_flashing = False
        self._preview_pinned = False
        self.status_var = tk.StringVar(value="Select a region on a monitor.")

        # PV monitoring state
        self._pv_poll_job = None
        self._pv_alert_rows: list[tk.Frame] = []
        self._pv_alert_labels: list[tk.Label] = []

        # Message log state
        self._log_items: dict[str, tuple[str, int]] = {}   # message -> (item_id, count)
        self._log_hints: dict[str, str] = {}               # item_id -> plain explanation
        self._log_tip = None
        self._log_tip_item = None
        self._geom_before_hide = None

        if getattr(sys, "frozen", False):
            _base = Path(sys.executable).parent
        else:
            _base = Path(__file__).parent
        self._presets_path = _base / "presets.json"
        self._presets = self._load_presets()

        pv_saved = self._presets.get("pv_thresholds", {})
        self._pv_thr_vars = []
        for channel, _label, def_lo_o, def_lo_r, def_hi_o, def_hi_r, _unit in _PV_MONITORS:
            saved = pv_saved.get(_pv_key(channel), {})
            # backward-compat: old keys were "orange"/"red"
            lo_r_var = tk.DoubleVar(value=saved.get("lo_red",    saved.get("red",    def_lo_r)))
            lo_o_var = tk.DoubleVar(value=saved.get("lo_orange", saved.get("orange", def_lo_o)))
            hi_o_var = tk.DoubleVar(value=saved.get("hi_orange", def_hi_o))
            hi_r_var = tk.DoubleVar(value=saved.get("hi_red",    def_hi_r))
            for v in (lo_r_var, lo_o_var, hi_o_var, hi_r_var):
                v.trace_add("write", lambda *_: self._save_pv_thresholds())
            self._pv_thr_vars.append((lo_r_var, lo_o_var, hi_o_var, hi_r_var))

        self._build_ui()
        self.bind("<Button-1>", self._on_any_click)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        saved_geom = self._presets.get("window_geometry")
        self.geometry(saved_geom if saved_geom else "400x360")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _on_any_click(self, event=None):
        if not self._is_flashing:
            return

        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None

        self._is_flashing = False
        if hasattr(self, "_flash_overlay"):
            self._flash_overlay.place_forget()
        if self._flash_img_label is not None:
            self._flash_img_label.place_forget()
        self._set_ui_visible(True)

    def _get_icon_path(self):
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        return str(base / "icon.ico")
    
    def _load_sound_files(self):
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        sounds_dir = base / "sounds"
        options = ["beep"]
        if sounds_dir.exists():
            for f in sorted(sounds_dir.glob("*.wav")):
                options.append(f.stem)
        return options

    def _images_dir(self):
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        return base / "images"

    def _load_image_files(self):
        """List stems of template images in the images/ folder (empty if none)."""
        images_dir = self._images_dir()
        options = []
        if images_dir.exists():
            for ext in ("*.png", "*.gif", "*.jpg", "*.jpeg"):
                for f in sorted(images_dir.glob(ext)):
                    if f.stem not in options:
                        options.append(f.stem)
        return options

    def _image_path(self, stem):
        """Resolve a stem to an existing image file path, or None."""
        if not stem:
            return None
        images_dir = self._images_dir()
        for ext in (".png", ".gif", ".jpg", ".jpeg"):
            p = images_dir / f"{stem}{ext}"
            if p.exists():
                return p
        return None

    def _make_flash_photo(self, size, *, fade=None, invert=False, key=None):
        """Build an ImageTk.PhotoImage of the selected template scaled to `size`.

        The template's alpha is used as a stencil and the result is a *chroma-keyed*
        RGB image: pixels painted with the chroma colour (the window theme colour)
        become transparent / click-through via the window's ``-transparentcolor``,
        while flash-coloured pixels stay opaque and clickable.

          * ``invert=False`` (fill): the silhouette is the flash colour and the
            surrounding area is chroma → only the scorpion shows, surroundings are
            see-through.
          * ``invert=True``: the surrounding area is the flash colour and the
            silhouette is chroma → the surroundings blink and the scorpion is a
            see-through hole.

        ``fade`` (0..1) blends the (non-inverted) fill toward chroma to produce a
        faint "ghost" used for window alignment. Returns None if no image is
        selected / found or the size is degenerate.
        """
        from PIL import Image
        path = self._image_path(self.image_file.get())
        w, h = size
        if path is None or w < 1 or h < 1:
            return None
        try:
            src = Image.open(path).convert("RGBA")
            src = src.resize((int(w), int(h)), resample=Image.LANCZOS)
            alpha = src.split()[3]
            # Binarize alpha: LANCZOS anti-aliases the edges into partial-alpha
            # pixels; compositing those blends flash<->chroma into intermediate
            # colours that the exact-match -transparentcolor can't key out (the
            # magenta fringe / non-transparent silhouette). A hard threshold keeps
            # every pixel either pure flash or pure chroma, so the key is clean.
            alpha = alpha.point(lambda a: 255 if a >= 128 else 0)
            chroma = self._color_rgb(key or self._chroma)
            flash = self._color_rgb(self.flash_color.get())
            if invert:
                # flash-coloured field with the scorpion punched out to chroma
                base = Image.new("RGB", src.size, flash)
                hole = Image.new("RGB", src.size, chroma)
                out = Image.composite(hole, base, alpha)
            else:
                # flash-coloured scorpion on a chroma (transparent) background
                base = Image.new("RGB", src.size, chroma)
                fill = Image.new("RGB", src.size, flash)
                out = Image.composite(fill, base, alpha)
                if fade is not None:
                    ghost = Image.new("RGB", src.size, chroma)
                    out = Image.blend(ghost, out, max(0.0, min(1.0, fade)))
            from PIL import ImageTk as _ImageTk
            return _ImageTk.PhotoImage(out)
        except Exception as e:
            self._log_message(f"Image load failed: {e}")
            return None

    def _color_rgb(self, color):
        """Return a Tk colour string as an (r, g, b) 0-255 tuple usable by PIL."""
        try:
            r, g, b = self.winfo_rgb(color)
            return (r // 256, g // 256, b // 256)
        except Exception:
            return (255, 34, 34)

    def _build_ui(self):
        pad = dict(padx=10, pady=5)

        # Theme background color — used as the chroma key for the transparent
        # (HUD) mode during tracking. Anything painted with this color becomes
        # fully transparent and click-through; only the circle / PV badges stay.
        self._chroma = ttk.Style().lookup("TFrame", "background") or self.cget("bg")
        self.configure(bg=self._chroma)

        # Dedicated chroma key for the flash images. The theme colour keys fine for
        # native widgets, but PIL-painted pixels must match the -transparentcolor
        # value EXACTLY, which a named/system theme colour can't guarantee. A unique
        # magenta we both paint and key on is reliably transparent / click-through.
        self._flash_key = "#ff00ff"

        # Řádek 0: mon_frame vlevo, kolečko samostatně vpravo
        self._top_frame = ttk.Frame(self)
        self._top_frame.grid(row=0, column=0, sticky="ew", **pad)

        self._mon_frame = ttk.LabelFrame(self._top_frame, text="Monitor")
        self._mon_frame.pack(side="left")

        inner = ttk.Frame(self._mon_frame)
        inner.pack(fill="x", padx=6, pady=4)

        self._mon_combo = ttk.Combobox(inner, values=self._monitor_labels,
                                       state="readonly", width=12)
        self._mon_combo.current(0)
        self._mon_combo.pack(side="left", padx=(0, 4))
        self._mon_combo.bind("<<ComboboxSelected>>",
                             lambda e: self._selected_monitor_idx.set(self._mon_combo.current()))

        ttk.Button(inner, text="Identify",
                   command=self._identify_monitors).pack(side="left")

        # Kolečko hned za Monitor skupinou
        self._canvas_circle = tk.Canvas(self._top_frame, width=34, height=34,
                                        highlightthickness=0,
                                        bg=ttk.Style().lookup("TFrame", "background"))
        self._canvas_circle.pack(side="left", padx=(10, 4))
        self._circle = self._canvas_circle.create_oval(3, 3, 31, 31,
                                                       fill="#888888", outline="", width=0)
        self._canvas_circle.bind("<Button-1>", lambda e: self._toggle_tracking())
        self._canvas_circle.config(cursor="hand2")

        # Settings button hned za kolečkem
        self._settings_popup = None
        self._settings_btn = ttk.Button(self._top_frame, text="⚙ Settings",
                                        command=self._toggle_settings_popup)
        self._settings_btn.pack(side="left", padx=(4, 0))

        # Action buttons — placed on the top row, using the space right of Settings
        self._preview_popup = None
        self._preview_photo = None
        self.btn_preview = ttk.Button(self._top_frame, text="Preview region", width=14)
        self.btn_preview.pack(side="left", padx=(12, 2))
        self.btn_preview.bind("<Enter>", self._show_preview_popup)
        self.btn_preview.bind("<Leave>", lambda *_: self.after(100, self._check_hide_preview))
        self.btn_preview.bind("<Button-1>", self._toggle_preview_popup)

        self.btn_reference = ttk.Button(self._top_frame, text="Set reference",
                                        command=self._select_region, width=13)
        self.btn_reference.pack(side="left", padx=2)

        self.btn_resnap = ttk.Button(self._top_frame, text="↺",
                                     command=self._save_reference,
                                     state="disabled", width=3)
        self.btn_resnap.pack(side="left", padx=(2, 0))

        # Region presets — compact inline row (row 1)
        self._preset_frame = ttk.LabelFrame(self, text="Region presets")
        self._preset_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 4))

        self._preset_var = tk.StringVar()
        self._preset_combo = ttk.Combobox(self._preset_frame, textvariable=self._preset_var,
                                           state="readonly", width=20)
        self._preset_combo.grid(row=0, column=0, padx=(6, 4), pady=4)
        self._refresh_preset_combo()

        ttk.Button(self._preset_frame, text="Load",
                   command=self._load_preset, width=6).grid(row=0, column=1, padx=(0, 4), pady=4)
        ttk.Button(self._preset_frame, text="Save region",
                   command=self._save_preset, width=10).grid(row=0, column=2, padx=(0, 4), pady=4)
        ttk.Button(self._preset_frame, text="Delete",
                   command=self._delete_preset, width=6).grid(row=0, column=3, padx=(0, 6), pady=4)

        # PV alert panel (row=2) — individual badges shown only when condition
        # breached, laid out side by side and wrapping to new lines as needed.
        self._pv_frame = ttk.Frame(self)
        self._pv_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._pv_frame.grid_remove()
        self._pv_frame.grid_propagate(False)
        self._pv_last_width = 0
        self._pv_frame.bind("<Configure>", self._on_pv_frame_configure)
        self.columnconfigure(0, weight=1)

        for _pv_name, _label, *_thresholds in _PV_MONITORS:
            row_frame = tk.Frame(self._pv_frame, bg="#cc6600", padx=8, pady=5)
            row_frame._active = False
            lbl = tk.Label(row_frame, text="", bg="#cc6600", fg="white",
                           font=("Segoe UI", 9, "bold"), anchor="w")
            lbl.pack()
            self._pv_alert_rows.append(row_frame)
            self._pv_alert_labels.append(lbl)

        # Message log (row=3) — shows runtime messages such as PV fetch errors.
        # Hidden while tracking (see _set_ui_visible).
        self._log_frame = ttk.LabelFrame(self, text="Message log")
        self._log_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.rowconfigure(3, weight=1)
        self._log_frame.rowconfigure(0, weight=1)
        self._log_frame.columnconfigure(0, weight=1)

        cols = ("time", "count", "msg")
        tv = ttk.Treeview(self._log_frame, columns=cols, show="headings", height=6)
        tv.heading("time", text="Time")
        tv.heading("count", text="#")
        tv.heading("msg", text="Message  (hover for details)")
        tv.column("time", width=70, anchor="w", stretch=False)
        tv.column("count", width=40, anchor="center", stretch=False)
        tv.column("msg", width=250, anchor="w", stretch=True)
        vsb = ttk.Scrollbar(self._log_frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(2, 6))
        vsb.grid(row=0, column=1, sticky="ns", pady=(2, 6), padx=(0, 6))
        ttk.Button(self._log_frame, text="Clear", width=6,
                   command=self._clear_log).grid(row=1, column=0, columnspan=2,
                                                 sticky="e", padx=6, pady=(0, 6))
        self._log_tree = tv
        tv.bind("<Motion>", self._on_log_hover)
        tv.bind("<Leave>", lambda e: self._hide_log_tip())

        # Pre-build Settings variables (popup builds widgets on first open)
        self.flash_color = tk.StringVar(value="#ff2222")
        self.flash_duration = tk.DoubleVar(value=0.0)
        self.sound_enabled = tk.BooleanVar(value=True)
        self.sound_freq = tk.IntVar(value=1500)
        self.sound_duration = tk.IntVar(value=500)
        self.sound_file = tk.StringVar(value="beep")
        self._sound_files = self._load_sound_files()
        self._flash_color_btn = None  # created in popup

        # Flash-image (template) settings
        self.flash_mode = tk.StringVar(value="color")   # "color" | "image" | "alternate"
        self.image_file = tk.StringVar(value="")
        self._image_files = self._load_image_files()
        self._flash_photo = None        # cached PhotoImage for the current flash
        self._flash_photo_inv = None    # inverse (surround-on) variant for "alternate"
        self._flash_img_label = None    # tk.Label showing the template while flashing
        self._align_photo = None        # ghost PhotoImage during window alignment
        self._align_label = None
        self._align_bind = None
        self._align_last_size = (0, 0)

        # Restore saved flash settings, then persist on change.
        saved_mode = self._presets.get("flash_mode")
        if saved_mode in ("color", "image", "alternate"):
            self.flash_mode.set(saved_mode)
        saved_img = self._presets.get("image_file")
        if saved_img in self._image_files:
            self.image_file.set(saved_img)
        self.flash_mode.trace_add("write", lambda *_: self._save_flash_settings())
        self.image_file.trace_add("write", lambda *_: self._save_flash_settings())

    # ------------------------------------------------------------------
    # Region presets
    # ------------------------------------------------------------------
    def _load_presets(self):
        if self._presets_path.exists():
            try:
                data = json.loads(self._presets_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _save_presets_file(self):
        self._presets_path.write_text(
            json.dumps(self._presets, indent=2), encoding="utf-8")

    def _save_pv_thresholds(self):
        thr = {}
        for i, (channel, *_rest) in enumerate(_PV_MONITORS):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            try:
                thr[_pv_key(channel)] = {
                    "lo_red":    lo_r_var.get(),
                    "lo_orange": lo_o_var.get(),
                    "hi_orange": hi_o_var.get(),
                    "hi_red":    hi_r_var.get(),
                }
            except tk.TclError:
                pass
        self._presets["pv_thresholds"] = thr
        self._save_presets_file()

    def _save_flash_settings(self):
        self._presets["flash_mode"] = self.flash_mode.get()
        self._presets["image_file"] = self.image_file.get()
        self._save_presets_file()

    def _preset_names(self):
        return sorted(k for k in self._presets if k not in _RESERVED_PRESET_KEYS)

    def _refresh_preset_combo(self):
        names = self._preset_names()
        self._preset_combo["values"] = names
        if names and self._preset_var.get() not in names:
            self._preset_var.set(names[0])
        elif not names:
            self._preset_var.set("")

    def _save_preset(self):
        if not self.region:
            messagebox.showwarning("No region", "Select a region first.", parent=self)
            return
        name = simpledialog.askstring("Save preset", "Preset name:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self._presets:
            if not messagebox.askyesno("Overwrite?",
                                       f'Preset "{name}" already exists. Overwrite?',
                                       parent=self):
                return
        existing = self._presets.get(name)
        if isinstance(existing, dict):
            existing["region"] = list(self.region)
        else:
            self._presets[name] = list(self.region)
        self._save_presets_file()
        self._refresh_preset_combo()
        self._preset_var.set(name)

    def _load_preset(self):
        name = self._preset_var.get()
        if not name or name not in self._presets:
            messagebox.showwarning("No preset", "Select a preset first.", parent=self)
            return
        if self.tracking:
            self._stop_tracking()
        data = self._presets[name]
        if isinstance(data, list):
            coords = data
            preset_geom = None
        elif isinstance(data, dict):
            coords = data.get("region") or []
            preset_geom = data.get("window_geometry")
        else:
            return
        if not coords:
            return
        self._region_selected(tuple(coords))
        self._set_ui_visible(True)
        geom = preset_geom or self._presets.get("window_geometry")
        if geom:
            self.geometry(geom)

    def _delete_preset(self):
        name = self._preset_var.get()
        if not name or name not in self._presets:
            messagebox.showwarning("No preset", "Select a preset first.", parent=self)
            return
        if not messagebox.askyesno("Delete preset",
                                   f'Delete preset "{name}"?', parent=self):
            return
        del self._presets[name]
        self._save_presets_file()
        self._refresh_preset_combo()

    # ------------------------------------------------------------------
    # Settings popup
    # ------------------------------------------------------------------
    def _toggle_settings_popup(self):
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None
            return
        self._build_settings_popup()

    def _build_settings_popup(self):
        popup = tk.Toplevel(self)
        popup.title("Settings")
        popup.resizable(False, False)
        popup.transient(self)
        self._settings_popup = popup

        # Position below the Settings button
        self.update_idletasks()
        x = self.winfo_rootx() + 10
        y = self.winfo_rooty() + self._top_frame.winfo_height() + 10
        popup.geometry(f"+{x}+{y}")

        frame = ttk.Frame(popup, padding=8)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        # Detection
        thr_frame = ttk.LabelFrame(frame, text="Detection")
        thr_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))

        ttk.Label(thr_frame, text="Threshold:").grid(row=0, column=0, padx=(6,2), pady=6)
        ttk.Spinbox(thr_frame, from_=0.5, to=50.0, increment=0.5,
                    textvariable=self.change_threshold,
                    width=6, format="%.1f").grid(row=0, column=1, padx=(0,6), pady=6)

        ttk.Label(thr_frame, text="Flash color:").grid(row=1, column=0, padx=(6,2), pady=(0,6))
        self._flash_color_btn = tk.Button(thr_frame, bg=self.flash_color.get(), width=3,
                                          relief="groove", command=self._pick_flash_color)
        self._flash_color_btn.grid(row=1, column=1, sticky="w", padx=(0,6), pady=(0,6))

        ttk.Label(thr_frame, text="Flash duration (s):").grid(row=2, column=0, padx=(6,2), pady=(0,6))
        ttk.Spinbox(thr_frame, from_=0, to=60, increment=0.5,
                    textvariable=self.flash_duration,
                    width=6, format="%.1f").grid(row=2, column=1, padx=(0,6), pady=(0,6))

        # Flash mode: full-background color, template image only, or alternating
        self._FLASH_MODE_LABELS = {
            "color":     "Background color",
            "image":     "Image only",
            "alternate": "Image + background (alternate)",
        }
        _mode_to_label = self._FLASH_MODE_LABELS
        _label_to_mode = {v: k for k, v in _mode_to_label.items()}

        ttk.Label(thr_frame, text="Flash mode:").grid(row=3, column=0, padx=(6,2), pady=(0,6))
        mode_combo = ttk.Combobox(thr_frame, state="readonly", width=24,
                                  values=list(_mode_to_label.values()))
        mode_combo.set(_mode_to_label.get(self.flash_mode.get(), "Background color"))
        mode_combo.grid(row=3, column=1, sticky="w", padx=(0,6), pady=(0,6))
        mode_combo.bind("<<ComboboxSelected>>",
                        lambda e: self.flash_mode.set(_label_to_mode.get(mode_combo.get(), "color")))

        ttk.Label(thr_frame, text="Image:").grid(row=4, column=0, padx=(6,2), pady=(0,6))
        img_combo = ttk.Combobox(thr_frame, textvariable=self.image_file,
                                 state="readonly", width=24, values=self._image_files)
        if self.image_file.get() in self._image_files:
            img_combo.current(self._image_files.index(self.image_file.get()))
        img_combo.grid(row=4, column=1, sticky="w", padx=(0,6), pady=(0,6))

        ttk.Label(thr_frame,
                  text="Align the image via \"Set location and size of this window\" below.",
                  foreground="gray", wraplength=220, justify="left").grid(
            row=5, column=0, columnspan=2, padx=6, pady=(0, 6), sticky="w")

        # Sound
        sound_frame = ttk.LabelFrame(frame, text="Sound")
        sound_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))

        ttk.Checkbutton(sound_frame, text="Play sound on change",
                        variable=self.sound_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 2))

        ttk.Label(sound_frame, text="Freq (Hz):").grid(row=1, column=0, padx=(6,2), pady=(0,4))
        ttk.Spinbox(sound_frame, from_=200, to=4000, increment=100,
                    textvariable=self.sound_freq, width=6).grid(row=1, column=1, padx=(0,6), pady=(0,4))

        ttk.Label(sound_frame, text="Duration (ms):").grid(row=2, column=0, padx=(6,2), pady=(0,4))
        ttk.Spinbox(sound_frame, from_=50, to=3000, increment=50,
                    textvariable=self.sound_duration, width=6).grid(row=2, column=1, padx=(0,6), pady=(0,4))

        ttk.Label(sound_frame, text="Sound file:").grid(row=3, column=0, padx=(6,2), pady=(0,6))
        self._sound_combo = ttk.Combobox(sound_frame, textvariable=self.sound_file,
                                         values=self._sound_files, state="readonly", width=12)
        if self.sound_file.get() in self._sound_files:
            self._sound_combo.current(self._sound_files.index(self.sound_file.get()))
        self._sound_combo.grid(row=3, column=1, padx=(0,6), pady=(0,6))

        # Window position & size
        win_frame = ttk.LabelFrame(frame, text="Window position & size")
        win_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(win_frame,
                  text="Move and resize the Announcer window, then save its position.").grid(
            row=0, column=0, padx=(6, 4), pady=(4, 4), sticky="w")
        ttk.Button(win_frame, text="Set location and size of this window",
                   command=self._start_window_recording).grid(
            row=0, column=1, padx=(0, 6), pady=(4, 4))

        # PV Limits table
        pv_lim_frame = ttk.LabelFrame(frame, text="PV Limits")
        pv_lim_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Column headers
        ttk.Label(pv_lim_frame, text="Channel",
                  font=("Segoe UI", 8, "bold")).grid(row=1, column=0, padx=(6, 8), pady=(0, 2), sticky="w")
        for _col, (_htxt, _hbg) in enumerate(
            [("Lolo", "#aa1c00"), ("Lo", "#b85a00"), ("Hi", "#b85a00"), ("HiHi", "#aa1c00")],
            start=1,
        ):
            tk.Label(pv_lim_frame, text=f" {_htxt} ", bg=_hbg, fg="white",
                     font=("Segoe UI", 8, "bold")).grid(row=1, column=_col, padx=3, pady=(0, 2))

        ttk.Separator(pv_lim_frame, orient="horizontal").grid(
            row=2, column=0, columnspan=5, sticky="ew", padx=4, pady=(0, 2))

        # Data rows
        for i, (_channel, label, default_lo_o, _lo_r, _hi_o, _hi_r, unit) in enumerate(_PV_MONITORS):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            inc = 0.01 if abs(default_lo_o) < 10 else 0.1
            fmt = "%.2f" if abs(default_lo_o) < 10 else "%.1f"
            row_idx = i + 3
            ttk.Label(pv_lim_frame, text=f"{label} ({unit})", anchor="w").grid(
                row=row_idx, column=0, padx=(6, 8), pady=(2, 4), sticky="w")
            for _col, var in enumerate([lo_r_var, lo_o_var, hi_o_var, hi_r_var], start=1):
                ttk.Spinbox(pv_lim_frame, from_=-9999, to=9999, increment=inc,
                            textvariable=var, width=7, format=fmt).grid(
                    row=row_idx, column=_col, padx=3, pady=(2, 4))

        # Close when clicking on the main window background (not on popup or its dropdowns)
        self._settings_close_bind = self.bind("<Button-1>", self._on_main_click_close_settings, "+")

    def _on_main_click_close_settings(self, _event):
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None
        try:
            self.unbind("<Button-1>", self._settings_close_bind)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Identify monitors
    # ------------------------------------------------------------------
    def _identify_monitors(self):
        labels = []
        for i, m in enumerate(self._monitors):
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.attributes("-alpha", 0.85)
            w.configure(bg="#111111")
            lbl = tk.Label(w, text=f"Monitor {i+1}\n{m.width}×{m.height}",
                           font=("Arial", 36, "bold"), fg="white", bg="#111111",
                           padx=30, pady=20)
            lbl.pack()
            w.update_idletasks()
            ww = w.winfo_width()
            wh = w.winfo_height()
            cx = m.x + (m.width - ww) // 2
            cy = m.y + (m.height - wh) // 2
            w.geometry(f"+{cx}+{cy}")
            labels.append(w)
        self.after(2500, lambda: [w.destroy() for w in labels])

    # ------------------------------------------------------------------
    # Region selection
    # ------------------------------------------------------------------
    def _select_region(self):
        idx = self._selected_monitor_idx.get()
        mon = self._monitors[idx]
        self._selector_open = True
        self.withdraw()
        self.after(150, lambda: RegionSelector(self, mon, self._region_selected))

    def _on_selector_closed(self):
        if self._selector_open:
            self._selector_open = False
            self.after(50, self.deiconify)

    def _region_selected(self, bbox):
        if bbox:
            self.region = bbox
            self.reference = None
            self.tracking = False
            self.changed = False
            self.btn_resnap.config(state="normal")

            self.status_var.set(f"Region: {bbox}  — set reference.")
            self.after(500, self._update_preview)
            self._save_reference()
            self._update_circle()
        else:
            self.status_var.set("Selection cancelled.")

    # ------------------------------------------------------------------
    # Reference
    # ------------------------------------------------------------------
    def _save_reference(self):
        if not self.region:
            return
        img = self._grab()
        if img is None:
            return
        self.reference = img.copy()
        self.tracking = False
        self.changed = False

        self.status_var.set("Reference saved. Start tracking.")
        self._update_preview(img)
        self._update_circle()

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------
    def _toggle_tracking(self):
        if self.tracking:
            self._stop_tracking()
        elif self.changed:
            self._reset()
        else:
            self._start_tracking()

    def _start_tracking(self):
        if self.reference is None:
            return
        self.tracking = True
        self.changed = False
        self.status_var.set("Tracking active…")
        self._poll()
        self._update_circle()
        self._set_ui_visible(False)
        self._pv_frame.grid()
        self._poll_pvs()

    def _stop_tracking(self):
        self.tracking = False
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
            self._pv_poll_job = None
        for row in self._pv_alert_rows:
            row._active = False
            row.place_forget()
        self._pv_frame.grid_remove()
        self.status_var.set("Tracking stopped.")
        self._update_circle()
        self._set_ui_visible(True)

    def _poll(self):
        if not self.tracking:
            return
        img = self._grab()
        if img is not None:
            stat = ImageStat.Stat(ImageChops.difference(img, self.reference))
            diff = sum(stat.mean) / len(stat.mean)
            if diff > self.change_threshold.get():
                self._on_change_detected(diff)
                return
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll)

    def _on_change_detected(self, diff):
        self.tracking = False
        self.changed = True
        # Tracking is over — stop polling PVs as well (no point fetching once
        # we're no longer watching; otherwise stray archiver errors keep logging).
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
            self._pv_poll_job = None
        self.status_var.set(f"CHANGE DETECTED  (diff={diff:.1f})")
        self._start_flash()
        if self.sound_enabled.get():
            self.after(0, self._play_sound)
        self._update_circle()

    # ------------------------------------------------------------------
    # Flash and sound
    # ------------------------------------------------------------------
    def _start_flash(self):
        self._is_flashing = True
        d = self.flash_duration.get()
        self._flash_deadline = time.time() + d if d > 0 else float("inf")

        # Decide effective mode: fall back to "color" if image is requested but
        # no usable template is available.
        mode = self.flash_mode.get()
        self._flash_photo = None
        self._flash_photo_inv = None
        if mode in ("image", "alternate"):
            self.update_idletasks()
            size = (self.winfo_width(), self.winfo_height())
            self._flash_photo = self._make_flash_photo(size, key=self._flash_key)
            if mode == "alternate":
                self._flash_photo_inv = self._make_flash_photo(size, invert=True, key=self._flash_key)
            if self._flash_photo is None:
                self._log_message("Flash image not set or not found — using background color.")
                mode = "color"
        self._flash_active_mode = mode

        # Keep chroma-key transparency on for the whole trip: keyed areas stay
        # see-through (per user choice — transparency over clickability). A dedicated
        # magenta key is used so PIL-painted pixels reliably become transparent. The
        # alarm is dismissed by clicking an opaque (flash-coloured) area or Esc.
        try:
            self.attributes("-transparentcolor", self._flash_key)
        except Exception:
            pass

        # Overlay frame covers the whole window — avoids ttk widget bg gaps
        if not hasattr(self, "_flash_overlay"):
            self._flash_overlay = tk.Frame(self)
        self._flash_overlay.configure(bg=self._flash_key)
        self._flash_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self._flash_overlay.lift()
        self._flash_overlay.bind("<Button-1>", self._on_any_click)

        if mode in ("image", "alternate"):
            if self._flash_img_label is None or not self._flash_img_label.winfo_exists():
                self._flash_img_label = tk.Label(self._flash_overlay, bd=0,
                                                 highlightthickness=0)
            self._flash_img_label.configure(image=self._flash_photo, bg=self._flash_key)
            self._flash_img_label.bind("<Button-1>", self._on_any_click)
            self._flash_img_label.place(x=0, y=0, relwidth=1, relheight=1)
            self._flash_img_label.lift()

        # Esc as a reliable dismiss (clicks only land on opaque areas). Needs focus
        # because the flashing window is borderless (overrideredirect).
        try:
            self.focus_force()
        except Exception:
            pass
        self.bind("<Escape>", self._on_any_click)
        self._do_flash()

    def _do_flash(self):
        if time.time() > self._flash_deadline:
            self._is_flashing = False
            self._flash_overlay.place_forget()
            if self._flash_img_label is not None:
                self._flash_img_label.place_forget()
            self._set_ui_visible(True)
            return
        self._flash_state = not self._flash_state
        mode = getattr(self, "_flash_active_mode", "color")
        flash_col = self.flash_color.get()
        chroma = self._flash_key   # keyed areas are see-through via -transparentcolor
        if mode == "image":
            # Surroundings always transparent; only the colour-tinted silhouette
            # blinks on/off.
            self._flash_overlay.configure(bg=chroma)
            self._flash_img_label.configure(bg=chroma)
            if self._flash_state:
                self._flash_img_label.configure(image=self._flash_photo)
                self._flash_img_label.lift()
                self._flash_img_label.place(x=0, y=0, relwidth=1, relheight=1)
            else:
                self._flash_img_label.place_forget()
        elif mode == "alternate":
            # Inverse seesaw — the scorpion stays visible as a shape the whole time:
            #   state on  -> coloured scorpion, transparent surround
            #   state off -> coloured surround, scorpion is a see-through hole
            self._flash_overlay.configure(bg=chroma)
            self._flash_img_label.configure(
                image=self._flash_photo if self._flash_state else self._flash_photo_inv,
                bg=chroma)
            self._flash_img_label.lift()
            self._flash_img_label.place(x=0, y=0, relwidth=1, relheight=1)
        else:
            color = flash_col if self._flash_state else "#440000"
            self._flash_overlay.configure(bg=color)
        self._flash_job = self.after(FLASH_INTERVAL_MS, self._do_flash)

    def _play_sound(self):
        import threading
        import winsound
        def _beep():
            chosen = self.sound_file.get()
            if chosen == "beep":
                winsound.Beep(self.sound_freq.get(), self.sound_duration.get())
            else:
                if getattr(sys, "frozen", False):
                    base = Path(sys.executable).parent
                else:
                    base = Path(__file__).parent
                wav = base / "sounds" / f"{chosen}.wav"
                if wav.exists():
                    winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        threading.Thread(target=_beep, daemon=True).start()
    def _reset(self):
        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None
        self._is_flashing = False
        if hasattr(self, "_flash_overlay"):
            self._flash_overlay.place_forget()
        if self._flash_img_label is not None:
            self._flash_img_label.place_forget()
        self.changed = False
        self.tracking = False
        self.status_var.set("Ready.")
        self._update_circle()

    def _update_circle(self):
        if self.tracking:
            fill, outline = "#22cc22", "#117711"   # green  — active
        elif self.changed:
            fill, outline = "#cc2222", "#881111"   # red    — change detected
        elif self.reference is not None:
            fill, outline = "#ddaa00", "#886600"   # orange — ready, has reference
        else:
            fill, outline = "#888888", ""          # grey   — no reference yet
        self._canvas_circle.itemconfig(self._circle, fill=fill, outline=outline, width=2)
        
    def _set_ui_visible(self, visible):
        if visible:
            self._mon_frame.pack(side="left", before=self._canvas_circle)
            self._settings_btn.pack(side="left", padx=(4, 0))
            self.btn_preview.pack(side="left", padx=(12, 2))
            self.btn_reference.pack(side="left", padx=2)
            self.btn_resnap.pack(side="left", padx=(2, 0))
            self._preset_frame.grid()
            self._log_frame.grid()
            self._set_transparent(False)
        else:
            self._geom_before_hide = self.geometry()
            self._mon_frame.pack_forget()
            self._settings_btn.pack_forget()
            self.btn_preview.pack_forget()
            self.btn_reference.pack_forget()
            self.btn_resnap.pack_forget()
            self._preset_frame.grid_remove()
            self._log_frame.grid_remove()
            self._set_transparent(True)

    def _set_transparent(self, on):
        """HUD mode: drop window chrome and chroma-key the background so only the
        circle (and any PV alert badges) remain visible and clickable."""
        try:
            if on:
                self.overrideredirect(True)
                self.attributes("-transparentcolor", self._chroma)
                self.attributes("-topmost", True)
            else:
                self.attributes("-transparentcolor", "")
                self.overrideredirect(False)
                self.attributes("-topmost", True)
                if self._geom_before_hide:
                    self.geometry(self._geom_before_hide)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Message log
    # ------------------------------------------------------------------
    def _log_message(self, msg, hint=None):
        """Thread-safe: append/refresh a message in the log table.

        `hint` is an optional plain-language explanation shown as a tooltip when
        the user hovers the row.
        """
        self.after(0, self._do_log, msg, hint)

    def _do_log(self, msg, hint=None):
        tv = self._log_tree
        ts = time.strftime("%H:%M:%S")
        entry = self._log_items.get(msg)
        if entry and tv.exists(entry[0]):
            item_id, count = entry[0], entry[1] + 1
            tv.set(item_id, "time", ts)
            tv.set(item_id, "count", count)
            tv.move(item_id, "", "end")
            self._log_items[msg] = (item_id, count)
            tv.see(item_id)
            return
        item_id = tv.insert("", "end", values=(ts, 1, msg))
        self._log_items[msg] = (item_id, 1)
        if hint:
            self._log_hints[item_id] = hint
        children = tv.get_children("")
        if len(children) > 500:
            oldest = children[0]
            for m, (iid, _c) in list(self._log_items.items()):
                if iid == oldest:
                    del self._log_items[m]
                    break
            self._log_hints.pop(oldest, None)
            tv.delete(oldest)
        tv.see(item_id)

    def _clear_log(self):
        self._log_tree.delete(*self._log_tree.get_children(""))
        self._log_items.clear()
        self._log_hints.clear()
        self._hide_log_tip()

    def _on_log_hover(self, event):
        tv = self._log_tree
        item = tv.identify_row(event.y)
        hint = self._log_hints.get(item) if item else None
        if not hint:
            self._hide_log_tip()
            return
        if item == self._log_tip_item and self._log_tip and self._log_tip.winfo_exists():
            return
        self._hide_log_tip()
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=hint, bg="#ffffe0", fg="#222222", justify="left",
                 relief="solid", bd=1, font=("Segoe UI", 9),
                 wraplength=340, padx=6, pady=4).pack()
        tip.geometry(f"+{event.x_root + 14}+{event.y_root + 16}")
        self._log_tip = tip
        self._log_tip_item = item

    def _hide_log_tip(self):
        if self._log_tip and self._log_tip.winfo_exists():
            self._log_tip.destroy()
        self._log_tip = None
        self._log_tip_item = None

    def _pick_flash_color(self):
        from tkinter.colorchooser import askcolor
        color = askcolor(color=self.flash_color.get(), title="Pick flash color")
        if color and color[1]:
            self.flash_color.set(color[1])
            self._flash_color_btn.config(bg=color[1])

    def _start_window_recording(self):
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None

        rec_win = tk.Toplevel(self)
        rec_win.title("Set window position & size")
        rec_win.attributes("-topmost", True)
        rec_win.resizable(False, False)

        ttk.Label(rec_win,
                  text="Move and resize the Announcer window\nto the desired position, then click DONE.",
                  justify="center").pack(padx=16, pady=(12, 8))

        scope_var = tk.StringVar(value="global")
        scope_frame = ttk.LabelFrame(rec_win, text="Save for")
        scope_frame.pack(padx=12, pady=(0, 8), fill="x")
        ttk.Radiobutton(scope_frame, text="All presets (global)",
                        variable=scope_var, value="global").pack(anchor="w", padx=8, pady=(4, 2))

        current_preset = self._preset_var.get()
        if current_preset and current_preset in self._presets:
            ttk.Radiobutton(scope_frame,
                            text=f'Selected preset only: "{current_preset}"',
                            variable=scope_var, value="preset").pack(anchor="w", padx=8, pady=(0, 4))
        else:
            ttk.Label(scope_frame, text="(select a preset to enable preset-only save)",
                      foreground="gray").pack(anchor="w", padx=8, pady=(0, 4))

        # Optional: snap the window to the template's native pixel size so the
        # flashing image matches the captured object 1:1 on the monitor.
        img_path = self._image_path(self.image_file.get())
        if self.flash_mode.get() in ("image", "alternate") and img_path is not None:
            def fit_native():
                try:
                    from PIL import Image
                    iw, ih = Image.open(img_path).size
                except Exception as e:
                    self._log_message(f"Image size read failed: {e}")
                    return
                self.geometry(f"{iw}x{ih}")
                self.update_idletasks()
                self._refresh_align_ghost()
            fit_frame = ttk.Frame(rec_win)
            fit_frame.pack(padx=12, pady=(0, 8), fill="x")
            ttk.Button(fit_frame, text="Fit window to image (1:1 pixels)",
                       command=fit_native).pack(fill="x")
            ttk.Label(fit_frame,
                      text="Sets the window to the image's captured pixel size, then just drag to position.",
                      foreground="gray", wraplength=300, justify="left").pack(anchor="w", pady=(2, 0))

        btn_frame = ttk.Frame(rec_win)
        btn_frame.pack(pady=(0, 12))

        # Show a faded ghost of the template so the window can be aligned over
        # the real object on the monitor (only in image / alternate modes).
        self._start_align_ghost()

        def on_done():
            # Save the CLIENT origin (not the decorated frame origin): during the
            # actual flash the window is borderless (overrideredirect), so its
            # client area must land where the ghost was aligned here.
            geom = f"{self.winfo_width()}x{self.winfo_height()}+{self.winfo_rootx()}+{self.winfo_rooty()}"
            self._stop_align_ghost()
            self._save_window_geometry(geom, scope_var.get(), current_preset)
            rec_win.destroy()

        def on_cancel():
            self._stop_align_ghost()
            rec_win.destroy()

        ttk.Button(btn_frame, text="DONE", command=on_done, width=10).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Cancel", command=on_cancel, width=10).pack(side="left")
        rec_win.protocol("WM_DELETE_WINDOW", on_cancel)

        self.update_idletasks()
        rec_win.update_idletasks()
        rx = self.winfo_rootx()
        ry = self.winfo_rooty()
        rw = self.winfo_width()
        rec_win.geometry(f"+{rx + rw + 10}+{ry}")

    def _start_align_ghost(self):
        """During window recording, overlay a faint template ghost that rescales
        with the window so the user can align it over the monitor object."""
        if self.flash_mode.get() not in ("image", "alternate"):
            return
        if self._image_path(self.image_file.get()) is None:
            self._log_message("No template image selected to align.")
            return
        try:
            self.attributes("-alpha", 0.5)   # see the desktop through the window
        except Exception:
            pass
        if self._align_label is None or not self._align_label.winfo_exists():
            self._align_label = tk.Label(self, bd=0, highlightthickness=0, bg=self._chroma)
        self._align_last_size = (0, 0)
        self._align_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._align_label.lift()
        self._align_bind = self.bind("<Configure>", self._on_align_configure, "+")
        self.after(50, self._refresh_align_ghost)

    def _on_align_configure(self, event):
        if event.widget is self:
            self._refresh_align_ghost()

    def _refresh_align_ghost(self):
        if self._align_label is None or not self._align_label.winfo_exists():
            return
        size = (self.winfo_width(), self.winfo_height())
        if size == self._align_last_size:
            return
        self._align_last_size = size
        self._align_photo = self._make_flash_photo(size, fade=0.5)
        if self._align_photo is not None:
            self._align_label.configure(image=self._align_photo)
            self._align_label.lift()

    def _stop_align_ghost(self):
        if self._align_bind is not None:
            try:
                self.unbind("<Configure>", self._align_bind)
            except Exception:
                pass
            self._align_bind = None
        if self._align_label is not None:
            self._align_label.place_forget()
        self._align_photo = None
        try:
            self.attributes("-alpha", 1.0)
        except Exception:
            pass

    def _save_window_geometry(self, geom, scope, preset_name):
        if scope == "global":
            self._presets["window_geometry"] = geom
        elif scope == "preset" and preset_name and preset_name in self._presets:
            data = self._presets[preset_name]
            if isinstance(data, list):
                self._presets[preset_name] = {"region": data, "window_geometry": geom}
            elif isinstance(data, dict):
                data["window_geometry"] = geom
        self._save_presets_file()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _grab(self):
        try:
            return ImageGrab.grab(bbox=self.region, all_screens=True)
        except Exception as e:
            self.status_var.set(f"Screenshot error: {e}")
            return None

    def _update_preview(self, img=None):
        if img is None:
            img = self._grab()
        if img is None:
            self.status_var.set("_update_preview: grab vrátil None")
            return
        thumb = img.copy()
        thumb.thumbnail((640, 400), resample=0)
        self._preview_photo = ImageTk.PhotoImage(thumb)
        if self.btn_preview.cget("state") == "disabled":
            self.btn_preview.config(state="normal")
        if self._preview_popup and self._preview_popup.winfo_exists():
            self._preview_popup_label.config(image=self._preview_photo)

    def _show_preview_popup(self, event=None):
        if self._preview_photo is None:
            return
        if self._preview_popup and self._preview_popup.winfo_exists():
            return
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        lbl = tk.Label(popup, image=self._preview_photo, relief="solid", bd=1)
        lbl.pack()
        self._preview_popup_label = lbl
        # Umísti popup nad tlačítko
        x = self.btn_preview.winfo_rootx()
        y = self.btn_preview.winfo_rooty() - self._preview_photo.height() - 8
        popup.geometry(f"+{x}+{y}")
        popup.bind("<Enter>", lambda e: None)
        self._preview_popup = popup

    def _hide_preview_popup(self, event=None):
        if self._preview_popup and self._preview_popup.winfo_exists():
            self._preview_popup.destroy()
        self._preview_popup = None

    def _toggle_preview_popup(self, event=None):
        if self._preview_pinned:
            self._preview_pinned = False
            self.btn_preview.config(text="Preview region")
            self._hide_preview_popup()
        else:
            self._preview_pinned = True
            self.btn_preview.config(text="Preview region ✓")
            self._show_preview_popup()

    def _check_hide_preview(self):
        if self._preview_pinned:
            return
        if not (self._preview_popup and self._preview_popup.winfo_exists()):
            return
        x, y = self.winfo_pointerxy()
        p = self._preview_popup
        bx = self.btn_preview.winfo_rootx()
        by = self.btn_preview.winfo_rooty()
        bw = self.btn_preview.winfo_width()
        bh = self.btn_preview.winfo_height()
        over_btn = (bx <= x <= bx + bw and by <= y <= by + bh)
        over_popup = (p.winfo_rootx() <= x <= p.winfo_rootx() + p.winfo_width() and
                      p.winfo_rooty() <= y <= p.winfo_rooty() + p.winfo_height())
        if over_btn or over_popup:
            self.after(100, self._check_hide_preview)
        else:
            self._hide_preview_popup()

    def _poll_pvs(self):
        if not self.tracking:
            return

        _CPVA = "https://10.78.0.57:8443/api/1.0/cpva/samples"
        _ssl  = ssl.create_default_context()
        _ssl.check_hostname = False
        _ssl.verify_mode    = ssl.CERT_NONE

        def fetch_avg(pv_name):
            try:
                params = urllib.parse.urlencode({
                    "channelName": pv_name,
                    "start": str(start_ns),
                    "end":   str(now_ns),
                })
                req = urllib.request.Request(
                    f"{_CPVA}?{params}",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=2.0, context=_ssl) as resp:
                    data = json.loads(resp.read())
                vals = []
                for sample in data:
                    v = sample.get("value")
                    if isinstance(v, (int, float)):
                        vals.append(float(v))
                    elif isinstance(v, list) and v and isinstance(v[0], (int, float)):
                        vals.append(float(v[0]))
                recent = vals[-PV_AVG_COUNT:]
                return sum(recent) / len(recent) if recent else None
            except Exception as e:
                msg, hint = _readable_pv_error(pv_name, e)
                self._log_message(msg, hint)
                return None

        def worker():
            results = []
            for channel, _label, *_thresholds in _PV_MONITORS:
                if isinstance(channel, tuple):
                    a = fetch_avg(channel[0])
                    sub = channel[1]
                    b = sub if isinstance(sub, (int, float)) else fetch_avg(sub)
                    avg = (a - b) if (a is not None and b is not None) else None
                    abs_val = a          # raw temperature for the absolute check
                else:
                    avg = fetch_avg(channel)
                    abs_val = avg
                results.append((avg, abs_val))
            self.after(0, lambda r=results: self._update_pv_display(r))

        now_ns   = int(time.time() * 1e9)
        start_ns = now_ns - 60 * 1_000_000_000   # last 60 s

        threading.Thread(target=worker, daemon=True).start()
        self._pv_poll_job = self.after(PV_POLL_MS, self._poll_pvs)

    def _update_pv_display(self, results: list):
        for i, ((avg, abs_val), (_, label, _lo_o, _lo_r, _hi_o, _hi_r, unit)) in enumerate(zip(results, _PV_MONITORS)):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            lo_r = lo_r_var.get()
            lo_o = lo_o_var.get()
            hi_o = hi_o_var.get()
            hi_r = hi_r_var.get()
            row = self._pv_alert_rows[i]
            lbl = self._pv_alert_labels[i]

            # External condition: raw temperature outside its absolute range
            abs_range = _PV_ABS_RANGE[i]
            abs_breach = (abs_range is not None and abs_val is not None
                          and (abs_val < abs_range[0] or abs_val > abs_range[1]))

            if abs_breach:
                bg = "#7a1fa0"   # purple — out of absolute range
            elif avg is not None and (avg < lo_r or avg > hi_r):
                bg = "#cc2200"
            elif avg is not None and (avg < lo_o or avg > hi_o):
                bg = "#cc6600"
            else:
                bg = None

            if bg is not None:
                if abs_breach:
                    fmt = ".2f" if abs(abs_val) < 10 else ".1f"
                    arrow = " ↑" if abs_val > abs_range[1] else " ↓"
                    text = f"⛔ {label}: {abs_val:{fmt}} °C{arrow}"
                else:
                    suffix = f" {unit}" if unit else ""
                    fmt = ".2f" if abs(avg) < 10 else ".1f"
                    arrow = " ↑" if avg > hi_o else " ↓"
                    text = f"⚠  {label}: {avg:{fmt}}{suffix}{arrow}"
                row.configure(bg=bg)
                lbl.configure(bg=bg, text=text)
                row._active = True
            else:
                row._active = False

        self._relayout_pv_alerts()

    def _on_pv_frame_configure(self, event):
        # Re-flow only when the available width actually changes (placing
        # children / setting the frame height fire <Configure> with same width).
        if event.width != self._pv_last_width:
            self._pv_last_width = event.width
            self._relayout_pv_alerts()

    def _relayout_pv_alerts(self):
        """Lay active alert badges left-to-right, wrapping to a new line so
        nothing overlaps or gets clipped at the right edge."""
        for row in self._pv_alert_rows:
            row.place_forget()
        active = [r for r in self._pv_alert_rows if getattr(r, "_active", False)]
        if not active:
            self._pv_frame.configure(height=1)
            return

        self.update_idletasks()
        avail = self._pv_frame.winfo_width()
        if avail <= 1:
            avail = max(1, self.winfo_width() - 20)

        gap = 4
        x = y = line_h = 0
        for row in active:
            w = row.winfo_reqwidth()
            h = row.winfo_reqheight()
            if x > 0 and x + w > avail:   # wrap to next line
                x = 0
                y += line_h + gap
                line_h = 0
            row.place(x=x, y=y)
            x += w + gap
            line_h = max(line_h, h)
        self._pv_frame.configure(height=y + line_h)

    def _on_close(self):
        self.tracking = False
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
        self.destroy()


if __name__ == "__main__":
    app = ScreenTracker()
    app.mainloop()
