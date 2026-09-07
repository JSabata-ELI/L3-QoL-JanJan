"""Audit the identity of every deployed .exe: window title, title-bar icon,
taskbar icon and taskbar button name.

For each exe it launches the program, waits for a real top-level window, then
records:
  * the window text (what the title bar shows)
  * the ICON_BIG / ICON_SMALL the window answers with, rendered to PNG
  * the taskbar button found through UI Automation: its name and a crop of the
    exact pixels the taskbar draws (the only way to attribute an icon to a
    program -- see the taskbar-icon notes in memory)
  * the program's own icon.ico rendered to PNG for comparison

Run:  python audit_exe_identity.py [--only "Git Work"] [--outdir DIR]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from pathlib import Path

import win32con
import win32gui
import win32ui
from PIL import Image, ImageGrab

SHARE = Path(r"\\hapls-share.cs.eli-beams.eu\scratch\Software")

SKIP_DIRS = {"Fiji", "ImageJ", "CS_Studio"}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()


# ---------------------------------------------------------------- processes
def exe_path_of_pid(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def running_instances(exe_name: str) -> list[str]:
    """CSV output on purpose: tasklist's table view truncates image names at
    ~25 characters, so a long name like "Diagnostic Webex listener v1.1.1.exe"
    never matches and the program looks like it is not running."""
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh",
                          "/fi", f"imagename eq {exe_name}"],
                         capture_output=True, text=True, errors="replace").stdout
    return [ln for ln in out.splitlines()
            if ln.lower().startswith(f'"{exe_name.lower()}"')]


def version_strings(exe: Path) -> dict:
    """FileDescription/ProductName out of the exe's version resource -- this is
    the text Windows puts on the taskbar button when no shortcut names it."""
    import win32api
    try:
        langs = win32api.GetFileVersionInfo(str(exe), r"\VarFileInfo\Translation")
    except Exception:
        return {}
    if not langs:
        return {}
    lang, cp = langs[0]
    out = {}
    for key in ("FileDescription", "ProductName", "FileVersion",
                "ProductVersion", "InternalName", "OriginalFilename"):
        try:
            out[key] = win32api.GetFileVersionInfo(
                str(exe), f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{key}")
        except Exception:
            out[key] = None
    return out


# ---------------------------------------------------------------- windows
def top_windows_for_exe(exe: Path) -> list[tuple[int, str, int, int]]:
    """(hwnd, title, area, pid) of visible, non-tool top-level windows whose
    process image is this exe."""
    found: list[tuple[int, str, int, int]] = []
    target = str(exe).lower()

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetParent(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if exe_path_of_pid(pid.value).lower() != target:
            return True
        title = win32gui.GetWindowText(hwnd)
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        found.append((hwnd, title, max(0, r - l) * max(0, b - t), pid.value))
        return True

    win32gui.EnumWindows(cb, None)
    return found


def window_icons(hwnd: int) -> dict:
    res = {}
    out = ctypes.c_size_t(0)
    for name, which in (("big", 1), ("small", 0), ("small2", 2)):
        out.value = 0
        ok = user32.SendMessageTimeoutW(wt.HWND(hwnd), 0x007F, which, 0,
                                        0x0002, 3000, ctypes.byref(out))
        res[name] = int(out.value) if ok else -1
    try:
        res["class"] = user32.GetClassLongPtrW(hwnd, -14)  # GCLP_HICON
    except Exception:
        res["class"] = 0
    return res


def icon_to_png(hicon: int, path: Path, size: int = 48) -> bool:
    if not hicon:
        return False
    screen = win32gui.GetDC(0)
    try:
        hdc = win32ui.CreateDCFromHandle(screen)
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(hdc, size, size)
        mem = hdc.CreateCompatibleDC()
        mem.SelectObject(bmp)
        mem.FillSolidRect((0, 0, size, size), 0xC0C0C0)
        win32gui.DrawIconEx(mem.GetHandleOutput(), 0, 0, hicon,
                            size, size, 0, None, win32con.DI_NORMAL)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bits, "raw", "BGRX", 0, 1)
        img.save(path)
        return True
    except Exception as exc:
        print(f"    icon render failed: {exc}")
        return False
    finally:
        win32gui.ReleaseDC(0, screen)


def ico_file_to_png(ico: Path, path: Path, size: int = 48) -> bool:
    try:
        img = Image.open(ico)
        img.load()
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (192, 192, 192))
        flat.paste(img, (0, 0), img)
        flat.resize((size, size)).save(path)
        return True
    except Exception as exc:
        print(f"    ico render failed: {exc}")
        return False


# ---------------------------------------------------------------- taskbar
def taskbar_buttons() -> list[tuple[str, tuple[int, int, int, int], int]]:
    """(name, screen rect, tray window handle) for every taskbar button.

    The primary taskbar comes first, so a caller can take the first match."""
    import uiautomation as auto
    out: list = []
    root = auto.GetRootControl()
    trays = [c for c in root.GetChildren()
             if c.ClassName in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")]
    trays.sort(key=lambda c: 0 if c.ClassName == "Shell_TrayWnd" else 1)
    for tray in trays:
        for btn in tray.GetChildren():
            _collect_buttons(btn, out, tray.NativeWindowHandle)
    return out


def _collect_buttons(ctrl, out, tray_hwnd, depth=0):
    import uiautomation as auto
    if depth > 8:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        try:
            if isinstance(k, auto.ButtonControl):
                r = k.BoundingRectangle
                out.append((k.Name, (r.left, r.top, r.right, r.bottom),
                            tray_hwnd))
        except Exception:
            pass
        _collect_buttons(k, out, tray_hwnd, depth + 1)


def print_window(hwnd: int) -> tuple[Image.Image, tuple[int, int]] | None:
    """Render a window's own pixels. The taskbar is auto-hidden on this desk,
    so a screen grab of its rectangle only returns wallpaper -- PrintWindow
    with PW_RENDERFULLCONTENT draws it regardless."""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    dc = win32gui.GetWindowDC(hwnd)
    try:
        src = win32ui.CreateDCFromHandle(dc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        if not user32.PrintWindow(wt.HWND(hwnd), mem.GetSafeHdc(), 2):
            return None
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        return img, (l, t)
    except Exception as exc:
        print(f"    PrintWindow failed: {exc}")
        return None
    finally:
        win32gui.ReleaseDC(hwnd, dc)


def crop_button(tray_hwnd: int, rect, path: Path) -> bool:
    got = print_window(tray_hwnd)
    if not got:
        return False
    img, (ox, oy) = got
    l, t, r, b = rect
    box = (l - ox, t - oy, r - ox, b - oy)
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    cut = img.crop(box)
    cut.resize((cut.width * 3, cut.height * 3), Image.NEAREST).save(path)
    return True


# ---------------------------------------------------------------- one program
def audit(exe: Path, outdir: Path, wait: int, settle: float = 3.0) -> dict:
    tag = exe.parent.name.replace(" ", "_")
    rec: dict = {"folder": exe.parent.name, "exe": exe.name,
                 "expected_name": exe.stem}

    attached = bool(running_instances(exe.name))
    rec["already_running"] = attached

    rec["version_info"] = version_strings(exe)
    ico = exe.parent / "icon.ico"
    rec["icon_ico_present"] = ico.exists()
    if ico.exists():
        p = outdir / f"{tag}_file.png"
        if ico_file_to_png(ico, p):
            rec["png_icon_file"] = p.name

    proc = None if attached else subprocess.Popen([str(exe)],
                                                  cwd=str(exe.parent))
    try:
        deadline = time.time() + wait
        wins: list = []
        while time.time() < deadline:
            wins = [w for w in top_windows_for_exe(exe)
                    if w[1].strip() and w[2] > 40000]
            if wins:
                time.sleep(settle)       # let the real title/icon settle
                wins = [w for w in top_windows_for_exe(exe)
                        if w[1].strip() and w[2] > 40000]
                break
            time.sleep(0.5)
        if not wins:
            rec["error"] = f"no window within {wait}s"
            rec["any_windows"] = [w[1] for w in top_windows_for_exe(exe)]
            return rec

        wins.sort(key=lambda w: -w[2])
        hwnd, title, _, _ = wins[0]
        rec["window_title"] = title
        rec["all_window_titles"] = [w[1] for w in wins]

        icons = window_icons(hwnd)
        rec["icon_handles"] = {k: hex(v) for k, v in icons.items()}
        src = icons.get("big") or icons.get("small") or icons.get("class")
        p = outdir / f"{tag}_window.png"
        if icon_to_png(src, p):
            rec["png_window_icon"] = p.name

        time.sleep(1.0)
        btns = taskbar_buttons()
        rec["taskbar_all_buttons"] = [n for n, _, _ in btns]
        hit = None
        for name, r, tray in btns:
            base = title.split("  ")[0].strip()
            if name and (title.lower() in name.lower()
                         or (base and base.lower() in name.lower())
                         or exe.stem.lower() in name.lower()):
                hit = (name, r, tray)
                break
        if hit:
            rec["taskbar_button_name"] = hit[0]
            p = outdir / f"{tag}_taskbar.png"
            if crop_button(hit[2], hit[1], p):
                rec["png_taskbar"] = p.name
        else:
            rec["error_taskbar"] = "no taskbar button matched the window title"
        return rec
    finally:
        if proc is not None:            # never close what the user had open
            for hwnd, *_ in top_windows_for_exe(exe):
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                except Exception:
                    pass
            time.sleep(2.0)
            # By pid, never by image name: the user may have their own copy of
            # the same program open, and /im would take that down too.
            subprocess.run(["taskkill", "/f", "/t", "/pid", str(proc.pid)],
                           capture_output=True)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            time.sleep(1.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--outdir", default="")
    ap.add_argument("--wait", type=int, default=90)
    ap.add_argument("--settle", type=float, default=3.0,
                    help="seconds to wait after the window appears -- the "
                         "shell needs a few of them before it stops drawing "
                         "the placeholder on a brand new taskbar button")
    args = ap.parse_args()

    outdir = Path(args.outdir) if args.outdir else Path(__file__).parent / "_audit_out"
    outdir.mkdir(parents=True, exist_ok=True)

    exes: list[Path] = []
    for d in sorted(SHARE.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS:
            continue
        if args.only and not any(o.lower() in d.name.lower() for o in args.only):
            continue
        for e in sorted(d.glob("*.exe")):
            exes.append(e)

    report = []
    for exe in exes:
        print(f"== {exe.parent.name} / {exe.name}", flush=True)
        try:
            rec = audit(exe, outdir, args.wait, args.settle)
        except Exception as exc:
            rec = {"folder": exe.parent.name, "exe": exe.name,
                   "error": f"harness: {exc!r}"}
        print("   " + json.dumps({k: v for k, v in rec.items()
                                  if k != "taskbar_all_buttons"},
                                 ensure_ascii=False), flush=True)
        report.append(rec)

    (outdir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport -> {outdir / 'report.json'}")


if __name__ == "__main__":
    main()
