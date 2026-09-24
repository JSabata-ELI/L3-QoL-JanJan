"""Check the "Selected cameras" box shows every selected camera.

It ran with a fixed 95 px height, so the 4th and 5th row of every column were
cut off and a scrollbar appeared instead. This builds the real window, ticks a
growing number of cameras, verifies no label falls outside the visible box, and
saves a picture of the box for each case.

Run:  python testing/render_selected_cams.py
Writes testing/selected_cams_<n>.png
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s  # noqa: E402

OUTDIR = Path(__file__).resolve().parent
FAILS = []


def check(label, ok, extra=""):
    print(f"  {'OK ' if ok else 'BAD'}  {label}{('  — ' + extra) if extra else ''}")
    if not ok:
        FAILS.append(label)


def grab(widget, out: Path):
    from PIL import ImageGrab
    widget.update_idletasks()
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    w, h = widget.winfo_width(), widget.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(out, "PNG")
    return w, h


def main():
    s._set_dpi_awareness()
    app = s.App()
    app.all_screens_var.set(False)
    for v in app.monitor_vars:
        v.set(False)
    app.update()

    all_cams = list(app.camera_vars.keys())
    canvas = app._sel_cams_canvas
    box = app.nametowidget(app.nametowidget(canvas.winfo_parent()).winfo_parent())

    for n in (2, 10, 26, 60):
        app._programmatic_cam_update = True
        for i, cam in enumerate(all_cams):
            app.camera_vars[cam].set(i < n)
        app._programmatic_cam_update = False
        app._update_name_label()
        app.update()
        app.update_idletasks()

        labels = canvas.winfo_children()[0].winfo_children()
        rows = {int(w.grid_info()["row"]) for w in labels}
        cols = {int(w.grid_info()["column"]) for w in labels}
        per_col = max(rows) + 1
        ch = canvas.winfo_height()
        overflow = [w.cget("text") for w in labels
                    if w.winfo_y() + w.winfo_reqheight() > ch]

        print(f"{n} cameras — {len(labels)} labels, {len(cols)} columns, "
              f"{per_col} rows/col, box {canvas.winfo_width()}x{ch}")
        check("one label per camera plus the heading", len(labels) == n + 1,
              f"got {len(labels)}")
        check("every label inside the visible box", not overflow,
              ", ".join(overflow[:6]))
        check("no vertical scrollbar in the box",
              not any(isinstance(w, s.ttk.Scrollbar) and
                      str(w.cget("orient")) == "vertical"
                      for w in box.winfo_children()))
        if n <= 26:
            check("horizontal scrollbar hidden", not app._sel_hsb.winfo_ismapped())
        w, h = grab(box, OUTDIR / f"selected_cams_{n}.png")
        print(f"  saved selected_cams_{n}.png ({w}x{h})")

    # Monitors and cameras together: the heading must start its own column.
    for v in app.monitor_vars:
        v.set(True)
    app._programmatic_cam_update = True
    for i, cam in enumerate(all_cams):
        app.camera_vars[cam].set(i < 7)
    app._programmatic_cam_update = False
    app._update_name_label()
    app.update()
    app.update_idletasks()
    labels = canvas.winfo_children()[0].winfo_children()
    ch = canvas.winfo_height()
    overflow = [w.cget("text") for w in labels
                if w.winfo_y() + w.winfo_reqheight() > ch]
    mon_n = len(app.monitor_vars)
    print(f"{mon_n} monitors + 7 cameras — {len(labels)} labels, box height {ch}")
    check("both headings and every item are there", len(labels) == mon_n + 7 + 2,
          f"got {len(labels)}")
    check("every label inside the visible box", not overflow, ", ".join(overflow[:6]))
    w, h = grab(box, OUTDIR / "selected_cams_monitors.png")
    print(f"  saved selected_cams_monitors.png ({w}x{h})")

    app.destroy()
    print()
    print("FAILED: " + ", ".join(FAILS) if FAILS else "all checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
