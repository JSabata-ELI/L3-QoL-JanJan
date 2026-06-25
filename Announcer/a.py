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

# (pv_name, label, lo_orange, lo_red, hi_orange, hi_red, unit)
_PV_MONITORS = [
    ("L3-UTIL-HEB03-001:PressOut_PSI", "Helium volume", 46, 45.5, 57, 60, "PSI"),
    ("HAPLS-VOLT_IN_CGL-SEEDER_ER3_ALPHA1:SeederPZTVoltage", "Alpha voltage", 1.2, 1.1, 1.9, 2.2, "V"),
]

_RESERVED_PRESET_KEYS = {"pv_thresholds", "window_geometry"}


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

        if getattr(sys, "frozen", False):
            _base = Path(sys.executable).parent
        else:
            _base = Path(__file__).parent
        self._presets_path = _base / "presets.json"
        self._presets = self._load_presets()

        pv_saved = self._presets.get("pv_thresholds", {})
        self._pv_thr_vars = []
        for pv_name, _label, def_lo_o, def_lo_r, def_hi_o, def_hi_r, _unit in _PV_MONITORS:
            saved = pv_saved.get(pv_name, {})
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
        self.geometry(saved_geom if saved_geom else "350x150")

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

    def _build_ui(self):
        pad = dict(padx=10, pady=5)

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

        # Action buttons
        self._btn_frame = ttk.Frame(self)
        self._btn_frame.grid(row=1, column=0, sticky="w", **pad)
        btn_frame = self._btn_frame

        self._preview_popup = None
        self._preview_photo = None
        self.btn_preview = ttk.Button(btn_frame, text="Preview region", width=16)
        self.btn_preview.grid(row=0, column=0, padx=4)
        self.btn_preview.bind("<Enter>", self._show_preview_popup)
        self.btn_preview.bind("<Leave>", lambda *_: self.after(100, self._check_hide_preview))
        self.btn_preview.bind("<Button-1>", self._toggle_preview_popup)

        self.btn_reference = ttk.Button(btn_frame, text="Set reference",
                                        command=self._select_region, width=16)
        self.btn_reference.grid(row=0, column=1, padx=4)

        self.btn_resnap = ttk.Button(btn_frame, text="↺",
                                     command=self._save_reference,
                                     state="disabled", width=3)
        self.btn_resnap.grid(row=0, column=2, padx=(0, 4))

        # Presets panel
        self._preset_frame = ttk.LabelFrame(self, text="Region presets")
        self._preset_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 5))

        self._preset_var = tk.StringVar()
        self._preset_combo = ttk.Combobox(self._preset_frame, textvariable=self._preset_var,
                                           state="readonly", width=18)
        self._preset_combo.grid(row=0, column=0, padx=(6, 4), pady=6)
        self._refresh_preset_combo()

        ttk.Button(self._preset_frame, text="Load",
                   command=self._load_preset, width=6).grid(row=0, column=1, padx=(0, 4), pady=6)
        ttk.Button(self._preset_frame, text="Save region",
                   command=self._save_preset, width=10).grid(row=0, column=2, padx=(0, 4), pady=6)
        ttk.Button(self._preset_frame, text="Delete",
                   command=self._delete_preset, width=6).grid(row=0, column=3, padx=(0, 6), pady=6)

        # PV alert panel (row=3) — individual rows shown only when condition breached
        self._pv_frame = ttk.Frame(self)
        self._pv_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._pv_frame.grid_remove()
        self.columnconfigure(0, weight=1)

        for _pv_name, _label, *_thresholds in _PV_MONITORS:
            row_frame = tk.Frame(self._pv_frame, bg="#cc6600", padx=8, pady=5)
            lbl = tk.Label(row_frame, text="", bg="#cc6600", fg="white",
                           font=("Segoe UI", 9, "bold"), anchor="w")
            lbl.pack()
            self._pv_alert_rows.append(row_frame)
            self._pv_alert_labels.append(lbl)

        # Pre-build Settings variables (popup builds widgets on first open)
        self.flash_color = tk.StringVar(value="#ff2222")
        self.flash_duration = tk.DoubleVar(value=0.0)
        self.sound_enabled = tk.BooleanVar(value=True)
        self.sound_freq = tk.IntVar(value=1500)
        self.sound_duration = tk.IntVar(value=500)
        self.sound_file = tk.StringVar(value="beep")
        self._sound_files = self._load_sound_files()
        self._flash_color_btn = None  # created in popup

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
        for i, (pv_name, *_rest) in enumerate(_PV_MONITORS):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            try:
                thr[pv_name] = {
                    "lo_red":    lo_r_var.get(),
                    "lo_orange": lo_o_var.get(),
                    "hi_orange": hi_o_var.get(),
                    "hi_red":    hi_r_var.get(),
                }
            except tk.TclError:
                pass
        self._presets["pv_thresholds"] = thr
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

        # PV Limits table
        pv_lim_frame = ttk.LabelFrame(frame, text="PV Limits")
        pv_lim_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Zone legend strip
        legend_fr = tk.Frame(pv_lim_frame)
        legend_fr.grid(row=0, column=0, columnspan=5, sticky="w", padx=6, pady=(4, 2))
        for _txt, _bg in [("  RED  ", "#aa1c00"), ("  WARN  ", "#b85a00"),
                          ("   OK   ", "#2a6030"), ("  WARN  ", "#b85a00"), ("  RED  ", "#aa1c00")]:
            tk.Label(legend_fr, text=_txt, bg=_bg, fg="white",
                     font=("Segoe UI", 7, "bold")).pack(side="left", padx=1)

        # Column headers
        ttk.Label(pv_lim_frame, text="Channel",
                  font=("Segoe UI", 8, "bold")).grid(row=1, column=0, padx=(6, 8), pady=(0, 2), sticky="w")
        for _col, (_htxt, _hbg) in enumerate(
            [("< Red", "#aa1c00"), ("< Warn", "#b85a00"), ("Warn >", "#b85a00"), ("Red >", "#aa1c00")],
            start=1,
        ):
            tk.Label(pv_lim_frame, text=f" {_htxt} ", bg=_hbg, fg="white",
                     font=("Segoe UI", 8, "bold")).grid(row=1, column=_col, padx=3, pady=(0, 2))

        ttk.Separator(pv_lim_frame, orient="horizontal").grid(
            row=2, column=0, columnspan=5, sticky="ew", padx=4, pady=(0, 2))

        # Data rows
        for i, (_pv_name, label, default_lo_o, _lo_r, _hi_o, _hi_r, unit) in enumerate(_PV_MONITORS):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            inc = 0.01 if default_lo_o < 10 else 0.1
            fmt = "%.2f" if default_lo_o < 10 else "%.1f"
            row_idx = i + 3
            ttk.Label(pv_lim_frame, text=f"{label} ({unit})", anchor="w").grid(
                row=row_idx, column=0, padx=(6, 8), pady=(2, 4), sticky="w")
            for _col, var in enumerate([lo_r_var, lo_o_var, hi_o_var, hi_r_var], start=1):
                ttk.Spinbox(pv_lim_frame, from_=0, to=9999, increment=inc,
                            textvariable=var, width=7, format=fmt).grid(
                    row=row_idx, column=_col, padx=3, pady=(2, 4))

        # Window position & size
        win_frame = ttk.LabelFrame(frame, text="Window position & size")
        win_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(win_frame,
                  text="Move and resize the Announcer window, then save its position.").grid(
            row=0, column=0, padx=(6, 4), pady=(4, 4), sticky="w")
        ttk.Button(win_frame, text="Set location and size of this window",
                   command=self._start_window_recording).grid(
            row=0, column=1, padx=(0, 6), pady=(4, 4))

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
            row.pack_forget()
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
        # Overlay frame covers the whole window — avoids ttk widget bg gaps
        if not hasattr(self, "_flash_overlay"):
            self._flash_overlay = tk.Frame(self)
        self._flash_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self._flash_overlay.lift()
        self._do_flash()

    def _do_flash(self):
        if time.time() > self._flash_deadline:
            self._is_flashing = False
            self._flash_overlay.place_forget()
            self._set_ui_visible(True)
            return
        self._flash_state = not self._flash_state
        color = self.flash_color.get() if self._flash_state else "#440000"
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
            self._btn_frame.grid()
            self._preset_frame.grid()
        else:
            self._mon_frame.pack_forget()
            self._settings_btn.pack_forget()
            self._btn_frame.grid_remove()
            self._preset_frame.grid_remove()

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

        btn_frame = ttk.Frame(rec_win)
        btn_frame.pack(pady=(0, 12))

        def on_done():
            geom = self.geometry()
            self._save_window_geometry(geom, scope_var.get(), current_preset)
            rec_win.destroy()

        ttk.Button(btn_frame, text="DONE", command=on_done, width=10).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Cancel", command=rec_win.destroy, width=10).pack(side="left")

        self.update_idletasks()
        rec_win.update_idletasks()
        rx = self.winfo_rootx()
        ry = self.winfo_rooty()
        rw = self.winfo_width()
        rec_win.geometry(f"+{rx + rw + 10}+{ry}")

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

        def worker():
            now_ns   = int(time.time() * 1e9)
            start_ns = now_ns - 60 * 1_000_000_000   # last 60 s
            results  = []
            for pv_name, _label, *_thresholds in _PV_MONITORS:
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
                    avg = sum(recent) / len(recent) if recent else None
                except Exception as e:
                    print(f"[PV] {pv_name}: {e}")
                    avg = None
                results.append(avg)
            self.after(0, lambda r=results: self._update_pv_display(r))

        threading.Thread(target=worker, daemon=True).start()
        self._pv_poll_job = self.after(PV_POLL_MS, self._poll_pvs)

    def _update_pv_display(self, results: list):
        for i, (avg, (_, label, _lo_o, _lo_r, _hi_o, _hi_r, unit)) in enumerate(zip(results, _PV_MONITORS)):
            lo_r_var, lo_o_var, hi_o_var, hi_r_var = self._pv_thr_vars[i]
            lo_r = lo_r_var.get()
            lo_o = lo_o_var.get()
            hi_o = hi_o_var.get()
            hi_r = hi_r_var.get()
            row = self._pv_alert_rows[i]
            lbl = self._pv_alert_labels[i]

            if avg is not None and (avg < lo_r or avg > hi_r):
                bg = "#cc2200"
            elif avg is not None and (avg < lo_o or avg > hi_o):
                bg = "#cc6600"
            else:
                bg = None

            if bg is not None:
                suffix = f" {unit}" if unit else ""
                fmt = ".2f" if abs(avg) < 10 else ".1f"
                arrow = " ↑" if avg > hi_o else " ↓"
                row.configure(bg=bg)
                lbl.configure(bg=bg, text=f"⚠  {label}: {avg:{fmt}}{suffix}{arrow}")
                if not row.winfo_ismapped():
                    row.pack(anchor="w", pady=(0, 2))
            else:
                if row.winfo_ismapped():
                    row.pack_forget()

    def _on_close(self):
        self.tracking = False
        if self._pv_poll_job:
            self.after_cancel(self._pv_poll_job)
        self.destroy()


if __name__ == "__main__":
    app = ScreenTracker()
    app.mainloop()
